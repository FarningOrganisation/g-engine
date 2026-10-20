from __future__ import annotations

import pyglet

from .renderer import Renderer
from .renderer_utils import _transform_point
from ..resources import Color
from ..inspector import inspector_field
from ..runtime import NullDrawable, presentation_enabled

class LineRenderer(Renderer):
    """Render a line between two local-space points.

    Attributes:
        start: Local-space line start.
        end: Local-space line end.
        thickness: Positive line width before owner scaling.
        color: Line color.
        opacity: Line opacity.
    """
    __inspector_fields__ = {
        "start": inspector_field([-32, 0], tooltip="Line start in local coordinates."),
        "end": inspector_field([32, 0], tooltip="Line end in local coordinates."),
        "color": inspector_field([255, 255, 255, 255], widget="color", tooltip="Line color."),
        "thickness": inspector_field(1.0, minimum=0.001, tooltip="Line width in pixels."),
        "opacity": inspector_field(255, minimum=0, maximum=255, tooltip="Opacity from transparent (0) to opaque (255)."),
    }
    def __init__(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        color: Color = (255, 255, 255),
        thickness: float = 1,
        *,
        batch: pyglet.graphics.Batch | None = None,
        visible: bool = True,
        screen_space: bool = False,
    ) -> None:
        """Create a line renderer.

        Args:
            start: Local-space line start.
            end: Local-space line end.
            color: Line color.
            thickness: Positive line width in pixels.
            batch: Optional pyglet batch for the drawable.
            visible: Whether the line is shown.
            screen_space: Whether its coordinates are screen-space.

        Raises:
            ValueError: Endpoints are equal or thickness is not positive.
        """
        super().__init__(visible=visible, screen_space=screen_space)
        if start == end:
            raise ValueError("line endpoints must be different")
        if thickness <= 0:
            raise ValueError("thickness must be positive")
        self.start = start
        self.end = end
        self._thickness = thickness
        if not presentation_enabled():
            self._drawable = NullDrawable()
            self._drawable.color = color
            self._drawable.thickness = thickness
            return
        self._drawable = pyglet.shapes.Line(
            *start,
            *end,
            thickness=thickness,
            color=color,
            batch=batch,
        )

    @property
    def drawable(self) -> pyglet.shapes.Line:
        """Underlying pyglet line drawable."""
        return self._drawable

    @property
    def thickness(self) -> float:
        """Configured line width before owner scaling."""
        return self._thickness

    @thickness.setter
    def thickness(self, value: float) -> None:
        """Set a positive line width."""
        if value <= 0:
            raise ValueError("thickness must be positive")
        self._thickness = value
        self._drawable.thickness = value

    @property
    def color(self) -> Color:
        """Line color."""
        return self._drawable.color

    @color.setter
    def color(self, value: Color) -> None:
        """Set the line color."""
        self._drawable.color = value

    @property
    def opacity(self) -> int:
        """Line opacity."""
        return self._drawable.opacity

    @opacity.setter
    def opacity(self, value: int) -> None:
        """Set the line opacity."""
        self._drawable.opacity = value

    def sync(self) -> None:
        """Transform endpoints and synchronize width and visibility."""
        if self.owner is None:
            return
        self._drawable.x, self._drawable.y = _transform_point(
            self.owner, self.start
        )
        self._drawable.x2, self._drawable.y2 = _transform_point(
            self.owner, self.end
        )
        scale_x, scale_y = self.owner.world_scale
        self._drawable.thickness = self._thickness * max(
            abs(scale_x), abs(scale_y)
        )
        self._drawable.visible = self.effective_visible
