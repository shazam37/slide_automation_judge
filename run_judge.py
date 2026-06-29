#!/usr/bin/env python3
"""
run_judge.py — CLI for running the SVG Judge on a pair of PNG files.

Usage:
    python run_judge.py judge --sketch sketch.png --source source.png
    python run_judge.py judge --sketch sketch.png --source source.png --layout-description "Two-column layout"
    python run_judge.py evaluate --golden-dir judge_module/tests/golden_tests
    python run_judge.py evaluate --golden-dir judge_module/tests/golden_tests --output results.json

    # Legacy flat-arg form (still supported):
    python run_judge.py --sketch sketch.png --source source.png
    python run_judge.py --evaluate --golden-dir judge_module/tests/golden_tests

Environment variables:
    ANTHROPIC_API_KEY   — required for direct Anthropic API
    AWS_DEFAULT_REGION  — required for AWS Bedrock (e.g. us-east-1)
    LANGSMITH_API_KEY   — optional, enables LangSmith tracing
    LANGSMITH_PROJECT   — optional, LangSmith project name (default: judge-module)
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Allow running this script from inside the package directory (python run_judge.py ...)
# by ensuring the parent of judge_module/ is on sys.path.
_pkg_parent = Path(__file__).resolve().parent.parent
if str(_pkg_parent) not in sys.path:
    sys.path.insert(0, str(_pkg_parent))


def _load_png_as_data_uri(path: Path) -> str:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


_DEFAULT_BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"

_RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _build_llm(model_override: str | None = None):
    """
    Build the LLM. Tries Anthropic direct API first, then AWS Bedrock.
    Returns (llm, effective_model_id).
    Pass --model on the CLI (or set JUDGE_MODEL env var) to override the model ID.
    """
    model_id = model_override or os.getenv("JUDGE_MODEL")

    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            from langchain_anthropic import ChatAnthropic
            chosen = model_id or _DEFAULT_ANTHROPIC_MODEL
            print(f"Using Anthropic direct API ({chosen})")
            return ChatAnthropic(
                model=chosen,
                temperature=0,
                max_tokens=4096,
                api_key=anthropic_key,
            ), chosen
        except ImportError:
            print("langchain-anthropic not installed, trying Bedrock...")

    # Fall back to AWS Bedrock (supports standard credentials OR bearer token)
    has_aws_creds = (
        os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("AWS_BEARER_TOKEN_BEDROCK")
        or os.getenv("AWS_PROFILE")
        or os.getenv("AWS_DEFAULT_REGION")  # enough to attempt; boto3 may find creds elsewhere
    )
    if has_aws_creds:
        try:
            from botocore.config import Config as BotocoreConfig
            from langchain_aws import ChatBedrockConverse
            region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
            chosen = model_id or _DEFAULT_BEDROCK_MODEL
            print(f"Using AWS Bedrock ({chosen}, region={region})")
            return ChatBedrockConverse(
                model=chosen,
                region_name=region,
                temperature=0,
                max_tokens=8192,
                config=BotocoreConfig(
                    read_timeout=300,    # 5 min — reasoning + structured output can be long
                    connect_timeout=10,
                    retries={"max_attempts": 0},  # let trustcall handle retries
                ),
            ), chosen
        except ImportError:
            print("langchain-aws not installed. Run: pip install langchain-aws")

    print("ERROR: No LLM provider available.")
    print("Options:")
    print("  Anthropic API:  export ANTHROPIC_API_KEY=sk-ant-...")
    print("  AWS Bedrock:    export AWS_BEARER_TOKEN_BEDROCK=... AWS_DEFAULT_REGION=us-east-1")
    print("  AWS (keys):     export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=us-east-1")
    sys.exit(1)


def _model_short_tag(model_id: str) -> str:
    """Extract a short human-readable tag from a model ID, e.g. 'sonnet45'."""
    lower = model_id.lower()
    # New format: claude-{family}-{major}-{minor} e.g. claude-sonnet-4-5-20250929
    m = re.search(r'(sonnet|haiku|opus|fable)-(\d+)-(\d+)', lower)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    # New format without minor version: claude-fable-5
    m = re.search(r'(sonnet|haiku|opus|fable)-(\d+)', lower)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    # Old format: claude-3-5-sonnet
    m = re.search(r'claude-(\d+)-(\d+)-(sonnet|haiku|opus)', lower)
    if m:
        return f"{m.group(3)}{m.group(1)}{m.group(2)}"
    return re.sub(r'[^a-z0-9]', '', lower)[:16]


def _save_case_result(
    case_id: str,
    result,
    model_id: str,
    prompt_version: str,
    ts: datetime,
    expected_verdict: str | None = None,
) -> Path:
    """Serialize one judge result to results/{case_id}/{timestamp}_{version}_{model}.json."""
    case_dir = _RESULTS_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    actual_verdict = result.verdict if result else None
    correct = (actual_verdict == expected_verdict) if (actual_verdict and expected_verdict) else None

    data = {
        "run_at": ts.isoformat(timespec="seconds"),
        "prompt_version": prompt_version,
        "model": model_id,
        "case_id": case_id,
        "expected_verdict": expected_verdict,
        "verdict": actual_verdict,
        "correct": correct,
        "layout_score": result.layout_score if result else None,
        "content_fidelity_score": result.content_fidelity_score if result else None,
        "confidence": result.confidence if result else None,
        "violations": [v.model_dump() for v in result.violations] if result else [],
        "reasoning": result.reasoning if result else None,
        "critique": result.critique if result else None,
        "critique_target": result.critique_target if result else None,
    }

    model_tag = _model_short_tag(model_id)
    filename = f"{ts.strftime('%Y%m%d_%H%M%S')}_{prompt_version}_{model_tag}.json"
    out_path = case_dir / filename
    out_path.write_text(json.dumps(data, indent=2))
    return out_path


def _load_layout_description(sketch_path: Path, cli_value: str) -> tuple[str, str]:
    """
    Resolve layout_description: CLI arg takes priority, then metadata.json fallback.
    Returns (layout_description, source_label) where source_label describes origin.
    """
    if cli_value:
        return cli_value, "CLI"
    metadata_json = sketch_path.parent / "metadata.json"
    if metadata_json.exists():
        try:
            meta = json.loads(metadata_json.read_text())
            desc = meta.get("layout_description", "")
            if desc:
                return desc, f"metadata.json ({sketch_path.parent.name})"
        except Exception:
            pass
    return "", "none"


def run_single(args):
    from judge_module_revised import SVGJudgeAgent, SVGJudgeConfig, SVGJudgeInput, SVGJudgePrompts

    sketch_path = Path(args.sketch)
    source_path = Path(args.source)

    if not sketch_path.exists():
        print(f"ERROR: sketch file not found: {sketch_path}")
        sys.exit(1)
    if not source_path.exists():
        print(f"ERROR: source file not found: {source_path}")
        sys.exit(1)

    layout_description, layout_source = _load_layout_description(
        sketch_path, args.layout_description or ""
    )

    model_override = getattr(args, "model", None)
    llm, model_id = _build_llm(model_override)
    prompt_version = getattr(args, "prompt_version", "v1")
    agent = SVGJudgeAgent(
        prompts=SVGJudgePrompts(),
        config=SVGJudgeConfig(llm=llm, prompt_version=prompt_version),
    )

    print(f"\nEvaluating:")
    print(f"  Sketch:         {sketch_path}")
    print(f"  Source:         {source_path}")
    print(f"  Prompt version: {prompt_version}")
    print(f"  Layout desc:    [{layout_source}]" + (f" {layout_description[:80]}..." if len(layout_description) > 80 else f" {layout_description}" if layout_description else " (none)"))
    print()

    result = agent.invoke(SVGJudgeInput(
        sketch_png=_load_png_as_data_uri(sketch_path),
        source_png=_load_png_as_data_uri(source_path),
        layout_description=layout_description,
    ))

    print(f"Verdict:                 {result.verdict.upper()}")
    print(f"Confidence:              {result.confidence:.2f}")
    print(f"Layout score:            {result.layout_score}/5")
    print(f"Content fidelity score:  {result.content_fidelity_score}/5")

    if result.reasoning:
        print(f"\nReasoning:")
        for line in result.reasoning.split("\n"):
            print(f"  {line}")

    if result.violations:
        print(f"\nViolations ({len(result.violations)}):")
        for v in result.violations:
            containers = f" [{', '.join(v.affected_containers)}]" if v.affected_containers else ""
            print(f"  [{v.severity.upper()}] {v.rule}{containers}: {v.description}")

    if result.critique:
        print(f"\nCritique (→ {result.critique_target}):")
        print(f"  {result.critique}")

    print()

    # Auto-save when sketch lives inside a golden test case directory
    case_match = re.search(r'(case_\d+)', str(sketch_path.resolve()))
    if case_match:
        case_id = case_match.group(1)
        expected_verdict = None
        expected_json = sketch_path.parent / "expected.json"
        if expected_json.exists():
            expected_verdict = json.loads(expected_json.read_text()).get("verdict")
        saved = _save_case_result(case_id, result, model_id, prompt_version, datetime.now(), expected_verdict)
        print(f"Result saved → {saved.relative_to(Path(__file__).parent)}")

    return result


def run_evaluation(args):
    from judge_module_revised import SVGJudgeAgent, SVGJudgeConfig, SVGJudgePrompts
    from judge_module_revised.judge_evaluator import evaluate_judge

    golden_dir = Path(args.golden_dir)
    if not golden_dir.exists():
        print(f"ERROR: golden_dir not found: {golden_dir}")
        sys.exit(1)

    model_override = getattr(args, "model", None)
    llm, model_id = _build_llm(model_override)
    prompt_version = getattr(args, "prompt_version", "v1")
    agent = SVGJudgeAgent(
        prompts=SVGJudgePrompts(),
        config=SVGJudgeConfig(llm=llm, prompt_version=prompt_version),
    )

    print(f"\nRunning evaluation on: {golden_dir}")
    print(f"Prompt version:        {prompt_version}\n")
    report = evaluate_judge(agent, golden_dir=golden_dir)
    print(report.summary())

    # Save per-case result files; all cases in a batch share the same timestamp
    batch_ts = datetime.now()
    for r in report.results:
        _save_case_result(
            r.case_id, r.output, model_id, prompt_version, batch_ts,
            expected_verdict=r.expected_verdict,
        )
    print(f"Per-case results saved → results/ ({len(report.results)} files)")

    if args.output:
        output_path = Path(args.output)
        results_data = {
            "prompt_version": report.prompt_version,
            "summary": {
                "total": report.total,
                "correct": report.correct_count,
                "accuracy": round(report.accuracy, 4),
                "precision": round(report.precision, 4),
                "recall": round(report.recall, 4),
                "f1_score": round(report.f1_score, 4),
                "true_positives": report.true_positive_count,
                "true_negatives": report.true_negative_count,
                "false_positives": report.false_positive_count,
                "false_negatives": report.false_negative_count,
                "false_positive_rate": round(report.false_positive_rate, 4),
                "false_negative_rate": round(report.false_negative_rate, 4),
                "errors": report.error_count,
            },
            "rule_analysis": {
                "over_firing_rules": report.over_firing_rules,
                "under_firing_rules": report.under_firing_rules,
                "per_rule_metrics": {
                    rule: {
                        **m,
                        "precision": round(
                            m["tp"] / (m["tp"] + m["fp"]) if (m["tp"] + m["fp"]) > 0 else 0.0, 4
                        ),
                        "recall": round(
                            m["tp"] / (m["tp"] + m["fn"]) if (m["tp"] + m["fn"]) > 0 else 0.0, 4
                        ),
                    }
                    for rule, m in report.per_rule_metrics.items()
                },
            },
            "cases": [
                {
                    "case_id": r.case_id,
                    "expected": r.expected_verdict,
                    "actual": r.actual_verdict,
                    "correct": r.correct,
                    "error": r.error,
                    "output": r.output.model_dump() if r.output else None,
                }
                for r in report.results
            ],
        }
        output_path.write_text(json.dumps(results_data, indent=2))
        print(f"\nDetailed results saved to: {output_path}")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="SVG Judge — evaluate slide sketch quality"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Single evaluation
    single_parser = subparsers.add_parser("judge", help="Judge a single sketch/source pair")
    single_parser.add_argument("--sketch", required=True, help="Path to sketch PNG")
    single_parser.add_argument("--source", required=True, help="Path to source slide PNG")
    single_parser.add_argument("--layout-description", default="", help="Layout description text")
    single_parser.add_argument("--prompt-version", default="v2", help="Prompt version tag (e.g. v1, v2)")
    single_parser.add_argument("--model", default=None, help="Override model ID (e.g. anthropic.claude-3-5-sonnet-20241022-v2:0)")

    # Batch evaluation
    eval_parser = subparsers.add_parser("evaluate", help="Run evaluation on golden test set")
    eval_parser.add_argument(
        "--golden-dir",
        default="judge_module/tests/golden_tests",
        help="Path to golden test cases directory",
    )
    eval_parser.add_argument("--output", default=None, help="Save detailed results to JSON file")
    eval_parser.add_argument("--prompt-version", default="v2", help="Prompt version tag (e.g. v1, v2)")
    eval_parser.add_argument("--model", default=None, help="Override model ID (e.g. anthropic.claude-3-5-sonnet-20241022-v2:0)")

    # Support legacy flat args for convenience: python run_judge.py --sketch x --source y
    parser.add_argument("--sketch", default=None)
    parser.add_argument("--source", default=None)
    parser.add_argument("--layout-description", default="")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--golden-dir", default="judge_module/tests/golden_tests")
    parser.add_argument("--output", default=None)
    parser.add_argument("--prompt-version", default="v2", help="Prompt version tag (e.g. v1, v2)")
    parser.add_argument("--model", default=None, help="Override model ID")

    args = parser.parse_args()

    # Set up LangSmith tracing if configured
    if os.getenv("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGSMITH_PROJECT", "judge-module"))

    if args.command == "evaluate" or args.evaluate:
        run_evaluation(args)
    elif args.command == "judge" or (args.sketch and args.source):
        run_single(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
