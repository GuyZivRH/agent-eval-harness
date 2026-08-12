"""Tests for per-case score banding and histograms (issue #182).

A value off the judge's declared scale is invalid, not excellent: it must not
render as a green pass, and it must not vanish from the distribution glyph.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "eval-run" / "scripts"))

from report import _ascii_score_hist, _score_band_class


def test_bands_span_the_declared_scale():
    assert _score_band_class(2, 0, 2) == "pass"
    assert _score_band_class(1, 0, 2) == "warn"
    assert _score_band_class(0, 0, 2) == "fail"


def test_value_above_the_scale_is_not_a_pass():
    # frac would be 2.0 -> "pass" without the guard.
    assert _score_band_class(4, 0, 2) == "fail"


def test_value_below_the_scale_is_not_a_pass():
    assert _score_band_class(-1, 0, 2) == "fail"


def test_histogram_keeps_an_off_scale_value_visible():
    glyph = _ascii_score_hist(1, [0, 1, 4], smin=0, smax=2)
    # Bins are lo..hi inclusive; the 4 widens the axis instead of being dropped.
    assert glyph.endswith(" 4")


def test_histogram_unchanged_when_every_value_is_on_scale():
    glyph = _ascii_score_hist(1, [0, 1, 2], smin=0, smax=2)
    assert glyph.startswith("0 ") and glyph.endswith(" 2")
