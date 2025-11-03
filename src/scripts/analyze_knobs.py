"""
PostgreSQL Knobs Analysis Script
=================================

Comprehensive analysis of ALL PostgreSQL configuration parameters.
Demonstrates how to retrieve, categorize, and analyze database knobs
for ML-based optimization.
"""

import os
from src.knobs import PostgreSQLKnobRetriever


def main():
    """Demonstrate all knobs retrieval and analysis."""

    print("PostgreSQL ALL Knobs Analysis")
    print("=" * 30)

    retriever = PostgreSQLKnobRetriever()

    print("1. Summary of ALL PostgreSQL Knobs:")
    print("-" * 35)
    summary = retriever.get_knobs_summary()
    for key, value in summary.items():
        print(f"  {key.replace('_', ' ').title()}: {value}")

    print("\n2. Comparison - Predefined vs All:")
    print("-" * 35)
    all_knobs = retriever.get_all_knobs_with_metadata()
    predefined = all_knobs[all_knobs["is_predefined_tunable"]]
    non_predefined = all_knobs[~all_knobs["is_predefined_tunable"]]

    print(f"  Total PostgreSQL knobs: {len(all_knobs)}")
    print(
        f"  Predefined tunable: {len(predefined)} "
        f"({len(predefined)/len(all_knobs)*100:.1f}%)"
    )
    print(
        f"  Non-predefined: {len(non_predefined)} "
        f"({len(non_predefined)/len(all_knobs)*100:.1f}%)"
    )

    print("\n3. Discovering New Tunable Candidates:")
    print("-" * 38)
    print("  (Non-predefined, runtime-modifiable, numeric)")

    candidates = all_knobs[
        (~all_knobs["is_predefined_tunable"])
        & (all_knobs["is_runtime_modifiable"])
        & (all_knobs["vartype"].isin(["integer", "real"]))
    ]

    print(f"\n  Found {len(candidates)} potential new tunable knobs!")
    print("\n  Top 10 candidates:")
    for i, (_, knob) in enumerate(candidates.head(10).iterrows(), 1):
        print(f"    {i}. {knob['name']}")
        print(f"       Category: {knob['category']}")
        print(f"       Type: {knob['vartype']}, Context: {knob['context']}")
        print(f"       Current: {knob['value']}")
        print()

    print("\n4. Knobs by Context (When Can Be Changed):")
    print("-" * 42)
    contexts = retriever.get_all_contexts()
    for context in contexts:
        context_knobs = retriever.get_knobs_by_context(context)
        numeric_count = len(
            context_knobs[context_knobs["vartype"].isin(["integer", "real"])]
        )
        print(
            f"  {context:20s}: {len(context_knobs):3d} total, "
            f"{numeric_count:3d} numeric"
        )

    print("\n5. Top 10 Categories by Knob Count:")
    print("-" * 35)
    categories = retriever.get_all_categories()
    category_counts = []
    for category in categories:
        count = len(retriever.get_knobs_by_category(category))
        category_counts.append((category, count))

    category_counts.sort(key=lambda x: x[1], reverse=True)
    for i, (category, count) in enumerate(category_counts[:10], 1):
        print(f"  {i:2d}. {category:50s} ({count} knobs)")

    print("\n6. Interesting Non-Predefined Knobs:")
    print("-" * 36)

    jit_knobs = all_knobs[all_knobs["name"].str.contains("jit_", na=False)]
    if len(jit_knobs) > 0:
        print("\n  A. JIT (Just-In-Time) Compilation:")
        for _, knob in jit_knobs.iterrows():
            print(f"     - {knob['name']:30s}: {knob['value']}")

    parallel_knobs = all_knobs[
        (all_knobs["name"].str.contains("parallel", case=False, na=False))
        & (~all_knobs["is_predefined_tunable"])
    ]
    if len(parallel_knobs) > 0:
        print("\n  B. Additional Parallel Query Settings:")
        for _, knob in parallel_knobs.iterrows():
            print(f"     - {knob['name']:30s}: {knob['value']}")

    wal_knobs = all_knobs[
        (all_knobs["name"].str.contains("wal_", na=False))
        & (~all_knobs["is_predefined_tunable"])
    ]
    if len(wal_knobs) > 0:
        print("\n  C. Additional WAL/Replication Settings:")
        for _, knob in wal_knobs.head(5).iterrows():
            print(f"     - {knob['name']:30s}: {knob['value']}")

    print("\n7. Exporting All Knobs:")
    print("-" * 23)
    output_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "postgresql_all_knobs_demo.csv"
    )
    output_path = os.path.normpath(output_path)
    retriever.save_all_knobs(output_path, include_metadata=True)

    print("\n8. ML-Ready Knob Filtering:")
    print("-" * 28)

    ml_ready = all_knobs[
        (all_knobs["is_runtime_modifiable"])
        & (all_knobs["vartype"].isin(["integer", "real"]))
    ]
    print(f"  Numeric, runtime-modifiable: {len(ml_ready)} knobs")

    ml_all_numeric = all_knobs[all_knobs["vartype"].isin(["integer", "real"])]
    print(f"  All numeric knobs: {len(ml_all_numeric)} knobs")

    ml_predefined = all_knobs[
        (all_knobs["is_predefined_tunable"])
        & (all_knobs["vartype"].isin(["integer", "real"]))
    ]
    print(f"  Predefined numeric tunable: {len(ml_predefined)} knobs")
    print("\n  Recommendation: Start with predefined, expand to full set as needed")

    print("\n9. Creating Custom Tuning Profile:")
    print("-" * 34)

    olap_knobs = all_knobs[
        (
            all_knobs["name"].str.contains(
                "parallel|work_mem|hash|sort", case=False, na=False
            )
        )
        | (all_knobs["custom_category"] == "memory")
        | (all_knobs["custom_category"] == "parallelism")
    ]
    print(f"  OLAP-relevant knobs identified: {len(olap_knobs)}")

    oltp_knobs = all_knobs[
        (
            all_knobs["name"].str.contains(
                "connection|checkpoint|commit", case=False, na=False
            )
        )
        | (all_knobs["custom_category"] == "connections")
        | (all_knobs["custom_category"] == "checkpoint")
    ]
    print(f"  OLTP-relevant knobs identified: {len(oltp_knobs)}")

    print("=" * 36)
    print("\nAnalysis Complete!")
    print("=" * 18)

    print("Key Insights:")
    print(f"  - We have access to {len(all_knobs)} PostgreSQL configuration parameters")
    print(f"  - {len(candidates)} non-predefined knobs are runtime-modifiable & numeric")
    print(f"  - Consider expanding beyond the predefined {len(predefined)} knobs")
    print(f"  - {len(ml_ready)} knobs total are ideal for online ML optimization")
    print("=" * 62)


if __name__ == "__main__":
    try:
        main()
    except (ConnectionError, ImportError, FileNotFoundError, ValueError) as e:
        print(f"\n🔴 Error: {e}")
        print("\nPlease ensure:")
        print("  1. PostgreSQL is running")
        print("  2. Database credentials are correct in .env")
        print("  3. Database exists")
        print("\nSee docs/ENVIRONMENT_SETUP.md for help.")
