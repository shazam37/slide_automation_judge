#!/bin/bash
#
# Phase 3 Experiment: Sonnet 4.5 vs Opus 4.8 Comparison
#
# Tests 10 diverse cases with both models and tracks:
# - Accuracy metrics (verdict, analysis, violations, scores)
# - Cost metrics (input/output tokens)
# - Latency metrics (time per case)
#

set -e

# Must set these before running
export AWS_DEFAULT_REGION=us-east-1

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

GOLDEN_DIR="tests/phase3_10cases"
RESULTS_DIR="results"

# Model IDs
SONNET_MODEL="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
OPUS_MODEL="us.anthropic.claude-opus-4-8-20250514-v2:0"

echo "========================================"
echo "Phase 3: Model Comparison Experiment"
echo "========================================"
echo ""
echo "Testing 10 cases:"
echo "  - case_001, case_005, case_010, case_015, case_021"
echo "  - case_040, case_045, case_050, case_060, case_064"
echo ""
echo "Models to compare:"
echo "  1. Sonnet 4.5: $SONNET_MODEL"
echo "  2. Opus 4.8:   $OPUS_MODEL"
echo ""

# Function to run evaluation and capture timing
run_experiment() {
    local model_name=$1
    local model_id=$2
    local output_file=$3

    echo "========================================"
    echo "Running: $model_name"
    echo "========================================"
    echo "Model ID: $model_id"
    echo "Output:   $output_file"
    echo ""

    start_time=$(date +%s)

    python run_judge.py evaluate \
        --golden-dir "$GOLDEN_DIR" \
        --prompt-version v2.1 \
        --model "$model_id" \
        --output "$output_file"

    end_time=$(date +%s)
    elapsed=$((end_time - start_time))

    echo ""
    echo "Completed in ${elapsed}s"
    echo ""

    # Calculate average time per case
    avg_time=$(echo "scale=2; $elapsed / 10" | bc)
    echo "Average time per case: ${avg_time}s"
    echo ""
}

# Ensure results directory exists
mkdir -p "$RESULTS_DIR"

# Run experiments
echo "Starting experiments at $(date)"
echo ""

run_experiment "Sonnet 4.5" "$SONNET_MODEL" "$RESULTS_DIR/phase3_sonnet45_10cases.json"
echo ""
echo "----------------------------------------"
echo ""
run_experiment "Opus 4.8" "$OPUS_MODEL" "$RESULTS_DIR/phase3_opus48_10cases.json"

echo ""
echo "========================================"
echo "Experiments Complete!"
echo "========================================"
echo ""
echo "Results saved to:"
echo "  - $RESULTS_DIR/phase3_sonnet45_10cases.json"
echo "  - $RESULTS_DIR/phase3_opus48_10cases.json"
echo ""
echo "Per-case result files saved to:"
echo "  - $RESULTS_DIR/case_*/*.json"
echo ""
echo "Next steps:"
echo "  1. Review accuracy comparison"
echo "  2. Analyze cost/latency tradeoffs"
echo "  3. Make model selection decision"
echo ""
