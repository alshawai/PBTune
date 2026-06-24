#!/usr/bin/env bash
#
# Full (CITEABLE) knob-importance run with stability selection.
#
# Use this once, for the final/publishable analysis. On top of the same tiers
# run_importance_fast.sh produces, it runs the group-clustered stability loop
# (Meinshausen & Buhlmann 2010, B in [50, 100]) to attach per-knob selection
# probabilities and a tier-distribution — the reproducibility numbers you cite.
# This is the expensive pass; it does NOT change the tier assignments.
#
# It is worth running on a many-core machine (cloud server) and is doubly
# pointless on a noisy `--warmup 1 --duration 1` sweep — give it a real sweep.
#
# Input: an LHS-design session JSON produced by
#   python -m scripts.experiments --experiment lhs_design
#
# Tunables (override via environment):
#   WORKLOAD          path component             (default: oltp)
#   SYSBENCH_WORKLOAD path component             (default: oltp_read_write)
#   TIER              path component             (default: extensive)
#   RESULTS_DIR       tuning_sessions dir to analyze
#                     (default: results/{WORKLOAD}/{SYSBENCH_WORKLOAD}/lhs_runs/{TIER}/tuning_sessions)
#   WORKLOAD_LABEL    label for outputs          (default: oltp_read_write)
#   EXPORT_TIERS      data_driven_tiers.json dest
#                     (default: auto → data/data_driven_knobs/{label}/data_driven_tiers.json)
#   RF_TREES          surrogate tree count       (default: 500)
#   BORUTA_ITER       shadow iterations          (default: 100)
#   STABILITY_B       stability subsamples       (default: 50)
#   STABILITY_JOBS    parallel subsample workers (default: all cores)
#
# Extra flags pass straight through, e.g.:
#   ./scripts/run_importance_full.sh --scalpel-stability-iter 100
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source .venv/bin/activate

WORKLOAD="${WORKLOAD:-oltp}"
SYSBENCH_WORKLOAD="${SYSBENCH_WORKLOAD:-oltp_read_write}"
TIER="${TIER:-extensive}"
WORKLOAD_LABEL="${WORKLOAD_LABEL:-oltp_read_write}"
RESULTS_DIR="${RESULTS_DIR:-results/${WORKLOAD}/${SYSBENCH_WORKLOAD}/lhs_runs/${TIER}/tuning_sessions}"
EXPORT_TIERS="${EXPORT_TIERS:-auto}"
RF_TREES="${RF_TREES:-500}"
BORUTA_ITER="${BORUTA_ITER:-100}"
STABILITY_B="${STABILITY_B:-50}"
STABILITY_JOBS="${STABILITY_JOBS:-$(nproc 2>/dev/null || echo 4)}"

echo "==> FULL importance (tiers + stability selection)"
echo "==> results-dir  = ${RESULTS_DIR}"
echo "==> export-tiers = ${EXPORT_TIERS}"
echo "==> rf-trees=${RF_TREES} boruta-iter=${BORUTA_ITER} stability-b=${STABILITY_B} jobs=${STABILITY_JOBS}"

exec python -m src.scripts.analyze_knob_importance \
    --algorithm scalpel \
    --results-dir "${RESULTS_DIR}" \
    --workload-label "${WORKLOAD_LABEL}" \
    --export-tiers "${EXPORT_TIERS}" \
    --scalpel-stability-b "${STABILITY_B}" \
    --scalpel-stability-jobs "${STABILITY_JOBS}" \
    --scalpel-rf-trees "${RF_TREES}" \
    --scalpel-boruta-iter "${BORUTA_ITER}" \
    "$@"
