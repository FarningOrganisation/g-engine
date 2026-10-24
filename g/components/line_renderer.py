from __future__ import annotations

import pyglet
import pyglet.shapes

from .renderer import Renderer
from .renderer_utils import _bounds_from_points, _transform_point
from ..geometry import (
    DashPattern,
    dashed_line_segments,
    validate_dash_pattern,
)
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
        "dash_pattern": inspector_field(None, tooltip="Optional [dash, gap] lengths in local pixels."),
        "opacity": inspector_field(255, minimum=0, maximum=255, tooltip="Opacity from transparent (0) to opaque (255)."),
    }
    def __init__(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        color: Color = (255, 255, 255),
        thickness: float = 1,
        *,
        dash_pattern: DashPattern | None = None,
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
            dash_pattern: Optional positive ``(dash, gap)`` lengths.
            batch: Optional pyglet batch for the drawable.
            visible: Whether the line is shown.
            screen_space: Whether its coordinates are screen-space.

        Raises:
            ValueError: Endpoints, thickness, or dash pattern is invalid.
        """
        super().__init__(visible=visible, screen_space=screen_space)
        if start == end:
            raise ValueError("line endpoints must be different")
        if thickness <= 0:
            raise ValueError("thickness must be positive")
        self.start = start
        self.end = end
        self._thickness = thickness
        self._dash_pattern = validate_dash_pattern(dash_pattern)
        self._presentation_enabled = presentation_enabled()
        if not self._presentation_enabled:
            self._drawables = tuple(
                NullDrawable()
                for _ in dashed_line_segments(
                    start,
                    end,
                    self._dash_pattern,
                )
            )
            for drawable in self._drawables:
                drawable.color = color
                drawable.thickness = thickness
            return
        self._drawables = tuple(
            pyglet.shapes.Line(
                *segment_start,
                *segment_end,
                thickness=thickness,
                color=color,
                batch=batch,
            )
            for segment_start, segment_end in dashed_line_segments(
                start,
                end,
                self._dash_pattern,
            )
        )

    @property
    def drawable(self) -> pyglet.shapes.Line:
        """Underlying pyglet line drawable."""
        return self._drawables[0]

    @property
    def drawables(self) -> tuple[pyglet.shapes.Line, ...]:
        """Line drawables for the solid line or visible dashes."""
        return self._drawables

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
        for drawable in self._drawables:
            drawable.thickness = value

    @property
    def dash_pattern(self) -> DashPattern | None:
        """Visible dash and blank lengths, or ``None`` for a solid line."""
        return self._dash_pattern

    @dash_pattern.setter
    def dash_pattern(self, value: DashPattern | None) -> None:
        """Set positive dash and blank lengths, or disable dashing."""
        self._dash_pattern = validate_dash_pattern(value)

    @property
    def color(self) -> Color:
        """Line color."""
        return self.drawable.color

    @color.setter
    def color(self, value: Color) -> None:
        """Set the line color."""
        for drawable in self._drawables:
            drawable.color = value

    @property
    def opacity(self) -> int:
        """Line opacity."""
        return self.drawable.opacity

    @opacity.setter
    def opacity(self, value: int) -> None:
        """Set the line opacity."""
        for drawable in self._drawables:
            drawable.opacity = value

    def _resize_drawables(self, count: int) -> None:
        count = max(1, count)
        current = len(self._drawables)
        if count < current:
            for drawable in self._drawables[count:]:
                drawable.delete()
            self._drawables = self._drawables[:count]
            return
        if count == current:
            return
        color = self.drawable.color
        opacity = self.drawable.opacity
        added = []
        for _ in range(count - current):
            drawable = (
                pyglet.shapes.Line(
                    0,
                    0,
                    1,
                    0,
                    thickness=self._thickness,
                    color=color,
                )
                if self._presentation_enabled
                else NullDrawable()
            )
            drawable.color = color
            drawable.thickness = self._thickness
            drawable.opacity = opacity
            drawable.batch = (
                self._desired_batch if self._camera_visible else None
            )
            drawable.group = (
                self._desired_group if self._camera_visible else None
            )
            if self._shader is not None and self._presentation_enabled:
                drawable.program = self._shader.program
            added.append(drawable)
        self._drawables = (*self._drawables, *added)

    def sync(self) -> None:
        """Transform endpoints and synchronize width and visibility."""
        if self.owner is None:
            return
        start = _transform_point(self.owner, self.start)
        end = _transform_point(self.owner, self.end)
        scale_x, scale_y = self.owner.world_scale
        scale = max(abs(scale_x), abs(scale_y))
        pattern = (
            None
            if self._dash_pattern is None
            else (
                self._dash_pattern[0] * scale,
                self._dash_pattern[1] * scale,
            )
        )
        segments = dashed_line_segments(start, end, pattern)
        self._resize_drawables(len(segments))
        thickness = self._thickness * scale
        for index, drawable in enumerate(self._drawables):
            visible = index < len(segments)
            if visible:
                segment_start, segment_end = segments[index]
                drawable.x, drawable.y = segment_start
                drawable.x2, drawable.y2 = segment_end
                drawable.thickness = thickness
            drawable.visible = bool(visible and self.effective_visible)

    def world_bounds(self) -> tuple[float, float, float, float] | None:
        """Return transformed line bounds including its thickness."""
        if self.owner is None:
            return None
        scale_x, scale_y = self.owner.world_scale
        return _bounds_from_points(
            (
                _transform_point(self.owner, self.start),
                _transform_point(self.owner, self.end),
            ),
            padding=self._thickness * max(abs(scale_x), abs(scale_y)) / 2,
        )
