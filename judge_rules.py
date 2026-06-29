"""
judge_module/judge_rules.py

Rule definitions for the SVG Judge, separated from the agent prompt.

This module defines the rule identifiers and their metadata so that:
  1. The evaluation harness can reference rules by ID.
  2. Rule sets can be extended per container type without editing the agent.
  3. Future per-container-type judges can import only the rules they need.

Rule IDs match the `rule` field in SVGJudgeViolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal


ContainerType = Literal["text", "table", "chart", "mixed_elements", "image", "any"]
Severity = Literal["critical", "major", "minor"]


@dataclass
class JudgeRule:
    """Metadata for a single judge rule."""

    rule_id: str
    description: str
    severity: Severity
    container_types: List[ContainerType]  # which container types this rule applies to
    dimension: Literal["layout", "content_fidelity"]
    auto_reject: bool = False  # True = single violation causes immediate rejection


# ---------------------------------------------------------------------------
# Layout rules
# ---------------------------------------------------------------------------

LAYOUT_RULES: List[JudgeRule] = [
    JudgeRule(
        rule_id="space_utilization",
        description="Containers must cover >= 80% of slide area. No large empty regions (> 8%) without a decorative layer.",
        severity="critical",
        container_types=["any"],
        dimension="layout",
        auto_reject=True,
    ),
    JudgeRule(
        rule_id="proportion_balance",
        description="Container sizes must match content volume. Heavy content gets proportionally more space.",
        severity="major",
        container_types=["any"],
        dimension="layout",
    ),
    JudgeRule(
        rule_id="alignment",
        description="Container edges must align to a consistent grid. Side-by-side containers must have matching top edges and heights.",
        severity="major",
        container_types=["any"],
        dimension="layout",
    ),
    JudgeRule(
        rule_id="reading_flow",
        description="Arrangement must support natural L→R, T→B reading order. Most important container at eye-landing point.",
        severity="minor",
        container_types=["any"],
        dimension="layout",
    ),
    JudgeRule(
        rule_id="fit_feasibility",
        description="Content must plausibly fit within container dimensions. No container too small for its content.",
        severity="critical",
        container_types=["any"],
        dimension="layout",
        auto_reject=True,
    ),
    JudgeRule(
        rule_id="text_fill_ratio_critical",
        description="TEXT container text block occupies < 40% of container inner height (severely under-filled).",
        severity="critical",
        container_types=["text"],
        dimension="layout",
        auto_reject=True,
    ),
    JudgeRule(
        rule_id="text_fill_ratio_major",
        description="TEXT container text block occupies 40–65% of container inner height with visible empty band at bottom.",
        severity="major",
        container_types=["text"],
        dimension="layout",
    ),
    JudgeRule(
        rule_id="text_overflow",
        description="TEXT container text is visibly clipped or overflows the container border.",
        severity="critical",
        container_types=["text"],
        dimension="layout",
        auto_reject=True,
    ),
    JudgeRule(
        rule_id="overlap",
        description="Content containers must not overlap (unless layout description explicitly permits it).",
        severity="critical",
        container_types=["any"],
        dimension="layout",
        auto_reject=True,
    ),
    JudgeRule(
        rule_id="total_content_area",
        description="Total content area must be >= 60% of slide area.",
        severity="critical",
        container_types=["any"],
        dimension="layout",
        auto_reject=True,
    ),
]

# ---------------------------------------------------------------------------
# Content fidelity rules
# ---------------------------------------------------------------------------

CONTENT_FIDELITY_RULES: List[JudgeRule] = [
    JudgeRule(
        rule_id="table_row_count",
        description="TABLE must have the same number of rows as the source data.",
        severity="critical",
        container_types=["table"],
        dimension="content_fidelity",
        auto_reject=True,
    ),
    JudgeRule(
        rule_id="table_column_count",
        description="TABLE must have the same number of columns as the source data.",
        severity="critical",
        container_types=["table"],
        dimension="content_fidelity",
        auto_reject=True,
    ),
    JudgeRule(
        rule_id="table_cell_transposition",
        description="TABLE cell content must appear in the correct column AND row — no swapped or shifted cells.",
        severity="critical",
        container_types=["table"],
        dimension="content_fidelity",
        auto_reject=True,
    ),
    JudgeRule(
        rule_id="table_header_distinction",
        description="TABLE header row must be visually distinct from data rows.",
        severity="major",
        container_types=["table"],
        dimension="content_fidelity",
    ),
    JudgeRule(
        rule_id="text_bullet_count",
        description="TEXT container must have the same number of bullet points as the source.",
        severity="critical",
        container_types=["text"],
        dimension="content_fidelity",
        auto_reject=True,
    ),
    JudgeRule(
        rule_id="text_hierarchy",
        description="TEXT content hierarchy must be preserved (headings remain headings, etc.).",
        severity="major",
        container_types=["text"],
        dimension="content_fidelity",
    ),
    JudgeRule(
        rule_id="chart_type",
        description="CHART must use the same chart type as the source (bar, column, line, pie).",
        severity="major",
        container_types=["chart"],
        dimension="content_fidelity",
    ),
    JudgeRule(
        rule_id="chart_labels",
        description="CHART must have correct axis labels and series names.",
        severity="minor",
        container_types=["chart"],
        dimension="content_fidelity",
    ),
    JudgeRule(
        rule_id="mixed_elements_node_count",
        description="MIXED_ELEMENTS container must have at least half the node count of the source.",
        severity="major",
        container_types=["mixed_elements"],
        dimension="content_fidelity",
    ),
    JudgeRule(
        rule_id="image_placeholder",
        description="IMAGE container must have a placeholder rect in approximately the correct region.",
        severity="minor",
        container_types=["image"],
        dimension="content_fidelity",
    ),
]

# ---------------------------------------------------------------------------
# Combined lookup
# ---------------------------------------------------------------------------

ALL_RULES: List[JudgeRule] = LAYOUT_RULES + CONTENT_FIDELITY_RULES

RULES_BY_ID: Dict[str, JudgeRule] = {r.rule_id: r for r in ALL_RULES}


def get_rules_for_container(container_type: ContainerType) -> List[JudgeRule]:
    """Return all rules that apply to a given container type."""
    return [
        r for r in ALL_RULES
        if container_type in r.container_types or "any" in r.container_types
    ]
