"""Colour ranges."""

from __future__ import annotations

import pytest

from custom_components.wled_audio_receiver.spectrum import HueRange


def test_default_range() -> None:
    """The default runs from red up to violet, as before the option existed."""
    hues = HueRange()
    assert (hues.start, hues.end, hues.reverse) == (0, 270, False)
    assert [hues.at(p) for p in (0, 0.5, 1)] == [0, 135, 270]


@pytest.mark.parametrize(
    ("hues", "positions"),
    [
        # Upwards, wrapping past 360: magenta, red, yellow.
        (HueRange(300, 60), {0: 300, 0.5: 0, 1: 60}),
        # Downwards from the same ends: magenta, blue, cyan, green, yellow.
        (HueRange(300, 60, reverse=True), {0: 300, 0.5: 180, 1: 60}),
        # Downwards without wrapping: blue, cyan, green, yellow, red.
        (HueRange(240, 0, reverse=True), {0: 240, 0.5: 120, 1: 0}),
        # Upwards from blue to red wraps through magenta.
        (HueRange(240, 0), {0: 240, 0.5: 300, 1: 0}),
        # The whole wheel.
        (HueRange(0, 360), {0: 0, 0.25: 90, 0.5: 180, 1: 0}),
        (HueRange(0, 360, reverse=True), {0: 0, 0.25: 270, 0.5: 180}),
        # One colour.
        (HueRange(200, 200), {0: 200, 0.5: 200, 1: 200}),
        (HueRange(200, 200, reverse=True), {0: 200, 1: 200}),
    ],
)
def test_range_paths(hues: HueRange, positions: dict[float, float]) -> None:
    """Hues follow the chosen direction round the wheel."""
    for position, expected in positions.items():
        assert hues.at(position) == pytest.approx(expected), position


def test_from_options() -> None:
    """Missing options fall back to the defaults."""
    keys = ("a", "b", "c")
    assert HueRange.from_options({}, *keys) == HueRange()
    assert HueRange.from_options({"a": 10, "b": 20, "c": True}, *keys) == HueRange(10, 20, True)
