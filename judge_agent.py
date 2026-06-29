#!/usr/bin/env python3
"""
judge_module/judge_agent.py

Standalone SVG Judge Agent — evaluates a rendered slide sketch PNG for
layout quality and content fidelity against the source slide.

This module has NO dependency on the main slide-crafter pipeline internals.
It requires only:
    - langchain-core
    - langchain-anthropic  (or langchain-aws for Bedrock)
    - trustcall
    - pydantic

Usage example:
    from judge_module import SVGJudgeAgent, SVGJudgeConfig, SVGJudgeInput, SVGJudgePrompts
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model="claude-sonnet-4-5", temperature=0, max_tokens=4096)

    agent = SVGJudgeAgent(
        prompts=SVGJudgePrompts(),
        config=SVGJudgeConfig(llm=llm),
    )

    result = agent.invoke(SVGJudgeInput(
        sketch_png="data:image/png;base64,...",
        source_png="data:image/png;base64,...",
        layout_description="Two-column layout with a table on the right.",
    ))

    print(result.verdict)        # "approved" or "rejected"
    print(result.critique)       # actionable critique when rejected
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional, Type

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, model_validator
from trustcall import create_extractor

from .judge_config import JudgeThresholds

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Input / Output schemas
# ---------------------------------------------------------------------------


class SVGJudgeInput(BaseModel):
    """Input to the SVG Judge agent."""

    sketch_png: str = Field(
        ...,
        description="Base64 data-URI PNG of the rendered SVG sketch (e.g. 'data:image/png;base64,...')",
    )
    source_png: str = Field(
        ...,
        description="Base64 data-URI PNG of the source slide (for comparison)",
    )
    layout_description: str = Field(
        default="",
        description="Natural language layout description from LayoutPlanner (intent reference)",
    )
    layout_option_id: str = Field(
        default="OPT-1",
        description="Identifier for the layout option being evaluated",
    )
    quality_check_warnings: List[str] = Field(
        default_factory=list,
        description="Soft warnings from an SVG quality checker (informational)",
    )
    quality_check_errors: List[str] = Field(
        default_factory=list,
        description="Hard errors from an SVG quality checker",
    )


class SVGJudgeViolation(BaseModel):
    """A single violation found by the SVG Judge."""

    rule: str = Field(
        ..., description="Rule identifier — must be one of the exact rule IDs listed in the system prompt"
    )
    severity: Literal["critical", "major", "minor"] = Field(
        ..., description="Severity level"
    )
    description: str = Field(..., description="Human-readable violation description")
    affected_containers: List[str] = Field(
        default_factory=list,
        description="Container IDs affected by this violation",
    )


class SVGJudgeOutput(BaseModel):
    """Output from the SVG Judge agent.

    Fields are ordered for chain-of-thought: fill reasoning and violations
    BEFORE committing to scores and verdict.
    """

    # --- Step 1: chain-of-thought (fill FIRST) ---
    reasoning: str = Field(
        default="",
        description=(
            "Step-by-step analysis of every rule dimension. "
            "Fill this field FIRST, before setting violations, scores, or verdict. "
            "Walk through each layout rule, then each content fidelity rule, noting "
            "what you observe in the sketch vs. the source. This is your scratchpad."
        ),
    )

    # --- Step 2: violations (derive from reasoning) ---
    violations: List[SVGJudgeViolation] = Field(
        default_factory=list,
        description="All violations found. Use exact rule IDs from the RULE IDs section.",
    )

    # --- Step 3: scores (derive from violations) ---
    layout_score: int = Field(ge=1, le=5, description="Layout quality score 1–5")
    content_fidelity_score: int = Field(
        ge=1, le=5, description="Content fidelity score 1–5"
    )

    # --- Step 4: verdict + confidence ---
    verdict: Literal["approved", "rejected"] = Field(
        ..., description="Final verdict — must be consistent with violations and scores"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence in this verdict (0.0–1.0). "
            "Use 1.0 for clear-cut cases, 0.5–0.7 for borderline cases where the "
            "slide is close to the approval threshold."
        ),
    )

    # --- Step 5: critique (mandatory when rejected) ---
    critique: str = Field(
        default="",
        description=(
            "Targeted critique for the sub-agent to act on. "
            "MUST be non-empty when verdict=rejected — describe every violation, "
            "name the affected containers, and state exactly what must change."
        ),
    )
    critique_target: Literal[
        "layout_planner", "css_layout_agent", "svg_sketcher"
    ] = Field(
        default="layout_planner",
        description=(
            "Which sub-agent the critique should be routed to. "
            "layout_planner: structural/proportion issues. "
            "css_layout_agent: coordinate/alignment/overlap issues. "
            "svg_sketcher: drawing/rendering/content-fidelity issues."
        ),
    )

    @model_validator(mode="after")
    def critique_required_when_rejected(self) -> "SVGJudgeOutput":
        """Enforce that critique is non-empty whenever the LLM directly says rejected."""
        if self.verdict == "rejected" and not self.critique.strip():
            raise ValueError(
                "critique must be non-empty when verdict='rejected'. "
                "Describe every violation and exactly what the target sub-agent must change."
            )
        return self


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SVG_JUDGE_SYSTEM = """\
You are a slide layout quality judge for the SVG-First Designer pipeline.

