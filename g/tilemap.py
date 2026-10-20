from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntFlag
from math import floor, isfinite
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

import pyglet
from pyglet import gl
from pyglet.sprite import SpriteGroup
from .runtime import NullBatch, presentation_enabled

from .resources import Color, Image, source_of
from .scene import SceneLayer

if TYPE_CHECKING:
    from .camera import Camera
    from pyglet.graphics.vertexdomain import IndexedVertexList


TileProperty: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | list["TileProperty"]
    | dict[str, "TileProperty"]
)


class _ChunkVisibilityGroup(pyglet.graphics.Group):
    # Chunk visibility must not be consolidated with another chunk's draw group.
    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)


class TileFlags(IntFlag):
    """Texture transforms applied when rendering a tile cell.

    Attributes:
        NONE: Render without a transform.
        FLIP_HORIZONTAL: Mirror the tile horizontally.
        FLIP_VERTICAL: Mirror the tile vertically.
        FLIP_DIAGONAL: Swap the tile's diagonal texture coordinates.
    """

    NONE = 0
    FLIP_HORIZONTAL = 1
    FLIP_VERTICAL = 2
    FLIP_DIAGONAL = 4


_TILE_ID_BITS = 29
_TILE_ID_MASK = (1 << _TILE_ID_BITS) - 1
_FLAGS_SHIFT = _TILE_ID_BITS


@dataclass(frozen=True)
class TileCell:
    """Tile-set global ID and rendering transforms stored in a cell.

    Attributes:
        tile_id: Non-negative global tile ID.
        flags: Bitwise combination of :class:`TileFlags`.

    Raises:
        ValueError: If ``tile_id`` is outside the supported non-negative range.
    """

    tile_id: int
    flags: TileFlags = TileFlags.NONE

    def __post_init__(self) -> None:
        if self.tile_id < 0 or self.tile_id >= _TILE_ID_MASK:
            raise ValueError("tile ID is out of range")


@dataclass(frozen=True)
class TileContact:
    """A collider contact with one tile cell.

    Attributes:
        layer: Tile layer containing the cell.
        column: Cell column.
        row: Cell row.
        cell: Tile cell involved in the contact.
        point: Contact point in world coordinates.
        normal: Contact normal in world coordinates.
    """

    layer: TileLayer
    column: int
    row: int
    cell: TileCell
    point: tuple[float, float] = field(compare=False, hash=False)
    normal: tuple[float, float] = field(compare=False, hash=False)


def encode_tile_cell(cell: TileCell | None) -> int:
    if cell is None:
        return 0
    return (int(cell.flags) << _FLAGS_SHIFT) | (cell.tile_id + 1)


def decode_tile_cell(value: int) -> TileCell | None:
    if value == 0:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("encoded tile cell must be a non-negative integer")
    tile_id = (value & _TILE_ID_MASK) - 1
    raw_flags = value >> _FLAGS_SHIFT
    if raw_flags & ~int(
        TileFlags.FLIP_HORIZONTAL
        | TileFlags.FLIP_VERTICAL
        | TileFlags.FLIP_DIAGONAL
    ):
        raise ValueError("encoded tile cell contains unknown flags")
    flags = TileFlags(raw_flags)
    return TileCell(tile_id, flags)


@dataclass(frozen=True)
class TileCollision:
    """Collision geometry and alpha-generation metadata for a tile.

    Attributes:
        x: Horizontal collision offset in pixels.
        y: Vertical collision offset in pixels.
        width: Optional collision width in pixels; ``None`` uses cell width.
        height: Optional collision height in pixels; ``None`` uses cell height.
        shape: Either ``"rectangle"`` or ``"polygon"``.
        polygons: Normalized polygon vertices, used for polygon shapes.
        generated_from_alpha: Whether geometry was generated from image alpha.
        alpha_threshold: Alpha cutoff recorded for generated collision data.
    """

    x: float = 0
    y: float = 0
    width: float | None = None
    height: float | None = None
    shape: Literal["rectangle", "polygon"] = "rectangle"
    polygons: tuple[
        tuple[tuple[float, float], ...],
        ...,
    ] = ()
    generated_from_alpha: bool = False
    alpha_threshold: int = 255

    def __post_init__(self) -> None:
        """Validate shape parameters and normalized polygon coordinates.

        Raises:
            ValueError: If the shape or any supplied geometry is invalid.
        """
        if self.shape not in {"rectangle", "polygon"}:
            raise ValueError("unknown tile collision shape")
        for name, value in (("x", self.x), ("y", self.y)):
            if not isfinite(value):
                raise ValueError(
                    f"tile collision {name} must be finite"
                )
        for name, value in (("width", self.width), ("height", self.height)):
            if value is not None and (
                not isfinite(value) or value <= 0
            ):
                raise ValueError(f"tile collision {name} must be positive")
        if self.shape == "polygon":
            if not self.polygons:
                raise ValueError(
                    "polygon tile collision must contain polygons"
                )
            for polygon in self.polygons:
                if len(polygon) < 3:
                    raise ValueError(
                        "tile collision polygon needs at least three points"
                    )
                for x, y in polygon:
                    if (
                        not isfinite(x)
                        or not isfinite(y)
                        or not 0 <= x <= 1
                        or not 0 <= y <= 1
                    ):
                        raise ValueError(
                            "tile collision polygon points must be "
                            "normalized"
                        )
        elif self.polygons:
            raise ValueError(
                "rectangle tile collision cannot contain polygons"
            )
        if (
            not isinstance(self.alpha_threshold, int)
            or isinstance(self.alpha_threshold, bool)
            or not 0 <= self.alpha_threshold <= 255
        ):
            raise ValueError(
                "tile collision alpha threshold must be between 0 and 255"
            )

    @classmethod
    def polygon(
        cls,
        points: Sequence[tuple[float, float]],
        *,
        generated_from_alpha: bool = False,
        alpha_threshold: int = 255,
    ) -> TileCollision:
        """Build polygon collision from normalized points.

        Args:
            points: Polygon vertices in normalized tile coordinates.
            generated_from_alpha: Whether these points were alpha-generated.
            alpha_threshold: Alpha cutoff associated with generated points.

        Returns:
            A polygon collision definition.

        Raises:
            ValueError: If points or alpha threshold are invalid.
        """
        return cls(
            shape="polygon",
            polygons=(tuple(points),),
            generated_from_alpha=generated_from_alpha,
            alpha_threshold=alpha_threshold,
        )

    @property
    def is_full_rectangle(self) -> bool:
        """Whether this is the unmodified full-cell rectangle."""
        return (
            self.shape == "rectangle"
            and self.x == 0
            and self.y == 0
            and self.width is None
            and self.height is None
        )


