"""
Tuner Configuration Module Test
================================

Test the knob space, loader, and PBT configuration.

Usage:
------
python -m src.tuner.config
"""

if __name__ == "__main__":
    from src.tuner.config import (
        get_knob_space,
        RAPID_CONFIG,
        STANDARD_CONFIG,
    )

    print("Tuner Configuration - Quick Test")
    print("=" * 32)

    print("\n[TEST 1] Knob Space Loading")
    print("-" * 27)
    try:
        for tier in ["minimal", "core", "standard", "extensive"]:
            ks = get_knob_space(tier)
            print(f"🟢 {tier.capitalize():12} - {len(ks.knobs):2}")
        print("\n🟢 All knob tiers loaded successfully!")

    except (ValueError, TypeError, AttributeError) as e:
        print(f"\n🔴 ERROR loading knob spaces: {e}")
        import traceback
        traceback.print_exc()

    print("-" * 38)
    print("\n[TEST 2] PBT Configuration")
    print("-" * 26)
    try:
        configs = {
            "RAPID": RAPID_CONFIG,
            "STANDARD": STANDARD_CONFIG,
        }

        for name, config in configs.items():
            print(f"\n{name} Config:")
            print(f"  Population size: {config.population_size}")
            print(f"  Generations: {config.num_generations}")
            print(f"  Workers per quantile: {config.num_workers_per_quantile}")
            print(f"  Ready interval: {config.ready_interval}")

        print("\n 🟢 PBT configurations working correctly!")

    except (ValueError, TypeError, AttributeError) as e:
        print(f"\n🔴 ERROR with PBT configs: {e}")

    print("-" * 41)
    print("\n[TEST 3] Knob Space Operations")
    print("-" * 30)
    try:
        ks = get_knob_space("minimal")

        config = ks.sample_random_config(seed=42)
        print(f"🟢 Random config sampled: {list(config.keys())}")

        is_valid, errors = ks.validate_config(config)
        print(f"🟢 Config validation: {'PASS' if is_valid else 'FAIL'}")

        perturbed = ks.perturb_config(config, seed=42)
        print("🟢 Config perturbed successfully")

        default = ks.get_default_config()
        print(f"🟢 Default config retrieved: {list(default.keys())}")

        print("\n🟢 Knob space operations working correctly!")

    except (ValueError, TypeError, AttributeError) as e:
        print(f"\n✗ ERROR with knob operations: {e}")
        import traceback
        traceback.print_exc()

    print("=" * 50 + "\n")
