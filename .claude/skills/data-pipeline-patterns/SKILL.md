---
name: data-pipeline-patterns
description: ETL patterns, data validation, schema evolution, CSV/JSON processing pipelines. Use when building data ingestion, transformation, or loading workflows, or when reviewing data processing code.
---

# Data Pipeline Patterns

Follow these practices when building or reviewing data ingestion, transformation, and loading (ETL/ELT) pipelines.

## 1. Pipeline Architecture

- **Separate Extract, Transform, Load**: Keep each stage as an independent, testable function or module. Avoid monolithic scripts that read, process, and write in a single function.
- **Idempotency**: Every pipeline run with the same input should produce the same output. Avoid operations that depend on mutable global state or system clock unless timestamping is the explicit goal.
- **Intermediate Checkpoints**: For long-running pipelines, write intermediate results to disk (e.g., after extraction, after transformation). This allows restarting from a checkpoint rather than re-running from scratch after a failure.

## 2. Data Validation

Validate data at pipeline boundaries — both at ingestion and before output.

- **Schema Validation**: Define expected schemas explicitly. Use libraries like `pydantic`, `pandera` (for DataFrames), or `jsonschema` to enforce types, ranges, and required fields.
  ```python
  import pandera as pa

  schema = pa.DataFrameSchema({
      "user_id": pa.Column(int, nullable=False),
      "score": pa.Column(float, pa.Check.in_range(0.0, 1.0)),
      "timestamp": pa.Column("datetime64[ns]"),
  })
  validated_df = schema.validate(raw_df)
  ```
- **Row-Level Checks**: Validate individual records for nulls, out-of-range values, duplicates, and referential integrity.
- **Aggregate Checks**: After transformation, verify row counts, value distributions, and summary statistics match expectations (e.g., "output should have the same number of rows as input" or "mean score should be between 0.4 and 0.6").

## 3. Schema Evolution

Schemas change over time. Plan for it:

- **Additive Changes**: New columns with default values are safe.
- **Breaking Changes**: Renaming or removing columns, changing types. Handle these with explicit migration scripts or versioned schemas.
- **Version Your Schemas**: Include a `schema_version` field in output files or metadata so downstream consumers know what to expect.

## 4. CSV / JSON Processing

- **CSV**: Always specify `encoding='utf-8'` and handle quoting/escaping correctly. Use `pandas.read_csv()` with explicit `dtype` parameters to prevent silent type inference errors (e.g., ZIP codes read as integers).
- **JSON**: Use `json.loads()` / `json.dumps()` with `indent=2` for human-readable output. For large files, use streaming parsers like `ijson` instead of loading the entire file into memory.
- **Parquet**: For analytical workloads or large datasets, prefer Parquet over CSV. It preserves types, compresses well, and supports columnar reads.

## 5. Error Handling in Pipelines

- **Fail Loudly on Critical Errors**: If the input file is missing or the schema is fundamentally wrong, fail immediately with a clear error message.
- **Quarantine Bad Records**: For pipelines processing many records, isolate invalid rows into a separate "rejected" file rather than crashing the entire pipeline. Log the reason for rejection.
- **Retry Transient Failures**: Network timeouts, API rate limits, and temporary file locks should be retried with exponential backoff.

## 6. Logging and Observability

- Log the start and end of each pipeline stage with row counts and elapsed time.
- Include a unique `run_id` (e.g., UUID or timestamp) so that logs and output files can be correlated.
- For production pipelines, emit structured logs (JSON) that can be ingested by monitoring systems.
