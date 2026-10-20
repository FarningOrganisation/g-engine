from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import nullcontext
from os import PathLike
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import uuid4
from pathlib import Path
from copy import deepcopy

import pyglet

from .camera import Camera
from .components import (
    Area,
    Light2D,
    PointLight2D,
    ShadowOccluder2D,
    ShapeStyle,
    SpotLight2D,
    SpriteAnimation,
)
from .grid import Grid
from .navigation import NavigationAgent, NavigationWorld
from .nodes import GameObject, Node
from .objects import (
    create_circle,
    create_line,
    create_polygon,
    create_rect,
    create_sprite,
    create_area_box,
    create_area_circle,
    create_background,
    StatefulGameObject,
    create_stateful_sprite,
    create_text,
    create_triangle,
)
from .physics import CollisionContact, PhysicsWorld
from .resources import Color, Font, Image, ImageSource, TextureFilter
from .math import Vec2
from .timing import SceneTime

if TYPE_CHECKING:
    from .game import Game
    from .shader import PostProcess, PostProcessPipeline
    from .tilemap import TileContact, TileLayer, TileSet
    from .persistence import SceneDocument, SceneTypeRegistry


GameObjectType = TypeVar("GameObjectType", bound=GameObject)
SceneType = TypeVar("SceneType", bound="Scene")


class SceneLayer:
    """Base for an ordered scene layer.

    Visibility controls drawing; enabled state controls updating. Subclasses
    provide the layer-specific contents and rendering behavior.
    """

    def __init__(
        self,
        scene: Scene,
        name: str,
        *,
        identifier: str | None = None,
        visible: bool = True,
        enabled: bool = True,
    ) -> None:
        """Create a layer belonging to ``scene``.

        Args:
            scene (Scene): Owning scene.
            name (str): Non-empty layer name.
            identifier (str | None): Optional stable identifier; generated when omitted.
            visible (bool): Whether the layer is drawn.
            enabled (bool): Whether the layer is updated.

        Raises:
            ValueError: If ``name`` is empty.
        """
        if not name:
            raise ValueError("layer name must not be empty")
        self.scene = scene
        self.id = identifier or str(uuid4())
        self.name = name
        self.visible = visible
        self.enabled = enabled

    @property
    def empty(self) -> bool:
        """Whether the layer contains no content; subclasses must implement."""
        raise NotImplementedError

    def update(self, delta_time: float) -> None:
        """Update the layer for a simulation step measured in seconds."""
        del delta_time

    def draw(self, camera: Camera) -> None:
        """Draw layer contents using the scene camera."""
        del camera

    def draw_ui(self) -> None:
        """Draw screen-space layer contents."""
        pass

    def delete(self) -> None:
        """Release layer-owned resources."""
        pass


class ObjectLayer(SceneLayer):
    """A layer that owns a hierarchy of game objects."""

    def __init__(
        self,
        scene: Scene,
        name: str,
        *,
        identifier: str | None = None,
        visible: bool = True,
        enabled: bool = True,
    ) -> None:
        """Create an object layer with its own object root and render batches."""
        super().__init__(
            scene,
            name,
            identifier=identifier,
            visible=visible,
            enabled=enabled,
        )
        self.root = Node()
        from .runtime import NullBatch
        self._batch = (
            pyglet.graphics.Batch()
            if scene.presentation_enabled else NullBatch()
        )
        self._ui_batch = (
            pyglet.graphics.Batch()
            if scene.presentation_enabled else NullBatch()
        )
        self._groups: dict[int, pyglet.graphics.Group] = {}
        self._render_structure: tuple[object, ...] | None = None

    @property
    def objects(self) -> tuple[GameObject, ...]:
        """Return game objects parented directly to this layer's root."""
        return tuple(
            child for child in self.root.children if isinstance(child, GameObject)
        )

    def get_children(self, *, recursive: bool = False) -> tuple[GameObject, ...]:
        """Return direct object children, or all descendant objects if recursive."""
        return self.root.get_children(recursive=recursive)

    def get_child(self, name: str, *, recursive: bool = False) -> GameObject | None:
        """Find a child by name; search descendants when ``recursive`` is true."""
        return self.root.get_child(name, recursive=recursive)

    def require_child(self, name: str, *, recursive: bool = False) -> GameObject:
        """Find a child by name or raise ``LookupError`` if it is absent."""
        child = self.get_child(name, recursive=recursive)
        if child is None:
            scope = "descendant" if recursive else "child"
            raise LookupError(f"ObjectLayer {self.name!r} does not have {scope} {name!r}")
        return child

    @property
    def empty(self) -> bool:
        """Return whether the layer has no top-level game objects."""
        return not self.objects

    def add(
        self,
        game_object: GameObjectType,
        *,
        z_index: int | None = None,
    ) -> GameObjectType:
        """Add an object to this layer, optionally setting its drawing order."""
        return self.scene.add(game_object, layer=self, z_index=z_index)

    def remove(self, game_object: GameObject) -> None:
        """Remove an object subtree from this layer.

        Raises:
            ValueError: If ``game_object`` belongs to another layer.
        """
        if game_object.layer is not self:
            raise ValueError("game object does not belong to this layer")
        self.scene.remove(game_object)

    def _group_for(self, z_index: int) -> pyglet.graphics.Group:
        if not self.scene.presentation_enabled:
            from .runtime import NullGroup
            return NullGroup(order=z_index)  # type: ignore[return-value]
        if z_index not in self._groups:
            self._groups[z_index] = pyglet.graphics.Group(order=z_index)
        return self._groups[z_index]

    def draw(self, camera: Camera) -> None:
        """Synchronize object renderers and draw this layer's batch."""
        del camera
        for node in self._prepare_render_structure():
            if not isinstance(node, GameObject):
                continue
            for renderer in node.renderers:
                renderer.sync()
        self._batch.draw()

    def _prepare_render_structure(self) -> tuple[Node, ...]:
        nodes = self.root.walk()
        structure = tuple(
            (
                id(node), id(node.parent), node.z_index,
                tuple(id(component) for component in node.components),
                tuple(
                    (id(renderer), renderer.screen_space, renderer.layer_ordered,
                     tuple(id(drawable) for drawable in renderer.drawables))
                    for renderer in node.renderers
                ),
            )
            for node in nodes if isinstance(node, GameObject)
        )
        if structure != self._render_structure:
            self.scene._prepare_subtree(self.root, self)
            self._render_structure = structure
            nodes = self.root.walk()
        return nodes
    def draw_ui(self) -> None:
        """Draw the layer's screen-space batch."""
        self._ui_batch.draw()

    def delete(self) -> None:
        """Delete resources for objects in the layer and detach its root children."""
        for node in self.root.children:
            for descendant in node.walk():
                if isinstance(descendant, GameObject) and descendant.layer is self:
                    descendant.delete()
            node.parent = None


