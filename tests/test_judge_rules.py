"""
judge_module/tests/test_judge_rules.py

Unit tests for judge_rules.py — no LLM calls required.
"""

import pytest
from judge_module.judge_rules import (
    ALL_RULES,
    RULES_BY_ID,
    get_rules_for_container,
    JudgeRule,
)
from judge_module.judge_config import JudgeThresholds


# ---------------------------------------------------------------------------
# Rule registry tests
# ---------------------------------------------------------------------------


def test_all_rules_have_unique_ids():
    ids = [r.rule_id for r in ALL_RULES]
    assert len(ids) == len(set(ids)), "Duplicate rule IDs found"


def test_rules_by_id_lookup():
    assert "space_utilization" in RULES_BY_ID
    assert "table_row_count" in RULES_BY_ID
    assert "text_bullet_count" in RULES_BY_ID


def test_get_rules_for_text_container():
    rules = get_rules_for_container("text")
    rule_ids = {r.rule_id for r in rules}
    # Text-specific rules must be present
    assert "text_fill_ratio_critical" in rule_ids
    assert "text_fill_ratio_major" in rule_ids
    assert "text_overflow" in rule_ids
    assert "text_bullet_count" in rule_ids
    # "any" rules must also be present
    assert "space_utilization" in rule_ids
    assert "overlap" in rule_ids


def test_get_rules_for_table_container():
    rules = get_rules_for_container("table")
    rule_ids = {r.rule_id for r in rules}
    assert "table_row_count" in rule_ids
    assert "table_column_count" in rule_ids
    assert "table_cell_transposition" in rule_ids
    # Text-specific rules must NOT be present
    assert "text_fill_ratio_critical" not in rule_ids


def test_auto_reject_rules_are_critical():
    for rule in ALL_RULES:
        if rule.auto_reject:
            assert rule.severity == "critical", (
                f"Rule '{rule.rule_id}' has auto_reject=True but severity='{rule.severity}' "
                "(auto-reject rules must be critical)"
            )


# ---------------------------------------------------------------------------
# JudgeThresholds tests
# ---------------------------------------------------------------------------


def test_thresholds_approve_clean_slide():
    t = JudgeThresholds()
    assert t.is_approved(
        layout_score=4,
        content_fidelity_score=4,
        critical_count=0,
        major_count=0,
    )


def test_thresholds_reject_on_critical():
    t = JudgeThresholds()
    assert not t.is_approved(
        layout_score=5,
        content_fidelity_score=5,
        critical_count=1,
        major_count=0,
    )


def test_thresholds_reject_on_too_many_major():
    t = JudgeThresholds(max_major_violations=2)
    assert not t.is_approved(
        layout_score=4,
        content_fidelity_score=4,
        critical_count=0,
        major_count=3,
    )


def test_thresholds_approve_at_major_limit():
    t = JudgeThresholds(max_major_violations=2)
    assert t.is_approved(
        layout_score=4,
        content_fidelity_score=4,
        critical_count=0,
        major_count=2,
    )


def test_thresholds_reject_on_low_layout_score():
    t = JudgeThresholds(min_layout_score=3)
    assert not t.is_approved(
        layout_score=2,
        content_fidelity_score=4,
        critical_count=0,
        major_count=0,
    )


def test_thresholds_reject_on_low_fidelity_score():
    t = JudgeThresholds(min_content_fidelity_score=3)
    assert not t.is_approved(
        layout_score=4,
        content_fidelity_score=2,
        critical_count=0,
        major_count=0,
    )


def test_thresholds_custom_values():
    t = JudgeThresholds(
        max_major_violations=0,
        min_layout_score=4,
        min_content_fidelity_score=4,
    )
    # Strict: no major violations allowed, scores must be >= 4
    assert t.is_approved(
        layout_score=4, content_fidelity_score=4, critical_count=0, major_count=0
    )
    assert not t.is_approved(
        layout_score=4, content_fidelity_score=4, critical_count=0, major_count=1
    )
    assert not t.is_approved(
        layout_score=3, content_fidelity_score=4, critical_count=0, major_count=0
    )
