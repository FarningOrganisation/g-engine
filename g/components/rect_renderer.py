from __future__ import annotations

import pyglet

from .shape_style import ShapeStyle
from .styled_shape_renderer import StyledShapeRenderer
from ..resources import Color
from ..inspector import inspector_field
from ..runtime import NullDrawable, presentation_enabled

class RectRenderer(StyledShapeRenderer):
    """Render a centered rectangle using the owner's world transform.

    Attributes:
        width: Rectangle width in local pixels.
        height: Rectangle height in local pixels.
    """
    __inspector_fields__ = {
        "width": inspector_field(64.0, minimum=0, tooltip="Rectangle width in local pixels."),
        "height": inspector_field(64.0, minimum=0, tooltip="Rectangle height in local pixels."),
    }
    def __init__(
        self,
        width: float,
        height: float,
        color: Color = (255, 255, 255),
        *,
        style: ShapeStyle | None = None,
        batch: pyglet.graphics.Batch | None = None,
        visible: bool = True,
        screen_space: bool = False,
    ) -> None:
        """Create a rectangle renderer.

        Args:
            width: Rectangle width in local pixels.
            height: Rectangle height in local pixels.
            color: Default fill color.
            style: Optional fill and outline settings.
            batch: Optional pyglet batch for the shape drawables.
            visible: Whether the shape is shown.
            screen_space: Whether it is rendered in screen coordinates.

        Raises:
            ValueError: Either dimension is negative.
        """
        super().__init__(
            color,
            style,
            visible=visible,
            screen_space=screen_space,
        )
        if width < 0 or height < 0:
            raise ValueError("width and height must not be negative")
        self.width = width
        self.height = height
        if not presentation_enabled():
            self._drawable = NullDrawable()
            self._outline = NullDrawable()
            self._drawable.color = self._fill_color or color
            self._outline.color = self._outline_color or color
            return
        fill_color = self._fill_color or (255, 255, 255, 255)
        outline_color = self._outline_color or fill_color
        self._drawable = pyglet.shapes.Rectangle(
            0, 0, width, height, color=fill_color, batch=batch
        )
        self._drawable.anchor_position = width / 2, height / 2
        self._outline = pyglet.shapes.Box(
            0,
            0,
            width,
            height,
            thickness=self._outline_thickness,
            color=outline_color,
            batch=batch,
        )
        self._outline.anchor_position = width / 2, height / 2

    @property
    def drawable(self) -> pyglet.shapes.Rectangle:
        """Rectangle used for the fill."""
        return self._drawable

    @property
    def fill_drawable(self) -> pyglet.shapes.Rectangle:
        """Rectangle used for the fill."""
        return self._drawable

    @property
    def outline_drawables(self) -> tuple[pyglet.shapes.Box, ...]:
        """Box drawable used for the outline."""
        return (self._outline,)

    def _apply_outline_thickness(self, value: float) -> None:
        self._outline.thickness = value

    def sync(self) -> None:
        """Update position, rotation, scale, outline, and style from the owner."""
        if self.owner is None:
            return
        world_x, world_y = self.owner.world_position
        self._drawable.position = world_x, world_y
        self._outline.position = world_x, world_y
        self._drawable.rotation = self.owner.world_rotation
        self._outline.rotation = self.owner.world_rotation
        scale_x, scale_y = self.owner.world_scale
        self._drawable.width = self.width * abs(scale_x)
        self._drawable.height = self.height * abs(scale_y)
        self._outline.width = self.width * abs(scale_x)
        self._outline.height = self.height * abs(scale_y)
        self._outline.thickness = self._outline_thickness * max(
            abs(scale_x), abs(scale_y)
        )
        self._sync_style()
