# Judge Module — Standalone Slide Quality Judge

A self-contained Python module for evaluating slide design quality using a vision LLM.

The judge receives two PNG images — a rendered slide sketch and the original source slide — and returns a structured verdict: **approved** or **rejected**, with scores, violation details, and an actionable critique.

---

## What This Module Does

The judge evaluates two dimensions:

1. **Layout quality** — space utilization, alignment, container proportions, text fill ratio, overlap detection
2. **Content fidelity** — are all data rows, bullet points, and chart elements present and correctly placed?

When a slide is rejected, the judge identifies which part of the pipeline caused the issue and provides a specific critique.

---

## Setup

### 1. Install dependencies

```bash
pip install -r judge_module/requirements.txt
```

### 2. Configure your LLM provider

The judge uses a vision-capable LLM (Claude Sonnet recommended). Set one of:

**Option A — Anthropic direct API:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**Option B — AWS Bedrock:**
```bash
export AWS_DEFAULT_REGION=us-east-1
# Configure AWS credentials via ~/.aws/credentials or IAM role
```

### 3. (Optional) Enable LangSmith tracing

```bash
export LANGSMITH_API_KEY=ls__...
export LANGSMITH_PROJECT=judge-module
```

---

## Quick Start

### Judge a single slide pair

```bash
python judge_module/run_judge.py --sketch sketch.png --source source.png
```

With layout context:
```bash
python judge_module/run_judge.py \
    --sketch sketch.png \
    --source source.png \
    --layout-description "Two-column layout: bullet list on left, table on right."
```

### Run the evaluation harness

```bash
python judge_module/run_judge.py --evaluate --golden-dir judge_module/tests/golden_tests
```

Save detailed results:
```bash
python judge_module/run_judge.py --evaluate --output results.json
```

---

## Python API

```python
from langchain_anthropic import ChatAnthropic
from judge_module import SVGJudgeAgent, SVGJudgeConfig, SVGJudgeInput, SVGJudgePrompts

# 1. Create the LLM
llm = ChatAnthropic(model="claude-sonnet-4-5", temperature=0, max_tokens=4096)

# 2. Create the agent
agent = SVGJudgeAgent(
    prompts=SVGJudgePrompts(),
    config=SVGJudgeConfig(llm=llm),
)

# 3. Invoke with base64 PNG data-URIs
import base64
from pathlib import Path

def load_png(path):
    data = Path(path).read_bytes()
    return f"data:image/png;base64,{base64.b64encode(data).decode()}"

result = agent.invoke(SVGJudgeInput(
    sketch_png=load_png("sketch.png"),
    source_png=load_png("source.png"),
    layout_description="Two-column layout with a table on the right.",
))

print(result.verdict)                  # "approved" or "rejected"
print(result.layout_score)             # 1–5
print(result.content_fidelity_score)   # 1–5
print(result.critique)                 # actionable critique when rejected
print(result.critique_target)          # "layout_planner" | "css_layout_agent" | "svg_sketcher"

for v in result.violations:
    print(f"[{v.severity}] {v.rule}: {v.description}")
```

---

## Module Structure

```
judge_module/
├── __init__.py             — public API exports
├── judge_agent.py          — SVGJudgeAgent (LLM agent, prompts, schemas)
├── judge_rules.py          — Rule definitions (IDs, severities, container types)
├── judge_evaluator.py      — Evaluation harness (golden test runner)
├── judge_config.py         — Configurable thresholds (max_major_violations, etc.)
├── run_judge.py            — CLI entry point
├── requirements.txt        — Minimal dependencies
└── tests/
    ├── __init__.py
    ├── test_judge_rules.py — Unit tests (no LLM required)
    └── golden_tests/
        ├── README.md       — How to add golden test cases
        └── case_NNN_*/     — Test case directories (add your own)
```

---

## Running Tests

Unit tests (no LLM required):
```bash
python -m pytest judge_module/tests/test_judge_rules.py -v
```

---

## Customising the Judge

### Override the system prompt

```python
from judge_module import SVGJudgePrompts

custom_prompts = SVGJudgePrompts(
    system_instruction="Your custom system prompt here...",
)
agent = SVGJudgeAgent(prompts=custom_prompts, config=SVGJudgeConfig(llm=llm))
```

### Adjust approval thresholds

```python
from judge_module.judge_config import JudgeThresholds

thresholds = JudgeThresholds(
    max_major_violations=1,   # stricter: only 1 major violation allowed
    min_layout_score=4,       # stricter: layout score must be >= 4
    min_content_fidelity_score=4,
)

# Use thresholds to re-evaluate an existing output
approved = thresholds.is_approved(
    layout_score=result.layout_score,
    content_fidelity_score=result.content_fidelity_score,
    critical_count=sum(1 for v in result.violations if v.severity == "critical"),
    major_count=sum(1 for v in result.violations if v.severity == "major"),
)
```

---

## Getting PNG Pairs from LangSmith

To build the golden test dataset, export `(sketch.png, source.png)` pairs from LangSmith:

1. Open a LangSmith trace for a `SVGJudgeAgent` run
2. Find the `sketch_png` and `source_png` input fields (base64 data-URIs)
3. Decode and save as PNG files:

```python
import base64, re
from pathlib import Path

def save_data_uri(data_uri: str, output_path: str):
    # Strip the "data:image/png;base64," prefix
    b64 = re.sub(r"^data:image/\w+;base64,", "", data_uri)
    Path(output_path).write_bytes(base64.b64decode(b64))

save_data_uri(sketch_png_from_langsmith, "case_001/sketch.png")
save_data_uri(source_png_from_langsmith, "case_001/source.png")
```

4. Visually inspect the pair and write `expected.json` with the correct verdict
5. See `judge_module/tests/golden_tests/README.md` for the full format

---

## Three-Week Development Plan

| Week | Focus | Goal |
|------|-------|------|
| **Week 1** | Setup + data preparation | Module running, 20+ golden test cases collected |
| **Week 2** | Prompt improvement + evaluation | Judge accuracy ≥ 80% on golden test set |
| **Week 3** | Finalization + configurable thresholds | Final evaluation run, documentation complete |
