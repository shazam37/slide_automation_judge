#!/usr/bin/env python3
"""
convert_dataset.py — Convert LangSmith JSONL dataset to golden test format.

Usage:
    python convert_dataset.py --input dataset.jsonl --output-dir tests/golden_tests
"""

import argparse
import base64
import json
import re
import sys
from pathlib import Path


def extract_layout_info(text: str) -> tuple[str, str]:
    """Extract layout_option_id and layout_description from human message text."""
    option_id = "OPT-1"
    description = ""

    # Extract Layout Option ID
    m = re.search(r"## Layout Option ID\s*\n(.+)", text)
    if m:
        option_id = m.group(1).strip()

    # Extract Layout Description block
    m = re.search(r"## Layout Description.*?\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
    if m:
        description = m.group(1).strip()

    return option_id, description


def convert(input_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(input_path) as f:
        lines = [l.strip() for l in f if l.strip()]

    print(f"Found {len(lines)} records in {input_path.name}")

    for i, line in enumerate(lines):
        record = json.loads(line)
        case_id = f"case_{i:03d}"
        case_dir = output_dir / case_id
        case_dir.mkdir(exist_ok=True)

        # --- Images ---
        msgs = record["inputs"]["input"]
        human_msg = next((m for m in msgs if m["type"] == "human"), None)
        if not human_msg:
            print(f"  [{case_id}] SKIP: no human message")
            continue

        images = [c for c in human_msg["content"] if c.get("type") == "image_url"]
        if len(images) < 2:
            print(f"  [{case_id}] SKIP: expected 2 images, got {len(images)}")
            continue

        def save_image(data_uri: str, path: Path):
            # Strip "data:image/png;base64," prefix
            b64 = data_uri.split(",", 1)[1]
            path.write_bytes(base64.b64decode(b64))

        save_image(images[0]["image_url"]["url"], case_dir / "sketch.png")
        save_image(images[1]["image_url"]["url"], case_dir / "source.png")

        # --- Layout description ---
        text_items = [c["text"] for c in human_msg["content"] if c.get("type") == "text"]
        human_text = "\n".join(text_items)
        option_id, layout_description = extract_layout_info(human_text)

        # --- Expected output ---
        parsed = record["outputs"].get("parsed") or {}
        verdict = parsed.get("verdict", "rejected")
        violations = parsed.get("violations", [])
        expected_violation_ids = [v["rule"] for v in violations]

        expected = {
            "verdict": verdict,
            "layout_option_id": option_id,
            "layout_description": layout_description,
            "expected_violations": expected_violation_ids,
            # Store full violation detail for reference (not used by evaluator)
            "_reference_violations": violations,
            "_reference_layout_score": parsed.get("layout_score"),
            "_reference_content_fidelity_score": parsed.get("content_fidelity_score"),
            "_reference_critique_target": parsed.get("critique_target"),
        }
        (case_dir / "expected.json").write_text(json.dumps(expected, indent=2))

        # --- Metadata ---
        meta = {
            "layout_description": layout_description,
            "layout_option_id": option_id,
            "source_dataset": input_path.name,
            "revision_id": record.get("metadata", {}).get("revision_id", ""),
            "quality_check_errors": [],
            "quality_check_warnings": [],
        }
        (case_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

        crit = sum(1 for v in violations if v.get("severity") == "critical")
        major = sum(1 for v in violations if v.get("severity") == "major")
        print(
            f"  [{case_id}] verdict={verdict:8s}  violations={len(violations)} "
            f"(crit={crit}, major={major})  rules={expected_violation_ids}"
        )

    print(f"\nDone. Golden tests written to: {output_dir}")
    print(f"Verdicts: {sum(1 for l in lines if json.loads(l)['outputs'].get('parsed', {}) and json.loads(l)['outputs']['parsed'].get('verdict') == 'approved')} approved, "
          f"{sum(1 for l in lines if json.loads(l)['outputs'].get('parsed', {}) and json.loads(l)['outputs']['parsed'].get('verdict') == 'rejected')} rejected")


def main():
    parser = argparse.ArgumentParser(description="Convert LangSmith JSONL to golden test format")
    parser.add_argument("--input", required=True, help="Path to .jsonl dataset file")
    parser.add_argument(
        "--output-dir",
        default="tests/golden_tests",
        help="Directory to write case_NNN folders (default: tests/golden_tests)",
    )
    args = parser.parse_args()

    convert(Path(args.input), Path(args.output_dir))


if __name__ == "__main__":
    main()
