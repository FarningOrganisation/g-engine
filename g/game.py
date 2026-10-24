from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from os import PathLike
from pathlib import Path
from typing import TypeAlias, TypeVar

import pyglet

from .components import ShapeStyle, SpriteAnimation
from .input import Input, Key, Modifiers, MouseButton
from .nodes import GameObject
from .objects import StatefulGameObject
from .project import ProjectConfig
from .presentation import PresentationSettings, ScalingMode
from .resources import (
    Color,
    Font,
    Image,
    ImageSource,
    ResourceBundle,
    ResourceManager,
    TextureFilter,
)
from .math import Vec2
from .network import NetworkSession
from .runtime import NullBatch, NullDrawable, set_presentation_enabled
from .scene import LayerSelector, Scene, SceneManager
from .timing import SceneTime


KeyHandler: TypeAlias = Callable[[Key, Modifiers], None]
PressedKeyHandler: TypeAlias = Callable[[Key, Modifiers, float], None]
MouseMoveHandler: TypeAlias = Callable[[float, float, float, float], None]
MouseButtonHandler: TypeAlias = Callable[
    [float, float, MouseButton, Modifiers], None
]
MouseDragHandler: TypeAlias = Callable[
    [float, float, float, float, MouseButton, Modifiers], None
]
MouseScrollHandler: TypeAlias = Callable[[float, float, float, float], None]
LoadingProgressHandler: TypeAlias = Callable[[float], None]
LoadingCompleteHandler: TypeAlias = Callable[[], None]
GameObjectType = TypeVar("GameObjectType", bound=GameObject)


