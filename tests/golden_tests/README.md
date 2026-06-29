# Golden Test Dataset

This directory contains golden test cases for the SVG Judge evaluation harness.

## Directory Structure

Each test case is a subdirectory:

```
golden_tests/
    case_001_approved_clean/
        sketch.png          ← rendered SVG sketch PNG (the output to evaluate)
        source.png          ← source slide PNG (the reference)
        expected.json       ← expected verdict
        metadata.json       ← optional: layout description, option ID
    case_002_rejected_overflow/
        sketch.png
        source.png
        expected.json
    ...
```

## File Formats

### `expected.json`
```json
{
    "verdict": "rejected",
    "notes": "Text overflows container C1. Table in C2 has only 3 rows but source has 5."
}
```
- `verdict`: `"approved"` or `"rejected"` — the ground truth label
- `notes`: human explanation of why this verdict is expected (for documentation)

### `metadata.json` (optional)
```json
{
    "layout_description": "Two-column layout: text on left, table on right.",
    "layout_option_id": "OPT-1"
}
```

## Coverage Guidelines

Aim for 20+ cases covering:

| Category | Target count | Examples |
|----------|-------------|---------|
| Approved — clean layout | 5+ | Good space utilization, correct content |
| Rejected — critical layout | 5+ | Text overflow, container overlap, empty slide |
| Rejected — critical content | 5+ | Wrong table row count, missing bullets |
| Rejected — major violations | 3+ | Under-filled text, poor space utilization |
| Edge cases | 2+ | Mimic mode slides, block diagrams |

## How to Add a Case

1. Create a new subdirectory: `case_NNN_description/`
2. Add `sketch.png` and `source.png` (PNG format, any resolution)
3. Add `expected.json` with the correct verdict and notes
4. Optionally add `metadata.json` with layout context
5. Run the evaluation harness to verify the case is loaded correctly:
   ```bash
   python -m pytest judge_module/tests/ -v
   ```

## Getting PNG Pairs from LangSmith

You can export `(sketch.png, source.png)` pairs from LangSmith traces:
- Find a trace in the `SVGJudgeAgent` run
- The `sketch_png` and `source_png` inputs are base64 data-URIs
- Decode them and save as PNG files
- Determine the expected verdict based on visual inspection
