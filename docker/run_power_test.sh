#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_power_test.sh
# TPC-H Power Test: executes all 22 queries sequentially against the
# PostgreSQL instance running in the evaluation container.
#
# Output format (parsed by docker_env._parse_tpch_output):
#   Query 1: 2.345s
#   Query 2: 0.892s
#   ...
#   Query 22: 1.234s
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

TPCH_DIR="/opt/tpch"
QUERIES_DIR="${TPCH_DIR}/queries"
PG_USER="${POSTGRES_USER:-postgres}"
PG_DB="${POSTGRES_DB:-eval}"

if [ ! -d "${QUERIES_DIR}" ]; then
    echo "ERROR: TPC-H query directory not found: ${QUERIES_DIR}" >&2
    exit 1
fi

echo "Starting TPC-H Power Test (22 queries) …"
echo ""

TOTAL_START=$(date +%s%N)

for q in $(seq 1 22); do
    QUERY_FILE="${QUERIES_DIR}/${q}.sql"
    if [ ! -f "${QUERY_FILE}" ]; then
        echo "WARNING: Query file missing: ${QUERY_FILE}" >&2
        continue
    fi

    START=$(date +%s%N)
    psql -U "${PG_USER}" -d "${PG_DB}" \
         -v ON_ERROR_STOP=1 \
         --no-psqlrc \
         -q \
         -f "${QUERY_FILE}" \
         > /dev/null 2>&1
    END=$(date +%s%N)

    # Duration in seconds with 3 decimal places
    DURATION_MS=$(( (END - START) / 1000000 ))
    DURATION_S=$(awk "BEGIN {printf \"%.3f\", ${DURATION_MS}/1000}")
    echo "Query ${q}: ${DURATION_S}s"
done

TOTAL_END=$(date +%s%N)
TOTAL_MS=$(( (TOTAL_END - TOTAL_START) / 1000000 ))
TOTAL_S=$(awk "BEGIN {printf \"%.3f\", ${TOTAL_MS}/1000}")

echo ""
echo "Power Test total: ${TOTAL_S}s"
