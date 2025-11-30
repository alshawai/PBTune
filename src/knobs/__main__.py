"""Run the knobs retrieval and preprocessing demonstration."""

if __name__ == "__main__":
    import sys
    from src.knobs import PostgreSQLKnobRetriever, preprocess_and_save_knobs

    print("PostgreSQL Knob Retrieval & Preprocessing - Quick Test")
    print("=" * 54)

    print("\n[TEST 1] Knob Retrieval")
    print("-" * 24)
    try:
        retriever = PostgreSQLKnobRetriever()

        print("Summary of PostgreSQL Knobs:")
        summary = retriever.get_knobs_summary()
        for key, value in summary.items():
            print(f"  {key.replace('_', ' ').title()}: {value}")

        print("\nSample Tunable Knobs (first 5):")
        tunable = retriever.get_tunable_knobs()
        print(tunable[["name", "value", "unit", "custom_category"]].head(5).to_string(index=False))

        print("\n🟢 Knob retrieval working correctly!")

    except (ValueError, TypeError, AttributeError) as e:
        print(f"\n🔴 ERROR: {e}")
        print("\nPlease ensure:")
        print("  1. PostgreSQL is running")
        print("  2. Database credentials are correct in .env")
        print("  3. Database exists")

    print("-" * 36)
    print("\n[TEST 2] Knob Preprocessing")
    print("-" * 27)

    raw_csv = sys.argv[1] if len(sys.argv) > 1 else None

    try:
        saved_paths = preprocess_and_save_knobs(raw_csv_path=raw_csv)

        print("\n🟢 Preprocessing complete!")
        print("\nGenerated CSVs:")
        for tier, path in saved_paths.items():
            print(f"  {tier}: {path}")

        print("\nYou can now use these CSVs with:")
        print("  from src.tuner.config import get_knob_space")
        print("  knob_space = get_knob_space('minimal')")

    except (ValueError, TypeError, AttributeError) as e:
        print(f"\n🔴 ERROR during preprocessing: {e}")
        import traceback
        traceback.print_exc()

    print("=" * 50 + "\n")
