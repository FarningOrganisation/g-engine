from __future__ import annotations

import argparse
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from importlib import import_module
from io import TextIOBase
from math import ceil, cos, floor, hypot, radians, sin
import os
from pathlib import Path
import platform
from queue import Empty, Queue
import sys
import subprocess
from tempfile import TemporaryDirectory
from threading import RLock, Thread
from time import monotonic
import traceback
from typing import Any, TextIO, cast
import webbrowser

import pyglet
from pyglet import gl
from pyglet.image.buffer import Framebuffer
from pyglet.math import Mat4
from pyglet.window import key, mouse

from g import (
    Container,
    Collider,
    Game,
    GameObject,
    SceneRenderer,
    SceneSerializer,
    TileLayer,
    UIView,
    Widget,
)
from g.persistence import JsonValue, SceneFormatError, scene_types
from g.components import (
    describe_component_requirement,
    missing_component_requirements,
)
from g.export import ExportError, ExportResult, export_project
from g.project import (
    ProjectConfig,
    ProjectExportConfig,
    ProjectFormatError,
    ProjectImportError,
    ProjectNetworkConfig,
)
from g.presentation import ScalingMode, presentation_rect
from g.resources import Color, clamp_texture_edges
from g.timing import FixedStepAccumulator
from g.tilemap import TileCell, TileFlags
from g.validation import ValidationPurpose, validate_project
from imgui_bundle import (
    icons_fontawesome_6,
    imguizmo,  # pyright: ignore[reportMissingModuleSource]
    imgui,
    portable_file_dialogs,  # pyright: ignore[reportMissingModuleSource]
)
from imgui_bundle.python_backends import pyglet_backend
from imgui_bundle.python_backends import compute_fb_scale

from .panel_support import (
    ASSET_DRAG_PAYLOAD as _ASSET_DRAG_PAYLOAD,
    IMAGE_SUFFIXES as _IMAGE_SUFFIXES,
    AssetPickerTarget as _AssetPickerTarget,
    RenameTarget as _RenameTarget,
    TileCollisionDrag,
)
from .prefab import PREFAB_SUFFIX
from .preferences import (
    EditorPreferences,
    ThemePreference,
    editor_config_directory,
    load_editor_preferences,
    resolve_theme,
)
from .component_tools import ComponentToolsPanel
from .collider_edit import (
    apply_geometry,
    drag_values,
    edge_projection,
    geometry_values,
    handles as collider_handles,
    outline as collider_outline,
    validate_geometry,
)
from .panels import (
    AnimationPanel,
    AssetsPanel,
    InspectorPanel,
    LayersPanel,
    ParticlesPanel,
    TilemapPanel,
    TilePalettePanel,
    ToolsPanel,
)
from .state import (
    DEFAULT_SCENE_SIZE,
    EditorState,
    WorldSelectionClipboard,
    GUI_CONTAINER_TYPES,
    new_object_record,
    new_scene_document,
    position,
    set_position,
    walk_object_records,
)
from .trust import ProjectTrustStore
from .ui import (
    begin_panel,
    color as _color,
    configure_console_font,
    configure_fonts,
    configure_style,
    flag as _flag,
    icon_button,
)

__all__ = [
    "ImGuiEditor",
    "main",
    "new_object_record",
    "new_scene_document",
    "run_editor",
    "walk_object_records",
]

_DOCUMENTATION_URL = "https://farningorganisation.github.io/g-engine/"
_FARNING_URL = "https://www.farning.de"


@dataclass
class _TileEditDrag:
    layer: dict[str, JsonValue]
    tool: str
    start: tuple[int, int]
    current: tuple[int, int]
    revision: int
    layer_ids: tuple[str, ...] = ()
    tiles: dict[str, dict[tuple[int, int], TileCell]] = field(default_factory=dict)
    originals: dict[str, dict[tuple[int, int], TileCell | None]] = field(default_factory=dict)
    objects: dict[GameObject, tuple[float, float]] = field(default_factory=dict)


@dataclass
class _ColliderDrag:
    object_id: str
    component_index: int
    collider: Collider
    shape: str
    handle: str
    start: tuple[float, float]
    original: dict[str, JsonValue]
    latest: dict[str, JsonValue]
    revision: int

_SCRIPT_CHECK_INTERVAL = 0.5
_ASSET_CHECK_INTERVAL = 1.0
_MAX_LISTED_REFERENCES = 12
_ERROR_TEXT_COLOR = (1.0, 0.35, 0.3, 1.0)
_STDOUT_TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)


@dataclass(frozen=True)
class _EditorClipboard:
    kind: str
    records: tuple[dict[str, JsonValue], ...]
    origins: tuple[tuple[float, float], ...]
    viewport_click_serial: int
    source_selection_ids: tuple[str, ...]
    source_parent_identifier: str | None
    source_parent_identifiers: tuple[str | None, ...]


class _ConsoleBuffer:
    def __init__(self, maximum_characters: int = 200_000) -> None:
        self._maximum_characters = maximum_characters
        self._entries: deque[tuple[str, bool]] = deque()
        self._characters = 0
        self._lock = RLock()

    def write(self, text: str, *, error: bool = False) -> None:
        if not text:
            return
        with self._lock:
            if self._entries and self._entries[-1][1] == error:
                previous, _ = self._entries.pop()
                self._entries.append((previous + text, error))
            else:
                self._entries.append((text, error))
            self._characters += len(text)
            while self._characters > self._maximum_characters:
                first, first_error = self._entries.popleft()
                excess = self._characters - self._maximum_characters
                removed = min(len(first), excess)
                self._characters -= removed
                if removed < len(first):
                    self._entries.appendleft((first[removed:], first_error))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._characters = 0

    def snapshot(self) -> str:
        with self._lock:
            return "".join(text for text, _ in self._entries)

    def entries(self) -> tuple[tuple[str, bool], ...]:
        with self._lock:
            return tuple(self._entries)


class _FrameRateTracker:
    def __init__(self, window: float = 1.0) -> None:
        self._window = window
        self._timestamps: deque[float] = deque()

    @property
    def fps(self) -> float:
        if len(self._timestamps) < 2:
            return 0.0
        elapsed = self._timestamps[-1] - self._timestamps[0]
        return (
            (len(self._timestamps) - 1) / elapsed
            if elapsed > 0
            else 0.0
        )

    def record(self, timestamp: float) -> None:
        self._timestamps.append(timestamp)
        cutoff = timestamp - self._window
        while (
            len(self._timestamps) > 2
            and self._timestamps[1] < cutoff
        ):
            self._timestamps.popleft()

    def reset(self) -> None:
        self._timestamps.clear()


class _TeeStream(TextIOBase):
    def __init__(
        self,
        original: TextIO,
        console: _ConsoleBuffer,
        *,
        error: bool = False,
    ) -> None:
        super().__init__()
        self._original = original
        self._console = console
        self._error = error

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._console.write(text, error=self._error)
        return self._original.write(text)

    def flush(self) -> None:
        self._original.flush()

    def isatty(self) -> bool:
        return self._original.isatty()

    def fileno(self) -> int:
        return self._original.fileno()


class _MacSaveDialog:
    def __init__(
        self,
        title: str,
        directory: Path,
        filename: str,
    ) -> None:
        script = (
            "set chosenFile to choose file name"
            f" with prompt {_applescript_string(title)}"
            " default location "
            f"(POSIX file {_applescript_string(str(directory))})"
            f" default name {_applescript_string(filename)}\n"
            "POSIX path of chosenFile"
        )
        self._process = subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._result: str | None = None

    def ready(self, timeout: int = 0) -> bool:
        del timeout
        return self._process.poll() is not None

    def kill(self) -> bool:
        if self._process.poll() is not None:
            return False
        self._process.terminate()
        return True

    def result(self) -> str:
        if self._result is not None:
            return self._result
        stdout, _ = self._process.communicate()
        self._result = (
            stdout.strip()
            if self._process.returncode == 0
            else ""
        )
        return self._result


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class _RenderWindow:
    def __init__(
        self,
        window: Any,
        width: int,
        height: int,
    ) -> None:
        self.window = window
        self.width = width
        self.height = height

    @property
    def view(self) -> Any:
        return self.window.view

    @view.setter
    def view(self, matrix: Any) -> None:
        self.window.view = matrix

    def get_framebuffer_size(self) -> tuple[int, int]:
        return self.width, self.height

class SceneRenderTarget:
    def __init__(self, window: Any) -> None:
        self.window = window
        self.width = 1
        self.height = 1
        self.texture: pyglet.image.Texture | None = None
        self.framebuffer: Framebuffer | None = None
        self.ensure_size(1, 1)

    @property
    def texture_ref(self) -> Any:
        if self.texture is None:
            return imgui.ImTextureRef()
        texture_id = self.texture.id
        if texture_id is None:
            return imgui.ImTextureRef()
        return imgui.ImTextureRef(texture_id)

    def ensure_size(self, width: int, height: int) -> None:
        width = max(1, min(4096, int(width)))
        height = max(1, min(4096, int(height)))
        if (
            self.texture is not None
            and self.framebuffer is not None
            and (width, height) == (self.width, self.height)
        ):
            return
        self.delete()
        self.width = width
        self.height = height
        self.texture = pyglet.image.Texture.create(
            width,
            height,
            min_filter=gl.GL_LINEAR,
            mag_filter=gl.GL_LINEAR,
        )
        clamp_texture_edges(self.texture)
        self.framebuffer = Framebuffer()
        self.framebuffer.attach_texture(self.texture)
        if not self.framebuffer.is_complete:
            status = self.framebuffer.get_status()
            self.delete()
            raise RuntimeError(f"editor framebuffer is incomplete: {status}")

    def render(self, preview: EnginePreview) -> None:
        if self.framebuffer is None:
            raise RuntimeError("editor framebuffer is unavailable")
        previous_framebuffer = (gl.GLint * 1)()
        viewport = (gl.GLint * 4)()
        clear_color = (gl.GLfloat * 4)()
        previous_projection = self.window.projection
        gl.glGetIntegerv(
            gl.GL_FRAMEBUFFER_BINDING,
            previous_framebuffer,
        )
        gl.glGetIntegerv(gl.GL_VIEWPORT, viewport)
        gl.glGetFloatv(gl.GL_COLOR_CLEAR_VALUE, clear_color)
        try:
            self.framebuffer.bind()
            gl.glViewport(0, 0, self.width, self.height)
            self.window.projection = Mat4.orthogonal_projection(
                0,
                self.width,
                0,
                self.height,
                -8192,
                8192,
            )
            gl.glClearColor(0.055, 0.063, 0.082, 1)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            preview.render(
                _RenderWindow(self.window, self.width, self.height)
            )
        finally:
            gl.glBindFramebuffer(
                gl.GL_FRAMEBUFFER,
                previous_framebuffer[0],
            )
            gl.glViewport(*viewport)
            gl.glClearColor(*clear_color)
            self.window.projection = previous_projection

    def delete(self) -> None:
        if self.framebuffer is not None:
            self.framebuffer.delete()
            self.framebuffer = None
        if self.texture is not None:
            self.texture.delete()
            self.texture = None


