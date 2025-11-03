"""Run the knobs retrieval demonstration."""

if __name__ == "__main__":
    from src.knobs import PostgreSQLKnobRetriever

    print("PostgreSQL Knob Retrieval - Quick Test")
    print("=" * 38)

    try:
        retriever = PostgreSQLKnobRetriever()

        print("1. Summary of PostgreSQL Knobs:")
        summary = retriever.get_knobs_summary()
        for key, value in summary.items():
            print(f"  {key.replace('_', ' ').title()}: {value}")
        print("-" * 38)

        print("\n2. Sample Tunable Knobs (first 10):")
        tunable = retriever.get_tunable_knobs()
        print(tunable[["name", "value", "unit", "custom_category"]].head(10).to_string(index=False))
        print("-" * 58)

        print("\n3. Numeric Knobs for ML:")
        numeric = retriever.get_numeric_knobs()
        print(f"  Found {len(numeric)} numeric parameters suitable for ML optimization")
        print("-" * 58)

        print("\n[SUCCESS] Knob retrieval working correctly!")
        print("\nFor detailed analysis, run:")
        print("  python -m src.scripts.analyze_knobs")

    except (ConnectionError, ImportError, ValueError, RuntimeError) as e:
        print(f"\n[ERROR] {e}")
        print("\nPlease ensure:")
        print("  1. PostgreSQL is running")
        print("  2. Database credentials are correct in .env")
        print("  3. Database exists")

    print("=" * 37)