class Game:
    """Owns the active scene, resources, input, and game-loop lifecycle.

    Coordinates and dimensions use virtual pixels with the origin at the
    bottom-left. Convenience methods forward to the current scene.

    Attributes:
        width: Virtual canvas width in pixels.
        height: Virtual canvas height in pixels.
        input: Keyboard and mouse state shared with the active scene.
        resources: Resource manager used by scenes and components.
        network: Network session associated with this game.
        scenes: Scene registry and active-scene manager.
        presentation: Output scaling and window settings.
        project: Loaded project configuration, if any.
    """

    def __init__(
        self,
        width: int,
        height: int,
        bg_color: Color = (0, 0, 0),
        *,
        grid_cell_size: int = 32,
        scaling: ScalingMode = "fit",
        matte_color: Color = (0, 0, 0, 255),
        resizable: bool = True,
        fullscreen: bool = False,
        asset_root: str | Path | None = None,
        texture_filter: TextureFilter = TextureFilter.LINEAR,
        presentation_enabled: bool = True,
    ) -> None:
        """Create a game with a virtual canvas and an initial empty scene.

        Args:
            width (int): Virtual canvas width in pixels.
            height (int): Virtual canvas height in pixels.
            bg_color (Color): Initial scene background color.
            grid_cell_size (int): Default grid cell size in pixels.
            scaling (ScalingMode): How the virtual canvas fits the output window.
            matte_color (Color): Color outside the presented canvas.
            resizable (bool): Whether the window may be resized by default.
            fullscreen (bool): Whether the window starts fullscreen by default.
            asset_root (str | Path | None): Root directory used to resolve relative assets.
            texture_filter (TextureFilter): Default image scaling filter.
            presentation_enabled (bool): Whether graphics presentation is enabled.

        Raises:
            ValueError: If either canvas dimension is not positive.
        """
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        self.width = width
        self.height = height
        self.bg_color = bg_color
        self.default_grid_cell_size = grid_cell_size
        self.presentation_enabled = presentation_enabled
        set_presentation_enabled(presentation_enabled)
        self.presentation = PresentationSettings(
            scaling,
            matte_color,
            resizable,
            fullscreen,
        )
        self.input = Input()
        self.resources = ResourceManager(
            asset_root,
            texture_filter=texture_filter,
            presentation_enabled=presentation_enabled,
        )
        self.network = NetworkSession(self)
        self.project: ProjectConfig | None = None
        self._started = False
        self._loading_bundle: ResourceBundle | None = None
        self._loading_progress_handler: LoadingProgressHandler | None = None
        self._loading_complete_handler: LoadingCompleteHandler | None = None
        self._key_down_handlers: list[KeyHandler] = []
        self._key_pressed_handlers: list[PressedKeyHandler] = []
        self._key_up_handlers: list[KeyHandler] = []
        self._mouse_move_handlers: list[MouseMoveHandler] = []
        self._mouse_down_handlers: list[MouseButtonHandler] = []
        self._mouse_up_handlers: list[MouseButtonHandler] = []
        self._mouse_drag_handlers: list[MouseDragHandler] = []
        self._mouse_scroll_handlers: list[MouseScrollHandler] = []
        self.scenes = SceneManager(
            self,
            Scene(
                bg_color=bg_color,
                grid_cell_size=grid_cell_size,
                presentation_enabled=presentation_enabled,
            ),
        )
        if presentation_enabled:
            transition_batch = pyglet.graphics.Batch()
            self._transition_batch = transition_batch
            self._transition_overlay = pyglet.shapes.Rectangle(
                0, 0, width, height, color=(0, 0, 0),
                batch=transition_batch,
            )
        else:
            self._transition_batch = NullBatch()
            self._transition_overlay = NullDrawable()
        self._transition_overlay.visible = False

    @property
    def pixel_width(self) -> int:
        """Return the virtual canvas width in pixels."""
        return self.width

    @property
    def pixel_height(self) -> int:
        """Return the virtual canvas height in pixels."""
        return self.height

    @classmethod
    def from_grid(
        cls,
        columns: int,
        rows: int,
        cell_size: int = 32,
        bg_color: Color = (0, 0, 0),
    ) -> Game:
        """Create a game whose virtual dimensions are measured in grid cells.

        Args:
            columns (int): Number of grid columns.
            rows (int): Number of grid rows.
            cell_size (int): Cell width and height in pixels.
            bg_color (Color): Initial scene background color.

        Returns:
            Game: A game with canvas size ``columns * cell_size`` by
            ``rows * cell_size``.

        Raises:
            ValueError: If a dimension or the cell size is not positive.
        """
        if columns <= 0 or rows <= 0 or cell_size <= 0:
            raise ValueError("columns, rows and cell_size must be positive")
        return cls(
            columns * cell_size,
            rows * cell_size,
            bg_color,
            grid_cell_size=cell_size,
        )

    @classmethod
    def from_project(
        cls,
        filename: str | Path = "g.project.json",
        *,
        network_role: str | None = None,
    ) -> Game:
        """Create and configure a game from a project file.

        Args:
            filename (str | Path): Project JSON path.
            network_role (str | None): Optional role override (for example, ``"server"``).

        Returns:
            Game: The configured game.
        """
        project = ProjectConfig.read(filename)
        project.import_scripts()
        game = cls(
            project.width,
            project.height,
            grid_cell_size=project.grid_cell_size,
            scaling=project.scaling,
            matte_color=project.matte_color,
            resizable=project.resizable,
            fullscreen=project.fullscreen,
            asset_root=project.asset_root,
            presentation_enabled=network_role != "server",
        )
        game.project = project
        game.network.configure(
            (
                network_role
                if network_role is not None
                else "client" if project.network.enabled else "offline"
            ),
            snapshot_hz=project.network.snapshot_hz,
            max_clients=project.network.max_clients,
        )
        return game

    def load_project(
        self,
        filename: str | Path = "g.project.json",
    ) -> ProjectConfig:
        """Load project settings and assets into this game.

        Args:
            filename (str | Path): Project JSON path.

        Returns:
            ProjectConfig: The loaded project configuration.
        """
        project = ProjectConfig.read(filename)
        project.import_scripts()
        self.project = project
        self.resources.asset_root = project.asset_root
        self.presentation = PresentationSettings(
            project.scaling,
            project.matte_color,
            project.resizable,
            project.fullscreen,
        )
        self.network.configure(
            "client" if project.network.enabled else "offline",
            snapshot_hz=project.network.snapshot_hz,
            max_clients=project.network.max_clients,
        )
        return project

    @property
    def root(self):
        """Return the current scene's default object-layer root.

        Returns:
            (Node): Root node for objects on the default layer.
        """
        return self.scenes.current.root

    @property
    def grid(self):
        """Return the current scene's grid, in virtual-pixel coordinates.

        Returns:
            (Grid): Grid helper for the current scene.
        """
        return self.scenes.current.grid

    @property
    def navigation(self):
        """Return the current scene's navigation world.

        Returns:
            (NavigationWorld): Navigation system for the current scene.
        """
        return self.scenes.current.navigation

    @property
    def physics(self):
        """Return the current scene's physics world.

        Returns:
            (PhysicsWorld): Physics system for the current scene.
        """
        return self.scenes.current.physics

    @property
    def time(self) -> SceneTime:
        """Return the current scene's clock and timer scheduler."""
        return self.scenes.current.time

    @property
    def camera(self):
        """Return the current scene's camera.

        Returns:
            (Camera): Camera for the current scene.
        """
        return self.scenes.current.camera

    @property
    def _batch(self):
        return self.scenes.current._batch

    @property
    def _ui_batch(self):
        return self.scenes.current._ui_batch

    @property
    def objects(self) -> tuple[GameObject, ...]:
        """Return top-level game objects from all object layers."""
        return self.scenes.current.objects

    def add(
        self,
        game_object: GameObjectType,
        *,
        layer: LayerSelector | None = None,
        z_index: int | None = None,
    ) -> GameObjectType:
        """Attach an existing object to the current scene.

        Args:
            game_object (GameObjectType): Detached object to attach.
            layer (LayerSelector | None): Object layer name or instance; ``None`` selects the default.
            z_index (int | None): Optional drawing order within its layer.

        Returns:
            GameObjectType: The same object, after forwarding attachment to the scene.

        Raises:
            ValueError: If the object is already attached or the layer is invalid.
        """
        return self.scenes.current.add(
            game_object,
            layer=layer,
            z_index=z_index,
        )

    def add_circle(
        self,
        x: float,
        y: float,
        radius: float,
        color: Color = (255, 255, 255),
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        style: ShapeStyle | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create and add a circle centered at ``(x, y)`` in virtual pixels.

        Args:
            radius: Circle radius in virtual pixels.
            color: Fill color; defaults to white.
        """
        return self.scenes.current.add_circle(
            x,
            y,
            radius,
            color,
            layer,
            z_index=z_index,
            style=style,
            collider=collider,
        )

    def add_rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        color: Color = (255, 255, 255),
        angle: float = 0,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
        style: ShapeStyle | None = None,
        collider: bool = False,
    ) -> GameObject:
        """Create and add a rectangle centered at ``(x, y)`` in virtual pixels.

        ``angle`` is measured in degrees. ``color`` defaults to white.
        """
        return self.scenes.current.add_rect(
            x,
            y,
            width,
            height,
            color,
            angle,
            layer,
            z_index=z_index,
            style=style,
            collider=collider,
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
        """Create and add a sprite centered at ``(x, y)`` in virtual pixels.

        ``angle`` is measured in degrees. The image may be an animation source.
        """
        return self.scenes.current.add_sprite(
            x,
            y,
            image,
            angle,
            layer,
            z_index=z_index,
            filtering=filtering,
            collider=collider,
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
        alignment: str = "center",
    ) -> GameObject:
        """Create and add text anchored at ``(x, y)`` in virtual pixels.

        Args:
            screen_space (bool): If true, render independently of the camera.
            alignment (str): Horizontal alignment around ``(x, y)``:
                ``"left"``, ``"center"``, or ``"right"``.

        Returns:
            GameObject: The created text object.
        """
        return self.scenes.current.add_text(
            x,
            y,
            text,
            font,
            font_size,
            color,
            angle,
            layer,
            z_index=z_index,
            screen_space=screen_space,
            alignment=alignment,
        )

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
        """Create an object containing an omnidirectional point light."""
        return self.scenes.current.add_point_light(
            x,
            y,
            radius,
            color=color,
            intensity=intensity,
            falloff=falloff,
            casts_shadows=casts_shadows,
            layer=layer,
        )

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
        """Create an object containing a directed spotlight."""
        return self.scenes.current.add_spot_light(
            x,
            y,
            range,
            angle=angle,
            inner_angle=inner_angle,
            color=color,
            intensity=intensity,
            falloff=falloff,
            casts_shadows=casts_shadows,
            rotation=rotation,
            layer=layer,
        )

    def add_stateful_game_object(
        self,
        states: Mapping[str, GameObject],
        initial_state: str,
        layer: LayerSelector | int | None = None,
        *,
        z_index: int | None = None,
    ) -> StatefulGameObject:
        """Create and add an object that switches among supplied visuals."""
        return self.scenes.current.add_stateful_game_object(
            states,
            initial_state,
            layer,
            z_index=z_index,
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
        """Create and add an animated sprite with named visual states."""
        return self.scenes.current.add_stateful_sprite(
            x,
            y,
            states,
            initial_state,
            angle,
            layer,
            z_index=z_index,
            collider=collider,
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
        """Create and add a line between two virtual-pixel positions."""
        return self.scenes.current.add_line(
            start,
            end,
            color,
            thickness,
            layer,
            z_index=z_index,
            screen_space=screen_space,
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
        """Create and add a triangle using three virtual-pixel vertices."""
        return self.scenes.current.add_triangle(
            first,
            second,
            third,
            color,
            layer,
            z_index=z_index,
            style=style,
            screen_space=screen_space,
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
        """Create and add a polygon from virtual-pixel vertices."""
        return self.scenes.current.add_polygon(
            points,
            color,
            layer,
            z_index=z_index,
            style=style,
            screen_space=screen_space,
        )

    def add_area_box(
        self, x: float, y: float, width: float, height: float,
        layer: LayerSelector | int | None = None, *,
        rotation: float = 0, enabled: bool = True, z_index: int | None = None,
    ) -> GameObject:
        """Create and add a sensor box centered at ``(x, y)`` in pixels."""
        return self.scenes.current.add_area_box(
            x, y, width, height, layer, rotation=rotation, enabled=enabled, z_index=z_index,
        )

    def add_background(
        self, image: Image | None = None, layer: LayerSelector | None = None, *,
        z_index: int = 0, tile_size: Vec2 | tuple[float, float] | None = None,
        repeat_x: bool = True, repeat_y: bool = True,
        offset: Vec2 | tuple[float, float] = (0, 0),
        parallax: Vec2 | tuple[float, float] = (0, 0),
        scroll_speed: Vec2 | tuple[float, float] = (0, 0),
        opacity: int = 255,
    ) -> GameObject:
        """Create and add a repeated, scrolling background image.

        With no layer specified, the scene's background object layer is used.
        """
        return self.scenes.current.add_background(
            image, layer, z_index=z_index, tile_size=tile_size,
            repeat_x=repeat_x, repeat_y=repeat_y, offset=offset, parallax=parallax,
            scroll_speed=scroll_speed, opacity=opacity,
        )

    def add_area_circle(
        self, x: float, y: float, radius: float,
        layer: LayerSelector | int | None = None, *,
        enabled: bool = True, z_index: int | None = None,
    ) -> GameObject:
        """Create and add a circular sensor centered at ``(x, y)`` in pixels."""
        return self.scenes.current.add_area_circle(
            x, y, radius, layer, enabled=enabled, z_index=z_index,
        )

    def remove(self, game_object: GameObject) -> None:
        """Compatibility alias that permanently deletes an object subtree.

        Raises:
            ValueError: If the object does not belong to the current scene.
        """
        self.scenes.current.delete(game_object)

    def delete(self, game_object: GameObject) -> None:
        """Permanently delete an object subtree from the current scene."""
        self.scenes.current.delete(game_object)

    def detach(self, game_object: GameObject) -> GameObject:
        """Detach and return a reusable object subtree from the current scene."""
        return self.scenes.current.detach(game_object)

    def on_key_down(self, handler: KeyHandler) -> KeyHandler:
        """Register a callback for a key's initial press.

        The callback receives ``(key, modifiers)``; repeated key events do not
        trigger it. The handler is returned for decorator-style registration.
        """
        self._key_down_handlers.append(handler)
        return handler

    def on_key_pressed(self, handler: PressedKeyHandler) -> PressedKeyHandler:
        """Register a per-update callback for every held key.

        The callback receives ``(key, modifiers, delta_time)``; ``delta_time``
        is in seconds.
        """
        self._key_pressed_handlers.append(handler)
        return handler

    def on_key_up(self, handler: KeyHandler) -> KeyHandler:
        """Register a callback receiving ``(key, modifiers)`` on key release."""
        self._key_up_handlers.append(handler)
        return handler

    def on_mouse_move(self, handler: MouseMoveHandler) -> MouseMoveHandler:
        """Register a callback receiving ``(x, y, dx, dy)`` on pointer motion.

        Positions and deltas are in virtual pixels.
        """
        self._mouse_move_handlers.append(handler)
        return handler

    def on_mouse_down(self, handler: MouseButtonHandler) -> MouseButtonHandler:
        """Register a callback receiving ``(x, y, button, modifiers)`` on press."""
        self._mouse_down_handlers.append(handler)
        return handler

    def on_mouse_up(self, handler: MouseButtonHandler) -> MouseButtonHandler:
        """Register a callback receiving ``(x, y, button, modifiers)`` on release."""
        self._mouse_up_handlers.append(handler)
        return handler

    def on_mouse_drag(self, handler: MouseDragHandler) -> MouseDragHandler:
        """Register a drag callback receiving ``(x, y, dx, dy, buttons, modifiers)``."""
        self._mouse_drag_handlers.append(handler)
        return handler

    def on_mouse_scroll(
        self, handler: MouseScrollHandler
    ) -> MouseScrollHandler:
        """Register a scroll callback receiving ``(x, y, scroll_x, scroll_y)``."""
        self._mouse_scroll_handlers.append(handler)
        return handler

    def _prepare_subtree(self, node) -> None:
        self.scenes.current._prepare_subtree(node)

    def _update(self, delta_time: float) -> None:
        self.network._before_update()
        self.input._begin_frame()
        self.scenes._update(delta_time)
        self._dispatch_pressed_keys(delta_time)
        self.network._after_update(delta_time)

    @property
    def is_loading(self) -> bool:
        """Whether an asset bundle is currently being loaded."""
        return self._loading_bundle is not None

    def load_assets(
        self,
        bundle: ResourceBundle,
        *,
        on_progress: LoadingProgressHandler | None = None,
        on_complete: LoadingCompleteHandler | None = None,
    ) -> None:
        """Load a named asset bundle incrementally while drawing continues.

        Normal scene and user updates pause until loading completes. One asset
        is loaded before each rendered frame.

        Args:
            bundle: Bundle created by this game's resource manager.
            on_progress: Optional callback receiving progress from 0.0 to 1.0.
            on_complete: Optional callback invoked after all assets load.

        Raises:
            ValueError: Another bundle is loading or the bundle belongs to a
                different resource manager.
        """
        if self._loading_bundle is not None:
            raise ValueError("another asset bundle is already loading")
        if bundle._manager is not self.resources:
            raise ValueError(
                "asset bundle belongs to a different resource manager"
            )
        if on_progress is not None:
            on_progress(bundle.progress)
        if bundle.loaded:
            if on_complete is not None:
                on_complete()
            return
        self._loading_bundle = bundle
        self._loading_progress_handler = on_progress
        self._loading_complete_handler = on_complete

    def _advance_loading(self) -> bool:
        bundle = self._loading_bundle
        if bundle is None:
            return False
        bundle._load_next()
        progress_handler = self._loading_progress_handler
        if progress_handler is not None:
            progress_handler(bundle.progress)
        if bundle.loaded:
            complete_handler = self._loading_complete_handler
            self._loading_bundle = None
            self._loading_progress_handler = None
            self._loading_complete_handler = None
            if complete_handler is not None:
                complete_handler()
        return True

    def _draw(self) -> None:
        self.scenes.current._draw()

    def _draw_background(self) -> None:
        self.scenes.current._draw_background()

    def _draw_ui(self) -> None:
        self.scenes.current._draw_ui()

    def _draw_transition(self) -> None:
        transition = self.scenes.transition
        if transition is None:
            self._transition_overlay.visible = False
            return
        color = transition.overlay_color
        self._transition_overlay.color = color[:3]
        self._transition_overlay.opacity = color[3] if len(color) == 4 else 255
        self._transition_overlay.visible = True
        self._transition_batch.draw()

    def _start(self) -> None:
        if self._started:
            return
        self._started = True
        self.on_start()

    def _stop(self) -> None:
        if not self._started:
            return
        try:
            self.on_stop()
        finally:
            self.network.disconnect()
            self._started = False

    def on_start(self) -> None:
        """Hook called once when this game begins running; override as needed."""
        pass

    def on_stop(self) -> None:
        """Hook called when the game loop stops; override as needed."""
        pass

    def run(
        self,
        title: str = "Game",
        *,
        interval: float = 1 / 60,
        update: Callable[[float], None] | None = None,
        window_width: int | None = None,
        window_height: int | None = None,
        fullscreen: bool | None = None,
        resizable: bool | None = None,
    ) -> None:
        """Create a window and run the game loop until the window closes.

        The optional ``update`` callback runs after each scene update and
        receives the fixed simulation step in seconds.

        Args:
            title (str): Window title.
            interval (float): Fixed simulation step in seconds.
            update (Callable[[float], None] | None): Optional callback receiving each fixed step in seconds.
            window_width (int | None): Output window width; defaults to the virtual width.
            window_height (int | None): Output window height; defaults to the virtual height.
            fullscreen (bool | None): Overrides the configured fullscreen setting.
            resizable (bool | None): Overrides the configured resizable setting.
        """
        from .window import Window

        window = Window(
            title,
            width=window_width or self.width,
            height=window_height or self.height,
            resizable=(
                self.presentation.resizable
                if resizable is None
                else resizable
            ),
            fullscreen=(
                self.presentation.fullscreen
                if fullscreen is None
                else fullscreen
            ),
        )
        window.show_game(self)
        window.run(update, interval)

    def run_server(
        self,
        *,
        interval: float = 1 / 60,
    ) -> None:
        """Run the network server's fixed-step loop without a window.

        The network role must be ``"server"`` and the server must already be
        listening. ``interval`` is the simulation step in seconds.

        Raises:
            RuntimeError: If the network session is not a listening server.
            ValueError: If ``interval`` is not positive.
        """
        from time import monotonic, sleep

        from .timing import FixedStepAccumulator

        if not self.network.is_server:
            raise RuntimeError("run_server requires the server network role")
        if self.network.state != "listening":
            raise RuntimeError("network server must listen before running")
        if interval <= 0:
            raise ValueError("interval must be positive")
        accumulator = FixedStepAccumulator(interval)
        previous = monotonic()
        self._start()
        try:
            while self.network.state == "listening":
                current = monotonic()
                if self._advance_loading():
                    previous = current
                    sleep(min(interval / 4, 0.002))
                    continue
                accumulator.advance(current - previous, self._update)
                previous = current
                sleep(min(interval / 4, 0.002))
        except KeyboardInterrupt:
            pass
        finally:
            self._stop()

    def _handle_key_press(self, symbol: int, modifiers: int) -> None:
        if self.scenes.input_blocked:
            return
        if self.scenes.current.gui.on_key_press(symbol, modifiers):
            return
        key = Key(symbol)
        active_modifiers = Modifiers(modifiers)
        was_pressed = self.input.pressed(key)
        self.input._press_key(key, active_modifiers)
        if not was_pressed:
            for handler in self._key_down_handlers:
                handler(key, active_modifiers)

    def _handle_key_release(self, symbol: int, modifiers: int) -> None:
        if self.scenes.input_blocked:
            return
        if self.scenes.current.gui.on_key_release(symbol, modifiers):
            return
        key = Key(symbol)
        active_modifiers = Modifiers(modifiers)
        self.input._release_key(key)
        for handler in self._key_up_handlers:
            handler(key, active_modifiers)

    def _dispatch_pressed_keys(self, delta_time: float) -> None:
        if self.scenes.input_blocked:
            return
        for key, modifiers in tuple(self.input._keys.items()):
            for handler in self._key_pressed_handlers:
                handler(key, modifiers, delta_time)

    def _handle_mouse_motion(
        self, x: float, y: float, delta_x: float, delta_y: float
    ) -> None:
        if self.scenes.input_blocked:
            return
        self.input._move_mouse(x, y, delta_x, delta_y)
        if self.scenes.current.gui.on_mouse_motion(
            x, y, delta_x, delta_y
        ):
            return
        for handler in self._mouse_move_handlers:
            handler(x, y, delta_x, delta_y)

    def _handle_mouse_press(
        self, x: float, y: float, button: int, modifiers: int
    ) -> None:
        if self.scenes.input_blocked:
            return
        mouse_button = MouseButton(button)
        self.input._move_mouse(x, y, 0, 0)
        if self.scenes.current.gui.on_mouse_press(
            x, y, button, modifiers
        ):
            return
        self.input._press_mouse(mouse_button)
        for handler in self._mouse_down_handlers:
            handler(x, y, mouse_button, Modifiers(modifiers))

    def _handle_mouse_release(
        self, x: float, y: float, button: int, modifiers: int
    ) -> None:
        if self.scenes.input_blocked:
            return
        mouse_button = MouseButton(button)
        self.input._move_mouse(x, y, 0, 0)
        if self.scenes.current.gui.on_mouse_release(
            x, y, button, modifiers
        ):
            return
        self.input._release_mouse(mouse_button)
        for handler in self._mouse_up_handlers:
            handler(x, y, mouse_button, Modifiers(modifiers))

    def _handle_mouse_drag(
        self,
        x: float,
        y: float,
        delta_x: float,
        delta_y: float,
        buttons: int,
        modifiers: int,
    ) -> None:
        if self.scenes.input_blocked:
            return
        self.input._move_mouse(x, y, delta_x, delta_y)
        if self.scenes.current.gui.on_mouse_drag(
            x,
            y,
            delta_x,
            delta_y,
            buttons,
            modifiers,
        ):
            return
        for handler in self._mouse_drag_handlers:
            handler(
                x,
                y,
                delta_x,
                delta_y,
                MouseButton(buttons),
                Modifiers(modifiers),
            )

    def _handle_mouse_scroll(
        self, x: float, y: float, scroll_x: float, scroll_y: float
    ) -> None:
        if self.scenes.input_blocked:
            return
        self.input._move_mouse(x, y, 0, 0)
        if self.scenes.current.gui.on_mouse_scroll(
            x, y, scroll_x, scroll_y
        ):
            return
        self.input._scroll_mouse(scroll_x, scroll_y)
        for handler in self._mouse_scroll_handlers:
            handler(x, y, scroll_x, scroll_y)

    def _handle_text(self, text: str) -> None:
        if not self.scenes.input_blocked:
            self.scenes.current.gui.on_text(text)

    def _handle_text_motion(self, motion: int) -> None:
        if not self.scenes.input_blocked:
            self.scenes.current.gui.on_text_motion(motion)

    def _handle_text_motion_select(self, motion: int) -> None:
        if not self.scenes.input_blocked:
            self.scenes.current.gui.on_text_motion(motion, select=True)