@dataclass(frozen=True)
class TileNavigation:
    """Walkability and traversal cost for a tile definition.

    Attributes:
        walkable: Whether navigation may traverse the tile.
        cost: Positive traversal cost.
    """

    walkable: bool = True
    cost: float = 1

    def __post_init__(self) -> None:
        """Validate that traversal cost is positive and finite.

        Raises:
            ValueError: If ``cost`` is not positive and finite.
        """
        if not isfinite(self.cost) or self.cost <= 0:
            raise ValueError("tile navigation cost must be positive")


@dataclass(frozen=True)
class TileAnimationFrame:
    """One tile and its duration in a tile animation.

    Attributes:
        tile_id: Non-negative local tile ID to display.
        duration: Positive duration in seconds.
    """

    tile_id: int
    duration: float

    def __post_init__(self) -> None:
        """Validate tile ID and frame duration.

        Raises:
            ValueError: If the ID is negative or duration is not positive.
        """
        if self.tile_id < 0:
            raise ValueError("animation tile ID must be non-negative")
        if not isfinite(self.duration) or self.duration <= 0:
            raise ValueError("animation frame duration must be positive")


@dataclass(frozen=True)
class TileAnimation:
    """Looping sequence of tile animation frames.

    Attributes:
        frames: Non-empty frame sequence in playback order.
    """

    frames: tuple[TileAnimationFrame, ...]

    def __post_init__(self) -> None:
        """Ensure the animation contains at least one frame.

        Raises:
            ValueError: If ``frames`` is empty.
        """
        if not self.frames:
            raise ValueError("tile animation must contain frames")

    def tile_id_at(self, elapsed: float) -> int:
        """Return the frame's tile ID at elapsed seconds, looping by duration."""
        total = sum(frame.duration for frame in self.frames)
        position = elapsed % total
        for frame in self.frames:
            if position < frame.duration:
                return frame.tile_id
            position -= frame.duration
        return self.frames[-1].tile_id


