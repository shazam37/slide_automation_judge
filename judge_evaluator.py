"""
judge_module/judge_evaluator.py

Evaluation harness for the SVG Judge.

Runs the judge on a golden test dataset and reports accuracy, precision,
recall, F1, and per-rule violation frequency metrics.

Golden test dataset format (directory structure):
    golden_tests/
        case_001/
            sketch.png          — rendered SVG sketch PNG
            source.png          — source slide PNG
            expected.json       — expected verdict and optional violation list
            metadata.json       — optional: layout_description, quality check data
        case_002/
            ...

expected.json format:
    {
        "verdict": "approved" | "rejected",
        "expected_violations": ["text_fill_ratio_critical", "overlap"],  <- optional
        "notes": "optional human notes about why this verdict is expected"
    }

metadata.json format (optional):
    {
        "layout_description": "Two-column layout with a table on the right.",
        "layout_option_id": "OPT-1",
        "quality_check_errors": ["Text overflows container C2"],
        "quality_check_warnings": ["Alignment gap detected near C1"]
    }

Usage:
    from judge_module.judge_evaluator import evaluate_judge
    from judge_module import SVGJudgeAgent, SVGJudgeConfig, SVGJudgePrompts
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model="claude-sonnet-4-5", temperature=0, max_tokens=4096)
    agent = SVGJudgeAgent(prompts=SVGJudgePrompts(), config=SVGJudgeConfig(llm=llm))

    report = evaluate_judge(agent, golden_dir=Path("judge_module/tests/golden_tests"))
    print(report.summary())
"""

from __future__ import annotations

import base64
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .judge_agent import SVGJudgeAgent, SVGJudgeInput, SVGJudgeOutput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class GoldenCase:
    """A single golden test case."""

    case_id: str
    sketch_png_path: Path
    source_png_path: Path
    expected_verdict: str                    # "approved" or "rejected"
    layout_description: str = ""
    layout_option_id: str = "OPT-1"
    notes: str = ""
    expected_violations: List[str] = field(default_factory=list)
    """Optional list of rule IDs expected to be flagged. When provided, enables
    per-rule precision/recall metrics. Use exact rule IDs from judge_rules.py."""
    quality_check_errors: List[str] = field(default_factory=list)
    """Hard errors from the SVG validator — passed to the judge at evaluation time."""
    quality_check_warnings: List[str] = field(default_factory=list)
    """Soft warnings from the SVG validator — passed to the judge at evaluation time."""


@dataclass
class CaseResult:
    """Result of running the judge on a single golden case."""

    case_id: str
    expected_verdict: str
    actual_verdict: str
    output: Optional[SVGJudgeOutput]
    expected_violations: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def correct(self) -> bool:
        return self.expected_verdict == self.actual_verdict

    @property
    def is_true_positive(self) -> bool:
        """Judge correctly rejected a flawed slide (expected=rejected, actual=rejected)."""
        return self.expected_verdict == "rejected" and self.actual_verdict == "rejected"

    @property
    def is_true_negative(self) -> bool:
        """Judge correctly approved a good slide (expected=approved, actual=approved)."""
        return self.expected_verdict == "approved" and self.actual_verdict == "approved"

    @property
    def is_false_positive(self) -> bool:
        """Judge over-rejected a good slide (expected=approved, actual=rejected).

        Standard ML convention: positive class = 'rejected' (defect detected).
        FP = judge falsely predicted 'rejected' for a slide that was actually fine.
        """
        return self.expected_verdict == "approved" and self.actual_verdict == "rejected"

    @property
    def is_false_negative(self) -> bool:
        """Judge missed a flaw (expected=rejected, actual=approved).

        Standard ML convention: positive class = 'rejected' (defect detected).
        FN = judge falsely predicted 'approved' for a slide that was actually flawed.
        """
        return self.expected_verdict == "rejected" and self.actual_verdict == "approved"


