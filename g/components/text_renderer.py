from __future__ import annotations

import pyglet

from .renderer import Renderer
from ..resources import Color, Font
from ..inspector import inspector_field
from ..runtime import NullDrawable, presentation_enabled

class TextRenderer(Renderer):
    """Render centered text using a project font or pyglet's default font.

    Attributes:
        text: Displayed text.
        font: Project font, or ``None`` for pyglet's default font.
        font_size: Font size in points before transform scaling.
        color: Text color.
        opacity: Text opacity.
    """
    __inspector_fields__ = {
        "text": inspector_field("Text", tooltip="Text displayed by this renderer."),
        "font": inspector_field(None, widget="font", tooltip="Project font asset, or null for the default font."),
        "font_size": inspector_field(24.0, minimum=0.001, tooltip="Font size in points."),
        "color": inspector_field([255, 255, 255, 255], widget="color", tooltip="Text color."),
        "opacity": inspector_field(255, minimum=0, maximum=255, tooltip="Opacity from transparent (0) to opaque (255)."),
    }
    def __init__(
        self,
        text: str,
        font: Font | None = None,
        font_size: float = 12,
        color: Color = (255, 255, 255),
        *,
        batch: pyglet.graphics.Batch | None = None,
        visible: bool = True,
        screen_space: bool = False,
    ) -> None:
        """Create a text renderer.

        Args:
            text: Text content.
            font: Project font asset, or ``None`` for the default.
            font_size: Positive font size in points.
            color: Text color.
            batch: Optional pyglet batch for the label.
            visible: Whether the text is shown.
            screen_space: Whether the text uses screen coordinates.

        Raises:
            ValueError: ``font_size`` is not positive.
        """
        super().__init__(visible=visible, screen_space=screen_space)
        if font_size <= 0:
            raise ValueError("font_size must be positive")
        self._font = font
        self._font_size = font_size
        if not presentation_enabled():
            self._drawable = NullDrawable()
            self._drawable.text = text
            self._drawable.font_name = (
                None if font is None else font.family
            )
            self._drawable.font_size = font_size
            self._drawable.color = color
            return
        self._drawable = pyglet.text.Label(
            text,
            0,
            0,
            anchor_x="center",
            anchor_y="center",
            font_name=None if font is None else font.family,
            font_size=font_size,
            color=color,
            batch=batch,
        )

    @property
    def drawable(self) -> pyglet.text.Label:
        """Underlying pyglet text label."""
        return self._drawable

    @property
    def text(self) -> str:
        """Current text content."""
        return self._drawable.text

    @text.setter
    def text(self, value: str) -> None:
        """Replace the text content."""
        self._drawable.text = value

    @property
    def font(self) -> Font | None:
        """Project font asset, or ``None`` for the default font."""
        return self._font

    @font.setter
    def font(self, value: Font | None) -> None:
        """Set the project font or restore the default font."""
        self._font = value
        self._drawable.font_name = (
            pyglet.font.load().name if value is None else value.family
        )

    @property
    def font_size(self) -> float:
        """Configured font size in points before owner scaling."""
        return self._font_size

    @font_size.setter
    def font_size(self, value: float) -> None:
        """Set a positive font size in points."""
        if value <= 0:
            raise ValueError("font_size must be positive")
        self._font_size = value
        self._drawable.font_size = value

    @property
    def color(self) -> Color:
        """Text color."""
        return self._drawable.color

    @color.setter
    def color(self, value: Color) -> None:
        """Set text color, adding full opacity to three-channel colors."""
        self._drawable.color = value if len(value) == 4 else (*value, 255)

    @property
    def opacity(self) -> int:
        """Text opacity."""
        return self._drawable.opacity

    @opacity.setter
    def opacity(self, value: int) -> None:
        """Set text opacity."""
        self._drawable.opacity = value

    def sync(self) -> None:
        """Synchronize position, rotation, scaled font size, and visibility."""
        if self.owner is None:
            return
        self._drawable.x, self._drawable.y = self.owner.world_position
        self._drawable.rotation = self.owner.world_rotation
        scale_x, scale_y = self.owner.world_scale
        scaled_size = self._font_size * max(abs(scale_x), abs(scale_y))
        if self._drawable.font_size != scaled_size:
            self._drawable.font_size = scaled_size
        self._drawable.visible = self.effective_visible
