# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""Non-finite knob values must not survive load or construction (ticket #171 / B10).

Background
----------
``ssl_max_protocol_version`` in the extensive tier has an empty ``boot_val``.
``pandas`` reads the empty cell as ``NaN`` and the loader's
``boot_val or value`` fallback keeps it (``NaN`` is truthy), so the knob's
``default`` becomes a floating-point ``NaN``. Because ``NaN != NaN`` the knob
registers as a spurious configuration change on every generation and would
reach PostgreSQL as the literal string ``'nan'``.

Seams under test:
- ``load_knob_space_for_tier`` / ``load_knob_space_from_csv`` (source fix).
- ``KnobSpace.__init__`` construction guard.
"""

import math

import numpy as np
import pytest

from src.knobs.knob_loader import (
    load_knob_space_for_tier,
    load_knob_space_from_csv,
)
from src.knobs.knob_space import KnobDefinition, KnobSpace, KnobType

SHIPPED_TIERS = ("minimal", "core", "standard", "extensive")


def _is_non_finite(value) -> bool:
    return isinstance(value, (float, np.floating)) and not math.isfinite(value)


@pytest.mark.parametrize("tier", SHIPPED_TIERS)
def test_no_non_finite_values_survive_construction_for_every_tier(tier):
    """Every shipped tier must construct with only finite numeric values."""
    space = load_knob_space_for_tier(tier)
    offenders = {
        name: {
            field: getattr(knob, field)
            for field in ("min_value", "max_value", "default")
            if _is_non_finite(getattr(knob, field))
        }
        for name, knob in space.knobs.items()
    }
    offenders = {name: fields for name, fields in offenders.items() if fields}
    assert offenders == {}, offenders


def test_ssl_max_protocol_version_carries_a_valid_enum_default():
    """The offending knob must carry a valid enum member, not NaN.

    Its empty PostgreSQL boot value corresponds to the '' enum member (no
    version ceiling), which is a valid choice in the knob's enum domain.
    """
    space = load_knob_space_from_csv(
        "data/expert_defined_knobs/extensive_knobs.csv"
    )
    knob = space.knobs["ssl_max_protocol_version"]
    assert knob.default is not None
    assert not _is_non_finite(knob.default)
    assert knob.default in knob.enum_values


def test_construction_rejects_non_finite_numeric_value():
    """KnobSpace construction must reject a knob carrying a NaN numeric field."""
    bad = KnobDefinition(
        name="broken",
        knob_type=KnobType.REAL,
        min_value=0.0,
        max_value=1.0,
        default=float("nan"),
    )
    with pytest.raises(ValueError, match="non-finite"):
        KnobSpace([bad])


def test_default_config_diff_is_stable_for_offending_knob():
    """Two independent default reads must not report a spurious change.

    NaN != NaN would make ssl_max_protocol_version look changed between two
    identical default configs; a valid default keeps the diff empty.
    """
    space = load_knob_space_from_csv(
        "data/expert_defined_knobs/extensive_knobs.csv"
    )
    first = space.get_default_config()
    second = space.get_default_config()
    changed = {
        name
        for name in first
        if first[name] != second[name]
    }
    assert "ssl_max_protocol_version" not in changed
    assert changed == set()
