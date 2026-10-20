from __future__ import annotations

from collections.abc import Mapping
from math import ceil, floor
from os import PathLike
from typing import TYPE_CHECKING, Protocol, TypeVar

from .components import ShapeStyle, SpriteAnimation
from .nodes import GameObject, Node
from .objects import create_circle, create_line, create_rect, create_sprite, create_stateful_sprite, create_text
from .resources import Color, Font, ImageSource, ResourceManager, TextureFilter

if TYPE_CHECKING:
    from .scene import LayerSelector

GameObjectType = TypeVar("GameObjectType", bound=GameObject)
GridPosition = tuple[int, int]


class GridOwner(Protocol):
    """Owner interface required by :class:`Grid`."""

    @property
    def width(self) -> int:
        """Canvas width in pixels."""
        ...

    @property
    def height(self) -> int:
        """Canvas height in pixels."""
        ...

    @property
    def root(self) -> Node:
        """Root node used for overlay objects."""
        ...

    @property
    def resources(self) -> ResourceManager:
        """Resource manager used to resolve sprite paths."""
        ...

    def add(
        self,
        game_object: GameObjectType,
        *,
        layer: LayerSelector | None = None,
        z_index: int | None = None,
    ) -> GameObjectType:
        """Attach an object to the owner."""
        ...

    def remove(self, game_object: GameObject) -> None:
        """Remove an object from the owner."""
        ...