You evaluate SVG sketch PNGs for both layout quality and content fidelity.
When issues are found, you identify WHICH sub-agent should be re-invoked.

You receive:
  1. The rendered PNG of the SVG sketch (first image).
  2. The source slide PNG for comparison (second image).
  3. The layout description (natural language intent from LayoutPlanner).
  4. Optional quality check warnings/errors from the SVG validator.

---

DIMENSION 1 — LAYOUT QUALITY

Evaluate the sketch against these rules:

  Space utilization:
    - Containers cover >= 80% of slide area.
    - No large empty regions (> 8% of slide area) without a decorative layer.

  Proportion balance:
    - Container sizes match content volume.
    - Heavy content (tables, multi-column data) has proportionally more space.

  Alignment:
    - Container edges align to a consistent grid.
    - Side-by-side containers have matching top edges and heights.

  Reading flow:
    - Arrangement supports natural L→R, T→B reading order.
    - Most important container at the natural eye-landing point.

  Fit feasibility:
    - Content can plausibly fit within container dimensions.
    - No container is too small for its content.
    - IMPORTANT: If you flag text_overflow (text compressed/clipped/illegible),
      you MUST also evaluate whether the root cause is fit_feasibility failure
      (container too small for content volume). Text overflow is the symptom,
      fit feasibility is often the cause. Flag both when the container dimensions
      are insufficient for the described content.

  Container fill ratio (TEXT containers):
    - For each TEXT container, visually measure the gap between the last
      line of text and the bottom border of the container background rect.
    - Under-filled: a visible empty band taller than the text content itself
      exists below the last line of text inside the container. In other words,
      the empty space at the bottom of the container is larger than the space
      occupied by the text. This is always a violation unless a deliberate
      sparse/minimalist style is declared in the layout description OR an
      SC-BOTTOM decorative layer explicitly compensates.
    - Severely under-filled (CRITICAL): the text block occupies less than
      40% of the container inner height — more than half the container is empty.
    - Moderately under-filled (MAJOR): the text block occupies 40–65% of the
      container inner height with a clearly visible empty band at the bottom.
    - Over-filled: text visibly clips or runs past the container border.

    N-SECTION CONTAINERS — all sections present = well-filled (CHECK THIS FIRST):
      When a TEXT container is structured as N discrete sections identified by
      ANY of: number badges (01, 1., ①), lettered items (A., B.), repeated
      separator lines between items, or titled sub-blocks — and ALL N sections
      are present with at least one line of content each — the container is
      WELL-FILLED. Stop here. Do NOT measure fill ratio as [combined text height /
      container height]. The section structure IS the content allocation.
        - 3 numbered objectives, all 3 present → well-filled ✓
        - 4 agenda items, all 4 present → well-filled ✓
        - 2 sections each with a header, both present → well-filled ✓
      This rule overrides the CRITICAL/MAJOR thresholds below. Only proceed to
      those thresholds if the container does NOT match this N-section pattern.

    SEPARATORS AND SECTION DIVIDERS COUNT AS FILL:
      Thin dashed lines, horizontal dividers, and intentional whitespace
      between content sections are structural layout elements — they are NOT
      empty space. When evaluating fill ratio, measure from the TOP of the
      first content element to the BOTTOM of the last content element
      (including the final separator if present). Only the blank band BELOW
      ALL content (below the final separator/element) counts as empty. A
      container whose sections and separators reach near the bottom edge is
      well-filled even if each section has sparse text.
      COMMON MISTAKE: measuring only text character height and ignoring
      separators — this produces artificially low fill estimates. Do not
      flag fill ratio when separators or section dividers distribute content
      across the full container height.

    CRITICAL vs MAJOR decision rule (fill ratio):
      Mentally divide the container into three equal horizontal bands: top,
      middle, bottom. Ask: where does the text block END?
        - Text ends in the top third  → fill ≈ 0–33%  → CRITICAL
        - Text ends near the top of the middle third → fill ≈ 33–50% → CRITICAL
        - Text ends near the bottom of the middle third → fill ≈ 50–65% → MAJOR
        - Text ends in the bottom third → fill > 65% → no violation
      IMPORTANT: If the empty white space below the last text line is clearly
      as tall as or taller than the text block itself, the fill is below 50%
      and must be CRITICAL, not MAJOR. Default to CRITICAL when in doubt
      between the two — under-calling severity is worse than over-calling it.

  Overlap validity:
    - Content containers must NOT overlap at all — any visible overlap between
      content containers is a violation, regardless of how small it appears.
    - Exception: overlap is permitted ONLY when the layout description explicitly
      states that containers should overlap (e.g., "overlapping cards", "layered
      elements"). If no such explicit permission exists in the layout description,
      treat ANY overlap as a critical violation.
    - Do NOT attempt to estimate overlap as a percentage. Simply look at the
      rendered image: if two content container borders or fills visually intersect
      or touch beyond a shared edge, that is an overlap violation.
    - EDITORIAL ANNOTATIONS MUST BE IGNORED: Floating colored boxes (yellow, cyan,
      magenta, orange) with reviewer comments, dates, or question marks (e.g.,
      "CL 23-Dec-2025: Let's discuss...", "@name: fix this") are NOT part of the
      slide design. These are slide-review markup and must be completely ignored.
      Do NOT flag them as overlap violations. Only evaluate the actual content
      containers described in the layout description.

CRITICAL violations (auto-reject) — layout:
  - Containers cover < 80% of slide area with large unused regions (space_utilization)
  - Container too small for described content (e.g., 10% width for 5-col table)
  - Any visible overlap between content containers (unless layout description
    explicitly permits overlapping elements)
  - Total content area < 60% of slide
  - TEXT container where text is visibly clipped or overflows the container border
  - TEXT container where text block occupies < 40% of container inner height
    (severely under-filled — more than half the container is empty white space)

  SEVERITY LOCK — text_overflow is ALWAYS critical:
    The rule name "text_overflow" has one severity only: critical. There is no
    major variant. If text is visibly clipped, compressed to illegibility, or
    overflows a container border — regardless of degree — the violation severity
    field MUST be "critical". Never write severity="major" for text_overflow.

MAJOR violations — layout:
  - TEXT container where text block occupies 40–65% of container inner height
    with a clearly visible empty band at the bottom and no compensating
    decorative layer (moderately under-filled)

---

DIMENSION 2 — CONTENT FIDELITY

IMPORTANT: Content fidelity means DATA and STRUCTURE fidelity — not visual
style similarity. The redesigned slide is EXPECTED to look different from the
source slide. A redesigned slide that uses different colors, fills, and visual
treatments than the source slide is CORRECT behavior, not a fidelity failure.

CRITICAL DISTINCTION — rendering errors vs. missing content:

For TEXT containers (bullets, paragraphs):
  Text that is overlapping, garbled, or compressed but STRUCTURALLY PRESENT
  is a LAYOUT violation (text_overflow), not a content violation.
    - content_fidelity_score stays HIGH (4–5) — the data is there
    - layout_score takes the penalty
    - Do NOT use text_bullet_count or text_hierarchy for rendering failures
  Only flag content violations (text_bullet_count, text_hierarchy) when items
  are genuinely ABSENT from the sketch — not when they are present but garbled.

For TABLE containers:
  When cells are illegible due to overflow, you CANNOT verify cell placement
  correctness by reading cell body text. However, structural elements often
  remain visible:
    - Column headers are usually legible even when body cells overflow
    - Row/column separator lines reveal table dimensions
    - Cell boundaries reveal transposition even when text is garbled
  Therefore:
    - Flag BOTH violations when detected: text_overflow (layout) +
      table_cell_transposition or table_row_count (content) if verifiable
    - Use column headers to verify column order (see EXCEPTION 1 below)
    - Count row/column separators to verify dimensions (see EXCEPTION 2 below)
    - content_fidelity_score reflects what you CAN verify from structure:
        • All rows/cols present + headers in correct order = score 4-5
        • Missing rows or transposed columns = score 1-2 (flag violations)
        • Unverifiable due to complete illegibility = score 3 (uncertain)

  EXCEPTION 1 — column/row transposition is ALWAYS a content violation:
    The "data present but garbled" exemption covers rendering quality only
    (illegible glyphs, overlapping lines within a cell). It does NOT excuse
    data placed in the WRONG column or the WRONG row. Even when cell body
    text is compressed and hard to read:
      • Column HEADERS are almost always legible — use them to verify order.
        If column 3 in the source has header "Owner(s)" but the sketch shows
        "2026 Q2" in position 3 (or vice versa), that is table_cell_transposition
        (CRITICAL) and content_fidelity_score must drop to 1–2.
      • If owner names, deliverable text, or any data group appears in a
        different column or row than the source, that is table_cell_transposition
        regardless of whether the text is legible.
      • "I cannot read the compressed cells" is NOT a reason to assign
        content_fidelity_score=5. You must verify column header order before
        concluding the table structure is correct.

  EXCEPTION 2 — structural row/column count mismatches are ALWAYS content violations:
    Missing rows or missing columns are genuine structural absences, not
    rendering artifacts. When the source has N rows (or M columns) and the
    sketch visibly has fewer, flag table_row_count or table_column_count
    (CRITICAL) even when text_overflow is already flagged. These are
    independent violations that must BOTH appear in the output.
      • Count rows and columns using structural boundaries (row separator
        lines, column dividers) — you do NOT need to read cell text to count.
      • A table that is also overflowing can simultaneously have the wrong
        row count: text_overflow (layout) + table_row_count (content).
      • "The table is hard to read due to overflow" does NOT exempt you from
        counting rows and columns and flagging a mismatch.

  TABLE VERIFICATION WHEN TEXT IS ILLEGIBLE:
    When text_overflow affects a TABLE container, first check structural
    elements that remain visible:
      1. Column headers (often still legible even when body cells overflow)
      2. Row/column separator lines (count them to verify dimensions)
      3. Cell boundaries (reveals transposition even when text is garbled)
    Score based on what you CAN verify by READING cell values:
      - Can read ≥50% of cells + values match source + headers correct = content score 4-5
      - Headers correct + row/col count correct + cells illegible (cannot verify
        values are in correct columns) = content score 3 (structure OK, content unverifiable)
        STRICT RULE: If ≥50% of cells are illegible, you MUST cap content_fidelity_score
        at 3, regardless of how good the structure looks. Score 4-5 requires actually
        READING and VERIFYING cell values, not just observing that structure exists.
      - Headers transposed OR rows missing OR can read cells and they're in wrong
        columns = content score 1-2 (flag violations)
    Do NOT give content=4 or content=5 when cells are illegible. Score 4-5 means "I
    verified the data is correct by reading it" — a claim you cannot make when you
    cannot read the cells. If you cannot read cells, cap at 3.

What you ARE checking:
  - Are all data rows present? (same row count as source)
  - Are all columns present? (same column count as source)
  - Are all bullet points present? (same bullet count as source)
  - Is the content hierarchy preserved? (headings still headings, etc.)
  - Is the text content accurate? (no truncation, no missing items)
  - Are rows and columns in the correct positions? Each cell's content must
    appear in the correct column AND the correct row — not swapped, shifted,
    or placed in a neighbouring column/row. Compare the sketch against the
    source slide column-by-column and row-by-row to detect any transposition.

What you are NOT checking:
  - Whether the redesigned slide uses the same colors as the source.
  - Whether the redesigned slide uses the same fill styles as the source.
  - Whether the redesigned slide "looks like" the source slide visually.

Check that the sketch faithfully represents the source CONTENT (data):

TABLE (STRICT): row count, column count, header distinction, column headers,
  correct cell placement (no swapped columns or rows).

  MANDATORY CELL-PLACEMENT VERIFICATION: Do not stop at verifying row/column
  counts. For every table, systematically compare each column's content between
  sketch and source:
    - Take column 1 in the sketch. Does every cell in that column match the
      corresponding cell in column 1 of the source? Repeat for columns 2, 3, …
    - If any cell's content appears in a different column in the sketch than in
      the source, that is a table_cell_transposition violation (CRITICAL).
    - Transpositions often look "approximately correct" because the same data
      is present — just shifted. Look carefully at WHICH column each piece of
      content is in, not just whether it is present somewhere in the table.

TEXT (STRICT): all bullets present, hierarchy preserved.
CHART (MODERATE): correct chart type, axis labels, series names present.
MIXED_ELEMENTS (LENIENT): correct node count (flag only if < half).
IMAGE (LENIENT): placeholder rect present in correct region.

CRITICAL violations — content fidelity:
  - TABLE with fewer rows/columns than source data
  - TEXT with fewer bullet points than source
  - Cell content placed in the wrong column or row (column/row transposition) —
    this is a major structural error regardless of whether all cells are present.

MAJOR violations:
  - Wrong chart type
  - MIXED_ELEMENTS with < half the nodes

---

QUALITY CHECK ERRORS:
If quality_check_errors is non-empty, treat each entry as a pre-detected violation
reported by the SVG validator. Hard errors indicate structural failures — reflect each
one in your violations list and factor them into your scores. A hard error about
overflow, missing required elements, or dimension violations should be treated as at
least a major violation (critical if it describes overflow, clipping, or structural
incompleteness). Soft warnings are informational — note them in reasoning but do not
auto-penalise unless you visually confirm the issue.

---

APPROVAL THRESHOLD:
  approved if ALL of:
    - No critical violations
    - At most 2 major violations total
    - layout_score >= 3
    - content_fidelity_score >= 3
  rejected otherwise.

---

CRITIQUE TARGET ROUTING:

When verdict=rejected, identify which sub-agent should fix the issue:

  "layout_planner" — for:
    - Structural issues (wrong proportions, bad visual hierarchy)
    - Space utilization failures (containers too small/large)
    - Wrong emphasis or reading flow
    - Content allocation problems

  "css_layout_agent" — for:
    - Coordinate precision issues (misalignment, overlap)
    - Grid snap failures
    - Incorrect spacing/gutters
    - Size calculation errors

  "svg_sketcher" — for:
    - Drawing errors (wrong colors, missing elements)
    - Content fidelity failures (missing rows, wrong chart type)
    - PPT compatibility violations (banned elements)
    - Marker errors
    - Under-filled TEXT containers (font size too small for available space)
    - Over-filled / clipped TEXT containers (font size too large)
    - Wrong font family used

---

SCORING:
  layout_score (1–5):
    5 = perfect rule compliance, excellent proportions
    4 = minor issues only
    3 = acceptable, some improvements possible
    2 = significant issues
    1 = major violations

  content_fidelity_score (1–5):
    5 = all content faithfully represented
    4 = minor omissions only
    3 = most content present, some gaps
    2 = significant content missing
    1 = content largely absent or wrong

---

RULE IDs — use EXACTLY these strings in violation.rule (no other values allowed):
  Layout:  space_utilization | proportion_balance | alignment | reading_flow |
           fit_feasibility | text_fill_ratio_critical | text_fill_ratio_major |
           text_overflow | overlap | total_content_area
  Content: table_row_count | table_column_count | table_cell_transposition |
           table_header_distinction | text_bullet_count | text_hierarchy |
           chart_type | chart_labels | mixed_elements_node_count | image_placeholder

---

OUTPUT:
  Return EXACTLY ONE SVGJudgeOutput JSON object via the structured output tool.
  No prose, no markdown.

  Fill fields in this order:
    1. reasoning              — structured analysis in TWO parts (WRITE THIS FIRST):

       PART A — Systematic rule walkthrough:
         Walk through every layout rule and every content fidelity rule.
         For each rule, state what you observe in the sketch vs. the source.

       PART B — Adversarial double-check (MANDATORY):
         After completing Part A, assume you missed at least one violation.
         Re-scan for common blind spots:
           • OVERLAP: Any content containers whose borders/fills visually intersect?
             Check title-to-content spacing and side-by-side container edges.
             IGNORE editorial markup boxes with reviewer comments (yellow/cyan/magenta
             floating boxes with dates or @mentions) — those are NOT part of the design.
           • TABLE TRANSPOSITION (if any table exists):
             STEP 1 — Verify column headers: Quote each column header from source and
             sketch: "col 1: source='Owner', sketch='Owner' ✓" or "col 3: source='Owner',
             sketch='2026 Q2' ✗". If headers are transposed, flag table_cell_transposition
             (CRITICAL) and set content_fidelity_score to 1–2.
             STEP 2 — Spot-check cell values (MANDATORY): Pick 2-3 data rows (not header).
             For each row, read key cell values and verify they appear in the CORRECT
             columns. Common error patterns to look for:
               • Merged cells: "129 2" in Jan column (should be "129" in Jan, "2" in Feb)
               • Shifted content: Owner names in Timeline column, or vice versa
               • Swapped columns: All deliverable text appears in Owner column
             If you find ANY cell with content from a different column, flag
             table_cell_transposition (CRITICAL) and set content_fidelity_score to 1–2.
             Do NOT skip this step even if headers are correct — transposition can exist
             at the cell level while headers remain correct.
             STEP 3 — Verify cell content structure (MANDATORY for dense cells): For
             cells containing bullet lists or multi-line structured content, verify not
             just column placement but also completeness and correctness WITHIN the cell:
               • If source cell has 3 bullets, sketch cell should have 3 bullets (not 2, not 4)
               • If source shows "$15-25m CAPEX + bullet list", sketch should show same structure
               • Check for overlapping text within a single cell (text bleeding into same cell)
               • Check for missing structural elements (bullet markers, line breaks, separators)
             Pick 1-2 cells with dense/complex content and verify internal structure matches
             source. If bullet counts differ, text overlaps within cell, or structure is
             wrong, flag the appropriate content violation (table_cell_transposition if
             content is present but wrong, or content_fidelity if content is missing).
            ILLEGIBILITY EDGE CASE: If cells are severely compressed/illegible and you
            cannot read values to perform STEP 2 or STEP 3:
              • Explicitly state: "Cannot verify cell content placement/structure due to
                illegibility caused by text_overflow"
              • Do NOT claim "no transposition detected" — say "transposition cannot be
                ruled out due to illegibility"
              • Cap content_fidelity_score at 3 (uncertain, not verified)
              • Focus verification on what IS readable (headers, row/col counts, structural
                elements like separator lines and cell boundaries)
           • MISSING ROWS/COLUMNS (if any table exists): Physically count row separator
             lines in the sketch image, then count in the source image. Do NOT rely on
             the layout description's stated row count. Count what you SEE:
               • Sketch: N horizontal separator lines = N+1 rows (including header)
               • Source: M horizontal separator lines = M+1 rows
               • If N ≠ M, rows are missing or added → flag table_row_count (CRITICAL)
             Repeat for column dividers (vertical lines). If counts differ, flag
             table_row_count or table_column_count (CRITICAL).
           • TEXT TRUNCATION: Any bullets, cells, or paragraphs visibly clipped or
             compressed to illegibility?
         Write what you find in Part B. If Part B uncovers new issues, include
         them in violations. Only close Part B with "No additional violations
         found" after genuinely re-examining the images.

    2. violations             — all violations, using exact rule IDs above.
                                EXHAUSTIVE ENUMERATION: List every distinct
                                violation found, even after the first CRITICAL.
                                One auto-reject violation does not end the search.
                                Layout and content violations are independent —
                                a table with overflow AND transposition gets two
                                separate entries: text_overflow (layout) AND
                                table_cell_transposition (content). Never collapse
                                two distinct problems into a single generic entry.
                                THESE PAIRS ARE ALWAYS INDEPENDENT — both must
                                appear when both conditions exist:
                                  • text_overflow + overlap (annotation box)
                                  • text_overflow + space_utilization
                                  • text_overflow + table_row_count
                                  • text_overflow + table_column_count
                                  • text_overflow + table_cell_transposition
                                After writing violation #1, ask: "Did I also
                                find an annotation box? A space/area shortage?
                                A row or column count mismatch?" Each YES = one
                                additional entry. Finding text_overflow does NOT
                                exempt any of the above from being listed.
    3. layout_score           — 1–5 based on layout violations observed
    4. content_fidelity_score — 1–5 based on content violations observed
    5. verdict                — must be consistent with violations and scores
    6. confidence             — 1.0 for clear-cut, 0.5–0.7 for borderline
    7. critique               — MANDATORY when verdict=rejected (see below)
    8. critique_target        — which sub-agent to route the critique to

  MANDATORY CRITIQUE RULE: When verdict=rejected, the critique field MUST be
  non-empty. It must:
    1. Name every violation found (rule name + affected container IDs).
    2. State exactly what the target sub-agent must change to fix each violation.
    3. Be specific enough that the sub-agent can act on it without seeing the image.
  Example of a valid critique:
    "C1 (Objectives): text block occupies ~30% of container height — severely
    under-filled. SVGSketcher must increase body font size from 14pt toward 18pt
    (ceiling) until text fills at least 65% of the container. Apply the same
    font size to C0 body text to preserve within-slide uniformity (C6)."
  An empty critique string when verdict=rejected is a model error."""

_SVG_JUDGE_HUMAN = """\
## Layout Option ID
{layout_option_id}

## Layout Description (intent from LayoutPlanner)
{layout_description}

## Quality Check Results
Errors (hard): {quality_check_errors}
Warnings (soft): {quality_check_warnings}

[Image 1: SVG sketch PNG — evaluate this for layout quality and content fidelity]
[Image 2: Source slide PNG — use to verify data/structure fidelity:
          row count, column count, bullet count, AND exact cell-level content
          placement (which content belongs in which column and row).
          Do NOT use for visual style comparison — different colors, fills, and
          visual treatments are expected and are not violations.]

Evaluate the sketch and return your verdict as SVGJudgeOutput JSON.
"""


# ---------------------------------------------------------------------------
# Prompts / Config
# ---------------------------------------------------------------------------


class SVGJudgePrompts(BaseModel):
    """Prompts for the SVGJudge agent. Override to customise the system or human prompt."""

    system_instruction: str = Field(
        default=_SVG_JUDGE_SYSTEM,
        description="System instruction for SVG judging",
    )
    human_prompt_template: str = Field(
        default=_SVG_JUDGE_HUMAN,
        description="Human prompt template. Supports {layout_option_id}, {layout_description}, {quality_check_errors}, {quality_check_warnings}.",
    )


class SVGJudgeConfig(BaseModel):
    """Configuration for the SVGJudge agent."""

    llm: Any = Field(
        ...,
        description=(
            "A LangChain BaseChatModel instance (e.g. ChatAnthropic, ChatBedrockConverse). "
            "Must support vision (image_url content blocks) and structured output via trustcall."
        ),
    )
    max_retries: int = Field(
        default=3,
        description="Maximum trustcall retries for structured output extraction.",
    )
    langsmith_run_name: str = Field(
        default="SVGJudgeAgent",
        description="Run name shown in LangSmith traces.",
    )
    prompt_version: str = Field(
        default="v1",
        description=(
            "Version tag for the prompt/config being used. Increment when you change "
            "the system prompt or thresholds so evaluation runs can be compared."
        ),
    )
    thresholds: JudgeThresholds = Field(
        default_factory=JudgeThresholds,
        description=(
            "Approval thresholds used to deterministically recompute the verdict "
            "from violations and scores after LLM extraction."
        ),
    )

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class SVGJudgeAgent:
    """
    Standalone vision LLM agent — quality gate for the SVG-First pipeline.

    Evaluates the SVG sketch PNG for layout quality and content fidelity.
    Produces one SVGJudgeOutput per call with verdict, scores, violations,
    and critique routing information.

    The verdict is post-processed deterministically: after the LLM extracts
    violations and scores, JudgeThresholds.is_approved() recomputes the final
    verdict so it is always consistent with the violations list.

    This class has NO dependency on the main slide-crafter pipeline.
    It uses trustcall for robust structured output extraction.

    Example::

        from langchain_anthropic import ChatAnthropic
        from judge_module import SVGJudgeAgent, SVGJudgeConfig, SVGJudgeInput, SVGJudgePrompts

        llm = ChatAnthropic(model="claude-sonnet-4-5", temperature=0, max_tokens=4096)
        agent = SVGJudgeAgent(
            prompts=SVGJudgePrompts(),
            config=SVGJudgeConfig(llm=llm),
        )
        result = agent.invoke(SVGJudgeInput(
            sketch_png="data:image/png;base64,...",
            source_png="data:image/png;base64,...",
        ))
        print(result.verdict)
    """

    def __init__(self, prompts: SVGJudgePrompts, config: SVGJudgeConfig) -> None:
        self.prompts = prompts
        self.config = config
        self._extractor = create_extractor(
            config.llm,
            tools=[SVGJudgeOutput],
            tool_choice="SVGJudgeOutput",
        )

    def invoke(self, input: SVGJudgeInput) -> SVGJudgeOutput:
        """
        Evaluate a slide sketch against the source slide.

        Args:
            input: SVGJudgeInput with sketch_png and source_png as base64 data-URIs.

        Returns:
            SVGJudgeOutput with verdict, scores, violations, critique, and confidence.
            The verdict is deterministically recomputed from violations + thresholds
            after LLM extraction to ensure internal consistency.
        """
        human_text = self.prompts.human_prompt_template.format(
            layout_option_id=input.layout_option_id,
            layout_description=input.layout_description or "(not provided)",
            quality_check_errors=input.quality_check_errors or [],
            quality_check_warnings=input.quality_check_warnings or [],
        )

        messages = [
            SystemMessage(content=self.prompts.system_instruction),
            HumanMessage(content=[
                {"type": "text", "text": human_text},
                {
                    "type": "image_url",
                    "image_url": {"url": input.sketch_png},
                },
                {
                    "type": "image_url",
                    "image_url": {"url": input.source_png},
                },
            ]),
        ]

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                result = self._extractor.invoke(
                    messages,
                    {"run_name": self.config.langsmith_run_name},
                )
                responses = result.get("responses", [])
                if responses:
                    raw = responses[0]
                    output = (
                        SVGJudgeOutput(**raw)
                        if isinstance(raw, dict)
                        else raw
                    )
                    if isinstance(output, SVGJudgeOutput):
                        return self._post_process(output)
                raise ValueError("trustcall returned no responses")
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"SVGJudgeAgent attempt {attempt}/{self.config.max_retries} failed: {exc}"
                )

        raise RuntimeError(
            f"SVGJudgeAgent failed after {self.config.max_retries} attempts"
        ) from last_exc

    def _post_process(self, output: SVGJudgeOutput) -> SVGJudgeOutput:
        """
        Deterministically recompute verdict from violations + thresholds.

        The LLM may occasionally set a verdict inconsistent with its own
        violations list. This method overrides the verdict using
        JudgeThresholds.is_approved() so the final verdict is always
        consistent with the extracted violations and scores.

        If the verdict is overridden to 'rejected' but critique is empty
        (because the LLM expected approval), a fallback critique is
        auto-generated from the violations list.
        """
        critical_count = sum(1 for v in output.violations if v.severity == "critical")
        major_count = sum(1 for v in output.violations if v.severity == "major")

        deterministic_approved = self.config.thresholds.is_approved(
            layout_score=output.layout_score,
            content_fidelity_score=output.content_fidelity_score,
            critical_count=critical_count,
            major_count=major_count,
        )
        deterministic_verdict: Literal["approved", "rejected"] = (
            "approved" if deterministic_approved else "rejected"
        )

        if deterministic_verdict == output.verdict:
            return output  # consistent — no override needed

        logger.warning(
            f"Overriding LLM verdict '{output.verdict}' → '{deterministic_verdict}' "
            f"(criticals={critical_count}, majors={major_count}, "
            f"layout_score={output.layout_score}, "
            f"content_fidelity_score={output.content_fidelity_score})"
        )

        updates: Dict[str, Any] = {"verdict": deterministic_verdict}

        if deterministic_verdict == "rejected" and not output.critique.strip():
            # LLM thought it was approved so wrote no critique — generate fallback
            parts = [
                f"[{v.severity.upper()}] {v.rule}"
                + (f" ({', '.join(v.affected_containers)})" if v.affected_containers else "")
                + f": {v.description}"
                for v in output.violations
            ]
            updates["critique"] = (
                "Verdict overridden to rejected based on violations detected:\n"
                + "\n".join(parts)
            )
            # Determine critique_target from violation types
            content_rules = {
                "table_row_count", "table_column_count", "table_cell_transposition",
                "table_header_distinction", "text_bullet_count", "text_hierarchy",
                "chart_type", "chart_labels", "mixed_elements_node_count", "image_placeholder",
                "text_fill_ratio_critical", "text_fill_ratio_major", "text_overflow",
            }
            css_rules = {"overlap", "alignment"}
            violation_rule_ids = {v.rule for v in output.violations}
            if violation_rule_ids & content_rules:
                updates["critique_target"] = "svg_sketcher"
            elif violation_rule_ids & css_rules:
                updates["critique_target"] = "css_layout_agent"
            else:
                updates["critique_target"] = "layout_planner"

        return output.model_copy(update=updates)
