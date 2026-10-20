from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .base import Component, requires_components
from .collider import Collider, ColliderMode
from ..inspector import inspector_field

if TYPE_CHECKING:
    from ..nodes import GameObject
    from ..tilemap import TileLayer

AreaHandler = Callable[["GameObject"], None]


@requires_components(Collider)
class Area(Component):
    """Track overlap events for one sensor collider.

    Attributes:
        collider_index: Index of the selected collider among this object's
            collider components. The selected collider is set to sensor mode.
    """

    __inspector_fields__ = {
        "collider_index": inspector_field(0, minimum=0, tooltip="Index among this object's colliders. The selected collider becomes a sensor."),
    }

    def __init__(self, collider_index: int = 0, *, enabled: bool = True) -> None:
        """Create an area that selects a collider when attached.

        Args:
            collider_index: Index among attached Collider components.
            enabled: Whether overlap tracking and callbacks are active.

        Raises:
            ValueError: The index is not a non-negative integer.
        """
        super().__init__(enabled=enabled)
        if isinstance(collider_index, bool) or not isinstance(collider_index, int) or collider_index < 0:
            raise ValueError("collider_index must be a non-negative integer")
        self._collider_index = collider_index
        self._collider: Collider | None = None
        self._overlapping: set[GameObject] = set()
        self._enter_handlers: list[AreaHandler] = []
        self._stay_handlers: list[AreaHandler] = []
        self._exit_handlers: list[AreaHandler] = []

    @property
    def collider_index(self) -> int:
        """Index of the selected collider among attached colliders."""
        if self.owner is not None and self._collider is not None:
            return self._colliders().index(self._collider)
        return self._collider_index

    @collider_index.setter
    def collider_index(self, value: int) -> None:
        """Select a collider by its index and switch it to sensor mode.

        Raises:
            ValueError: The index is invalid or does not identify an attached
                collider.
        """
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("collider_index must be a non-negative integer")
        if self.owner is not None:
            colliders = self._colliders()
            if value >= len(colliders):
                raise ValueError("Area collider_index does not reference an existing collider")
            self._collider = colliders[value]
            self._collider.mode = ColliderMode.SENSOR
        self._collider_index = value

    def _colliders(self) -> tuple[Collider, ...]:
        if self.owner is None:
            raise RuntimeError("Area is not attached")
        return tuple(item for item in self.owner.components if isinstance(item, Collider))

    @property
    def collider(self) -> Collider:
        """The selected sensor collider.

        Raises:
            RuntimeError: The area has not selected a collider yet.
        """
        if self._collider is None:
            raise RuntimeError("Area is not attached")
        return self._collider

    def uses_collider(self, collider: Component) -> bool:
        """Return whether ``collider`` is the collider selected by this area."""
        return self._collider is collider

    def on_attach(self) -> None:
        """Resolve the configured collider index on the owning object."""
        self.collider_index = self._collider_index

    def on_detach(self) -> None:
        """Clear the collider selection, overlap history, and callbacks."""
        self._collider = None
        self._overlapping.clear()
        self._enter_handlers.clear()
        self._stay_handlers.clear()
        self._exit_handlers.clear()

    @property
    def overlapping_objects(self) -> tuple[GameObject | TileLayer, ...]:
        """Current overlapping objects and tile layers, or empty if inactive."""
        if self.owner is None or self.owner.scene is None or not self.enabled or not self.owner.enabled_in_hierarchy:
            return ()
        return self.collider.overlapping_objects

    def contains(self, game_object: GameObject) -> bool:
        """Return whether ``game_object`` is currently overlapping.

        Args:
            game_object: Object to test.
        """
        return game_object in self.overlapping_objects

    def on_enter(self, handler: AreaHandler) -> AreaHandler:
        """Register a callback for newly overlapping game objects.

        Args:
            handler: Callback invoked when an object begins overlapping.

        Returns:
            The handler, allowing decorator-style registration.
        """
        self._enter_handlers.append(handler)
        return handler

    def on_stay(self, handler: AreaHandler) -> AreaHandler:
        """Register a callback for game objects that remain overlapping.

        Args:
            handler: Callback invoked while an object remains overlapping.

        Returns:
            The handler, allowing decorator-style registration.
        """
        self._stay_handlers.append(handler)
        return handler

    def on_exit(self, handler: AreaHandler) -> AreaHandler:
        """Register a callback for game objects that stop overlapping.

        Args:
            handler: Callback invoked when an object stops overlapping.

        Returns:
            The handler, allowing decorator-style registration.
        """
        self._exit_handlers.append(handler)
        return handler

    def remove_handler(self, handler: AreaHandler) -> None:
        """Remove a handler from every event list in which it appears.

        Args:
            handler: Callback to remove.
        """
        for handlers in (self._enter_handlers, self._stay_handlers, self._exit_handlers):
            if handler in handlers:
                handlers.remove(handler)

    def update(self, delta_time: float) -> None:
        """Compare current overlaps with the previous update and notify handlers."""
        from ..nodes import GameObject

        del delta_time
        current = {target for target in self.overlapping_objects if isinstance(target, GameObject)}
        for targets, handlers in (
            (current - self._overlapping, self._enter_handlers),
            (current & self._overlapping, self._stay_handlers),
            (self._overlapping - current, self._exit_handlers),
        ):
            for target in targets:
                for handler in tuple(handlers):
                    if self.owner is None:
                        return
                    with self.owner.require_scene().physics._independent_queries():
                        handler(target)
        if self.owner is not None:
            self._overlapping = {target for target in current if target.scene is self.owner.scene}

    def _reset_overlaps(self) -> None:
        """Discard remembered overlap state."""
        self._overlapping.clear()
