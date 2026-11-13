"""Colour ranges that map the frequency channels to hues."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from typing import Any

from .const import (
    DEFAULT_HUE_END,
    DEFAULT_HUE_REVERSE,
    DEFAULT_HUE_START,
)


@dataclass(frozen=True, slots=True)
class HueRange:
    """A path around the colour wheel from the bass hue to the treble hue.

    Without reverse the path runs upwards (red, yellow, green, blue, violet),
    wrapping past 360; with reverse it runs downwards. Equal start and end
    hues (0 and 360 included) give the whole wheel, starting and ending there.
    """

    start: float = DEFAULT_HUE_START
    end: float = DEFAULT_HUE_END
    reverse: bool = DEFAULT_HUE_REVERSE

    @property
    def span(self) -> float:
        """Return the length of the path in degrees, 0-360."""
        diff = (self.start - self.end) if self.reverse else (self.end - self.start)
        span = diff % 360
        # The same hue at both ends: once round the whole wheel.
        return 360.0 if span == 0 else span

    def at(self, position: float) -> float:
        """Return the hue at a position from 0 (bass) to 1 (treble)."""
        offset = self.span * position
        return (self.start - offset if self.reverse else self.start + offset) % 360

    @classmethod
    def from_options(
        cls, options: dict[str, Any], start_key: str, end_key: str, reverse_key: str
    ) -> HueRange:
        """Build a range from config entry options, with defaults."""
        return cls(
            option_hue(options.get(start_key), DEFAULT_HUE_START),
            option_hue(options.get(end_key), DEFAULT_HUE_END),
            bool(options.get(reverse_key, DEFAULT_HUE_REVERSE)),
        )


def rgb_to_hue(rgb: list[int] | tuple[int, ...]) -> float | None:
    """Return the hue of an RGB colour in whole degrees, or None for greys."""
    r, g, b = (c / 255 for c in rgb)
    hue, saturation, _ = colorsys.rgb_to_hsv(r, g, b)
    if saturation == 0:
        return None
    return float(round(hue * 360) % 360)


def hue_to_rgb(hue: float) -> list[int]:
    """Return the fully saturated, full brightness RGB colour of a hue."""
    return [round(c * 255) for c in colorsys.hsv_to_rgb((hue % 360) / 360, 1.0, 1.0)]


def option_hue(value: Any, default: float) -> float:
    """Read a stored colour option: an RGB list, or a hue (as stored by earlier versions)."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        hue = rgb_to_hue(value)
        return default if hue is None else hue
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default