LayerSelector = str | ObjectLayer


class SceneLayers:
    """Ordered collection of a scene's object and tile layers."""

    def __init__(self, scene: Scene) -> None:
        """Create a collection with an initial default object layer."""
        self._scene = scene
        self._layers: list[SceneLayer] = []
        self._default = self.add_object("Default", identifier="default")

    def __iter__(self) -> Iterator[SceneLayer]:
        """Iterate through layers in draw/update order."""
        return iter(self._layers)

    def __len__(self) -> int:
        """Return the number of layers."""
        return len(self._layers)

    def __getitem__(self, key: str | int) -> SceneLayer:
        """Get a layer by index, name, or stable ID.

        Raises:
            KeyError: If a string key matches no layer.
            IndexError: If an integer index is out of range.
        """
        if isinstance(key, int):
            return self._layers[key]
        for layer in self._layers:
            if layer.id == key or layer.name == key:
                return layer
        raise KeyError(key)

    @property
    def default(self) -> ObjectLayer:
        """The object layer used when no layer is specified."""
        return self._default

    @default.setter
    def default(self, layer: ObjectLayer | str) -> None:
        """Set the default object layer by instance or name/ID."""
        resolved = self.resolve(layer)
        if not isinstance(resolved, ObjectLayer):
            raise ValueError("the default layer must be an object layer")
        self._default = resolved

    def add(
        self,
        name: str,
        *,
        identifier: str | None = None,
        visible: bool = True,
        enabled: bool = True,
    ) -> ObjectLayer:
        """Add an object layer and return it."""
        return self.add_object(
            name,
            identifier=identifier,
            visible=visible,
            enabled=enabled,
        )

    def add_object(
        self,
        name: str,
        *,
        identifier: str | None = None,
        visible: bool = True,
        enabled: bool = True,
    ) -> ObjectLayer:
        """Add an object layer with the supplied identity and state."""
        self._validate_new_layer(name, identifier)
        layer = ObjectLayer(
            self._scene,
            name,
            identifier=identifier,
            visible=visible,
            enabled=enabled,
        )
        self._layers.append(layer)
        return layer

    def add_tile(
        self,
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
        properties: dict[str, Any] | None = None,
    ) -> TileLayer:
        """Add a tile layer configured with a tileset and cell dimensions.

        Args:
            name (str): Layer name.
            tileset (TileSet | None): Optional tileset bound at global ID zero.
            identifier (str | None): Optional stable layer identifier.
            visible (bool): Whether the layer is drawn.
            enabled (bool): Whether its data participates in scene systems.
            cell_width (int | None): Cell width in pixels; defaults to the
                tileset width or 32.
            cell_height (int | None): Cell height in pixels; defaults to the
                tileset height or 32.
            chunk_size (int): Chunk edge length in cells.
            opacity (float): Opacity from zero to one.
            offset (tuple[float, float]): World-space origin offset in pixels.
            collision_enabled (bool): Whether tile collision is active.
            navigation_enabled (bool): Whether tile navigation is active.
            properties (dict[str, Any] | None): Additional layer properties.

        Returns:
            TileLayer: The newly added layer.

        Raises:
            ValueError: If the name or identifier is duplicated, or dimensions
                or opacity are invalid.
        """
        from .tilemap import TileLayer

        self._validate_new_layer(name, identifier)
        layer = TileLayer(
            self._scene,
            name,
            tileset,
            identifier=identifier,
            visible=visible,
            enabled=enabled,
            cell_width=cell_width,
            cell_height=cell_height,
            chunk_size=chunk_size,
            opacity=opacity,
            offset=offset,
            collision_enabled=collision_enabled,
            navigation_enabled=navigation_enabled,
            properties=properties,
        )
        self._layers.append(layer)
        return layer

    def _validate_new_layer(
        self,
        name: str,
        identifier: str | None,
    ) -> None:
        if any(layer.name == name for layer in self._layers):
            raise ValueError(f"scene layer name already exists: {name!r}")
        if identifier is not None and any(
            layer.id == identifier for layer in self._layers
        ):
            raise ValueError(f"scene layer ID already exists: {identifier!r}")

    def remove(self, layer: SceneLayer | str) -> SceneLayer:
        """Remove and return an empty, non-default layer.

        Raises:
            ValueError: If the layer is the default or contains content.
        """
        resolved = self.resolve(layer)
        if resolved is self.default:
            raise ValueError("cannot remove the default scene layer")
        if not resolved.empty:
            raise ValueError("cannot remove a non-empty scene layer")
        self._layers.remove(resolved)
        resolved.delete()
        return resolved

    def move(self, layer: SceneLayer | str, index: int) -> None:
        """Move a layer to an index, clamped to the current collection bounds."""
        resolved = self.resolve(layer)
        self._layers.remove(resolved)
        self._layers.insert(max(0, min(index, len(self._layers))), resolved)

    def resolve(self, layer: SceneLayer | str | None) -> SceneLayer:
        """Resolve a layer reference; ``None`` selects the default layer.

        Raises:
            ValueError: If a layer instance belongs to another scene.
            TypeError: If ``layer`` is not a layer, name, ID, or ``None``.
            KeyError: If a name or ID does not exist.
        """
        if layer is None:
            return self.default
        if isinstance(layer, SceneLayer):
            if layer.scene is not self._scene or layer not in self._layers:
                raise ValueError("scene layer belongs to another scene")
            return layer
        if not isinstance(layer, str):
            raise TypeError("scene layer must be a name, ID, or SceneLayer")
        return self[layer]

    def resolve_object(
        self,
        layer: ObjectLayer | str | None,
    ) -> ObjectLayer:
        """Resolve a layer and require it to be an object layer."""
        resolved = self.resolve(layer)
        if not isinstance(resolved, ObjectLayer):
            raise ValueError("game objects can only use object layers")
        return resolved

    def _clear(self) -> None:
        for layer in self._layers:
            layer.delete()
        self._layers.clear()


