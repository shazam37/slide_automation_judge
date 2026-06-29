# Slide Automation Judge

**SVG-First Designer Pipeline — Judge Module (Revised)**

A production-ready judge agent that evaluates rendered slide sketches for layout quality and content fidelity, providing structured feedback for the SVG-First Designer pipeline.

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run single evaluation
python run_judge.py judge \
  --sketch tests/golden_tests/case_000/sketch.png \
  --source tests/golden_tests/case_000/source.png

# Run full golden test suite
python run_judge.py evaluate --golden-dir tests/golden_tests
```

---

## What This Does

The Judge evaluates slide sketches against source slides on two dimensions:

### 1. Layout Quality (score 1-5)
- Space utilization (≥80% coverage)
- Proportion balance
- Alignment & grid snap
- Reading flow
- Container fill ratios
- Text overflow detection
- Overlap validation

### 2. Content Fidelity (score 1-5)
- Table row/column counts
- Cell placement verification (no transposition)
- Text bullet counts
- Content hierarchy preservation
- Chart type correctness

**Verdict:** `approved` (pass to next stage) or `rejected` (route critique back to sub-agent)

---

## Project Status

| Branch | Status | Description |
|--------|--------|-------------|
| `main` | 🟢 Active | Phase 2: Analysis accuracy improvements |
| `arch/split-agent` | 🔵 Planned | Multi-agent architecture (LayoutJudge + ContentJudge) |

### Current Performance (Phase 2)
- **Verdict Accuracy:** 5/5 (100%) on 5-case test set
- **Analysis Accuracy:** Testing in progress
- **Target:** ≥85% violation detection, ≥80% score accuracy

See [Progress_documentation.md](Progress_documentation.md) for full revision history.

---

## Key Features

✅ **Deterministic Rule IDs** — 20 fixed rules, no free-form violations  
✅ **Structured Reasoning** — Chain-of-thought analysis (Part A + Part B)  
✅ **Confidence Scoring** — 0.0-1.0 scale for borderline cases  
✅ **Cell-Level Verification** — Detects merged cells and transposition  
✅ **Editorial Markup Ignored** — Annotation boxes don't trigger false positives  
✅ **65 Golden Test Cases** — Comprehensive test coverage  

---

## Architecture

```
SVGJudgeAgent
├── Input: sketch PNG + source PNG + layout description
├── Structured Reasoning (Part A + Part B)
│   ├── Part A: Systematic rule walkthrough (layout + content)
│   └── Part B: Adversarial double-check (blind spots)
├── Violations: List of rule breaches with severity
├── Scores: layout_score (1-5) + content_fidelity_score (1-5)
├── Verdict: approved / rejected (based on threshold)
└── Critique: Routed to layout_planner | css_layout_agent | svg_sketcher
```

**LLM:** Claude Sonnet 4.5 via AWS Bedrock  
**Framework:** LangChain + trustcall (structured output)  
**Max Retries:** 3 attempts per sketch  

---

## Rule Taxonomy

### Layout Rules (9)
- `space_utilization` — Containers cover ≥80% of slide
- `proportion_balance` — Container sizes match content volume
- `alignment` — Edges align to consistent grid
- `reading_flow` — L→R, T→B natural order
- `fit_feasibility` — Content plausibly fits in container
- `text_fill_ratio_critical` — TEXT containers <40% filled
- `text_fill_ratio_major` — TEXT containers 40-65% filled
- `text_overflow` — Text clipped or illegible
- `overlap` — Content containers intersect
- `total_content_area` — Total coverage <60% of slide

### Content Fidelity Rules (10)
- `table_row_count` — Missing rows
- `table_column_count` — Missing columns
- `table_cell_transposition` — Data in wrong columns/rows
- `table_header_distinction` — Headers not distinguished
- `text_bullet_count` — Missing bullet points
- `text_hierarchy` — Heading levels not preserved
- `chart_type` — Wrong chart type used
- `chart_labels` — Missing axis labels or series names
- `mixed_elements_node_count` — Missing nodes (≥50% threshold)
- `image_placeholder` — Image placeholder missing

---

## Repository Structure

```
.
├── judge_agent.py              # Core agent (system prompt + structured output)
├── judge_config.py             # Configuration (model, thresholds)
├── judge_evaluator.py          # Batch evaluation runner
├── run_judge.py                # CLI entry point
├── tests/
│   ├── golden_tests/           # 65 test cases (case_000 - case_064)
│   │   ├── case_000/
│   │   │   ├── sketch.png      # Rendered SVG sketch
│   │   │   ├── source.png      # Source slide for comparison
│   │   │   ├── expected.json   # Expected verdict + violations
│   │   │   └── metadata.json   # Test case metadata
│   │   └── ...
│   └── test_judge_rules.py
├── results/                    # Auto-saved results per case
│   ├── case_000/
│   │   └── 20260629_120000_v2_sonnet45.json
│   └── ...
├── Progress_documentation.md   # Full revision history
├── README.md                   # This file
└── requirements.txt
```

---

## Usage Examples

### Single Case Evaluation
```bash
python run_judge.py judge \
  --sketch tests/golden_tests/case_020/sketch.png \
  --source tests/golden_tests/case_020/source.png
