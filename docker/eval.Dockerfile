# ─────────────────────────────────────────────────────────────────────────────
# PBT Evaluation Image
# ─────────────────────────────────────────────────────────────────────────────
# Builds a PostgreSQL image with sysbench and TPC-H dbgen pre-installed,
# ready for resource-constrained comparative benchmark evaluation.
#
# Build:
#   docker build -f docker/eval.Dockerfile -t pbt-eval docker/
#
# Build with specific PG version:
#   docker build --build-arg PG_VERSION=18 -f docker/eval.Dockerfile -t pbt-eval docker/
# ─────────────────────────────────────────────────────────────────────────────

ARG PG_VERSION=18
FROM postgres:${PG_VERSION}

LABEL org.opencontainers.image.title="pbt-eval" \
    org.opencontainers.image.description="PostgreSQL evaluation image for PBT comparative benchmarking" \
    org.opencontainers.image.source="https://github.com/Data-Vanta/ai-database-optimization"

RUN apt-get update && apt-get install -y --no-install-recommends \
    # Sysbench OLTP benchmark
    sysbench \
    # TPC-H dbgen build dependencies
    build-essential \
    gcc \
    make \
    git \
    # Utility tools used by evaluation scripts
    curl \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Compile from the official TPC-H Tools repository.
COPY build_dbgen.sh /tmp/build_dbgen.sh
RUN chmod +x /tmp/build_dbgen.sh && /tmp/build_dbgen.sh

# A small script that executes all 22 TPC-H queries sequentially.
COPY run_power_test.sh /opt/tpch/run_power_test.sh
RUN chmod +x /opt/tpch/run_power_test.sh

# Template config that evaluation scripts augment via ALTER SYSTEM.
# Intentionally minimal — the runner applies the actual knob configs.
RUN echo "log_min_duration_statement = -1" >> /usr/share/postgresql/postgresql.conf.sample && \
    echo "shared_preload_libraries = ''" >> /usr/share/postgresql/postgresql.conf.sample

HEALTHCHECK --interval=5s --timeout=5s --start-period=30s --retries=12 \
    CMD pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-eval}" || exit 1

EXPOSE 5432
