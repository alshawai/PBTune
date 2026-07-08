#!/usr/bin/env bash
#
# Routine (FAST) knob-importance tier generation.
#
# Use this for day-to-day tier regeneration that feeds PBT/BO knob-sets. The
# minimal/core/standard tier assignments come ENTIRELY from the single primary
# BORUTA + fANOVA + Lorenz pass; the expensive group-clustered stability loop
# only annotates them with reproducibility numbers and never changes a tier.
# So we disable stability (--scalpel-stability-b 0) and trim the surrogate to
# get identical tiers in a fraction of the wall-clock.
#
# Input: an LHS-design session JSON produced by
#   python -m scripts.experiments --experiment lhs_design
# Pair with run_importance_full.sh when you want the citeable confidence run.
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
#   RF_TREES          surrogate tree count       (default: 200)
#   BORUTA_ITER       shadow iterations          (default: 80)
#
# Extra flags pass straight through, e.g.:
#   ./scripts/run_importance_fast.sh --scalpel-coverage-core 0.85
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
RF_TREES="${RF_TREES:-200}"
BORUTA_ITER="${BORUTA_ITER:-80}"

echo "==> FAST importance (tiers only, stability disabled)"
echo "==> results-dir  = ${RESULTS_DIR}"
echo "==> export-tiers = ${EXPORT_TIERS}"
echo "==> rf-trees=${RF_TREES} boruta-iter=${BORUTA_ITER} stability-b=0"

exec python -m src.scripts.analyze_knob_importance \
    --algorithm scalpel \
    --results-dir "${RESULTS_DIR}" \
    --workload-label "${WORKLOAD_LABEL}" \
    --export-tiers "${EXPORT_TIERS}" \
    --scalpel-stability-b 0 \
    --scalpel-rf-trees "${RF_TREES}" \
    --scalpel-boruta-iter "${BORUTA_ITER}" \
    "$@"
