from __future__ import annotations

import pyglet

from .shape_style import ShapeStyle
from .styled_shape_renderer import StyledShapeRenderer
from .renderer_utils import _bounds_from_points, _transform_point
from ..resources import Color
from ..inspector import inspector_field
from ..runtime import NullDrawable, presentation_enabled

class TriangleRenderer(StyledShapeRenderer):
    """Render a three-vertex polygon defined in local coordinates.

    Attributes:
        points: Three local-space vertices.
    """
    __inspector_fields__ = {
        "points": inspector_field([[-32, -24], [32, -24], [0, 32]], tooltip="Three distinct local-space vertices."),
    }
    def __init__(
        self,
        points: tuple[
            tuple[float, float],
            tuple[float, float],
            tuple[float, float],
        ],
        color: Color = (255, 255, 255),
        *,
        style: ShapeStyle | None = None,
        batch: pyglet.graphics.Batch | None = None,
        visible: bool = True,
        screen_space: bool = False,
    ) -> None:
        """Create a triangle renderer.

        Args:
            points: Three distinct local-space vertices.
            color: Default fill color.
            style: Optional fill and outline settings.
            batch: Optional pyglet batch for the shape drawables.
            visible: Whether the shape is shown.
            screen_space: Whether it is rendered in screen coordinates.

        Raises:
            ValueError: The points are not distinct.
        """
        super().__init__(
            color,
            style,
            visible=visible,
            screen_space=screen_space,
        )
        if len(set(points)) != 3:
            raise ValueError("triangle points must be different")
        self.points = points
        if not presentation_enabled():
            self._drawable = NullDrawable()
            self._outlines = tuple(NullDrawable() for _ in range(3))
            self._drawable.color = self._fill_color or color
            for outline in self._outlines:
                outline.color = self._outline_color or color
            return
        fill_color = self._fill_color or (255, 255, 255, 255)
        outline_color = self._outline_color or fill_color
        self._drawable = pyglet.shapes.Triangle(
            *points[0],
            *points[1],
            *points[2],
            color=fill_color,
            batch=batch,
        )
        self._outlines = tuple(
            pyglet.shapes.Line(
                *start,
                *end,
                thickness=self._outline_thickness,
                color=outline_color,
                batch=batch,
            )
            for start, end in zip(points, (*points[1:], points[0]))
        )

    @property
    def drawable(self) -> pyglet.shapes.Triangle:
        """Triangle used for the fill."""
        return self._drawable

    @property
    def fill_drawable(self) -> pyglet.shapes.Triangle:
        """Triangle used for the fill."""
        return self._drawable

    @property
    def outline_drawables(self) -> tuple[pyglet.shapes.Line, ...]:
        """Three line drawables forming the outline."""
        return self._outlines

    def _apply_outline_thickness(self, value: float) -> None:
        for outline in self._outlines:
            outline.thickness = value

    def sync(self) -> None:
        """Transform vertices and synchronize style with the owning object."""
        if self.owner is None:
            return
        first, second, third = (
            _transform_point(self.owner, point) for point in self.points
        )
        self._drawable.position = first
        self._drawable.x2, self._drawable.y2 = second
        self._drawable.x3, self._drawable.y3 = third
        world_points = (first, second, third)
        scale_x, scale_y = self.owner.world_scale
        thickness = self._outline_thickness * max(
            abs(scale_x), abs(scale_y)
        )
        for outline, start, end in zip(
            self._outlines,
            world_points,
            (*world_points[1:], world_points[0]),
        ):
            outline.position = start
            outline.x2, outline.y2 = end
            outline.thickness = thickness
        self._sync_style()

    def world_bounds(self) -> tuple[float, float, float, float] | None:
        """Return transformed triangle bounds including its outline."""
        if self.owner is None:
            return None
        scale_x, scale_y = self.owner.world_scale
        return _bounds_from_points(
            tuple(_transform_point(self.owner, point) for point in self.points),
            padding=self._outline_thickness
            * max(abs(scale_x), abs(scale_y))
            / 2,
        )
