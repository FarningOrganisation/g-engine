from __future__ import annotations

from abc import ABC, abstractmethod
from math import isfinite

import pyglet

from .renderer import Drawable, Renderer
from .shape_style import ShapeStyle
from ..resources import Color
from ..inspector import inspector_field

class StyledShapeRenderer(Renderer, ABC):
    """Base class for filled and outlined geometric renderers."""
    __inspector_fields__ = {
        "fill": inspector_field(
            [255, 255, 255, 255], widget="color", optional_color=(255, 255, 255, 255),
            counterpart="outline", tooltip="Shape fill color. Disable to render only the outline.",
        ),
        "outline": inspector_field(
            None, widget="color", optional_color=(0, 0, 0, 255),
            counterpart="fill", tooltip="Shape outline color. Enable to draw a border.",
        ),
        "outline_thickness": inspector_field(1.0, minimum=0.001, tooltip="Outline width in pixels."),
    }
    def __init__(
        self,
        color: Color,
        style: ShapeStyle | None,
        *,
        visible: bool,
        screen_space: bool,
    ) -> None:
        """Initialize style state shared by concrete shape renderers.

        Args:
            color: Fallback fill color when no explicit style is supplied.
            style: Fill and outline configuration; defaults to a solid fill.
            visible: Whether the shape is shown.
            screen_space: Whether coordinates are in screen space.
        """
        super().__init__(visible=visible, screen_space=screen_space)
        resolved = style or ShapeStyle(fill=color)
        self._fill_color = resolved.fill
        self._outline_color = resolved.outline
        self._outline_thickness = resolved.outline_thickness
        self._fill_visible = resolved.fill is not None
        self._outline_visible = resolved.outline is not None

    @property
    @abstractmethod
    def fill_drawable(self) -> pyglet.shapes.ShapeBase:
        """Drawable used for the shape fill."""
        pass

    @property
    @abstractmethod
    def outline_drawables(self) -> tuple[pyglet.shapes.ShapeBase, ...]:
        """Drawables used to form the shape outline."""
        pass

    @property
    def drawables(self) -> tuple[Drawable, ...]:
        """Return the fill drawable followed by outline drawables."""
        return self.fill_drawable, *self.outline_drawables

    @property
    def color(self) -> Color:
        """Color of the active fill, or the first outline when unfilled."""
        if self._fill_color is not None:
            return self.fill_drawable.color
        return self.outline_drawables[0].color

    @color.setter
    def color(self, value: Color) -> None:
        """Set the fill color."""
        self.fill_color = value

    @property
    def fill_color(self) -> Color | None:
        """Fill color, or ``None`` when no fill is configured."""
        return (
            self.fill_drawable.color
            if self._fill_color is not None
            else None
        )

    @fill_color.setter
    def fill_color(self, value: Color | None) -> None:
        """Set or disable the fill color."""
        self._fill_color = value
        self._fill_visible = value is not None
        if value is not None:
            self.fill_drawable.color = value

    @property
    def outline_color(self) -> Color | None:
        """Outline color, or ``None`` when no outline is configured."""
        return (
            self.outline_drawables[0].color
            if self._outline_color is not None
            else None
        )

    @outline_color.setter
    def outline_color(self, value: Color | None) -> None:
        """Set or disable the outline color."""
        self._outline_color = value
        self._outline_visible = value is not None
        if value is not None:
            for drawable in self.outline_drawables:
                drawable.color = value

    @property
    def outline_thickness(self) -> float:
        """Configured outline width in local pixels."""
        return self._outline_thickness

    @outline_thickness.setter
    def outline_thickness(self, value: float) -> None:
        """Set a finite, positive outline width."""
        if not isfinite(value) or value <= 0:
            raise ValueError("outline_thickness must be finite and positive")
        self._outline_thickness = value
        self._apply_outline_thickness(value)

    @property
    def fill_visible(self) -> bool:
        """Whether a configured fill is displayed."""
        return self._fill_visible

    @fill_visible.setter
    def fill_visible(self, value: bool) -> None:
        """Control display of a configured fill."""
        self._fill_visible = value

    @property
    def outline_visible(self) -> bool:
        """Whether a configured outline is displayed."""
        return self._outline_visible

    @outline_visible.setter
    def outline_visible(self, value: bool) -> None:
        """Control display of configured outlines."""
        self._outline_visible = value

    @property
    def opacity(self) -> int:
        """Opacity of the fill, or the first outline when unfilled."""
        if self._fill_color is not None:
            return self.fill_drawable.opacity
        return self.outline_drawables[0].opacity

    @opacity.setter
    def opacity(self, value: int) -> None:
        """Set opacity on the fill and all outline drawables."""
        for drawable in self.drawables:
            drawable.opacity = value

    def _sync_style(self) -> None:
        """Apply effective visibility and style flags to the drawables."""
        visible = self.effective_visible
        self.fill_drawable.visible = bool(
            visible and self._fill_visible and self._fill_color is not None
        )
        outline_visible = bool(
            visible
            and self._outline_visible
            and self._outline_color is not None
        )
        for drawable in self.outline_drawables:
            drawable.visible = outline_visible

    @abstractmethod
    def _apply_outline_thickness(self, value: float) -> None:
        """Apply an outline width to concrete shape drawables."""
        pass
