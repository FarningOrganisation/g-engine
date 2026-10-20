from __future__ import annotations

from typing import TYPE_CHECKING
from collections.abc import Mapping
from math import isfinite

import pyglet

from .renderer import Renderer
from .renderer_utils import _transform_point
from ..inspector import inspector_field
from ..runtime import NullDrawable, presentation_enabled

if TYPE_CHECKING:
    from ..tilemap import TileCell, TileLayer


class TileRenderer(Renderer):
    """Render tile patches as one transformable object without baking images.

    Attributes:
        patches: Source tile layers whose cells are rendered.
        opacity: Overall byte opacity, multiplied by each source layer opacity.
    """

    __inspector_fields__ = {
        "opacity": inspector_field(
            255, minimum=0, maximum=255,
            tooltip="Opacity applied to all tiles, combined with their source-layer opacity.",
        ),
    }

    def __init__(
        self, patches: tuple[TileLayer, ...] = (), *,
        visible: bool = True, enabled: bool = True,
        screen_space: bool = False, opacity: int = 255,
    ) -> None:
        """Create tile sprites for the supplied patches.

        Args:
            patches: Tile layers to render.
            visible: Whether the renderer is shown.
            enabled: Whether renderer behavior is enabled.
            screen_space: Whether patches use screen coordinates.
            opacity: Overall opacity from 0 through 255.

        Raises:
            ValueError: Opacity is outside its valid range.
        """
        super().__init__(visible=visible, enabled=enabled, screen_space=screen_space)
        if not 0 <= opacity <= 255:
            raise ValueError("Tile renderer opacity must be between 0 and 255")
        self.patches = patches
        self.opacity = opacity
        self._elapsed = 0.0
        self._tiles: list[tuple[TileLayer, int, int, TileCell, pyglet.sprite.Sprite, int]] = []
        self._placeholder = None
        if not presentation_enabled():
            self._placeholder = NullDrawable()
            return
        for patch in patches:
            for chunk in patch.chunks.values():
                for row in range(chunk.size):
                    for column in range(chunk.size):
                        cell = chunk.get(column, row)
                        if cell is None:
                            continue
                        x = chunk.column * chunk.size + column
                        y = chunk.row * chunk.size + row
                        tileset, local_id = patch.resolve_tile(cell.tile_id)
                        displayed = tileset.displayed_tile_id(local_id, 0)
                        sprite = pyglet.sprite.Sprite(self._image(patch, cell, displayed), subpixel=True)
                        self._tiles.append((patch, x, y, cell, sprite, displayed))
        if not self._tiles:
            self._placeholder = pyglet.sprite.Sprite(
                pyglet.image.SolidColorImagePattern((0, 0, 0, 0)).create_image(1, 1),
            )

    @staticmethod
    def _image(patch: TileLayer, cell: TileCell, displayed: int) -> pyglet.image.TextureRegion:
        tileset, _ = patch.resolve_tile(cell.tile_id)
        source = tileset.texture_region(displayed)
        image = source.get_region(0, 0, source.width, source.height)
        image.anchor_x = image.anchor_y = 0
        image.tex_coords = patch._texture_coordinates(source.tex_coords, cell.flags)
        return image

    @property
    def drawable(self) -> pyglet.sprite.Sprite:
        """First tile sprite, or the placeholder when the patch set is empty."""
        if self._tiles:
            return self._tiles[0][4]
        if self._placeholder is None:
            raise RuntimeError("Tile renderer drawable has been deleted")
        return self._placeholder

    @property
    def drawables(self) -> tuple[pyglet.sprite.Sprite, ...]:
        """All tile sprites, or the placeholder when there are no tiles."""
        return tuple(item[4] for item in self._tiles) or (self.drawable,)

    def bind_batch(
        self,
        batch: pyglet.graphics.Batch | None,
        group: pyglet.graphics.Group | None,
    ) -> None:
        """Bind tile sprites to a batch, or unbind them when detached."""
        if batch is None:
            for _, _, _, _, sprite, _ in self._tiles:
                sprite.batch = None
                sprite.group = None
            if self._placeholder is not None:
                self._placeholder.batch = None
                self._placeholder.group = None
            return
        groups = {id(patch): pyglet.graphics.Group(order=i, parent=group)
                  for i, patch in enumerate(self.patches)}
        for patch, _, _, _, sprite, _ in self._tiles:
            sprite.batch = batch
            sprite.group = groups[id(patch)]
        if self._placeholder is not None:
            self._placeholder.batch = batch
            self._placeholder.group = group

    def update(self, delta_time: float) -> None:
        """Advance source tile animations and replace changed frame images."""
        self._elapsed += delta_time
        for index, (patch, x, y, cell, sprite, previous) in enumerate(self._tiles):
            tileset, local_id = patch.resolve_tile(cell.tile_id)
            displayed = tileset.displayed_tile_id(local_id, self._elapsed)
            if displayed != previous:
                sprite.image = self._image(patch, cell, displayed)
                self._tiles[index] = patch, x, y, cell, sprite, displayed

    def save_state(self) -> Mapping[str, object]:
        """Return animation elapsed time for a runtime checkpoint."""
        return {"elapsed": self._elapsed}

    def load_state(self, state: Mapping[str, object]) -> None:
        """Restore validated animation elapsed time and refresh tile frames."""
        value = state.get("elapsed")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value) or value < 0:
            raise ValueError("Invalid TileRenderer checkpoint elapsed time")
        self._elapsed = float(value)
        self.update(0)

    def world_quads(self) -> tuple[tuple[tuple[float, float], ...], ...]:
        """Return the four transformed world-space corners of every tile."""
        if self.owner is None:
            return ()
        return tuple(
            tuple(_transform_point(self.owner, point) for point in (
                (left, bottom), (left + patch.cell_width, bottom),
                (left + patch.cell_width, bottom + patch.cell_height),
                (left, bottom + patch.cell_height),
            ))
            for patch, x, y, _, _, _ in self._tiles
            for left, bottom in (patch.cell_origin(x, y),)
        )

    def sync(self) -> None:
        """Synchronize tile positions, transforms, opacity, and visibility."""
        if self.owner is None:
            return
        sx, sy = self.owner.world_scale
        for patch, x, y, _, sprite, _ in self._tiles:
            position = _transform_point(self.owner, patch.cell_origin(x, y))
            if (sprite.x, sprite.y) != position:
                sprite.position = *position, sprite.z
            if sprite.rotation != self.owner.world_rotation:
                sprite.rotation = self.owner.world_rotation
            image = sprite.image
            if not isinstance(image, pyglet.image.TextureRegion):
                raise TypeError("Tile renderer sprites must use static tile regions")
            scale_x = sx * patch.cell_width / image.width
            scale_y = sy * patch.cell_height / image.height
            if sprite.scale_x != scale_x:
                sprite.scale_x = scale_x
            if sprite.scale_y != scale_y:
                sprite.scale_y = scale_y
            opacity = round(self.opacity * patch.opacity)
            if sprite.opacity != opacity:
                sprite.opacity = opacity
            visible = self.effective_visible and patch.visible and patch.enabled
            if sprite.visible != visible:
                sprite.visible = visible
