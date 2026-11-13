"""Colour ranges."""

from __future__ import annotations

import pytest

from custom_components.wled_audio_receiver.spectrum import (
    HueRange,
    hue_to_rgb,
    option_hue,
    rgb_to_hue,
)


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
        # The same colour at both ends: the whole wheel, from and back to it.
        (HueRange(200, 200), {0: 200, 0.25: 290, 0.5: 20, 1: 200}),
        (HueRange(200, 200, reverse=True), {0: 200, 0.25: 110, 0.5: 20, 1: 200}),
        (HueRange(0, 0), {0: 0, 0.5: 180}),
        # Nearly the same colours: (nearly) one colour.
        (HueRange(200, 201), {0: 200, 0.5: 200.5, 1: 201}),
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


@pytest.mark.parametrize(
    ("rgb", "hue"),
    [
        ([255, 0, 0], 0),
        ([255, 255, 0], 60),
        ([0, 255, 0], 120),
        ([0, 0, 255], 240),
        ([255, 0, 255], 300),
        ([128, 0, 255], 270),  # the default treble colour
        ([64, 0, 0], 0),  # dark and pale colours keep their hue
        ([255, 200, 200], 0),
        ([255, 255, 255], None),
        ([0, 0, 0], None),
    ],
)
def test_rgb_to_hue(rgb: list[int], hue: float | None) -> None:
    """Only the hue of a picked colour is used; greys have none."""
    assert rgb_to_hue(rgb) == hue


def test_hue_to_rgb_round_trip() -> None:
    """Whole-degree hues survive a round trip through the colour picker."""
    for hue in range(0, 360, 5):
        assert rgb_to_hue(hue_to_rgb(hue)) == hue


def test_option_hue() -> None:
    """Stored colours are RGB lists; hues stored by earlier versions still work."""
    assert option_hue([0, 0, 255], 0) == 240
    assert option_hue(200, 0) == 200
    assert option_hue(None, 270) == 270
    assert option_hue([10, 10, 10], 270) == 270  # grey: default
    assert option_hue(True, 270) == 270


def test_same_picked_colour_is_whole_wheel() -> None:
    """Picking the same colour for bass and treble gives the whole wheel."""
    keys = ("a", "b", "c")
    hues = HueRange.from_options({"a": [255, 0, 0], "b": [255, 0, 0]}, *keys)
    assert hues.span == 360
    assert hues.at(0.5) == pytest.approx(180)