```

Output saved to `results/case_020/YYYYMMDD_HHMMSS_v2_sonnet45.json`

### Batch Evaluation
```bash
python run_judge.py evaluate \
  --golden-dir tests/golden_tests \
  --output results/batch_eval_20260629.json
```

Generates summary report with pass/fail counts and per-case details.

### Programmatic Usage
```python
from judge_agent import SVGJudgeAgent, SVGJudgeInput, SVGJudgeConfig
from langchain_aws import ChatBedrockConverse

llm = ChatBedrockConverse(
    model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region_name="us-east-1",
    temperature=0,
    max_tokens=4096
)

agent = SVGJudgeAgent(config=SVGJudgeConfig(llm=llm))

result = agent.invoke(SVGJudgeInput(
    sketch_png="data:image/png;base64,...",
    source_png="data:image/png;base64,...",
    layout_description="Two-column layout with table on right."
))

print(result.verdict)         # "approved" or "rejected"
print(result.layout_score)    # 1-5
print(result.violations)      # List of violations
```

---

## Testing Strategy

### 5-Case Quick Test (Primary)
```bash
# Diverse test set covering different design types
for case in case_013 case_017 case_020 case_024 case_036; do
  python run_judge.py judge \
    --sketch tests/golden_tests/$case/sketch.png \
    --source tests/golden_tests/$case/source.png
done
```

- **case_013:** Two-column text (annotation boxes)
- **case_017:** Four-column comparison table (dense)
- **case_020:** Two data tables (transposition, missing row)
- **case_024:** Table + callout + chart (truncation)
- **case_036:** Full-width workstream table (overflow)

### Full 65-Case Suite
```bash
python run_judge.py evaluate --golden-dir tests/golden_tests
```

**Test case breakdown:**
- 13 original cases (case_000 - case_012)
- 52 production dataset cases (case_013 - case_064)

---

## Environment Setup

### Required Environment Variables
```bash
export AWS_BEARER_TOKEN_BEDROCK="<your-token>"
export AWS_DEFAULT_REGION="us-east-1"
```

These must be set in every terminal session before running the judge.

### Python Dependencies
```
langchain-core>=0.3.0
langchain-aws>=0.2.0
trustcall>=0.2.0
pydantic>=2.0
pillow>=10.0
boto3>=1.34.0
```

---

## Revision History

### Phase 2 (2026-06-29) — Current
**Focus:** Analysis accuracy improvements

**Changes:**
- Cell-level spot-checks (detect merged cells, transposition)
- Physical row counting (don't rely on layout description)
- Content score cap when cells illegible (score=3, not 5)
- Link text_overflow to fit_feasibility

**Expected:** 85%+ violation detection, 80%+ score accuracy

### Phase 1 (2026-06-29)
**Focus:** Bug fixes from initial revision

**Changes:**
- Fixed annotation box false positives (ignore editorial markup)
- Restored content fidelity rules for tables
- Simplified Devil's advocate check (60 → 20 lines)

**Results:** 100% verdict accuracy, 70% overall (C grade)

### Phase 0 (2026-06-23)
**Focus:** Add deterministic structure

**Changes:**
- Fixed 20-rule taxonomy
- Structured reasoning field (Part A + Part B)
- Confidence scoring
- Expanded prompt 485 → 859 lines

**Results:** 40% accuracy (regression due to 3 bugs)

---

## Roadmap

### Near-Term (Phase 2 Testing)
- [ ] Re-run 5-case test set with Phase 2 fixes
- [ ] Achieve ≥85% analysis accuracy
- [ ] Document test results in Progress_documentation.md
- [ ] Tag as `v0.3-analysis-improvements`

### Medium-Term (Split-Agent Architecture)
- [ ] Create `arch/split-agent` branch
- [ ] Implement LayoutJudge (300-line prompt)
- [ ] Implement ContentJudge (300-line prompt)
- [ ] Implement Coordinator (aggregation + verdict logic)
- [ ] Parallel execution (30% faster)
- [ ] Compare single-agent vs split-agent performance
- [ ] Merge winner to `main`, tag as `v2.0`

### Long-Term
- [ ] Add ChartJudge (specialized chart evaluation)
- [ ] Add TextJudge (specialized text fidelity)
- [ ] Multi-language support (non-English slides)
- [ ] Real-time feedback streaming
- [ ] Integration with full SVG-First Designer pipeline

---

## Contributing

This is an internal research project. External contributions are not currently accepted.

For questions or feedback:
- **Owner:** shazam37
- **Repository:** https://github.com/shazam37/slide_automation_judge

---

## License

Proprietary — Internal use only.

---

## Acknowledgments

- **Claude Sonnet 4.5** — LLM judge model
- **AWS Bedrock** — Model hosting
- **LangChain** — Agent framework
- **trustcall** — Structured output validation

---

**Last Updated:** 2026-06-29  
**Version:** Phase 2 (Analysis Improvements)  
**Status:** 🟡 Testing in progress