@dataclass(frozen=True)
class TileDefinition:
    """Metadata associated with one local tile ID.

    Attributes:
        tile_id: Non-negative local ID within its tile set.
        name: Optional display name.
        collision: Optional collision geometry.
        navigation: Optional walkability and cost metadata.
        animation: Optional looping tile animation.
        tags: Tile tags.
        properties: Additional JSON-compatible tile properties.
    """

    tile_id: int
    name: str | None = None
    collision: TileCollision | None = None
    navigation: TileNavigation | None = None
    animation: TileAnimation | None = None
    tags: frozenset[str] = frozenset()
    properties: dict[str, TileProperty] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the tile ID and optional name.

        Raises:
            ValueError: If the ID is negative or the name is empty.
        """
        if self.tile_id < 0:
            raise ValueError("tile ID must be non-negative")
        if self.name == "":
            raise ValueError("tile name must not be empty")


class TileSet:
    """Regular image atlas divided into a grid of tile regions.

    Attributes:
        image: Atlas image.
        tile_width: Tile width in pixels.
        tile_height: Tile height in pixels.
        columns: Number of atlas columns used.
        rows: Number of atlas rows.
        margin: Atlas edge margin in pixels.
        spacing: Gap between tiles in pixels.
        properties: Additional tile-set properties.
    """

    def __init__(
        self,
        image: Image,
        tile_width: int,
        tile_height: int | None = None,
        *,
        columns: int | None = None,
        margin: int = 0,
        spacing: int = 0,
        properties: dict[str, TileProperty] | None = None,
    ) -> None:
        """Create a grid tile set from an image atlas.

        Args:
            image: Image containing the tile atlas.
            tile_width: Positive tile width in pixels.
            tile_height: Tile height; defaults to ``tile_width``.
            columns: Optional number of used columns, not exceeding the atlas.
            margin: Non-negative edge margin.
            spacing: Non-negative gap between atlas tiles.
            properties: Optional tile-set properties.

        Raises:
            ValueError: If dimensions, margin, spacing, or grid are invalid.
        """
        tile_height = tile_width if tile_height is None else tile_height
        if tile_width <= 0 or tile_height <= 0:
            raise ValueError("tile dimensions must be positive")
        if margin < 0 or spacing < 0:
            raise ValueError("tile margin and spacing must be non-negative")
        available_width = image.width - margin * 2
        available_height = image.height - margin * 2
        calculated_columns = (available_width + spacing) // (
            tile_width + spacing
        )
        rows = (available_height + spacing) // (tile_height + spacing)
        self.columns = calculated_columns if columns is None else columns
        if (
            self.columns <= 0
            or self.columns > calculated_columns
            or rows <= 0
        ):
            raise ValueError("tileset grid does not fit its image")
        self.image = image
        self.tile_width = tile_width
        self.tile_height = tile_height
        self.margin = margin
        self.spacing = spacing
        self.rows = rows
        self.properties = dict(properties or {})
        self._definitions: dict[int, TileDefinition] = {}
        self._regions: dict[int, Any] = {}
        self._collision_revision = 0
        self._render_revision = 0

    @property
    def tile_count(self) -> int:
        """Number of tile IDs addressable in the atlas."""
        return self.columns * self.rows

    @property
    def definitions(self) -> tuple[TileDefinition, ...]:
        """Defined tile metadata sorted by local tile ID."""
        return tuple(
            self._definitions[tile_id]
            for tile_id in sorted(self._definitions)
        )

    @property
    def has_animations(self) -> bool:
        """Whether any defined tile has an animation."""
        return any(
            definition.animation is not None
            for definition in self._definitions.values()
        )

    def define(
        self,
        tile_id: int,
        *,
        name: str | None = None,
        collision: TileCollision | None = None,
        navigation: TileNavigation | None = None,
        animation: TileAnimation | None = None,
        tags: set[str] | frozenset[str] = frozenset(),
        properties: dict[str, TileProperty] | None = None,
    ) -> TileDefinition:
        """Define or replace metadata for a local tile ID.

        Args:
            tile_id: Tile ID within this atlas.
            name: Optional display name.
            collision: Optional collision definition.
            navigation: Optional navigation definition.
            animation: Optional animation using local tile IDs.
            tags: Tile tags.
            properties: Additional tile properties.

        Returns:
            The stored definition.

        Raises:
            ValueError: If the ID or any animation frame ID is outside the
                atlas.
        """
        self._validate_tile_id(tile_id)
        if animation is not None:
            for frame in animation.frames:
                self._validate_tile_id(frame.tile_id)
        definition = TileDefinition(
            tile_id,
            name,
            collision,
            navigation,
            animation,
            frozenset(tags),
            dict(properties or {}),
        )
        self._definitions[tile_id] = definition
        self._collision_revision += 1
        return definition

    def definition(self, tile_id: int) -> TileDefinition:
        """Return defined metadata or a default definition for the ID.

        Args:
            tile_id: Local tile ID.

        Raises:
            ValueError: If ``tile_id`` is outside the atlas.
        """
        self._validate_tile_id(tile_id)
        return self._definitions.get(tile_id, TileDefinition(tile_id))

    def displayed_tile_id(self, tile_id: int, elapsed: float) -> int:
        """Return the animation frame ID or the original ID if static.

        Args:
            tile_id: Local tile ID.
            elapsed: Animation time in seconds.
        """
        animation = self.definition(tile_id).animation
        return tile_id if animation is None else animation.tile_id_at(elapsed)

    def texture_region(self, tile_id: int) -> Any:
        """Return a cached pyglet texture region for a local tile ID.

        Raises:
            ValueError: If ``tile_id`` is outside the atlas.
        """
        self._validate_tile_id(tile_id)
        region = self._regions.get(tile_id)
        if region is not None:
            return region
        column = tile_id % self.columns
        row = tile_id // self.columns
        x = self.margin + column * (self.tile_width + self.spacing)
        y = (
            self.image.height
            - self.margin
            - self.tile_height
            - row * (self.tile_height + self.spacing)
        )
        texture = source_of(self.image).get_texture()
        region = texture.get_region(
            x,
            y,
            self.tile_width,
            self.tile_height,
        )
        # Keep linear filtering inside this cell, not neighboring atlas pixels.
        coordinates = region.tex_coords
        half_u = 0.5 / texture.width
        half_v = 0.5 / texture.height
        left = coordinates[0] + half_u
        bottom = coordinates[1] + half_v
        right = coordinates[3] - half_u
        top = coordinates[7] - half_v
        region.tex_coords = (
            left, bottom, coordinates[2],
            right, bottom, coordinates[5],
            right, top, coordinates[8],
            left, top, coordinates[11],
        )
        self._regions[tile_id] = region
        return region

    def _validate_tile_id(self, tile_id: int) -> None:
        if (
            not isinstance(tile_id, int)
            or isinstance(tile_id, bool)
            or not 0 <= tile_id < self.tile_count
        ):
            raise ValueError(
                f"tile ID must be between 0 and {self.tile_count - 1}"
            )


class ColorTileSet(TileSet):
    """Tile set whose cells are solid colors rather than source artwork.

    Attributes:
        colors: Current tile colors as RGBA byte tuples.
    """

    def __init__(
        self,
        colors: Sequence[Color],
        *,
        properties: dict[str, TileProperty] | None = None,
    ) -> None:
        """Create one tile per supplied RGB or RGBA color.

        Args:
            colors: Non-empty sequence of three- or four-channel byte colors.
            properties: Optional tile-set properties.

        Raises:
            ValueError: If no colors are supplied or a color is invalid.
        """
        if not colors:
            raise ValueError("color tileset must contain at least one color")
        self._colors = [self._normalize_color(color) for color in colors]
        super().__init__(
            self._create_image(self._colors),
            1,
            1,
            columns=len(self._colors),
            margin=1,
            spacing=2,
            properties=properties,
        )

    @property
    def colors(self) -> tuple[tuple[int, int, int, int], ...]:
        """Colors as immutable RGBA byte tuples."""
        return tuple(self._colors)

    def set_color(self, tile_id: int, color: Color) -> None:
        """Replace a tile's color and invalidate its rendered texture.

        Args:
            tile_id: Local tile ID.
            color: Three-channel RGB or four-channel RGBA byte color.

        Raises:
            ValueError: If the tile ID or color is invalid.
        """
        self._validate_tile_id(tile_id)
        resolved = self._normalize_color(color)
        if self._colors[tile_id] == resolved:
            return
        self._colors[tile_id] = resolved
        self.image = self._create_image(self._colors)
        self._regions.clear()
        self._render_revision += 1

    @staticmethod
    def _normalize_color(color: Color) -> tuple[int, int, int, int]:
        if len(color) not in {3, 4} or any(
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or not 0 <= channel <= 255
            for channel in color
        ):
            raise ValueError(
                "tile color must contain three or four channels from 0 to 255"
            )
        return color if len(color) == 4 else (*color, 255)

    @staticmethod
    def _create_image(colors: Sequence[tuple[int, int, int, int]]) -> Image:
        pixels = b"".join(
            bytes(color)
            for _ in range(3)
            for color in colors
            for _ in range(3)
        )
        source = pyglet.image.ImageData(
            len(colors) * 3,
            3,
            "RGBA",
            pixels,
        )
        return Image._from_source(source)


class ImageCollectionTileSet(TileSet):
    """Tile set containing separately supplied images in a generated atlas.

    Attributes:
        images: Source images in tile-ID order.
    """

    def __init__(
        self,
        images: Sequence[Image],
        *,
        properties: dict[str, TileProperty] | None = None,
    ) -> None:
        """Create a tile set from a non-empty sequence of images.

        Args:
            images: Source images; differing sizes are centered in the atlas.
            properties: Optional tile-set properties.

        Raises:
            ValueError: If no images are supplied.
        """
        if not images:
            raise ValueError(
                "image collection tileset must contain at least one image"
            )
        self._images = list(images)
        tile_width = max(image.width for image in self._images)
        tile_height = max(image.height for image in self._images)
        super().__init__(
            self._create_image(
                self._images,
                tile_width,
                tile_height,
            ),
            tile_width,
            tile_height,
            columns=len(self._images),
            margin=1,
            spacing=2,
            properties=properties,
        )

    @property
    def images(self) -> tuple[Image, ...]:
        """Source images in local tile-ID order."""
        return tuple(self._images)

    @staticmethod
    def _create_image(
        images: Sequence[Image],
        tile_width: int,
        tile_height: int,
    ) -> Image:
        atlas_width = (
            2 + len(images) * tile_width + (len(images) - 1) * 2
        )
        atlas_height = tile_height + 2
        pixels = bytearray(atlas_width * atlas_height * 4)
        for index, image in enumerate(images):
            x = 1 + index * (tile_width + 2)
            y = 1
            x += (tile_width - image.width) // 2
            y += (tile_height - image.height) // 2
            image_data = source_of(image).get_image_data()
            source = image_data.get_data("RGBA", image.width * 4)
            for row in range(image.height):
                source_start = row * image.width * 4
                target_start = (
                    (y + row) * atlas_width + x
                ) * 4
                pixels[
                    target_start : target_start + image.width * 4
                ] = source[
                    source_start : source_start + image.width * 4
                ]
        source = pyglet.image.ImageData(
            atlas_width,
            atlas_height,
            "RGBA",
            bytes(pixels),
        )
        return Image._from_source(source)


@dataclass(frozen=True)
class TileSetBinding:
    """Map a tile-set local ID range into a layer's global IDs.

    Attributes:
        identifier: Unique binding identifier within the layer.
        name: Display name.
        first_gid: First global tile ID in the range.
        tileset: Bound tile set.
    """

    identifier: str
    name: str
    first_gid: int
    tileset: TileSet

    def __post_init__(self) -> None:
        """Validate binding names and global ID range.

        Raises:
            ValueError: If an identifier or name is empty or the ID range is
                invalid.
        """
        if not self.identifier:
            raise ValueError("tileset identifier must not be empty")
        if not self.name:
            raise ValueError("tileset name must not be empty")
        if self.first_gid < 0:
            raise ValueError("tileset first global ID must be non-negative")
        TileCell(self.last_gid)

    @property
    def last_gid(self) -> int:
        """Last global tile ID covered by the binding."""
        return self.first_gid + self.tileset.tile_count - 1

    def contains(self, tile_id: int) -> bool:
        """Return whether ``tile_id`` falls in this binding's inclusive range.

        Args:
            tile_id: Global tile ID.
        """
        return self.first_gid <= tile_id <= self.last_gid


@dataclass
class TileChunk:
    """Fixed-size sparse tile storage and render data for one chunk.

    Attributes:
        column: Chunk column in the layer.
        row: Chunk row in the layer.
        size: Number of cells along each chunk edge.
        cells: Encoded cells in row-major order.
        dirty: Whether the chunk's render data needs rebuilding.
    """

    column: int
    row: int
    size: int
    cells: list[int] = field(init=False)
    dirty: bool = True
    _vertex_list: IndexedVertexList | None = None
    _group: pyglet.graphics.Group | None = None
    _vertex_lists: list[IndexedVertexList] = field(default_factory=list)
    _groups: list[pyglet.graphics.Group] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Allocate empty row-major cell storage."""
        self.cells = [0] * (self.size * self.size)

    @property
    def empty(self) -> bool:
        """Whether every cell is empty."""
        return not any(self.cells)

    def get(self, local_column: int, local_row: int) -> TileCell | None:
        """Return a local cell or ``None`` if it is empty.

        Args:
            local_column: Column within the chunk.
            local_row: Row within the chunk.
        """
        return decode_tile_cell(
            self.cells[local_row * self.size + local_column]
        )

    def set(
        self,
        local_column: int,
        local_row: int,
        cell: TileCell | None,
    ) -> None:
        """Set a local cell and mark the chunk dirty when it changes.

        Args:
            local_column: Column within the chunk.
            local_row: Row within the chunk.
            cell: Replacement cell, or ``None`` to clear it.
        """
        index = local_row * self.size + local_column
        encoded = encode_tile_cell(cell)
        if self.cells[index] != encoded:
            self.cells[index] = encoded
            self.dirty = True

    def delete(self) -> None:
        """Delete vertex lists and clear this chunk's render references."""
        for vertex_list in self._vertex_lists:
            vertex_list.delete()
        if (
            self._vertex_list is not None
            and self._vertex_list not in self._vertex_lists
        ):
            self._vertex_list.delete()
        self._vertex_lists.clear()
        self._groups.clear()
        self._vertex_list = None
        self._group = None


