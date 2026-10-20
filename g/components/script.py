from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Component

if TYPE_CHECKING:
    from ..game import Game, GameObject
    from ..resources import ResourceManager
    from ..scene import Scene
    from ..tilemap import TileContact
    from ..physics import CollisionContact

class Script(Component):
    """Base class for gameplay scripts with scene, game, and input access."""
    def on_collision(self, contact: CollisionContact) -> None:
        """Handle the start of a solid physics collision."""
        pass

    def on_collision_stay(self, contact: CollisionContact) -> None:
        """Handle a continuing solid physics collision."""
        pass

    def on_collision_end(self, contact: CollisionContact) -> None:
        """Handle the end of a solid physics collision."""
        pass

    def on_overlap(self, other: GameObject) -> None:
        """Handle the start of a collider overlap."""
        pass

    def on_overlap_stay(self, other: GameObject) -> None:
        """Handle a continuing collider overlap."""
        pass

    def on_overlap_end(self, other: GameObject) -> None:
        """Handle the end of a collider overlap."""
        pass

    def on_tile_collision(self, contact: TileContact) -> None:
        """Handle the start of a solid collision with a tile layer."""
        pass

    def on_tile_collision_stay(self, contact: TileContact) -> None:
        """Handle a continuing solid collision with a tile layer."""
        pass

    def on_tile_collision_end(self, contact: TileContact) -> None:
        """Handle the end of a solid collision with a tile layer."""
        pass

    def on_tile_overlap(self, contact: TileContact) -> None:
        """Handle the start of an overlap with a tile layer."""
        pass

    def on_tile_overlap_stay(self, contact: TileContact) -> None:
        """Handle a continuing overlap with a tile layer."""
        pass

    def on_tile_overlap_end(self, contact: TileContact) -> None:
        """Handle the end of an overlap with a tile layer."""
        pass

    @property
    def game_object(self) -> GameObject:
        """Owning game object.

        Raises:
            RuntimeError: The script is not attached.
        """
        if self.owner is None:
            raise RuntimeError("script is not attached to a game object")
        return self.owner

    @property
    def scene(self) -> Scene:
        """Scene containing the owning game object.

        Raises:
            RuntimeError: The object is not attached to a scene.
        """
        scene = self.game_object.scene
        if scene is None:
            raise RuntimeError("script's game object is not in a scene")
        return scene

    @property
    def game(self) -> Game:
        """Game that owns the current scene."""
        return self.scene.game

    @property
    def input(self):
        """Input manager of the current game."""
        return self.game.input

    @property
    def resources(self) -> ResourceManager:
        """Resource manager of the current game."""
        return self.game.resources