class Grid:
    """Maps virtual-pixel positions to fixed-size grid cells."""

    def __init__(
        self,
        game: GridOwner,
        cell_size: int = 32,
        *,
        columns: int | None = None,
        rows: int | None = None,
    ) -> None:
        """Create a grid over a game or scene.

        Args:
            game (GridOwner): Owner providing canvas dimensions and object attachment.
            cell_size (int): Cell width and height in pixels.
            columns (int | None): Optional explicit column count.
            rows (int | None): Optional explicit row count.

        Raises:
            ValueError: If a cell size or grid dimension is not positive.
        """
        if cell_size <= 0:
            raise ValueError("cell_size must be positive")
        self.game = game
        self.cell_size = cell_size
        self.columns = columns if columns is not None else ceil(game.width / cell_size)
        self.rows = rows if rows is not None else ceil(game.height / cell_size)
        if self.columns <= 0 or self.rows <= 0:
            raise ValueError("columns and rows must be positive")
        self._overlay: tuple[GameObject, ...] = ()

    def contains(self, position: GridPosition) -> bool:
        """Return whether the integer ``(column, row)`` is inside the grid."""
        column, row = position
        return 0 <= column < self.columns and 0 <= row < self.rows

    def cell_center(self, column: float, row: float) -> tuple[float, float]:
        """Return the world-pixel center of a cell coordinate."""
        offset = self.cell_size / 2
        return column * self.cell_size + offset, row * self.cell_size + offset

    def cell_origin(self, column: float, row: float) -> tuple[float, float]:
        """Return the world-pixel bottom-left origin of a cell coordinate."""
        return column * self.cell_size, row * self.cell_size

    def world_to_cell(self, x: float, y: float) -> GridPosition:
        """Map a world-pixel point to its containing cell using floor."""
        return floor(x / self.cell_size), floor(y / self.cell_size)

    def snap_to_center(
        self, position: tuple[float, float]
    ) -> tuple[float, float]:
        """Snap a world-pixel point to its cell center."""
        return self.cell_center(*self.world_to_cell(*position))

    def snap_to_origin(
        self, position: tuple[float, float]
    ) -> tuple[float, float]:
        """Snap a world-pixel point to its cell origin."""
        return self.cell_origin(*self.world_to_cell(*position))

    def add(
        self,
        game_object: GameObjectType,
        column: float,
        row: float,
        *,
        layer: LayerSelector | None = None,
        z_index: int | None = None,
    ) -> GameObjectType:
        """Center an existing object in a cell and attach it to the owner."""
        game_object.position = self.cell_center(column, row)
        return self.game.add(
            game_object,
            layer=layer,
            z_index=z_index,
        )

    def move(
        self,
        game_object: GameObject,
        column: float,
        row: float,
    ) -> None:
        """Move an object to the center of a cell; does not reattach it."""
        game_object.position = self.cell_center(column, row)

    def add_circle(
        self,
        column: float,
        row: float,
        color: Color,
        radius: float | None = None,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        style: ShapeStyle | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create a circle centered in a cell; default radius is half a cell."""
        x, y = self.cell_center(column, row)
        target, order = self._placement(layer, z_index)
        return self.game.add(
            create_circle(
                x,
                y,
                color,
                self.cell_size / 2 if radius is None else radius,
                z_index=order or 0,
                style=style,
                collider=collider,
            ),
            layer=target,
        )

    def add_rect(
        self,
        column: float,
        row: float,
        color: Color,
        width: float | None = None,
        height: float | None = None,
        angle: float = 0,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        style: ShapeStyle | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create a rectangle centered in a cell; omitted dimensions use cell size."""
        x, y = self.cell_center(column, row)
        target, order = self._placement(layer, z_index)
        return self.game.add(
            create_rect(
                x,
                y,
                color,
                self.cell_size if width is None else width,
                self.cell_size if height is None else height,
                angle,
                z_index=order or 0,
                style=style,
                collider=collider,
            ),
            layer=target,
        )

    def add_sprite(
        self,
        column: float,
        row: float,
        image: ImageSource | str | PathLike[str],
        angle: float = 0,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        filtering: TextureFilter | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create a sprite centered in a cell; ``angle`` is measured in degrees."""
        x, y = self.cell_center(column, row)
        target, order = self._placement(layer, z_index)
        if isinstance(image, (str, PathLike)):
            image = self.game.resources.load_image(image, filtering=filtering)
        elif filtering is not None:
            raise ValueError("filtering can only be set when loading an image path")
        return self.game.add(
            create_sprite(
                image,
                x,
                y,
                angle,
                z_index=order or 0,
                collider=collider,
            ),
            layer=target,
        )

    def add_text(
        self,
        column: float,
        row: float,
        text: str,
        font: Font | None = None,
        font_size: float = 12,
        color: Color = (255, 255, 255),
        angle: float = 0,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        screen_space: bool = False,
        alignment: str = "center",
    ) -> GameObject:
        """Create text anchored at the center of a cell."""
        x, y = self.cell_center(column, row)
        target, order = self._placement(layer, z_index)
        return self.game.add(
            create_text(
                text,
                x,
                y,
                font,
                font_size,
                color,
                angle,
                z_index=order or 0,
                screen_space=screen_space,
                alignment=alignment,
            ),
            layer=target,
        )

    def add_stateful_sprite(
        self,
        column: float,
        row: float,
        states: Mapping[str, ImageSource | SpriteAnimation | None],
        initial_state: str,
        angle: float = 0,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create a stateful sprite centered in a cell."""
        x, y = self.cell_center(column, row)
        target, order = self._placement(layer, z_index)
        return self.game.add(
            create_stateful_sprite(
                states,
                initial_state,
                x,
                y,
                angle,
                z_index=order or 0,
                collider=collider,
            ),
            layer=target,
        )

    @staticmethod
    def _placement(
        layer: LayerSelector | int | None,
        z_index: int | None,
    ) -> tuple[LayerSelector | None, int | None]:
        if isinstance(layer, int):
            if z_index is not None:
                raise ValueError(
                    "use z_index instead of an integer layer argument"
                )
            return None, layer
        return layer, z_index

    @property
    def overlay_visible(self) -> bool:
        """Return whether the grid overlay currently has visible line objects."""
        return bool(self._overlay)

    def show(
        self,
        color: Color = (255, 255, 255, 80),
        thickness: float = 1,
        z_index: int = -1_000_000,
    ) -> tuple[GameObject, ...]:
        """Replace the grid overlay with lines covering the game canvas.

        Args:
            color (Color): Grid-line color, including optional alpha.
            thickness (float): Line thickness in pixels.
            z_index (int): Drawing order for generated lines.

        Returns:
            tuple[GameObject, ...]: The added line objects.
        """
        self.hide()
        x_coordinates = [
            column * self.cell_size
            for column in range(self.columns + 1)
            if column * self.cell_size <= self.game.width
        ]
        y_coordinates = [
            row * self.cell_size
            for row in range(self.rows + 1)
            if row * self.cell_size <= self.game.height
        ]
        if x_coordinates[-1] != self.game.width:
            x_coordinates.append(self.game.width)
        if y_coordinates[-1] != self.game.height:
            y_coordinates.append(self.game.height)
        lines = [
            *(
                create_line(
                    (x, 0),
                    (x, self.game.height),
                    color,
                    thickness,
                    z_index=z_index,
                )
                for x in x_coordinates
            ),
            *(
                create_line(
                    (0, y),
                    (self.game.width, y),
                    color,
                    thickness,
                    z_index=z_index,
                )
                for y in y_coordinates
            ),
        ]
        self._overlay = tuple(self.game.add(line) for line in lines)
        return self._overlay

    def hide(self) -> None:
        """Remove all grid-overlay lines created by :meth:`show`."""
        for line in self._overlay:
            if line.parent is self.game.root:
                self.game.remove(line)
        self._overlay = ()
