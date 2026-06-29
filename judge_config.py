"""
judge_module/judge_config.py

Configurable quality thresholds for the SVG Judge.

These thresholds control when a slide is approved vs. rejected.
Adjust them as the judge accuracy improves over time.
"""

from pydantic import BaseModel, Field


class JudgeThresholds(BaseModel):
    """
    Quality thresholds used by the judge to determine approval.

    The judge approves a slide if ALL of the following hold:
      - No critical violations
      - Total major violations <= max_major_violations
      - layout_score >= min_layout_score
      - content_fidelity_score >= min_content_fidelity_score
    """

    max_major_violations: int = Field(
        default=2,
        description="Maximum number of major violations allowed for approval.",
    )
    min_layout_score: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Minimum layout_score (1–5) required for approval.",
    )
    min_content_fidelity_score: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Minimum content_fidelity_score (1–5) required for approval.",
    )
    enable_by_default: bool = Field(
        default=False,
        description=(
            "Whether the judge is enabled by default in the pipeline. "
            "Set to True once judge accuracy is sufficient (target: >= 80% on golden test set)."
        ),
    )

    def is_approved(
        self,
        layout_score: int,
        content_fidelity_score: int,
        critical_count: int,
        major_count: int,
    ) -> bool:
        """
        Determine approval based on scores and violation counts.

        Args:
            layout_score: Layout quality score (1–5).
            content_fidelity_score: Content fidelity score (1–5).
            critical_count: Number of critical violations.
            major_count: Number of major violations.

        Returns:
            True if the slide meets all approval thresholds.
        """
        if critical_count > 0:
            return False
        if major_count > self.max_major_violations:
            return False
        if layout_score < self.min_layout_score:
            return False
        if content_fidelity_score < self.min_content_fidelity_score:
            return False
        return True


# Default thresholds — used by the evaluation harness
DEFAULT_THRESHOLDS = JudgeThresholds()