class EnginePreview:
    def __init__(self, state: EditorState) -> None:
        self.state = state
        self.renderer = SceneRenderer()
        self.serializer = SceneSerializer()
        self.game: Game | None = None
        self.load_error: str | None = None
        self.document_revision = -1
        self._initialization_error: str | None = None
        self.mode = "edit"
        self._simulation_clock = FixedStepAccumulator()
        width, height = self._game_size()
        self.editor_camera = [width / 2, height / 2]
        self.editor_zoom = 1.0
        self.reload()

    def _game_size(self) -> tuple[int, int]:
        value = self.state.document.settings.get(
            "size",
            list(DEFAULT_SCENE_SIZE),
        )
        if (
            isinstance(value, list)
            and len(value) == 2
            and all(
                isinstance(item, int)
                and not isinstance(item, bool)
                and item > 0
                for item in value
            )
        ):
            return cast(tuple[int, int], tuple(value))
        return DEFAULT_SCENE_SIZE

    def reload(self) -> None:
        try:
            self._reload()
        except Exception as error:
            self.game = None
            self.load_error = f"{type(error).__name__}: {error}"
            self.state.error = self.load_error
            self.document_revision = self.state.revision
            print(f"Scene preview initialization failed: {self.load_error}", file=sys.stderr)
            traceback.print_exc()
        if self.game is not None and self.state.error == self._initialization_error:
            self.state.error = None
            self._initialization_error = None
        if self.game is None and self.mode == "play":
            play_error = self.load_error
            self.mode = "edit"
            self.state.runtime_mode = "edit"
            self.reload()
            self.state.error = play_error
            self._initialization_error = play_error
            self.state.status = "Could not start Play; see Console"

    def _reload(self) -> None:
        self._simulation_clock.reset()
        self.load_error = None
        old_game = self.game
        self.game = None
        if old_game is not None and old_game._started:
            old_game._stop()
        width, height = self._game_size()
        game = Game(width, height, asset_root=self.state.asset_root)
        try:
            document = self.state.document
            if self.mode == "edit":
                document = deepcopy(document)
                for layer in document.layers:
                    objects = layer.get("objects", [])
                    if not isinstance(objects, list):
                        continue
                    roots = [
                        record for record in objects if isinstance(record, dict)
                    ]
                    for record in walk_object_records(roots):
                        components = record.get("components")
                        if not isinstance(components, list):
                            continue
                        while True:
                            typed = [
                                (component, scene_types.component_type_for_name(
                                    str(component.get("type"))
                                ))
                                for component in components
                                if isinstance(component, dict)
                            ]
                            present = tuple(kind for _, kind in typed)
                            invalid = [
                                (component, kind, missing)
                                for component, kind in typed
                                if (missing := missing_component_requirements(
                                    kind, present, include_auto_add=False
                                ))
                            ]
                            if not invalid:
                                break
                            for component, kind, missing in invalid:
                                print(
                                    f"Edit preview warning ({record.get('name')}): "
                                    f"{kind.__name__} requires "
                                    f"{describe_component_requirement(missing[0])}; "
                                    "component omitted from edit preview only.",
                                    file=sys.stderr,
                                )
                                components.remove(component)
            scene = self.serializer.restore(
                game,
                document,
                asset_root=self.state.asset_root,
                allow_missing_resources=True,
                initialize_components=self.mode == "play",
            )
        except (OSError, SceneFormatError) as error:
            self.game = None
            self.load_error = str(error)
            self.state.error = self.load_error
            print(f"Scene preview failed to load: {error}", file=sys.stderr)
            traceback.print_exc()
            self.document_revision = self.state.revision
            return
        game.scenes.add("editor", scene)
        game.scenes.switch("editor")
        if self.mode == "edit" and self.state.selected_ui_view is not None:
            selected_view_id = self.state.selected_ui_view.get("id")
            selected_view = next(
                (
                    view
                    for view in scene.gui.views
                    if view.id == selected_view_id
                ),
                None,
            )
            if (
                selected_view is not None
                and selected_view is not scene.gui.current
            ):
                scene.gui.replace(selected_view.name)
        if self.mode == "play":
            try:
                game._start()
            except Exception as error:
                try:
                    game._stop()
                except Exception as cleanup_error:
                    raise ExceptionGroup(
                        "Preview startup and cleanup failed", [error, cleanup_error],
                    ) from error
                raise
        self.game = game
        self.document_revision = self.state.revision

    def synchronize(self) -> None:
        if self.document_revision != self.state.revision:
            self.reload()

    def set_mode(self, mode: str) -> None:
        if mode not in {"edit", "play"}:
            raise ValueError(f"unknown runtime mode: {mode!r}")
        if mode == self.mode:
            return
        self.mode = mode
        self.state.runtime_mode = mode
        self.reload()
        if self.mode != mode:
            return
        self.state.status = f"Preview mode: {mode.title()}"

    def update(self, delta_time: float) -> None:
        if self.game is None:
            return
        if self.mode == "play":
            self._simulation_clock.advance(
                max(delta_time, 0),
                self.game._update,
            )

    def render(self, window: _RenderWindow) -> None:
        if self.game is None:
            return
        camera = self.game.scenes.current.camera
        camera.viewport_width = window.width
        camera.viewport_height = window.height
        if self.mode == "edit":
            camera.position = (
                self.editor_camera[0],
                self.editor_camera[1],
            )
            camera.zoom = self.editor_zoom
            camera.rotation = 0
        scene = self.game.scenes.current
        backgrounds = [
            (
                scene._background,
                scene._background.width,
                scene._background.height,
            )
        ]
        try:
            for background, _, _ in backgrounds:
                background.width = window.width
                background.height = window.height
            self.renderer.render(self.game, window)
        finally:
            for background, width, height in backgrounds:
                background.width = width
                background.height = height

    def pick(self, x: float, y: float) -> str | None:
        if self.game is None:
            return None
        scene = self.game.scenes.current
        world = scene.camera.screen_to_world((x, y))
        candidates = [
            node
            for node in scene.walk()
            if isinstance(node, GameObject)
            and node.enabled_in_hierarchy
            and node.visible_in_hierarchy
            and node.layer is not None
            and node.layer.enabled
            and node.layer.visible
            and node.layer.id in {str(layer["id"]) for layer in self.state.selected_layers}
        ]
        layer_order = {
            layer.id: index
            for index, layer in enumerate(scene.layers)
        }
        candidates.sort(
            key=lambda item: (
                layer_order[item.layer.id] if item.layer is not None else -1,
                item.z_index,
            ),
            reverse=True,
        )
        for item in candidates:
            try:
                if item.contains_point(world.x, world.y):
                    return item.scene_id
            except RuntimeError:
                pass
            if self._renderer_contains(item, world.x, world.y):
                return item.scene_id
        return None

    def screen_to_world(self, x: float, y: float) -> tuple[float, float]:
        if self.game is None:
            return 0, 0
        value = self.game.scenes.current.camera.screen_to_world((x, y))
        return value.x, value.y

    def world_to_screen(self, x: float, y: float) -> tuple[float, float]:
        if self.game is None:
            return 0, 0
        value = self.game.scenes.current.camera.world_to_screen((x, y))
        return value.x, value.y

    def pan_pixels(self, delta_x: float, delta_y: float) -> None:
        self.editor_camera[0] -= delta_x / self.editor_zoom
        self.editor_camera[1] -= delta_y / self.editor_zoom
        if self.game is not None:
            self.game.scenes.current.camera.position = (
                self.editor_camera[0],
                self.editor_camera[1],
            )

    def zoom_at(self, factor: float, screen_x: float, screen_y: float) -> None:
        if self.game is None:
            return
        camera = self.game.scenes.current.camera
        before = camera.screen_to_world((screen_x, screen_y))
        self.editor_zoom = min(
            8.0,
            max(0.01, self.editor_zoom * factor),
        )
        camera.zoom = self.editor_zoom
        after = camera.screen_to_world((screen_x, screen_y))
        self.editor_camera[0] += before.x - after.x
        self.editor_camera[1] += before.y - after.y
        camera.position = (
            self.editor_camera[0],
            self.editor_camera[1],
        )

    def apply_selected_transform(self) -> None:
        target = self.selected_runtime_object()
        record = self.state.selected
        if target is None or record is None:
            return
        target.position = position(record)
        transform = record.get("transform")
        if isinstance(transform, dict):
            rotation = transform.get("rotation", 0)
            if isinstance(rotation, (int, float)) and not isinstance(
                rotation, bool
            ):
                target.rotation = float(rotation)
            scale = transform.get("scale", [1, 1])
            if (
                isinstance(scale, list)
                and len(scale) == 2
            ):
                scale_x, scale_y = scale
                if (
                    isinstance(scale_x, (int, float))
                    and not isinstance(scale_x, bool)
                    and isinstance(scale_y, (int, float))
                    and not isinstance(scale_y, bool)
                ):
                    target.scale_x = float(scale_x)
                    target.scale_y = float(scale_y)
        self.document_revision = self.state.revision

    def selected_runtime_object(self) -> GameObject | None:
        if self.game is None:
            return None
        record = self.state.selected
        if record is None:
            return None
        identifier = record.get("id")
        return next(
            (
                node
                for node in self.game.scenes.current.walk()
                if isinstance(node, GameObject)
                and node.scene_id == identifier
            ),
            None,
        )

    def selected_runtime_objects(
        self,
        *,
        top_level_only: bool = False,
    ) -> tuple[GameObject, ...]:
        if self.game is None:
            return ()
        selected = set(self.state.selected_ids)
        objects = tuple(
            node
            for node in self.game.scenes.current.walk()
            if isinstance(node, GameObject)
            and node.scene_id in selected
        )
        if not top_level_only:
            return objects
        return tuple(
            item
            for item in objects
            if not any(
                isinstance(ancestor, GameObject)
                and ancestor.scene_id in selected
                for ancestor in self._object_ancestors(item)
            )
        )

    @staticmethod
    def _object_ancestors(
        item: GameObject,
    ) -> tuple[GameObject, ...]:
        ancestors: list[GameObject] = []
        parent = item.parent
        while parent is not None:
            if isinstance(parent, GameObject):
                ancestors.append(parent)
            parent = parent.parent
        return tuple(ancestors)

    def selected_world_transform(
        self,
    ) -> tuple[float, float, float, float, float] | None:
        target = self.selected_runtime_object()
        if target is None:
            return None
        world = target.world_position
        scale_x, scale_y = target.world_scale
        return (
            world.x,
            world.y,
            target.world_rotation,
            scale_x,
            scale_y,
        )

    def selected_local_position_for_world(
        self,
        world_x: float,
        world_y: float,
    ) -> tuple[float, float]:
        target = self.selected_runtime_object()
        if target is None:
            return world_x, world_y
        return self._local_position_for_world(
            target,
            world_x,
            world_y,
        )

    @staticmethod
    def _local_position_for_world(
        target: GameObject,
        world_x: float,
        world_y: float,
    ) -> tuple[float, float]:
        if target.parent is None:
            return world_x, world_y
        parent = target.parent
        parent_x, parent_y = parent.world_position
        scale_x, scale_y = parent.world_scale
        if scale_x == 0 or scale_y == 0:
            raise ValueError(
                "cannot move an object through a parent with zero scale"
            )
        delta_x = world_x - parent_x
        delta_y = world_y - parent_y
        angle = radians(parent.world_rotation)
        cosine = cos(angle)
        sine = sin(angle)
        return (
            (delta_x * cosine - delta_y * sine) / scale_x,
            (delta_x * sine + delta_y * cosine) / scale_y,
        )

    def move_selected_objects_by_world(
        self,
        delta_x: float,
        delta_y: float,
    ) -> None:
        targets = self.selected_runtime_objects(top_level_only=True)
        if not targets or (delta_x == 0 and delta_y == 0):
            return
        records = {
            record.get("id"): record
            for record in self.state.selected_records
        }
        for target in targets:
            world = target.world_position
            local_x, local_y = self._local_position_for_world(
                target,
                world.x + delta_x,
                world.y + delta_y,
            )
            target.position = (local_x, local_y)
            record = records.get(target.scene_id)
            if record is not None:
                set_position(record, local_x, local_y)
        self.state.mark_changed("Moved selected objects")
        self.document_revision = self.state.revision

    def object_screen_bounds(
        self,
        item: GameObject,
    ) -> tuple[float, float, float, float]:
        try:
            left, bottom, right, top = item._world_bounds()
        except RuntimeError:
            world = item.world_position
            left = right = world.x
            bottom = top = world.y
            for renderer in item.renderers:
                drawable = renderer.drawable
                width = getattr(drawable, "width", None)
                height = getattr(drawable, "height", None)
                if not isinstance(width, (int, float)):
                    width = getattr(drawable, "content_width", None)
                if not isinstance(height, (int, float)):
                    height = getattr(drawable, "content_height", None)
                if isinstance(width, (int, float)) and isinstance(
                    height,
                    (int, float),
                ):
                    scale_x, scale_y = item.world_scale
                    half_width = abs(float(width) * scale_x) / 2
                    half_height = abs(float(height) * scale_y) / 2
                    left = min(left, world.x - half_width)
                    right = max(right, world.x + half_width)
                    bottom = min(bottom, world.y - half_height)
                    top = max(top, world.y + half_height)
        first = self.world_to_screen(left, bottom)
        second = self.world_to_screen(right, top)
        return (
            min(first[0], second[0]),
            min(first[1], second[1]),
            max(first[0], second[0]),
            max(first[1], second[1]),
        )

    def reveal_object_if_outside_viewport(
        self,
        identifier: str,
        viewport_width: float,
        viewport_height: float,
    ) -> bool:
        if self.game is None or viewport_width <= 0 or viewport_height <= 0:
            return False
        target = next(
            (
                node
                for node in self.game.scenes.current.walk()
                if isinstance(node, GameObject)
                and node.scene_id == identifier
            ),
            None,
        )
        if target is None:
            return False
        left, bottom, right, top = self.object_screen_bounds(target)
        if (
            right >= 0
            and left <= viewport_width
            and top >= 0
            and bottom <= viewport_height
        ):
            return False
        try:
            world_left, world_bottom, world_right, world_top = (
                target._world_bounds()
            )
            center_x = (world_left + world_right) / 2
            center_y = (world_bottom + world_top) / 2
        except RuntimeError:
            center_x, center_y = target.world_position
        self.editor_camera[:] = [center_x, center_y]
        self.game.scenes.current.camera.position = (
            center_x,
            center_y,
        )
        return True

    def objects_in_rect(
        self,
        left: float,
        bottom: float,
        right: float,
        top: float,
    ) -> list[str]:
        if self.game is None:
            return []
        selected_layer_ids = {str(layer["id"]) for layer in self.state.selected_layers}
        identifiers: list[str] = []
        for node in self.game.scenes.current.walk():
            if (
                not isinstance(node, GameObject)
                or node.scene_id is None
                or node.layer is None
                or node.layer.id not in selected_layer_ids
                or not node.visible_in_hierarchy
                or not node.enabled_in_hierarchy
            ):
                continue
            item_left, item_bottom, item_right, item_top = (
                self.object_screen_bounds(node)
            )
            if (
                item_right >= left
                and item_left <= right
                and item_top >= bottom
                and item_bottom <= top
            ):
                identifiers.append(node.scene_id)
        return identifiers

    def gui_element_bounds(
        self,
        identifier: str,
    ) -> tuple[float, float, float, float] | None:
        widget = self._gui_widget(identifier)
        return widget.bounds if widget is not None else None

    def gui_elements_in_rect(
        self,
        layer_identifier: str,
        left: float,
        bottom: float,
        right: float,
        top: float,
    ) -> list[str]:
        if self.game is None:
            return []
        view = self._ui_view(layer_identifier)
        if view is None:
            return []
        identifiers: list[str] = []

        def collect(widget: Widget) -> None:
            item_left, item_bottom, item_right, item_top = widget.bounds
            if (
                item_right >= left
                and item_left <= right
                and item_top >= bottom
                and item_bottom <= top
            ):
                identifiers.append(widget.id)
            if isinstance(widget, Container):
                for child in widget.children:
                    collect(child)

        for widget in view.root.children:
            collect(widget)
        return identifiers

    def pick_gui(
        self,
        layer_identifier: str,
        x: float,
        y: float,
    ) -> str | None:
        if self.game is None:
            return None
        view = self._ui_view(layer_identifier)
        if view is None:
            return None

        def pick(widget: Widget) -> Widget | None:
            if not widget.visible_in_hierarchy:
                return None
            if isinstance(widget, Container):
                for child in sorted(
                    widget.children,
                    key=lambda item: item.layer,
                    reverse=True,
                ):
                    target = pick(child)
                    if target is not None:
                        return target
            left, bottom, right, top = widget.bounds
            return (
                widget
                if left <= x <= right and bottom <= y <= top
                else None
            )

        for widget in sorted(
            view.root.children,
            key=lambda item: item.layer,
            reverse=True,
        ):
            target = pick(widget)
            if target is not None:
                return target.id
        return None

    def _gui_widget(self, identifier: str) -> Widget | None:
        if self.game is None:
            return None

        def find(widget: Widget) -> Widget | None:
            if widget.id == identifier:
                return widget
            if isinstance(widget, Container):
                for child in widget.children:
                    target = find(child)
                    if target is not None:
                        return target
            return None

        for view in self.game.scenes.current.gui.views:
            for widget in view.root.children:
                target = find(widget)
                if target is not None:
                    return target
        return None

    def _ui_view(self, identifier: str) -> UIView | None:
        if self.game is None:
            return None
        return next(
            (
                view
                for view in self.game.scenes.current.gui.views
                if view.id == identifier
            ),
            None,
        )

    def apply_tile(
        self,
        column: int,
        row: int,
        tile_id: int | None,
        *,
        flags: TileFlags = TileFlags.NONE,
    ) -> None:
        if self.game is None or self.state.selected_layer_id is None:
            return
        layer = self.game.scenes.current.layers[
            self.state.selected_layer_id
        ]
        if not isinstance(layer, TileLayer):
            return
        layer.set_tile(column, row, tile_id, flags=flags)
        self.document_revision = self.state.revision

    def forward_key(
        self, pressed: bool, symbol: int, modifiers: int
    ) -> None:
        if self.game is None or self.mode != "play":
            return
        if pressed:
            self.game._handle_key_press(symbol, modifiers)
        else:
            self.game._handle_key_release(symbol, modifiers)

    def forward_text(self, text: str) -> None:
        if self.game is not None and self.mode == "play":
            self.game._handle_text(text)

    def forward_text_motion(
        self,
        motion: int,
        *,
        select: bool = False,
    ) -> None:
        if self.game is None or self.mode != "play":
            return
        if select:
            self.game._handle_text_motion_select(motion)
        else:
            self.game._handle_text_motion(motion)

    def forward_mouse(
        self,
        event: str,
        x: float,
        y: float,
        button: int = 0,
        modifiers: int = 0,
        delta_x: float = 0,
        delta_y: float = 0,
    ) -> None:
        if self.game is None or self.mode != "play":
            return
        if event == "press":
            self.game._handle_mouse_press(x, y, button, modifiers)
        elif event == "release":
            self.game._handle_mouse_release(x, y, button, modifiers)
        elif event == "drag":
            self.game._handle_mouse_drag(
                x,
                y,
                delta_x,
                delta_y,
                button,
                modifiers,
            )
        elif event == "motion":
            self.game._handle_mouse_motion(x, y, delta_x, delta_y)

    def close(self) -> None:
        if self.game is not None and self.game._started:
            self.game._stop()

    @staticmethod
    def _renderer_contains(item: GameObject, x: float, y: float) -> bool:
        from g.components import TileRenderer
        from g.geometry import BoxGeometry, contains_point

        world_x, world_y = item.world_position
        scale_x, scale_y = item.world_scale
        for renderer in item.renderers:
            if isinstance(renderer, TileRenderer):
                if any(contains_point(BoxGeometry(quad), x, y) for quad in renderer.world_quads()):
                    return True
                continue
            drawable = renderer.drawable
            width = getattr(drawable, "width", None)
            height = getattr(drawable, "height", None)
            if not isinstance(width, (int, float)):
                width = getattr(drawable, "content_width", None)
            if not isinstance(height, (int, float)):
                height = getattr(drawable, "content_height", None)
            if isinstance(width, (int, float)) and isinstance(
                height, (int, float)
            ):
                half_width = abs(float(width) * scale_x) / 2
                half_height = abs(float(height) * scale_y) / 2
                if (
                    world_x - half_width <= x <= world_x + half_width
                    and world_y - half_height <= y <= world_y + half_height
                ):
                    return True
        return False


