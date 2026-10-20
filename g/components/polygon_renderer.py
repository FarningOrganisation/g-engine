from __future__ import annotations

import pyglet
from pyglet.graphics.shader import ShaderProgram as PygletShaderProgram

from .shape_style import ShapeStyle
from .styled_shape_renderer import StyledShapeRenderer
from .renderer_utils import _transform_point
from ..resources import Color
from ..inspector import inspector_field
from ..runtime import NullDrawable, presentation_enabled

class PolygonRenderer(StyledShapeRenderer):
    """Render a polygon defined by at least three distinct local vertices.

    Attributes:
        points: Polygon vertices in local coordinates.
    """
    __inspector_fields__ = {
        "points": inspector_field([[-32, -32], [32, -32], [32, 32], [-32, 32]], tooltip="Polygon vertices in local coordinates; at least three distinct points."),
    }
    def __init__(
        self,
        points: tuple[tuple[float, float], ...],
        color: Color = (255, 255, 255),
        *,
        style: ShapeStyle | None = None,
        batch: pyglet.graphics.Batch | None = None,
        visible: bool = True,
        screen_space: bool = False,
    ) -> None:
        """Create a polygon renderer.

        Args:
            points: Polygon vertices in local coordinates.
            color: Default fill color.
            style: Optional fill and outline settings.
            batch: Optional pyglet batch for the shape drawables.
            visible: Whether the shape is shown.
            screen_space: Whether it is rendered in screen coordinates.

        Raises:
            ValueError: Fewer than three distinct vertices are supplied.
        """
        super().__init__(
            color,
            style,
            visible=visible,
            screen_space=screen_space,
        )
        if len(points) < 3 or len(set(points)) < 3:
            raise ValueError("polygon requires at least three different points")
        self.points = points
        self._scale = (1.0, 1.0)
        if not presentation_enabled():
            self._drawable = NullDrawable()
            self._outlines = tuple(
                NullDrawable() for _ in range(len(points))
            )
            self._drawable.color = self._fill_color or color
            for outline in self._outlines:
                outline.color = self._outline_color or color
            return
        fill_color = self._fill_color or (255, 255, 255, 255)
        outline_color = self._outline_color or fill_color
        self._drawable = self._create_drawable(
            fill_color, batch, None, self._scale
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

    def _create_drawable(
        self,
        color: Color,
        batch: pyglet.graphics.Batch | None,
        group: pyglet.graphics.Group | None,
        scale: tuple[float, float],
        program: PygletShaderProgram | None = None,
    ) -> pyglet.shapes.Polygon:
        scale_x, scale_y = scale
        points = tuple((x * scale_x, y * scale_y) for x, y in self.points)
        drawable = pyglet.shapes.Polygon(
            *points,
            color=color,
            batch=batch,
            group=group,
            program=program,
        )
        first_x, first_y = points[0]
        drawable.anchor_position = -first_x, -first_y
        return drawable

    @property
    def drawable(self) -> pyglet.shapes.Polygon:
        """Polygon used for the fill."""
        return self._drawable

    @property
    def fill_drawable(self) -> pyglet.shapes.Polygon:
        """Polygon used for the fill."""
        return self._drawable

    @property
    def outline_drawables(self) -> tuple[pyglet.shapes.Line, ...]:
        """Line drawables forming the polygon outline."""
        return self._outlines

    def _apply_outline_thickness(self, value: float) -> None:
        for outline in self._outlines:
            outline.thickness = value

    def sync(self) -> None:
        """Update polygon geometry and style using the owner's transform."""
        if self.owner is None:
            return
        scale = self.owner.world_scale
        if scale != self._scale:
            old_drawable = self._drawable
            self._drawable = self._create_drawable(
                old_drawable.color,
                old_drawable.batch,
                old_drawable.group,
                scale,
                old_drawable.program,
            )
            self._drawable.opacity = old_drawable.opacity
            old_drawable.delete()
            self._scale = scale
        world_x, world_y = self.owner.world_position
        self._drawable.position = world_x, world_y
        self._drawable.rotation = self.owner.world_rotation
        world_points = tuple(
            _transform_point(self.owner, point) for point in self.points
        )
        thickness = self._outline_thickness * max(
            abs(scale[0]), abs(scale[1])
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
