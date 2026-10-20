from __future__ import annotations

import pyglet

from .renderer import Renderer
from ..resources import Image, center_source, source_of
from ..inspector import inspector_field
from ..runtime import NullDrawable, presentation_enabled


def _drawable_source(
    image: Image | None,
) -> pyglet.image.AbstractImage:
    if image is not None:
        if not isinstance(image, Image):
            raise TypeError("SpriteRenderer accepts static Images; use SpriteAnimator for animation playback")
        texture = source_of(image).get_texture()
        region = texture.get_region(0, 0, texture.width, texture.height)
        region.anchor_x, region.anchor_y = texture.anchor_x, texture.anchor_y
        coordinates = region.tex_coords
        left, right = min(coordinates[0::3]), max(coordinates[0::3])
        bottom, top = min(coordinates[1::3]), max(coordinates[1::3])
        half_u = (right - left) / (2 * region.width)
        half_v = (top - bottom) / (2 * region.height)
        # Keep linear filtering inside the image, including atlas-backed animation frames.
        region.tex_coords = tuple(
            min(right - half_u, max(left + half_u, value)) if axis % 3 == 0
            else min(top - half_v, max(bottom + half_v, value)) if axis % 3 == 1
            else value
            for axis, value in enumerate(coordinates)
        )
        return region
    # An image-less sprite keeps a transparent 1x1 drawable so it can stay in batches.
    placeholder = pyglet.image.SolidColorImagePattern(
        (0, 0, 0, 0)
    ).create_image(1, 1)
    placeholder.anchor_x = 0
    placeholder.anchor_y = 0
    return placeholder


class SpriteRenderer(Renderer):
    """Render a static image at the owning object's world position.

    Attributes:
        image: Current static image or ``None``.
        flip_x: Whether the image is mirrored horizontally.
        flip_y: Whether the image is mirrored vertically.
        opacity: Sprite opacity.
    """
    __inspector_fields__ = {
        "image": inspector_field(None, widget="image", tooltip="Static sprite image. Use Sprite Animator for playback. May be empty."),
        "opacity": inspector_field(255, minimum=0, maximum=255, tooltip="Opacity from transparent (0) to opaque (255)."),
        "flip_x": inspector_field(False, tooltip="Mirror the sprite horizontally without changing its transform or collider."),
        "flip_y": inspector_field(False, tooltip="Mirror the sprite vertically without changing its transform or collider."),
    }
    def __init__(
        self,
        image: Image | None = None,
        *,
        batch: pyglet.graphics.Batch | None = None,
        visible: bool = True,
        screen_space: bool = False,
        flip_x: bool = False,
        flip_y: bool = False,
    ) -> None:
        """Create a renderer for a static image or an empty sprite.

        Args:
            image: Image to display, or ``None`` for no visible image.
            batch: Optional pyglet batch for the sprite.
            visible: Whether the sprite is shown.
            screen_space: Whether it uses screen coordinates.
            flip_x: Mirror the image horizontally.
            flip_y: Mirror the image vertically.
        """
        super().__init__(visible=visible, screen_space=screen_space)
        self._image = image
        self.flip_x = flip_x
        self.flip_y = flip_y
        if not presentation_enabled():
            self._drawable = NullDrawable()
            return
        if image is not None:
            center_source(image)
        self._drawable = pyglet.sprite.Sprite(
            _drawable_source(image),
            0,
            0,
            batch=batch,
        )

    @property
    def drawable(self) -> pyglet.sprite.Sprite:
        """Underlying pyglet sprite drawable."""
        return self._drawable

    @property
    def image(self) -> Image | None:
        """Image currently displayed, if any."""
        return self._image

    @image.setter
    def image(self, value: Image | None) -> None:
        """Replace the static image; ``None`` hides the sprite."""
        if isinstance(self._drawable, NullDrawable):
            self._image = value
            return
        if value is not None:
            center_source(value)
        drawable_source = _drawable_source(value)
        self._image = value
        self._drawable.image = drawable_source

    @property
    def opacity(self) -> int:
        """Sprite opacity."""
        return self._drawable.opacity

    @opacity.setter
    def opacity(self, value: int) -> None:
        """Set the sprite opacity."""
        self._drawable.opacity = value

    def sync(self) -> None:
        """Synchronize position, rotation, scale, mirroring, and visibility."""
        if self.owner is None:
            return
        x, y = self.owner.world_position
        if (self._drawable.x, self._drawable.y) != (x, y):
            self._drawable.position = x, y, self._drawable.z
        rotation = self.owner.world_rotation
        if self._drawable.rotation != rotation:
            self._drawable.rotation = rotation
        scale_x, scale_y = self.owner.world_scale
        scale_x = -scale_x if self.flip_x else scale_x
        scale_y = -scale_y if self.flip_y else scale_y
        if self._drawable.scale_x != scale_x:
            self._drawable.scale_x = scale_x
        if self._drawable.scale_y != scale_y:
            self._drawable.scale_y = scale_y
        visible = self.effective_visible and self._image is not None
        if self._drawable.visible != visible:
            self._drawable.visible = visible