class Scene:
    """A self-contained world with objects, layers, simulation, and callbacks.

    Subclass the lifecycle hooks to react to loading, entering, showing,
    exiting, unloading, and fixed-step updates. Simulation time is in seconds.
    """

    def __init__(
        self,
        *,
        bg_color: Color | None = None,
        ambient_light: Color = (64, 64, 64, 255),
        grid_cell_size: int | None = None,
        presentation_enabled: bool = True,
    ) -> None:
        """Create an unregistered scene.

        Args:
            bg_color (Color | None): Background color; defaults to the owning game's color.
            ambient_light: RGB or RGBA color applied wherever native lights do
                not contribute.
            grid_cell_size (int | None): Grid cell size in pixels; defaults to the game.
            presentation_enabled (bool): Whether graphics resources are created.
        """
        self.bg_color = bg_color
        self.ambient_light = ambient_light
        self.grid_cell_size = grid_cell_size
        self.presentation_enabled = presentation_enabled
        self.post_process: PostProcess | PostProcessPipeline | None = None
        self._game: Game | None = None
        self._loaded = False
        self._components_ready_enabled = True
        self._source_document: SceneDocument | None = None
        self._source_registry: SceneTypeRegistry | None = None
        self.time = SceneTime()
        self.layers = SceneLayers(self)

    @property
    def ambient_light(self) -> Color:
        """Color applied to the world before native light contributions."""
        return self._ambient_light

    @ambient_light.setter
    def ambient_light(self, value: Color) -> None:
        """Set the ambient RGB or RGBA byte color.

        Raises:
            ValueError: The color is not three or four byte channels.
        """
        channels = tuple(value)
        if len(channels) not in {3, 4} or any(
            not isinstance(channel, int) or not 0 <= channel <= 255
            for channel in channels
        ):
            raise ValueError(
                "ambient_light must contain three or four byte channels"
            )
        self._ambient_light = channels  # type: ignore[assignment]

    @property
    def lights(self) -> tuple[Light2D, ...]:
        """Enabled lights attached to enabled objects in visible layers."""
        return tuple(
            component
            for node in self.walk()
            if (
                isinstance(node, GameObject)
                and node.enabled_in_hierarchy
                and node.layer is not None
                and node.layer.visible
            )
            for component in node.components
            if isinstance(component, Light2D) and component.enabled
        )

    @property
    def shadow_occluders(self) -> tuple[ShadowOccluder2D, ...]:
        """Enabled shadow occluders attached to enabled scene objects."""
        return tuple(
            component
            for node in self.walk()
            if (
                isinstance(node, GameObject)
                and node.enabled_in_hierarchy
                and node.layer is not None
                and node.layer.visible
            )
            for component in node.components
            if isinstance(component, ShadowOccluder2D) and component.enabled
        )

    @property
    def lighting_active(self) -> bool:
        """Whether an enabled light currently opts the scene into lighting."""
        return bool(self.lights)

    def add_point_light(
        self,
        x: float,
        y: float,
        radius: float = 256,
        *,
        color: Color = (255, 255, 255, 255),
        intensity: float = 1,
        falloff: float = 1,
        casts_shadows: bool = False,
        layer: LayerSelector | None = None,
    ) -> GameObject:
        """Create an object containing a :class:`PointLight2D` component."""
        game_object = GameObject(x, y)
        game_object.add_component(
            PointLight2D(
                radius,
                color=color,
                intensity=intensity,
                falloff=falloff,
                casts_shadows=casts_shadows,
            )
        )
        return self.add(game_object, layer=layer)

    def add_spot_light(
        self,
        x: float,
        y: float,
        range: float = 256,
        *,
        angle: float = 50,
        inner_angle: float | None = None,
        color: Color = (255, 255, 255, 255),
        intensity: float = 1,
        falloff: float = 1,
        casts_shadows: bool = False,
        rotation: float = 0,
        layer: LayerSelector | None = None,
    ) -> GameObject:
        """Create an object containing a directed :class:`SpotLight2D`."""
        game_object = GameObject(x, y, rotation=rotation)
        game_object.add_component(
            SpotLight2D(
                range,
                angle=angle,
                inner_angle=inner_angle,
                color=color,
                intensity=intensity,
                falloff=falloff,
                casts_shadows=casts_shadows,
            )
        )
        return self.add(game_object, layer=layer)

    @property
    def game(self) -> Game:
        """Return the owning game, or raise ``RuntimeError`` before binding."""
        if self._game is None:
            raise RuntimeError("scene must be added to a game before use")
        return self._game

    @property
    def input(self):
        """Forward to the owning game's input state.

        Returns:
            (Input): Keyboard and mouse state for the owning game.
        """
        return self.game.input

    @property
    def resources(self):
        """Forward to the owning game's resource manager.

        Returns:
            (ResourceManager): Shared resource manager.
        """
        return self.game.resources

    @property
    def width(self) -> int:
        """Return the virtual scene width in pixels."""
        return self.game.width

    @property
    def height(self) -> int:
        """Return the virtual scene height in pixels."""
        return self.game.height

    @property
    def pixel_width(self) -> int:
        """Return the virtual scene width in pixels."""
        return self.width

    @property
    def pixel_height(self) -> int:
        """Return the virtual scene height in pixels."""
        return self.height

    @property
    def objects(self) -> tuple[GameObject, ...]:
        """Return top-level game objects across object layers."""
        return tuple(
            game_object
            for layer in self.layers
            if isinstance(layer, ObjectLayer)
            for game_object in layer.objects
        )

    @property
    def root(self) -> Node:
        """Return the root node of the current default object layer."""
        return self.default_layer.root

    def get_children(self, *, recursive: bool = False) -> tuple[GameObject, ...]:
        """Return direct children across object layers, or all descendants."""
        return tuple(
            child
            for layer in self.layers
            if isinstance(layer, ObjectLayer)
            for child in layer.get_children(recursive=recursive)
        )

    def get_child(self, name: str, *, recursive: bool = False) -> GameObject | None:
        """Find the first named object across object layers."""
        return next(
            (child for child in self.get_children(recursive=recursive) if child.name == name),
            None,
        )

    def require_child(self, name: str, *, recursive: bool = False) -> GameObject:
        """Find a named object or raise ``LookupError``."""
        child = self.get_child(name, recursive=recursive)
        if child is None:
            scope = "descendant" if recursive else "child"
            raise LookupError(f"Scene does not have {scope} {name!r}")
        return child

    @property
    def default_layer(self) -> ObjectLayer:
        """Return the default object layer."""
        return self.layers.default

    @default_layer.setter
    def default_layer(self, layer: ObjectLayer | str) -> None:
        """Choose the default object layer by instance or name/ID."""
        self.layers.default = layer

    @property
    def _batch(self) -> pyglet.graphics.Batch:
        return self.default_layer._batch

    @property
    def _ui_batch(self) -> pyglet.graphics.Batch:
        return self.default_layer._ui_batch

    def walk(self) -> tuple[Node, ...]:
        """Return all nodes in object-layer hierarchy order."""
        return tuple(
            node
            for layer in self.layers
            if isinstance(layer, ObjectLayer)
            for node in layer.root.walk()
        )

    def _bind(self, game: Game) -> None:
        from .tilemap import TileLayer

        if self._game is not None:
            if self._game is game:
                return
            raise ValueError("scene already belongs to another game")
        self._game = game
        cell_size = (
            game.default_grid_cell_size
            if self.grid_cell_size is None
            else self.grid_cell_size
        )
        self.bg_color = game.bg_color if self.bg_color is None else self.bg_color
        self.grid = Grid(self, cell_size)
        self.navigation = NavigationWorld(
            self.grid.columns,
            self.grid.rows,
            self.grid.cell_size,
        )
        self.navigation.bind(self.walk)
        self.physics = PhysicsWorld()
        self.physics.bind(
            self.walk,
            lambda: (
                layer
                for layer in self.layers
                if isinstance(layer, TileLayer)
            ),
        )
        self._collisions: set[tuple[GameObject, GameObject]] = set()
        self._collision_contacts: dict[
            tuple[GameObject, GameObject], tuple[CollisionContact, CollisionContact]
        ] = {}
        self._overlaps: set[tuple[GameObject, GameObject]] = set()
        self._tile_collisions: set[tuple[GameObject, TileContact]] = set()
        self._tile_overlaps: set[tuple[GameObject, TileContact]] = set()
        self.camera = Camera(self.width, self.height)
        if not self.presentation_enabled:
            from .runtime import NullBatch, NullDrawable
            self._background_batch = NullBatch()
            self._background = NullDrawable()
            self.gui = _HeadlessGUIManager()
            return
        self._background_batch = pyglet.graphics.Batch()
        rgb = self.bg_color[:3]
        self._background = pyglet.shapes.Rectangle(
            0,
            0,
            self.width,
            self.height,
            color=rgb,
            batch=self._background_batch,
            group=pyglet.graphics.Group(order=-1_000_000),
        )
        self._background.opacity = (
            self.bg_color[3] if len(self.bg_color) == 4 else 255
        )
        from .gui import GUIManager

        self.gui = GUIManager(self)

    def _load(self) -> None:
        if not self._loaded:
            loaded = False
            try:
                self.on_load()
                loaded = True
            finally:
                if not loaded:
                    self.time._reset()
            self._loaded = True

    def _unload(self) -> None:
        try:
            if self._loaded:
                self.on_unload()
        finally:
            self.time._reset()
            self._loaded = False

    def add(
        self,
        game_object: GameObjectType,
        *,
        layer: LayerSelector | None = None,
        z_index: int | None = None,
    ) -> GameObjectType:
        """Attach a detached object subtree to an object layer.

        Args:
            game_object (GameObjectType): Object to attach.
            layer (LayerSelector | None): Object-layer name or instance; defaults to ``default_layer``.
            z_index (int | None): Optional drawing order within the selected layer.

        Returns:
            GameObjectType: The attached object.

        Raises:
            ValueError: If the object is already parented or the layer is invalid.
        """
        if game_object.parent is not None:
            raise ValueError("game object already belongs to a scene")
        target = self.layers.resolve_object(layer)
        if z_index is not None:
            game_object.z_index = z_index
        target.root.add_child(game_object)
        self._prepare_subtree(game_object, target)
        self.physics.refresh()
        return game_object

    def save(
        self,
        filename: str,
        *,
        asset_root: str | None = None,
        registry=None,
    ) -> None:
        """Serialize this scene to a file.

        Args:
            filename (str): Destination scene-document path.
            asset_root (str | None): Base directory used to resolve serialized assets.
            registry (SceneTypeRegistry | None): Optional custom scene-type registry.
        """
        from .persistence import SceneSerializer

        SceneSerializer(registry).save(
            self,
            filename,
            asset_root=asset_root,
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

    def add_circle(
        self,
        x: float,
        y: float,
        color: Color,
        radius: float,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        style: ShapeStyle | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create and add a circle centered at ``(x, y)`` in virtual pixels."""
        target, order = self._placement(layer, z_index)
        return self.add(
            create_circle(
                x,
                y,
                color,
                radius,
                z_index=order or 0,
                style=style,
                collider=collider,
            ),
            layer=target,
        )

    def add_rect(
        self,
        x: float,
        y: float,
        color: Color,
        width: float,
        height: float,
        angle: float = 0,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        style: ShapeStyle | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create and add a rectangle centered at ``(x, y)``; angle is degrees."""
        target, order = self._placement(layer, z_index)
        return self.add(
            create_rect(
                x,
                y,
                color,
                width,
                height,
                angle,
                z_index=order or 0,
                style=style,
                collider=collider,
            ),
            layer=target,
        )

    def add_sprite(
        self,
        x: float,
        y: float,
        image: ImageSource | str | PathLike[str],
        angle: float = 0,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        filtering: TextureFilter | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create and add a sprite at a virtual-pixel position."""
        target, order = self._placement(layer, z_index)
        if isinstance(image, (str, PathLike)):
            image = self.resources.load_image(image, filtering=filtering)
        elif filtering is not None:
            raise ValueError("filtering can only be set when loading an image path")
        return self.add(
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
        x: float,
        y: float,
        text: str,
        font: Font | None = None,
        font_size: float = 12,
        color: Color = (255, 255, 255),
        angle: float = 0,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        screen_space: bool = False,
    ) -> GameObject:
        """Create and add text anchored at a virtual-pixel position.

        Set ``screen_space`` to render independently of the camera.
        """
        target, order = self._placement(layer, z_index)
        return self.add(
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
            ),
            layer=target,
        )

    def add_stateful_game_object(
        self,
        states: Mapping[str, GameObject],
        initial_state: str,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
    ) -> StatefulGameObject:
        """Create and add a game object that selects among named visuals."""
        target, order = self._placement(layer, z_index)
        return self.add(
            StatefulGameObject(states, initial_state, z_index=order),
            layer=target,
        )

    def add_stateful_sprite(
        self,
        x: float,
        y: float,
        states: Mapping[str, ImageSource | SpriteAnimation | None],
        initial_state: str,
        angle: float = 0,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create and add a sprite with named image or animation states."""
        target, order = self._placement(layer, z_index)
        return self.add(
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

    def add_line(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        color: Color = (255, 255, 255),
        thickness: float = 1,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        screen_space: bool = False,
    ) -> GameObject:
        """Create and add a line between two virtual-pixel points."""
        target, order = self._placement(layer, z_index)
        return self.add(
            create_line(
                start,
                end,
                color,
                thickness,
                z_index=order or 0,
                screen_space=screen_space,
            ),
            layer=target,
        )

    def add_triangle(
        self,
        first: tuple[float, float],
        second: tuple[float, float],
        third: tuple[float, float],
        color: Color = (255, 255, 255),
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        style: ShapeStyle | None = None,
        screen_space: bool = False,
    ) -> GameObject:
        """Create and add a triangle from three virtual-pixel vertices."""
        target, order = self._placement(layer, z_index)
        return self.add(
            create_triangle(
                first,
                second,
                third,
                color,
                z_index=order or 0,
                style=style,
                screen_space=screen_space,
            ),
            layer=target,
        )

    def add_polygon(
        self,
        points: Sequence[tuple[float, float]],
        color: Color = (255, 255, 255),
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        style: ShapeStyle | None = None,
        screen_space: bool = False,
    ) -> GameObject:
        """Create and add a polygon from at least three virtual-pixel points.

        Raises:
            ValueError: If fewer than three points are supplied.
        """
        target, order = self._placement(layer, z_index)
        return self.add(
            create_polygon(
                points,
                color,
                z_index=order or 0,
                style=style,
                screen_space=screen_space,
            ),
            layer=target,
        )

    def add_area_box(
        self, x: float, y: float, width: float, height: float,
        layer: LayerSelector | int | None = None, *,
        rotation: float = 0, enabled: bool = True, z_index: int | None = None,
    ) -> GameObject:
        """Create and add a box sensor centered at ``(x, y)`` in pixels."""
        target, order = self._placement(layer, z_index)
        obj = create_area_box(x, y, width, height, rotation=rotation, enabled=enabled)
        obj.z_index = order or 0
        return self.add(obj, layer=target)

    def add_background(
        self, image: Image | None = None, layer: LayerSelector | None = None, *,
        z_index: int = 0, tile_size: Vec2 | tuple[float, float] | None = None,
        repeat_x: bool = True, repeat_y: bool = True,
        offset: Vec2 | tuple[float, float] = (0, 0),
        parallax: Vec2 | tuple[float, float] = (0, 0),
        scroll_speed: Vec2 | tuple[float, float] = (0, 0),
        opacity: int = 255,
    ) -> GameObject:
        """Create and add a repeated background, using a background layer by default."""
        obj = create_background(
            image, tile_size=tile_size, repeat_x=repeat_x, repeat_y=repeat_y,
            offset=offset, parallax=parallax, scroll_speed=scroll_speed, opacity=opacity,
        )
        if layer is None:
            name = "Background"
            suffix = 2
            while True:
                existing = next((candidate for candidate in self.layers if candidate.name == name), None)
                if isinstance(existing, ObjectLayer):
                    target = existing
                    break
                if existing is None:
                    target = self.layers.add(name)
                    self.layers.move(target, 0)
                    break
                name = f"Background {suffix}"
                suffix += 1
        else:
            target = self.layers.resolve_object(layer)
        return self.add(obj, layer=target, z_index=z_index)

    def add_area_circle(
        self, x: float, y: float, radius: float,
        layer: LayerSelector | int | None = None, *,
        enabled: bool = True, z_index: int | None = None,
    ) -> GameObject:
        """Create and add a circular sensor centered at ``(x, y)`` in pixels."""
        target, order = self._placement(layer, z_index)
        obj = create_area_circle(x, y, radius, enabled=enabled)
        obj.z_index = order or 0
        return self.add(obj, layer=target)

    def remove(self, game_object: GameObject) -> None:
        """Remove an object subtree and dispose of its resources.

        Raises:
            ValueError: If the object does not belong to this scene.
            ExceptionGroup: If cleanup of one or more removed objects fails.
        """
        layer = game_object.layer
        if not isinstance(layer, ObjectLayer) or layer.scene is not self:
            raise ValueError("game object does not belong to this scene")
        nodes = game_object.walk()
        game_object.parent = None
        removed = set(nodes)
        self._collisions = {
            pair
            for pair in self._collisions
            if pair[0] not in removed and pair[1] not in removed
        }
        self._overlaps = {
            pair
            for pair in self._overlaps
            if pair[0] not in removed and pair[1] not in removed
        }
        self._collision_contacts = {
            pair: contacts for pair, contacts in self._collision_contacts.items()
            if pair[0] not in removed and pair[1] not in removed
        }
        self._tile_collisions = {pair for pair in self._tile_collisions if pair[0] not in removed}
        self._tile_overlaps = {pair for pair in self._tile_overlaps if pair[0] not in removed}
        self.physics.refresh()
        self.physics.refresh_contacts()
        errors: list[Exception] = []
        for node in reversed(nodes):
            if isinstance(node, GameObject):
                try:
                    node._delete_resources()
                except Exception as error:
                    errors.append(error)
            node.parent = None
        if errors:
            raise ExceptionGroup("GameObject subtree cleanup failed", errors)

    def move_to_layer(
        self,
        game_object: GameObject,
        layer: LayerSelector,
    ) -> GameObject:
        """Move an attached object subtree to another object layer."""
        if game_object.scene is not self or game_object.layer is None:
            raise ValueError("game object does not belong to this scene")
        target = self.layers.resolve_object(layer)
        if game_object.layer is target:
            return game_object
        if game_object.parent is not None:
            game_object.parent.remove_child(game_object)
        target.root.add_child(game_object)
        self._prepare_subtree(game_object, target)
        self.physics.refresh()
        return game_object

    def _prepare_subtree(
        self,
        node: Node,
        layer: ObjectLayer | None = None,
    ) -> None:
        if layer is None and isinstance(node, GameObject):
            layer = node.layer
        if layer is None:
            for candidate in self.layers:
                if not isinstance(candidate, ObjectLayer):
                    continue
                if node is candidate.root or candidate.root.is_ancestor_of(node):
                    layer = candidate
                    break
        if layer is None:
            raise ValueError("game object subtree is not attached to a scene layer")
        for descendant in node.walk():
            if not isinstance(descendant, GameObject):
                continue
            descendant._scene = self
            descendant._layer = layer
            agent = descendant.get_component(NavigationAgent)
            if agent is not None:
                agent.world = self.navigation
            for renderer in descendant.renderers:
                if not self.presentation_enabled:
                    continue
                batch = (
                    layer._ui_batch if renderer.screen_space and not renderer.layer_ordered else layer._batch
                )
                group = layer._group_for(descendant.z_index)
                renderer.bind_batch(batch, group)
        if self._components_ready_enabled:
            for descendant in node.walk():
                if isinstance(descendant, GameObject):
                    for component in descendant.components:
                        component._mark_ready()

    def _initialize_components(self) -> None:
        self._components_ready_enabled = True
        for node in self.walk():
            if isinstance(node, GameObject):
                for component in node.components:
                    component._mark_ready()

    def _update(self, delta_time: float) -> None:
        from .scripts.platformer_motor import PlatformerMotor

        self.gui._update(delta_time)
        if self.gui.pauses_scene:
            return
        self.time._update(delta_time)
        if self.post_process is not None and self.post_process.enabled:
            self.post_process.update(delta_time)
        self.physics.step(delta_time)
        for layer in self.layers:
            if not layer.enabled:
                continue
            layer.update(delta_time)
            if isinstance(layer, ObjectLayer):
                for node in layer.root.walk():
                    if (
                        not isinstance(node, GameObject)
                        or node.scene is not self
                        or not node.enabled_in_hierarchy
                    ):
                        continue
                    node.update(delta_time)
                    for component in node.components:
                        if component.owner is node and node.scene is self and component.enabled and not isinstance(component, (Area, PlatformerMotor)):
                            component.update(delta_time)
        # Motors consume input and support velocities after ordinary scripts,
        # independently of layer and attachment order.
        for layer in self.layers:
            if isinstance(layer, ObjectLayer) and layer.enabled:
                for node in layer.root.walk():
                    if isinstance(node, GameObject) and node.scene is self and node.enabled_in_hierarchy:
                        for component in node.components:
                            if isinstance(component, PlatformerMotor) and component.owner is node and component.enabled:
                                component.update(delta_time)
        with self.physics._share_contacts():
            for layer in self.layers:
                if isinstance(layer, ObjectLayer):
                    for node in layer.root.walk():
                        if isinstance(node, GameObject) and node.scene is self:
                            for component in node.components:
                                if isinstance(component, Area):
                                    if component.owner is node and node.scene is self and layer.enabled and node.enabled_in_hierarchy and component.enabled:
                                        context = (
                                            nullcontext()
                                            if type(component) is Area and "update" not in component.__dict__
                                            else self.physics._independent_queries()
                                        )
                                        with context:
                                            component.update(delta_time)
                                    else:
                                        component._reset_overlaps()
        self._dispatch_collisions()
        self.update(delta_time)

    def _dispatch_collisions(self) -> None:
        self.physics.refresh_contacts()
        current = {
            (first, second)
            for first, second in self.physics.collision_pairs
            if isinstance(second, GameObject)
        }
        overlaps = {
            (first, second)
            for first, second in self.physics.overlap_pairs
            if isinstance(second, GameObject)
        }
        tile_collisions = set(self.physics.tile_collision_pairs)
        tile_overlaps = set(self.physics.tile_overlap_pairs)
        contacts = dict(self.physics.collision_contacts)
        for first, second in current - self._collisions:
            a, b = contacts[(first, second)]
            first._dispatch_collision(first._collision_handlers, a)
            second._dispatch_collision(second._collision_handlers, b)
        for first, second in current & self._collisions:
            a, b = contacts[(first, second)]
            first._dispatch_collision(first._collision_stay_handlers, a)
            second._dispatch_collision(second._collision_stay_handlers, b)
        for first, second in self._collisions - current:
            a, b = self._collision_contacts[(first, second)]
            first._dispatch_collision(first._collision_end_handlers, a)
            second._dispatch_collision(second._collision_end_handlers, b)
        for first, contact in tile_collisions - self._tile_collisions:
            first._dispatch_tile_collision(
                first._tile_collision_handlers,
                contact,
            )
        for first, contact in tile_collisions & self._tile_collisions:
            first._dispatch_tile_collision(
                first._tile_collision_stay_handlers,
                contact,
            )
        for first, contact in self._tile_collisions - tile_collisions:
            first._dispatch_tile_collision(
                first._tile_collision_end_handlers,
                contact,
            )
        for first, second in overlaps - self._overlaps:
            first._dispatch_collision(first._overlap_handlers, second)
            second._dispatch_collision(second._overlap_handlers, first)
        for first, second in overlaps & self._overlaps:
            first._dispatch_collision(first._overlap_stay_handlers, second)
            second._dispatch_collision(second._overlap_stay_handlers, first)
        for first, second in self._overlaps - overlaps:
            first._dispatch_collision(first._overlap_end_handlers, second)
            second._dispatch_collision(second._overlap_end_handlers, first)
        for first, contact in tile_overlaps - self._tile_overlaps:
            first._dispatch_tile_collision(
                first._tile_overlap_handlers,
                contact,
            )
        for first, contact in tile_overlaps & self._tile_overlaps:
            first._dispatch_tile_collision(
                first._tile_overlap_stay_handlers,
                contact,
            )
        for first, contact in self._tile_overlaps - tile_overlaps:
            first._dispatch_tile_collision(
                first._tile_overlap_end_handlers,
                contact,
            )
        self._collisions = {pair for pair in current if pair[0].scene is self and pair[1].scene is self}
        self._collision_contacts = {pair: contacts[pair] for pair in self._collisions}
        self._overlaps = {pair for pair in overlaps if pair[0].scene is self and pair[1].scene is self}
        self._tile_collisions = {pair for pair in tile_collisions if pair[0].scene is self}
        self._tile_overlaps = {pair for pair in tile_overlaps if pair[0].scene is self}

    def _draw(self) -> None:
        for layer in self.layers:
            if not layer.visible:
                continue
            layer.draw(self.camera)

    def _draw_background(self) -> None:
        self._background_batch.draw()

    def _draw_ui(self) -> None:
        for layer in self.layers:
            if layer.visible:
                layer.draw_ui()
        self.gui._draw()

    def on_load(self) -> None:
        """Hook called once when the scene is first loaded; override as needed."""
        pass

    def on_enter(self, previous: Scene | None) -> None:
        """Hook called when this scene becomes current, before it is shown.

        Args:
            previous (Scene | None): Scene that was active, or ``None`` for
                the initial scene.
        """
        del previous

    def on_shown(self) -> None:
        """Hook called when the scene becomes visible after any transition."""
        pass

    def on_exit(self, next_scene: Scene | None) -> None:
        """Hook called before switching away from this scene."""
        del next_scene

    def on_unload(self) -> None:
        """Hook called when this scene is unloaded; override as needed."""
        pass

    def update(self, delta_time: float) -> None:
        """Hook called once per simulation step; ``delta_time`` is in seconds."""
        del delta_time


class _HeadlessGUIManager:
    pauses_scene = False

    def _update(self, delta_time: float) -> None:
        del delta_time

    def _draw(self) -> None:
        pass

    def on_key_press(self, symbol: object, modifiers: object) -> bool:
        del symbol, modifiers
        return False

    def on_key_release(self, symbol: object, modifiers: object) -> bool:
        del symbol, modifiers
        return False

    def on_mouse_motion(self, *args: object) -> bool:
        del args
        return False

    def on_mouse_press(self, *args: object) -> bool:
        del args
        return False

    def on_mouse_release(self, *args: object) -> bool:
        del args
        return False

    def on_mouse_drag(self, *args: object) -> bool:
        del args
        return False

    def on_mouse_scroll(self, *args: object) -> bool:
        del args
        return False

    def on_text(self, text: str) -> bool:
        del text
        return False

    def on_text_motion(self, motion: object, *, select: bool = False) -> bool:
        del motion, select
        return False


class Transition(ABC):
    """Base class for timed scene transitions.

    The scene swap occurs halfway through the transition; subclasses provide
    the overlay color at the current progress.
    """

    def __init__(self, duration: float, *, block_input: bool = True) -> None:
        """Create a transition with a positive duration in seconds.

        Args:
            duration (float): Total transition duration in seconds.
            block_input (bool): Whether game input is blocked during the transition.

        Raises:
            ValueError: If ``duration`` is not positive.
        """
        if duration <= 0:
            raise ValueError("duration must be positive")
        self.duration = duration
        self.block_input = block_input
        self.elapsed = 0.0
        self._swapped = False
        self._swap: Callable[[], Scene] | None = None
        self.incoming: Scene | None = None

    def _start(self, swap: Callable[[], Scene]) -> None:
        self.elapsed = 0.0
        self._swapped = False
        self._swap = swap
        self.incoming = None

    def _update(self, delta_time: float) -> bool:
        self.elapsed = min(self.duration, self.elapsed + delta_time)
        if not self._swapped and self.elapsed >= self.duration / 2:
            if self._swap is None:
                raise RuntimeError("transition has not been started")
            self.incoming = self._swap()
            self._swapped = True
        return self.elapsed >= self.duration

    @property
    def progress(self) -> float:
        """Return elapsed transition time normalized to the range 0–1."""
        return self.elapsed / self.duration

    @property
    @abstractmethod
    def overlay_color(self) -> Color:
        """Return the current RGBA overlay color; implement in a subclass."""
        pass


class FadeTransition(Transition):
    """Fade an overlay in and out while switching scenes at full coverage."""

    def __init__(
        self,
        duration: float = 0.5,
        color: Color = (0, 0, 0),
        *,
        block_input: bool = True,
    ) -> None:
        """Create a fade transition.

        Args:
            duration (float): Total transition duration in seconds.
            color (Color): Overlay RGB or RGBA color.
            block_input (bool): Whether scene input is blocked during the transition.
        """
        super().__init__(duration, block_input=block_input)
        self.color = color

    @property
    def overlay_color(self) -> Color:
        """Return the configured color with alpha faded by transition progress."""
        alpha = round(
            255
            * (
                self.progress * 2
                if self.progress <= 0.5
                else (1 - self.progress) * 2
            )
        )
        rgb = self.color[:3]
        base_alpha = self.color[3] if len(self.color) == 4 else 255
        return *rgb, round(alpha * base_alpha / 255)


class SceneManager:
    """Registers scenes and controls switching, lifecycle, and transitions."""

    def __init__(self, game: Game, default_scene: Scene) -> None:
        """Register and enter the initial scene."""
        self.game = game
        self._scenes: dict[str, Scene] = {}
        self._transition: Transition | None = None
        self._updating = False
        self._pending_scene_action: Callable[[], None] | None = None
        self.add("default", default_scene)
        self._current = default_scene
        self._enter(default_scene, None, shown=True)

    @property
    def current(self) -> Scene:
        """Return the active scene."""
        return self._current

    @property
    def transition(self) -> Transition | None:
        """Return the active transition, if any."""
        return self._transition

    @property
    def input_blocked(self) -> bool:
        """Whether the current transition blocks game input."""
        return bool(self._transition and self._transition.block_input)

    def add(self, name: str, scene: SceneType) -> SceneType:
        """Register a unique scene name and bind the scene to this game.

        Returns:
            SceneType: The same scene instance.

        Raises:
            ValueError: If the name is empty/duplicated or scene already registered.
        """
        if not name:
            raise ValueError("scene name must not be empty")
        if name in self._scenes:
            raise ValueError(f"scene already exists: {name!r}")
        if scene in self._scenes.values():
            raise ValueError("scene is already registered")
        scene._bind(self.game)
        self._scenes[name] = scene
        return scene

    def load(
        self,
        name: str,
        filename: str,
        *,
        asset_root: str | None = None,
        registry=None,
    ) -> Scene:
        """Load a serialized scene, register it under ``name``, and return it.

        Args:
            name (str): Unique name for the loaded scene.
            filename (str): Path to the scene document.
            asset_root (str | None): Base directory used to resolve assets.
            registry (SceneTypeRegistry | None): Optional custom type registry.

        Returns:
            Scene: The registered scene.

        Raises:
            ValueError: If the name is empty, duplicated, or the scene cannot
                be registered.
        """
        from .persistence import SceneSerializer

        scene = SceneSerializer(registry).load(
            self.game,
            filename,
            asset_root=asset_root,
        )
        return self.add(name, scene)

    def get(self, name: str) -> Scene:
        """Return a registered scene by name.

        Raises:
            ValueError: If no scene has that name.
        """
        try:
            return self._scenes[name]
        except KeyError:
            raise ValueError(f"unknown scene: {name!r}") from None

    def _request_scene_action(self, action: Callable[[], None]) -> None:
        if self._transition is not None:
            raise RuntimeError("cannot restart or restore during a scene transition")
        if self._pending_scene_action is not None:
            raise RuntimeError("a scene operation is already queued")
        if self._updating:
            self._pending_scene_action = action
        else:
            action()

    def _replace_current(self, scene: Scene) -> None:
        outgoing = self.current
        name = next(name for name, value in self._scenes.items() if value is outgoing)
        scene._bind(self.game)
        try:
            self._switch_now(scene, shown=True)
        except Exception as error:
            self._current = outgoing
            try:
                scene._unload()
                scene.layers._clear()
            except Exception as cleanup_error:
                raise ExceptionGroup("Scene replacement and cleanup failed", [error, cleanup_error]) from error
            raise
        self._scenes[name] = scene
        try:
            outgoing._unload()
        finally:
            outgoing.layers._clear()
            self.game.input._reset()

    def restart(self) -> None:
        """Reload the original scene design, after the update if called in gameplay."""
        from .persistence import SceneSerializer

        current = self.current
        if current._source_document is None:
            raise RuntimeError("restart requires a scene loaded by SceneSerializer")
        original = deepcopy(current._source_document)
        root = Path(self.game.resources.asset_root)
        registry = current._source_registry
        self._request_scene_action(
            lambda: self._replace_current(SceneSerializer(registry).restore(
                self.game, original, asset_root=root,
            ))
        )

    def checkpoint_path(
        self, slot: str = "checkpoint", *, directory: str | Path | None = None,
        application_name: str | None = None,
    ) -> Path:
        """Return the resolved checkpoint path for a save slot.

        Args:
            slot (str): ASCII letters, digits, hyphens, or underscores.
            directory (str | Path | None): Explicit save directory; otherwise an app data path is used.
            application_name (str | None): App-data directory name when ``directory`` is omitted.

        Raises:
            ValueError: If the slot or application name is unsafe.
        """
        if not slot or not all(c.isascii() and (c.isalnum() or c in "-_") for c in slot):
            raise ValueError("save slot must contain only ASCII letters, numbers, '-' or '_'")
        if directory is None:
            name = application_name or (
                self.game.project.export.name if self.game.project is not None else None
            )
            if not name or name in {".", ".."} or "/" in name or "\\" in name:
                raise ValueError("provide a safe application_name or checkpoint directory")
            directory = Path(pyglet.resource.get_settings_path(name)) / "saves"
        return Path(directory).expanduser().resolve() / f"{slot}.scene.json"

    def save_checkpoint(
        self, slot: str = "checkpoint", *, directory: str | Path | None = None,
        application_name: str | None = None,
    ) -> Path:
        """Save the active scene and runtime state to a checkpoint.

        When called during an update, writing is deferred until that update ends.
        """
        from .persistence import SceneSerializer

        path = self.checkpoint_path(slot, directory=directory, application_name=application_name)
        def save() -> None:
            SceneSerializer(self.current._source_registry).save(
                self.current, path, asset_root=self.game.resources.asset_root,
                include_runtime_state=True,
            )
        self._request_scene_action(save)
        return path

    def load_checkpoint(
        self, slot: str = "checkpoint", *, directory: str | Path | None = None,
        application_name: str | None = None,
    ) -> None:
        """Restore a checkpoint into the active scene.

        When called during an update, restoration is deferred until that update
        ends. The checkpoint must have been created by :meth:`save_checkpoint`.
        """
        from .persistence import SceneDocument, SceneFormatError, SceneSerializer

        path = self.checkpoint_path(slot, directory=directory, application_name=application_name)
        def load() -> None:
            document = SceneDocument.read(path)
            if "checkpoint" not in document.settings:
                raise SceneFormatError("save file is not a runtime checkpoint")
            scene = SceneSerializer(self.current._source_registry).restore(
                self.game, document, asset_root=self.game.resources.asset_root,
            )
            self._replace_current(scene)
        self._request_scene_action(load)

    def remove(self, name: str) -> Scene:
        """Unregister and unload an inactive scene.

        Raises:
            ValueError: If ``name`` refers to the active scene.
        """
        scene = self.get(name)
        if scene is self._current:
            raise ValueError("cannot remove an active scene")
        del self._scenes[name]
        scene._unload()
        return scene

    def switch(
        self,
        name: str,
        *,
        transition: Transition | None = None,
    ) -> Scene:
        """Switch to a registered scene, optionally with a transition.

        Returns:
            Scene: The target scene. With a transition, it becomes current at the
            transition midpoint.

        Raises:
            ValueError: If the target is already the current scene.
            RuntimeError: If another transition is active.
        """
        target = self.get(name)
        if target is self.current:
            raise ValueError("scene is already current")
        if transition is None:
            return self._switch_now(target, shown=True)
        self._begin_transition(
            transition,
            lambda: self._switch_now(target, shown=False),
        )
        return target

    def _begin_transition(
        self,
        transition: Transition,
        swap: Callable[[], Scene],
    ) -> None:
        if self._transition is not None:
            raise RuntimeError("a scene transition is already active")
        self._transition = transition
        transition._start(swap)
        if transition.block_input:
            self.game.input._reset()

    def _switch_now(self, target: Scene, *, shown: bool) -> Scene:
        outgoing = self._current
        outgoing.on_exit(target)
        self._current = target
        self._enter(target, outgoing, shown=shown)
        return target

    def _enter(
        self,
        scene: Scene,
        previous: Scene | None,
        *,
        shown: bool,
    ) -> None:
        scene._load()
        scene.on_enter(previous)
        if shown:
            scene.on_shown()

    def _update(self, delta_time: float) -> None:
        if self._transition is not None:
            transition = self._transition
            if transition._update(delta_time):
                self._transition = None
                if transition.incoming is not None:
                    transition.incoming.on_shown()
        self._updating = True
        try:
            self._current._update(delta_time)
        except BaseException:
            self._pending_scene_action = None
            raise
        finally:
            self._updating = False
        action = self._pending_scene_action
        self._pending_scene_action = None
        if action is not None:
            action()
