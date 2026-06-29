"""
judge_module — Standalone slide quality judge.

Public API:
    SVGJudgeAgent      — the judge agent (invoke to evaluate a slide)
    SVGJudgeConfig     — agent configuration (LLM, retries, prompt_version, thresholds)
    SVGJudgeInput      — input schema (sketch_png, source_png, ...)
    SVGJudgeOutput     — output schema (verdict, scores, violations, critique, confidence, reasoning)
    SVGJudgeViolation  — single violation record
    SVGJudgePrompts    — prompt container (override system/human prompts)
    JudgeThresholds    — approval thresholds (max_major_violations, min scores)
"""

from .judge_agent import (
    SVGJudgeAgent,
    SVGJudgeConfig,
    SVGJudgeInput,
    SVGJudgeOutput,
    SVGJudgeViolation,
    SVGJudgePrompts,
)
from .judge_config import JudgeThresholds

__all__ = [
    "SVGJudgeAgent",
    "SVGJudgeConfig",
    "SVGJudgeInput",
    "SVGJudgeOutput",
    "SVGJudgeViolation",
    "SVGJudgePrompts",
    "JudgeThresholds",
]