@dataclass
class EvaluationReport:
    """
    Aggregated evaluation results over the full golden test set.

    Metrics treat 'rejected' as the positive class (we are detecting flawed slides):
      - Precision: of slides the judge rejects, what fraction truly have flaws?
      - Recall:    of slides that truly have flaws, what fraction does the judge catch?
      - F1:        harmonic mean of precision and recall.
    """

    results: List[CaseResult] = field(default_factory=list)
    prompt_version: str = "v1"

    # --- Counts ---

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def correct_count(self) -> int:
        return sum(1 for r in self.results if r.correct)

    @property
    def true_positive_count(self) -> int:
        """Judge correctly rejected (expected=rejected, actual=rejected)."""
        return sum(1 for r in self.results if r.is_true_positive)

    @property
    def true_negative_count(self) -> int:
        """Judge correctly approved (expected=approved, actual=approved)."""
        return sum(1 for r in self.results if r.is_true_negative)

    @property
    def false_positive_count(self) -> int:
        """Judge wrongly rejected (expected=approved, actual=rejected)."""
        return sum(1 for r in self.results if r.is_false_positive)

    @property
    def false_negative_count(self) -> int:
        """Judge missed a flaw (expected=rejected, actual=approved)."""
        return sum(1 for r in self.results if r.is_false_negative)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    # --- Verdict-level metrics ---

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.0
        return self.correct_count / self.total

    @property
    def precision(self) -> float:
        """Of slides the judge rejected, what fraction actually had flaws?"""
        denom = self.true_positive_count + self.false_positive_count
        if denom == 0:
            return 0.0
        return self.true_positive_count / denom

    @property
    def recall(self) -> float:
        """Of slides that truly had flaws, what fraction did the judge catch?"""
        denom = self.true_positive_count + self.false_negative_count
        if denom == 0:
            return 0.0
        return self.true_positive_count / denom

    @property
    def f1_score(self) -> float:
        """Harmonic mean of precision and recall."""
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)

    @property
    def false_positive_rate(self) -> float:
        """FP / (FP + TN): proportion of truly-good slides incorrectly rejected."""
        total_actually_approved = sum(1 for r in self.results if r.expected_verdict == "approved")
        if total_actually_approved == 0:
            return 0.0
        return self.false_positive_count / total_actually_approved

    @property
    def false_negative_rate(self) -> float:
        """FN / (FN + TP): proportion of truly-flawed slides missed by the judge."""
        total_actually_rejected = sum(1 for r in self.results if r.expected_verdict == "rejected")
        if total_actually_rejected == 0:
            return 0.0
        return self.false_negative_count / total_actually_rejected

    # --- Per-rule analysis ---

    @property
    def over_firing_rules(self) -> Dict[str, int]:
        """
        Rule IDs that appeared in violations of FALSE POSITIVE cases
        (judge rejected a slide that should have been approved — over-rejection).

        High frequency = that rule is triggering too aggressively.
        Tighten its prompt description to reduce over-rejections.
        """
        freq: Dict[str, int] = defaultdict(int)
        for r in self.results:
            if r.is_false_positive and r.output:
                for v in r.output.violations:
                    freq[v.rule] += 1
        return dict(sorted(freq.items(), key=lambda x: x[1], reverse=True))

    @property
    def under_firing_rules(self) -> Dict[str, int]:
        """
        Rule IDs from expected_violations that were MISSED in FALSE NEGATIVE cases
        (judge approved a slide that should have been rejected — missed flaw).

        Only populated when expected_violations are provided in expected.json.
        High frequency = that rule is not being detected reliably.
        Strengthen its prompt description to improve detection recall.
        """
        freq: Dict[str, int] = defaultdict(int)
        for r in self.results:
            if r.is_false_negative and r.expected_violations:
                actual_rules = (
                    {v.rule for v in r.output.violations} if r.output else set()
                )
                for expected_rule in r.expected_violations:
                    if expected_rule not in actual_rules:
                        freq[expected_rule] += 1
        return dict(sorted(freq.items(), key=lambda x: x[1], reverse=True))

    # Aliases kept for backward compatibility
    @property
    def fn_rule_frequency(self) -> Dict[str, int]:
        """Alias for over_firing_rules (rules causing false positives)."""
        return self.over_firing_rules

    @property
    def fp_rule_frequency(self) -> Dict[str, int]:
        """Alias for under_firing_rules (rules causing false negatives)."""
        return self.under_firing_rules

    @property
    def per_rule_metrics(self) -> Dict[str, Dict[str, int]]:
        """
        Per-rule precision/recall when expected_violations are provided.

        Returns a dict mapping rule_id → {tp, fp, fn} counts, from which
        per-rule precision and recall can be derived.

        Only cases that have expected_violations contribute to this metric.
        """
        counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
        for r in self.results:
            if not r.expected_violations or r.error:
                continue
            actual_rules = {v.rule for v in r.output.violations} if r.output else set()
            expected_rules = set(r.expected_violations)
            for rule in expected_rules | actual_rules:
                in_expected = rule in expected_rules
                in_actual = rule in actual_rules
                if in_expected and in_actual:
                    counts[rule]["tp"] += 1
                elif in_actual and not in_expected:
                    counts[rule]["fp"] += 1
                elif in_expected and not in_actual:
                    counts[rule]["fn"] += 1
        return dict(counts)

    # --- Summary ---

    def summary(self) -> str:
        lines = [
            "=" * 70,
            f"SVG Judge Evaluation Report  [prompt_version={self.prompt_version}]",
            "=" * 70,
            f"Total cases:          {self.total}",
            f"  True positives:     {self.true_positive_count}  (correctly rejected)",
            f"  True negatives:     {self.true_negative_count}  (correctly approved)",
            f"  False positives:    {self.false_positive_count}  (wrongly rejected — over-rejection)",
            f"  False negatives:    {self.false_negative_count}  (missed flaws — under-detection)",
            f"  Errors:             {self.error_count}",
            "",
            "Verdict-level metrics:",
            f"  Accuracy:           {self.accuracy:.1%}",
            f"  Precision:          {self.precision:.1%}  (of rejections, how many were right?)",
            f"  Recall:             {self.recall:.1%}  (of flawed slides, how many caught?)",
            f"  F1 Score:           {self.f1_score:.1%}",
            f"  False positive rate:{self.false_positive_rate:.1%}  (approved-but-rejected)",
            f"  False negative rate:{self.false_negative_rate:.1%}  (rejected-but-approved)",
        ]

        # Rules over-firing (appear in FP violation lists — caused over-rejections)
        over_freq = self.over_firing_rules
        if over_freq:
            lines += [
                "",
                "Rules over-firing (caused over-rejections — tighten prompt description):",
            ]
            for rule, count in over_freq.items():
                lines.append(f"  {rule:<40s}  {count}x")

        # Rules under-firing (missed in FN cases — caused missed flaws)
        under_freq = self.under_firing_rules
        if under_freq:
            lines += [
                "",
                "Rules under-firing (missed in flawed slides — strengthen prompt description):",
            ]
            for rule, count in under_freq.items():
                lines.append(f"  {rule:<40s}  {count}x")

        # Per-rule precision/recall table
        rule_metrics = self.per_rule_metrics
        if rule_metrics:
            lines += [
                "",
                f"{'Rule':<42s} {'TP':>4} {'FP':>4} {'FN':>4} {'Prec':>6} {'Rec':>6}",
                "-" * 70,
            ]
            for rule, m in sorted(rule_metrics.items()):
                tp, fp, fn = m["tp"], m["fp"], m["fn"]
                prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
                rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
                lines.append(
                    f"  {rule:<40s} {tp:>4} {fp:>4} {fn:>4} {prec:>5.0%} {rec:>5.0%}"
                )

        lines += ["", "Per-case results:"]
        for r in self.results:
            if r.error:
                lines.append(f"  {r.case_id:25s}  ERROR: {r.error}")
                continue

            if r.correct:
                status = "✅"
                lines.append(
                    f"  {r.case_id:25s}  expected={r.expected_verdict:8s}  "
                    f"actual={r.actual_verdict:8s}  {status}"
                )
            else:
                tag = "FP (over-rejected)" if r.is_false_positive else "FN (missed flaw)"
                # FP = judge wrongly rejected a good slide (over-rejection)
                # FN = judge wrongly approved a flawed slide (missed flaw)
                lines.append(
                    f"  {r.case_id:25s}  expected={r.expected_verdict:8s}  "
                    f"actual={r.actual_verdict:8s}  ❌ {tag}"
                )
                # Show judge output details for misclassified cases
                if r.output:
                    lines.append(
                        f"    layout_score={r.output.layout_score}  "
                        f"content_fidelity_score={r.output.content_fidelity_score}  "
                        f"confidence={r.output.confidence:.2f}"
                    )
                    if r.output.violations:
                        lines.append(f"    Violations ({len(r.output.violations)}):")
                        for v in r.output.violations:
                            containers = (
                                f" [{', '.join(v.affected_containers)}]"
                                if v.affected_containers
                                else ""
                            )
                            lines.append(
                                f"      [{v.severity.upper()}] {v.rule}{containers}: {v.description}"
                            )
                    else:
                        lines.append("    Violations: none reported")
                    if r.output.critique:
                        lines.append(f"    Critique (→ {r.output.critique_target}):")
                        for line in r.output.critique.split("\n"):
                            lines.append(f"      {line}")

        lines.append("=" * 70)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_image_as_data_uri(path: Path) -> str:
    """Load a PNG file and return it as a base64 data-URI."""
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _load_golden_cases(golden_dir: Path) -> List[GoldenCase]:
    """
    Scan golden_dir for test cases.

    Each case is a subdirectory containing:
        sketch.png, source.png, expected.json
    and optionally:
        metadata.json
    """
    cases: List[GoldenCase] = []
    for case_dir in sorted(golden_dir.iterdir()):
        if not case_dir.is_dir():
            continue

        sketch = case_dir / "sketch.png"
        source = case_dir / "source.png"
        expected_file = case_dir / "expected.json"

        if not (sketch.exists() and source.exists() and expected_file.exists()):
            logger.warning(
                f"Skipping {case_dir.name}: missing sketch.png, source.png, or expected.json"
            )
            continue

        expected = json.loads(expected_file.read_text())
        verdict = expected.get("verdict", "")
        if verdict not in ("approved", "rejected"):
            logger.warning(
                f"Skipping {case_dir.name}: expected.json verdict must be 'approved' or 'rejected', got '{verdict}'"
            )
            continue

        metadata: dict = {}
        metadata_file = case_dir / "metadata.json"
        if metadata_file.exists():
            metadata = json.loads(metadata_file.read_text())

        cases.append(
            GoldenCase(
                case_id=case_dir.name,
                sketch_png_path=sketch,
                source_png_path=source,
                expected_verdict=verdict,
                layout_description=metadata.get("layout_description", ""),
                layout_option_id=metadata.get("layout_option_id", "OPT-1"),
                notes=expected.get("notes", ""),
                expected_violations=expected.get("expected_violations", []),
                quality_check_errors=metadata.get("quality_check_errors", []),
                quality_check_warnings=metadata.get("quality_check_warnings", []),
            )
        )

    return cases


