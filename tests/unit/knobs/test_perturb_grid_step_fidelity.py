# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Grid-step fidelity of numeric perturbation (ticket #167, bugs B4/B5/B6).

Seams under test (both confirmed this session):
- ``KnobSpace.perturb_config`` — the public PBT exploration step.
- ``KnobDefinition.normalize_value`` — the public domain-projection method.

The spec (from the diagnostic prototype) is:

    factor    = choice(perturbation_factors)     # discrete, not uniform
    delta     = value * (factor - 1.0)
    if abs(delta) < step:                        # too small to move the grid
        delta = step * sign(factor - 1.0)        # force >= one grid step
    new_value = clamp(round(value + delta), lo, hi)

Expected literals below (0.8/1.2 factors, the {2, 4} neighbours of 3, the
"escape from 0", the "not stuck at/below 4") come from that spec, never from
recomputing the implementation.
"""

import numpy as np

from src.knobs.knob_space import (
    KnobDefinition,
    KnobScale,
    KnobSpace,
    KnobType,
)


def _int_space(min_value, max_value, *, scale=KnobScale.LINEAR, step=None):
    """A one-knob integer space named ``k``."""
    knob = KnobDefinition(
        name="k",
        knob_type=KnobType.INTEGER,
        min_value=min_value,
        max_value=max_value,
        scale=scale,
        default=min_value,
        step=step,
    )
    return KnobSpace([knob])


def test_normalize_integer_rounds_not_truncates():
    """B5: integer normalization must round, not truncate toward zero.

    2.9 belongs to grid cell 3 under rounding; truncation biases it down to 2.
    """
    knob = KnobDefinition(
        name="k", knob_type=KnobType.INTEGER, min_value=0, max_value=100
    )
    assert knob.normalize_value(2.9) == 3
    assert knob.normalize_value(7.5) == 8  # rounds to nearest (7.5 -> 8), not truncated to 7 (B5)
    assert knob.normalize_value(0.9) == 1


def test_perturbation_factors_are_discrete():
    """B4: the multiplicative factor is drawn from the discrete pair, not a
    continuous uniform over the interval.

    With a large value the grid-step floor is negligible, so every accepted
    move must land on exactly 0.8x or 1.2x — never an interior ratio.
    """
    space = _int_space(1, 10_000_000)
    rng = np.random.default_rng(20240607)
    start = 100_000

    ratios = []
    for _ in range(300):
        out = space.perturb_config({"k": start}, (0.8, 1.2), rng=rng)["k"]
        ratios.append(out / start)

    for ratio in ratios:
        assert min(abs(ratio - 0.8), abs(ratio - 1.2)) < 1e-3, ratio
    # Both discrete factors must actually occur across the stream.
    assert any(abs(r - 0.8) < 1e-3 for r in ratios)
    assert any(abs(r - 1.2) < 1e-3 for r in ratios)


def test_small_integer_always_moves_by_at_least_one_step():
    """B6/B4: perturbing 3 (not at a bound) must always change it.

    Under the spec the only reachable neighbours are 2 (factor 0.8) and 4
    (factor 1.2); 3 must never be returned unchanged.
    """
    space = _int_space(0, 100)
    results = set()
    for seed in range(200):
        rng = np.random.default_rng(seed)
        results.add(space.perturb_config({"k": 3}, (0.8, 1.2), rng=rng)["k"])

    assert results <= {2, 4}, results
    assert results == {2, 4}, results


def test_zero_escapes_within_bounded_perturbations():
    """B6: a knob sitting at 0 must escape 0 within a bounded number of steps.

    Multiplicative perturbation leaves 0 absorbing (0 * factor == 0); the
    grid-step floor must let it climb off the floor.
    """
    space = _int_space(0, 100)
    rng = np.random.default_rng(99)
    value = 0
    for _ in range(50):
        value = space.perturb_config({"k": value}, (0.8, 1.2), rng=rng)["k"]
        if value > 0:
            break
    assert value > 0


def test_perturbation_can_increase_past_truncation_trap():
    """B5/B6: an up-factor on a small value must be able to increase it.

    Under truncation ``4 * 1.2 == 4.8`` collapses back to 4, so 4 can never
    grow. A correct grid step must let it reach 5.
    """
    space = _int_space(0, 1000)
    reachable = set()
    for seed in range(200):
        rng = np.random.default_rng(seed)
        reachable.add(space.perturb_config({"k": 4}, (0.8, 1.2), rng=rng)["k"])
    assert 5 in reachable, reachable


def test_repeated_perturbation_is_not_monotonically_decreasing():
    """PROPERTY: quantize -> perturb -> quantize cycles must be able to rise.

    Truncation makes an up-factor collapse back down (4 * 1.2 = 4.8 -> 4), so a
    fed-back trajectory started at 4 only ever stays flat or falls (zero upward
    steps). A correct grid step must produce at least one upward step.
    """
    space = _int_space(0, 1000)
    rng = np.random.default_rng(2024)
    value = 4
    trajectory = [value]
    for _ in range(40):
        value = space.perturb_config({"k": value}, (0.8, 1.2), rng=rng)["k"]
        trajectory.append(value)
    upward_steps = [
        i for i in range(len(trajectory) - 1) if trajectory[i + 1] > trajectory[i]
    ]
    assert upward_steps, trajectory


def test_perturbation_respects_bounds():
    """Preservation: perturbed values stay within [min, max]."""
    space = _int_space(10, 20)
    rng = np.random.default_rng(7)
    for _ in range(200):
        out = space.perturb_config({"k": 15}, (0.8, 1.2), rng=rng)["k"]
        assert 10 <= out <= 20


def test_perturbation_preserves_dependency_repair():
    """Preservation: worker-pool knobs stay clamped to max_worker_processes."""
    knobs = [
        KnobDefinition(
            name="max_worker_processes",
            knob_type=KnobType.INTEGER,
            min_value=1,
            max_value=64,
            default=8,
        ),
        KnobDefinition(
            name="max_parallel_workers",
            knob_type=KnobType.INTEGER,
            min_value=0,
            max_value=64,
            default=8,
        ),
    ]
    space = KnobSpace(knobs)
    rng = np.random.default_rng(3)
    out = space.perturb_config(
        {"max_worker_processes": 8, "max_parallel_workers": 64},
        (0.8, 1.2),
        rng=rng,
    )
    assert out["max_parallel_workers"] <= out["max_worker_processes"]
