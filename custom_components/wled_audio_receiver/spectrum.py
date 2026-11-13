"""Colour ranges that map the frequency channels to hues."""

from __future__ import annotations

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
    give one colour; start 0 and end 360 give the whole wheel.
    """

    start: float = DEFAULT_HUE_START
    end: float = DEFAULT_HUE_END
    reverse: bool = DEFAULT_HUE_REVERSE

    @property
    def span(self) -> float:
        """Return the length of the path in degrees, 0-360."""
        diff = (self.start - self.end) if self.reverse else (self.end - self.start)
        span = diff % 360
        if span == 0 and self.start % 360 == self.end % 360 and self.start != self.end:
            return 360.0
        return span

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
            float(options.get(start_key, DEFAULT_HUE_START)),
            float(options.get(end_key, DEFAULT_HUE_END)),
            bool(options.get(reverse_key, DEFAULT_HUE_REVERSE)),
        )
