from __future__ import annotations

import pyglet

from .shape_style import ShapeStyle
from .styled_shape_renderer import StyledShapeRenderer
from ..resources import Color
from ..inspector import inspector_field
from ..runtime import NullDrawable, presentation_enabled

class CircleRenderer(StyledShapeRenderer):
    """Render a circle centered on the owning object's world position.

    Attributes:
        radius: Circle radius in local pixels.
    """
    __inspector_fields__ = {
        "radius": inspector_field(32.0, minimum=0, tooltip="Circle radius in local pixels."),
    }
    def __init__(
        self,
        radius: float,
        color: Color = (255, 255, 255),
        *,
        style: ShapeStyle | None = None,
        batch: pyglet.graphics.Batch | None = None,
        visible: bool = True,
        screen_space: bool = False,
    ) -> None:
        """Create a circle renderer.

        Args:
            radius: Circle radius in local pixels.
            color: Default fill color.
            style: Optional fill and outline settings.
            batch: Optional pyglet batch for the shape drawables.
            visible: Whether the shape is shown.
            screen_space: Whether it is rendered in screen coordinates.

        Raises:
            ValueError: ``radius`` is negative.
        """
        super().__init__(
            color,
            style,
            visible=visible,
            screen_space=screen_space,
        )
        if radius < 0:
            raise ValueError("radius must not be negative")
        self.radius = radius
        if not presentation_enabled():
            self._drawable = NullDrawable()
            self._outline = NullDrawable()
            self._drawable.color = self._fill_color or color
            self._outline.color = self._outline_color or color
            return
        fill_color = self._fill_color or (255, 255, 255, 255)
        outline_color = self._outline_color or fill_color
        self._drawable = pyglet.shapes.Circle(
            0, 0, radius, color=fill_color, batch=batch
        )
        self._outline = pyglet.shapes.Arc(
            0,
            0,
            radius,
            closed=True,
            thickness=self._outline_thickness,
            color=outline_color,
            batch=batch,
        )

    @property
    def drawable(self) -> pyglet.shapes.Circle:
        """Circle used for the fill."""
        return self._drawable

    @property
    def fill_drawable(self) -> pyglet.shapes.Circle:
        """Circle used for the fill."""
        return self._drawable

    @property
    def outline_drawables(self) -> tuple[pyglet.shapes.Arc, ...]:
        """Arc drawable used for the outline."""
        return (self._outline,)

    def _apply_outline_thickness(self, value: float) -> None:
        self._outline.thickness = value

    def sync(self) -> None:
        """Update position, scale, outline, and visibility from the owner."""
        if self.owner is None:
            return
        world_x, world_y = self.owner.world_position
        self._drawable.position = world_x, world_y
        self._outline.position = world_x, world_y
        scale_x, scale_y = self.owner.world_scale
        scale = max(abs(scale_x), abs(scale_y))
        self._drawable.radius = self.radius * scale
        self._outline.radius = self.radius * scale
        self._outline.thickness = self._outline_thickness * scale
        self._sync_style()