# ---------------------------------------------------------------------------
# Main evaluation function
# ---------------------------------------------------------------------------


def evaluate_judge(
    agent: SVGJudgeAgent,
    golden_dir: Path,
) -> EvaluationReport:
    """
    Run the judge on all golden test cases in golden_dir.

    Args:
        agent: Configured SVGJudgeAgent instance.
        golden_dir: Path to the directory containing golden test case subdirectories.

    Returns:
        EvaluationReport with accuracy, precision, recall, F1, per-rule frequency,
        and per-case results with violation details on misclassified cases.
    """
    cases = _load_golden_cases(golden_dir)
    if not cases:
        logger.warning(f"No valid golden test cases found in {golden_dir}")
        return EvaluationReport(prompt_version=agent.config.prompt_version)

    logger.info(f"Running evaluation on {len(cases)} golden test cases...")
    report = EvaluationReport(prompt_version=agent.config.prompt_version)

    for case in cases:
        logger.info(f"  Evaluating case: {case.case_id}")
        try:
            sketch_uri = _load_image_as_data_uri(case.sketch_png_path)
            source_uri = _load_image_as_data_uri(case.source_png_path)

            output = agent.invoke(
                SVGJudgeInput(
                    sketch_png=sketch_uri,
                    source_png=source_uri,
                    layout_description=case.layout_description,
                    layout_option_id=case.layout_option_id,
                    quality_check_errors=case.quality_check_errors,
                    quality_check_warnings=case.quality_check_warnings,
                )
            )

            report.results.append(
                CaseResult(
                    case_id=case.case_id,
                    expected_verdict=case.expected_verdict,
                    actual_verdict=output.verdict,
                    output=output,
                    expected_violations=case.expected_violations,
                )
            )
        except Exception as exc:
            logger.error(f"  Case {case.case_id} failed: {exc}")
            report.results.append(
                CaseResult(
                    case_id=case.case_id,
                    expected_verdict=case.expected_verdict,
                    actual_verdict="error",
                    output=None,
                    expected_violations=case.expected_violations,
                    error=str(exc),
                )
            )

    logger.info(
        f"Evaluation complete. "
        f"Accuracy={report.accuracy:.1%}  "
        f"Precision={report.precision:.1%}  "
        f"Recall={report.recall:.1%}  "
        f"F1={report.f1_score:.1%}"
    )
    return report
