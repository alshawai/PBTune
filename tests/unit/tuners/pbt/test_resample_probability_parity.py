"""Ticket #166 — single source of truth for ``resample_probability`` (bug B3).

Before this fix two literals diverged:

* ``PBTConfig.resample_probability`` defaulted to ``0.0`` for every preset
  (``config.py``), so a *programmatic* construction of a profile ran with
  ``0.0``.
* ``build_pbt_config`` fell back to a hard-coded ``0.1`` when
  ``--resample-probability`` was omitted (``cli.py``), so a *CLI* invocation of
  the same profile ran a different algorithm.

The canonical value is ``0.1`` — the probability the reference experiment setup
(the ``thorough`` profile, run via the CLI) actually used, and the value the
``--resample-probability`` help text has always documented as the default. The
fix therefore unifies *upward*: the ``PBTConfig`` field default becomes ``0.1``
and the CLI's duplicated literal is removed so the CLI inherits that one
default. This is behaviour-preserving for CLI runs (already ``0.1``); only
programmatic ``PBTConfig()`` construction rises ``0.0 -> 0.1`` to match.

The expected literal ``0.1`` comes from that canonical decision, not from
recomputing either code path's own fallback.
"""

from __future__ import annotations

import pytest

from src.tuners.pbt.cli import PBT_CONFIG_BY_PROFILE, build_pbt_config, parse_args
from src.tuners.pbt.config import PBTConfig

# The single source of truth: every shipped preset leaves resample_probability
# at the PBTConfig default of 0.1, and the CLI inherits it when unset.
CANONICAL_RESAMPLE_PROBABILITY = 0.1


def test_pbtconfig_default_is_the_canonical_value() -> None:
    """The one definition lives on ``PBTConfig`` and is the canonical 0.1."""
    assert PBTConfig().resample_probability == CANONICAL_RESAMPLE_PROBABILITY


def test_cli_default_falls_back_to_preset_single_source() -> None:
    """A bare CLI invocation must inherit the preset default, not a CLI literal."""
    args = parse_args([])  # --config defaults to "standard", no --resample-probability

    config = build_pbt_config(args)

    assert config.resample_probability == CANONICAL_RESAMPLE_PROBABILITY


@pytest.mark.parametrize("profile", sorted(PBT_CONFIG_BY_PROFILE))
def test_cli_and_programmatic_parity_all_profiles(profile: str) -> None:
    """CLI-built and programmatic construction of a profile must not diverge."""
    args = parse_args(["--config", profile])

    cli_config = build_pbt_config(args)
    preset = PBT_CONFIG_BY_PROFILE[profile]

    # Parity: the CLI must not silently substitute a different value than the
    # preset a programmatic caller would get.
    assert cli_config.resample_probability == preset.resample_probability
    # And every shipped profile carries the canonical single source of truth.
    assert cli_config.resample_probability == CANONICAL_RESAMPLE_PROBABILITY


def test_explicit_resample_probability_override_honored() -> None:
    """An explicit --resample-probability still overrides the preset default."""
    args = parse_args(["--resample-probability", "0.35"])

    config = build_pbt_config(args)

    assert config.resample_probability == 0.35


def test_pbtconfig_to_dict_records_resample_probability() -> None:
    """The config's serialization contract carries resample_probability.

    ``to_dict`` is the source the on-disk session record draws from, so the
    field must be present with the canonical default when unset.
    """
    serialized = PBTConfig().to_dict()

    assert "resample_probability" in serialized
    assert serialized["resample_probability"] == CANONICAL_RESAMPLE_PROBABILITY
