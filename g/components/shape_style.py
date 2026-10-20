from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from ..resources import Color

@dataclass(frozen=True)
class ShapeStyle:
    """Immutable fill and outline settings shared by shape renderers.

    Attributes:
        fill: Fill color, or ``None`` to disable filling.
        outline: Outline color, or ``None`` to disable the outline.
        outline_thickness: Positive outline width in local pixels.
    """
    fill: Color | None = (255, 255, 255)
    outline: Color | None = None
    outline_thickness: float = 1

    def __post_init__(self) -> None:
        """Validate that the style draws something with a positive width."""
        if self.fill is None and self.outline is None:
            raise ValueError("shape style requires a fill or outline")
        if not isfinite(self.outline_thickness) or self.outline_thickness <= 0:
            raise ValueError("outline_thickness must be finite and positive")