class ImGuiEditor(
    ComponentToolsPanel,
    AnimationPanel,
    ParticlesPanel,
    ToolsPanel,
    TilePalettePanel,
    LayersPanel,
    TilemapPanel,
    AssetsPanel,
    InspectorPanel,
):
    def __init__(
        self,
        filename: Path | None = None,
        asset_root: Path | None = None,
        modules: tuple[str, ...] = (),
        *,
        visible: bool = True,
        start_hidden: bool = False,
        preferences: EditorPreferences | None = None,
        preferences_path: Path | None = None,
    ) -> None:
        pyglet.options.dpi_scaling = "stretch"
        resolved_root = (asset_root or Path.cwd()).resolve()
        self.project = ProjectConfig.discover(resolved_root)
        self._trust_store = ProjectTrustStore()
        self._project_trust_pending = False
        if self.project is not None and self.project.script_modules:
            if self._trust_store.is_trusted(self.project.root):
                self.project.import_scripts()
            else:
                self._project_trust_pending = True
        self.state = EditorState(filename, asset_root, modules)
        if self.project is not None and not self._project_trust_pending:
            self.state.sync_script_components()
        self.window = pyglet.window.Window(
            width=1440,
            height=900,
            caption="g Editor",
            resizable=True,
            vsync=True,
            visible=visible and not start_hidden,
            file_drops=True,
        )
        self._closed = False
        self._script_reload_error: str | None = None
        self.window.push_handlers(self)
        imgui.create_context()
        configure_fonts()
        self._console_font = configure_console_font()
        self._preferences_path = (
            preferences_path
            if preferences_path is not None
            else (
                editor_config_directory() / "settings.json"
                if visible
                else None
            )
        )
        self._preferences = preferences or (
            load_editor_preferences(self._preferences_path)
            if self._preferences_path is not None
            else EditorPreferences(theme="dark")
        )
        self._theme_preference = self._preferences.theme
        self._theme = resolve_theme(self._theme_preference)
        configure_style(self._theme)
        io = imgui.get_io()
        io.config_flags |= _flag(imgui.ConfigFlags_.docking_enable)
        self._layout_file: Path | None = None
        if visible:
            config_directory = editor_config_directory()
            config_directory.mkdir(parents=True, exist_ok=True)
            self._layout_file = config_directory / "imgui.ini"
            io.set_ini_filename(str(self._layout_file))
        else:
            io.set_ini_filename("")
        self._dock_layout_pending = self._layout_file is None
        if (
            self._layout_file is not None
            and self._layout_file.is_file()
        ):
            try:
                layout_data = self._layout_file.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )
            except OSError as error:
                print(
                    f"Could not read editor layout; resetting it: {error}",
                    file=sys.stderr,
                )
                self._dock_layout_pending = True
            else:
                self._dock_layout_pending = (
                    "[Window][Layers]" not in layout_data
                    or "[Window][UI Views]" not in layout_data
                    or "[Window][Console]" not in layout_data
                    or "[Window][Tile Palette]" not in layout_data
                    or "[Window][Animation]" not in layout_data
                    or "[Window][Particles]" not in layout_data
                )
        elif self._layout_file is not None:
            self._dock_layout_pending = True
        self.imgui_renderer = pyglet_backend.create_renderer(self.window)
        self._configure_imgui_clipboard()
        self._console_buffer = _ConsoleBuffer()
        self._status_logged_error: str | None = None
        self._console_auto_scroll = True
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        self._console_stdout = _TeeStream(
            self._original_stdout,
            self._console_buffer,
        )
        self._console_stderr = _TeeStream(
            self._original_stderr,
            self._console_buffer,
            error=True,
        )
        sys.stdout = self._console_stdout
        sys.stderr = self._console_stderr
        self.preview = EnginePreview(self.state)
        self.render_target = SceneRenderTarget(self.window)
        self._requested_target_size = (900, 700)
        self._last_frame = monotonic()
        self._last_script_check = self._last_frame
        self._last_asset_check = self._last_frame
        self._frame_rate = _FrameRateTracker()
        self._viewport_rect = (0.0, 0.0, 0.0, 0.0)
        self._viewport_visible = False
        self._layer_content_rect = (0.0, 0.0, 0.0, 0.0)
        self._viewport_hovered = False
        self._pan_button: int | None = None
        self._right_pan_candidate = False
        self._right_pan_dragged = False
        self._right_pan_position: tuple[float, float] | None = None
        self._gui_drag_id: str | None = None
        self._space_pan_held = False
        self._shift_pick_held = False
        self._marquee_start: tuple[float, float] | None = None
        self._marquee_current: tuple[float, float] | None = None
        self._marquee_additive = False
        self._marquee_toggle = False
        self._palette_drag_start: int | None = None
        self._palette_zoom = 1.0
        self._tile_collision_drag: TileCollisionDrag | None = None
        self._tile_collision_target: tuple[str | None, int, str] | None = None
        self._tile_collision_point: tuple[int, int] | None = None
        self._tile_collision_add = False
        self._tile_fill_start: tuple[int, int] | None = None
        self._tile_pick_start: tuple[int, int] | None = None
        self._tile_pick_current: tuple[int, int] | None = None
        self._tile_pick_layer_id: str | None = None
        self._tile_edit_drag: _TileEditDrag | None = None
        self._pending_tileset_delete: str | None = None
        self._hierarchy_visible_orders: dict[str, list[str]] = {}
        self._hierarchy_keyboard_selection: str | None = None
        self._hierarchy_drag_identifier: str | None = None
        self._shortcut_context: str | None = None
        self._clipboard: _EditorClipboard | None = None
        self._world_clipboard: WorldSelectionClipboard | None = None
        self._world_clipboard_click_serial = 0
        self._world_paste_count = 0
        self._tile_conversion_name = "Platform"
        self._tile_conversion_destination = ""
        self._tile_conversion_new_layer = "Platforms"
        self._tile_conversion_collisions = False
        self._tile_conversion_revision = 0
        self._tile_conversion_selection: dict[str, dict[tuple[int, int], TileCell]] = {}
        self._component_clipboard: dict[str, JsonValue] | None = None
        self._component_open: dict[str, bool] = {}
        self._clipboard_paste_count = 0
        self._clipboard_hierarchy_parent: str | None = None
        self._clipboard_last_pasted_ids: tuple[str, ...] = ()
        self._viewport_click_serial = 0
        self._viewport_paste_position: tuple[float, float] | None = None
        self._transform_tool = "move"
        self._initialize_component_tools()
        self._collider_component_index: int | None = None
        self._collider_object_id: str | None = None
        self._collider_point: int | None = None
        self._collider_shape: str | None = None
        self._collider_drag: _ColliderDrag | None = None
        self._transform_space = "global"
        self._json_buffers: dict[str, str] = {}
        self._optional_color_values: dict[str, list[int]] = {}
        self._tile_config_buffers: dict[
            str,
            tuple[int, int, int, int],
        ] = {}
        self._path_dialog: str | None = None
        self._native_dialog: Any | None = None
        self._path_buffer = str(
            filename or self.state.asset_root / "scene.scene.json"
        )
        self._popup_to_open: str | None = None
        self._new_script_name = "Player"
        self._new_script_template = 0
        self._new_script_focus_pending = False
        self._new_script_object_id: str | None = None
        self._pending_script_attachment: tuple[Path, str] | None = None
        self._export_name = "Game"
        self._export_version = "0.1.0"
        self._export_startup_scene = ""
        self._export_server_startup_scene = ""
        self._export_icon = ""
        self._network_enabled = False
        self._network_endpoint = "ws://127.0.0.1:7777"
        self._network_bind = "127.0.0.1"
        self._network_port = 7777
        self._network_max_clients = 8
        self._network_snapshot_hz = 20
        self._network_auto_connect = True
        self._export_thread: Thread | None = None
        self._export_output: Queue[str] = Queue()
        self._export_completed: tuple[
            ExportResult | None,
            str | None,
            bool,
        ] | None = None
        self._pending_action: str | None = None
        self._after_save_action: str | None = None
        self._asset_files: tuple[Path, ...] = ()
        self._asset_scan: tuple[
            tuple[Path, ...],
            tuple[Path, ...],
        ] | None = None
        self._selected_asset: Path | None = None
        self._pending_asset_delete: tuple[
            Path,
            tuple[str, ...],
        ] | None = None
        self._selected_asset_directory = self.state.asset_root
        self._asset_clipboard: Path | None = None
        self._asset_trash = TemporaryDirectory(
            prefix=".g-editor-trash-",
            dir=self.state.asset_root,
        )
        self._asset_trash_root = Path(self._asset_trash.name)
        self._asset_directories: tuple[Path, ...] = ()
        self._asset_drag_path: Path | None = None
        self._asset_drag_paths: tuple[Path, ...] = ()
        self._selected_assets: set[Path] = set()
        self._asset_selection_anchor: Path | None = None
        self._initialize_animation_panel()
        self._initialize_particles_panel()
        self._asset_picker: _AssetPickerTarget | None = None
        self._asset_picker_keep_alive = False
        self._asset_field_button_rects: dict[
            str,
            tuple[float, float, float, float],
        ] = {}
        self._asset_field_targets: dict[str, _AssetPickerTarget] = {}
        self._asset_pointer_press: tuple[
            str,
            tuple[float, float, float, float],
        ] | None = None
        self._asset_item_pointer_press: tuple[
            Path,
            tuple[float, float, float, float],
        ] | None = None
        self._asset_item_rects: dict[
            Path,
            tuple[float, float, float, float],
        ] = {}
        self._assets_rect = (0.0, 0.0, 0.0, 0.0)
        self._rename_target: _RenameTarget | None = None
        self._rename_buffer = ""
        self._rename_focus_pending = False
        self._atlas_textures: dict[
            Path,
            tuple[int, pyglet.image.Texture],
        ] = {}
        self._refresh_assets()
        if self._project_trust_pending:
            self._popup_to_open = "Project Scripts"

    def _configure_imgui_clipboard(self) -> None:
        platform = imgui.get_platform_io()

        def get_clipboard_text(_context: Any) -> str:
            del _context
            return self.window.get_clipboard_text()

        def set_clipboard_text(_context: Any, text: str) -> None:
            del _context
            self.window.set_clipboard_text(text)

        platform.platform_get_clipboard_text_fn = get_clipboard_text
        platform.platform_set_clipboard_text_fn = set_clipboard_text

    def _register_project_script(self, path: Path) -> None:
        project = self.project or ProjectConfig.create(
            self.state.asset_root
        )
        try:
            module = project.module_for_path(path)
            previous = (
                project.path.read_bytes()
                if project.path.is_file()
                else None
            )
            updated = project.with_module(module)
            updated.write()
            current = updated.path.read_bytes()
        except (OSError, ProjectFormatError) as error:
            self.state.error = str(error)
            return

        def restore(contents: bytes | None) -> None:
            if contents is None:
                updated.path.unlink(missing_ok=True)
            else:
                updated.path.write_bytes(contents)
            self.project = ProjectConfig.discover(self.state.asset_root)
            self._refresh_assets()

        before = self.state.begin_history_change()
        self.project = updated
        self.state.commit_history_change(
            f"Registered project script {module}",
            before,
            undo_effect=lambda: restore(previous),
            redo_effect=lambda: restore(current),
        )
        try:
            if self._trust_store.is_trusted(updated.root):
                updated.import_scripts()
                self.preview.reload()
                self.state.status = f"Registered and loaded {module}"
            else:
                self._project_trust_pending = True
                self._popup_to_open = "Project Scripts"
                self.state.status = f"Registered project script {module}"
        except (ProjectFormatError, ProjectImportError) as error:
            self.state.error = str(error)
        self._refresh_assets()

    def _begin_create_python_script(
        self, *, attach_to_selected: bool = False
    ) -> None:
        self._new_script_name = "Player"
        self._new_script_template = 0
        self._new_script_object_id = (
            self.state.selected_id if attach_to_selected else None
        )
        self._new_script_focus_pending = True
        self._popup_to_open = "Create Python Script"

    def _attach_project_script(
        self,
        path: Path,
        object_identifier: str | None = None,
    ) -> None:
        project = self.project
        if project is None:
            self.state.error = "script is not registered in this project"
            return
        try:
            module = project.module_for_path(path)
            if module not in project.script_modules:
                raise ValueError(
                    "script is not registered in this project"
                )
            if not self._trust_store.is_trusted(project.root):
                raise ValueError(
                    "trust and load project scripts before attaching them"
                )
            project.import_scripts()
            component_types = [
                component_type
                for component_type in scene_types.editable_component_types
                if (
                    scene_types.component_type_for_name(
                        component_type
                    ).__module__
                    == module
                )
            ]
            if not component_types:
                raise ValueError(
                    f"{path.name} does not define an editor-attachable "
                    "scene component"
                )
            if len(component_types) > 1:
                raise ValueError(
                    f"{path.name} defines multiple scene components; "
                    "use Add Component to choose one"
                )
            if object_identifier is not None:
                self.state.select(object_identifier)
            component_type = next(iter(component_types))
            self.state.add_component(component_type)
            self.preview.reload()
            self.state.error = None
            self.state.status = (
                f"Added {component_type} from {path.name}"
            )
        except (
            OSError,
            ProjectFormatError,
            ProjectImportError,
            SceneFormatError,
            ValueError,
        ) as error:
            self.state.error = str(error)

    def _reload_changed_scripts(self) -> None:
        project = self.project
        if (
            project is None
            or self._project_trust_pending
            or not project.script_modules
            or not self._trust_store.is_trusted(project.root)
        ):
            return
        changed = project.changed_scripts()
        if not changed:
            return
        try:
            project.import_scripts()
        except (ProjectFormatError, ProjectImportError) as error:
            self._script_reload_error = str(error)
            self.state.error = self._script_reload_error
            print(f"Script reload failed: {error}", file=sys.stderr)
            return
        if self.state.error == self._script_reload_error:
            self.state.error = None
        self._script_reload_error = None
        self.state.sync_script_components()
        self.preview.reload()
        self.state.status = f"Reloaded {', '.join(changed)}"

    def _unregister_project_script(self, path: Path) -> None:
        project = self.project
        if project is None:
            return
        try:
            module = project.module_for_path(path)
            previous = project.path.read_bytes()
            updated = project.without_module(module)
            updated.write()
            current = updated.path.read_bytes()
        except (OSError, ProjectFormatError) as error:
            self.state.error = str(error)
            return

        def restore(contents: bytes) -> None:
            updated.path.write_bytes(contents)
            self.project = ProjectConfig.read(updated.path)
            self._refresh_assets()

        before = self.state.begin_history_change()
        self.project = updated
        self.state.commit_history_change(
            f"Unregistered project script {module}",
            before,
            undo_effect=lambda: restore(previous),
            redo_effect=lambda: restore(current),
        )
        self.state.status = (
            f"Unregistered {module}; restart the editor to unload it"
        )
        self.state.error = None
        self._refresh_assets()

    def run(self) -> int:
        pyglet.app.run()
        return 0

    def on_draw(self) -> None:
        if self._closed:
            return
        now = monotonic()
        delta_time = min(now - self._last_frame, 0.1)
        self._last_frame = now
        self._frame_rate.record(now)
        if (
            self.preview.mode == "edit"
            and now - self._last_script_check >= _SCRIPT_CHECK_INTERVAL
        ):
            self._last_script_check = now
            self._reload_changed_scripts()
        if now - self._last_asset_check >= _ASSET_CHECK_INTERVAL:
            self._last_asset_check = now
            self._poll_asset_changes()
        frame_started = False
        recovery_state: imgui.internal.ErrorRecoveryState | None = None
        try:
            self.render_target.ensure_size(*self._requested_target_size)
            self.preview.synchronize()
            self._validate_component_tool()
            self.preview.update(delta_time)
            self._animation_preview.update(delta_time)
            self._update_particles_preview(delta_time)
            self.imgui_renderer.process_inputs()
            io = imgui.get_io()
            window_size = self.window.get_size()
            io.display_size = imgui.ImVec2(*window_size)
            # The backend only initializes this ratio once, but monitors can differ.
            io.display_framebuffer_scale = imgui.ImVec2(
                *compute_fb_scale(window_size, self.window.get_framebuffer_size()),
            )
            imgui.new_frame()
            frame_started = True
            recovery_state = imgui.internal.ErrorRecoveryState()
            imgui.internal.error_recovery_store_state(recovery_state)
            imguizmo.im_guizmo.begin_frame()
            self._draw_interface()
            self.preview.synchronize()
            if self._viewport_visible:
                with self._animation_viewport_override():
                    self.render_target.render(self.preview)
            imgui.render()
            frame_started = False
            self.window.clear()
            self.imgui_renderer.render(imgui.get_draw_data())
        except Exception as error:
            self.state.error = str(error)
            print(f"Editor frame failed: {error}", file=sys.stderr)
            traceback.print_exc()
            if frame_started and recovery_state is not None:
                io = imgui.get_io()
                recovery_asserts = (
                    io.config_error_recovery_enable_assert
                )
                recovery_debug_log = (
                    io.config_error_recovery_enable_debug_log
                )
                recovery_tooltip = (
                    io.config_error_recovery_enable_tooltip
                )
                try:
                    io.config_error_recovery_enable_assert = False
                    io.config_error_recovery_enable_debug_log = False
                    io.config_error_recovery_enable_tooltip = False
                    imgui.internal.error_recovery_try_to_recover_state(
                        recovery_state
                    )
                    imgui.end_frame()
                except Exception as recovery_error:
                    print(
                        f"Editor frame recovery failed: {recovery_error}",
                        file=sys.stderr,
                    )
                    traceback.print_exc()
                finally:
                    io.config_error_recovery_enable_assert = recovery_asserts
                    io.config_error_recovery_enable_debug_log = (
                        recovery_debug_log
                    )
                    io.config_error_recovery_enable_tooltip = recovery_tooltip
        finally:
            self._animation_frame_textures.clear()

    def _draw_interface(self) -> None:
        self._shortcut_context = None
        self._asset_picker_keep_alive = False
        self._asset_field_button_rects.clear()
        self._asset_field_targets.clear()
        self._draw_menu()
        self._draw_status()
        dockspace_id = imgui.dock_space_over_viewport()
        self._initialize_default_dock_layout(dockspace_id)
        self._draw_layers()
        self._draw_ui_views()
        self._draw_layer_content()
        self._draw_assets()
        self._draw_tile_palette_panel()
        self._draw_viewport()
        self._draw_inspector()
        self._draw_animation_panel()
        self._draw_particles_panel()
        self._draw_console()
        self._poll_export()
        self._poll_native_dialog()
        self._draw_modals()
        if (
            self._asset_picker is not None
            and (
                self._asset_picker.field_id not in self._asset_field_targets
                or (
                    imgui.is_mouse_clicked(_flag(imgui.MouseButton_.left))
                    and not self._asset_picker_keep_alive
                )
            )
        ):
            self._asset_picker = None
        self._update_caption()

    def _initialize_default_dock_layout(self, dockspace_id: int) -> None:
        if not self._dock_layout_pending:
            return
        self._dock_layout_pending = False
        viewport = imgui.get_current_context().viewports[0]
        builder = imgui.internal
        builder.dock_builder_remove_node(dockspace_id)
        builder.dock_builder_add_node(dockspace_id)
        builder.dock_builder_set_node_size(
            dockspace_id,
            viewport.get_build_work_rect().get_size(),
        )
        _, left_id, center_id = builder.dock_builder_split_node_py(
            dockspace_id,
            imgui.Dir.left,
            0.22,
        )
        _, right_id, center_id = builder.dock_builder_split_node_py(
            center_id,
            imgui.Dir.right,
            0.26,
        )
        _, assets_id, navigation_id = (
            builder.dock_builder_split_node_py(
                left_id,
                imgui.Dir.down,
                0.25,
            )
        )
        _, layer_content_id, layers_id = (
            builder.dock_builder_split_node_py(
                navigation_id,
                imgui.Dir.down,
                0.60,
            )
        )
        _, status_id, viewport_id = builder.dock_builder_split_node_py(
            center_id,
            imgui.Dir.down,
            0.46,
        )
        builder.dock_builder_dock_window("UI Views", layers_id)
        builder.dock_builder_dock_window("Layers", layers_id)
        builder.dock_builder_dock_window(
            "Hierarchy###LayerContent",
            layer_content_id,
        )
        builder.dock_builder_dock_window("Assets", assets_id)
        builder.dock_builder_dock_window("Scene", viewport_id)
        builder.dock_builder_dock_window("Inspector", right_id)
        builder.dock_builder_dock_window("Tile Palette", status_id)
        builder.dock_builder_dock_window("Animation", status_id)
        builder.dock_builder_dock_window("Particles", status_id)
        builder.dock_builder_dock_window("Console", status_id)
        builder.dock_builder_finish(dockspace_id)

    def _begin_panel(
        self,
        title: str,
        *,
        title_bar: bool = True,
    ) -> bool:
        flags = 0
        if not title_bar:
            flags |= _flag(imgui.WindowFlags_.no_title_bar)
        return begin_panel(title, flags=flags)

    def _draw_menu(self) -> None:
        if not imgui.begin_main_menu_bar():
            return
        if imgui.begin_menu("File"):
            if imgui.menu_item("New Scene", "Cmd/Ctrl+N", False)[0]:
                self._request_action("new")
            if imgui.menu_item(
                "Load Scene...",
                "Cmd/Ctrl+O",
                False,
            )[0]:
                self._request_action("open")
            if imgui.menu_item("Save Scene", "Cmd/Ctrl+S", False)[0]:
                self._save()
            if imgui.menu_item(
                "Save Scene As...",
                "Cmd/Ctrl+Shift+S",
                False,
            )[0]:
                self._show_path_dialog("save")
            imgui.separator()
            if imgui.menu_item("Quit", "Cmd/Ctrl+Q", False)[0]:
                self._request_action("quit")
            imgui.end_menu()
        if imgui.begin_menu("Edit"):
            undo_label = self.state.undo_label
            redo_label = self.state.redo_label
            if imgui.menu_item(
                f"Undo {undo_label}" if undo_label else "Undo",
                "Cmd/Ctrl+Z",
                False,
                self.state.can_undo,
            )[0]:
                self._undo()
            if imgui.menu_item(
                f"Redo {redo_label}" if redo_label else "Redo",
                "Cmd/Ctrl+Shift+Z",
                False,
                self.state.can_redo,
            )[0]:
                self._redo()
            imgui.separator()
            enabled = (
                self.state.selected is not None
                or self.state.selected_gui_element is not None
                or bool(self.state.selected_tile_cells)
            )
            if imgui.menu_item("Delete", "Cmd/Ctrl+Delete", False, enabled)[0]:
                self.state.delete_selected()
            imgui.end_menu()
        if imgui.begin_menu("Add"):
            if imgui.menu_item("Object Layer", "", False)[0]:
                self.state.add_layer()
            if imgui.menu_item("Tile Layer", "", False)[0]:
                self.state.add_tile_layer()
            if imgui.menu_item("UI View", "", False)[0]:
                self.state.add_ui_view()
            imgui.end_menu()
        if imgui.begin_menu("Project"):
            building = (
                self._export_thread is not None
                and self._export_thread.is_alive()
            )
            if imgui.menu_item(
                "Validate Project",
                "",
                False,
                not building,
            )[0]:
                self._validate_project("project")
            imgui.separator()
            if imgui.menu_item(
                "Export Game...",
                "",
                False,
                not building,
            )[0]:
                self._open_export_dialog()
            imgui.end_menu()
        if imgui.begin_menu("Window"):
            if imgui.begin_menu("Theme"):
                for preference, label in (
                    ("system", "System"),
                    ("light", "Light"),
                    ("dark", "Dark"),
                ):
                    if imgui.menu_item(
                        label,
                        "",
                        self._theme_preference == preference,
                    )[0]:
                        self._set_theme(cast(ThemePreference, preference))
                imgui.end_menu()
            imgui.end_menu()
        if imgui.begin_menu("Help"):
            if imgui.menu_item("Documentation", "", False)[0]:
                self._open_external_url(
                    _DOCUMENTATION_URL,
                    "documentation",
                )
            if imgui.menu_item("Farning Website", "", False)[0]:
                self._open_external_url(_FARNING_URL, "Farning website")
            imgui.end_menu()
        imgui.end_main_menu_bar()

    def _validate_project(
        self,
        purpose: ValidationPurpose,
    ) -> bool:
        project = self.project or ProjectConfig.create(
            self.state.asset_root
        )
        filename = self.state.filename
        if filename is None:
            filename = project.root / "__open_scene__.scene.json"
        report = validate_project(
            project,
            purpose=purpose,
            document_overrides={filename: self.state.document},
            import_scripts=False,
        )
        if report.is_valid:
            self.state.error = None
            self.state.status = "Project validation passed"
            return True
        message = report.format()
        self.state.error = message
        self.state.status = (
            f"Project validation found {len(report.issues)} error"
            f"{'' if len(report.issues) == 1 else 's'}"
        )
        print(message, file=sys.stderr)
        return False

    def _set_theme(self, preference: ThemePreference) -> None:
        self._theme_preference = preference
        self._theme = resolve_theme(preference)
        self._preferences.theme = preference
        configure_style(self._theme)
        if self._preferences_path is None:
            return
        try:
            self._preferences.save(self._preferences_path)
        except OSError as error:
            self.state.error = f"Could not save editor settings: {error}"

    def _open_external_url(self, url: str, label: str) -> None:
        try:
            opened = webbrowser.open(url)
        except webbrowser.Error as error:
            self.state.error = f"Could not open {label}: {error}"
            return
        if not opened:
            self.state.error = f"Could not open {label}: no browser accepted the URL"

    def _undo(self) -> None:
        if self.preview.mode != "edit":
            return
        self._cancel_collider_drag()
        self._cancel_spline_drag()
        try:
            changed = self.state.undo()
        except (OSError, ValueError) as error:
            self.state.error = f"Could not undo: {error}"
            return
        if changed:
            self._refresh_assets()
            self.preview.reload()
            self._refresh_component_tool_after_history()

    def _redo(self) -> None:
        if self.preview.mode != "edit":
            return
        self._cancel_collider_drag()
        self._cancel_spline_drag()
        try:
            changed = self.state.redo()
        except (OSError, ValueError) as error:
            self.state.error = f"Could not redo: {error}"
            return
        if changed:
            self._refresh_assets()
            self.preview.reload()
            self._refresh_component_tool_after_history()


















    def _begin_rename(
        self,
        kind: str,
        identifier: str,
        current_name: str,
    ) -> None:
        self._rename_target = _RenameTarget(kind, identifier)
        self._rename_buffer = current_name
        self._rename_focus_pending = True

    def _is_renaming(self, kind: str, identifier: str) -> bool:
        return self._rename_target == _RenameTarget(kind, identifier)

    def _draw_inline_rename(
        self,
        kind: str,
        identifier: str,
        current_name: str,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        if not self._is_renaming(kind, identifier):
            return
        if not self._rename_buffer:
            self._rename_buffer = current_name
        cursor = imgui.get_cursor_screen_pos()
        inset = 20.0 if kind in {"object", "gui", "asset"} else 4.0
        imgui.set_cursor_screen_pos(
            imgui.ImVec2(minimum.x + inset, minimum.y)
        )
        imgui.set_next_item_width(
            max(40.0, maximum.x - minimum.x - inset)
        )
        if self._rename_focus_pending:
            imgui.set_keyboard_focus_here()
            self._rename_focus_pending = False
        submitted, self._rename_buffer = imgui.input_text(
            f"##rename-{kind}-{identifier}",
            self._rename_buffer,
            (
                _flag(imgui.InputTextFlags_.enter_returns_true)
                | _flag(imgui.InputTextFlags_.auto_select_all)
            ),
        )
        deactivated = imgui.is_item_deactivated_after_edit()
        imgui.set_cursor_screen_pos(cursor)
        if submitted or deactivated:
            try:
                self._commit_rename()
            except (OSError, ValueError, SceneFormatError) as error:
                self.state.error = str(error)
                self._rename_focus_pending = True

    def _commit_rename(self) -> None:
        target = self._rename_target
        if target is None:
            return
        name = self._rename_buffer.strip()
        if (
            not name
            or name in {".", ".."}
            or Path(name).name != name
        ):
            raise ValueError("name must be a single non-empty name")
        if target.kind == "object":
            self.state.select(target.identifier)
            self.state.set_object_value("name", name)
        elif target.kind == "gui":
            self.state.select(target.identifier)
            self.state.set_gui_element_value("name", name)
        elif target.kind == "layer":
            self.state.select_layer(target.identifier)
            self.state.set_layer_value("name", name)
        elif target.kind == "asset":
            self._rename_asset(Path(target.identifier), name)
        elif target.kind == "animation-state":
            self._rename_animation_state(target.identifier, name)
        else:
            raise ValueError(f"unknown rename target: {target.kind}")
        self._rename_target = None
        self._rename_buffer = ""
        self.state.error = None
    def _draw_viewport(self) -> None:
        self._viewport_visible = self._begin_panel("Scene")
        if self._viewport_visible:
            if imgui.is_window_focused():
                self._shortcut_context = "viewport"
            self._draw_viewport_toolbar()
            self._draw_viewport_tools()
            viewport_error = self.preview.load_error
            if viewport_error:
                imgui.push_style_color(
                    imgui.Col_.text, imgui.ImVec4(*_ERROR_TEXT_COLOR)
                )
                imgui.text_wrapped(
                    (
                        f"Scene preview unavailable: {viewport_error}\n"
                        "See Console for details. Fix the scene or component "
                        "dependencies in Inspector to restore the preview."
                    )
                    if self.preview.load_error
                    else viewport_error
                )
                imgui.pop_style_color()
            available = imgui.get_content_region_avail()
            image_origin = imgui.get_cursor_screen_pos()
            game = self.preview.game
            screen_space_preview = (
                game is not None
                and (
                    self.preview.mode == "play"
                    or self.state.selected_ui_view is not None
                )
            )
            if screen_space_preview and game is not None:
                target_width = max(1, game.width)
                target_height = max(1, game.height)
                presentation = presentation_rect(
                    target_width,
                    target_height,
                    max(1.0, available.x),
                    max(1.0, available.y),
                    game.presentation.scaling,
                )
                display_width = presentation.width
                display_height = presentation.height
                matte = game.presentation.matte_color
                matte_alpha = matte[3] if len(matte) == 4 else 255
                imgui.get_window_draw_list().add_rect_filled(
                    image_origin,
                    imgui.ImVec2(
                        image_origin.x + available.x,
                        image_origin.y + available.y,
                    ),
                    _color(
                        matte[0],
                        matte[1],
                        matte[2],
                        matte_alpha,
                    ),
                )
                imgui.set_cursor_screen_pos(
                    imgui.ImVec2(
                        image_origin.x + presentation.x,
                        image_origin.y + presentation.y,
                    )
                )
            else:
                target_width = max(1, int(available.x))
                target_height = max(1, int(available.y))
                display_width = float(target_width)
                display_height = float(target_height)
            self._requested_target_size = (target_width, target_height)
            self.render_target.ensure_size(target_width, target_height)
            imgui.image(
                self.render_target.texture_ref,
                imgui.ImVec2(display_width, display_height),
                imgui.ImVec2(0, 1),
                imgui.ImVec2(1, 0),
            )
            minimum = imgui.get_item_rect_min()
            maximum = imgui.get_item_rect_max()
            self._viewport_rect = (
                minimum.x,
                minimum.y,
                maximum.x,
                maximum.y,
            )
            self._viewport_hovered = imgui.is_item_hovered()
            self._draw_viewport_context_menu(minimum, maximum)
            self._accept_viewport_asset_drop(minimum)
            if self.preview.mode == "edit":
                self._draw_viewport_overlay(minimum, maximum)
            self._handle_viewport_input(minimum, maximum)
        imgui.end()

    def _draw_viewport_context_menu(
        self,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2 | None = None,
    ) -> None:
        if maximum is None:
            maximum = imgui.ImVec2(
                minimum.x + self.render_target.width,
                minimum.y + self.render_target.height,
            )
        right_button = _flag(imgui.MouseButton_.right)
        if (
            self.preview.mode == "edit"
            and imgui.is_item_clicked(right_button)
        ):
            pointer = imgui.get_mouse_pos()
            display_width = max(1.0, maximum.x - minimum.x)
            display_height = max(1.0, maximum.y - minimum.y)
            local_x = (
                pointer.x - minimum.x
            ) * self.render_target.width / display_width
            local_y = (
                maximum.y - pointer.y
            ) * self.render_target.height / display_height
            self._right_pan_candidate = True
            self._right_pan_dragged = False
            self._right_pan_position = (local_x, local_y)
        if (
            self._right_pan_candidate
            and imgui.is_mouse_dragging(right_button, 2.0)
        ):
            self._right_pan_dragged = True
        if (
            self._right_pan_candidate
            and imgui.is_mouse_released(right_button)
        ):
            if not self._right_pan_dragged:
                self._open_viewport_context_menu()
            self._right_pan_candidate = False
            self._right_pan_dragged = False
            self._right_pan_position = None
        if not imgui.begin_popup("##viewport-context"):
            return
        self._shortcut_context = "viewport"
        layer = self.state.selected_layer
        if layer is not None and imgui.begin_menu("Add Here"):
            pointer = self._viewport_paste_position
            if layer.get("type", "object") == "object":
                for label, kind in (
                    ("Empty Object", "empty"),
                    ("Rectangle", "rectangle"),
                    ("Circle", "circle"),
                    ("Text", "text"),
                    ("Box Area", "area_box"),
                    ("Circle Area", "area_circle"),
                ):
                    if imgui.menu_item(label, "", False)[0]:
                        x, y = pointer or (0, 0)
                        world_x, world_y = self.preview.screen_to_world(x, y)
                        self.state.select(None)
                        self.state.add_object(
                            kind,
                            x=world_x,
                            y=world_y,
                        )
                if imgui.menu_item("Sprite", "", False)[0]:
                    x, y = pointer or (0, 0)
                    world_x, world_y = self.preview.screen_to_world(x, y)
                    self.state.select(None)
                    try:
                        self.state.add_sprite(
                            x=world_x,
                            y=world_y,
                            at_root=True,
                        )
                    except ValueError as error:
                        self.state.error = str(error)
                if imgui.menu_item("Background", "", False)[0]:
                    self.state.add_background()
            elif layer.get("type") == "ui":
                for label, kind in (
                    ("Panel", "panel"),
                    ("Label", "label"),
                    ("Button", "button"),
                    ("Toggle", "toggle"),
                    ("Slider", "slider"),
                    ("Text Input", "text_input"),
                    ("Scroll View", "scroll"),
                    ("Stack Layout", "stack"),
                    ("Vertical Layout", "vbox"),
                    ("Horizontal Layout", "hbox"),
                    ("Grid Layout", "grid"),
                    ("Anchor Layout", "anchor"),
                ):
                    if imgui.menu_item(label, "", False)[0]:
                        self.state.select(None)
                        x, y = pointer or (310, 276)
                        self.state.add_gui_element(kind, x=x, y=y)
            imgui.end_menu()
        has_selection = bool(self.state.selected_ids) or any(self.state.selected_tile_groups.values())
        single = len(self.state.selected_ids) == 1
        if imgui.menu_item("Rename", "", False, single)[0]:
            self._start_selected_rename()
        imgui.separator()
        if imgui.menu_item("Cut", "Cmd/Ctrl+X", False, has_selection)[0]:
            self._cut_selection()
        if imgui.menu_item("Copy", "Cmd/Ctrl+C", False, has_selection)[0]:
            self._copy_selection()
        if imgui.menu_item(
            "Convert Tiles to GameObject...", "", False,
            any(self.state.selected_tile_groups.values()),
        )[0]:
            self._cancel_tile_edit_drag()
            self._tile_conversion_destination = self.state.tile_conversion_destination()
            self._tile_conversion_revision = self.state.revision
            self._tile_conversion_selection = deepcopy(self.state.selected_tile_groups)
            self._popup_to_open = "Convert Tiles to GameObject"
        if imgui.menu_item("Paste Here", "Cmd/Ctrl+V", False)[0]:
            self._paste_selection()
        if imgui.menu_item(
            "Duplicate",
            "Cmd/Ctrl+D",
            False,
            has_selection,
        )[0]:
            self._duplicate_selection()
        imgui.separator()
        if imgui.menu_item(
            "Delete", "Cmd/Ctrl+Delete", False,
            has_selection or bool(self.state.selected_tile_cells),
        )[0]:
            self.state.delete_selected()
        imgui.end_popup()

    def _open_viewport_context_menu(self) -> None:
        pointer = self._right_pan_position
        if pointer is not None:
            local_x, local_y = pointer
            self._viewport_click_serial += 1
            self._viewport_paste_position = (local_x, local_y)
            layer = self.state.selected_layer
            identifier: str | None = None
            if layer is not None and layer.get("type") == "ui":
                layer_id = layer.get("id")
                if isinstance(layer_id, str):
                    identifier = self.preview.pick_gui(
                        layer_id,
                        local_x,
                        local_y,
                    )
            elif (
                layer is not None
                and layer.get("type", "object") == "object"
            ):
                identifier = self.preview.pick(local_x, local_y)
            if identifier is not None:
                self.state.select(identifier)
            else:
                self.state.select(None)
        imgui.open_popup("##viewport-context")

    def _accept_viewport_asset_drop(
        self,
        minimum: imgui.ImVec2,
    ) -> None:
        if not imgui.begin_drag_drop_target():
            return
        payload = imgui.accept_drag_drop_payload_py_id(
            _ASSET_DRAG_PAYLOAD
        )
        if payload is not None and self._asset_drag_path is not None:
            path = self._asset_drag_path
            layer = self.state.selected_layer
            try:
                if self.preview.mode != "edit":
                    raise ValueError(
                        "stop Play mode before placing assets"
                    )
                if (
                    layer is None
                    or layer.get("type", "object") != "object"
                ):
                    raise ValueError(
                        "select an object layer before placing this asset"
                    )
                pointer = imgui.get_mouse_pos()
                world_x, world_y = self.preview.screen_to_world(
                    pointer.x - minimum.x,
                    pointer.y - minimum.y,
                )
                if path.name.endswith(PREFAB_SUFFIX):
                    self.state.instantiate_prefab(
                        path,
                        position=(world_x, world_y),
                    )
                    self.state.status = f"Instantiated prefab {path.name}"
                elif self._asset_matches_kind(path, "image"):
                    self.state.add_sprite(
                        path,
                        x=world_x,
                        y=world_y,
                        at_root=True,
                    )
                    self.state.status = f"Added sprite {path.name}"
                else:
                    raise ValueError(
                        "only image and prefab assets can be placed "
                        "in the viewport"
                    )
                self._asset_picker = None
                self.state.error = None
            except (ValueError, OSError) as error:
                self.state.error = str(error)
        imgui.end_drag_drop_target()

    def _draw_viewport_toolbar(self) -> None:
        toolbar_visible = imgui.begin_child(
            "##ViewportToolbar",
            imgui.ImVec2(0, 42),
            _flag(imgui.ChildFlags_.frame_style),
            _flag(imgui.WindowFlags_.no_scrollbar),
        )
        if toolbar_visible:
            playing = self.preview.mode == "play"
            icon = (
                icons_fontawesome_6.ICON_FA_STOP
                if playing
                else icons_fontawesome_6.ICON_FA_PLAY
            )
            action = "Stop" if playing else "Play"
            if icon_button(
                icon,
                "viewport-playback",
                action,
                size=imgui.ImVec2(32, 0),
            ):
                if not playing:
                    self._reload_changed_scripts()
                if playing or self._validate_project("preview"):
                    self.preview.set_mode("edit" if playing else "play")
                    self._frame_rate.reset()
            imgui.same_line()
            imgui.text_disabled(
                (
                    f"Playing | {self._frame_rate.fps:.0f} FPS"
                    if playing
                    else "Edit"
                )
            )
            stats = self.preview.renderer.stats
            imgui.same_line()
            imgui.text_disabled(
                f"| {stats.submitted_renderers} drawn, "
                f"{stats.culled_renderers} culled, "
                f"~{stats.approximate_draw_calls} calls"
            )
            imgui.same_line()
            self._draw_component_tool_selector()

            grid_controls_width = 195
            grid_controls_x = max(
                imgui.get_cursor_pos_x() + 12,
                imgui.get_window_width() - grid_controls_width,
            )
            imgui.same_line()
            imgui.set_cursor_pos_x(grid_controls_x)
            imgui.text_disabled("Grid")
            imgui.same_line()
            imgui.begin_disabled(playing)
            imgui.set_next_item_width(100)
            changed, value = imgui.input_int(
                "##viewport-grid-size",
                self.state.grid_size,
                1,
                16,
            )
            if changed and value > 0:
                self.state.set_grid_size(value)
            imgui.same_line()
            if icon_button(
                icons_fontawesome_6.ICON_FA_MAGNET,
                "viewport-grid-snap",
                (
                    "Grid Snap unavailable\nStop Play mode to change snapping"
                    if playing
                    else f"Grid Snap: {'On' if self.state.snap_enabled else 'Off'}\n"
                    "Snap object transforms to the grid; collider editing stays unsnapped"
                ),
                size=imgui.ImVec2(34, 30),
                selected=self.state.snap_enabled,
            ):
                self.state.set_snap_enabled(not self.state.snap_enabled)
            imgui.end_disabled()
        imgui.end_child()

    def _draw_viewport_overlay(
        self,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        if self.preview.mode != "edit" or self.preview.game is None:
            return
        draw_list = imgui.get_window_draw_list()
        camera = self.preview.game.scenes.current.camera
        width = maximum.x - minimum.x
        height = maximum.y - minimum.y
        if width <= 0 or height <= 0:
            return
        selected_layer = self.state.selected_layer
        editing_ui = (
            selected_layer is not None
            and selected_layer.get("type") == "ui"
        )
        scale_x = width / self.render_target.width
        scale_y = height / self.render_target.height
        if not editing_ui:
            lower_left = camera.screen_to_world((0, 0))
            upper_right = camera.screen_to_world((width, height))
            grid = self._visible_grid_step(
                self.state.grid_size,
                camera.zoom,
            )
            start_x = floor(min(lower_left.x, upper_right.x) / grid) * grid
            end_x = ceil(max(lower_left.x, upper_right.x) / grid) * grid
            start_y = floor(min(lower_left.y, upper_right.y) / grid) * grid
            end_y = ceil(max(lower_left.y, upper_right.y) / grid) * grid
            grid_color = _color(46, 55, 70, 85)
            axis_color = _color(92, 110, 132, 140)
            for world_x in range(int(start_x), int(end_x) + grid, grid):
                screen_x, _ = self.preview.world_to_screen(world_x, 0)
                draw_list.add_line(
                    imgui.ImVec2(minimum.x + screen_x, minimum.y),
                    imgui.ImVec2(minimum.x + screen_x, maximum.y),
                    axis_color if world_x == 0 else grid_color,
                )
            for world_y in range(int(start_y), int(end_y) + grid, grid):
                _, screen_y = self.preview.world_to_screen(0, world_y)
                screen_y = maximum.y - screen_y
                draw_list.add_line(
                    imgui.ImVec2(minimum.x, screen_y),
                    imgui.ImVec2(maximum.x, screen_y),
                    axis_color if world_y == 0 else grid_color,
                )
        selection_color = _color(60, 196, 255)
        if self.state.selected_gui_elements:
            for gui_element in self.state.selected_gui_elements:
                identifier = gui_element.get("id")
                bounds = (
                    self.preview.gui_element_bounds(identifier)
                    if isinstance(identifier, str)
                    else None
                )
                if bounds is None:
                    continue
                x, y, right, top = bounds
                draw_list.add_rect(
                    imgui.ImVec2(
                        minimum.x + x * scale_x,
                        maximum.y - top * scale_y,
                    ),
                    imgui.ImVec2(
                        minimum.x + right * scale_x,
                        maximum.y - y * scale_y,
                    ),
                    selection_color,
                    thickness=2,
                )
        else:
            for selected in self.preview.selected_runtime_objects():
                left, bottom, right, top = (
                    self.preview.object_screen_bounds(selected)
                )
                if left == right and bottom == top:
                    draw_list.add_circle(
                        imgui.ImVec2(
                            minimum.x + left,
                            maximum.y - bottom,
                        ),
                        10,
                        selection_color,
                        24,
                        2,
                    )
                else:
                    draw_list.add_rect(
                        imgui.ImVec2(
                            minimum.x + left,
                            maximum.y - top,
                        ),
                        imgui.ImVec2(
                            minimum.x + right,
                            maximum.y - bottom,
                        ),
                        selection_color,
                        thickness=2,
                    )
            self._draw_collider_overlay(minimum, maximum)
            self._draw_spline_overlay(minimum, maximum)
            if self._viewport_tool == "transform":
                self._draw_imguizmo(minimum, maximum)
        if (
            self._marquee_start is not None
            and self._marquee_current is not None
        ):
            start_x, start_y = self._marquee_start
            current_x, current_y = self._marquee_current
            first = imgui.ImVec2(
                minimum.x + min(start_x, current_x) * scale_x,
                maximum.y - max(start_y, current_y) * scale_y,
            )
            second = imgui.ImVec2(
                minimum.x + max(start_x, current_x) * scale_x,
                maximum.y - min(start_y, current_y) * scale_y,
            )
            draw_list.add_rect_filled(
                first,
                second,
                _color(60, 196, 255, 35),
            )
            draw_list.add_rect(
                first,
                second,
                selection_color,
                thickness=1,
            )
        self._draw_tile_pick_preview(minimum, maximum)
        self._draw_tile_placement_preview(minimum, maximum)
        self._draw_tile_selection(minimum, maximum)

    def _editable_colliders(self) -> list[tuple[int, Collider]]:
        if self.preview.mode != "edit" or len(self.state.selected_records) != 1:
            return []
        owner = self.preview.selected_runtime_object()
        if owner is None:
            return []
        identifier = owner.scene_id
        if self._collider_object_id != identifier:
            self._cancel_collider_drag()
            owner = self.preview.selected_runtime_object()
            if owner is None:
                return []
            self._collider_object_id = identifier
            self._collider_component_index = None
            self._collider_point = None
            self._collider_shape = None
        colliders: list[tuple[int, Collider]] = [
            (index, component) for index, component in enumerate(owner.components)
            if isinstance(component, Collider)
        ]
        stale = False
        if colliders and self._collider_component_index not in [index for index, _ in colliders]:
            self._collider_component_index = colliders[0][0]
            self._collider_point = None
            stale = True
        active_shape = next(
            (collider.shape for index, collider in colliders if index == self._collider_component_index),
            None,
        )
        if active_shape != self._collider_shape:
            # Shape switches invalidate handle names and polygon point selection.
            if self._collider_shape is not None:
                self._collider_point = None
                stale = True
            self._collider_shape = active_shape
        if stale and self._collider_drag is not None:
            # Cancelling reloads the preview, so rebuild against fresh runtime objects.
            self._cancel_collider_drag()
            return self._editable_colliders()
        return colliders

    def _active_collider(self) -> tuple[int, Collider] | None:
        return next(
            (item for item in self._editable_colliders() if item[0] == self._collider_component_index), None,
        )

    def _draw_collider_overlay(self, minimum: imgui.ImVec2, maximum: imgui.ImVec2) -> None:
        self._editable_colliders()
        draw_list = imgui.get_window_draw_list()

        def screen(point: tuple[float, float]) -> imgui.ImVec2:
            x, y = self.preview.world_to_screen(*point)
            return imgui.ImVec2(minimum.x + x, maximum.y - y)

        for owner in self.preview.selected_runtime_objects():
            for collider in owner.components:
                if not isinstance(collider, Collider):
                    continue
                active = (
                    self._collider_edit_mode
                    and owner.scene_id == self._collider_object_id
                    and owner.scene_id == self.state.selected_id
                    and owner.components.index(collider) == self._collider_component_index
                    and len(self.state.selected_records) == 1
                )
                color = _color(245, 196, 85, 255 if active else 140)
                points = collider_outline(collider)
                for a, b in zip(points, (*points[1:], points[0])):
                    draw_list.add_line(screen(a), screen(b), color, 2 if active else 1)
                if active:
                    for name, point in collider_handles(collider).items():
                        selected = name == f"point:{self._collider_point}"
                        draw_list.add_circle_filled(
                            screen(point), 7 if selected else 6,
                            _color(255, 245, 210) if selected else color, 12,
                        )

    def _cancel_collider_drag(self) -> None:
        if self._collider_drag is not None:
            self._collider_drag = None
            self.preview.reload()

    def _begin_collider_drag(self, handle: str, world: tuple[float, float]) -> None:
        active = self._active_collider()
        if active is None or self.state.selected_id is None:
            return
        index, collider = active
        original = geometry_values(collider)
        self._collider_drag = _ColliderDrag(
            self.state.selected_id, index, collider, collider.shape, handle, world,
            original, deepcopy(original), self.state.revision,
        )
        self._collider_point = int(handle.split(":")[1]) if handle.startswith("point:") else None

    def _update_collider_drag(self, world: tuple[float, float], *, down: bool) -> None:
        drag = self._collider_drag
        if drag is None:
            return
        if (
            self.preview.mode != "edit"
            or self.state.selected_id != drag.object_id
            or self.state.revision != drag.revision
            or self.preview.selected_runtime_object() is not drag.collider.owner
            or drag.collider.shape != drag.shape
            or self._collider_component_index != drag.component_index
            or not self._collider_edit_mode
        ):
            self._cancel_collider_drag()
            return
        if down:
            try:
                values = drag_values(drag.collider, drag.handle, drag.start, world, drag.original)
                validate_geometry(drag.shape, values)
                apply_geometry(drag.collider, values)
                drag.latest = values
                self.state.error = None
            except ValueError as error:
                self.state.error = str(error)
            return
        self._collider_drag = None
        if drag.latest != drag.original:
            self.state.set_collider_geometry(drag.component_index, drag.latest)

    def _insert_collider_point(self, edge: int, amount: float) -> None:
        active = self._active_collider()
        if active is None:
            return
        index, collider = active
        if collider.shape != "polygon":
            return
        a, b = collider.points[edge], collider.points[(edge + 1) % len(collider.points)]
        points: list[JsonValue] = [list(point) for point in collider.points]
        points.insert(edge + 1, [a[0] + (b[0] - a[0]) * amount, a[1] + (b[1] - a[1]) * amount])
        self.state.set_collider_geometry(index, {"points": points})
        self._collider_point = edge + 1

    def _delete_collider_point(self) -> None:
        active = self._active_collider()
        if active is None or self._collider_point is None:
            return
        index, collider = active
        if collider.shape != "polygon":
            return
        if len(collider.points) <= 3:
            raise ValueError("Polygon collider needs at least three vertices")
        if not 0 <= self._collider_point < len(collider.points):
            self._collider_point = None
            return
        points: list[JsonValue] = [list(point) for i, point in enumerate(collider.points) if i != self._collider_point]
        self.state.set_collider_geometry(index, {"points": points})
        self._collider_point = None

    def _collider_hit(
        self, collider: Collider, x: float, y: float,
    ) -> tuple[str, int, float] | None:
        pointer = (x, y)
        locations = {
            name: self.preview.world_to_screen(*point)
            for name, point in collider_handles(collider).items()
        }
        name, distance = min(
            ((name, hypot(x - point[0], y - point[1])) for name, point in locations.items()),
            key=lambda item: (item[1], item[0] == "center"),
        )
        if distance <= 16:
            return name, -1, 0.0
        points = [self.preview.world_to_screen(*point) for point in collider_outline(collider)]
        edge, (distance, amount) = min(
            ((i, edge_projection(pointer, a, b)) for i, (a, b) in enumerate(zip(points, (*points[1:], points[0])))),
            key=lambda item: item[1][0],
        )
        if distance <= 8:
            return "edge", edge, amount
        return None

    def _handle_collider_click(self, x: float, y: float) -> bool:
        active = self._active_collider()
        if active is None:
            return False
        _, collider = active
        hit = self._collider_hit(collider, x, y)
        if hit is not None:
            name, edge, amount = hit
            imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
            if name != "edge":
                self._begin_collider_drag(name, self.preview.screen_to_world(x, y))
                return True
            if collider.shape == "polygon" and imgui.get_io().key_shift:
                try:
                    self._insert_collider_point(edge, amount)
                except ValueError as error:
                    self.state.error = str(error)
            else:
                self._begin_collider_drag("center", self.preview.screen_to_world(x, y))
            return True
        self._collider_point = None
        return False

    def _tile_cell_viewport_bounds(
        self,
        layer: dict[str, JsonValue],
        column: int,
        row: int,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> tuple[imgui.ImVec2, imgui.ImVec2]:
        width, height = self.state._tile_pair(
            layer.get("cell_size"), "cell_size"
        )
        offset_x, offset_y = self.state._tile_pair(
            layer.get("offset", [0, 0]), "offset"
        )
        left, bottom = self.preview.world_to_screen(
            offset_x + column * width, offset_y + row * height
        )
        right, top = self.preview.world_to_screen(
            offset_x + (column + 1) * width,
            offset_y + (row + 1) * height,
        )
        scale_x = (maximum.x - minimum.x) / self.render_target.width
        scale_y = (maximum.y - minimum.y) / self.render_target.height
        return (
            imgui.ImVec2(
                minimum.x + min(left, right) * scale_x,
                maximum.y - max(top, bottom) * scale_y,
            ),
            imgui.ImVec2(
                minimum.x + max(left, right) * scale_x,
                maximum.y - min(top, bottom) * scale_y,
            ),
        )

    def _draw_tile_selection(
        self,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        layer = self.state.selected_layer
        if (
            self.preview.mode != "edit"
            or self.preview.game is None
            or layer is None
            or layer.get("type") != "tile"
        ):
            return
        draw_list = imgui.get_window_draw_list()
        draw_list.push_clip_rect(minimum, maximum, True)
        try:
            cells = self.state.selected_tile_cells
            drag = self._tile_edit_drag
            dx, dy = (0, 0)
            if drag is not None and drag.tool == "move" and self._tile_edit_drag_valid(drag):
                dx = drag.current[0] - drag.start[0]
                dy = drag.current[1] - drag.start[1]
            for column, row in cells:
                first, second = self._tile_cell_viewport_bounds(
                    layer, column + dx, row + dy, minimum, maximum
                )
                self._draw_tile_preview_border(draw_list, first, second)
            for other in self.state.selected_layers:
                if other is layer or other.get("type") != "tile":
                    continue
                for column, row in self.state.selected_tile_groups.get(str(other["id"]), {}):
                    first, second = self._tile_cell_viewport_bounds(
                        other, column + dx, row + dy, minimum, maximum,
                    )
                    self._draw_tile_preview_border(draw_list, first, second)
            if drag is None or not self._tile_edit_drag_valid(drag):
                return
            if drag.tool == "select":
                left, right = sorted((drag.start[0], drag.current[0]))
                bottom, top = sorted((drag.start[1], drag.current[1]))
                first, _ = self._tile_cell_viewport_bounds(
                    layer, left, top, minimum, maximum
                )
                _, second = self._tile_cell_viewport_bounds(
                    layer, right, bottom, minimum, maximum
                )
                self._draw_tile_preview_border(draw_list, first, second)
        except (ValueError, SceneFormatError) as error:
            self.state.error = str(error)
        finally:
            draw_list.pop_clip_rect()

    def _draw_tile_placement_preview(
        self,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        layer = self.state.selected_layer
        if (
            not self._viewport_hovered
            or self.preview.mode != "edit"
            or layer is None
            or layer.get("type") != "tile"
            or self.state.tile_tool not in {"pencil", "rectangle"}
            or not self.state.has_tile_stamp
            or self._tile_pick_start is not None
            or (
                self.state.tile_tool == "pencil"
                and self._shift_pick_held
            )
            or self.preview.game is None
        ):
            return

        pointer = imgui.get_mouse_pos()
        display_width = max(1.0, maximum.x - minimum.x)
        display_height = max(1.0, maximum.y - minimum.y)
        local_x = (
            pointer.x - minimum.x
        ) * self.render_target.width / display_width
        local_y = (
            maximum.y - pointer.y
        ) * self.render_target.height / display_height
        world_x, world_y = self.preview.screen_to_world(local_x, local_y)
        try:
            cell = self.state.world_to_tile_cell(world_x, world_y)
        except (ValueError, SceneFormatError):
            return

        rectangle = self.state.tile_tool == "rectangle"
        start = self._tile_fill_start if rectangle else None
        stamp, stamp_size = self.state.palette_stamp
        cells = self._tile_placement_preview_cells(
            start or cell,
            cell,
            stamp,
            stamp_size,
            randomize=self.state.random_tile_brush,
            stamp_from_top_left=not rectangle,
        )
        draw_list = imgui.get_window_draw_list()
        draw_list.push_clip_rect(minimum, maximum, True)
        for column, row, preview_cell in cells:
            tile_minimum, tile_maximum = self._tile_cell_viewport_bounds(
                layer, column, row, minimum, maximum
            )
            self._draw_tile_placement_preview_cell(
                draw_list,
                layer,
                preview_cell.tile_id,
                tile_minimum,
                tile_maximum,
                flags=preview_cell.flags,
            )
        draw_list.pop_clip_rect()

    def _draw_tile_pick_preview(
        self,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        layer = self.state.selected_layer
        picking = (
            self._tile_pick_start is not None
            or (
                self._viewport_hovered
                and (
                    self.state.tile_tool == "picker"
                    or (
                        self.state.tile_tool == "pencil"
                        and self._shift_pick_held
                    )
                )
            )
        )
        if (
            not picking
            or self.preview.mode != "edit"
            or layer is None
            or layer.get("type") != "tile"
            or self.preview.game is None
        ):
            return
        imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
        current = self._tile_pick_current
        if current is None:
            pointer = imgui.get_mouse_pos()
            display_width = max(1.0, maximum.x - minimum.x)
            display_height = max(1.0, maximum.y - minimum.y)
            local_x = (
                pointer.x - minimum.x
            ) * self.render_target.width / display_width
            local_y = (
                maximum.y - pointer.y
            ) * self.render_target.height / display_height
            try:
                world = self.preview.screen_to_world(local_x, local_y)
                current = self.state.world_to_tile_cell(*world)
            except (ValueError, SceneFormatError):
                return
        start = self._tile_pick_start or current
        left, right = sorted((start[0], current[0]))
        bottom, top = sorted((start[1], current[1]))
        first, _ = self._tile_cell_viewport_bounds(
            layer, left, top, minimum, maximum
        )
        _, second = self._tile_cell_viewport_bounds(
            layer, right, bottom, minimum, maximum
        )
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(first, second, _color(255, 196, 64, 28))
        draw_list.add_rect(
            first,
            second,
            _color(255, 196, 64),
            thickness=2,
        )

    def _draw_tile_placement_preview_cell(
        self,
        draw_list: imgui.ImDrawList,
        layer: dict[str, JsonValue],
        tile_id: int,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
        *,
        flags: TileFlags = TileFlags.NONE,
    ) -> None:
        for tileset in self.state.tile_layer_tilesets(layer):
            first_gid = tileset.get("first_gid")
            tile_count = tileset.get("tile_count")
            if (
                not isinstance(first_gid, int)
                or not isinstance(tile_count, int)
                or not first_gid <= tile_id < first_gid + tile_count
            ):
                continue
            local_id = tile_id - first_gid
            raw_kind = tileset.get("type")
            kind = raw_kind if isinstance(raw_kind, str) else "atlas"
            preserve_aspect = kind == "images"
            if kind == "color":
                colors = tileset.get("colors")
                if (
                    not isinstance(colors, list)
                    or local_id >= len(colors)
                ):
                    return
                raw_channels = colors[local_id]
                if not isinstance(raw_channels, list):
                    return
                channels = [
                    channel
                    for channel in raw_channels
                    if isinstance(channel, int)
                    and not isinstance(channel, bool)
                ]
                if len(channels) != len(raw_channels) or len(channels) not in {
                    3,
                    4,
                }:
                    return
                rgba = [*channels, 255][:4]
                rgba[3] = int(rgba[3] * 0.48)
                draw_list.add_rect_filled(
                    minimum,
                    maximum,
                    _color(*rgba),
                )
                self._draw_tile_preview_border(draw_list, minimum, maximum)
                return

            try:
                if kind == "images":
                    images = tileset.get("images")
                    if (
                        not isinstance(images, list)
                        or local_id >= len(images)
                    ):
                        return
                    image_path = images[local_id]
                    if not isinstance(image_path, str):
                        return
                    texture = self._atlas_texture(image_path)
                    uv_minimum = imgui.ImVec2(0, 1)
                    uv_maximum = imgui.ImVec2(1, 0)
                else:
                    image = tileset.get("image")
                    tile_size = tileset.get("tile_size")
                    columns = tileset.get("columns")
                    if (
                        not isinstance(image, str)
                        or not isinstance(tile_size, list)
                        or len(tile_size) != 2
                        or not isinstance(columns, int)
                        or columns <= 0
                    ):
                        return
                    tile_width, tile_height = tile_size
                    if (
                        not isinstance(tile_width, int)
                        or not isinstance(tile_height, int)
                        or tile_width <= 0
                        or tile_height <= 0
                    ):
                        return
                    texture = self._atlas_texture(image)
                    margin = tileset.get("margin", 0)
                    spacing = tileset.get("spacing", 0)
                    margin = (
                        margin
                        if isinstance(margin, int)
                        and not isinstance(margin, bool)
                        else 0
                    )
                    spacing = (
                        spacing
                        if isinstance(spacing, int)
                        and not isinstance(spacing, bool)
                        else 0
                    )
                    column, row = local_id % columns, local_id // columns
                    left = margin + column * (tile_width + spacing)
                    top = margin + row * (tile_height + spacing)
                    uv_minimum = imgui.ImVec2(
                        left / texture.width,
                        1 - top / texture.height,
                    )
                    uv_maximum = imgui.ImVec2(
                        (left + tile_width) / texture.width,
                        1 - (top + tile_height) / texture.height,
                    )
            except (OSError, ValueError) as error:
                self.state.error = f"Could not preview tile: {error}"
                return

            texture_id = texture.id
            if texture_id is None:
                return
            image_minimum = minimum
            image_maximum = maximum
            if preserve_aspect:
                cell_width = maximum.x - minimum.x
                cell_height = maximum.y - minimum.y
                scale = min(
                    cell_width / texture.width,
                    cell_height / texture.height,
                )
                image_width = texture.width * scale
                image_height = texture.height * scale
                image_minimum = imgui.ImVec2(
                    minimum.x + (cell_width - image_width) / 2,
                    minimum.y + (cell_height - image_height) / 2,
                )
                image_maximum = imgui.ImVec2(
                    image_minimum.x + image_width,
                    image_minimum.y + image_height,
                )
            if flags == TileFlags.NONE:
                draw_list.add_image(
                    imgui.ImTextureRef(texture_id),
                    image_minimum,
                    image_maximum,
                    uv_minimum,
                    uv_maximum,
                    _color(255, 255, 255, 135),
                )
            else:
                coordinates = TileLayer._texture_coordinates(
                    (
                        uv_minimum.x, uv_maximum.y, 0,
                        uv_maximum.x, uv_maximum.y, 0,
                        uv_maximum.x, uv_minimum.y, 0,
                        uv_minimum.x, uv_minimum.y, 0,
                    ),
                    flags,
                )
                draw_list.add_image_quad(
                    imgui.ImTextureRef(texture_id),
                    image_minimum,
                    imgui.ImVec2(image_maximum.x, image_minimum.y),
                    image_maximum,
                    imgui.ImVec2(image_minimum.x, image_maximum.y),
                    imgui.ImVec2(coordinates[9], coordinates[10]),
                    imgui.ImVec2(coordinates[6], coordinates[7]),
                    imgui.ImVec2(coordinates[3], coordinates[4]),
                    imgui.ImVec2(coordinates[0], coordinates[1]),
                    _color(255, 255, 255, 135),
                )
            self._draw_tile_preview_border(draw_list, minimum, maximum)
            return

    @staticmethod
    def _draw_tile_preview_border(
        draw_list: imgui.ImDrawList,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        draw_list.add_rect(
            minimum,
            maximum,
            _color(75, 210, 255, 210),
            thickness=1.5,
        )

    @staticmethod
    def _tile_placement_preview_cells(
        start: tuple[int, int],
        end: tuple[int, int],
        stamp: list[TileCell | None],
        selection_size: tuple[int, int],
        *,
        randomize: bool = False,
        stamp_from_top_left: bool = False,
    ) -> list[tuple[int, int, TileCell]]:
        stamp_width, stamp_height = selection_size
        if (
            not stamp
            or stamp_width <= 0
            or stamp_height <= 0
            or len(stamp) != stamp_width * stamp_height
        ):
            return []
        preview: list[tuple[int, int, TileCell]] = []
        occupied = [cell for cell in stamp if cell is not None]
        if not occupied:
            return []
        if stamp_from_top_left:
            for stamp_y in range(stamp_height):
                row = start[1] - stamp_y
                for stamp_x in range(stamp_width):
                    column = start[0] + stamp_x
                    if randomize:
                        index = (
                            column * 73_856_093 ^ row * 19_349_663
                        ) % len(occupied)
                        cell = occupied[index]
                    else:
                        index = stamp_y * stamp_width + stamp_x
                        cell = stamp[index]
                    if cell is not None:
                        preview.append((column, row, cell))
            return preview

        left, right = sorted((start[0], end[0]))
        bottom, top = sorted((start[1], end[1]))
        for row in range(bottom, top + 1):
            for column in range(left, right + 1):
                if randomize:
                    index = (
                        column * 73_856_093 ^ row * 19_349_663
                    ) % len(occupied)
                    cell = occupied[index]
                else:
                    stamp_x = (column - left) % stamp_width
                    stamp_y = (top - row) % stamp_height
                    index = stamp_y * stamp_width + stamp_x
                    cell = stamp[index]
                if cell is not None:
                    preview.append((column, row, cell))
        return preview

    @staticmethod
    def _visible_grid_step(
        base_step: int,
        zoom: float,
        minimum_screen_spacing: float = 12,
    ) -> int:
        step = base_step
        while step * zoom < minimum_screen_spacing:
            step *= 2
        return step

    def _draw_imguizmo(
        self,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        record = self.state.selected
        if (
            record is None
            or self.state.mixed_layer_selection
            or self.preview.mode != "edit"
            or self.preview.game is None
        ):
            return
        if (
            len(self.state.selected_records) > 1
            and self._transform_tool != "move"
        ):
            return
        width = maximum.x - minimum.x
        height = maximum.y - minimum.y
        if width <= 0 or height <= 0:
            return
        world_transform = self.preview.selected_world_transform()
        if world_transform is None:
            return
        (
            world_x,
            world_y,
            rotation_value,
            scale_x,
            scale_y,
        ) = world_transform
        screen_x, screen_y = self.preview.world_to_screen(world_x, world_y)
        angle = radians(rotation_value)
        cosine = cos(angle)
        sine = sin(angle)
        gizmo = imguizmo.im_guizmo
        object_matrix = gizmo.Matrix16(
            [
                cosine * scale_x,
                sine * scale_x,
                0,
                0,
                -sine * scale_y,
                cosine * scale_y,
                0,
                0,
                0,
                0,
                1,
                0,
                screen_x,
                screen_y,
                0,
                1,
            ]
        )
        view = gizmo.Matrix16(
            [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        )
        projection = gizmo.Matrix16(
            [
                2 / width,
                0,
                0,
                0,
                0,
                2 / height,
                0,
                0,
                0,
                0,
                -1,
                0,
                -1,
                -1,
                0,
                1,
            ]
        )
        operation = {
            "move": gizmo.OPERATION.translate,
            "rotate": gizmo.OPERATION.rotate_z,
            "scale": gizmo.OPERATION.scale,
        }[self._transform_tool]
        snap = None
        if self.state.snap_enabled and self._transform_tool == "move":
            # The gizmo matrix uses screen pixels, not world units.
            grid = (
                self.state.grid_size
                * self.preview.game.scenes.current.camera.zoom
            )
            snap = gizmo.Matrix3([grid, grid, grid])
        elif imgui.get_io().key_shift:
            snap = gizmo.Matrix3(
                [15, 15, 15]
                if self._transform_tool == "rotate"
                else [0.1, 0.1, 0.1]
            )
        gizmo.set_orthographic(True)
        gizmo.set_drawlist()
        gizmo.set_rect(minimum.x, minimum.y, width, height)
        changed = gizmo.manipulate(
            view,
            projection,
            operation,
            (
                gizmo.MODE.local
                if self._effective_transform_space() == "local"
                else gizmo.MODE.world
            ),
            object_matrix,
            snap=snap,
        )
        if not changed:
            return
        components = gizmo.decompose_matrix_to_components(object_matrix)
        translation = components.translation.values.tolist()
        rotation_values = components.rotation.values.tolist()
        scales = components.scale.values.tolist()
        new_x, new_y = self.preview.screen_to_world(
            float(translation[0]),
            float(translation[1]),
        )
        if self._transform_tool == "move":
            if self.state.snap_enabled:
                new_x, new_y = self.state.grid_cell_center(
                    new_x,
                    new_y,
                )
            self.preview.move_selected_objects_by_world(
                new_x - world_x,
                new_y - world_y,
            )
            return
        elif self._transform_tool == "rotate":
            if self._effective_transform_space() == "global":
                self.state.rotate_selected_world(
                    float(rotation_values[2])
                )
            else:
                target = self.preview.selected_runtime_object()
                parent_rotation = (
                    target.parent.world_rotation
                    if target is not None
                    and target.parent is not None
                    else 0
                )
                self.state.set_object_value(
                    "rotation",
                    float(rotation_values[2]) - parent_rotation,
                )
        elif self._transform_tool == "scale":
            target = self.preview.selected_runtime_object()
            parent_scale_x, parent_scale_y = (
                target.parent.world_scale
                if target is not None and target.parent is not None
                else (1, 1)
            )
            if parent_scale_x == 0 or parent_scale_y == 0:
                self.state.error = (
                    "Cannot scale an object through a parent "
                    "with zero scale"
                )
                return
            self.state.set_object_value(
                "scale_x",
                max(0.05, float(scales[0]) / parent_scale_x),
            )
            self.state.set_object_value(
                "scale_y",
                max(0.05, float(scales[1]) / parent_scale_y),
            )
        self.preview.apply_selected_transform()

    def _tile_edit_drag_valid(self, drag: _TileEditDrag) -> bool:
        return (
            self.preview.mode == "edit"
            and self.state.selected_layer is drag.layer
            and self.state.tile_tool == drag.tool
            and self.state.revision == drag.revision
            and tuple(str(layer["id"]) for layer in self.state.selected_layers) == drag.layer_ids
        )

    def _select_world_rectangle(
        self, start: tuple[int, int], end: tuple[int, int],
    ) -> None:
        self.state.select_tile_rectangle(start, end)
        if not self.state.mixed_layer_selection or self.preview.game is None:
            return
        layer = self.state.selected_layer
        assert layer is not None
        width, height = self.state.selection_grid_size
        ox, oy = self.state._tile_pair(layer.get("offset", [0, 0]), "offset")
        left, right = sorted((start[0], end[0]))
        bottom, top = sorted((start[1], end[1]))
        camera = self.preview.game.scenes.current.camera
        a = camera.world_to_screen((ox + left * width, oy + bottom * height))
        b = camera.world_to_screen((ox + (right + 1) * width, oy + (top + 1) * height))
        self.state.select_many(self.preview.objects_in_rect(
            min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y),
        ))

    def _cancel_tile_edit_drag(self) -> None:
        drag = self._tile_edit_drag
        self._tile_edit_drag = None
        if drag is not None and (drag.originals or drag.objects):
            self.preview.reload()

    def _preview_tile_move(self, drag: _TileEditDrag) -> None:
        if self.preview.game is None:
            return
        dx = drag.current[0] - drag.start[0]
        dy = drag.current[1] - drag.start[1]
        width, height = self.state.selection_grid_size
        scene = self.preview.game.scenes.current
        for identifier, cells in drag.tiles.items():
            runtime = scene.layers[identifier]
            if not isinstance(runtime, TileLayer):
                raise ValueError("Selected tile layer is no longer available")
            originals = drag.originals.setdefault(identifier, {})
            for p, cell in originals.items():
                runtime.set_tile(*p, cell.tile_id if cell else None, flags=cell.flags if cell else TileFlags.NONE)
            affected = set(cells) | {(x + dx, y + dy) for x, y in cells}
            for p in affected:
                if p not in originals:
                    originals[p] = runtime.tile_at(*p)
            for p in cells:
                runtime.set_tile(*p, None)
            for (x, y), cell in cells.items():
                runtime.set_tile(x + dx, y + dy, cell.tile_id, flags=cell.flags)
        positions = []
        for target, (x, y) in drag.objects.items():
            positions.append((target, self.preview._local_position_for_world(
                target, x + dx * width, y + dy * height,
            )))
        for target, local in positions:
            target.position = local

    def _begin_tile_edit_drag(self, cell: tuple[int, int]) -> None:
        layer = self.state.selected_layer
        if layer is None or layer.get("type") != "tile":
            raise ValueError("select a tile layer first")
        tool = self.state.tile_tool
        if tool not in {"select", "move"}:
            raise ValueError("select the tile selection or move tool first")
        self.state.selection_grid_size
        world_hit = False
        if self.state.mixed_layer_selection and self.preview.game is not None:
            layer_width, layer_height = self.state.selection_grid_size
            ox, oy = self.state._tile_pair(layer.get("offset", [0, 0]), "offset")
            camera = self.preview.game.scenes.current.camera
            point = camera.world_to_screen((
                ox + (cell[0] + 0.5) * layer_width,
                oy + (cell[1] + 0.5) * layer_height,
            ))
            for target in self.preview.selected_runtime_objects(top_level_only=True):
                left, bottom, right, top = self.preview.object_screen_bounds(target)
                if left <= point.x <= right and bottom <= point.y <= top:
                    world_hit = True
        if tool == "select" or (
            not world_hit and not any(cell in cells for cells in self.state.selected_tile_groups.values())
        ):
            self._select_world_rectangle(cell, cell)
        if tool == "move" and not any(self.state.selected_tile_groups.values()) and not (
            self.state.mixed_layer_selection and self.state.selected_records
        ):
            return
        self._tile_edit_drag = _TileEditDrag(
            layer, tool, cell, cell, self.state.revision,
            tuple(str(layer["id"]) for layer in self.state.selected_layers),
            deepcopy(self.state.selected_tile_groups),
            objects={
                target: (target.world_position.x, target.world_position.y)
                for target in self.preview.selected_runtime_objects(top_level_only=True)
            } if self.state.mixed_layer_selection else {},
        )

    def _update_tile_edit_drag(
        self, cell: tuple[int, int], *, down: bool
    ) -> None:
        drag = self._tile_edit_drag
        if drag is None:
            return
        if not self._tile_edit_drag_valid(drag):
            self._cancel_tile_edit_drag()
            return
        drag.current = cell
        try:
            if drag.tool == "select":
                self._select_world_rectangle(drag.start, cell)
            elif down:
                self._preview_tile_move(drag)
            else:
                if self.state.move_world_selection(
                    cell[0] - drag.start[0], cell[1] - drag.start[1]
                ):
                    self.preview.reload()
                elif drag.originals or drag.objects:
                    self.preview.reload()
        except (ValueError, SceneFormatError):
            self._cancel_tile_edit_drag()
            raise
        finally:
            if not down:
                self._tile_edit_drag = None

    def _handle_viewport_input(
        self,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        self._validate_component_tool()
        left_button = _flag(imgui.MouseButton_.left)
        middle_button = _flag(imgui.MouseButton_.middle)
        right_button = _flag(imgui.MouseButton_.right)
        pointer = imgui.get_mouse_pos()
        display_width = max(1.0, maximum.x - minimum.x)
        display_height = max(1.0, maximum.y - minimum.y)
        local_x = (
            pointer.x - minimum.x
        ) * self.render_target.width / display_width
        local_y = (
            maximum.y - pointer.y
        ) * self.render_target.height / display_height
        if self._spline_drag is not None:
            imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
            self._update_spline_drag(
                self.preview.screen_to_world(local_x, local_y),
                down=imgui.is_mouse_down(left_button),
            )
            return
        if self._collider_drag is not None:
            imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
            self._update_collider_drag(
                self.preview.screen_to_world(local_x, local_y),
                down=imgui.is_mouse_down(left_button),
            )
            return
        if self._collider_edit_mode and self._viewport_hovered:
            active = self._active_collider()
            if active is not None and self._collider_hit(active[1], local_x, local_y) is not None:
                imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
        if self._viewport_tool == "spline" and self._viewport_hovered:
            spline = self._active_spline()
            if spline is not None:
                try:
                    if self._spline_hit(spline, local_x, local_y) is not None:
                        imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
                except ValueError as error:
                    self.state.error = str(error)
        if self._tile_pick_start is not None:
            layer = self.state.selected_layer
            if (
                layer is None
                or layer.get("type") != "tile"
                or layer.get("id") != self._tile_pick_layer_id
                or self.state.tile_tool not in {"pencil", "picker"}
            ):
                self._tile_pick_start = None
                self._tile_pick_current = None
                self._tile_pick_layer_id = None
                return
            try:
                self._update_tile_pick((local_x, local_y))
                if imgui.is_mouse_down(left_button):
                    return
                self._finish_tile_pick()
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
                self._tile_pick_start = None
                self._tile_pick_current = None
                self._tile_pick_layer_id = None
            return
        if self._tile_edit_drag is not None:
            try:
                if not self._tile_edit_drag_valid(self._tile_edit_drag):
                    self._cancel_tile_edit_drag()
                else:
                    world = self.preview.screen_to_world(local_x, local_y)
                    self._update_tile_edit_drag(
                        self.state.world_to_tile_cell(*world),
                        down=imgui.is_mouse_down(left_button),
                    )
                    return
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
                self._cancel_tile_edit_drag()
                return
        if self._marquee_start is not None:
            if imgui.is_mouse_down(left_button):
                self._marquee_current = (local_x, local_y)
                return
            self._finish_marquee()
            return
        if self._gui_drag_id is not None:
            if not imgui.is_mouse_down(left_button):
                self._gui_drag_id = None
            elif self.preview.mode == "edit":
                record = self.state.selected_gui_element
                if record is None:
                    self._gui_drag_id = None
                else:
                    x = record.get("x", 0)
                    y = record.get("y", 0)
                    if isinstance(x, (int, float)) and isinstance(
                        y,
                        (int, float),
                    ):
                        delta = imgui.get_io().mouse_delta
                        self.state.move_selected_gui_elements_by(
                            delta.x * self.render_target.width
                            / display_width,
                            -delta.y * self.render_target.height
                            / display_height,
                        )
                        return
        if self._pan_button is not None:
            if not imgui.is_mouse_down(self._pan_button):
                self._pan_button = None
            elif self.preview.mode == "edit":
                delta = imgui.get_io().mouse_delta
                self.preview.pan_pixels(delta.x, -delta.y)
                return
            else:
                self._pan_button = None
        if not self._viewport_hovered:
            return
        if (
            self.preview.mode == "edit"
            and imgui.is_mouse_clicked(middle_button)
        ):
            self._pan_button = middle_button
            return
        if (
            self.preview.mode == "edit"
            and imgui.is_mouse_clicked(right_button)
        ):
            self._pan_button = right_button
            return
        if (
            self.preview.mode == "edit"
            and self._space_pan_held
            and imgui.is_mouse_clicked(left_button)
        ):
            self._pan_button = left_button
            return
        if (
            self.preview.mode == "edit"
            and imgui.is_mouse_clicked(left_button)
        ):
            self._shortcut_context = "viewport"
            self._viewport_click_serial += 1
            self._viewport_paste_position = (local_x, local_y)
            if self._collider_edit_mode and self._handle_collider_click(local_x, local_y):
                return
            if self._viewport_tool == "spline" and self._handle_spline_click(local_x, local_y):
                return
        selected_layer = self.state.selected_layer
        tile_tool = self.state.tile_tool
        if (
            self.preview.mode == "edit"
            and selected_layer is not None
            and selected_layer.get("type") == "tile"
            and (
                tile_tool == "picker"
                or (
                    tile_tool == "pencil"
                    and self._shift_pick_held
                )
            )
        ):
            self._tile_fill_start = None
            if imgui.is_mouse_down(left_button):
                try:
                    self._begin_tile_pick((local_x, local_y))
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
            return
        if (
            self.preview.mode == "edit"
            and selected_layer is not None
            and selected_layer.get("type") == "tile"
            and tile_tool in {"select", "move"}
        ):
            self._tile_fill_start = None
            if imgui.is_mouse_clicked(left_button):
                try:
                    world = self.preview.screen_to_world(local_x, local_y)
                    cell = self.state.world_to_tile_cell(*world)
                    io = imgui.get_io()
                    if (
                        tile_tool == "select"
                        and (io.key_ctrl or io.key_super)
                    ):
                        self.state.toggle_tile_selection_cell(*cell)
                    else:
                        self._begin_tile_edit_drag(cell)
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
            return
        valid_paint_tile = self.state.has_tile_stamp
        tile_brush_active = (
            tile_tool == "eraser"
            or (
                tile_tool in {"pencil", "rectangle"}
                and valid_paint_tile
            )
        )
        is_editable_tile_layer = (
            self.preview.mode == "edit"
            and selected_layer is not None
            and selected_layer.get("type") == "tile"
            and tile_brush_active
        )
        if is_editable_tile_layer and tile_tool == "rectangle":
            world_x, world_y = self.preview.screen_to_world(
                local_x,
                local_y,
            )
            cell = self.state.world_to_tile_cell(world_x, world_y)
            if imgui.is_mouse_clicked(left_button):
                self._tile_fill_start = cell
                return
            if (
                self._tile_fill_start is not None
                and imgui.is_mouse_released(left_button)
            ):
                try:
                    painted = self.state.fill_tile_rectangle(
                        self._tile_fill_start,
                        cell,
                    )
                    for column, row, tile_id in painted:
                        painted_cell = self.state.tile_at(column, row)
                        self.preview.apply_tile(
                            column,
                            row,
                            tile_id,
                            flags=(
                                TileFlags.NONE
                                if painted_cell is None
                                else painted_cell.flags
                            ),
                        )
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
                finally:
                    self._tile_fill_start = None
                return
            if self._tile_fill_start is not None:
                return
        if (
            is_editable_tile_layer
            and imgui.is_mouse_down(left_button)
        ):
            world_x, world_y = self.preview.screen_to_world(
                local_x,
                local_y,
            )
            try:
                if self.state.tile_tool == "eraser":
                    column, row = self.state.paint_tile(
                        world_x,
                        world_y,
                        None,
                    )
                    self.preview.apply_tile(column, row, None)
                else:
                    for column, row, tile_id in (
                        self.state.paint_stamp(world_x, world_y)
                    ):
                        painted_cell = self.state.tile_at(column, row)
                        self.preview.apply_tile(
                            column,
                            row,
                            tile_id,
                            flags=(
                                TileFlags.NONE
                                if painted_cell is None
                                else painted_cell.flags
                            ),
                        )
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
            return
        if (
            self.preview.mode == "edit"
            and selected_layer is not None
            and selected_layer.get("type") == "ui"
            and imgui.is_mouse_clicked(left_button)
        ):
            io = imgui.get_io()
            command = io.key_super or io.key_ctrl
            layer_identifier = selected_layer.get("id")
            identifier = (
                self.preview.pick_gui(
                    layer_identifier,
                    local_x,
                    local_y,
                )
                if isinstance(layer_identifier, str)
                else None
            )
            if identifier is not None:
                self.state.select(
                    identifier,
                    additive=io.key_shift,
                    toggle=command,
                )
            elif not io.key_shift and not command:
                self.state.select(None)
            selected_element = self.state.selected_gui_element
            if (
                identifier is not None
                and self.state.is_selected(identifier)
                and selected_element is not None
                and self.state.gui_element_is_free_positioned(
                    selected_element
                )
            ):
                self._gui_drag_id = identifier
            elif identifier is None:
                self._begin_marquee(
                    local_x,
                    local_y,
                    additive=io.key_shift,
                    toggle=command,
                )
            return
        gizmo = imguizmo.im_guizmo
        if self._viewport_tool == "transform" and (gizmo.is_over() or gizmo.is_using()):
            return
        if (
            self.preview.mode == "edit"
            and selected_layer is not None
            and selected_layer.get("type", "object") == "object"
            and imgui.is_mouse_clicked(left_button)
        ):
            io = imgui.get_io()
            command = io.key_super or io.key_ctrl
            identifier = self.preview.pick(local_x, local_y)
            if identifier is not None:
                self.state.select(
                    identifier,
                    additive=io.key_shift,
                    toggle=command,
                )
            elif not io.key_shift and not command:
                self.state.select(None)
            if identifier is None:
                self._begin_marquee(
                    local_x,
                    local_y,
                    additive=io.key_shift,
                    toggle=command,
                )

    def _begin_marquee(
        self,
        x: float,
        y: float,
        *,
        additive: bool,
        toggle: bool,
    ) -> None:
        self._marquee_start = (x, y)
        self._marquee_current = (x, y)
        self._marquee_additive = additive
        self._marquee_toggle = toggle

    def _finish_marquee(self) -> None:
        start = self._marquee_start
        current = self._marquee_current
        self._marquee_start = None
        self._marquee_current = None
        if start is None or current is None:
            return
        if abs(start[0] - current[0]) < 4 and abs(
            start[1] - current[1]
        ) < 4:
            return
        left = min(start[0], current[0])
        right = max(start[0], current[0])
        bottom = min(start[1], current[1])
        top = max(start[1], current[1])
        selected_layer = self.state.selected_layer
        if selected_layer is None:
            return
        if selected_layer.get("type") == "ui":
            layer_identifier = selected_layer.get("id")
            identifiers = (
                self.preview.gui_elements_in_rect(
                    layer_identifier,
                    left,
                    bottom,
                    right,
                    top,
                )
                if isinstance(layer_identifier, str)
                else []
            )
        else:
            identifiers = self.preview.objects_in_rect(
                left,
                bottom,
                right,
                top,
            )
        self.state.select_many(
            identifiers,
            additive=self._marquee_additive,
            toggle=self._marquee_toggle,
        )










    def _draw_status(self) -> None:
        error = self.state.error
        if error != self._status_logged_error:
            if error and error not in self._console_buffer.snapshot():
                self._console_buffer.write(f"{error}\n", error=True)
            self._status_logged_error = error
        flags = (
            imgui.WindowFlags_.no_title_bar
            | imgui.WindowFlags_.no_resize
            | imgui.WindowFlags_.no_move
            | imgui.WindowFlags_.no_scrollbar
            | imgui.WindowFlags_.no_saved_settings
            | imgui.WindowFlags_.no_docking
            | imgui.WindowFlags_.menu_bar
        )
        if imgui.internal.begin_viewport_side_bar(
            "##editor-status-bar",
            imgui.get_main_viewport(),
            imgui.Dir.down,
            imgui.get_frame_height(),
            _flag(flags),
        ):
            if imgui.begin_menu_bar():
                message = self.state.error or self.state.status
                text = " ".join(message.splitlines())
                if self.state.error:
                    suffix = " (see Console)"
                    maximum_width = imgui.get_window_width() * 0.5
                    text = text[:120]
                    if len(" ".join(message.splitlines())) > len(text):
                        text += "..."
                    if imgui.calc_text_size(text + suffix).x > maximum_width:
                        low, high = 0, len(text)
                        while low < high:
                            middle = (low + high + 1) // 2
                            if imgui.calc_text_size(text[:middle] + "..." + suffix).x <= maximum_width:
                                low = middle
                            else:
                                high = middle - 1
                        text = text[:low].rstrip(". ") + "..."
                    text += suffix
                    imgui.text_colored(imgui.ImVec4(1, 0.35, 0.3, 1), text)
                else:
                    imgui.text_unformatted(text)
                if imgui.is_item_hovered():
                    imgui.set_tooltip("See Console for the full error" if error else message)
                imgui.text_disabled(
                    f"  |  {self.state.asset_root}  |  "
                    f"{self.preview.editor_zoom:.0%}"
                )
                imgui.end_menu_bar()
        imgui.end()

    def _draw_console(self) -> None:
        if self._begin_panel("Console"):
            if imgui.button("Clear"):
                self._console_buffer.clear()
            imgui.same_line()
            changed, auto_scroll = imgui.checkbox(
                "Auto-scroll",
                self._console_auto_scroll,
            )
            if changed:
                self._console_auto_scroll = auto_scroll
            imgui.separator()
            if imgui.begin_child("##console-output"):
                imgui.push_font(self._console_font, 0.0)
                try:
                    entries = self._console_buffer.entries()
                    if entries:
                        for text, error in entries:
                            imgui.push_style_color(
                                imgui.Col_.text,
                                imgui.ImVec4(*(
                                    _ERROR_TEXT_COLOR if error else _STDOUT_TEXT_COLOR
                                )),
                            )
                            for line in text.splitlines(keepends=True):
                                imgui.text_unformatted(line.rstrip("\r\n"))
                                if not line.endswith(("\n", "\r")):
                                    imgui.same_line(0, 0)
                            imgui.pop_style_color()
                        if self._console_auto_scroll:
                            imgui.set_scroll_here_y(1.0)
                    else:
                        imgui.text_disabled(
                            "Python stdout and stderr will appear here."
                        )
                finally:
                    imgui.pop_font()
            imgui.end_child()
        imgui.end()

    def _open_export_dialog(self) -> None:
        project = self.project
        export = (
            project.export
            if project is not None
            else ProjectExportConfig()
        )
        self._export_name = export.name
        self._export_version = export.version
        self._export_startup_scene = export.startup_scene
        self._export_server_startup_scene = (
            export.server_startup_scene
        )
        self._export_icon = export.icon or ""
        network = (
            project.network
            if project is not None
            else ProjectNetworkConfig()
        )
        self._network_enabled = network.enabled
        self._network_endpoint = network.endpoint
        self._network_bind = network.bind
        self._network_port = network.port
        self._network_max_clients = network.max_clients
        self._network_snapshot_hz = network.snapshot_hz
        self._network_auto_connect = network.auto_connect
        filename = self.state.filename
        root = (
            project.root
            if project is not None
            else self.state.asset_root
        )
        if not self._export_startup_scene and filename is not None:
            try:
                self._export_startup_scene = filename.relative_to(
                    root
                ).as_posix()
            except ValueError:
                pass
        self._popup_to_open = "Export Game"

    def _start_export(self, *, run_after: bool) -> bool:
        if self._export_thread is not None and self._export_thread.is_alive():
            self.state.error = "an export is already running"
            return False
        if self.state.filename is None:
            self.state.error = "save the startup scene before exporting"
            return False
        try:
            if self.state.dirty:
                self.state.save()
            project = self.project or ProjectConfig.create(
                self.state.asset_root
            )
            if (
                project.script_modules
                and not self._trust_store.is_trusted(project.root)
            ):
                raise ExportError(
                    "trust project scripts before exporting"
                )
            size = self.state.document.settings.get(
                "size",
                list(DEFAULT_SCENE_SIZE),
            )
            if (
                not isinstance(size, list)
                or len(size) != 2
            ):
                raise ExportError(
                    "scene size must contain two positive integers"
                )
            width, height = size
            if (
                not isinstance(width, int)
                or isinstance(width, bool)
                or width <= 0
                or not isinstance(height, int)
                or isinstance(height, bool)
                or height <= 0
            ):
                raise ExportError(
                    "scene size must contain two positive integers"
                )
            presentation = self.state.document.settings.get(
                "presentation",
                {},
            )
            if not isinstance(presentation, dict):
                raise ExportError(
                    "scene presentation settings must be an object"
                )
            scaling = presentation.get("scaling", "fit")
            if scaling not in {
                "fit",
                "integer_fit",
                "fill",
                "stretch",
            }:
                raise ExportError(
                    "scene presentation scaling mode is invalid"
                )
            matte = presentation.get(
                "matte_color",
                [0, 0, 0, 255],
            )
            if (
                not isinstance(matte, list)
                or len(matte) not in {3, 4}
                or any(
                    not isinstance(channel, int)
                    or isinstance(channel, bool)
                    or not 0 <= channel <= 255
                    for channel in matte
                )
            ):
                raise ExportError("scene presentation matte is invalid")
            resizable = presentation.get("resizable", True)
            fullscreen = presentation.get("fullscreen", False)
            if not isinstance(resizable, bool) or not isinstance(
                fullscreen,
                bool,
            ):
                raise ExportError(
                    "scene window settings must be booleans"
                )
            project = project.with_game_settings(
                width=width,
                height=height,
                grid_cell_size=self.state.grid_size,
                scaling=cast(ScalingMode, scaling),
                matte_color=cast(Color, tuple(matte)),
                resizable=resizable,
                fullscreen=fullscreen,
            )
            export = ProjectExportConfig(
                self._export_name.strip(),
                self._export_version.strip(),
                self._export_startup_scene.strip(),
                self._export_icon.strip() or None,
                project.export.hidden_imports,
                project.export.collect_packages,
                self._export_server_startup_scene.strip(),
            )
            project = project.with_export(export)
            project = project.with_network(ProjectNetworkConfig(
                self._network_enabled,
                self._network_endpoint.strip(),
                self._network_bind.strip(),
                self._network_port,
                self._network_max_clients,
                self._network_snapshot_hz,
                self._network_auto_connect,
            ))
            project.write()
            self.project = project
        except (
            ExportError,
            OSError,
            ProjectFormatError,
            SceneFormatError,
            ValueError,
        ) as error:
            self.state.error = str(error)
            return False
        self._export_completed = None
        self._export_output = Queue()
        output_queue = self._export_output

        def build() -> None:
            try:
                result = export_project(
                    project.path,
                    output=output_queue.put,
                )
            except (
                ExportError,
                OSError,
                ProjectFormatError,
            ) as error:
                self._export_completed = (
                    None,
                    str(error),
                    run_after,
                )
            else:
                self._export_completed = (
                    result,
                    None,
                    run_after,
                )

        self.state.error = None
        self.state.status = "Exporting game..."
        self._export_thread = Thread(
            target=build,
            name="g-export",
            daemon=True,
        )
        self._export_thread.start()
        return True

    def _poll_export(self) -> None:
        while True:
            try:
                line = self._export_output.get_nowait()
            except Empty:
                break
            self._console_buffer.write(f"[export] {line}\n")
        completed = self._export_completed
        if completed is None:
            return
        self._export_completed = None
        self._export_thread = None
        result, error, run_after = completed
        if error is not None:
            self.state.error = f"Export failed: {error}"
            print(self.state.error, file=sys.stderr)
            return
        if result is None:
            self.state.error = "Export failed without a result"
            return
        self.state.error = None
        self.state.status = f"Exported game to {result.output_directory}"
        if run_after:
            try:
                subprocess.Popen(
                    [str(result.executable)],
                    cwd=result.output_directory,
                )
            except OSError as launch_error:
                self.state.error = (
                    f"Export succeeded but launch failed: {launch_error}"
                )

    def _draw_modals(self) -> None:
        if self._popup_to_open is not None:
            imgui.open_popup(self._popup_to_open)
            self._popup_to_open = None
        opened, _ = imgui.begin_popup_modal(
            "Convert Tiles to GameObject", None,
            _flag(imgui.WindowFlags_.always_auto_resize),
        )
        if opened:
            _, self._tile_conversion_name = imgui.input_text("Name", self._tile_conversion_name)
            object_layers = [layer for layer in self.state.document.layers if layer.get("type", "object") == "object"]
            choices = [(str(layer["id"]), str(layer["name"])) for layer in object_layers]
            choices.append(("", "Create new object layer..."))
            current = next((i for i, choice in enumerate(choices) if choice[0] == self._tile_conversion_destination), len(choices) - 1)
            _, chosen = self._choice_property(
                "Destination layer", current, [label for _, label in choices],
            )
            self._tile_conversion_destination = choices[chosen][0]
            if not self._tile_conversion_destination:
                _, self._tile_conversion_new_layer = imgui.input_text("New layer name", self._tile_conversion_new_layer)
            _, self._tile_conversion_collisions = imgui.checkbox(
                "Include existing tile collisions", self._tile_conversion_collisions,
            )
            imgui.text_disabled("Source tiles are removed. Undo restores the entire conversion.")
            if imgui.button("Convert"):
                try:
                    if (self.state.revision != self._tile_conversion_revision
                            or self.state.selected_tile_groups != self._tile_conversion_selection):
                        raise ValueError("The tile selection changed. Reopen the conversion dialog.")
                    self.state.convert_selected_tiles(
                        self._tile_conversion_name, self._tile_conversion_destination or None,
                        include_collisions=self._tile_conversion_collisions,
                        new_layer_name=self._tile_conversion_new_layer if not self._tile_conversion_destination else None,
                    )
                except (ValueError, SceneFormatError, OSError) as error:
                    self.state.error = str(error)
                else:
                    self.state.error = None
                    self.preview.reload()
                    imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Cancel"):
                imgui.close_current_popup()
            if self.state.error:
                imgui.text_wrapped(self.state.error)
            imgui.end_popup()
        opened, _ = imgui.begin_popup_modal(
            "Create Python Script",
            None,
            _flag(imgui.WindowFlags_.always_auto_resize),
        )
        if opened:
            imgui.text("Script class name")
            if self._new_script_focus_pending:
                imgui.set_keyboard_focus_here()
                self._new_script_focus_pending = False
            submitted, self._new_script_name = imgui.input_text(
                "##new-script-name",
                self._new_script_name,
                _flag(imgui.InputTextFlags_.enter_returns_true),
            )
            imgui.text_disabled(
                "Example: PlayerController creates player_controller.py"
            )
            from .script_templates import SCRIPT_TEMPLATES
            _, self._new_script_template = self._choice_property(
                "Template##new-script-template",
                self._new_script_template,
                list(SCRIPT_TEMPLATES),
            )
            create = imgui.button("Create") or submitted
            if create:
                try:
                    path = self._create_python_script(
                        self._new_script_name,
                        template=SCRIPT_TEMPLATES[self._new_script_template],
                    )
                    identifier = self._new_script_object_id
                    self._new_script_object_id = None
                    if identifier is not None and self.state.error is None:
                        if self._project_trust_pending:
                            self._pending_script_attachment = (path, identifier)
                        else:
                            self._attach_project_script(path, identifier)
                    imgui.close_current_popup()
                except (
                    OSError,
                    ProjectFormatError,
                    ValueError,
                ) as error:
                    self.state.error = str(error)
                    self._new_script_focus_pending = True
            imgui.same_line()
            if imgui.button("Cancel"):
                self._new_script_object_id = None
                imgui.close_current_popup()
            imgui.end_popup()
        opened, _ = imgui.begin_popup_modal(
            "Export Game",
            None,
            _flag(imgui.WindowFlags_.always_auto_resize),
        )
        if opened:
            _, self._export_name = imgui.input_text(
                "Game Name",
                self._export_name,
            )
            _, self._export_version = imgui.input_text(
                "Version",
                self._export_version,
            )
            _, self._export_startup_scene = imgui.input_text(
                "Startup Scene",
                self._export_startup_scene,
            )
            _, self._export_server_startup_scene = imgui.input_text(
                "Server Startup Scene (optional)",
                self._export_server_startup_scene,
            )
            _, self._export_icon = imgui.input_text(
                "Icon (optional)",
                self._export_icon,
            )
            imgui.separator_text("Networking")
            _, self._network_enabled = imgui.checkbox(
                "Enable Networking",
                self._network_enabled,
            )
            imgui.begin_disabled(not self._network_enabled)
            _, self._network_endpoint = imgui.input_text(
                "Client Endpoint",
                self._network_endpoint,
            )
            _, self._network_auto_connect = imgui.checkbox(
                "Auto-connect Client",
                self._network_auto_connect,
            )
            _, self._network_bind = imgui.input_text(
                "Server Bind",
                self._network_bind,
            )
            _, self._network_port = imgui.input_int(
                "Server Port",
                self._network_port,
                1,
                100,
            )
            _, self._network_max_clients = imgui.input_int(
                "Maximum Clients",
                self._network_max_clients,
                1,
                4,
            )
            _, self._network_snapshot_hz = imgui.input_int(
                "Snapshots / Second",
                self._network_snapshot_hz,
                1,
                5,
            )
            imgui.end_disabled()
            imgui.text_disabled(
                "Paths are relative to the project folder."
            )
            imgui.text_disabled(
                "The project .venv is used when available."
            )
            if imgui.button("Build"):
                if self._start_export(run_after=False):
                    imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Build and Run"):
                if self._start_export(run_after=True):
                    imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Cancel"):
                imgui.close_current_popup()
            imgui.end_popup()
        opened, _ = imgui.begin_popup_modal(
            "Project Scripts",
            None,
            _flag(imgui.WindowFlags_.always_auto_resize),
        )
        if opened:
            imgui.text("This project contains Python scripts.")
            imgui.text(
                "Loading them can access files, the network, and other "
                "programs with your permissions."
            )
            imgui.text("Only trust projects from sources you trust.")
            if imgui.button("Trust and Load"):
                project = self.project
                if project is not None:
                    try:
                        self._trust_store.trust(project.root)
                        project.import_scripts()
                        self._project_trust_pending = False
                        self.state.sync_script_components()
                        self.preview.reload()
                        self.state.error = None
                        self.state.status = "Trusted and loaded project scripts"
                        pending = self._pending_script_attachment
                        self._pending_script_attachment = None
                        if pending is not None:
                            self._attach_project_script(*pending)
                        imgui.close_current_popup()
                    except (
                        ProjectFormatError,
                        ProjectImportError,
                        ValueError,
                    ) as error:
                        self._trust_store.revoke(project.root)
                        self.state.error = str(error)
            imgui.same_line()
            if imgui.button("Open Without Scripts"):
                self._pending_script_attachment = None
                self._project_trust_pending = False
                self.state.status = "Opened without project scripts"
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Cancel"):
                imgui.close_current_popup()
                self._close()
            imgui.end_popup()
        opened, _ = imgui.begin_popup_modal(
            "Unsaved Changes",
            None,
            _flag(imgui.WindowFlags_.always_auto_resize),
        )
        if opened:
            imgui.text("Save changes before continuing?")
            if imgui.button("Save"):
                action = self._pending_action
                self._pending_action = None
                if self.state.filename is None:
                    self._after_save_action = action
                    imgui.close_current_popup()
                    self._show_path_dialog("save")
                else:
                    try:
                        self.state.save()
                        imgui.close_current_popup()
                        if action is not None:
                            self._execute_action(action)
                    except SceneFormatError as error:
                        self.state.error = str(error)
            imgui.same_line()
            if imgui.button("Discard"):
                action = self._pending_action
                self._pending_action = None
                imgui.close_current_popup()
                if action is not None:
                    self._execute_action(action)
            imgui.same_line()
            if imgui.button("Cancel"):
                self._pending_action = None
                imgui.close_current_popup()
            imgui.end_popup()
        opened, _ = imgui.begin_popup_modal(
            "Delete Referenced Asset",
            None,
            _flag(imgui.WindowFlags_.always_auto_resize),
        )
        if opened:
            pending = self._pending_asset_delete
            if pending is None:
                imgui.close_current_popup()
            else:
                source, references = pending
                imgui.text(f"{source.name} is still referenced by:")
                for reference in references[:_MAX_LISTED_REFERENCES]:
                    imgui.bullet_text(reference)
                hidden = len(references) - _MAX_LISTED_REFERENCES
                if hidden > 0:
                    imgui.text_disabled(f"... and {hidden} more")
                imgui.text(
                    "Deleting it leaves these references pointing at a "
                    "missing asset."
                )
                imgui.text_disabled(
                    "Clear or replace them with the asset fields in the "
                    "Inspector. Undo restores the asset."
                )
                if imgui.button("Cancel"):
                    self._pending_asset_delete = None
                    imgui.close_current_popup()
                imgui.same_line()
                if imgui.button("Delete Anyway"):
                    try:
                        self._confirm_pending_asset_delete()
                    except (OSError, ValueError) as error:
                        self.state.error = str(error)
                    imgui.close_current_popup()
            imgui.end_popup()
        opened, _ = imgui.begin_popup_modal(
            "Delete Used Tileset",
            None,
            _flag(imgui.WindowFlags_.always_auto_resize),
        )
        if opened:
            identifier = self._pending_tileset_delete
            usage = (
                self.state.tileset_usage_count(identifier)
                if identifier is not None
                else 0
            )
            imgui.text(
                f"This tileset is used by {usage} tile cell(s)."
            )
            imgui.text("Deleting it will erase those cells.")
            if imgui.button("Delete Anyway") and identifier is not None:
                try:
                    self.state.remove_tileset(
                        identifier,
                        erase_used=True,
                    )
                    self.preview.reload()
                    self._pending_tileset_delete = None
                    imgui.close_current_popup()
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
            imgui.same_line()
            if imgui.button("Cancel"):
                self._pending_tileset_delete = None
                imgui.close_current_popup()
            imgui.end_popup()

    def _request_action(self, action: str) -> None:
        if self.state.dirty:
            self._pending_action = action
            self._popup_to_open = "Unsaved Changes"
        else:
            self._execute_action(action)

    def _execute_action(self, action: str) -> None:
        if action == "new":
            self.state.new()
            self.preview.reload()
        elif action == "open":
            self._show_path_dialog("open")
        elif action == "open_selected":
            try:
                self.state.open(self._path_buffer)
                self.preview.reload()
                self._refresh_assets()
            except SceneFormatError as error:
                self.state.error = str(error)
        elif action == "quit":
            self._close()

    def _show_path_dialog(self, mode: str) -> None:
        if self._native_dialog is not None:
            return
        self._path_dialog = mode
        scene_filters = [
            "g scene files",
            "*.scene.json *.json",
            "All files",
            "*",
        ]
        if mode == "save":
            default_path = Path(
                self.state.filename
                or self.state.asset_root / "scene.scene.json"
            )
            if platform.system() == "Darwin":
                self._native_dialog = _MacSaveDialog(
                    "Save Scene As",
                    default_path.parent,
                    default_path.name,
                )
            else:
                self._native_dialog = portable_file_dialogs.save_file(
                    "Save Scene As",
                    str(default_path),
                    scene_filters,
                )
        elif mode == "open":
            default_path = str(
                self.state.filename or self.state.asset_root
            )
            self._native_dialog = portable_file_dialogs.open_file(
                "Open Scene",
                default_path,
                scene_filters,
            )
        elif mode in {"atlas", "sprite"}:
            self._native_dialog = portable_file_dialogs.open_file(
                (
                    "Select Tile Atlas"
                    if mode == "atlas"
                    else "Select Sprite Image"
                ),
                str(self.state.asset_root),
                [
                    "Images",
                    "*.png *.jpg *.jpeg *.bmp",
                    "All files",
                    "*",
                ],
            )
        else:
            raise ValueError(f"unknown path dialog mode: {mode!r}")

    def _poll_native_dialog(self) -> None:
        dialog = self._native_dialog
        mode = self._path_dialog
        if dialog is None or mode is None or not dialog.ready():
            return
        self._native_dialog = None
        self._path_dialog = None
        if mode == "save":
            selected = dialog.result()
            if not isinstance(selected, str) or not selected:
                self._after_save_action = None
                return
            try:
                self.state.save(selected)
                self._refresh_assets()
                action = self._after_save_action
                self._after_save_action = None
                if action is not None:
                    self._execute_action(action)
            except SceneFormatError as error:
                self.state.error = str(error)
            return
        selected_files = dialog.result()
        if not isinstance(selected_files, list) or not selected_files:
            return
        selected = Path(selected_files[0]).resolve()
        if mode == "open":
            try:
                self.state.open(selected)
                self.preview.reload()
                self._refresh_assets()
            except SceneFormatError as error:
                self.state.error = str(error)
            return
        try:
            relative = selected.relative_to(self.state.asset_root)
        except ValueError:
            self.state.error = (
                "Selected image must be below the asset root"
            )
            return
        if mode == "sprite":
            try:
                self.state.set_selected_sprite_image(relative)
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
            return
        layer = self.state.selected_layer
        if layer is None or layer.get("type") != "tile":
            self.state.error = "Select a tile layer before choosing an atlas"
            return
        layer_id = str(layer.get("id", "tile"))
        self._json_buffers[
            f"tileset-path:{layer_id}"
        ] = relative.as_posix()

    def _save(self) -> None:
        if self.state.filename is None:
            self._show_path_dialog("save")
            return
        try:
            self.state.save()
        except SceneFormatError as error:
            self.state.error = str(error)

    def _update_caption(self) -> None:
        name = (
            self.state.filename.name
            if self.state.filename is not None
            else "Untitled"
        )
        marker = " *" if self.state.dirty else ""
        self.window.set_caption(f"g Editor - {name}{marker}")

    def _window_to_preview(
        self, x: float, y: float
    ) -> tuple[float, float] | None:
        minimum_x, minimum_y, maximum_x, maximum_y = self._viewport_rect
        ui_y = self.window.height - y
        if not (
            minimum_x <= x <= maximum_x
            and minimum_y <= ui_y <= maximum_y
        ):
            return None
        display_width = max(1.0, maximum_x - minimum_x)
        display_height = max(1.0, maximum_y - minimum_y)
        return (
            (x - minimum_x)
            * self.render_target.width
            / display_width,
            (maximum_y - ui_y)
            * self.render_target.height
            / display_height,
        )

    def _tile_picker_available(self, modifiers: int = 0) -> bool:
        layer = self.state.selected_layer
        return (
            self.preview.mode == "edit"
            and layer is not None
            and layer.get("type") == "tile"
            and (
                self.state.tile_tool == "picker"
                or (
                    self.state.tile_tool == "pencil"
                    and (
                        self._shift_pick_held
                        or bool(modifiers & key.MOD_SHIFT)
                    )
                )
            )
        )

    def _begin_tile_pick(self, point: tuple[float, float]) -> None:
        world = self.preview.screen_to_world(*point)
        cell = self.state.world_to_tile_cell(*world)
        layer = self.state.selected_layer
        if layer is None or layer.get("type") != "tile":
            raise ValueError("select a tile layer first")
        identifier = layer.get("id")
        self._tile_fill_start = None
        self._tile_pick_start = cell
        self._tile_pick_current = cell
        self._tile_pick_layer_id = (
            identifier if isinstance(identifier, str) else None
        )

    def _update_tile_pick(self, point: tuple[float, float]) -> None:
        world = self.preview.screen_to_world(*point)
        self._tile_pick_current = self.state.world_to_tile_cell(*world)

    def _finish_tile_pick(self) -> None:
        start = self._tile_pick_start
        current = self._tile_pick_current
        try:
            if start is not None and current is not None:
                self.state.sample_tile_rectangle(start, current)
        finally:
            self._tile_pick_start = None
            self._tile_pick_current = None
            self._tile_pick_layer_id = None

    def _copy_selection(self) -> bool:
        if self.state.selected_layer is not None and self.state.selected_layer.get("type") == "tile" and (
            self._shortcut_context != "hierarchy" or self.state.mixed_layer_selection
        ):
            try:
                copied_world = self.state.copy_world_selection()
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
                return False
            if copied_world is None:
                return False
            self._cancel_tile_edit_drag()
            self._world_clipboard = copied_world
            self._clipboard = None
            self._world_clipboard_click_serial = self._viewport_click_serial
            self._world_paste_count = 0
            self.state.status = "Copied tiles and objects" if copied_world.objects else "Copied tiles"
            return True
        source_selection_ids = tuple(self.state.selected_ids)
        copied = self.state.copy_selected()
        if copied is None:
            return False
        kind, records = copied
        source_parent_identifiers: list[str | None] = []
        if kind == "object":
            originals = {
                str(record.get("id")): record
                for record in self.state.selected_records
            }
            runtime_positions = {
                item.scene_id: (
                    item.world_position.x,
                    item.world_position.y,
                )
                for item in self.preview.selected_runtime_objects(
                    top_level_only=True
                )
                if item.scene_id is not None
            }
            origins = [
                runtime_positions.get(
                    str(record.get("id")),
                    self.state.object_world_transform(originals[str(record.get("id"))])[:2],
                )
                for record in records
            ]
            for record in records:
                original = originals[str(record.get("id"))]
                parent = self.state.object_parent(original)
                source_parent_identifiers.append(
                    str(parent.get("id")) if parent is not None else None
                )
        else:
            originals = {
                str(element.get("id")): element
                for element in self.state.selected_gui_elements
            }
            origins = [
                self.state.gui_element_screen_position(
                    originals[str(record.get("id"))]
                )
                for record in records
            ]
            for record in records:
                original = originals[str(record.get("id"))]
                parent = self.state.gui_element_parent(original)
                source_parent_identifiers.append(
                    str(parent.get("id")) if parent is not None else None
                )
        source_parent_identifier = (
            source_parent_identifiers[0]
            if source_parent_identifiers
            and all(
                identifier == source_parent_identifiers[0]
                for identifier in source_parent_identifiers
            )
            else None
        )
        self._clipboard = _EditorClipboard(
            kind,
            tuple(records),
            tuple(origins),
            self._viewport_click_serial,
            source_selection_ids,
            source_parent_identifier,
            tuple(source_parent_identifiers),
        )
        self._world_clipboard = None
        self._clipboard_paste_count = 0
        self._clipboard_hierarchy_parent = None
        self._clipboard_last_pasted_ids = ()
        self.state.status = (
            "Copied GUI elements"
            if kind == "gui"
            else "Copied game objects"
        )
        self.state.error = None
        return True

    def _cut_selection(self) -> None:
        if not self._copy_selection():
            return
        self.state.delete_selected()
        self.state.status = (
            "Cut tiles and objects"
            if self._world_clipboard is not None and self._world_clipboard.objects
            else "Cut tiles"
            if self._world_clipboard is not None
            else "Cut GUI elements"
            if self._clipboard is not None
            and self._clipboard.kind == "gui"
            else "Cut game objects"
        )

    def _duplicate_selection(self) -> None:
        if not self._copy_selection():
            return
        self._paste_selection()

    def _paste_selection(self) -> None:
        world_clipboard = self._world_clipboard
        if world_clipboard is not None and (
            self._shortcut_context in {"viewport", "layers"}
            or self._shortcut_context == "hierarchy" and self.state.mixed_layer_selection
        ):
            self._cancel_tile_edit_drag()
            try:
                width, height = self.state.selection_grid_size
                columns = rows = self._world_paste_count + 1
                if self._viewport_click_serial > self._world_clipboard_click_serial and self._viewport_paste_position is not None:
                    x, y = self.preview.screen_to_world(*self._viewport_paste_position)
                    columns = round((x - world_clipboard.origin[0]) / width)
                    rows = round((y - world_clipboard.origin[1]) / height)
                self.state.paste_world_selection(world_clipboard, columns, rows)
                self._world_paste_count += 1
                self.preview.reload()
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
            return
        clipboard = self._clipboard
        if clipboard is None:
            return
        parent_identifier: str | None = None
        positions: list[tuple[float, float]] | None = None
        object_parent_identifiers: tuple[str | None, ...] | None = None
        if self._shortcut_context == "hierarchy":
            selected_ids = tuple(self.state.selected_ids)
            if (
                self._clipboard_last_pasted_ids
                and selected_ids == self._clipboard_last_pasted_ids
            ):
                parent_identifier = self._clipboard_hierarchy_parent
            elif (
                not selected_ids
                or selected_ids == clipboard.source_selection_ids
            ):
                parent_identifier = clipboard.source_parent_identifier
            elif len(selected_ids) == 1:
                if clipboard.kind == "object":
                    selected = self.state.selected
                    if selected is not None:
                        parent_identifier = str(selected.get("id"))
                else:
                    selected_gui = self.state.selected_gui_element
                    if (
                        selected_gui is not None
                        and selected_gui.get("type")
                        in GUI_CONTAINER_TYPES
                    ):
                        parent_identifier = str(
                            selected_gui.get("id")
                        )
                    elif selected_gui is not None:
                        parent = self.state.gui_element_parent(selected_gui)
                        if parent is not None:
                            parent_identifier = str(parent.get("id"))
            self._clipboard_hierarchy_parent = parent_identifier
        elif self._shortcut_context == "viewport":
            if clipboard.kind == "object":
                object_parent_identifiers = clipboard.source_parent_identifiers
            if (
                self._viewport_click_serial
                > clipboard.viewport_click_serial
                and self._viewport_paste_position is not None
            ):
                target_x, target_y = self._viewport_paste_position
                if clipboard.kind == "object":
                    target_x, target_y = self.preview.screen_to_world(
                        target_x,
                        target_y,
                    )
                origin_x, origin_y = clipboard.origins[-1]
                delta_x = target_x - origin_x
                delta_y = target_y - origin_y
            else:
                offset = 16 * (self._clipboard_paste_count + 1)
                delta_x = float(offset)
                delta_y = float(offset)
            positions = [
                (x + delta_x, y + delta_y)
                for x, y in clipboard.origins
            ]
        else:
            return
        try:
            clones = self.state.paste_copied(
                clipboard.kind,
                list(clipboard.records),
                parent_identifier=parent_identifier,
                positions=positions,
                object_parent_identifiers=object_parent_identifiers,
            )
        except (ValueError, SceneFormatError) as error:
            self.state.error = str(error)
            return
        if self._shortcut_context == "hierarchy":
            self._clipboard_last_pasted_ids = tuple(
                identifier
                for clone in clones
                if isinstance(identifier := clone.get("id"), str)
            )
        self._clipboard_paste_count += 1

    def _start_selected_rename(self) -> bool:
        if self._shortcut_context == "animation-states":
            if self.preview.mode != "edit" or self._sync_animation_selection() is None:
                return False
            self._begin_rename("animation-state", self._animation_state_name, self._animation_state_name)
            return True
        if self._shortcut_context == "assets":
            path = (
                self._selected_asset
                if self._selected_asset is not None
                else self._selected_asset_directory
            )
            if path == self.state.asset_root:
                return False
            self._begin_rename("asset", str(path), path.name)
            return True
        if self._shortcut_context == "layers":
            layer = self.state.selected_layer
            if layer is None:
                return False
            identifier = layer.get("id")
            if not isinstance(identifier, str):
                return False
            self._begin_rename(
                "layer",
                identifier,
                str(layer.get("name") or "Layer"),
            )
            return True
        if (
            self._shortcut_context not in {"hierarchy", "viewport"}
            or len(self.state.selected_ids) != 1
        ):
            return False
        gui_element = self.state.selected_gui_element
        if gui_element is not None:
            identifier = gui_element.get("id")
            if isinstance(identifier, str):
                self._begin_rename(
                    "gui",
                    identifier,
                    str(
                        gui_element.get("name")
                        or gui_element.get("type")
                        or "Element"
                    ),
                )
                return True
        record = self.state.selected
        if record is None:
            return False
        identifier = record.get("id")
        if not isinstance(identifier, str):
            return False
        self._begin_rename(
            "object",
            identifier,
            str(
                record.get("name")
                or record.get("type")
                or "Object"
            ),
        )
        return True

    def _delete_selection_for_shortcut(self) -> None:
        if self._shortcut_context == "animation":
            return
        if self._shortcut_context == "animation-states":
            try:
                self._remove_animation_state()
            except ValueError as error:
                self.state.error = str(error)
            return
        if self._shortcut_context == "layers":
            try:
                self.state.delete_selected_layer()
            except ValueError as error:
                self.state.error = str(error)
            return
        self._cancel_tile_edit_drag()
        self.state.delete_selected()

    def on_key_press(self, symbol: int, modifiers: int) -> None:
        self._validate_component_tool()
        if (
            symbol in {key.LSHIFT, key.RSHIFT}
            and self.preview.mode == "edit"
            and not imgui.get_io().want_text_input
        ):
            self._shift_pick_held = True
            return
        command = modifiers & (key.MOD_COMMAND | key.MOD_CTRL)
        if (
            self._viewport_tool != "transform"
            and self.preview.mode == "edit"
            and self._shortcut_context == "viewport"
            and not imgui.get_io().want_text_input
        ):
            if symbol == key.ESCAPE:
                self._component_tool_escape()
                return
            if symbol in {key.DELETE, key.BACKSPACE}:
                if self._viewport_tool == "spline":
                    self._cancel_spline_drag()
                    try:
                        self._delete_spline_point()
                    except (ValueError, SceneFormatError) as error:
                        self.state.error = str(error)
                elif command:
                    self._cancel_collider_drag()
                    try:
                        self._delete_collider_point()
                    except ValueError as error:
                        self.state.error = str(error)
                return
        if (
            symbol == key.ESCAPE
            and self.preview.mode == "edit"
            and not imgui.get_io().want_text_input
            and self._shortcut_context == "viewport"
            and self.state.selected_layer is not None
            and self.state.selected_layer.get("type") == "tile"
            and self._asset_picker is None
            and self._rename_target is None
        ):
            self._cancel_tile_edit_drag()
            self._tile_fill_start = None
            self._tile_pick_start = None
            self._tile_pick_current = None
            self._tile_pick_layer_id = None
            self.state.clear_tile_selection()
            if self.state.mixed_layer_selection:
                self.state.select(None)
            return
        if (
            not command
            and not imgui.get_io().want_text_input
            and self.preview.mode == "edit"
            and self.state.selected_layer is not None
            and self.state.selected_layer.get("type") == "tile"
        ):
            tool = {
                key.Q: "select",
                key.W: "move",
                key.S: "pencil",
                key.D: "eraser",
                key.F: "rectangle",
            }.get(symbol)
            if tool is not None:
                self._cancel_tile_edit_drag()
                self._tile_fill_start = None
                self._tile_pick_start = None
                self._tile_pick_current = None
                self._tile_pick_layer_id = None
                self.state.tile_tool = tool
                if tool in {"pencil", "eraser", "rectangle"}:
                    self.state.clear_tile_selection()
                return
            transform = {
                key.R: self.state.rotate_tiles_clockwise,
                key.H: lambda: self.state.toggle_tile_flip("horizontal"),
                key.V: lambda: self.state.toggle_tile_flip("vertical"),
            }.get(symbol)
            if transform is not None:
                self._cancel_tile_edit_drag()
                self._tile_fill_start = None
                self._tile_pick_start = None
                self._tile_pick_current = None
                self._tile_pick_layer_id = None
                if transform():
                    self.preview.reload()
                return
        if symbol == key.ESCAPE and self._rename_target is not None:
            self._rename_target = None
            self._rename_buffer = ""
            return
        if (
            symbol in {key.ENTER, key.NUM_ENTER}
            and self._rename_target is not None
        ):
            if not command:
                try:
                    self._commit_rename()
                except (OSError, ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
                    self._rename_focus_pending = True
            return
        if symbol == key.ESCAPE and self._asset_picker is not None:
            self._asset_picker = None
            self.state.status = "Asset selection cancelled"
            return
        rename_key = (
            key.ENTER if sys.platform == "darwin" else key.F2
        )
        if (
            symbol == rename_key
            and not command
            and not imgui.get_io().want_text_input
            and self._start_selected_rename()
        ):
            return
        if (
            command
            and symbol in {key.ENTER, key.NUM_ENTER}
            and self.preview.mode == "edit"
            and not imgui.get_io().want_text_input
            and self._shortcut_context in {"hierarchy", "layers"}
        ):
            self._delete_selection_for_shortcut()
            return
        if (
            command
            and symbol in {key.Z, key.Y}
            and self.preview.mode == "edit"
            and not imgui.get_io().want_text_input
        ):
            if (
                symbol == key.Y
                or (
                    symbol == key.Z
                    and modifiers & key.MOD_SHIFT
                )
            ):
                self._redo()
            else:
                self._undo()
            return
        if (
            symbol == key.SPACE
            and self.preview.mode == "edit"
            and not imgui.get_io().want_text_input
        ):
            self._space_pan_held = True
            return
        if (
            command
            and symbol in {key.C, key.V, key.X}
            and self.preview.mode == "edit"
            and not imgui.get_io().want_text_input
            and self._shortcut_context in {"hierarchy", "viewport"}
        ):
            if symbol == key.C:
                self._copy_selection()
            elif symbol == key.V:
                self._paste_selection()
            else:
                self._cut_selection()
            return
        if (
            command
            and symbol in {key.C, key.V}
            and not imgui.get_io().want_text_input
            and self._shortcut_context == "assets"
        ):
            try:
                if symbol == key.C:
                    path = (
                        self._selected_asset
                        if self._selected_asset is not None
                        else self._selected_asset_directory
                    )
                    if path != self.state.asset_root:
                        self._copy_asset(path)
                else:
                    self._paste_asset(self._selected_asset_directory)
            except (OSError, ValueError) as error:
                self.state.error = str(error)
            return
        if (
            command
            and symbol == key.D
            and self.preview.mode == "edit"
            and not imgui.get_io().want_text_input
            and self._shortcut_context in {"hierarchy", "viewport"}
        ):
            self._duplicate_selection()
            return
        if (
            command
            and symbol == key.D
            and not imgui.get_io().want_text_input
            and self._shortcut_context == "assets"
        ):
            if self._selected_asset is not None:
                try:
                    self._duplicate_asset(self._selected_asset)
                except (OSError, ValueError) as error:
                    self.state.error = str(error)
            return
        if command and symbol == key.S:
            if modifiers & key.MOD_SHIFT:
                self._show_path_dialog("save")
            else:
                self._save()
            return
        if command and symbol == key.O:
            self._request_action("open")
            return
        if command and symbol == key.N:
            self._request_action("new")
            return
        if command and symbol == key.Q:
            self._request_action("quit")
            return
        if (
            not command
            and not imgui.get_io().want_text_input
            and self.preview.mode == "edit"
            and self.state.selected_layer is not None
            and self.state.selected_layer.get("type", "object")
            == "object"
        ):
            tool = {
                key.W: "move",
                key.E: "rotate",
                key.R: "scale",
            }.get(symbol)
            if tool is not None:
                self._activate_component_tool("transform")
                self._transform_tool = tool
                return
        if (
            symbol in {key.DELETE, key.BACKSPACE}
            and self.preview.mode == "edit"
        ):
            if not imgui.get_io().want_text_input:
                if self._shortcut_context == "assets":
                    path = (
                        self._selected_asset
                        if self._selected_asset is not None
                        else self._selected_asset_directory
                    )
                    if path != self.state.asset_root:
                        try:
                            self._request_delete_asset(path)
                        except (OSError, ValueError) as error:
                            self.state.error = str(error)
                else:
                    self._delete_selection_for_shortcut()
            return
        if (
            self.preview.mode == "play"
            and not imgui.get_io().want_text_input
        ) or self._viewport_hovered:
            self.preview.forward_key(True, symbol, modifiers)

    def on_file_drop(
        self,
        x: int,
        y: int,
        paths: list[str],
    ) -> None:
        screen_y = self.window.height - y
        if self._animation_rect is not None:
            left, top, right, bottom = self._animation_rect
            if left <= x <= right and top <= screen_y <= bottom:
                try:
                    sources = tuple(Path(path).expanduser().resolve() for path in paths)
                    if not sources or any(path.suffix.lower() not in _IMAGE_SUFFIXES for path in sources):
                        raise ValueError("drop image files or one GIF")
                    if len(sources) != 1 and any(path.suffix.lower() == ".gif" for path in sources):
                        raise ValueError("drop a GIF on its own")
                    imported = iter(self._import_external_assets(paths))
                    assets = tuple(
                        source if source.is_relative_to(self.state.asset_root) else next(imported)
                        for source in sources
                    )
                    self._drop_animation_assets(assets)
                except (OSError, ValueError, SceneFormatError, pyglet.image.codecs.ImageDecodeException) as error:
                    self.state.error = f"Could not add animation assets: {error}"
                return
        left, top, right, bottom = self._assets_rect
        if not (
            left <= x <= right
            and top <= screen_y <= bottom
        ):
            return
        try:
            self._import_external_assets(paths)
        except (OSError, ValueError) as error:
            self.state.error = f"Could not import assets: {error}"

    def on_key_release(self, symbol: int, modifiers: int) -> None:
        if symbol in {key.LSHIFT, key.RSHIFT}:
            self._shift_pick_held = False
            return
        if symbol == key.SPACE and self._space_pan_held:
            self._space_pan_held = False
            return
        if self.preview.mode == "play" or self._viewport_hovered:
            self.preview.forward_key(False, symbol, modifiers)

    def on_text(self, text: str) -> None:
        if (
            self.preview.mode == "play"
            and not imgui.get_io().want_text_input
        ):
            self.preview.forward_text(text)

    def on_text_motion(self, motion: int) -> None:
        if (
            self.preview.mode == "play"
            and not imgui.get_io().want_text_input
        ):
            self.preview.forward_text_motion(motion)

    def on_text_motion_select(self, motion: int) -> None:
        if (
            self.preview.mode == "play"
            and not imgui.get_io().want_text_input
        ):
            self.preview.forward_text_motion(motion, select=True)

    def on_mouse_scroll(
        self,
        x: float,
        y: float,
        scroll_x: float,
        scroll_y: float,
    ) -> None:
        del scroll_x
        point = self._window_to_preview(x, y)
        if self.preview.mode == "edit" and point is not None:
            self.preview.zoom_at(
                1.1**scroll_y,
                point[0],
                point[1],
            )

    def on_mouse_press(
        self,
        x: float,
        y: float,
        button: int,
        modifiers: int,
    ) -> None:
        if button == mouse.LEFT:
            screen_y = self.window.height - y
            for field_id, bounds in (
                self._asset_field_button_rects.items()
            ):
                left, top, right, bottom = bounds
                if left <= x <= right and top <= screen_y <= bottom:
                    self._asset_pointer_press = (
                        field_id,
                        bounds,
                    )
                    return
            for path, bounds in self._asset_item_rects.items():
                left, top, right, bottom = bounds
                if left <= x <= right and top <= screen_y <= bottom:
                    self._asset_item_pointer_press = (path, bounds)
                    return
        point = self._window_to_preview(x, y)
        if point is not None:
            if (
                button == mouse.LEFT
                and self._tile_picker_available(modifiers)
            ):
                try:
                    self._begin_tile_pick(point)
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
                return
            if (
                button in {mouse.MIDDLE, mouse.RIGHT}
                and self.preview.mode == "edit"
            ):
                return
            self.preview.forward_mouse(
                "press",
                *point,
                button=button,
                modifiers=modifiers,
            )

    def on_mouse_release(
        self,
        x: float,
        y: float,
        button: int,
        modifiers: int,
    ) -> None:
        if button == mouse.LEFT and self._tile_pick_start is not None:
            point = self._window_to_preview(x, y)
            try:
                if point is not None:
                    self._update_tile_pick(point)
                self._finish_tile_pick()
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
                self._tile_pick_start = None
                self._tile_pick_current = None
                self._tile_pick_layer_id = None
            return
        if button == mouse.LEFT:
            screen_y = self.window.height - y
            pressed_field = self._asset_pointer_press
            self._asset_pointer_press = None
            if pressed_field is not None:
                field_id, bounds = pressed_field
                left, top, right, bottom = bounds
                if left <= x <= right and top <= screen_y <= bottom:
                    target = self._asset_field_targets.get(field_id)
                    if target is not None:
                        self._toggle_asset_picker(target)
                        return
            pressed_asset = self._asset_item_pointer_press
            self._asset_item_pointer_press = None
            if pressed_asset is not None:
                path, bounds = pressed_asset
                left, top, right, bottom = bounds
                if (
                    left <= x <= right
                    and top <= screen_y <= bottom
                ):
                    picker = self._asset_picker
                    if picker is not None:
                        if self._asset_matches_kind(path, picker.kind):
                            try:
                                self._assign_asset_picker(path)
                            except (
                                ValueError,
                                SceneFormatError,
                            ) as error:
                                self.state.error = str(error)
                        return
                    self._selected_asset = path
                    return
        if button in {mouse.LEFT, mouse.MIDDLE, mouse.RIGHT}:
            self._pan_button = None
        point = self._window_to_preview(x, y)
        if point is not None:
            if button == mouse.RIGHT and self.preview.mode == "edit":
                return
            self.preview.forward_mouse(
                "release",
                *point,
                button=button,
                modifiers=modifiers,
            )

    def on_mouse_motion(
        self,
        x: float,
        y: float,
        delta_x: float,
        delta_y: float,
    ) -> None:
        point = self._window_to_preview(x, y)
        if point is not None:
            self.preview.forward_mouse(
                "motion",
                *point,
                delta_x=delta_x,
                delta_y=delta_y,
            )

    def on_mouse_drag(
        self,
        x: float,
        y: float,
        delta_x: float,
        delta_y: float,
        buttons: int,
        modifiers: int,
    ) -> None:
        point = self._window_to_preview(x, y)
        if self._tile_pick_start is not None:
            if point is not None:
                try:
                    self._update_tile_pick(point)
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
            return
        if point is not None:
            self.preview.forward_mouse(
                "drag",
                *point,
                button=buttons,
                modifiers=modifiers,
                delta_x=delta_x,
                delta_y=delta_y,
            )

    def on_close(self) -> Any:
        if self.state.dirty:
            self._request_action("quit")
            return pyglet.event.EVENT_HANDLED
        self._close()
        return pyglet.event.EVENT_HANDLED

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.window.remove_handlers(self)
        if sys.stdout is self._console_stdout:
            sys.stdout = self._original_stdout
        if sys.stderr is self._console_stderr:
            sys.stderr = self._original_stderr
        if self._native_dialog is not None:
            self._native_dialog.kill()
            self._native_dialog = None
        if self._layout_file is not None:
            imgui.save_ini_settings_to_disk(str(self._layout_file))
        self.preview.close()
        self.render_target.delete()
        for _, texture in self._atlas_textures.values():
            texture.delete()
        self._atlas_textures.clear()
        self._asset_trash.cleanup()
        self.imgui_renderer.shutdown()
        imgui.destroy_context()
        self.window.close()


def run_editor(
    filename: str | Path | None = None,
    *,
    asset_root: str | Path | None = None,
    modules: tuple[str, ...] = (),
) -> int:
    scene_path = (
        Path(filename).expanduser().resolve()
        if filename is not None else None
    )
    resolved_asset_root = (
        Path(asset_root).expanduser().resolve()
        if asset_root is not None
        else scene_path.parent if scene_path is not None else None
    )
    if scene_path is not None:
        os.chdir(scene_path.parent)
    for module in modules:
        import_module(module)
    preferences_path = editor_config_directory() / "settings.json"
    preferences = load_editor_preferences(preferences_path)
    from .splash import EditorSplash, center_window

    with EditorSplash(resolve_theme(preferences.theme)) as splash:
        editor = ImGuiEditor(
            scene_path,
            resolved_asset_root,
            modules,
            start_hidden=True,
            preferences=preferences,
            preferences_path=preferences_path,
        )
        splash.finish()
    center_window(editor.window)
    editor.window.set_visible(True)
    editor.window.activate()
    return editor.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open the g scene editor")
    parser.add_argument(
        "scene",
        nargs="?",
        help="scene document to open",
    )
    parser.add_argument(
        "--assets",
        default=None,
        help=(
            "asset root used for resource paths "
            "(defaults to the scene directory)"
        ),
    )
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        help="trusted Python module to import before loading the scene",
    )
    arguments = parser.parse_args(argv)
    try:
        return run_editor(
            arguments.scene,
            asset_root=arguments.assets,
            modules=tuple(arguments.module),
        )
    except (
        OSError,
        ProjectFormatError,
        RuntimeError,
        SceneFormatError,
    ) as error:
        parser.error(str(error))
    return 2
