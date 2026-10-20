from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Literal, TypeAlias

from .resources import Color


ScalingMode: TypeAlias = Literal[
    "fit",
    "integer_fit",
    "fill",
    "stretch",
]
SCALING_MODES: tuple[ScalingMode, ...] = (
    "fit",
    "integer_fit",
    "fill",
    "stretch",
)


@dataclass(frozen=True)
class PresentationSettings:
    """Output scaling and window options for a virtual-resolution game.

    Matte colors use 8-bit RGB or RGBA channels.
    """

    scaling: ScalingMode = "fit"
    matte_color: Color = (0, 0, 0, 255)
    resizable: bool = True
    fullscreen: bool = False

    def __post_init__(self) -> None:
        """Validate the scaling mode and matte color."""
        if self.scaling not in SCALING_MODES:
            raise ValueError(
                f"unknown presentation scaling mode: {self.scaling!r}"
            )
        if len(self.matte_color) not in {3, 4}:
            raise ValueError("matte color must have three or four channels")
        if any(
            not isinstance(channel, int) or not 0 <= channel <= 255
            for channel in self.matte_color
        ):
            raise ValueError(
                "matte color channels must be integers from 0 to 255"
            )


@dataclass(frozen=True)
class PresentationRect:
    """Output-space rectangle occupied by a virtual canvas.

    Coordinates and dimensions are in output pixels; scale factors convert
    virtual pixels to output pixels.
    """

    x: float
    y: float
    width: float
    height: float
    scale_x: float
    scale_y: float
    virtual_width: int
    virtual_height: int

    @property
    def right(self) -> float:
        """Return the right edge in output pixels."""
        return self.x + self.width

    @property
    def top(self) -> float:
        """Return the top edge in output pixels."""
        return self.y + self.height

    def contains(self, x: float, y: float) -> bool:
        """Return whether an output-pixel point lies within the rectangle."""
        return self.x <= x <= self.right and self.y <= y <= self.top

    def to_virtual(
        self,
        x: float,
        y: float,
        *,
        clamp: bool = False,
    ) -> tuple[float, float] | None:
        """Convert an output-pixel point to virtual coordinates.

        Args:
            x (float): Output-space x-coordinate.
            y (float): Output-space y-coordinate.
            clamp (bool): Clamp points outside the rectangle to its virtual bounds.

        Returns:
            tuple[float, float] | None: Virtual-pixel coordinates, or ``None``
                when outside and unclamped.
        """
        if not clamp and not self.contains(x, y):
            return None
        virtual_x = (x - self.x) / self.scale_x
        virtual_y = (y - self.y) / self.scale_y
        if clamp:
            virtual_x = min(max(virtual_x, 0), self.virtual_width)
            virtual_y = min(max(virtual_y, 0), self.virtual_height)
        return virtual_x, virtual_y

    def delta_to_virtual(
        self,
        delta_x: float,
        delta_y: float,
    ) -> tuple[float, float]:
        """Scale an output-pixel delta into virtual-pixel units."""
        return delta_x / self.scale_x, delta_y / self.scale_y


def presentation_rect(
    virtual_width: int,
    virtual_height: int,
    output_width: int | float,
    output_height: int | float,
    scaling: ScalingMode = "fit",
) -> PresentationRect:
    """Compute the canvas rectangle for a virtual and output size.

    ``fit`` preserves aspect ratio and fits entirely; ``integer_fit`` uses an
    integer scale when the output permits; ``fill`` preserves aspect ratio but
    may crop; ``stretch`` independently scales both axes.

    Args:
        virtual_width (int): Virtual canvas width in pixels.
        virtual_height (int): Virtual canvas height in pixels.
        output_width (int | float): Available output width in pixels.
        output_height (int | float): Available output height in pixels.
        scaling (ScalingMode): Scaling mode to apply.

    Returns:
        PresentationRect: Rectangle and scale factors mapping the virtual canvas to the output.

    Raises:
        ValueError: If a dimension is non-positive or the mode is unknown.
    """
    if virtual_width <= 0 or virtual_height <= 0:
        raise ValueError("virtual dimensions must be positive")
    if output_width <= 0 or output_height <= 0:
        raise ValueError("output dimensions must be positive")
    if scaling not in SCALING_MODES:
        raise ValueError(
            f"unknown presentation scaling mode: {scaling!r}"
        )
    width_ratio = float(output_width) / virtual_width
    height_ratio = float(output_height) / virtual_height
    if scaling == "stretch":
        scale_x = width_ratio
        scale_y = height_ratio
    else:
        scale = (
            max(width_ratio, height_ratio)
            if scaling == "fill"
            else min(width_ratio, height_ratio)
        )
        if scaling == "integer_fit" and scale >= 1:
            scale = max(1.0, float(floor(scale)))
        scale_x = scale
        scale_y = scale
    width = virtual_width * scale_x
    height = virtual_height * scale_y
    return PresentationRect(
        (float(output_width) - width) / 2,
        (float(output_height) - height) / 2,
        width,
        height,
        scale_x,
        scale_y,
        virtual_width,
        virtual_height,
    )