class TileLayer(SceneLayer):
    """Chunked tile scene layer with optional collision and navigation data.

    Attributes:
        cell_width: Cell width in pixels.
        cell_height: Cell height in pixels.
        chunk_size: Number of rows and columns in each chunk.
        offset: World-space lower-left origin of cell ``(0, 0)``.
        collision_enabled: Whether tile collision definitions are active.
        navigation_enabled: Whether tile navigation definitions are active.
        properties: Additional layer properties.
        chunks: Non-empty chunks keyed by chunk coordinates.
    """

    def __init__(
        self,
        scene: Any,
        name: str,
        tileset: TileSet | None = None,
        *,
        identifier: str | None = None,
        visible: bool = True,
        enabled: bool = True,
        cell_width: int | None = None,
        cell_height: int | None = None,
        chunk_size: int = 16,
        opacity: float = 1,
        offset: tuple[float, float] = (0, 0),
        collision_enabled: bool = False,
        navigation_enabled: bool = False,
        properties: dict[str, TileProperty] | None = None,
    ) -> None:
        """Create a tile layer.

        Args:
            scene: Owning scene.
            name: Layer name.
            tileset: Optional initial tile set bound at global ID zero.
            identifier: Optional layer identifier.
            visible: Initial visibility.
            enabled: Whether layer data participates in scene systems.
            cell_width: Cell width; defaults to the tile-set width or 32.
            cell_height: Cell height; defaults to the tile-set height or 32.
            chunk_size: Positive chunk edge length in cells.
            opacity: Layer opacity between zero and one.
            offset: World-space origin offset.
            collision_enabled: Whether tile collision is enabled.
            navigation_enabled: Whether tile navigation is enabled.
            properties: Additional layer properties.

        Raises:
            ValueError: If chunk/cell dimensions are invalid or opacity is
                outside ``[0, 1]``.
        """
        super().__init__(
            scene,
            name,
            identifier=identifier,
            visible=visible,
            enabled=enabled,
        )
        if chunk_size <= 0:
            raise ValueError("tile chunk size must be positive")
        self._tilesets: list[TileSetBinding] = []
        resolved_width = (
            tileset.tile_width
            if cell_width is None and tileset is not None
            else cell_width or 32
        )
        resolved_height = (
            tileset.tile_height
            if cell_height is None and tileset is not None
            else cell_height or 32
        )
        if resolved_width <= 0 or resolved_height <= 0:
            raise ValueError("tile cell dimensions must be positive")
        self.cell_width = resolved_width
        self.cell_height = resolved_height
        self.chunk_size = chunk_size
        self.offset = (float(offset[0]), float(offset[1]))
        self.collision_enabled = collision_enabled
        self.navigation_enabled = navigation_enabled
        self.properties = dict(properties or {})
        self.chunks: dict[tuple[int, int], TileChunk] = {}
        self._collision_revision = 0
        self._presentation_enabled = presentation_enabled()
        self._batch = (
            pyglet.graphics.Batch()
            if self._presentation_enabled else NullBatch()
        )
        self._elapsed = 0.0
        self._animation_state: tuple[int, ...] = ()
        self._render_state: tuple[tuple[int, int], ...] = ()
        self.opacity = opacity
        if tileset is not None:
            self.add_tileset(
                tileset,
                identifier="default",
                name="Tileset",
                first_gid=0,
            )

    @property
    def opacity(self) -> float:
        """Layer opacity in the inclusive range zero to one."""
        return self._opacity

    @opacity.setter
    def opacity(self, value: float) -> None:
        """Set opacity and invalidate chunk rendering when it changes.

        Args:
            value: Opacity from zero through one.

        Raises:
            ValueError: If ``value`` is non-finite or outside ``[0, 1]``.
        """
        if not isfinite(value) or not 0 <= value <= 1:
            raise ValueError("tile layer opacity must be between 0 and 1")
        changed = getattr(self, "_opacity", None) != value
        self._opacity = value
        if changed:
            self._mark_all_dirty()

    @property
    def empty(self) -> bool:
        """Whether the layer contains no allocated non-empty chunks."""
        return not self.chunks

    @property
    def tilesets(self) -> tuple[TileSetBinding, ...]:
        """Tile-set bindings ordered by their first global ID."""
        return tuple(self._tilesets)

    @property
    def tileset(self) -> TileSet | None:
        """First bound tile set, or ``None`` when no tile set is bound."""
        return (
            self._tilesets[0].tileset
            if self._tilesets
            else None
        )

    @tileset.setter
    def tileset(self, value: TileSet | None) -> None:
        """Replace all bindings with the supplied default binding.

        Args:
            value: Replacement tile set, or ``None`` to clear it.

        Raises:
            ValueError: If clearing a non-empty layer or if existing cells do
                not fit the replacement set.
        """
        self.set_tileset(value)

    def set_tile(
        self,
        column: int,
        row: int,
        tile_id: int | None,
        *,
        flags: TileFlags = TileFlags.NONE,
    ) -> None:
        """Set or erase a tile at global cell coordinates.

        Args:
            column: Cell column.
            row: Cell row.
            tile_id: Global tile ID, or ``None`` to erase the cell.
            flags: Optional texture transforms.

        Raises:
            RuntimeError: If setting a tile without a bound tile set.
            ValueError: If the global tile ID cannot be resolved.
        """
        if tile_id is not None:
            if not self._tilesets:
                raise RuntimeError("tile layer has no tileset")
            tileset, local_id = self.resolve_tile(tile_id)
            tileset.definition(local_id)
        chunk_column, local_column = divmod(column, self.chunk_size)
        chunk_row, local_row = divmod(row, self.chunk_size)
        key = (chunk_column, chunk_row)
        chunk = self.chunks.get(key)
        if chunk is None:
            if tile_id is None:
                return
            chunk = TileChunk(chunk_column, chunk_row, self.chunk_size)
            self.chunks[key] = chunk
        previous = chunk.get(local_column, local_row)
        updated = None if tile_id is None else TileCell(tile_id, flags)
        if previous == updated:
            return
        chunk.set(
            local_column,
            local_row,
            updated,
        )
        self._collision_revision += 1
        if chunk.empty:
            chunk.delete()
            del self.chunks[key]

    def tile_at(self, column: int, row: int) -> TileCell | None:
        """Return a cell by global column and row, or ``None`` if empty.

        Args:
            column: Cell column.
            row: Cell row.
        """
        chunk_column, local_column = divmod(column, self.chunk_size)
        chunk_row, local_row = divmod(row, self.chunk_size)
        chunk = self.chunks.get((chunk_column, chunk_row))
        if chunk is None:
            return None
        return chunk.get(local_column, local_row)

    def definition_at(
        self,
        column: int,
        row: int,
    ) -> TileDefinition | None:
        """Return metadata for a cell, or ``None`` when it is empty/unbound.

        Args:
            column: Cell column.
            row: Cell row.
        """
        cell = self.tile_at(column, row)
        if cell is None or not self._tilesets:
            return None
        tileset, local_id = self.resolve_tile(cell.tile_id)
        return tileset.definition(local_id)

    def world_to_cell(self, x: float, y: float) -> tuple[int, int]:
        """Map world coordinates to the containing cell, accounting for offset.

        Args:
            x: Horizontal world coordinate.
            y: Vertical world coordinate.
        """
        return (
            floor((x - self.offset[0]) / self.cell_width),
            floor((y - self.offset[1]) / self.cell_height),
        )

    def cell_origin(self, column: int, row: int) -> tuple[float, float]:
        """Return the world-space lower-left corner of a cell.

        Args:
            column: Cell column.
            row: Cell row.
        """
        return (
            self.offset[0] + column * self.cell_width,
            self.offset[1] + row * self.cell_height,
        )

    def set_tileset(self, tileset: TileSet | None) -> None:
        """Replace the layer's bindings with one default binding or none.

        Args:
            tileset: Replacement set, or ``None`` to remove the binding.

        Raises:
            ValueError: If removing the set while cells exist or if existing
                cells do not fit the replacement set.
        """
        if tileset is None and self.chunks:
            raise ValueError(
                "cannot remove all tilesets from a non-empty tile layer"
            )
        if tileset is not None:
            for chunk in self.chunks.values():
                for encoded in chunk.cells:
                    cell = decode_tile_cell(encoded)
                    if cell is not None:
                        tileset.definition(cell.tile_id)
        self._tilesets.clear()
        if tileset is not None:
            self.add_tileset(
                tileset,
                identifier="default",
                name="Tileset",
                first_gid=0,
            )
        self._delete_render_data()
        self._mark_all_dirty()
        self._collision_revision += 1

    def add_tileset(
        self,
        tileset: TileSet,
        *,
        identifier: str,
        name: str,
        first_gid: int,
    ) -> TileSetBinding:
        """Add a tile set at a specified global ID range.

        Args:
            tileset: Tile set to bind.
            identifier: Unique binding identifier.
            name: Display name.
            first_gid: First global tile ID assigned to the set.

        Returns:
            The created binding.

        Raises:
            ValueError: If the identifier is duplicated or ID ranges overlap.
        """
        binding = TileSetBinding(
            identifier,
            name,
            first_gid,
            tileset,
        )
        if any(
            candidate.identifier == identifier
            for candidate in self._tilesets
        ):
            raise ValueError(
                f"duplicate tileset identifier: {identifier!r}"
            )
        if any(
            not (
                binding.last_gid < candidate.first_gid
                or binding.first_gid > candidate.last_gid
            )
            for candidate in self._tilesets
        ):
            raise ValueError("tileset global ID ranges must not overlap")
        self._tilesets.append(binding)
        self._tilesets.sort(key=lambda candidate: candidate.first_gid)
        self._mark_all_dirty()
        self._collision_revision += 1
        return binding

    def resolve_tile(self, tile_id: int) -> tuple[TileSet, int]:
        """Resolve a global tile ID to its tile set and local ID.

        Raises:
            ValueError: If no binding contains ``tile_id``.
        """
        binding = next(
            (
                candidate
                for candidate in self._tilesets
                if candidate.contains(tile_id)
            ),
            None,
        )
        if binding is None:
            raise ValueError(f"unknown global tile ID: {tile_id}")
        return binding.tileset, tile_id - binding.first_gid

    def remove_tileset(
        self,
        identifier: str,
        *,
        erase_used: bool = False,
    ) -> int:
        """Remove a binding, optionally erasing cells that use its IDs.

        Args:
            identifier: Binding identifier to remove.
            erase_used: If true, erase cells belonging to the binding.

        Returns:
            Number of cells using the binding that were erased or found.

        Raises:
            ValueError: If the binding is unknown or is in use and
                ``erase_used`` is false.
        """
        binding = next(
            (
                candidate
                for candidate in self._tilesets
                if candidate.identifier == identifier
            ),
            None,
        )
        if binding is None:
            raise ValueError(f"unknown tileset: {identifier!r}")
        usage = sum(
            1
            for chunk in self.chunks.values()
            for encoded in chunk.cells
            if (
                (cell := decode_tile_cell(encoded)) is not None
                and binding.contains(cell.tile_id)
            )
        )
        if usage and not erase_used:
            raise ValueError(
                f"tileset is used by {usage} tile cell(s)"
            )
        if usage:
            for key, chunk in tuple(self.chunks.items()):
                for index, encoded in enumerate(chunk.cells):
                    cell = decode_tile_cell(encoded)
                    if cell is not None and binding.contains(cell.tile_id):
                        chunk.cells[index] = 0
                        chunk.dirty = True
                if chunk.empty:
                    chunk.delete()
                    del self.chunks[key]
        self._tilesets.remove(binding)
        self._delete_render_data()
        self._mark_all_dirty()
        self._collision_revision += 1
        return usage

    def _physics_collision_state(self) -> tuple[object, ...]:
        return (
            self.enabled,
            self.collision_enabled,
            self.cell_width,
            self.cell_height,
            tuple(self.offset),
            self._collision_revision,
            tuple(
                (
                    binding.identifier,
                    binding.first_gid,
                    binding.tileset._collision_revision,
                )
                for binding in self._tilesets
            ),
        )

    def _collision_cells(self) -> set[tuple[int, int]]:
        cells: set[tuple[int, int]] = set()
        if not self.enabled or not self.collision_enabled:
            return cells
        for chunk in self.chunks.values():
            for local_row in range(chunk.size):
                for local_column in range(chunk.size):
                    cell = chunk.get(local_column, local_row)
                    if cell is None:
                        continue
                    tileset, local_id = self.resolve_tile(cell.tile_id)
                    if tileset.definition(local_id).collision is None:
                        continue
                    cells.add(
                        (
                            chunk.column * chunk.size + local_column,
                            chunk.row * chunk.size + local_row,
                        )
                    )
        return cells

    def _collision_entries(
        self,
    ) -> tuple[tuple[int, int, TileCell, TileCollision], ...]:
        entries: list[
            tuple[int, int, TileCell, TileCollision]
        ] = []
        if not self.enabled or not self.collision_enabled:
            return ()
        for chunk in self.chunks.values():
            for local_row in range(chunk.size):
                for local_column in range(chunk.size):
                    cell = chunk.get(local_column, local_row)
                    if cell is None:
                        continue
                    tileset, local_id = self.resolve_tile(cell.tile_id)
                    collision = tileset.definition(local_id).collision
                    if collision is None:
                        continue
                    entries.append(
                        (
                            chunk.column * chunk.size + local_column,
                            chunk.row * chunk.size + local_row,
                            cell,
                            collision,
                        )
                    )
        return tuple(entries)

    def update(self, delta_time: float) -> None:
        """Update animation time and invalidate chunks as displayed tiles change.

        Args:
            delta_time: Elapsed time in seconds.
        """
        render_state = tuple(
            (id(binding.tileset), binding.tileset._render_revision)
            for binding in self._tilesets
        )
        if render_state != self._render_state:
            self._render_state = render_state
            self._mark_all_dirty()
        if not any(
            binding.tileset.has_animations
            for binding in self._tilesets
        ):
            return
        self._elapsed += delta_time
        state = tuple(
            definition.animation.tile_id_at(self._elapsed)
            for binding in self._tilesets
            for definition in binding.tileset.definitions
            if definition.animation is not None
        )
        if state != self._animation_state:
            self._animation_state = state
            self._mark_all_dirty()

    def draw(self, camera: Camera) -> None:
        """Draw visible chunks using the supplied camera.

        Args:
            camera: Camera used to determine the visible chunk range.
        """
        if not self._presentation_enabled:
            return
        if not self._tilesets or not self.chunks:
            return
        for chunk in self.chunks.values():
            if chunk.dirty:
                self._rebuild_chunk(chunk)
        visible = self._visible_chunks(camera)
        for key, chunk in self.chunks.items():
            chunk_visible = key in visible
            for group in chunk._groups:
                if group.visible != chunk_visible:
                    group.visible = chunk_visible
        self._batch.draw()

    def delete(self) -> None:
        """Release render resources for all chunks."""
        self._delete_render_data()

    def _visible_chunks(self, camera: Camera) -> set[tuple[int, int]]:
        corners = (
            camera.screen_to_world((0, 0)),
            camera.screen_to_world((camera.viewport_width, 0)),
            camera.screen_to_world((0, camera.viewport_height)),
            camera.screen_to_world(
                (camera.viewport_width, camera.viewport_height)
            ),
        )
        columns = [self.world_to_cell(point.x, point.y)[0] for point in corners]
        rows = [self.world_to_cell(point.x, point.y)[1] for point in corners]
        minimum_column = min(columns) // self.chunk_size
        maximum_column = max(columns) // self.chunk_size
        minimum_row = min(rows) // self.chunk_size
        maximum_row = max(rows) // self.chunk_size
        return {
            (column, row)
            for column in range(minimum_column, maximum_column + 1)
            for row in range(minimum_row, maximum_row + 1)
        }

    def _rebuild_chunk(self, chunk: TileChunk) -> None:
        chunk.delete()
        if not self._tilesets or chunk.empty:
            chunk.dirty = False
            return
        mesh_data: dict[
            str,
            tuple[
                TileSet,
                list[float],
                list[float],
                list[int],
            ],
        ] = {}
        for local_row in range(chunk.size):
            for local_column in range(chunk.size):
                cell = chunk.get(local_column, local_row)
                if cell is None:
                    continue
                binding = next(
                    candidate
                    for candidate in self._tilesets
                    if candidate.contains(cell.tile_id)
                )
                local_tile_id = cell.tile_id - binding.first_gid
                tile_id = binding.tileset.displayed_tile_id(
                    local_tile_id,
                    self._elapsed,
                )
                region = binding.tileset.texture_region(tile_id)
                _, positions, texture_coordinates, indices = (
                    mesh_data.setdefault(
                        binding.identifier,
                        (binding.tileset, [], [], []),
                    )
                )
                column = chunk.column * chunk.size + local_column
                row = chunk.row * chunk.size + local_row
                x, y = self.cell_origin(column, row)
                right = x + self.cell_width
                top = y + self.cell_height
                positions.extend(
                    (
                        x,
                        y,
                        0,
                        right,
                        y,
                        0,
                        right,
                        top,
                        0,
                        x,
                        top,
                        0,
                    )
                )
                texture_coordinates.extend(
                    self._texture_coordinates(region.tex_coords, cell.flags)
                )
                start = len(positions) // 3 - 4
                indices.extend(
                    (
                        start,
                        start + 1,
                        start + 2,
                        start,
                        start + 2,
                        start + 3,
                    )
                )
        if not mesh_data:
            chunk.dirty = False
            return
        program = pyglet.sprite.get_default_shader()
        alpha = round(self.opacity * 255)
        for tileset, positions, texture_coordinates, indices in (
            mesh_data.values()
        ):
            texture = source_of(tileset.image).get_texture()
            parent = _ChunkVisibilityGroup()
            group = SpriteGroup(
                texture,
                gl.GL_SRC_ALPHA,
                gl.GL_ONE_MINUS_SRC_ALPHA,
                program,
                parent,
            )
            vertex_count = len(positions) // 3
            vertex_list = program.vertex_list_indexed(
                vertex_count,
                gl.GL_TRIANGLES,
                indices,
                self._batch,
                group,
                position=("f", positions),
                colors=("Bn", (255, 255, 255, alpha) * vertex_count),
                translate=("f", (0, 0, 0) * vertex_count),
                scale=("f", (1, 1) * vertex_count),
                rotation=("f", (0,) * vertex_count),
                tex_coords=("f", texture_coordinates),
            )
            chunk._groups.append(parent)
            chunk._vertex_lists.append(vertex_list)
        chunk._group = chunk._groups[0]
        chunk._vertex_list = chunk._vertex_lists[0]
        chunk.dirty = False

    @staticmethod
    def _texture_coordinates(
        coordinates: tuple[float, ...],
        flags: TileFlags,
    ) -> tuple[float, ...]:
        points = tuple(
            tuple(coordinates[index : index + 3])
            for index in range(0, 12, 3)
        )
        lookup = {
            (0, 0): points[0],
            (1, 0): points[1],
            (1, 1): points[2],
            (0, 1): points[3],
        }
        result: list[float] = []
        for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)):
            if flags & TileFlags.FLIP_DIAGONAL:
                u, v = v, u
            if flags & TileFlags.FLIP_HORIZONTAL:
                u = 1 - u
            if flags & TileFlags.FLIP_VERTICAL:
                v = 1 - v
            result.extend(lookup[(u, v)])
        return tuple(result)

    def _mark_all_dirty(self) -> None:
        for chunk in self.chunks.values():
            chunk.dirty = True

    def _delete_render_data(self) -> None:
        for chunk in self.chunks.values():
            chunk.delete()
