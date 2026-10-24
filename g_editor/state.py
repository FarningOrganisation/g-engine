from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
from math import ceil, cos, floor, isfinite, radians, sin
from pathlib import Path
from random import Random
from time import monotonic
from typing import Callable, cast
from uuid import uuid4

from pyglet.image.codecs import ImageDecodeException

from g.persistence import (
    SCENE_VERSION,
    JsonValue,
    SceneDocument,
    SceneFormatError,
    scene_types,
)
from g.components import (
    Collider,
    Script,
    describe_component_requirement,
    missing_component_requirements,
)
from g.resources import Image, source_of
from g.components.spline import Spline
from g.inspector import inspector_fields, validate_field_value
from g.tilemap import (
    TileCell,
    TileFlags,
    TileSet,
    decode_tile_cell,
    encode_tile_cell,
)

DEFAULT_SCENE_SIZE = (1280, 720)
SCENE_RESOLUTION_PRESETS = (
    (640, 360),
    (960, 540),
    DEFAULT_SCENE_SIZE,
    (1600, 900),
    (1920, 1080),
)

from .prefab import (
    PREFAB_SUFFIX,
    PrefabDocument,
    instantiate_prefab_record,
    prefab_override_count,
    refresh_prefab_record,
)
from .collider_edit import COLLIDER_SHAPES, GEOMETRY_FIELDS, collider_shape, validate_geometry


GUI_CONTAINER_TYPES = {
    "panel",
    "stack",
    "vbox",
    "hbox",
    "grid",
    "anchor",
    "scroll",
    "list_view",
}
COLLECTION_TILE_GID_CAPACITY = 256
# Component types an object may carry several of (e.g. compound colliders).
REPEATABLE_COMPONENT_TYPES = frozenset({"g.collider"})
GUI_FREE_POSITION_CONTAINER_TYPES = {"panel"}


def _tile_flag_point(
    flags: TileFlags,
    point: tuple[int, int],
) -> tuple[int, int]:
    x, y = point
    if flags & TileFlags.FLIP_DIAGONAL:
        x, y = y, x
    if flags & TileFlags.FLIP_HORIZONTAL:
        x = 1 - x
    if flags & TileFlags.FLIP_VERTICAL:
        y = 1 - y
    return x, y


def transform_tile_flags(
    flags: TileFlags,
    operation: str,
) -> TileFlags:
    def operation_point(point: tuple[int, int]) -> tuple[int, int]:
        x, y = point
        if operation == "rotate_clockwise":
            return 1 - y, x
        if operation == "flip_horizontal":
            return 1 - x, y
        if operation == "flip_vertical":
            return x, 1 - y
        raise ValueError(f"unknown tile transform: {operation}")

    expected = {
        point: _tile_flag_point(flags, operation_point(point))
        for point in ((0, 0), (1, 0), (1, 1), (0, 1))
    }
    for raw_candidate in range(8):
        candidate = TileFlags(raw_candidate)
        if all(
            _tile_flag_point(candidate, point) == transformed
            for point, transformed in expected.items()
        ):
            return candidate
    raise ValueError("tile transform cannot be represented")


def transform_tile_matrix(
    cells: list[TileCell | None],
    width: int,
    height: int,
    *,
    rotation: int = 0,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
) -> tuple[list[TileCell | None], int, int]:
    if (
        width <= 0
        or height <= 0
        or len(cells) != width * height
    ):
        raise ValueError("tile matrix dimensions do not match its cells")
    transformed = list(cells)
    transformed_width = width
    transformed_height = height
    for _ in range(rotation % 4):
        rotated: list[TileCell | None] = [
            None
        ] * (transformed_width * transformed_height)
        rotated_width = transformed_height
        rotated_height = transformed_width
        for y in range(transformed_height):
            for x in range(transformed_width):
                cell = transformed[y * transformed_width + x]
                if cell is not None:
                    cell = TileCell(
                        cell.tile_id,
                        transform_tile_flags(
                            cell.flags,
                            "rotate_clockwise",
                        ),
                    )
                new_x = transformed_height - 1 - y
                new_y = x
                rotated[new_y * rotated_width + new_x] = cell
        transformed = rotated
        transformed_width = rotated_width
        transformed_height = rotated_height
    if flip_horizontal:
        flipped: list[TileCell | None] = [None] * len(transformed)
        for y in range(transformed_height):
            for x in range(transformed_width):
                cell = transformed[y * transformed_width + x]
                if cell is not None:
                    cell = TileCell(
                        cell.tile_id,
                        transform_tile_flags(
                            cell.flags,
                            "flip_horizontal",
                        ),
                    )
                flipped[
                    y * transformed_width
                    + transformed_width
                    - 1
                    - x
                ] = cell
        transformed = flipped
    if flip_vertical:
        flipped = [None] * len(transformed)
        for y in range(transformed_height):
            for x in range(transformed_width):
                cell = transformed[y * transformed_width + x]
                if cell is not None:
                    cell = TileCell(
                        cell.tile_id,
                        transform_tile_flags(
                            cell.flags,
                            "flip_vertical",
                        ),
                    )
                flipped[
                    (transformed_height - 1 - y) * transformed_width + x
                ] = cell
        transformed = flipped
    return transformed, transformed_width, transformed_height

def _collider_records(components: list[JsonValue]) -> list[dict[str, JsonValue]]:
    return [
        component for component in components
        if isinstance(component, dict) and issubclass(
            scene_types.component_type_for_name(str(component.get("type"))), Collider,
        )
    ]


def _area_sensor(components: list[JsonValue], index: JsonValue) -> dict[str, JsonValue]:
    colliders = _collider_records(components)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(colliders):
        raise ValueError("Area collider index must select an existing collider")
    collider = colliders[index]
    properties = collider.get("properties")
    if not isinstance(properties, dict):
        raise SceneFormatError("collider properties must be an object")
    return properties


def _component_area_properties(
    components: list[JsonValue], component: dict[str, JsonValue],
    properties: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    updated = deepcopy(properties)
    if component.get("type") == "g.area":
        _area_sensor(components, updated.get("collider_index", 0))["mode"] = "sensor"
    colliders = _collider_records(components)
    if component in colliders:
        index = colliders.index(component)
        if any(
            isinstance(candidate, dict) and candidate.get("type") == "g.area"
            and isinstance(area_properties := candidate.get("properties"), dict)
            and area_properties.get("collider_index", 0) == index
            for candidate in components
        ):
            updated["mode"] = "sensor"
    return updated

@dataclass
class EditorSnapshot:
    document: SceneDocument
    selected_layer_id: str | None
    selected_ids: list[str]
    selection_anchor_id: str | None
    active_tileset_ids: dict[str, str]
    palette_tile_ids: list[int]
    palette_selection_size: tuple[int, int]
    sampled_tile_cells: list[TileCell | None] | None
    sampled_tile_size: tuple[int, int] | None
    tile_selection_layer_id: str | None
    tile_selection: set[tuple[int, int]]
    selected_layer_ids: list[str]
    extra_tile_selections: dict[str, set[tuple[int, int]]]

@dataclass
class WorldSelectionClipboard:
    asset_root: Path
    layers: dict[str, dict[str, JsonValue]]
    tiles: dict[str, dict[tuple[int, int], TileCell]]
    objects: list[
        tuple[str, str | None, dict[str, JsonValue], tuple[float, float, float, float, float]]
    ]
    origin: tuple[float, float]


@dataclass
class HistoryEntry:
    label: str
    before: EditorSnapshot
    after: EditorSnapshot
    timestamp: float
    undo_effect: Callable[[], None] | None = None
    redo_effect: Callable[[], None] | None = None


def new_scene_document() -> SceneDocument:
    return SceneDocument(
        settings={
            "background": [30, 30, 36, 255],
            "ambient_light": [64, 64, 64, 255],
            "grid_cell_size": 32,
            "grid_snap": True,
            "camera": {
                "position": {"$vec2": [640, 360]},
                "zoom": 1,
                "rotation": 0,
            },
            "physics": {
                "gravity": [0, -980],
                "damping": 1.0,
            },
            "presentation": {
                "scaling": "fit",
                "matte_color": [30, 30, 36, 255],
                "resizable": True,
                "fullscreen": False,
            },
            "size": list(DEFAULT_SCENE_SIZE),
        },
        layers=[
            {
                "type": "tile",
                "id": str(uuid4()),
                "name": "Tiles",
                "visible": True,
                "enabled": True,
                "default": False,
                "tilesets": [],
                "tileset": None,
                "cell_size": [32, 32],
                "chunk_size": 16,
                "opacity": 1.0,
                "offset": [0.0, 0.0],
                "collision_enabled": False,
                "collision_generation": "alpha",
                "collision_alpha_threshold": 255,
                "collision_alpha_inverted": False,
                "navigation_enabled": False,
                "properties": {},
                "chunks": [],
            },
            {
                "type": "object",
                "id": "default",
                "name": "Objects",
                "visible": True,
                "enabled": True,
                "default": True,
                "objects": [],
            },
        ],
        ui_views=[
            {
                "type": "ui",
                "id": "hud",
                "name": "HUD",
                "startup": True,
                "transparent": True,
                "modal": False,
                "pauses_below": True,
                "pauses_scene": False,
                "elements": [],
            }
        ],
        version=SCENE_VERSION,
    )


def new_object_record(
    kind: str = "empty",
    *,
    x: float = 0,
    y: float = 0,
    image: str | None = None,
    image_size: tuple[int, int] | None = None,
) -> dict[str, JsonValue]:
    component: dict[str, JsonValue] | None = None
    display_name = "Game Object"
    if kind == "rectangle":
        display_name = "Rectangle"
        component = {
            "type": "g.rect_renderer",
            "properties": {
                "width": 64,
                "height": 64,
                "fill": [255, 255, 255, 255],
                "outline": None,
                "outline_thickness": 1,
                "visible": True,
                "screen_space": False,
                "enabled": True,
            },
        }
    elif kind == "circle":
        display_name = "Circle"
        component = {
            "type": "g.circle_renderer",
            "properties": {
                "radius": 32,
                "fill": [255, 255, 255, 255],
                "outline": None,
                "outline_thickness": 1,
                "visible": True,
                "screen_space": False,
                "enabled": True,
            },
        }
    elif kind == "text":
        display_name = "Text"
        component = {
            "type": "g.text_renderer",
            "properties": {
                "text": "Text",
                "font": None,
                "font_size": 24,
                "color": [255, 255, 255, 255],
                "opacity": 255,
                "visible": True,
                "screen_space": False,
                "enabled": True,
            },
        }
    elif kind == "sprite":
        if image is not None and not image:
            raise ValueError("sprite image path must not be empty")
        if image is not None and image_size is None:
            raise ValueError("sprite image size is required")
        if image_size is not None:
            width, height = image_size
            if width <= 0 or height <= 0:
                raise ValueError("sprite image size must be positive")
        display_name = Path(image).stem if image else "Sprite"
        component = {
            "type": "g.sprite_renderer",
            "properties": {
                "image": image,
                "opacity": 255,
                "visible": True,
                "screen_space": False,
                "enabled": True,
            },
        }
    elif kind == "background":
        display_name = "Background"
        component = scene_types.component_template("g.repeating_image_renderer")
    elif kind in {"area_box", "area_circle"}:
        display_name = "Area"
        component = {
            "type": "g.collider",
            "properties": {
                **(
                    {"shape": "box", "width": 64, "height": 64}
                    if kind == "area_box" else {"shape": "circle", "radius": 32}
                ),
                "mode": "sensor",
                "enabled": True,
            },
        }
    elif kind != "empty":
        raise ValueError(f"unknown editor object kind: {kind!r}")

    components: list[JsonValue] = []
    if component is not None:
        components.append(component)
    if kind in {"area_box", "area_circle"}:
        components.append({"type": "g.area", "properties": {"collider_index": 0, "enabled": True}})
    if kind == "rectangle":
        components.append(
            {
                "type": "g.collider",
                "properties": {
                    "shape": "box",
                    "width": 64,
                    "height": 64,
                    "enabled": True,
                },
            }
        )
    elif kind == "circle":
        components.append(
            {
                "type": "g.collider",
                "properties": {
                    "shape": "circle",
                    "radius": 32,
                    "enabled": True,
                },
            }
        )
    return {
        "id": str(uuid4()),
        "type": "g.game_object",
        "name": display_name,
        "enabled": True,
        "visible": True,
        "z_index": 0,
        "render_behind_parent": False,
        "offset_x": 0,
        "offset_y": 0,
        "scale_x": 1,
        "scale_y": 1,
        "opacity": 255,
        "transform": {
            "position": {"$vec2": [x, y]},
            "rotation": 0,
            "scale": [1, 1],
        },
        "speed": {"$vec2": [0, 0]},
        "properties": {},
        "script_properties": {},
        "components": components,
        "children": [],
    }


def walk_object_records(
    records: list[dict[str, JsonValue]],
) -> tuple[dict[str, JsonValue], ...]:
    result: list[dict[str, JsonValue]] = []

    def visit(record: dict[str, JsonValue]) -> None:
        result.append(record)
        children = record.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    for record in records:
        visit(record)
    return tuple(result)


def layer_objects(
    layer: Mapping[str, object],
) -> list[dict[str, JsonValue]]:
    if layer.get("type", "object") != "object":
        return []
    objects = layer.get("objects")
    if not isinstance(objects, list):
        raise SceneFormatError("scene layer objects must be an array")
    return cast(list[dict[str, JsonValue]], objects)


def default_layer(
    document: SceneDocument,
) -> dict[str, JsonValue]:
    return next(
        (
            layer
            for layer in document.layers
            if layer.get("default") is True
        ),
        document.layers[0],
    )


def position(record: Mapping[str, object]) -> tuple[float, float]:
    transform = record.get("transform")
    if not isinstance(transform, dict):
        return 0, 0
    raw_position = transform.get("position")
    if not isinstance(raw_position, dict):
        return 0, 0
    coordinates = raw_position.get("$vec2")
    if (
        not isinstance(coordinates, list)
        or len(coordinates) != 2
        or not all(isinstance(value, (int, float)) for value in coordinates)
    ):
        return 0, 0
    return float(coordinates[0]), float(coordinates[1])


def set_position(
    record: dict[str, JsonValue], x: float, y: float
) -> None:
    transform = record.setdefault("transform", {})
    if not isinstance(transform, dict):
        transform = {}
        record["transform"] = transform
    transform["position"] = {"$vec2": [x, y]}


def grid_coordinates(extent: int, cell_size: int) -> range:
    if extent <= 0 or cell_size <= 0:
        raise ValueError("grid extent and cell size must be positive")
    limit = ((extent + cell_size - 1) // cell_size) * cell_size
    return range(-limit, limit + 1, cell_size)


def new_gui_element_record(
    kind: str,
    *,
    x: float = 310,
    y: float = 276,
) -> dict[str, JsonValue]:
    common: dict[str, JsonValue] = {
        "id": str(uuid4()),
        "type": kind,
        "name": kind.title(),
        "x": x,
        "y": y,
        "visible": True,
        "enabled": True,
        "z_index": 0,
    }
    if kind == "panel":
        common.update(
            {
                "width": 240,
                "height": 140,
                "color": [40, 40, 40, 230],
                "children": [],
            }
        )
    elif kind == "label":
        common.update(
            {
                "width": 180,
                "height": 32,
                "text": "Label",
                "font": None,
                "font_size": 20,
                "color": [255, 255, 255, 255],
                "align": "center",
                "multiline": False,
            }
        )
    elif kind == "button":
        common.update(
            {
                "width": 180,
                "height": 48,
                "text": "Button",
                "background": [55, 65, 85, 255],
                "hover": [75, 90, 120, 255],
                "pressed": [35, 42, 58, 255],
                "disabled": [65, 65, 65, 255],
                "text_color": [255, 255, 255, 255],
                "font": None,
                "font_size": 16,
            }
        )
    elif kind == "toggle":
        common.update(
            {
                "width": 180,
                "height": 48,
                "text": "Toggle",
                "value": False,
                "background": [55, 65, 85, 255],
                "hover": [75, 90, 120, 255],
                "pressed": [35, 42, 58, 255],
                "disabled": [65, 65, 65, 255],
                "text_color": [255, 255, 255, 255],
                "font": None,
                "font_size": 16,
            }
        )
    elif kind == "slider":
        common.update(
            {
                "width": 200,
                "height": 24,
                "minimum": 0,
                "maximum": 100,
                "value": 50,
            }
        )
    elif kind == "progress_bar":
        common.update(
            {
                "name": "Progress Bar",
                "width": 200,
                "height": 20,
                "minimum": 0,
                "maximum": 100,
                "value": 50,
                "background": [55, 60, 72, 255],
                "fill": [75, 185, 105, 255],
            }
        )
    elif kind == "image":
        common.update(
            {
                "name": "Image",
                "width": 160,
                "height": 120,
                "image": None,
                "fit": "contain",
            }
        )
    elif kind == "checkbox":
        common.update(
            {
                "name": "Checkbox",
                "width": 180,
                "height": 28,
                "text": "Checkbox",
                "value": False,
                "box": [70, 78, 96, 255],
                "check": [235, 240, 255, 255],
                "text_color": [235, 240, 255, 255],
                "disabled": [90, 90, 95, 255],
                "font": None,
                "font_size": 14,
            }
        )
    elif kind == "dropdown":
        common.update(
            {
                "name": "Dropdown",
                "width": 200,
                "height": 36,
                "options": ["Option 1", "Option 2", "Option 3"],
                "selected_index": 0,
                "background": [55, 65, 85, 255],
                "options_color": [42, 50, 68, 255],
                "selected_color": [75, 90, 120, 255],
                "disabled": [65, 65, 65, 255],
                "text_color": [255, 255, 255, 255],
                "font": None,
                "font_size": 14,
            }
        )
    elif kind == "list_view":
        common.update(
            {
                "name": "List View",
                "width": 240,
                "height": 160,
                "selected_index": None,
                "spacing": 4,
                "selected_color": [75, 90, 120, 180],
                "scroll_speed": 32,
                "scrollbar_track": [38, 43, 55, 180],
                "scrollbar_thumb": [105, 115, 140, 230],
                "scrollbar_width": 10,
                "minimum_thumb": 24,
                "children": [],
            }
        )
    elif kind == "text_input":
        common.update(
            {
                "width": 200,
                "height": 28,
                "text": "",
                "color": [255, 255, 255, 255],
                "text_color": [0, 0, 0, 255],
                "caret_color": [0, 0, 0, 255],
            }
        )
    elif kind == "scroll":
        common.update(
            {
                "name": "Scroll View",
                "width": 240,
                "height": 160,
                "scroll_speed": 32,
                "horizontal": False,
                "scrollbar_track": [38, 43, 55, 180],
                "scrollbar_thumb": [105, 115, 140, 230],
                "scrollbar_width": 10,
                "minimum_thumb": 24,
                "children": [],
            }
        )
    elif kind == "stack":
        common.update(
            {
                "name": "Stack Layout",
                "width": 240,
                "height": 160,
                "padding": [8, 8, 8, 8],
                "children": [],
            }
        )
    elif kind in {"vbox", "hbox"}:
        common.update(
            {
                "name": (
                    "Vertical Layout"
                    if kind == "vbox"
                    else "Horizontal Layout"
                ),
                "width": 240 if kind == "vbox" else 360,
                "height": 240 if kind == "vbox" else 100,
                "spacing": 8,
                "padding": [12, 12, 12, 12],
                "alignment": "center",
                "children": [],
            }
        )
    elif kind == "grid":
        common.update(
            {
                "name": "Grid Layout",
                "width": 360,
                "height": 240,
                "columns": 2,
                "row_spacing": 8,
                "column_spacing": 8,
                "padding": [12, 12, 12, 12],
                "children": [],
            }
        )
    elif kind == "anchor":
        common.update(
            {
                "name": "Anchor Layout",
                "width": 320,
                "height": 220,
                "horizontal": "center",
                "vertical": "center",
                "anchor_margin": [12, 12, 12, 12],
                "children": [],
            }
        )
    else:
        raise ValueError(f"unknown GUI element kind: {kind!r}")
    return common


def layer_gui_elements(
    layer: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    if layer.get("type") != "ui":
        return []
    elements = layer.get("elements")
    if not isinstance(elements, list):
        raise SceneFormatError("UI view elements must be an array")
    if not all(isinstance(element, dict) for element in elements):
        raise SceneFormatError("UI view elements must contain objects")
    return cast(list[dict[str, JsonValue]], elements)


def walk_gui_element_records(
    records: list[dict[str, JsonValue]],
) -> tuple[dict[str, JsonValue], ...]:
    result: list[dict[str, JsonValue]] = []

    def visit(record: dict[str, JsonValue]) -> None:
        result.append(record)
        children = record.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    for record in records:
        visit(record)
    return tuple(result)


def record_z_index(record: Mapping[str, JsonValue]) -> int:
    value = record.get("z_index", 0)
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool)
        else 0
    )


def normalize_record_order(
    records: list[dict[str, JsonValue]],
) -> None:
    flattened: list[dict[str, JsonValue]] = []

    def visit(record: dict[str, JsonValue]) -> None:
        children = record.get("children")
        if isinstance(children, list):
            for child in children:
                if (
                    isinstance(child, dict)
                    and not child.get("render_behind_parent", False)
                ):
                    visit(child)
        flattened.append(record)
        if isinstance(children, list):
            for child in children:
                if (
                    isinstance(child, dict)
                    and child.get("render_behind_parent", False)
                ):
                    visit(child)

    for record in records:
        visit(record)
    highest = len(flattened) - 1
    for index, record in enumerate(flattened):
        record["z_index"] = highest - index


def normalize_gui_order(
    records: list[dict[str, JsonValue]],
) -> None:
    flattened = list(walk_gui_element_records(records))
    highest = len(flattened) - 1
    for index, record in enumerate(flattened):
        record["z_index"] = highest - index


class EditorState:
    def __init__(
        self,
        filename: Path | None = None,
        asset_root: Path | None = None,
        modules: tuple[str, ...] = (),
    ) -> None:
        self.filename = filename
        self.asset_root = (asset_root or Path.cwd()).resolve()
        self.modules = modules
        self.document = (
            SceneDocument.read(filename)
            if filename is not None
            else new_scene_document()
        )
        self._normalize_scene_settings()
        self._normalize_hierarchy_order()
        self.dirty = False
        self.selected_ids: list[str] = []
        self.selection_anchor_id: str | None = None
        selected_layer = default_layer(self.document)
        identifier = selected_layer.get("id")
        self.selected_layer_id = (
            identifier if isinstance(identifier, str) else None
        )
        self.runtime_mode = "edit"
        self._selected_layer_ids: list[str] = []
        self._extra_tile_selections: dict[str, set[tuple[int, int]]] = {}
        self.tile_tool = "pencil"
        self._tile_selection_layer_id: str | None = None
        self._tile_selection: set[tuple[int, int]] = set()
        self.paint_tile_id = 0
        self.active_tileset_ids: dict[str, str] = {}
        self.palette_tile_ids: list[int] = [0]
        self.palette_selection_size = (1, 1)
        self._sampled_tile_cells: list[TileCell | None] | None = None
        self._sampled_tile_size: tuple[int, int] | None = None
        self.tile_rotation = 0
        self.tile_flip_horizontal = False
        self.tile_flip_vertical = False
        self._tile_transform_source: (
            dict[str, dict[tuple[int, int], TileCell]] | None
        ) = None
        self._tile_transform_base_layers: (
            dict[str, dict[str, JsonValue]] | None
        ) = None
        self.random_tile_brush = False
        self.status = "Ready"
        self.error: str | None = None
        self.revision = 0
        self._history: list[HistoryEntry] = []
        self._history_cursor = 0
        self._history_limit = 200
        self._history_current = self._capture_snapshot()
        self._saved_document = deepcopy(self.document)

    @property
    def records(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(
            record
            for layer in self.document.layers
            for record in walk_object_records(layer_objects(layer))
        )

    @property
    def selected_layer(self) -> dict[str, JsonValue] | None:
        return next(
            (
                layer
                for layer in (
                    *self.document.layers,
                    *self.document.ui_views,
                )
                if layer.get("id") == self.selected_layer_id
            ),
            None,
        )

    @property
    def selected_ui_view(self) -> dict[str, JsonValue] | None:
        selected = self.selected_layer
        return (
            selected
            if selected is not None and selected.get("type") == "ui"
            else None
        )

    @property
    def selected_layers(self) -> tuple[dict[str, JsonValue], ...]:
        identifiers = (
            self._selected_layer_ids
            if self.selected_layer_id in self._selected_layer_ids
            else [self.selected_layer_id]
        )
        return tuple(
            layer for layer in (*self.document.layers, *self.document.ui_views)
            if layer.get("id") in identifiers
        )

    @property
    def mixed_layer_selection(self) -> bool:
        kinds = {str(layer.get("type", "object")) for layer in self.selected_layers}
        return "tile" in kinds and "object" in kinds

    @property
    def selection_grid_size(self) -> tuple[float, float]:
        layers = [layer for layer in self.selected_layers if layer.get("type") == "tile"]
        if not layers:
            raise ValueError("Select a tile layer first")
        sizes = [self._tile_pair(layer.get("cell_size"), "cell_size") for layer in layers]
        if any(size != sizes[0] for size in sizes):
            raise ValueError("Select tile layers with matching cell sizes to move them together")
        return sizes[0]

    @property
    def selected_id(self) -> str | None:
        return self.selected_ids[-1] if self.selected_ids else None

    @selected_id.setter
    def selected_id(self, identifier: str | None) -> None:
        self.selected_ids = [] if identifier is None else [identifier]
        self.selection_anchor_id = identifier

    @property
    def selected(self) -> dict[str, JsonValue] | None:
        if self.selected_id is None:
            return None
        return next(
            (
                record
                for record in self.records
                if record.get("id") == self.selected_id
            ),
            None,
        )

    @property
    def selected_records(self) -> tuple[dict[str, JsonValue], ...]:
        by_identifier = {
            record.get("id"): record
            for record in self.records
            if isinstance(record.get("id"), str)
        }
        return tuple(
            by_identifier[identifier]
            for identifier in self.selected_ids
            if identifier in by_identifier
        )

    @property
    def selected_gui_element(self) -> dict[str, JsonValue] | None:
        if self.selected_id is None:
            return None
        view = self.selected_ui_view
        if view is None:
            return None
        return next(
            (
                element
                for element in walk_gui_element_records(
                    layer_gui_elements(view)
                )
                if element.get("id") == self.selected_id
            ),
            None,
        )

    @property
    def selected_gui_elements(
        self,
    ) -> tuple[dict[str, JsonValue], ...]:
        view = self.selected_ui_view
        if view is None:
            return ()
        by_identifier = {
            element.get("id"): element
            for element in walk_gui_element_records(
                layer_gui_elements(view)
            )
            if isinstance(element.get("id"), str)
        }
        return tuple(
            by_identifier[identifier]
            for identifier in self.selected_ids
            if identifier in by_identifier
        )

    @property
    def grid_size(self) -> int:
        value = self.document.settings.get("grid_cell_size", 32)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return 32

    @property
    def snap_enabled(self) -> bool:
        return bool(self.document.settings.get("grid_snap", True))

    def is_selected(self, identifier: str) -> bool:
        return identifier in self.selected_ids

    def select(
        self,
        identifier: str | None,
        *,
        additive: bool = False,
        toggle: bool = False,
    ) -> None:
        if identifier is None:
            self.selected_id = None
            return
        target_layer_id: str | None = None
        target_kind: str | None = None
        for layer in self.document.layers:
            if any(
                record.get("id") == identifier
                for record in walk_object_records(layer_objects(layer))
            ):
                layer_id = layer.get("id")
                if isinstance(layer_id, str):
                    target_layer_id = layer_id
                    target_kind = "object"
                break
        if target_layer_id is None:
            for layer in self.document.ui_views:
                if any(
                    element.get("id") == identifier
                    for element in walk_gui_element_records(
                        layer_gui_elements(layer)
                    )
                ):
                    layer_id = layer.get("id")
                    if isinstance(layer_id, str):
                        target_layer_id = layer_id
                        target_kind = "ui"
                    break
        if target_layer_id is None:
            raise ValueError(f"unknown selectable item: {identifier!r}")
        current_kind = (
            "ui"
            if self.selected_gui_elements
            else "object"
            if self.selected_records
            else None
        )
        mixed_target = self.mixed_layer_selection and any(
            layer.get("id") == target_layer_id for layer in self.selected_layers
        )
        if (
            (self.selected_layer_id != target_layer_id and not mixed_target)
            or current_kind not in {None, target_kind}
        ):
            self.selected_ids.clear()
            additive = False
            toggle = False
        if not mixed_target:
            self._selected_layer_ids = [target_layer_id]
            self.selected_layer_id = target_layer_id
        if toggle:
            if identifier in self.selected_ids:
                self.selected_ids.remove(identifier)
                if self.selection_anchor_id == identifier:
                    self.selection_anchor_id = (
                        self.selected_id
                    )
                return
            self.selected_ids.append(identifier)
        elif additive:
            if identifier not in self.selected_ids:
                self.selected_ids.append(identifier)
            else:
                self.selected_ids.remove(identifier)
                self.selected_ids.append(identifier)
        else:
            self.selected_ids = [identifier]
        self.selection_anchor_id = identifier

    def select_range(
        self,
        identifier: str,
        ordered_ids: list[str],
        *,
        additive: bool = False,
    ) -> None:
        if identifier not in ordered_ids:
            self.select(identifier, additive=additive)
            return
        anchor = self.selection_anchor_id
        if anchor not in ordered_ids:
            anchor = self.selected_id
        if anchor not in ordered_ids:
            self.select(identifier, additive=additive)
            return
        start = ordered_ids.index(anchor)
        end = ordered_ids.index(identifier)
        selected_range = ordered_ids[min(start, end) : max(start, end) + 1]
        if additive:
            combined = list(self.selected_ids)
            for candidate in selected_range:
                if candidate not in combined:
                    combined.append(candidate)
            self.selected_ids = combined
        else:
            self.selected_ids = list(selected_range)
        if identifier in self.selected_ids:
            self.selected_ids.remove(identifier)
        self.selected_ids.append(identifier)
        self.selection_anchor_id = anchor

    def select_many(
        self,
        identifiers: list[str],
        *,
        additive: bool = False,
        toggle: bool = False,
    ) -> None:
        valid = list(dict.fromkeys(identifiers))
        if not valid:
            if not additive and not toggle:
                self.select(None)
            return
        if not additive and not toggle:
            self.select(valid[0])
            self.selected_ids = []
        for identifier in valid:
            self.select(
                identifier,
                additive=True,
                toggle=toggle,
            )
        self.selection_anchor_id = self.selected_id

    def select_layer(self, identifier: str, *, toggle: bool = False) -> None:
        if not any(
            layer.get("id") == identifier
            for layer in (
                *self.document.layers,
                *self.document.ui_views,
            )
        ):
            raise ValueError(
                f"unknown scene layer or UI view: {identifier!r}"
            )
        identifiers = [str(layer["id"]) for layer in self.selected_layers]
        target = next(
            layer for layer in (*self.document.layers, *self.document.ui_views)
            if layer.get("id") == identifier
        )
        if toggle and target.get("type") != "ui" and all(
            layer.get("type") != "ui" for layer in self.selected_layers
        ):
            if identifier in identifiers:
                identifiers.remove(identifier)
            else:
                identifiers.append(identifier)
        else:
            identifiers = [identifier]
        if identifiers != [str(layer["id"]) for layer in self.selected_layers]:
            self.clear_tile_selection()
        self._selected_layer_ids = identifiers
        # Tile tools use the primary tilemap as their movement grid.
        self.selected_layer_id = next(
            (str(layer["id"]) for layer in self.document.layers
             if layer.get("id") in identifiers and layer.get("type") == "tile"),
            identifiers[-1] if identifiers else None,
        )
        if self.mixed_layer_selection:
            self.tile_tool = "select"
        self.selected_id = None

    def copy_selected(
        self,
    ) -> tuple[str, list[dict[str, JsonValue]]] | None:
        if self.selected_gui_elements:
            selected = set(self.selected_ids)
            records = [
                element
                for element in self.selected_gui_elements
                if not self._gui_has_selected_ancestor(
                    element,
                    selected,
                )
            ]
            return "gui", deepcopy(records)
        if self.selected_records:
            selected = set(self.selected_ids)
            records = [
                record
                for record in self.selected_records
                if not self._object_has_selected_ancestor(
                    record,
                    selected,
                )
            ]
            return "object", deepcopy(records)
        return None

    @property
    def selected_prefab_record(self) -> dict[str, JsonValue] | None:
        return self.selected or (
            self.selected_ui_view
            if self.selected_gui_element is None
            else None
        )

    @property
    def selected_prefab_metadata(self) -> dict[str, JsonValue] | None:
        record = self.selected_prefab_record
        if record is None:
            return None
        metadata = record.get("prefab")
        return (
            cast(dict[str, JsonValue], metadata)
            if isinstance(metadata, dict)
            else None
        )

    def prefab_document_for_record(
        self,
        record: dict[str, JsonValue],
        existing: PrefabDocument | None = None,
    ) -> PrefabDocument:
        return PrefabDocument.create(
            record,
            prefab_id=existing.prefab_id if existing is not None else None,
            revision=existing.revision + 1 if existing is not None else 1,
            kind=(
                existing.kind
                if existing is not None
                else (
                    "ui_view"
                    if record.get("type") == "ui"
                    else "game_object"
                )
            ),
        )

    def instantiate_prefab(
        self,
        filename: str | Path,
        *,
        parent_identifier: str | None = None,
        position: tuple[float, float] | None = None,
    ) -> dict[str, JsonValue]:
        path = Path(filename).expanduser().resolve()
        source = self._project_relative_prefab(path)
        prefab = PrefabDocument.read(path)
        if prefab.kind != "game_object":
            raise ValueError(
                "UI-view prefabs must be added to the UI Views section"
            )
        layer = self.selected_layer
        if layer is None or layer.get("type", "object") != "object":
            raise ValueError("select an object layer before adding a prefab")
        record = instantiate_prefab_record(prefab, source)
        if position is not None:
            set_position(record, *position)
        target = layer_objects(layer)
        if parent_identifier is not None:
            parent = next(
                (
                    candidate
                    for candidate in walk_object_records(target)
                    if candidate.get("id") == parent_identifier
                ),
                None,
            )
            if parent is None:
                raise ValueError("prefab parent is unavailable")
            children = parent.setdefault("children", [])
            if not isinstance(children, list):
                raise SceneFormatError("object children must be an array")
            target = cast(list[dict[str, JsonValue]], children)
        target.append(record)
        normalize_record_order(layer_objects(layer))
        identifier = record.get("id")
        self.selected_id = (
            identifier if isinstance(identifier, str) else None
        )
        self.mark_changed(f"Instantiated prefab {path.name}")
        return record

    def instantiate_ui_view_prefab(
        self,
        filename: str | Path,
    ) -> dict[str, JsonValue]:
        path = Path(filename).expanduser().resolve()
        source = self._project_relative_prefab(path)
        prefab = PrefabDocument.read(path)
        if prefab.kind != "ui_view":
            raise ValueError(
                "game-object prefabs must be added to an object layer"
            )
        record = instantiate_prefab_record(prefab, source)
        base_name = str(record.get("name") or path.name.removesuffix(
            PREFAB_SUFFIX
        ))
        existing_names = {
            str(view.get("name"))
            for view in self.document.ui_views
        }
        name = base_name
        index = 2
        while name in existing_names:
            name = f"{base_name} {index}"
            index += 1
        record["name"] = name
        record["startup"] = False
        self.document.ui_views.append(record)
        identifier = record.get("id")
        if not isinstance(identifier, str):
            raise SceneFormatError("UI-view prefab instance ID is missing")
        self.select_layer(identifier)
        self.mark_changed(f"Instantiated UI-view prefab {path.name}")
        return record

    def link_selected_to_prefab(
        self,
        prefab: PrefabDocument,
        source: str,
    ) -> dict[str, JsonValue]:
        record = self.selected_prefab_record
        if record is None:
            raise ValueError("select a game object or UI view first")
        expected_kind = (
            "ui_view" if record.get("type") == "ui" else "game_object"
        )
        if prefab.kind != expected_kind:
            raise ValueError(
                f"cannot link {expected_kind.replace('_', ' ')} "
                f"to a {prefab.kind.replace('_', ' ')} prefab"
            )
        linked = instantiate_prefab_record(prefab, source)
        old_identifier = record.get("id")
        new_identifier = linked.get("id")
        if isinstance(old_identifier, str):
            linked["id"] = old_identifier
            if isinstance(new_identifier, str):
                self._replace_object_references(
                    linked,
                    new_identifier,
                    old_identifier,
                )
        if prefab.kind == "ui_view":
            linked["startup"] = bool(record.get("startup", False))
            self._replace_ui_view_record(record, linked)
            if isinstance(old_identifier, str):
                self.select_layer(old_identifier)
        else:
            transform = record.get("transform")
            if isinstance(transform, dict):
                linked["transform"] = deepcopy(transform)
            self._replace_object_record(record, linked)
            self.selected_id = (
                old_identifier if isinstance(old_identifier, str) else None
            )
        return linked

    def refresh_prefab_instances(
        self,
        prefab: PrefabDocument,
        source: str,
        *,
        record_history: bool = True,
    ) -> int:
        changed = 0
        if prefab.kind == "ui_view":
            changed += self._refresh_prefab_records(
                self.document.ui_views,
                prefab,
                source,
                nested=False,
            )
        else:
            for layer in self.document.layers:
                if layer.get("type", "object") != "object":
                    continue
                changed += self._refresh_prefab_records(
                    layer_objects(layer),
                    prefab,
                    source,
                )
        if changed and record_history:
            self.mark_changed(
                f"Updated {changed} prefab instance"
                f"{'' if changed == 1 else 's'}"
            )
        return changed

    def synchronize_prefabs(self) -> int:
        loaded: dict[str, PrefabDocument] = {}
        changed = 0
        missing: list[str] = []
        for layer in self.document.layers:
            if layer.get("type", "object") != "object":
                continue
            changed += self._synchronize_prefab_records(
                layer_objects(layer),
                loaded,
                missing,
            )
        changed += self._synchronize_prefab_records(
            self.document.ui_views,
            loaded,
            missing,
            nested=False,
        )
        if changed:
            self._history_current = self._capture_snapshot()
            self.revision += 1
        if missing:
            names = ", ".join(sorted(set(missing)))
            self.error = f"Missing prefab assets: {names}"
        return changed

    def _refresh_prefab_records(
        self,
        records: list[dict[str, JsonValue]],
        prefab: PrefabDocument,
        source: str,
        *,
        nested: bool = True,
    ) -> int:
        changed = 0
        for index, record in enumerate(tuple(records)):
            metadata = record.get("prefab")
            if (
                isinstance(metadata, dict)
                and metadata.get("prefab_id") == prefab.prefab_id
                and metadata.get("kind", prefab.kind) == prefab.kind
            ):
                record = refresh_prefab_record(record, prefab, source)
                records[index] = record
                changed += 1
            children = record.get("children")
            if nested and isinstance(children, list):
                changed += self._refresh_prefab_records(
                    cast(list[dict[str, JsonValue]], children),
                    prefab,
                    source,
                )
        return changed

    def _synchronize_prefab_records(
        self,
        records: list[dict[str, JsonValue]],
        loaded: dict[str, PrefabDocument],
        missing: list[str],
        *,
        nested: bool = True,
    ) -> int:
        changed = 0
        for index, record in enumerate(tuple(records)):
            metadata = record.get("prefab")
            source = (
                metadata.get("source")
                if isinstance(metadata, dict)
                else None
            )
            if isinstance(source, str):
                path = (self.asset_root / source).resolve()
                if not path.is_file():
                    missing.append(source)
                else:
                    prefab = loaded.setdefault(
                        source,
                        PrefabDocument.read(path),
                    )
                    expected_kind = (
                        "ui_view"
                        if record.get("type") == "ui"
                        else "game_object"
                    )
                    if prefab.kind != expected_kind:
                        raise SceneFormatError(
                            f"prefab kind mismatch for {source}: "
                            f"expected {expected_kind!r}, "
                            f"got {prefab.kind!r}"
                        )
                    refreshed = refresh_prefab_record(
                        record,
                        prefab,
                        source,
                    )
                    if refreshed != record:
                        record = refreshed
                        records[index] = record
                        changed += 1
            children = record.get("children")
            if nested and isinstance(children, list):
                changed += self._synchronize_prefab_records(
                    cast(list[dict[str, JsonValue]], children),
                    loaded,
                    missing,
                )
        return changed

    def revert_selected_prefab(self) -> bool:
        record = self.selected_prefab_record
        metadata = self.selected_prefab_metadata
        if record is None or metadata is None:
            return False
        source = metadata.get("source")
        if not isinstance(source, str):
            raise SceneFormatError("prefab source is missing")
        prefab = PrefabDocument.read((self.asset_root / source).resolve())
        clean = instantiate_prefab_record(prefab, source)
        current_ids = self._record_ids_by_template(record, prefab.kind)
        self._restore_record_ids(clean, current_ids, prefab.kind)
        if prefab.kind == "ui_view":
            clean["startup"] = bool(record.get("startup", False))
            clean["name"] = str(record.get("name") or clean.get("name") or "")
            self._replace_ui_view_record(record, clean)
        else:
            transform = record.get("transform")
            if isinstance(transform, dict):
                clean["transform"] = deepcopy(transform)
            self._replace_object_record(record, clean)
        identifier = clean.get("id")
        if prefab.kind == "ui_view" and isinstance(identifier, str):
            self.select_layer(identifier)
        else:
            self.selected_id = (
                identifier if isinstance(identifier, str) else None
            )
        self.mark_changed(f"Reverted prefab {Path(source).name}")
        return True

    def unpack_selected_prefab(self) -> bool:
        record = self.selected_prefab_record
        if record is None or not isinstance(record.get("prefab"), dict):
            return False
        record.pop("prefab", None)

        def strip(current: dict[str, JsonValue]) -> None:
            current.pop("template_id", None)
            components = current.get("components")
            if isinstance(components, list):
                for component in components:
                    if isinstance(component, dict):
                        component.pop("template_id", None)
            for key in ("children", "elements"):
                children = current.get(key)
                if isinstance(children, list):
                    for child in children:
                        if isinstance(child, dict):
                            strip(cast(dict[str, JsonValue], child))

        strip(record)
        self.mark_changed("Unpacked prefab instance")
        return True

    def selected_prefab_override_count(self) -> int:
        record = self.selected_prefab_record
        return prefab_override_count(record) if record is not None else 0

    def _project_relative_prefab(self, path: Path) -> str:
        try:
            relative = path.relative_to(self.asset_root.resolve())
        except ValueError:
            raise ValueError(
                "prefab must be below the asset root"
            ) from None
        if not relative.as_posix().endswith(PREFAB_SUFFIX):
            raise ValueError(f"prefab must end with {PREFAB_SUFFIX}")
        return relative.as_posix()

    def _replace_object_record(
        self,
        old: dict[str, JsonValue],
        new: dict[str, JsonValue],
    ) -> None:
        for layer in self.document.layers:
            if layer.get("type", "object") != "object":
                continue

            def replace(records: list[dict[str, JsonValue]]) -> bool:
                for index, candidate in enumerate(records):
                    if candidate is old:
                        records[index] = new
                        return True
                    children = candidate.get("children")
                    if isinstance(children, list) and replace(
                        cast(list[dict[str, JsonValue]], children)
                    ):
                        return True
                return False

            if replace(layer_objects(layer)):
                return
        raise SceneFormatError("prefab instance is unavailable")

    def _replace_ui_view_record(
        self,
        old: dict[str, JsonValue],
        new: dict[str, JsonValue],
    ) -> None:
        for index, candidate in enumerate(self.document.ui_views):
            if candidate is old:
                self.document.ui_views[index] = new
                return
        raise SceneFormatError("UI-view prefab instance is unavailable")

    @staticmethod
    def _record_ids_by_template(
        record: dict[str, JsonValue],
        kind: str,
    ) -> dict[str, str]:
        result: dict[str, str] = {}

        def visit(current: dict[str, JsonValue]) -> None:
            template_id = current.get("template_id")
            identifier = current.get("id")
            if isinstance(template_id, str) and isinstance(identifier, str):
                result[template_id] = identifier
            keys = (
                ("elements",)
                if kind == "ui_view" and current.get("type") == "ui"
                else ("children",)
            )
            for key in keys:
                children = current.get(key)
                if isinstance(children, list):
                    for child in children:
                        if isinstance(child, dict):
                            visit(cast(dict[str, JsonValue], child))

        visit(record)
        return result

    @staticmethod
    def _restore_record_ids(
        record: dict[str, JsonValue],
        identifiers: dict[str, str],
        kind: str,
    ) -> None:
        replacements: dict[str, str] = {}

        def visit(current: dict[str, JsonValue]) -> None:
            template_id = current.get("template_id")
            current_id = current.get("id")
            if (
                isinstance(template_id, str)
                and isinstance(current_id, str)
                and template_id in identifiers
            ):
                restored_id = identifiers[template_id]
                replacements[current_id] = restored_id
                current["id"] = restored_id
            keys = (
                ("elements",)
                if kind == "ui_view" and current.get("type") == "ui"
                else ("children",)
            )
            for key in keys:
                children = current.get(key)
                if isinstance(children, list):
                    for child in children:
                        if isinstance(child, dict):
                            visit(cast(dict[str, JsonValue], child))

        visit(record)
        for old_identifier, new_identifier in replacements.items():
            EditorState._replace_object_references(
                record,
                old_identifier,
                new_identifier,
            )

    @staticmethod
    def _replace_object_references(
        value: JsonValue,
        old_identifier: str,
        new_identifier: str,
    ) -> None:
        if isinstance(value, dict):
            if value.get("$object") == old_identifier:
                value["$object"] = new_identifier
            for child in value.values():
                EditorState._replace_object_references(
                    child,
                    old_identifier,
                    new_identifier,
                )
        elif isinstance(value, list):
            for child in value:
                EditorState._replace_object_references(
                    child,
                    old_identifier,
                    new_identifier,
                )

    def paste_copied(
        self,
        kind: str,
        records: list[dict[str, JsonValue]],
        *,
        parent_identifier: str | None = None,
        positions: list[tuple[float, float]] | None = None,
        object_parent_identifiers: tuple[str | None, ...] | None = None,
    ) -> tuple[dict[str, JsonValue], ...]:
        """Paste records; per-object parents interpret positions as world coordinates."""
        if kind not in {"object", "gui"}:
            raise ValueError(f"unknown clipboard kind: {kind!r}")
        if not records:
            return ()
        if positions is not None and len(positions) != len(records):
            raise ValueError(
                "clipboard positions must match the copied records"
            )
        if object_parent_identifiers is not None and (
            kind != "object" or len(object_parent_identifiers) != len(records)
            or parent_identifier is not None
        ):
            raise ValueError("object paste parents must match the copied object records")
        layer = self.selected_layer
        expected_layer_type = "ui" if kind == "gui" else "object"
        if layer is None or layer.get("type", "object") != expected_layer_type:
            raise ValueError(
                f"select a {expected_layer_type} layer before pasting"
            )
        clones = [self._clone_for_paste(record) for record in records]
        if positions is not None:
            for clone, (x, y) in zip(clones, positions, strict=True):
                if kind == "object":
                    set_position(clone, x, y)
                else:
                    clone["x"] = x
                    clone["y"] = y
        if kind == "object":
            target = layer_objects(layer)
            object_targets: list[list[dict[str, JsonValue]]] = []
            if object_parent_identifiers is not None:
                parents = {
                    str(record.get("id")): record
                    for record in walk_object_records(target)
                }
                for index, (clone, identifier) in enumerate(zip(
                    clones, object_parent_identifiers, strict=True,
                )):
                    parent = parents.get(identifier) if identifier is not None else None
                    if identifier is not None and parent is None:
                        raise ValueError("the object paste parent is unavailable")
                    if positions is not None:
                        x, y = positions[index]
                        local = self._local_transform_for_world((x, y, 0, 1, 1), parent)
                        set_position(clone, local[0], local[1])
                    if parent is None:
                        object_targets.append(target)
                    else:
                        children = parent.get("children")
                        if not isinstance(children, list):
                            raise SceneFormatError("object children must be an array")
                        object_targets.append(cast(list[dict[str, JsonValue]], children))
            if parent_identifier is not None:
                parent = next(
                    (
                        record
                        for record in walk_object_records(target)
                        if record.get("id") == parent_identifier
                    ),
                    None,
                )
                if parent is None:
                    raise ValueError(
                        "the object paste parent is unavailable"
                    )
                children = parent.setdefault("children", [])
                if not isinstance(children, list):
                    raise SceneFormatError(
                        "object children must be an array"
                    )
                target = cast(
                    list[dict[str, JsonValue]],
                    children,
                )
        else:
            target = layer_gui_elements(layer)
            if parent_identifier is not None:
                parent = next(
                    (
                        element
                        for element in walk_gui_element_records(target)
                        if element.get("id") == parent_identifier
                    ),
                    None,
                )
                if (
                    parent is None
                    or parent.get("type") not in GUI_CONTAINER_TYPES
                ):
                    raise ValueError(
                        "the GUI paste parent is not a container"
                    )
                children = parent.setdefault("children", [])
                if not isinstance(children, list):
                    raise SceneFormatError(
                        "GUI container children must be an array"
                    )
                if (
                    parent.get("type") == "anchor"
                    and (children or len(clones) > 1)
                ):
                    raise ValueError(
                        "an Anchor Layout can contain only one child"
                    )
                target = cast(
                    list[dict[str, JsonValue]],
                    children,
                )
        if kind == "object" and object_parent_identifiers is not None:
            for destination, clone in zip(object_targets, clones, strict=True):
                destination.append(clone)
        else:
            target.extend(clones)
        identifiers = [
            identifier
            for clone in clones
            if isinstance(identifier := clone.get("id"), str)
        ]
        self.select_many(identifiers)
        self._normalize_selected_layer_order()
        self.mark_changed(
            "Pasted GUI elements"
            if kind == "gui"
            else "Pasted game objects"
        )
        return tuple(clones)

    @staticmethod
    def _clone_for_paste(
        record: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        clone = deepcopy(record)

        def regenerate(current: dict[str, JsonValue]) -> None:
            current["id"] = str(uuid4())
            children = current.get("children")
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        regenerate(child)

        regenerate(clone)
        return clone

    def add_layer(self, name: str | None = None) -> dict[str, JsonValue]:
        existing_names = {
            str(layer.get("name", ""))
            for layer in self.document.layers
        }
        if name is None:
            index = 1
            name = f"Layer {index}"
            while name in existing_names:
                index += 1
                name = f"Layer {index}"
        if not name or name in existing_names:
            raise ValueError("layer name must be non-empty and unique")
        layer: dict[str, JsonValue] = {
            "type": "object",
            "id": str(uuid4()),
            "name": name,
            "visible": True,
            "enabled": True,
            "default": False,
            "objects": [],
        }
        self.document.layers.append(layer)
        identifier = layer["id"]
        self.selected_layer_id = (
            identifier if isinstance(identifier, str) else None
        )
        self.selected_id = None
        self.mark_changed(f"Added layer {name}")
        return layer

    def add_tile_layer(
        self,
        name: str | None = None,
    ) -> dict[str, JsonValue]:
        existing_names = {
            str(layer.get("name", ""))
            for layer in self.document.layers
        }
        if name is None:
            index = 1
            name = f"Tile Layer {index}"
            while name in existing_names:
                index += 1
                name = f"Tile Layer {index}"
        if not name or name in existing_names:
            raise ValueError("layer name must be non-empty and unique")
        layer: dict[str, JsonValue] = {
            "type": "tile",
            "id": str(uuid4()),
            "name": name,
            "visible": True,
            "enabled": True,
            "default": False,
            "tilesets": [],
            "tileset": None,
            "cell_size": [self.grid_size, self.grid_size],
            "chunk_size": 16,
            "opacity": 1.0,
            "offset": [0.0, 0.0],
            "collision_enabled": False,
            "collision_generation": "alpha",
            "collision_alpha_threshold": 255,
            "collision_alpha_inverted": False,
            "navigation_enabled": False,
            "properties": {},
            "chunks": [],
        }
        self.document.layers.append(layer)
        identifier = layer["id"]
        self.selected_layer_id = (
            identifier if isinstance(identifier, str) else None
        )
        self.selected_id = None
        self.mark_changed(f"Added tile layer {name}")
        return layer

    def add_ui_view(
        self,
        name: str | None = None,
    ) -> dict[str, JsonValue]:
        existing_names = {
            str(view.get("name", ""))
            for view in self.document.ui_views
        }
        if name is None:
            index = 1
            name = f"UI View {index}"
            while name in existing_names:
                index += 1
                name = f"UI View {index}"
        if not name or name in existing_names:
            raise ValueError("UI view name must be non-empty and unique")
        view: dict[str, JsonValue] = {
            "type": "ui",
            "id": str(uuid4()),
            "name": name,
            "startup": False,
            "transparent": True,
            "modal": False,
            "pauses_below": True,
            "pauses_scene": False,
            "elements": [],
        }
        self.document.ui_views.append(view)
        identifier = view["id"]
        self.selected_layer_id = (
            identifier if isinstance(identifier, str) else None
        )
        self.selected_id = None
        self.mark_changed(f"Added UI view {name}")
        return view

    def delete_selected_layer(self) -> bool:
        layer = self.selected_layer
        if layer is None:
            return False
        if layer.get("type") == "ui":
            if layer.get("startup") is True:
                raise ValueError("cannot delete the startup UI view")
            if layer_gui_elements(layer):
                raise ValueError("cannot delete a non-empty UI view")
            self.document.ui_views.remove(layer)
            replacement = next(
                view for view in self.document.ui_views
                if view.get("startup") is True
            )
            identifier = replacement.get("id")
            self.selected_layer_id = (
                identifier if isinstance(identifier, str) else None
            )
            self.selected_id = None
            self.mark_changed("Deleted UI view")
            return True
        if layer.get("default") is True:
            raise ValueError("cannot delete the default layer")
        if layer.get("type", "object") == "object":
            nonempty = bool(layer_objects(layer))
        elif layer.get("type") == "tile":
            chunks = layer.get("chunks")
            nonempty = isinstance(chunks, list) and bool(chunks)
        else:
            nonempty = bool(layer_gui_elements(layer))
        if nonempty:
            raise ValueError("cannot delete a non-empty layer")
        self.document.layers.remove(layer)
        replacement = default_layer(self.document)
        identifier = replacement.get("id")
        self.selected_layer_id = (
            identifier if isinstance(identifier, str) else None
        )
        self.mark_changed("Deleted layer")
        return True

    def move_selected_layer(self, offset: int) -> bool:
        layer = self.selected_layer
        if layer is None:
            return False
        collection = (
            self.document.ui_views
            if layer.get("type") == "ui"
            else self.document.layers
        )
        current = collection.index(layer)
        target = max(
            0,
            min(current + offset, len(collection) - 1),
        )
        if target == current:
            return False
        collection.pop(current)
        collection.insert(target, layer)
        self.mark_changed(
            "Reordered UI view"
            if layer.get("type") == "ui"
            else "Reordered layer"
        )
        return True

    def make_selected_ui_view_startup(self) -> None:
        view = self.selected_ui_view
        if view is None or view.get("startup") is True:
            return
        for candidate in self.document.ui_views:
            candidate["startup"] = candidate is view
        self.mark_changed("Changed startup UI view")

    def make_selected_layer_default(self) -> None:
        layer = self.selected_layer
        if layer is None or layer.get("default") is True:
            return
        if layer.get("type", "object") != "object":
            raise ValueError("the default layer must be an object layer")
        for candidate in self.document.layers:
            candidate["default"] = candidate is layer
        self.mark_changed("Changed default layer")

    def move_selected_to_layer(self, identifier: str) -> None:
        record = self.selected
        if record is None:
            return
        world_transform = self.object_world_transform(record)
        target = next(
            (
                layer
                for layer in self.document.layers
                if layer.get("id") == identifier
            ),
            None,
        )
        if target is None:
            raise ValueError(f"unknown scene layer: {identifier!r}")
        if target.get("type", "object") != "object":
            raise ValueError("game objects can only use object layers")
        source = next(
            (
                layer
                for layer in self.document.layers
                if record in walk_object_records(layer_objects(layer))
            ),
            None,
        )
        if source is target:
            return

        def detach(records: list[dict[str, JsonValue]]) -> bool:
            for index, candidate in enumerate(records):
                if candidate is record:
                    records.pop(index)
                    return True
                children = candidate.get("children")
                if isinstance(children, list):
                    typed_children = cast(
                        list[dict[str, JsonValue]],
                        [
                            child
                            for child in children
                            if isinstance(child, dict)
                        ],
                    )
                    if detach(typed_children):
                        candidate["children"] = cast(
                            JsonValue,
                            typed_children,
                        )
                        return True
            return False

        if source is None or not detach(layer_objects(source)):
            raise SceneFormatError("selected object is not in a scene layer")
        layer_objects(target).append(record)
        self._apply_record_transform(record, world_transform)
        normalize_record_order(layer_objects(source))
        normalize_record_order(layer_objects(target))
        self.selected_layer_id = identifier
        self.mark_changed("Moved object to layer")

    def set_layer_value(self, key: str, value: JsonValue) -> None:
        layer = self.selected_layer
        if layer is None:
            return
        self._set_layer_value(layer, key, value)

    def set_layer_value_for(
        self,
        identifier: str,
        key: str,
        value: JsonValue,
    ) -> None:
        layer = next(
            (
                candidate
                for candidate in (
                    *self.document.layers,
                    *self.document.ui_views,
                )
                if candidate.get("id") == identifier
            ),
            None,
        )
        if layer is None:
            raise ValueError(f"unknown layer: {identifier!r}")
        self._set_layer_value(layer, key, value)

    def _set_layer_value(
        self,
        layer: dict[str, JsonValue],
        key: str,
        value: JsonValue,
    ) -> None:
        is_view = layer.get("type") == "ui"
        allowed = (
            {
                "name",
                "transparent",
                "modal",
                "pauses_below",
                "pauses_scene",
            }
            if is_view
            else {"name", "visible", "enabled"}
        )
        if key not in allowed:
            raise KeyError(key)
        if key == "name":
            if not isinstance(value, str) or not value:
                raise ValueError("layer name must not be empty")
            if any(
                candidate is not layer
                and candidate.get("name") == value
                for candidate in (
                    self.document.ui_views
                    if is_view
                    else self.document.layers
                )
            ):
                kind = "UI view" if is_view else "layer"
                raise ValueError(f"{kind} name already exists: {value!r}")
        layer[key] = value
        self.mark_changed(
            f"Changed {'UI view' if is_view else 'layer'} {key}"
        )

    def configure_tile_layer(
        self,
        image: str,
        tile_width: int,
        tile_height: int,
        *,
        margin: int = 0,
        spacing: int = 0,
    ) -> None:
        layer = self._selected_tile_layer()
        if not image:
            raise ValueError("tileset image path must not be empty")
        path = Path(image)
        if path.is_absolute():
            raise ValueError("tileset image path must be relative")
        resolved = (self.asset_root / path).resolve()
        try:
            resolved.relative_to(self.asset_root)
        except ValueError:
            raise ValueError(
                "tileset image must be below the asset root"
            ) from None
        if not resolved.is_file():
            raise ValueError(f"tileset image does not exist: {image}")
        if tile_width <= 0 or tile_height <= 0:
            raise ValueError("tile dimensions must be positive")
        if margin < 0 or spacing < 0:
            raise ValueError("tileset margin and spacing must be non-negative")
        configured = TileSet(
            Image(resolved),
            tile_width,
            tile_height,
            margin=margin,
            spacing=spacing,
        )
        tilesets = self._tile_layer_tilesets(layer)
        active = self.active_tileset(layer)
        previous = active
        tiles: JsonValue = []
        properties: JsonValue = {}
        if isinstance(previous, dict):
            tiles = cast(JsonValue, previous.get("tiles", []))
            properties = cast(
                JsonValue,
                previous.get("properties", {}),
            )
        identifier = (
            str(active["id"])
            if active is not None
            else str(uuid4())
        )
        active_first_gid = (
            active.get("first_gid")
            if active is not None
            else None
        )
        first_gid = (
            active_first_gid
            if isinstance(active_first_gid, int)
            else self._next_tileset_gid(tilesets)
        )
        new_last_gid = first_gid + configured.tile_count - 1
        for candidate in tilesets:
            if candidate is active:
                continue
            candidate_first, candidate_last = self._tileset_range(
                candidate
            )
            if not (
                new_last_gid < candidate_first
                or first_gid > candidate_last
            ):
                raise ValueError(
                    "tileset global ID ranges must not overlap"
                )
        if active is not None:
            _, previous_last_gid = self._tileset_range(active)
            if new_last_gid < previous_last_gid:
                invalid_count = sum(
                    1
                    for chunk in self._tile_chunks(layer)
                    for encoded in cast(list[int], chunk["tiles"])
                    if (
                        (cell := decode_tile_cell(encoded)) is not None
                        and new_last_gid
                        < cell.tile_id
                        <= previous_last_gid
                    )
                )
                if invalid_count:
                    raise ValueError(
                        "tileset cannot be shrunk while "
                        f"{invalid_count} tile cell(s) use removed IDs"
                    )
        configured_record: dict[str, JsonValue] = {
            "type": "atlas",
            "id": identifier,
            "name": (
                str(active.get("name"))
                if active is not None
                else path.stem
            ),
            "first_gid": first_gid,
            "image": path.as_posix(),
            "tile_size": [tile_width, tile_height],
            "columns": configured.columns,
            "tile_count": configured.tile_count,
            "margin": margin,
            "spacing": spacing,
            "properties": properties,
            "tiles": tiles,
        }
        if active is None:
            tilesets.append(configured_record)
        else:
            active.clear()
            active.update(configured_record)
        layer["tileset"] = configured_record
        layer_id = layer.get("id")
        if isinstance(layer_id, str):
            self.active_tileset_ids[layer_id] = identifier
        layer["cell_size"] = [tile_width, tile_height]
        self.document.settings["grid_cell_size"] = tile_width
        self.select_palette_tiles([first_gid], 1, 1)
        self.mark_changed("Configured tileset")

    def add_color_tileset(
        self,
        name: str = "Colors",
        color: list[int] | None = None,
    ) -> dict[str, JsonValue]:
        layer = self._selected_tile_layer()
        colors = self._tile_layer_tilesets(layer)
        existing_names = {
            str(candidate.get("name"))
            for candidate in colors
        }
        resolved_name = name
        suffix = 2
        while resolved_name in existing_names:
            resolved_name = f"{name} {suffix}"
            suffix += 1
        resolved_color = self._validated_tile_color(
            [128, 128, 128, 255] if color is None else color
        )
        identifier = str(uuid4())
        first_gid = self._next_tileset_gid(colors)
        record: dict[str, JsonValue] = {
            "type": "color",
            "id": identifier,
            "name": resolved_name,
            "first_gid": first_gid,
            "colors": cast(JsonValue, [resolved_color]),
            "tile_count": 1,
            "properties": {},
            "tiles": [],
        }
        colors.append(record)
        layer["tileset"] = record
        layer_id = layer.get("id")
        if isinstance(layer_id, str):
            self.active_tileset_ids[layer_id] = identifier
        self.select_palette_tiles([first_gid], 1, 1)
        self.mark_changed(f"Added color palette {resolved_name}")
        return record

    def add_image_tileset(
        self,
        image: str,
        name: str = "Images",
    ) -> dict[str, JsonValue]:
        layer = self._selected_tile_layer()
        relative, loaded = self._validated_tileset_image(image)
        tilesets = self._tile_layer_tilesets(layer)
        existing_names = {
            str(candidate.get("name"))
            for candidate in tilesets
        }
        resolved_name = name
        suffix = 2
        while resolved_name in existing_names:
            resolved_name = f"{name} {suffix}"
            suffix += 1
        identifier = str(uuid4())
        first_gid = self._next_tileset_gid(tilesets)
        record: dict[str, JsonValue] = {
            "type": "images",
            "id": identifier,
            "name": resolved_name,
            "first_gid": first_gid,
            "images": [relative],
            "tile_count": 1,
            "properties": {},
            "tiles": [],
        }
        tilesets.append(record)
        layer["tileset"] = record
        layer_id = layer.get("id")
        if isinstance(layer_id, str):
            self.active_tileset_ids[layer_id] = identifier
        if len(tilesets) == 1:
            layer["cell_size"] = [loaded.width, loaded.height]
            self.document.settings["grid_cell_size"] = loaded.width
        self.select_palette_tiles([first_gid], 1, 1)
        self.mark_changed(f"Added image collection {resolved_name}")
        return record

    def add_image_tile(
        self,
        image: str,
        *,
        tileset_identifier: str | None = None,
    ) -> int:
        layer = self._selected_tile_layer()
        if tileset_identifier is not None:
            self.select_tileset(tileset_identifier)
        tileset = self.active_tileset(layer)
        if tileset is None or tileset.get("type") != "images":
            raise ValueError("select an image collection first")
        relative, _ = self._validated_tileset_image(image)
        raw_images = tileset.get("images")
        if not isinstance(raw_images, list):
            raise SceneFormatError(
                "image collection images must be an array"
            )
        _, last_gid = self._tileset_range(tileset)
        new_global_id = last_gid + 1
        for candidate in self._tile_layer_tilesets(layer):
            if candidate is tileset:
                continue
            candidate_first, candidate_last = self._tileset_range(candidate)
            if candidate_first <= new_global_id <= candidate_last:
                raise ValueError(
                    "image collection cannot grow into the next "
                    "tileset ID range"
                )
        raw_images.append(relative)
        tileset["tile_count"] = len(raw_images)
        self.select_palette_tiles([new_global_id], 1, 1)
        self.mark_changed(f"Added image tile {new_global_id}")
        return new_global_id

    def add_color_tile(
        self,
        color: list[int] | None = None,
    ) -> int:
        layer = self._selected_tile_layer()
        tileset = self.active_tileset(layer)
        if tileset is None or tileset.get("type") != "color":
            raise ValueError("select a color palette first")
        raw_colors = tileset.get("colors")
        if not isinstance(raw_colors, list):
            raise SceneFormatError("color palette colors must be an array")
        resolved = self._validated_tile_color(
            [128, 128, 128, 255] if color is None else color
        )
        _, last_gid = self._tileset_range(tileset)
        new_global_id = last_gid + 1
        for candidate in self._tile_layer_tilesets(layer):
            if candidate is tileset:
                continue
            candidate_first, candidate_last = self._tileset_range(candidate)
            if candidate_first <= new_global_id <= candidate_last:
                raise ValueError(
                    "color palette cannot grow into the next tileset ID range"
                )
        raw_colors.append(cast(JsonValue, resolved))
        tileset["tile_count"] = len(raw_colors)
        self.select_palette_tiles([new_global_id], 1, 1)
        self.mark_changed(f"Added color tile {new_global_id}")
        return new_global_id

    def tile_color(self, tile_id: int) -> list[int] | None:
        layer = self._selected_tile_layer()
        tileset, local_id = self._tileset_for_global_id(layer, tile_id)
        if tileset.get("type") != "color":
            return None
        colors = tileset.get("colors")
        if (
            not isinstance(colors, list)
            or not 0 <= local_id < len(colors)
            or not isinstance(colors[local_id], list)
        ):
            raise SceneFormatError("color palette data is invalid")
        return list(self._validated_tile_color(colors[local_id]))

    def set_tile_color(self, tile_id: int, color: list[int]) -> None:
        layer = self._selected_tile_layer()
        tileset, local_id = self._tileset_for_global_id(layer, tile_id)
        if tileset.get("type") != "color":
            raise ValueError("selected tile is not a color tile")
        colors = tileset.get("colors")
        if not isinstance(colors, list) or not 0 <= local_id < len(colors):
            raise SceneFormatError("color palette data is invalid")
        resolved = self._validated_tile_color(color)
        if colors[local_id] == resolved:
            return
        colors[local_id] = cast(JsonValue, resolved)
        self.mark_changed(f"Changed color tile {tile_id}")

    def set_tile_colors(
        self,
        tile_ids: list[int] | tuple[int, ...],
        color: list[int],
    ) -> None:
        layer = self._selected_tile_layer()
        targets = tuple(dict.fromkeys(tile_ids))
        resolved = self._validated_tile_color(color)
        entries: list[tuple[list[JsonValue], int]] = []
        for tile_id in targets:
            tileset, local_id = self._tileset_for_global_id(
                layer,
                tile_id,
            )
            if tileset.get("type") != "color":
                raise ValueError(
                    "all selected tiles must be color tiles"
                )
            colors = tileset.get("colors")
            if (
                not isinstance(colors, list)
                or not 0 <= local_id < len(colors)
            ):
                raise SceneFormatError(
                    "color palette data is invalid"
                )
            entries.append((colors, local_id))
        if entries and any(
            colors[local_id] != resolved
            for colors, local_id in entries
        ):
            for colors, local_id in entries:
                colors[local_id] = cast(JsonValue, list(resolved))
            self.mark_changed(
                f"Changed color for {len(targets)} tiles"
            )

    def begin_new_tileset(self) -> None:
        layer = self._selected_tile_layer()
        identifier = layer.get("id")
        if isinstance(identifier, str):
            self.active_tileset_ids[identifier] = "__new__"

    def _validated_tileset_image(self, image: str) -> tuple[str, Image]:
        if not image:
            raise ValueError("tile image path must not be empty")
        path = Path(image)
        if path.is_absolute():
            raise ValueError("tile image path must be relative")
        resolved = (self.asset_root / path).resolve()
        try:
            resolved.relative_to(self.asset_root)
        except ValueError:
            raise ValueError(
                "tile image must be below the asset root"
            ) from None
        if not resolved.is_file():
            raise ValueError(f"tile image does not exist: {image}")
        loaded = Image(resolved)
        return path.as_posix(), loaded

    @staticmethod
    def _validated_tile_color(value: object) -> list[int]:
        if (
            not isinstance(value, list)
            or len(value) not in {3, 4}
            or not all(
                isinstance(channel, int)
                and not isinstance(channel, bool)
                and 0 <= channel <= 255
                for channel in value
            )
        ):
            raise ValueError(
                "tile color must contain three or four channels from 0 to 255"
            )
        return [*value, 255] if len(value) == 3 else list(value)

    def active_tileset(
        self,
        layer: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue] | None:
        target = layer or self._selected_tile_layer()
        tilesets = self._tile_layer_tilesets(target)
        layer_id = target.get("id")
        active_id = (
            self.active_tileset_ids.get(layer_id)
            if isinstance(layer_id, str)
            else None
        )
        if active_id == "__new__":
            return None
        active = next(
            (
                tileset
                for tileset in tilesets
                if tileset.get("id") == active_id
            ),
            None,
        )
        if active is None and tilesets:
            active = tilesets[0]
            identifier = active.get("id")
            if isinstance(layer_id, str) and isinstance(identifier, str):
                self.active_tileset_ids[layer_id] = identifier
        return active

    def tile_layer_tilesets(
        self,
        layer: dict[str, JsonValue] | None = None,
    ) -> tuple[dict[str, JsonValue], ...]:
        target = layer or self._selected_tile_layer()
        return tuple(self._tile_layer_tilesets(target))

    def active_tileset_tile_ids(self) -> tuple[int, ...]:
        tileset = self.active_tileset()
        if tileset is None:
            return ()
        first_gid, last_gid = self._tileset_range(tileset)
        return tuple(range(first_gid, last_gid + 1))

    def select_tileset(self, identifier: str) -> None:
        layer = self._selected_tile_layer()
        tileset = next(
            (
                candidate
                for candidate in self._tile_layer_tilesets(layer)
                if candidate.get("id") == identifier
            ),
            None,
        )
        if tileset is None:
            raise ValueError(f"unknown tileset: {identifier!r}")
        layer_id = layer.get("id")
        if isinstance(layer_id, str):
            self.active_tileset_ids[layer_id] = identifier
        layer["tileset"] = tileset
        first_gid = tileset.get("first_gid")
        if isinstance(first_gid, int):
            self.select_palette_tiles([first_gid], 1, 1)

    def select_palette_tiles(
        self,
        tile_ids: list[int],
        width: int,
        height: int,
    ) -> None:
        if width <= 0 or height <= 0 or len(tile_ids) != width * height:
            raise ValueError("palette selection must be a non-empty rectangle")
        layer = self._selected_tile_layer()
        for tile_id in tile_ids:
            self._tileset_for_global_id(layer, tile_id)
        changed = (
            self.palette_tile_ids != tile_ids
            or self.palette_selection_size != (width, height)
            or self._sampled_tile_cells is not None
        )
        self.palette_tile_ids = list(tile_ids)
        self.palette_selection_size = (width, height)
        self._sampled_tile_cells = None
        self._sampled_tile_size = None
        self.paint_tile_id = tile_ids[0]
        if changed:
            self.reset_tile_transform()

    def toggle_palette_tile(
        self,
        tile_id: int,
        *,
        columns: int,
    ) -> None:
        if columns <= 0:
            raise ValueError("palette columns must be positive")
        layer = self._selected_tile_layer()
        tileset, local_id = self._tileset_for_global_id(layer, tile_id)
        first_gid, last_gid = self._tileset_range(tileset)
        selected_ids = (
            set(self.selected_placed_tile_ids)
            if self.selected_tile_cells
            else {
                cell.tile_id
                for cell in self._sampled_tile_cells or ()
                if cell is not None
            }
            if self._sampled_tile_cells is not None
            else set(self.palette_tile_ids)
        )
        selected_ids = {
            selected
            for selected in selected_ids
            if first_gid <= selected <= last_gid
        }
        if tile_id in selected_ids:
            selected_ids.remove(tile_id)
        else:
            selected_ids.add(tile_id)
        self.reset_tile_transform()
        if not selected_ids:
            self.palette_tile_ids = []
            self.palette_selection_size = (1, 1)
            self._sampled_tile_cells = None
            self._sampled_tile_size = None
            self.status = "Cleared tile palette selection"
            return
        local_ids = {
            selected - first_gid for selected in selected_ids
        }
        min_column = min(selected % columns for selected in local_ids)
        max_column = max(selected % columns for selected in local_ids)
        min_row = min(selected // columns for selected in local_ids)
        max_row = max(selected // columns for selected in local_ids)
        width = max_column - min_column + 1
        height = max_row - min_row + 1
        cells: list[TileCell | None] = [None] * (width * height)
        for selected in local_ids:
            x = selected % columns - min_column
            y = selected // columns - min_row
            cells[y * width + x] = TileCell(first_gid + selected)
        self.palette_tile_ids = [
            first_gid + selected
            for selected in sorted(local_ids)
        ]
        self.palette_selection_size = (width, height)
        self._sampled_tile_cells = cells
        self._sampled_tile_size = (width, height)
        self.paint_tile_id = self.palette_tile_ids[0]
        self.status = f"Selected {len(selected_ids)} palette tiles"

    @property
    def has_tile_stamp(self) -> bool:
        return (
            any(cell is not None for cell in self._sampled_tile_cells)
            if self._sampled_tile_cells is not None
            else bool(self.palette_tile_ids)
        )

    @property
    def sampled_tile_size(self) -> tuple[int, int] | None:
        return self._sampled_tile_size

    def sample_tile_rectangle(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> bool:
        layer = self._selected_tile_layer()
        left, right = sorted((start[0], end[0]))
        bottom, top = sorted((start[1], end[1]))
        width = right - left + 1
        height = top - bottom + 1
        cells = [
            self._tile_at_layer(layer, left + x, top - y)
            for y in range(height)
            for x in range(width)
        ]
        first = next((cell for cell in cells if cell is not None), None)
        if first is None:
            return False
        tileset, _ = self._tileset_for_global_id(layer, first.tile_id)
        layer_id = layer.get("id")
        tileset_id = tileset.get("id")
        if isinstance(layer_id, str) and isinstance(tileset_id, str):
            self.active_tileset_ids[layer_id] = tileset_id
        layer["tileset"] = tileset
        self.palette_tile_ids = [first.tile_id]
        self.palette_selection_size = (1, 1)
        self.paint_tile_id = first.tile_id
        self._sampled_tile_cells = cells
        self._sampled_tile_size = (width, height)
        self.clear_tile_selection()
        self.status = (
            f"Sampled {sum(cell is not None for cell in cells)} tiles "
            f"({width} x {height})"
        )
        return True

    @property
    def palette_stamp(
        self,
    ) -> tuple[list[TileCell | None], tuple[int, int]]:
        if self._sampled_tile_cells is not None:
            if self._sampled_tile_size is None:
                raise ValueError("sampled tile stamp size is unavailable")
            source = self._sampled_tile_cells
            size = self._sampled_tile_size
        else:
            source = [
                TileCell(tile_id) for tile_id in self.palette_tile_ids
            ]
            size = self.palette_selection_size
        cells, width, height = transform_tile_matrix(
            list(source),
            *size,
            rotation=self.tile_rotation,
            flip_horizontal=self.tile_flip_horizontal,
            flip_vertical=self.tile_flip_vertical,
        )
        return cells, (width, height)

    def tileset_usage_count(self, identifier: str) -> int:
        layer = self._selected_tile_layer()
        tileset = self._tileset_by_id(layer, identifier)
        first_gid, last_gid = self._tileset_range(tileset)
        return sum(
            1
            for chunk in self._tile_chunks(layer)
            for encoded in cast(list[int], chunk["tiles"])
            if (
                (cell := decode_tile_cell(encoded)) is not None
                and first_gid <= cell.tile_id <= last_gid
            )
        )

    def remove_tileset(
        self,
        identifier: str,
        *,
        erase_used: bool = False,
    ) -> int:
        layer = self._selected_tile_layer()
        tilesets = self._tile_layer_tilesets(layer)
        tileset = self._tileset_by_id(layer, identifier)
        first_gid, last_gid = self._tileset_range(tileset)
        usage = self.tileset_usage_count(identifier)
        if usage and not erase_used:
            raise ValueError(
                f"tileset is used by {usage} tile cell(s)"
            )
        if usage:
            chunks = self._tile_chunks(layer)
            for chunk in tuple(chunks):
                raw_tiles = cast(list[int], chunk["tiles"])
                for index, encoded in enumerate(raw_tiles):
                    cell = decode_tile_cell(encoded)
                    if (
                        cell is not None
                        and first_gid <= cell.tile_id <= last_gid
                    ):
                        raw_tiles[index] = 0
                if not any(raw_tiles):
                    chunks.remove(chunk)
        tilesets.remove(tileset)
        layer_id = layer.get("id")
        if isinstance(layer_id, str):
            self.active_tileset_ids.pop(layer_id, None)
        active = self.active_tileset(layer)
        if active is not None:
            layer["tileset"] = active
            first = active.get("first_gid")
            if isinstance(first, int):
                self.select_palette_tiles([first], 1, 1)
        else:
            layer["tileset"] = None
            self.palette_tile_ids = []
            self._sampled_tile_cells = None
            self._sampled_tile_size = None
        self.mark_changed(f"Removed tileset {tileset.get('name', '')}")
        return usage

    def set_tile_layer_value(self, key: str, value: JsonValue) -> None:
        layer = self._selected_tile_layer()
        if key not in {
            "cell_size",
            "chunk_size",
            "opacity",
            "offset",
            "collision_enabled",
            "collision_generation",
            "collision_alpha_threshold",
            "collision_alpha_inverted",
            "navigation_enabled",
            "properties",
        }:
            raise KeyError(key)
        if key == "chunk_size" and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise ValueError("chunk size must be positive")
        if key == "opacity" and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0 <= value <= 1
        ):
            raise ValueError("opacity must be between 0 and 1")
        if key in {"cell_size", "offset"}:
            if (
                not isinstance(value, list)
                or len(value) != 2
                or not all(
                    isinstance(item, (int, float))
                    and not isinstance(item, bool)
                    for item in value
                )
            ):
                raise ValueError(f"{key} must contain two numbers")
            numbers = cast(list[int | float], value)
            if key == "cell_size" and any(item <= 0 for item in numbers):
                raise ValueError("cell size must be positive")
        if key == "properties" and not isinstance(value, dict):
            raise ValueError("layer properties must be an object")
        if (
            key == "collision_generation"
            and value not in {"alpha", "rectangle", "existing"}
        ):
            raise ValueError("unknown collision generation mode")
        if key == "collision_alpha_threshold" and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= 255
        ):
            raise ValueError(
                "collision alpha threshold must be between 1 and 255"
            )
        if key == "collision_alpha_inverted" and not isinstance(value, bool):
            raise ValueError("collision alpha inversion must be a boolean")
        layer[key] = value
        if key == "collision_enabled" and value is True:
            self._generate_missing_collisions_for_painted_tiles(layer)
        self.mark_changed(f"Changed tile layer {key}")

    def paint_tile(
        self,
        world_x: float,
        world_y: float,
        tile_id: int | None = None,
        *,
        flags: TileFlags = TileFlags.NONE,
    ) -> tuple[int, int]:
        layer = self._selected_tile_layer()
        if tile_id is not None and not self._tile_layer_tilesets(layer):
            raise ValueError("configure the tile layer tileset first")
        if tile_id is not None:
            self._tileset_for_global_id(layer, tile_id)
            if layer.get("collision_enabled") is True:
                self._ensure_generated_tile_collision(layer, tile_id)
        cell_width, cell_height = self._tile_pair(
            layer.get("cell_size"),
            "cell_size",
        )
        offset_x, offset_y = self._tile_pair(
            layer.get("offset", [0, 0]),
            "offset",
        )
        column = floor((world_x - offset_x) / cell_width)
        row = floor((world_y - offset_y) / cell_height)
        changed = self._set_tile_cell(
            layer,
            column,
            row,
            None if tile_id is None else TileCell(tile_id, flags),
        )
        if changed:
            self.mark_changed(
                "Erased tile" if tile_id is None else f"Painted tile {tile_id}"
            )
        return column, row

    @staticmethod
    def _set_tile_cell(
        layer: dict[str, JsonValue],
        column: int,
        row: int,
        cell: TileCell | None,
    ) -> bool:
        chunk_size = layer.get("chunk_size", 16)
        if (
            not isinstance(chunk_size, int)
            or isinstance(chunk_size, bool)
            or chunk_size <= 0
        ):
            raise SceneFormatError("tile layer chunk_size must be positive")
        chunk_column, local_column = divmod(column, chunk_size)
        chunk_row, local_row = divmod(row, chunk_size)
        chunks = layer.setdefault("chunks", [])
        if not isinstance(chunks, list):
            raise SceneFormatError("tile layer chunks must be an array")
        chunk = next(
            (
                candidate
                for candidate in chunks
                if isinstance(candidate, dict)
                and candidate.get("position")
                == [chunk_column, chunk_row]
            ),
            None,
        )
        if chunk is None:
            if cell is None:
                return False
            chunk = {
                "position": [chunk_column, chunk_row],
                "size": chunk_size,
                "tiles": [0] * (chunk_size * chunk_size),
            }
            chunks.append(chunk)
        raw_tiles = chunk.get("tiles")
        if (
            not isinstance(raw_tiles, list)
            or len(raw_tiles) != chunk_size * chunk_size
        ):
            raise SceneFormatError("tile chunk data has an invalid size")
        index = local_row * chunk_size + local_column
        encoded = encode_tile_cell(cell)
        if raw_tiles[index] == encoded:
            return False
        raw_tiles[index] = encoded
        if not any(raw_tiles):
            chunks.remove(chunk)
        return True

    def _add_default_collision_to_painted_tiles(
        self,
        layer: dict[str, JsonValue],
    ) -> None:
        previous = layer.get("collision_generation")
        layer["collision_generation"] = "rectangle"
        try:
            self._generate_missing_collisions_for_painted_tiles(layer)
        finally:
            if previous is None:
                layer.pop("collision_generation", None)
            else:
                layer["collision_generation"] = previous

    def _painted_tile_ids(
        self,
        layer: dict[str, JsonValue],
    ) -> set[int]:
        tile_ids: set[int] = set()
        for chunk in self._tile_chunks(layer):
            raw_tiles = chunk.get("tiles")
            if not isinstance(raw_tiles, list):
                continue
            for encoded in raw_tiles:
                if not isinstance(encoded, int):
                    continue
                cell = decode_tile_cell(encoded)
                if cell is not None:
                    tile_ids.add(cell.tile_id)
        return tile_ids

    def _generate_missing_collisions_for_painted_tiles(
        self,
        layer: dict[str, JsonValue],
    ) -> int:
        generated = 0
        for tile_id in self._painted_tile_ids(layer):
            if self._ensure_generated_tile_collision(layer, tile_id):
                generated += 1
        return generated

    def _ensure_generated_tile_collision(
        self,
        layer: dict[str, JsonValue],
        tile_id: int,
        *,
        replace: bool = False,
    ) -> bool:
        definition = self._tile_definition_for_layer(
            layer,
            tile_id,
            create=True,
        )
        assert definition is not None
        if not replace and isinstance(definition.get("collision"), dict):
            return False
        mode = str(layer.get("collision_generation", "alpha"))
        if mode == "existing":
            return False
        if mode == "rectangle":
            collision: JsonValue = {
                "type": "rectangle",
                "x": 0,
                "y": 0,
                "width": None,
                "height": None,
                "generated_from_alpha": False,
                "alpha_threshold": 255,
            }
        else:
            raw_threshold = layer.get(
                "collision_alpha_threshold",
                255,
            )
            if (
                not isinstance(raw_threshold, int)
                or isinstance(raw_threshold, bool)
                or not 1 <= raw_threshold <= 255
            ):
                raise SceneFormatError(
                    "collision alpha threshold must be between 1 and 255"
                )
            threshold = raw_threshold
            collision = self._collision_from_tile_alpha(
                layer,
                tile_id,
                threshold,
            )
        if collision is None:
            if replace:
                definition.pop("collision", None)
            return False
        definition["collision"] = collision
        return True

    def generate_tile_collisions(
        self,
        tile_ids: list[int] | tuple[int, ...] | None = None,
        *,
        replace: bool = False,
    ) -> int:
        layer = self._selected_tile_layer()
        targets = (
            tuple(dict.fromkeys(tile_ids))
            if tile_ids is not None
            else self.active_tileset_tile_ids()
        )
        generated = sum(
            self._ensure_generated_tile_collision(
                layer,
                tile_id,
                replace=replace,
            )
            for tile_id in targets
        )
        if targets and (generated or replace):
            self.mark_changed(
                f"Generated collision for {len(targets)} tile"
                f"{'' if len(targets) == 1 else 's'}"
            )
        return generated

    def generate_tile_navigation(
        self,
        tile_ids: list[int] | tuple[int, ...] | None = None,
    ) -> int:
        targets = (
            tuple(dict.fromkeys(tile_ids))
            if tile_ids is not None
            else self.active_tileset_tile_ids()
        )
        generated = 0
        for tile_id in targets:
            definition = self.tile_definition(tile_id, create=True)
            assert definition is not None
            if isinstance(definition.get("navigation"), dict):
                continue
            definition["navigation"] = {
                "walkable": True,
                "cost": 1.0,
            }
            generated += 1
        if generated:
            self.mark_changed(
                f"Generated navigation for {generated} tile"
                f"{'' if generated == 1 else 's'}"
            )
        return generated

    def _collision_from_tile_alpha(
        self,
        layer: dict[str, JsonValue],
        tile_id: int,
        threshold: int,
    ) -> dict[str, JsonValue] | None:
        width, height, alpha = self._tile_alpha_data(layer, tile_id)
        inverted = layer.get("collision_alpha_inverted", False) is True
        solid = {
            (column, row)
            for row in range(height)
            for column in range(width)
            if (
                alpha[row * width + column] < threshold
                if inverted
                else alpha[row * width + column] >= threshold
            )
        }
        if not solid:
            return None
        cell_width, cell_height = self._tile_pair(
            layer.get("cell_size"),
            "cell_size",
        )
        minimum_x = min(column for column, _ in solid)
        maximum_x = max(column for column, _ in solid) + 1
        minimum_y = min(row for _, row in solid)
        maximum_y = max(row for _, row in solid) + 1
        rectangle = {
            (column, row)
            for row in range(minimum_y, maximum_y)
            for column in range(minimum_x, maximum_x)
        }
        if solid == rectangle:
            full = (
                minimum_x == 0
                and minimum_y == 0
                and maximum_x == width
                and maximum_y == height
            )
            return {
                "type": "rectangle",
                "x": 0 if full else minimum_x / width * cell_width,
                "y": 0 if full else minimum_y / height * cell_height,
                "width": (
                    None
                    if full
                    else (maximum_x - minimum_x) / width * cell_width
                ),
                "height": (
                    None
                    if full
                    else (maximum_y - minimum_y) / height * cell_height
                ),
                "generated_from_alpha": True,
                "alpha_threshold": threshold,
            }
        polygons = [
            [
                [x / width, y / height]
                for x, y in self._convex_hull_for_pixels(component)
            ]
            for component in self._opaque_components(solid)
        ]
        return {
            "type": "polygon",
            "polygons": cast(JsonValue, polygons),
            "generated_from_alpha": True,
            "alpha_threshold": threshold,
        }

    def _tile_alpha_data(
        self,
        layer: dict[str, JsonValue],
        tile_id: int,
    ) -> tuple[int, int, list[int]]:
        tileset, local_id = self._tileset_for_global_id(layer, tile_id)
        tileset_type = tileset.get("type")
        if tileset_type == "color":
            return 1, 1, [255]
        if tileset_type == "images":
            images = tileset.get("images")
            if (
                not isinstance(images, list)
                or not 0 <= local_id < len(images)
                or not isinstance(images[local_id], str)
            ):
                raise SceneFormatError(
                    "image collection tile is invalid"
                )
            loaded_images = [
                Image(self.asset_root / image)
                for image in images
                if isinstance(image, str)
            ]
            if len(loaded_images) != len(images):
                raise SceneFormatError(
                    "image collection paths must be strings"
                )
            width = max(image.width for image in loaded_images)
            height = max(image.height for image in loaded_images)
            image = loaded_images[local_id]
            source = source_of(image).get_image_data()
            rgba = source.get_data("RGBA", image.width * 4)
            alpha = [0] * (width * height)
            offset_x = (width - image.width) // 2
            offset_y = (height - image.height) // 2
            for row in range(image.height):
                for column in range(image.width):
                    alpha[
                        (offset_y + row) * width + offset_x + column
                    ] = rgba[(row * image.width + column) * 4 + 3]
            return width, height, alpha
        image_path = tileset.get("image")
        tile_size = tileset.get("tile_size")
        columns = tileset.get("columns")
        margin = tileset.get("margin", 0)
        spacing = tileset.get("spacing", 0)
        if (
            not isinstance(image_path, str)
            or not isinstance(tile_size, list)
            or len(tile_size) != 2
            or not all(isinstance(value, int) for value in tile_size)
            or not isinstance(columns, int)
            or not isinstance(margin, int)
            or not isinstance(spacing, int)
        ):
            raise SceneFormatError("atlas tileset is invalid")
        tile_width, tile_height = cast(list[int], tile_size)
        image = Image(self.asset_root / image_path)
        source = source_of(image).get_image_data()
        rgba = source.get_data("RGBA", image.width * 4)
        column = local_id % columns
        row = local_id // columns
        start_x = margin + column * (tile_width + spacing)
        start_y = (
            image.height
            - margin
            - tile_height
            - row * (tile_height + spacing)
        )
        alpha = [
            rgba[
                (
                    (start_y + local_y) * image.width
                    + start_x
                    + local_x
                )
                * 4
                + 3
            ]
            for local_y in range(tile_height)
            for local_x in range(tile_width)
        ]
        return tile_width, tile_height, alpha

    @staticmethod
    def _opaque_components(
        solid: set[tuple[int, int]],
    ) -> tuple[set[tuple[int, int]], ...]:
        remaining = set(solid)
        components: list[set[tuple[int, int]]] = []
        while remaining:
            pending = [remaining.pop()]
            component: set[tuple[int, int]] = set()
            while pending:
                pixel = pending.pop()
                component.add(pixel)
                x, y = pixel
                for neighbor in (
                    (x - 1, y),
                    (x + 1, y),
                    (x, y - 1),
                    (x, y + 1),
                ):
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        pending.append(neighbor)
            components.append(component)
        return tuple(components)

    @staticmethod
    def _convex_hull_for_pixels(
        pixels: set[tuple[int, int]],
    ) -> tuple[tuple[int, int], ...]:
        points = sorted(
            {
                corner
                for x, y in pixels
                for corner in (
                    (x, y),
                    (x + 1, y),
                    (x + 1, y + 1),
                    (x, y + 1),
                )
            }
        )
        if len(points) <= 2:
            return tuple(points)

        def cross(
            origin: tuple[int, int],
            first: tuple[int, int],
            second: tuple[int, int],
        ) -> int:
            return (
                (first[0] - origin[0]) * (second[1] - origin[1])
                - (first[1] - origin[1]) * (second[0] - origin[0])
            )

        lower: list[tuple[int, int]] = []
        for point in points:
            while (
                len(lower) >= 2
                and cross(lower[-2], lower[-1], point) <= 0
            ):
                lower.pop()
            lower.append(point)
        upper: list[tuple[int, int]] = []
        for point in reversed(points):
            while (
                len(upper) >= 2
                and cross(upper[-2], upper[-1], point) <= 0
            ):
                upper.pop()
            upper.append(point)
        return tuple(lower[:-1] + upper[:-1])

    def paint_stamp(
        self,
        world_x: float,
        world_y: float,
        *,
        randomize: bool | None = None,
        random_source: Random | None = None,
    ) -> list[tuple[int, int, int]]:
        layer = self._selected_tile_layer()
        if not self.has_tile_stamp:
            raise ValueError("select one or more palette tiles first")
        start_column, start_row = self.world_to_tile_cell(
            world_x,
            world_y,
        )
        stamp, (width, _) = self.palette_stamp
        occupied = [cell for cell in stamp if cell is not None]
        random_enabled = (
            self.random_tile_brush
            if randomize is None
            else randomize
        )
        chooser = random_source or Random()
        painted: list[tuple[int, int, int]] = []
        changed = False
        for index, selected_cell in enumerate(stamp):
            column = start_column + index % width
            row = start_row - index // width
            cell = (
                chooser.choice(occupied)
                if random_enabled
                else selected_cell
            )
            if cell is None:
                continue
            changed = (
                self._paint_tile_cell(layer, column, row, cell)
                or changed
            )
            painted.append((column, row, cell.tile_id))
        if changed:
            self.mark_changed("Painted tile stamp")
        return painted

    def world_to_tile_cell(
        self,
        world_x: float,
        world_y: float,
    ) -> tuple[int, int]:
        layer = self._selected_tile_layer()
        cell_width, cell_height = self._tile_pair(
            layer.get("cell_size"),
            "cell_size",
        )
        offset_x, offset_y = self._tile_pair(
            layer.get("offset", [0, 0]),
            "offset",
        )
        return (
            floor((world_x - offset_x) / cell_width),
            floor((world_y - offset_y) / cell_height),
        )

    def fill_tile_rectangle(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        *,
        randomize: bool | None = None,
        random_source: Random | None = None,
    ) -> list[tuple[int, int, int]]:
        if not self.has_tile_stamp:
            raise ValueError("select one or more palette tiles first")
        layer = self._selected_tile_layer()
        left, right = sorted((start[0], end[0]))
        bottom, top = sorted((start[1], end[1]))
        stamp, (width, height) = self.palette_stamp
        occupied = [cell for cell in stamp if cell is not None]
        random_enabled = (
            self.random_tile_brush
            if randomize is None
            else randomize
        )
        chooser = random_source or Random()
        painted: list[tuple[int, int, int]] = []
        changed = False
        for row in range(bottom, top + 1):
            for column in range(left, right + 1):
                if random_enabled:
                    cell = chooser.choice(occupied)
                else:
                    local_x = (column - left) % width
                    local_y = (top - row) % height
                    cell = stamp[
                        local_y * width + local_x
                    ]
                if cell is None:
                    continue
                changed = (
                    self._paint_tile_cell(layer, column, row, cell)
                    or changed
                )
                painted.append((column, row, cell.tile_id))
        if changed:
            self.mark_changed("Filled tile rectangle")
        return painted

    def tile_at(self, column: int, row: int) -> TileCell | None:
        layer = self._selected_tile_layer()
        return self._tile_at_layer(layer, column, row)

    @staticmethod
    def _tile_at_layer(
        layer: dict[str, JsonValue], column: int, row: int,
    ) -> TileCell | None:
        chunk_size = layer.get("chunk_size", 16)
        if not isinstance(chunk_size, int) or chunk_size <= 0:
            raise SceneFormatError("tile layer chunk_size must be positive")
        chunk_column, local_column = divmod(column, chunk_size)
        chunk_row, local_row = divmod(row, chunk_size)
        chunks = layer.get("chunks", [])
        if not isinstance(chunks, list):
            raise SceneFormatError("tile layer chunks must be an array")
        for chunk in chunks:
            if (
                isinstance(chunk, dict)
                and chunk.get("position") == [chunk_column, chunk_row]
            ):
                tiles = chunk.get("tiles")
                if not isinstance(tiles, list):
                    raise SceneFormatError(
                        "tile chunk tiles must be an array"
                    )
                encoded = tiles[
                    local_row * chunk_size + local_column
                ]
                if not isinstance(encoded, int):
                    raise SceneFormatError(
                        "encoded tile must be an integer"
                    )
                return decode_tile_cell(encoded)
        return None

    @property
    def selected_tile_cells(self) -> dict[tuple[int, int], TileCell]:
        layer = self.selected_layer
        if (
            layer is None
            or layer.get("type") != "tile"
            or self._tile_selection_layer_id != self.selected_layer_id
        ):
            return {}
        return {
            position: cell
            for position in sorted(self._tile_selection)
            if (cell := self.tile_at(*position)) is not None
        }

    @property
    def selected_placed_tile_ids(self) -> tuple[int, ...]:
        return tuple(
            dict.fromkeys(
                cell.tile_id
                for cell in self.selected_tile_cells.values()
            )
        )

    def toggle_tile_selection_cell(
        self,
        column: int,
        row: int,
    ) -> bool:
        layer = self._selected_tile_layer()
        layer_id = str(layer["id"])
        cell = self._tile_at_layer(layer, column, row)
        if cell is None:
            return False
        if self._tile_selection_layer_id != layer_id:
            self.clear_tile_selection()
            self._tile_selection_layer_id = layer_id
        position = (column, row)
        if position in self._tile_selection:
            self._tile_selection.remove(position)
        else:
            self._tile_selection.add(position)
            tileset, _ = self._tileset_for_global_id(
                layer,
                cell.tile_id,
            )
            identifier = tileset.get("id")
            if isinstance(identifier, str):
                self.active_tileset_ids[layer_id] = identifier
            layer["tileset"] = tileset
        self.reset_tile_transform()
        if not self._tile_selection and not self._extra_tile_selections:
            self._tile_selection_layer_id = None
        self.status = f"Selected {len(self.selected_tile_cells)} tiles"
        return True

    def reset_tile_transform(self) -> None:
        self.tile_rotation = 0
        self.tile_flip_horizontal = False
        self.tile_flip_vertical = False
        self._tile_transform_source = None
        self._tile_transform_base_layers = None

    def clear_tile_selection(self) -> None:
        self._tile_selection.clear()
        self._tile_selection_layer_id = None
        self._extra_tile_selections.clear()
        self.reset_tile_transform()

    def select_tile_rectangle(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> None:
        layer = self._selected_tile_layer()
        self.reset_tile_transform()
        self._select_tile_rectangle_for_layer(layer, start, end)
        self._extra_tile_selections.clear()
        width, height = self._tile_pair(layer.get("cell_size"), "cell_size")
        ox, oy = self._tile_pair(layer.get("offset", [0, 0]), "offset")
        left, right = sorted((start[0], end[0]))
        bottom, top = sorted((start[1], end[1]))
        for other in self.selected_layers:
            if other is layer or other.get("type") != "tile":
                continue
            cw, ch = self._tile_pair(other.get("cell_size"), "cell_size")
            x, y = self._tile_pair(other.get("offset", [0, 0]), "offset")
            a = (floor((ox + left * width - x) / cw), floor((oy + bottom * height - y) / ch))
            b = (ceil((ox + (right + 1) * width - x) / cw) - 1,
                 ceil((oy + (top + 1) * height - y) / ch) - 1)
            primary = set(self._tile_selection)
            self._select_tile_rectangle_for_layer(other, a, b)
            self._extra_tile_selections[str(other["id"])] = set(self._tile_selection)
            self._tile_selection = primary
            self._tile_selection_layer_id = str(layer["id"])
        selected = next(iter(self.selected_tile_cells.values()), None)
        if selected is not None:
            tileset, _ = self._tileset_for_global_id(
                layer,
                selected.tile_id,
            )
            layer_id = layer.get("id")
            identifier = tileset.get("id")
            if isinstance(layer_id, str) and isinstance(identifier, str):
                self.active_tileset_ids[layer_id] = identifier
            layer["tileset"] = tileset

    def _select_tile_rectangle_for_layer(
        self, layer: dict[str, JsonValue], start: tuple[int, int], end: tuple[int, int],
    ) -> None:
        chunk_size = layer.get("chunk_size", 16)
        if (
            not isinstance(chunk_size, int)
            or isinstance(chunk_size, bool)
            or chunk_size <= 0
        ):
            raise SceneFormatError("tile layer chunk_size must be positive")
        left, right = sorted((start[0], end[0]))
        bottom, top = sorted((start[1], end[1]))
        selected: set[tuple[int, int]] = set()
        for chunk in self._tile_chunks(layer):
            position = chunk.get("position")
            if (
                not isinstance(position, list)
                or len(position) != 2
                or not all(
                    isinstance(value, int) and not isinstance(value, bool)
                    for value in position
                )
            ):
                raise SceneFormatError("tile chunk position must be two integers")
            chunk_x, chunk_y = cast(list[int], position)
            origin_x = chunk_x * chunk_size
            origin_y = chunk_y * chunk_size
            if (
                origin_x > right
                or origin_x + chunk_size <= left
                or origin_y > top
                or origin_y + chunk_size <= bottom
            ):
                continue
            tiles = chunk.get("tiles")
            if (
                not isinstance(tiles, list)
                or len(tiles) != chunk_size * chunk_size
            ):
                raise SceneFormatError("tile chunk data has an invalid size")
            for index, encoded in enumerate(tiles):
                column = origin_x + index % chunk_size
                row = origin_y + index // chunk_size
                if left <= column <= right and bottom <= row <= top:
                    if not isinstance(encoded, int):
                        raise SceneFormatError("encoded tile must be an integer")
                    if decode_tile_cell(encoded) is not None:
                        selected.add((column, row))
        self._tile_selection_layer_id = str(layer["id"])
        self._tile_selection = selected
        self.status = f"Selected {len(selected)} tiles"

    @property
    def selected_tile_groups(self) -> dict[str, dict[tuple[int, int], TileCell]]:
        selections = dict(self._extra_tile_selections)
        if self._tile_selection_layer_id is not None and self._tile_selection_layer_id == self.selected_layer_id:
            selections[self._tile_selection_layer_id] = self._tile_selection
        return {
            str(layer["id"]): {
                p: cell for p in selections.get(str(layer["id"]), set())
                if (cell := self._tile_at_layer(layer, *p)) is not None
            }
            for layer in self.selected_layers if layer.get("type") == "tile"
        }

    def _capture_tile_transform_source(self) -> None:
        groups = {
            identifier: dict(cells)
            for identifier, cells in self.selected_tile_groups.items()
            if cells
        }
        self._tile_transform_source = groups
        layer_by_id = {
            str(layer["id"]): layer
            for layer in self.selected_layers
            if layer.get("type") == "tile"
        }
        self._tile_transform_base_layers = {
            identifier: deepcopy(layer_by_id[identifier])
            for identifier in groups
            if identifier in layer_by_id
        }

    def _apply_tile_selection_transform(self) -> bool:
        if self._tile_transform_source is None:
            self._capture_tile_transform_source()
        source = self._tile_transform_source or {}
        bases = self._tile_transform_base_layers or {}
        if not source:
            return False
        layers = {
            str(layer["id"]): layer
            for layer in self.selected_layers
            if layer.get("type") == "tile"
        }
        transformed_selections: dict[str, set[tuple[int, int]]] = {}
        replacements: list[
            tuple[dict[str, JsonValue], dict[str, JsonValue]]
        ] = []
        for identifier, cells in source.items():
            layer = layers.get(identifier)
            base = bases.get(identifier)
            if layer is None or base is None or not cells:
                continue
            left = min(column for column, _ in cells)
            right = max(column for column, _ in cells)
            bottom = min(row for _, row in cells)
            top = max(row for _, row in cells)
            width = right - left + 1
            height = top - bottom + 1
            matrix = [
                cells.get((left + x, top - y))
                for y in range(height)
                for x in range(width)
            ]
            transformed, new_width, new_height = transform_tile_matrix(
                matrix,
                width,
                height,
                rotation=self.tile_rotation,
                flip_horizontal=self.tile_flip_horizontal,
                flip_vertical=self.tile_flip_vertical,
            )
            working = deepcopy(base)
            for position in cells:
                self._set_tile_cell(working, *position, None)
            selected: set[tuple[int, int]] = set()
            for index, cell in enumerate(transformed):
                if cell is None:
                    continue
                x = left + index % new_width
                y = bottom + new_height - 1 - index // new_width
                self._set_tile_cell(working, x, y, cell)
                selected.add((x, y))
            replacements.append((layer, working))
            transformed_selections[identifier] = selected
        if not replacements:
            return False
        self._history_current = self._capture_snapshot()
        for layer, working in replacements:
            layer["chunks"] = working["chunks"]
        for identifier, selected in transformed_selections.items():
            if identifier == self._tile_selection_layer_id:
                self._tile_selection = selected
            else:
                self._extra_tile_selections[identifier] = selected
        return True

    def rotate_tiles_clockwise(self) -> bool:
        self.tile_rotation = (self.tile_rotation + 1) % 4
        changed = self._apply_tile_selection_transform()
        if changed:
            self.mark_changed("Rotate tiles clockwise")
        return changed

    def toggle_tile_flip(self, axis: str) -> bool:
        if axis == "horizontal":
            self.tile_flip_horizontal = not self.tile_flip_horizontal
        elif axis == "vertical":
            self.tile_flip_vertical = not self.tile_flip_vertical
        else:
            raise ValueError("tile flip axis must be horizontal or vertical")
        changed = self._apply_tile_selection_transform()
        if changed:
            self.mark_changed(f"Flip tiles {axis}")
        return changed

    def move_world_selection(self, columns: int, rows: int) -> bool:
        if not isinstance(columns, int) or isinstance(columns, bool) or not isinstance(rows, int) or isinstance(rows, bool):
            raise ValueError("Selection movement must use whole cells")
        if not columns and not rows:
            return False
        width, height = self.selection_grid_size
        groups = self.selected_tile_groups
        records = [
            record for record in self.selected_records
            if not self._object_has_selected_ancestor(record, set(self.selected_ids))
        ] if self.mixed_layer_selection else []
        if not any(groups.values()) and not records:
            return False
        transforms = []
        for record in records:
            x, y, angle, sx, sy = self.object_world_transform(record)
            transforms.append((record, self._local_transform_for_world(
                (x + columns * width, y + rows * height, angle, sx, sy),
                self.object_parent(record),
            )))
        working_layers = []
        for layer in self.selected_layers:
            cells = groups.get(str(layer["id"]))
            if not cells:
                continue
            working = deepcopy(layer)
            for p in cells:
                self._set_tile_cell(working, *p, None)
            for (x, y), cell in cells.items():
                self._set_tile_cell(working, x + columns, y + rows, cell)
            working_layers.append((layer, working))
        self._history_current = self._capture_snapshot()
        for layer, working in working_layers:
            layer["chunks"] = working["chunks"]
        for record, transform in transforms:
            self._apply_record_transform(record, transform)
        for identifier, cells in groups.items():
            moved = {(x + columns, y + rows) for x, y in cells}
            if identifier == self._tile_selection_layer_id:
                self._tile_selection = moved
            else:
                self._extra_tile_selections[identifier] = moved
        self.reset_tile_transform()
        self.mark_changed("Move selection")
        return True

    def copy_world_selection(self) -> WorldSelectionClipboard | None:
        groups = {identifier: cells for identifier, cells in self.selected_tile_groups.items() if cells}
        objects: list[
            tuple[str, str | None, dict[str, JsonValue], tuple[float, float, float, float, float]]
        ] = []
        origins = []
        layers = {str(layer["id"]): deepcopy(layer) for layer in self.selected_layers}
        for identifier, cells in groups.items():
            layer = layers[identifier]
            width, height = self._tile_pair(layer.get("cell_size"), "cell_size")
            ox, oy = self._tile_pair(layer.get("offset", [0, 0]), "offset")
            origins.extend((ox + x * width, oy + y * height) for x, y in cells)
        if self.mixed_layer_selection:
            selected = set(self.selected_ids)
            for layer in self.selected_layers:
                if layer.get("type", "object") != "object":
                    continue
                for record in walk_object_records(layer_objects(layer)):
                    if record.get("id") not in selected or self._object_has_selected_ancestor(record, selected):
                        continue
                    parent = self.object_parent(record)
                    world = self.object_world_transform(record)
                    objects.append((str(layer["id"]), str(parent["id"]) if parent else None, deepcopy(record), world))
                    origins.append(world[:2])
        if not origins:
            return None
        return WorldSelectionClipboard(
            self.asset_root, layers, deepcopy(groups), objects,
            (min(p[0] for p in origins), min(p[1] for p in origins)),
        )

    def paste_world_selection(
        self, clipboard: WorldSelectionClipboard, columns: int, rows: int,
    ) -> None:
        if not isinstance(columns, int) or isinstance(columns, bool) or not isinstance(rows, int) or isinstance(rows, bool):
            raise ValueError("Selection paste must use whole cells")
        if clipboard.asset_root != self.asset_root:
            raise ValueError("Paste tiles and objects into a scene with the same asset root")
        width, height = self.selection_grid_size
        destinations = {str(layer["id"]): layer for layer in self.selected_layers}
        tile_only = not clipboard.objects and len(clipboard.tiles) == 1
        if tile_only and len(destinations) == 1:
            identifier = next(iter(clipboard.tiles))
            destinations = {identifier: self._selected_tile_layer()}
        working: dict[str, dict[str, JsonValue]] = {}
        pasted_tiles: dict[str, set[tuple[int, int]]] = {}
        pasted_ids = []
        for identifier, cells in clipboard.tiles.items():
            if not cells:
                continue
            target = destinations.get(identifier)
            if target is None or target.get("type") != "tile":
                raise ValueError("Select the copied tile and object layers before pasting a mixed selection")
            source = clipboard.layers[identifier]
            if self._tile_pair(source.get("cell_size"), "cell_size") != (width, height):
                raise ValueError("Copied tiles and destination must have matching cell sizes")
            clone = working.setdefault(str(target["id"]), deepcopy(target))
            target_sets = self._tile_layer_tilesets(clone)
            remap: dict[int, int] = {}
            for source_set in self._tile_layer_tilesets(deepcopy(source)):
                comparable = {k: v for k, v in source_set.items() if k not in {"id", "first_gid", "name"}}
                equivalent = next(
                    (item for item in target_sets if
                     {k: v for k, v in item.items() if k not in {"id", "first_gid", "name"}} == comparable),
                    None,
                )
                if equivalent is None:
                    equivalent = deepcopy(source_set)
                    equivalent["id"] = str(uuid4())
                    equivalent["first_gid"] = self._next_tileset_gid(target_sets)
                    target_sets.append(equivalent)
                    if clone.get("tileset") is None:
                        clone["tileset"] = equivalent
                first, last = self._tileset_range(source_set)
                destination_first, _ = self._tileset_range(equivalent)
                count = last - first + 1
                remap.update((first + i, destination_first + i) for i in range(count))
            selected = set()
            source_offset = self._tile_pair(source.get("offset", [0, 0]), "offset")
            target_offset = self._tile_pair(target.get("offset", [0, 0]), "offset")
            offset_columns = round((source_offset[0] - target_offset[0]) / width)
            offset_rows = round((source_offset[1] - target_offset[1]) / height)
            for (x, y), cell in cells.items():
                if cell.tile_id not in remap:
                    raise SceneFormatError("Copied tile has no tileset definition")
                destination = (x + columns + offset_columns, y + rows + offset_rows)
                self._set_tile_cell(clone, *destination, TileCell(remap[cell.tile_id], cell.flags))
                selected.add(destination)
            pasted_tiles[str(target["id"])] = selected
        for identifier, parent_identifier, record, world_transform in clipboard.objects:
            target = destinations.get(identifier)
            if target is None or target.get("type", "object") != "object":
                raise ValueError("Select the copied tile and object layers before pasting a mixed selection")
            clone_layer = working.setdefault(identifier, deepcopy(target))
            objects = layer_objects(clone_layer)
            parent = next((r for r in walk_object_records(objects) if r.get("id") == parent_identifier), None)
            if parent_identifier is not None and parent is None:
                raise ValueError("The copied object's parent no longer exists")
            clone = self._clone_for_paste(record)
            original_parent = next(
                (r for r in self.records if r.get("id") == parent_identifier), None,
            )
            x, y, angle, sx, sy = world_transform
            local = self._local_transform_for_world(
                (x + columns * width, y + rows * height, angle, sx, sy), original_parent,
            )
            self._apply_record_transform(clone, local)
            if parent is None:
                objects.append(clone)
            else:
                children = parent.setdefault("children", [])
                if not isinstance(children, list):
                    raise SceneFormatError("object children must be an array")
                children.append(clone)
            pasted_ids.append(str(clone["id"]))
        self._history_current = self._capture_snapshot()
        for identifier, clone in working.items():
            target = next(layer for layer in self.document.layers if layer.get("id") == identifier)
            target.clear()
            target.update(clone)
        self.selected_ids = pasted_ids
        self.selection_anchor_id = self.selected_id
        self._tile_selection_layer_id = self.selected_layer_id
        self._tile_selection = pasted_tiles.pop(str(self.selected_layer_id), set())
        self._extra_tile_selections = pasted_tiles
        self.mark_changed("Paste selection")

    def tile_conversion_destination(self) -> str:
        objects = [
            layer for layer in self.selected_layers if layer.get("type", "object") == "object"
        ]
        if len(objects) == 1:
            return str(objects[0]["id"])
        layers = [layer for layer in self.document.layers if layer.get("type", "object") == "object"]
        layer = next((layer for layer in layers if layer.get("default") is True), layers[0] if layers else None)
        return str(layer["id"]) if layer is not None else ""

    def convert_selected_tiles(
        self, name: str, destination_layer_id: str | None, *,
        include_collisions: bool = False, new_layer_name: str | None = None,
    ) -> dict[str, JsonValue]:
        from g import Game
        from g.persistence import SceneLoadContext, SceneSerializer
        from g.physics import PhysicsWorld

        if not name.strip():
            raise ValueError("Enter a name for the new GameObject")
        groups = {identifier: cells for identifier, cells in self.selected_tile_groups.items() if cells}
        if not groups:
            raise ValueError("Select placed tiles before converting")
        if new_layer_name is not None:
            if not new_layer_name.strip() or any(layer.get("name") == new_layer_name.strip() for layer in self.document.layers):
                raise ValueError("New layer name must be non-empty and unique")
            destination: dict[str, JsonValue] = {
                "type": "object", "id": str(uuid4()), "name": new_layer_name.strip(),
                "visible": True, "enabled": True, "default": False, "objects": [],
            }
        else:
            target = next((layer for layer in self.document.layers if layer.get("id") == destination_layer_id), None)
            if target is None or target.get("type", "object") != "object":
                raise ValueError("Choose an existing object layer")
            destination = target
        sources = [layer for layer in self.document.layers if str(layer["id"]) in groups]
        bounds = []
        for layer in sources:
            width, height = self._tile_pair(layer.get("cell_size"), "cell_size")
            ox, oy = self._tile_pair(layer.get("offset", [0, 0]), "offset")
            bounds.extend((ox + x * width, oy + y * height, ox + (x + 1) * width, oy + (y + 1) * height)
                          for x, y in groups[str(layer["id"])])
        origin = (
            (min(b[0] for b in bounds) + max(b[2] for b in bounds)) / 2,
            (min(b[1] for b in bounds) + max(b[3] for b in bounds)) / 2,
        )
        record = new_object_record("empty", x=origin[0], y=origin[1])
        record["name"] = name.strip()
        patches: list[JsonValue] = []
        colliders: list[JsonValue] = []
        working_sources = []
        context = SceneLoadContext(Game(1, 1, asset_root=self.asset_root), self.asset_root, {})
        serializer = SceneSerializer()
        for source in sources:
            cells = groups[str(source["id"])]
            patch = deepcopy(source)
            patch["id"] = str(uuid4())
            patch["chunks"] = []
            ox, oy = self._tile_pair(source.get("offset", [0, 0]), "offset")
            patch["offset"] = [ox - origin[0], oy - origin[1]]
            patch["collision_enabled"] = True
            patch["navigation_enabled"] = False
            for p, cell in cells.items():
                self._set_tile_cell(patch, *p, cell)
            # Validate resources and use the same flip/collision rules as runtime tilemaps.
            runtime = serializer._restore_tile_layer(None, patch, context, detached=True)
            if include_collisions:
                entries = runtime._collision_entries()
                full = {
                    (x, y) for x, y, _, collision in entries
                    if collision.shape == "rectangle" and collision.x == 0 and collision.y == 0
                    and collision.width in (None, runtime.cell_width)
                    and collision.height in (None, runtime.cell_height)
                }
                for x, y, width, height in PhysicsWorld._merged_cells(full):
                    left, bottom = runtime.cell_origin(x, y)
                    w, h = width * runtime.cell_width, height * runtime.cell_height
                    colliders.append({
                        "type": "g.collider",
                        "properties": {
                            "shape": "box", "width": w, "height": h,
                            "offset_x": left + w / 2, "offset_y": bottom + h / 2,
                        },
                    })
                for x, y, cell, collision in entries:
                    if (x, y) in full:
                        continue
                    left, bottom = runtime.cell_origin(x, y)
                    for polygon in PhysicsWorld._tile_collision_polygons(runtime, collision):
                        points = [
                            [left + px * runtime.cell_width, bottom + py * runtime.cell_height]
                            for point in polygon
                            for px, py in (PhysicsWorld._transform_tile_collision_point(point, cell.flags),)
                        ]
                        for piece in Collider.decompose(tuple((point[0], point[1]) for point in points)):
                            properties: dict[str, JsonValue] = {
                                "shape": "polygon", "points": [list(point) for point in piece],
                            }
                            collider_record: dict[str, JsonValue] = {"type": "g.collider", "properties": properties}
                            colliders.append(collider_record)
            patches.append(patch)
            working = deepcopy(source)
            for p in cells:
                self._set_tile_cell(working, *p, None)
            working_sources.append((source, working))
        record["components"] = [
            {"type": "g.tile_renderer", "properties": {"patches": patches, "opacity": 255}},
            *colliders,
        ]
        self._history_current = self._capture_snapshot()
        if new_layer_name is not None:
            self.document.layers.append(destination)
        layer_objects(destination).append(record)
        normalize_record_order(layer_objects(destination))
        for source, working in working_sources:
            source["chunks"] = working["chunks"]
        self.clear_tile_selection()
        self._selected_layer_ids = [str(destination["id"])]
        self.selected_layer_id = str(destination["id"])
        self.selected_id = str(record["id"])
        self.mark_changed("Convert tiles to GameObject")
        return record

    def move_selected_tiles(self, columns: int, rows: int) -> bool:
        layer = self._selected_tile_layer()
        if (
            not isinstance(columns, int)
            or isinstance(columns, bool)
            or not isinstance(rows, int)
            or isinstance(rows, bool)
        ):
            raise ValueError("tile movement must use whole cells")
        cells = self.selected_tile_cells
        if not cells or (columns == 0 and rows == 0):
            return False
        working = deepcopy(layer)
        # Read all sources before erasing so overlapping moves preserve tiles.
        for column, row in cells:
            self._set_tile_cell(working, column, row, None)
        for (column, row), cell in cells.items():
            self._set_tile_cell(
                working, column + columns, row + rows, cell
            )
        self._history_current = self._capture_snapshot()
        layer["chunks"] = working["chunks"]
        self._tile_selection = {
            (column + columns, row + rows) for column, row in cells
        }
        self.reset_tile_transform()
        self.mark_changed("Move tiles")
        return True

    def tile_definition(
        self,
        tile_id: int,
        *,
        create: bool = False,
    ) -> dict[str, JsonValue] | None:
        layer = self._selected_tile_layer()
        return self._tile_definition_for_layer(
            layer,
            tile_id,
            create=create,
        )

    def _tile_definition_for_layer(
        self,
        layer: dict[str, JsonValue],
        tile_id: int,
        *,
        create: bool = False,
    ) -> dict[str, JsonValue] | None:
        tileset, local_id = self._tileset_for_global_id(layer, tile_id)
        definitions = tileset.setdefault("tiles", [])
        if not isinstance(definitions, list):
            raise SceneFormatError("tileset tiles must be an array")
        existing = next(
            (
                definition
                for definition in definitions
                if isinstance(definition, dict)
                and definition.get("id") == local_id
            ),
            None,
        )
        if existing is not None or not create:
            return cast(dict[str, JsonValue] | None, existing)
        definition: dict[str, JsonValue] = {
            "id": local_id,
            "name": None,
            "tags": [],
            "properties": {},
        }
        definitions.append(definition)
        return definition

    def set_tile_definition_value(
        self,
        tile_id: int,
        key: str,
        value: JsonValue,
        *,
        coalesce: bool = True,
    ) -> None:
        if key not in {
            "name",
            "collision",
            "navigation",
            "animation",
            "tags",
            "properties",
        }:
            raise KeyError(key)
        definition = self.tile_definition(tile_id, create=True)
        assert definition is not None
        definition[key] = value
        self.mark_changed(
            f"Changed tile {tile_id} {key}" if coalesce else f"Edited tile {tile_id} {key}"
        )

    def set_tile_definitions_value(
        self,
        tile_ids: list[int] | tuple[int, ...],
        key: str,
        value: JsonValue,
    ) -> None:
        if key not in {
            "collision",
            "navigation",
            "tags",
            "properties",
        }:
            raise KeyError(key)
        layer = self._selected_tile_layer()
        targets = tuple(dict.fromkeys(tile_ids))
        if not targets:
            return
        definitions = [
            self._tile_definition_for_layer(
                layer,
                tile_id,
                create=True,
            )
            for tile_id in targets
        ]
        if all(
            definition is not None and definition.get(key) == value
            for definition in definitions
        ):
            return
        for definition in definitions:
            assert definition is not None
            definition[key] = deepcopy(value)
        self.mark_changed(
            f"Changed {key} for {len(targets)} tiles"
        )

    def _selected_tile_layer(self) -> dict[str, JsonValue]:
        layer = self.selected_layer
        if layer is None or layer.get("type") != "tile":
            raise ValueError("select a tile layer first")
        return layer

    def _tile_layer_tilesets(
        self,
        layer: dict[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        raw = layer.get("tilesets")
        legacy = layer.get("tileset")
        if isinstance(raw, list) and (raw or not isinstance(legacy, dict)):
            return cast(list[dict[str, JsonValue]], raw)
        legacy = layer.pop("tileset", None)
        tilesets: list[dict[str, JsonValue]] = []
        if isinstance(legacy, dict):
            legacy.setdefault("id", str(uuid4()))
            legacy.setdefault("name", "Tileset")
            legacy.setdefault("first_gid", 0)
            tilesets.append(cast(dict[str, JsonValue], legacy))
        layer["tilesets"] = cast(JsonValue, tilesets)
        return tilesets

    @staticmethod
    def _next_tileset_gid(
        tilesets: list[dict[str, JsonValue]],
    ) -> int:
        next_gid = 0
        for tileset in tilesets:
            first_gid = tileset.get("first_gid")
            tile_count = tileset.get("tile_count")
            if isinstance(first_gid, int) and isinstance(tile_count, int):
                span = (
                    max(tile_count, COLLECTION_TILE_GID_CAPACITY)
                    if tileset.get("type") in {"color", "images"}
                    else tile_count
                )
                next_gid = max(next_gid, first_gid + span)
        return next_gid

    def _tileset_by_id(
        self,
        layer: dict[str, JsonValue],
        identifier: str,
    ) -> dict[str, JsonValue]:
        tileset = next(
            (
                candidate
                for candidate in self._tile_layer_tilesets(layer)
                if candidate.get("id") == identifier
            ),
            None,
        )
        if tileset is None:
            raise ValueError(f"unknown tileset: {identifier!r}")
        return tileset

    @staticmethod
    def _tileset_range(
        tileset: dict[str, JsonValue],
    ) -> tuple[int, int]:
        first_gid = tileset.get("first_gid")
        tile_count = tileset.get("tile_count")
        if not isinstance(first_gid, int) or not isinstance(tile_count, int):
            raise SceneFormatError("tileset global range is invalid")
        return first_gid, first_gid + tile_count - 1

    def _tileset_for_global_id(
        self,
        layer: dict[str, JsonValue],
        tile_id: int,
    ) -> tuple[dict[str, JsonValue], int]:
        if not isinstance(tile_id, int) or isinstance(tile_id, bool):
            raise ValueError("tile ID must be an integer")
        for tileset in self._tile_layer_tilesets(layer):
            first_gid, last_gid = self._tileset_range(tileset)
            if first_gid <= tile_id <= last_gid:
                return tileset, tile_id - first_gid
        tilesets = self._tile_layer_tilesets(layer)
        if len(tilesets) == 1:
            first_gid, last_gid = self._tileset_range(tilesets[0])
            raise ValueError(
                f"tile ID must be between {first_gid} and {last_gid}"
            )
        raise ValueError(f"unknown global tile ID: {tile_id}")

    @staticmethod
    def _tile_chunks(
        layer: dict[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        chunks = layer.setdefault("chunks", [])
        if not isinstance(chunks, list):
            raise SceneFormatError("tile layer chunks must be an array")
        return cast(list[dict[str, JsonValue]], chunks)

    def _paint_tile_cell(
        self,
        layer: dict[str, JsonValue],
        column: int,
        row: int,
        cell: TileCell | None,
    ) -> bool:
        return self._set_tile_cell(layer, column, row, cell)

    @staticmethod
    def _validate_tile_id(
        tileset: dict[str, JsonValue],
        tile_id: int,
    ) -> None:
        if not isinstance(tile_id, int) or isinstance(tile_id, bool):
            raise ValueError("tile ID must be an integer")
        tile_count = tileset.get("tile_count")
        if isinstance(tile_count, int) and not isinstance(tile_count, bool):
            if not 0 <= tile_id < tile_count:
                raise ValueError(
                    f"tile ID must be between 0 and {tile_count - 1}"
                )
            return
        TileCell(tile_id)

    @staticmethod
    def _tile_pair(
        value: object,
        name: str,
    ) -> tuple[float, float]:
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                for item in value
            )
        ):
            raise SceneFormatError(f"tile layer {name} is invalid")
        return float(value[0]), float(value[1])

    def mark_changed(self, status: str = "Scene modified") -> None:
        after = self._capture_snapshot()
        if after == self._history_current:
            self.status = status
            self.error = None
            return
        now = monotonic()
        del self._history[self._history_cursor :]
        coalescible = status.startswith(
            ("Changed ", "Moved ", "Painted ", "Erased ")
        )
        if (
            coalescible
            and self._history
            and self._history_cursor == len(self._history)
            and self._history[-1].label == status
            and now - self._history[-1].timestamp <= 0.35
        ):
            self._history[-1].after = after
            self._history[-1].timestamp = now
        else:
            self._history.append(
                HistoryEntry(
                    status,
                    self._history_current,
                    after,
                    now,
                )
            )
            if len(self._history) > self._history_limit:
                del self._history[0]
            self._history_cursor = len(self._history)
        self._history_current = after
        self.dirty = self.document != self._saved_document
        self.revision += 1
        self.status = status
        self.error = None

    def begin_history_change(self) -> EditorSnapshot:
        return self._capture_snapshot()

    def commit_history_change(
        self,
        label: str,
        before: EditorSnapshot,
        *,
        undo_effect: Callable[[], None],
        redo_effect: Callable[[], None],
    ) -> None:
        after = self._capture_snapshot()
        del self._history[self._history_cursor :]
        self._history.append(
            HistoryEntry(
                label,
                before,
                after,
                monotonic(),
                undo_effect,
                redo_effect,
            )
        )
        if len(self._history) > self._history_limit:
            del self._history[0]
        self._history_cursor = len(self._history)
        self._history_current = after
        self.dirty = self.document != self._saved_document
        self.revision += 1
        self.status = label
        self.error = None

    @property
    def can_undo(self) -> bool:
        return self._history_cursor > 0

    @property
    def can_redo(self) -> bool:
        return self._history_cursor < len(self._history)

    @property
    def undo_label(self) -> str | None:
        if not self.can_undo:
            return None
        return self._history[self._history_cursor - 1].label

    @property
    def redo_label(self) -> str | None:
        if not self.can_redo:
            return None
        return self._history[self._history_cursor].label

    def undo(self) -> bool:
        if not self.can_undo:
            return False
        entry = self._history[self._history_cursor - 1]
        if entry.undo_effect is not None:
            entry.undo_effect()
        self._history_cursor -= 1
        self._restore_snapshot(entry.before)
        self.status = f"Undid {entry.label}"
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            return False
        entry = self._history[self._history_cursor]
        if entry.redo_effect is not None:
            entry.redo_effect()
        self._history_cursor += 1
        self._restore_snapshot(entry.after)
        self.status = f"Redid {entry.label}"
        return True

    def _capture_snapshot(self) -> EditorSnapshot:
        return EditorSnapshot(
            deepcopy(self.document),
            self.selected_layer_id,
            list(self.selected_ids),
            self.selection_anchor_id,
            dict(self.active_tileset_ids),
            list(self.palette_tile_ids),
            self.palette_selection_size,
            deepcopy(self._sampled_tile_cells),
            self._sampled_tile_size,
            self._tile_selection_layer_id,
            set(self._tile_selection),
            [str(layer["id"]) for layer in self.selected_layers],
            deepcopy(self._extra_tile_selections),
        )

    def _restore_snapshot(self, snapshot: EditorSnapshot) -> None:
        restored = deepcopy(snapshot)
        self.document = restored.document
        self.selected_layer_id = restored.selected_layer_id
        self.selected_ids = restored.selected_ids
        self.selection_anchor_id = restored.selection_anchor_id
        self.active_tileset_ids = restored.active_tileset_ids
        self.palette_tile_ids = restored.palette_tile_ids
        self.palette_selection_size = restored.palette_selection_size
        self._sampled_tile_cells = restored.sampled_tile_cells
        self._sampled_tile_size = restored.sampled_tile_size
        self._tile_selection_layer_id = restored.tile_selection_layer_id
        self._tile_selection = restored.tile_selection
        self._selected_layer_ids = restored.selected_layer_ids
        self._extra_tile_selections = restored.extra_tile_selections
        self.reset_tile_transform()
        self._history_current = self._capture_snapshot()
        self.dirty = self.document != self._saved_document
        self.revision += 1
        self.error = None

    def _reset_history(self) -> None:
        self._history.clear()
        self._history_cursor = 0
        self._history_current = self._capture_snapshot()
        self._saved_document = deepcopy(self.document)

    def _normalize_hierarchy_order(self) -> None:
        for layer in self.document.layers:
            if layer.get("type", "object") == "object":
                normalize_record_order(layer_objects(layer))
        for view in self.document.ui_views:
            normalize_gui_order(layer_gui_elements(view))

    def _normalize_selected_layer_order(self) -> None:
        layer = self.selected_layer
        if layer is None:
            return
        if layer.get("type", "object") == "object":
            normalize_record_order(layer_objects(layer))
        elif layer.get("type") == "ui":
            normalize_gui_order(layer_gui_elements(layer))

    def set_grid_size(self, value: int) -> None:
        if value <= 0:
            raise ValueError("grid size must be positive")
        self.document.settings["grid_cell_size"] = value
        self.mark_changed("Grid size changed")

    def set_scene_background(self, color: JsonValue) -> None:
        if (
            not isinstance(color, list)
            or len(color) != 4
            or not all(
                isinstance(channel, int)
                and not isinstance(channel, bool)
                and 0 <= channel <= 255
                for channel in color
            )
        ):
            raise ValueError(
                "scene background must contain four color channels"
            )
        self.document.settings["background"] = color
        self.mark_changed("Changed scene background")

    def set_scene_ambient_light(self, color: JsonValue) -> None:
        """Set the ambient color used when native scene lighting is active."""
        if (
            not isinstance(color, list)
            or len(color) != 4
            or not all(
                isinstance(channel, int)
                and not isinstance(channel, bool)
                and 0 <= channel <= 255
                for channel in color
            )
        ):
            raise ValueError(
                "scene ambient light must contain four color channels"
            )
        self.document.settings["ambient_light"] = color
        self.mark_changed("Changed scene ambient light")

    def set_scene_camera_value(
        self,
        key: str,
        value: JsonValue,
    ) -> None:
        if key == "position":
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("camera position must contain two numbers")
            x = value[0]
            y = value[1]
            if (
                not isinstance(x, (int, float))
                or isinstance(x, bool)
                or not isfinite(x)
                or not isinstance(y, (int, float))
                or isinstance(y, bool)
                or not isfinite(y)
            ):
                raise ValueError("camera position must contain two numbers")
            stored: JsonValue = {"$vec2": [float(x), float(y)]}
        elif key in {"zoom", "rotation"}:
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not isfinite(value)
            ):
                raise ValueError(f"camera {key} must be a finite number")
            if key == "zoom" and value <= 0:
                raise ValueError("camera zoom must be positive")
            stored = float(value)
        else:
            raise KeyError(key)
        camera = self.document.settings.setdefault("camera", {})
        if not isinstance(camera, dict):
            raise SceneFormatError("scene camera settings must be an object")
        camera[key] = stored
        self.mark_changed(f"Changed scene camera {key}")

    def set_presentation_value(
        self,
        key: str,
        value: JsonValue,
    ) -> None:
        if key == "scaling":
            if value not in {"fit", "integer_fit", "fill", "stretch"}:
                raise ValueError("unknown presentation scaling mode")
        elif key == "matte_color":
            if (
                not isinstance(value, list)
                or len(value) != 4
                or any(
                    not isinstance(channel, int)
                    or isinstance(channel, bool)
                    or not 0 <= channel <= 255
                    for channel in value
                )
            ):
                raise ValueError(
                    "presentation matte must contain four color channels"
                )
        elif key not in {"resizable", "fullscreen"}:
            raise KeyError(key)
        elif not isinstance(value, bool):
            raise ValueError(f"presentation {key} must be a boolean")
        presentation = self.document.settings.setdefault(
            "presentation",
            {},
        )
        if not isinstance(presentation, dict):
            raise SceneFormatError(
                "scene presentation settings must be an object"
            )
        presentation[key] = value
        self.mark_changed(f"Changed presentation {key}")

    def set_scene_size(self, width: int, height: int) -> None:
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or width <= 0
            or not isinstance(height, int)
            or isinstance(height, bool)
            or height <= 0
        ):
            raise ValueError(
                "scene resolution must contain two positive integers"
            )
        size = [width, height]
        if self.document.settings.get("size") == size:
            return
        self.document.settings["size"] = size
        self.mark_changed("Changed scene resolution")

    def set_scene_physics_value(
        self,
        key: str,
        value: JsonValue,
    ) -> None:
        if key not in {"gravity", "damping"}:
            raise KeyError(key)
        if key == "gravity" and (
            not isinstance(value, list)
            or len(value) != 2
            or not all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                for item in value
            )
        ):
            raise ValueError("gravity must contain two numbers")
        if key == "damping" and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError("physics damping must not be negative")
        physics = self.document.settings.setdefault("physics", {})
        if not isinstance(physics, dict):
            raise SceneFormatError("scene physics settings must be an object")
        physics[key] = value
        self.mark_changed(f"Changed scene physics {key}")

    def _normalize_scene_settings(self) -> None:
        scene_size = self.document.settings.get(
            "size",
            list(DEFAULT_SCENE_SIZE),
        )
        default_camera_position = [
            DEFAULT_SCENE_SIZE[0] / 2,
            DEFAULT_SCENE_SIZE[1] / 2,
        ]
        scene_width = (
            scene_size[0]
            if isinstance(scene_size, list) and len(scene_size) == 2
            else None
        )
        scene_height = (
            scene_size[1]
            if isinstance(scene_size, list) and len(scene_size) == 2
            else None
        )
        if (
            isinstance(scene_width, (int, float))
            and not isinstance(scene_width, bool)
            and scene_width > 0
            and isinstance(scene_height, (int, float))
            and not isinstance(scene_height, bool)
            and scene_height > 0
        ):
            default_camera_position = [
                float(scene_width) / 2,
                float(scene_height) / 2,
            ]
        camera = self.document.settings.get("camera")
        if camera is None:
            camera = {}
            self.document.settings["camera"] = camera
        if not isinstance(camera, dict):
            raise SceneFormatError("scene camera settings must be an object")
        if "position" not in camera:
            camera["position"] = cast(
                JsonValue,
                {"$vec2": default_camera_position},
            )
        camera.setdefault("zoom", 1)
        camera.setdefault("rotation", 0)
        physics = self.document.settings.setdefault("physics", {})
        if not isinstance(physics, dict):
            raise SceneFormatError("scene physics settings must be an object")
        physics.setdefault("gravity", [0, -980])
        physics.setdefault("damping", 1.0)
        presentation = self.document.settings.setdefault(
            "presentation",
            {
                "scaling": "fit",
                "matte_color": deepcopy(
                    self.document.settings.get(
                        "background",
                        [30, 30, 36, 255],
                    )
                ),
                "resizable": True,
                "fullscreen": False,
            },
        )
        if not isinstance(presentation, dict):
            raise SceneFormatError(
                "scene presentation settings must be an object"
            )
        presentation.setdefault("scaling", "fit")
        presentation.setdefault(
            "matte_color",
            deepcopy(
                self.document.settings.get(
                    "background",
                    [30, 30, 36, 255],
                )
            ),
        )
        presentation.setdefault("resizable", True)
        presentation.setdefault("fullscreen", False)
        for layer in self.document.layers:
            if layer.get("type") != "tile":
                continue
            layer.setdefault("collision_generation", "rectangle")
            layer.setdefault("collision_alpha_threshold", 255)
            layer.setdefault("collision_alpha_inverted", False)
            if layer.get("collision_enabled") is True:
                self._generate_missing_collisions_for_painted_tiles(
                    layer
                )

    def set_snap_enabled(self, enabled: bool) -> None:
        self.document.settings["grid_snap"] = enabled
        self.mark_changed("Grid snapping changed")

    def grid_cell_center(
        self,
        x: float,
        y: float,
    ) -> tuple[float, float]:
        grid = self.grid_size
        half = grid / 2
        return (
            floor(x / grid) * grid + half,
            floor(y / grid) * grid + half,
        )

    def move_selected(self, x: float, y: float) -> None:
        record = self.selected
        if record is None:
            return
        if self.snap_enabled:
            x, y = self.grid_cell_center(x, y)
        if position(record) == (float(x), float(y)):
            return
        set_position(record, float(x), float(y))
        self.mark_changed("Object moved")

    def move_selected_world(self, x: float, y: float) -> None:
        record = self.selected
        if record is None:
            return
        if self.snap_enabled:
            x, y = self.grid_cell_center(x, y)
        world = list(self.object_world_transform(record))
        world[0] = float(x)
        world[1] = float(y)
        local = self._local_transform_for_world(
            cast(
                tuple[float, float, float, float, float],
                tuple(world),
            ),
            self.object_parent(record),
        )
        self._apply_record_transform(record, local)
        self.mark_changed("Object moved in world space")

    def rotate_selected_world(self, rotation: float) -> None:
        record = self.selected
        if record is None:
            return
        world = list(self.object_world_transform(record))
        world[2] = float(rotation)
        local = self._local_transform_for_world(
            cast(
                tuple[float, float, float, float, float],
                tuple(world),
            ),
            self.object_parent(record),
        )
        self._apply_record_transform(record, local)
        self.mark_changed("Object rotated in world space")

    def add_object(
        self, kind: str, *, x: float = 0, y: float = 0
    ) -> dict[str, JsonValue]:
        record = new_object_record(kind, x=x, y=y)
        layer = self.selected_layer or default_layer(self.document)
        if layer.get("type", "object") != "object":
            layer = default_layer(self.document)
        parent = self.selected
        if parent is None:
            layer_objects(layer).append(record)
        else:
            children = parent.setdefault("children", [])
            if not isinstance(children, list):
                raise SceneFormatError("object children must be an array")
            children.append(record)
        layer_id = layer.get("id")
        if isinstance(layer_id, str):
            self.selected_layer_id = layer_id
        identifier = record.get("id")
        self.selected_id = identifier if isinstance(identifier, str) else None
        normalize_record_order(layer_objects(layer))
        self.mark_changed(f"Added {record.get('name', 'object')}")
        return record

    def add_sprite(
        self,
        image: str | Path | None = None,
        *,
        x: float = 0,
        y: float = 0,
        at_root: bool = False,
    ) -> dict[str, JsonValue]:
        if image is None:
            record = new_object_record("sprite", x=x, y=y)
            relative = None
        else:
            relative, loaded = self._load_project_image(image)
            record = new_object_record(
                "sprite",
                x=x,
                y=y,
                image=relative,
                image_size=(loaded.width, loaded.height),
            )
        layer = self.selected_layer or default_layer(self.document)
        if layer.get("type", "object") != "object":
            raise ValueError("select an object layer before adding a sprite")
        parent = None if at_root else self.selected
        if parent is None:
            layer_objects(layer).append(record)
        else:
            children = parent.setdefault("children", [])
            if not isinstance(children, list):
                raise SceneFormatError("object children must be an array")
            children.append(record)
        identifier = record.get("id")
        self.selected_id = (
            identifier if isinstance(identifier, str) else None
        )
        normalize_record_order(layer_objects(layer))
        self.mark_changed(
            "Added sprite"
            if relative is None
            else f"Added sprite {Path(relative).name}"
        )
        return record

    def add_background(self) -> dict[str, JsonValue]:
        """Add an empty image pattern in a dedicated, ordinary background layer."""
        name = "Background"
        suffix = 2
        while True:
            layer = next((candidate for candidate in self.document.layers if candidate.get("name") == name), None)
            if layer is None:
                layer = {
                    "type": "object", "id": str(uuid4()), "name": name,
                    "visible": True, "enabled": True, "default": False, "objects": [],
                }
                self.document.layers.insert(0, layer)
                break
            if layer.get("type", "object") == "object":
                break
            name = f"Background {suffix}"
            suffix += 1
        record = new_object_record("background")
        objects = layer_objects(layer)
        objects.append(record)
        self.selected_layer_id = str(layer["id"])
        self.selected_id = str(record["id"])
        normalize_record_order(objects)
        self.mark_changed(f"Added {record['name']}")
        return record

    def set_selected_background_image(
        self, image: str | Path, *, component_index: int,
    ) -> None:
        properties = self._selected_background_properties(component_index)
        relative, loaded = self._load_project_image(image)
        first_image = properties.get("image") is None
        properties["image"] = relative
        if first_image:
            properties["tile_size"] = {"$vec2": [loaded.width, loaded.height]}
        self.mark_changed(f"Set background image to {Path(relative).name}")

    def _selected_background_properties(self, component_index: int) -> dict[str, JsonValue]:
        record = self.selected
        if record is None:
            raise ValueError("select an object with a RepeatingImageRenderer first")
        components = record.get("components", [])
        if not isinstance(components, list) or not 0 <= component_index < len(components):
            raise ValueError("background component index is out of range")
        component = components[component_index]
        if not isinstance(component, dict) or component.get("type") != "g.repeating_image_renderer":
            raise ValueError("selected component is not a RepeatingImageRenderer")
        properties = component.setdefault("properties", {})
        if not isinstance(properties, dict):
            raise SceneFormatError("component properties must be an object")
        return properties

    def clear_selected_background_image(self, *, component_index: int) -> bool:
        properties = self._selected_background_properties(component_index)
        if properties.get("image") is None:
            return False
        properties["image"] = None
        self.mark_changed("Cleared background image")
        return True

    def set_selected_sprite_image(
        self,
        image: str | Path,
    ) -> None:
        record = self.selected
        if record is None:
            raise ValueError("select an object with a SpriteRenderer first")
        components = record.get("components")
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        if not any(
            isinstance(component, dict) and component.get("type") == "g.sprite_renderer"
            and isinstance(component.get("properties"), dict)
            for component in components
        ):
            raise ValueError("select an object with a SpriteRenderer first")
        relative, loaded = self._load_project_image(image)
        renderer_found = False
        for component in components:
            if not isinstance(component, dict):
                continue
            properties = component.get("properties")
            if not isinstance(properties, dict):
                continue
            if component.get("type") == "g.sprite_renderer":
                properties["image"] = relative
                renderer_found = True
        if not renderer_found:
            raise SceneFormatError("sprite has no sprite renderer")
        record["name"] = Path(relative).stem or "Sprite"
        self.mark_changed(f"Changed sprite image to {relative}")

    def clear_selected_sprite_image(self) -> None:
        record = self.selected
        if record is None:
            raise ValueError("select an object with a SpriteRenderer first")
        components = record.get("components")
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        renderer = next(
            (
                properties
                for component in components
                if isinstance(component, dict)
                and component.get("type") == "g.sprite_renderer"
                and isinstance(properties := component.get("properties"), dict)
            ),
            None,
        )
        if renderer is None:
            raise SceneFormatError("sprite has no sprite renderer")
        renderer["image"] = None
        self.mark_changed("Cleared sprite image")

    def _load_project_image(
        self,
        image: str | Path,
    ) -> tuple[str, Image]:
        path = Path(image)
        resolved = (
            path.expanduser().resolve()
            if path.is_absolute()
            else (self.asset_root / path).resolve()
        )
        try:
            relative = resolved.relative_to(self.asset_root)
        except ValueError:
            raise ValueError(
                "sprite image must be below the asset root"
            ) from None
        if not resolved.is_file():
            raise ValueError(
                f"sprite image does not exist: {relative.as_posix()}"
            )
        try:
            loaded = Image(resolved)
        except (OSError, ImageDecodeException) as error:
            raise ValueError(
                f"could not load sprite image {relative.as_posix()}: "
                f"{error}"
            ) from error
        return relative.as_posix(), loaded

    def rename_asset_references(
        self,
        old_path: str,
        new_path: str,
        *,
        record_history: bool = True,
    ) -> int:
        changed = 0

        def replacement(value: object) -> str | None:
            if not isinstance(value, str):
                return None
            if value == old_path:
                return new_path
            prefix = f"{old_path}/"
            if value.startswith(prefix):
                return f"{new_path}/{value[len(prefix):]}"
            return None

        for layer in self.document.layers:
            if layer.get("type") == "tile":
                for tileset in self._tile_layer_tilesets(layer):
                    updated = replacement(tileset.get("image"))
                    if updated is not None:
                        tileset["image"] = updated
                        changed += 1
                    images = tileset.get("images")
                    if isinstance(images, list):
                        for index, image in enumerate(images):
                            updated = replacement(image)
                            if updated is not None:
                                images[index] = updated
                                changed += 1
            if layer.get("type", "object") != "object":
                continue
            for record in walk_object_records(layer_objects(layer)):
                metadata = record.get("prefab")
                if isinstance(metadata, dict):
                    updated = replacement(metadata.get("source"))
                    if updated is not None:
                        metadata["source"] = updated
                        changed += 1
                components = record.get("components", [])
                if not isinstance(components, list):
                    continue
                for component in components:
                    if not isinstance(component, dict):
                        continue
                    properties = component.get("properties")
                    if not isinstance(properties, dict):
                        continue
                    if component.get("type") in {"g.sprite_renderer", "g.repeating_image_renderer"}:
                        updated = replacement(properties.get("image"))
                        if updated is not None:
                            properties["image"] = updated
                            changed += 1
                    elif component.get("type") == "g.sprite_animator":
                        states = properties.get("states")
                        if not isinstance(states, list):
                            continue
                        for state in states:
                            if not isinstance(state, dict):
                                continue
                            updated = replacement(state.get("image"))
                            if updated is not None:
                                state["image"] = updated
                                changed += 1
                            frames = state.get("frames")
                            if not isinstance(frames, list):
                                continue
                            for frame in frames:
                                if not isinstance(frame, dict):
                                    continue
                                updated = replacement(frame.get("image"))
                                if updated is not None:
                                    frame["image"] = updated
                                    changed += 1
        for view in self.document.ui_views:
            metadata = view.get("prefab")
            if isinstance(metadata, dict):
                updated = replacement(metadata.get("source"))
                if updated is not None:
                    metadata["source"] = updated
                    changed += 1
        if changed and record_history:
            self.mark_changed(
                f"Updated {changed} renamed asset reference"
                f"{'' if changed == 1 else 's'}"
            )
        return changed

    def add_gui_element(
        self,
        kind: str,
        *,
        x: float | None = None,
        y: float | None = None,
    ) -> dict[str, JsonValue]:
        layer = self.selected_layer
        if layer is None or layer.get("type") != "ui":
            raise ValueError("select a UI view first")
        roots = layer_gui_elements(layer)
        parent = self.selected_gui_element
        if (
            parent is not None
            and parent.get("type") not in GUI_CONTAINER_TYPES
        ):
            parent = self.gui_element_parent(parent)
        if (
            parent is not None
            and parent.get("type") == "anchor"
            and self._gui_element_children(parent)
        ):
            raise ValueError(
                "an Anchor Layout can contain only one child"
            )
        if parent is None:
            target = roots
            record = new_gui_element_record(
                kind,
                x=310 if x is None else x,
                y=276 if y is None else y,
            )
        else:
            children = parent.setdefault("children", [])
            if not isinstance(children, list):
                raise SceneFormatError(
                    "GUI panel children must be an array"
                )
            target = cast(list[dict[str, JsonValue]], children)
            if (
                parent.get("type")
                in GUI_FREE_POSITION_CONTAINER_TYPES
            ):
                offset = 16 + len(target) * 16
                record = new_gui_element_record(
                    kind,
                    x=offset,
                    y=offset,
                )
            else:
                record = new_gui_element_record(kind, x=0, y=0)
        target.append(record)
        identifier = record.get("id")
        self.selected_id = (
            identifier if isinstance(identifier, str) else None
        )
        normalize_gui_order(roots)
        self.mark_changed(f"Added GUI {kind}")
        return record

    def set_gui_element_value(
        self,
        key: str,
        value: JsonValue,
    ) -> None:
        record = self.selected_gui_element
        if record is None:
            return
        if key == "z_index":
            raise ValueError(
                "GUI z-index is controlled by hierarchy order"
            )
        record[key] = value
        self.mark_changed(f"Changed GUI element {key}")

    def move_selected_gui_element(
        self,
        x: float,
        y: float,
    ) -> None:
        record = self.selected_gui_element
        if record is None:
            return
        if self.snap_enabled:
            grid = self.grid_size
            x = round(x / grid) * grid
            y = round(y / grid) * grid
        if record.get("x") == x and record.get("y") == y:
            return
        record["x"] = float(x)
        record["y"] = float(y)
        self.mark_changed("Moved GUI element")

    def move_selected_gui_elements_by(
        self,
        delta_x: float,
        delta_y: float,
    ) -> None:
        primary = self.selected_gui_element
        if primary is None:
            return
        primary_x, primary_y = self._gui_element_local_position(primary)
        target_x = primary_x + delta_x
        target_y = primary_y + delta_y
        if self.snap_enabled:
            grid = self.grid_size
            target_x = round(target_x / grid) * grid
            target_y = round(target_y / grid) * grid
        applied_x = target_x - primary_x
        applied_y = target_y - primary_y
        if applied_x == 0 and applied_y == 0:
            return
        selected = set(self.selected_ids)
        moved = False
        for element in self.selected_gui_elements:
            if not self.gui_element_is_free_positioned(element):
                continue
            parent = self.gui_element_parent(element)
            if (
                parent is not None
                and isinstance(parent.get("id"), str)
                and parent.get("id") in selected
            ):
                continue
            x, y = self._gui_element_local_position(element)
            element["x"] = x + applied_x
            element["y"] = y + applied_y
            moved = True
        if moved:
            self.mark_changed("Moved GUI elements")

    @staticmethod
    def _gui_element_children(
        element: Mapping[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        children = element.get("children")
        if not isinstance(children, list):
            return []
        return cast(
            list[dict[str, JsonValue]],
            [child for child in children if isinstance(child, dict)],
        )

    def gui_element_is_free_positioned(
        self,
        target: Mapping[str, JsonValue],
    ) -> bool:
        parent = self.gui_element_parent(target)
        return (
            parent is None
            or parent.get("type")
            in GUI_FREE_POSITION_CONTAINER_TYPES
        )

    def object_parent(
        self,
        target: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue] | None:
        def find(
            records: list[dict[str, JsonValue]],
        ) -> dict[str, JsonValue] | None:
            for record in records:
                children = record.get("children")
                if not isinstance(children, list):
                    continue
                typed_children = cast(
                    list[dict[str, JsonValue]],
                    [
                        child
                        for child in children
                        if isinstance(child, dict)
                    ],
                )
                if any(child is target for child in typed_children):
                    return record
                parent = find(typed_children)
                if parent is not None:
                    return parent
            return None

        for layer in self.document.layers:
            parent = find(layer_objects(layer))
            if parent is not None:
                return parent
        return None

    def _object_has_selected_ancestor(
        self,
        target: Mapping[str, JsonValue],
        selected: set[str],
    ) -> bool:
        parent = self.object_parent(target)
        while parent is not None:
            identifier = parent.get("id")
            if isinstance(identifier, str) and identifier in selected:
                return True
            parent = self.object_parent(parent)
        return False

    def _gui_has_selected_ancestor(
        self,
        target: Mapping[str, JsonValue],
        selected: set[str],
    ) -> bool:
        parent = self.gui_element_parent(target)
        while parent is not None:
            identifier = parent.get("id")
            if isinstance(identifier, str) and identifier in selected:
                return True
            parent = self.gui_element_parent(parent)
        return False

    def gui_element_at(
        self,
        x: float,
        y: float,
    ) -> str | None:
        layer = self.selected_layer
        if layer is None or layer.get("type") != "ui":
            return None
        def find(
            elements: list[dict[str, JsonValue]],
            parent_x: float,
            parent_y: float,
        ) -> str | None:
            candidates = sorted(
                elements,
                key=record_z_index,
                reverse=True,
            )
            for element in candidates:
                local_x, local_y = self._gui_element_local_position(element)
                element_x = parent_x + local_x
                element_y = parent_y + local_y
                children = element.get("children")
                if isinstance(children, list):
                    result = find(
                        cast(
                            list[dict[str, JsonValue]],
                            [
                                child
                                for child in children
                                if isinstance(child, dict)
                            ],
                        ),
                        element_x,
                        element_y,
                    )
                    if result is not None:
                        return result
                width = element.get("width", 0)
                height = element.get("height", 0)
                if not all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    for value in (width, height)
                ):
                    continue
                numeric_width, numeric_height = cast(
                    tuple[float | int, float | int],
                    (width, height),
                )
                if (
                    element_x <= x <= element_x + float(numeric_width)
                    and element_y <= y <= element_y + float(numeric_height)
                ):
                    identifier = element.get("id")
                    return (
                        identifier
                        if isinstance(identifier, str)
                        else None
                    )
            return None

        return find(layer_gui_elements(layer), 0, 0)

    @staticmethod
    def _gui_element_local_position(
        element: Mapping[str, JsonValue],
    ) -> tuple[float, float]:
        element_x = element.get("x", 0)
        element_y = element.get("y", 0)
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            for value in (element_x, element_y)
        ):
            return 0, 0
        numeric_x, numeric_y = cast(
            tuple[float | int, float | int],
            (element_x, element_y),
        )
        return float(numeric_x), float(numeric_y)

    def gui_element_parent(
        self,
        target: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue] | None:
        layer = self.selected_layer
        if layer is None or layer.get("type") != "ui":
            return None

        def find(
            elements: list[dict[str, JsonValue]],
        ) -> dict[str, JsonValue] | None:
            for element in elements:
                children = element.get("children")
                if not isinstance(children, list):
                    continue
                typed_children = cast(
                    list[dict[str, JsonValue]],
                    [
                        child
                        for child in children
                        if isinstance(child, dict)
                    ],
                )
                if any(child is target for child in typed_children):
                    return element
                parent = find(typed_children)
                if parent is not None:
                    return parent
            return None

        return find(layer_gui_elements(layer))

    def gui_element_screen_position(
        self,
        target: Mapping[str, JsonValue],
    ) -> tuple[float, float]:
        x, y = self._gui_element_local_position(target)
        parent = self.gui_element_parent(target)
        while parent is not None:
            parent_x, parent_y = self._gui_element_local_position(parent)
            x += parent_x
            y += parent_y
            parent = self.gui_element_parent(parent)
        return x, y

    @staticmethod
    def _record_location(
        records: list[dict[str, JsonValue]],
        target: Mapping[str, JsonValue],
    ) -> tuple[
        list[dict[str, JsonValue]],
        dict[str, JsonValue] | None,
        int,
    ] | None:
        for index, record in enumerate(records):
            if record is target:
                return records, None, index
            children = record.get("children")
            if not isinstance(children, list):
                continue
            typed_children = cast(
                list[dict[str, JsonValue]],
                children,
            )
            for child_index, child in enumerate(typed_children):
                if child is target:
                    return typed_children, record, child_index
            nested = EditorState._record_location(
                typed_children,
                target,
            )
            if nested is not None:
                target_list, parent, child_index = nested
                return (
                    target_list,
                    parent if parent is not None else record,
                    child_index,
                )
        return None

    @staticmethod
    def _record_transform(
        record: Mapping[str, JsonValue],
    ) -> tuple[float, float, float, float, float]:
        x, y = position(record)
        transform = record.get("transform")
        rotation = (
            transform.get("rotation", 0)
            if isinstance(transform, dict)
            else 0
        )
        scale = (
            transform.get("scale", [1, 1])
            if isinstance(transform, dict)
            else [1, 1]
        )
        numeric_rotation = (
            float(rotation)
            if isinstance(rotation, (int, float))
            and not isinstance(rotation, bool)
            else 0.0
        )
        if (
            not isinstance(scale, list)
            or len(scale) != 2
            or not all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                for item in scale
            )
        ):
            return x, y, numeric_rotation, 1.0, 1.0
        numeric_scale = cast(
            tuple[float | int, float | int],
            tuple(scale),
        )
        return (
            x,
            y,
            numeric_rotation,
            float(numeric_scale[0]),
            float(numeric_scale[1]),
        )

    def object_world_transform(
        self,
        record: Mapping[str, JsonValue],
    ) -> tuple[float, float, float, float, float]:
        x, y, rotation, scale_x, scale_y = self._record_transform(
            record
        )
        parent = self.object_parent(record)
        if parent is None:
            return x, y, rotation, scale_x, scale_y
        (
            parent_x,
            parent_y,
            parent_rotation,
            parent_scale_x,
            parent_scale_y,
        ) = self.object_world_transform(parent)
        local_x = x * parent_scale_x
        local_y = y * parent_scale_y
        angle = -radians(parent_rotation)
        cosine = cos(angle)
        sine = sin(angle)
        return (
            parent_x + local_x * cosine - local_y * sine,
            parent_y + local_x * sine + local_y * cosine,
            parent_rotation + rotation,
            parent_scale_x * scale_x,
            parent_scale_y * scale_y,
        )

    def _local_transform_for_world(
        self,
        world: tuple[float, float, float, float, float],
        parent: Mapping[str, JsonValue] | None,
    ) -> tuple[float, float, float, float, float]:
        if parent is None:
            return world
        (
            parent_x,
            parent_y,
            parent_rotation,
            parent_scale_x,
            parent_scale_y,
        ) = self.object_world_transform(parent)
        if parent_scale_x == 0 or parent_scale_y == 0:
            raise ValueError(
                "cannot reparent through an object with zero scale"
            )
        world_x, world_y, rotation, scale_x, scale_y = world
        delta_x = world_x - parent_x
        delta_y = world_y - parent_y
        angle = radians(parent_rotation)
        cosine = cos(angle)
        sine = sin(angle)
        return (
            (delta_x * cosine - delta_y * sine) / parent_scale_x,
            (delta_x * sine + delta_y * cosine) / parent_scale_y,
            rotation - parent_rotation,
            scale_x / parent_scale_x,
            scale_y / parent_scale_y,
        )

    @staticmethod
    def _apply_record_transform(
        record: dict[str, JsonValue],
        transform_value: tuple[float, float, float, float, float],
    ) -> None:
        x, y, rotation, scale_x, scale_y = transform_value
        set_position(record, x, y)
        transform = record.setdefault("transform", {})
        if not isinstance(transform, dict):
            transform = {}
            record["transform"] = transform
        transform["rotation"] = rotation
        transform["scale"] = [scale_x, scale_y]

    @staticmethod
    def _remove_record_roots(
        records: list[dict[str, JsonValue]],
        identifiers: set[str],
    ) -> None:
        kept: list[dict[str, JsonValue]] = []
        for record in records:
            if record.get("id") in identifiers:
                continue
            children = record.get("children")
            if isinstance(children, list):
                EditorState._remove_record_roots(
                    cast(list[dict[str, JsonValue]], children),
                    identifiers,
                )
            kept.append(record)
        records[:] = kept

    def reparent_selected_objects(
        self,
        target_identifier: str | None,
        placement: str,
    ) -> None:
        layer = self.selected_layer
        if layer is None or layer.get("type", "object") != "object":
            raise ValueError("select an object layer first")
        roots = layer_objects(layer)
        selected = set(self.selected_ids)
        moving = [
            record
            for record in walk_object_records(roots)
            if isinstance(record.get("id"), str)
            and record.get("id") in selected
            and not self._object_has_selected_ancestor(record, selected)
        ]
        if not moving:
            return
        target = (
            next(
                (
                    record
                    for record in walk_object_records(roots)
                    if record.get("id") == target_identifier
                ),
                None,
            )
            if target_identifier is not None
            else None
        )
        if placement not in {"before", "inside", "after", "root"}:
            raise ValueError(f"unknown hierarchy placement: {placement!r}")
        if placement != "root" and target is None:
            raise ValueError("the hierarchy drop target is unavailable")
        moving_subtree_ids = {
            identifier
            for record in moving
            for descendant in walk_object_records([record])
            if isinstance(identifier := descendant.get("id"), str)
        }
        if (
            target is not None
            and target.get("id") in moving_subtree_ids
        ):
            raise ValueError(
                "an object cannot be moved into its own subtree"
            )
        if placement == "inside":
            destination_parent = target
        elif placement in {"before", "after"}:
            assert target is not None
            location = self._record_location(roots, target)
            if location is None:
                raise ValueError("the hierarchy drop target is unavailable")
            _, destination_parent, _ = location
        else:
            destination_parent = None
        local_transforms = {
            str(record.get("id")): self._local_transform_for_world(
                self.object_world_transform(record),
                destination_parent,
            )
            for record in moving
        }
        moving_ids = {
            str(record.get("id"))
            for record in moving
        }
        self._remove_record_roots(roots, moving_ids)
        if placement == "root":
            destination = roots
            insertion_index = len(destination)
        elif placement == "inside":
            assert target is not None
            children = target.setdefault("children", [])
            if not isinstance(children, list):
                raise SceneFormatError("object children must be an array")
            destination = cast(list[dict[str, JsonValue]], children)
            insertion_index = len(destination)
        else:
            assert target is not None
            location = self._record_location(roots, target)
            if location is None:
                raise ValueError("the hierarchy drop target is unavailable")
            destination, _, target_index = location
            insertion_index = target_index + (
                1 if placement == "after" else 0
            )
        for offset, record in enumerate(moving):
            destination.insert(insertion_index + offset, record)
            self._apply_record_transform(
                record,
                local_transforms[str(record.get("id"))],
            )
        normalize_record_order(roots)
        self.mark_changed("Reordered game objects")

    def reparent_selected_gui_elements(
        self,
        target_identifier: str | None,
        placement: str,
        *,
        screen_positions: Mapping[str, tuple[float, float]] | None = None,
    ) -> None:
        layer = self.selected_layer
        if layer is None or layer.get("type") != "ui":
            raise ValueError("select a UI view first")
        roots = layer_gui_elements(layer)
        selected = set(self.selected_ids)
        moving = [
            record
            for record in walk_gui_element_records(roots)
            if isinstance(record.get("id"), str)
            and record.get("id") in selected
            and not self._gui_has_selected_ancestor(record, selected)
        ]
        if not moving:
            return
        target = (
            next(
                (
                    record
                    for record in walk_gui_element_records(roots)
                    if record.get("id") == target_identifier
                ),
                None,
            )
            if target_identifier is not None
            else None
        )
        if placement not in {"before", "inside", "after", "root"}:
            raise ValueError(f"unknown hierarchy placement: {placement!r}")
        if placement != "root" and target is None:
            raise ValueError("the GUI drop target is unavailable")
        moving_subtree_ids = {
            identifier
            for record in moving
            for descendant in walk_gui_element_records([record])
            if isinstance(identifier := descendant.get("id"), str)
        }
        if (
            target is not None
            and target.get("id") in moving_subtree_ids
        ):
            raise ValueError(
                "a GUI element cannot be moved into its own subtree"
            )
        if placement == "inside":
            assert target is not None
            if target.get("type") not in GUI_CONTAINER_TYPES:
                raise ValueError(
                    "GUI elements can only be children of containers"
                )
            destination_parent = target
        elif placement in {"before", "after"}:
            assert target is not None
            location = self._record_location(roots, target)
            if location is None:
                raise ValueError("the GUI drop target is unavailable")
            _, destination_parent, _ = location
        else:
            destination_parent = None
        positions = {
            str(record.get("id")): (
                screen_positions.get(
                    str(record.get("id")),
                    self.gui_element_screen_position(record),
                )
                if screen_positions is not None
                else self.gui_element_screen_position(record)
            )
            for record in moving
        }
        moving_ids = {
            str(record.get("id"))
            for record in moving
        }
        self._remove_record_roots(roots, moving_ids)
        if placement == "root":
            destination = roots
            insertion_index = len(destination)
        elif placement == "inside":
            assert target is not None
            children = target.setdefault("children", [])
            if not isinstance(children, list):
                raise SceneFormatError(
                    "GUI container children must be an array"
                )
            destination = cast(list[dict[str, JsonValue]], children)
            insertion_index = len(destination)
        else:
            assert target is not None
            location = self._record_location(roots, target)
            if location is None:
                raise ValueError("the GUI drop target is unavailable")
            destination, destination_parent, target_index = location
            insertion_index = target_index + (
                1 if placement == "after" else 0
            )
        if (
            destination_parent is not None
            and destination_parent.get("type") == "anchor"
            and len(destination) + len(moving) > 1
        ):
            raise ValueError(
                "an Anchor Layout can contain only one child"
            )
        parent_position = (
            self.gui_element_screen_position(destination_parent)
            if destination_parent is not None
            else (0.0, 0.0)
        )
        free_positioned = (
            destination_parent is None
            or destination_parent.get("type")
            in GUI_FREE_POSITION_CONTAINER_TYPES
        )
        for offset, record in enumerate(moving):
            destination.insert(insertion_index + offset, record)
            if free_positioned:
                screen_x, screen_y = positions[str(record.get("id"))]
                record["x"] = screen_x - parent_position[0]
                record["y"] = screen_y - parent_position[1]
            else:
                record["x"] = 0
                record["y"] = 0
        normalize_gui_order(roots)
        self.mark_changed("Reordered GUI elements")

    @property
    def available_component_types(self) -> tuple[str, ...]:
        record = self.selected
        if record is None:
            return ()
        components = record.get("components", [])
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        existing_names = {
            str(component.get("type"))
            for component in components
            if isinstance(component, dict)
        }
        supported = set(scene_types.editable_component_types)
        motor_types = {
            "g.top_down_motor",
            "g.platformer_motor",
        }
        if motor_types & existing_names:
            supported -= motor_types
        repeatable = REPEATABLE_COMPONENT_TYPES & supported
        return tuple(sorted((supported - existing_names) | repeatable))

    def object_component_warnings(
        self, record: dict[str, JsonValue]
    ) -> tuple[str, ...]:
        components = record.get("components", [])
        if not isinstance(components, list):
            return ("Object components must be an array",)
        warnings: list[str] = []
        present_types = []
        for component in components:
            if not isinstance(component, dict):
                warnings.append("Component record must be an object")
                continue
            try:
                present_types.append(
                    scene_types.component_type_for_name(
                        str(component.get("type"))
                    )
                )
            except SceneFormatError as error:
                warnings.append(str(error))
        types = tuple(present_types)
        for component_type in types:
            for requirement in missing_component_requirements(
                component_type, types, include_auto_add=False
            ):
                warnings.append(
                    f"{component_type.__name__} requires "
                    f"{describe_component_requirement(requirement)}"
                )
        return tuple(warnings)

    def sync_script_components(self) -> bool:
        """Match script component properties to their current fields."""
        changed = False
        for record in self.records:
            components = record.get("components")
            if not isinstance(components, list):
                continue
            for component in components:
                if not isinstance(component, dict):
                    continue
                name = str(component.get("type"))
                try:
                    codec = scene_types.component_codec_for_name(name)
                except SceneFormatError:
                    continue
                if codec.builtin or not issubclass(codec.value_type, Script):
                    continue
                properties = component.get("properties")
                if not isinstance(properties, dict):
                    continue
                allowed = {*codec.fields, "enabled"}
                for stale in set(properties) - allowed:
                    del properties[stale]
                    changed = True
                missing = [
                    field for field in codec.fields if field not in properties
                ]
                if missing:
                    template = scene_types.component_template(name)
                    defaults = template["properties"]
                    assert isinstance(defaults, dict)
                    for field in missing:
                        properties[field] = defaults.get(field)
                    changed = True
        if changed:
            self.mark_changed("Updated script properties")
        return changed

    def add_component(self, component_type: str) -> None:
        record = self.selected
        if record is None:
            raise ValueError("select a game object first")
        if component_type not in self.available_component_types:
            raise ValueError(
                f"component cannot be added: {component_type!r}"
            )
        components = record.get("components", [])
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        updated = deepcopy(components)
        self._append_component_with_requirements(
            updated,
            component_type,
            set(),
        )
        components[:] = updated
        record["components"] = components
        self.mark_changed(f"Added {component_type}")

    def paste_component(self, component: dict[str, JsonValue]) -> None:
        record = self.selected
        if record is None:
            raise ValueError("select a game object first")
        component_type = component.get("type")
        properties = component.get("properties")
        if not isinstance(component_type, str) or not isinstance(properties, dict):
            raise SceneFormatError("component clipboard needs a type and properties")
        paste_error = self.component_paste_error(component)
        if paste_error is not None:
            raise ValueError(paste_error)
        components = record.get("components", [])
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        updated = deepcopy(components)
        self._append_component_with_requirements(
            updated, component_type, set(), properties=properties,
        )
        pasted = updated[-1]
        assert isinstance(pasted, dict)
        record["components"] = updated
        for name, value in properties.items():
            self._sync_linked_geometry(record, pasted, name, value)
        self.mark_changed(f"Pasted {component_type}")

    def component_paste_error(self, component: dict[str, JsonValue]) -> str | None:
        record = self.selected
        if record is None:
            return "select a game object first"
        name = component.get("type")
        if not isinstance(name, str) or not isinstance(component.get("properties"), dict):
            return "component clipboard needs a type and properties"
        components = record.get("components", [])
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        names = {
            str(value.get("type")) for value in components if isinstance(value, dict)
        }
        if name in names and name not in REPEATABLE_COMPONENT_TYPES:
            return "component already exists on this game object"
        motors = {"g.top_down_motor", "g.platformer_motor"}
        if name in motors and names & motors:
            return "game object already has a movement motor"
        properties = component["properties"]
        assert isinstance(properties, dict)
        try:
            self._append_component_with_requirements(
                deepcopy(components), name, set(), properties=properties,
            )
        except (ValueError, SceneFormatError) as error:
            return str(error)
        return None

    def _append_component_with_requirements(
        self,
        components: list[JsonValue],
        component_type: str,
        resolving: set[str],
        *,
        properties: dict[str, JsonValue] | None = None,
    ) -> None:
        if component_type in resolving:
            raise ValueError(
                f"component dependency cycle involving {component_type}"
            )
        requested_directly = not resolving
        resolving.add(component_type)
        existing_names = {
            str(component.get("type"))
            for component in components
            if isinstance(component, dict)
        }
        existing_types = tuple(
            scene_types.component_type_for_name(name)
            for name in existing_names
        )
        if component_type in existing_names and not (
            requested_directly and component_type in REPEATABLE_COMPONENT_TYPES
        ):
            resolving.remove(component_type)
            return
        motors = {"g.top_down_motor", "g.platformer_motor"}
        if component_type in motors and existing_names & motors:
            raise ValueError("game object already has a movement motor")
        value_type = scene_types.component_type_for_name(component_type)
        for requirement in missing_component_requirements(
            value_type,
            existing_types,
        ):
            required_types = (
                requirement.component_types[:1]
                if requirement.any_of else requirement.component_types
            )
            for required_type in required_types:
                if any(
                    issubclass(existing, required_type)
                    for existing in existing_types
                ):
                    continue
                required_name = scene_types.component_name_for_type(
                    required_type
                )
                self._append_component_with_requirements(
                    components,
                    required_name,
                    resolving,
                )
                existing_types = tuple(
                    scene_types.component_type_for_name(str(component["type"]))
                    for component in components
                    if isinstance(component, dict)
                )
        components.append(
            {"type": component_type, "properties": deepcopy(properties)}
            if properties is not None else self._component_editor_template(
                component_type,
                components=components,
            )
        )
        if component_type == "g.area":
            added = components[-1]
            assert isinstance(added, dict)
            area_properties = added.get("properties")
            assert isinstance(area_properties, dict)
            _area_sensor(components, area_properties.get("collider_index", 0))["mode"] = "sensor"
        if component_type == "g.platformer_motor":
            for component in components:
                if not isinstance(component, dict):
                    continue
                if component.get("type") != "g.collider":
                    continue
                collider_properties = component.get("properties")
                assert isinstance(collider_properties, dict)
                if collider_properties.get("shape", "box") == "polygon":
                    continue
                collider_properties["friction"] = 0
        resolving.remove(component_type)

    def _component_editor_template(
        self,
        component_type: str,
        *,
        components: list[JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        template = scene_types.component_template(component_type)
        if component_type == "g.sprite_animator" and components is not None:
            image = next(
                (
                    properties.get("image")
                    for component in components
                    if isinstance(component, dict)
                    and component.get("type") == "g.sprite_renderer"
                    and isinstance(
                        properties := component.get("properties"),
                        dict,
                    )
                    and isinstance(properties.get("image"), str)
                ),
                None,
            )
            properties = template.get("properties")
            if isinstance(image, str) and isinstance(properties, dict):
                states = properties.get("states")
                if isinstance(states, list) and states:
                    state = states[0]
                    if isinstance(state, dict):
                        if "image" in state:
                            state["image"] = image
                        frames = state.get("frames")
                        if isinstance(frames, list) and frames:
                            frame = frames[0]
                            if isinstance(frame, dict):
                                frame["image"] = image
        return template

    def component_removal_error(
        self,
        component_index: int,
    ) -> str | None:
        record = self.selected
        if record is None:
            return "select a game object first"
        components = record.get("components")
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        try:
            components[component_index]
        except IndexError:
            raise SceneFormatError(
                "component index is out of range"
            ) from None
        remaining = [
            candidate
            for index, candidate in enumerate(components)
            if index != component_index
        ]
        colliders = _collider_records(components)
        selected_component = components[component_index]
        if selected_component in colliders:
            collider_index = colliders.index(selected_component)
            for candidate in remaining:
                if isinstance(candidate, dict) and candidate.get("type") == "g.area":
                    properties = candidate.get("properties")
                    if isinstance(properties, dict) and properties.get("collider_index", 0) == collider_index:
                        return "Selected sensor of Area; remove or retarget Area first"
        remaining_types = tuple(
            scene_types.component_type_for_name(
                str(candidate.get("type"))
            )
            for candidate in remaining
            if isinstance(candidate, dict)
        )
        for candidate_type in remaining_types:
            missing = missing_component_requirements(
                candidate_type,
                remaining_types,
            )
            if missing:
                return f"Required by {candidate_type.__name__}"
        return None

    def remove_component(self, component_index: int) -> None:
        record = self.selected
        if record is None:
            raise ValueError("select a game object first")
        components = record.get("components")
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        try:
            component = components[component_index]
        except IndexError:
            raise SceneFormatError(
                "component index is out of range"
            ) from None
        component_type = (
            str(component.get("type", "component"))
            if isinstance(component, dict)
            else "component"
        )
        removal_error = self.component_removal_error(component_index)
        if removal_error is not None:
            raise ValueError(
                f"cannot remove {component_type}; {removal_error}"
            )
        colliders = _collider_records(components)
        removed_collider_index = colliders.index(component) if component in colliders else None
        components.pop(component_index)
        if removed_collider_index is not None:
            for candidate in components:
                if isinstance(candidate, dict) and candidate.get("type") == "g.area":
                    properties = candidate.get("properties")
                    if isinstance(properties, dict):
                        index = properties.get("collider_index", 0)
                        if isinstance(index, int) and index > removed_collider_index:
                            properties["collider_index"] = index - 1
        self.mark_changed(f"Removed {component_type}")

    def reset_component(self, component_index: int) -> None:
        record, component, component_type = (
            self._selected_component(component_index)
        )
        components = record.get("components")
        assert isinstance(components, list)
        template = self._component_editor_template(
            component_type,
            components=components,
        )
        properties = template.get("properties")
        assert isinstance(properties, dict)
        properties = _component_area_properties(components, component, properties)
        component["properties"] = properties
        for name, value in properties.items():
            self._sync_linked_geometry(record, component, name, value)
        self.mark_changed(f"Reset {component_type}")

    def replace_component_properties(
        self,
        component_index: int,
        component_type: str,
        properties: dict[str, JsonValue],
    ) -> None:
        record, component, selected_type = (
            self._selected_component(component_index)
        )
        if selected_type != component_type:
            raise ValueError("component clipboard type does not match")
        components = record.get("components")
        assert isinstance(components, list)
        properties = _component_area_properties(components, component, properties)
        component["properties"] = properties
        for name, value in properties.items():
            self._sync_linked_geometry(record, component, name, value)
        self.mark_changed(f"Pasted {component_type} values")

    def _selected_component(
        self,
        component_index: int,
    ) -> tuple[
        dict[str, JsonValue],
        dict[str, JsonValue],
        str,
    ]:
        record = self.selected
        if record is None:
            raise ValueError("select a game object first")
        components = record.get("components")
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        try:
            component = components[component_index]
        except IndexError:
            raise SceneFormatError(
                "component index is out of range"
            ) from None
        if not isinstance(component, dict):
            raise SceneFormatError("component must be an object")
        component_type = component.get("type")
        if not isinstance(component_type, str):
            raise SceneFormatError("component type must be a string")
        return record, component, component_type

    def delete_selected(self) -> bool:
        layer = self.selected_layer
        if layer is not None and layer.get("type") == "tile":
            groups = self.selected_tile_groups
            selected = set(self.selected_ids) if self.mixed_layer_selection else set()
            if not any(groups.values()) and not selected:
                return False
            working = []
            for selected_layer in self.selected_layers:
                clone = deepcopy(selected_layer)
                if selected_layer.get("type") == "tile":
                    for p in groups.get(str(selected_layer["id"]), {}):
                        self._set_tile_cell(clone, *p, None)
                elif selected_layer.get("type", "object") == "object":
                    def remove_mixed_objects(records: list[dict[str, JsonValue]]) -> None:
                        records[:] = [record for record in records if record.get("id") not in selected]
                        for record in records:
                            children = record.get("children")
                            if isinstance(children, list):
                                remove_mixed_objects(cast(list[dict[str, JsonValue]], children))
                    remove_mixed_objects(layer_objects(clone))
                working.append((selected_layer, clone))
            self._history_current = self._capture_snapshot()
            for selected_layer, clone in working:
                selected_layer.clear()
                selected_layer.update(clone)
            self.clear_tile_selection()
            self.selected_id = None
            self.mark_changed("Delete selection" if self.mixed_layer_selection else "Delete tiles")
            return True
        gui_targets = self.selected_gui_elements
        if gui_targets:
            layer = self.selected_layer
            if layer is None:
                return False
            target_ids = {
                identifier
                for target in gui_targets
                if isinstance(
                    identifier := target.get("id"),
                    str,
                )
            }

            def remove_gui(
                records: list[dict[str, JsonValue]],
            ) -> bool:
                changed = False
                kept: list[dict[str, JsonValue]] = []
                for candidate in records:
                    identifier = candidate.get("id")
                    if identifier in target_ids:
                        changed = True
                        continue
                    children = candidate.get("children")
                    if isinstance(children, list):
                        typed_children = cast(
                            list[dict[str, JsonValue]],
                            children,
                        )
                        if remove_gui(typed_children):
                            changed = True
                    kept.append(candidate)
                if changed:
                    records[:] = kept
                return changed

            if not remove_gui(layer_gui_elements(layer)):
                return False
            self.selected_id = None
            normalize_gui_order(layer_gui_elements(layer))
            self.mark_changed("Deleted GUI elements")
            return True
        targets = self.selected_records
        if not targets:
            return False
        target_ids = {
            identifier
            for target in targets
            if isinstance(identifier := target.get("id"), str)
        }

        def remove(records: list[dict[str, JsonValue]]) -> bool:
            changed = False
            kept: list[dict[str, JsonValue]] = []
            for candidate in records:
                if candidate.get("id") in target_ids:
                    changed = True
                    continue
                children = candidate.get("children")
                if isinstance(children, list):
                    typed_children = cast(
                        list[dict[str, JsonValue]],
                        children,
                    )
                    if remove(typed_children):
                        changed = True
                kept.append(candidate)
            if changed:
                records[:] = kept
            return changed

        if not any(
            remove(layer_objects(layer))
            for layer in self.document.layers
        ):
            return False
        self.selected_id = None
        self._normalize_hierarchy_order()
        self.mark_changed("Deleted objects")
        return True

    def set_object_value(self, key: str, value: JsonValue) -> None:
        record = self.selected
        if record is None:
            return
        if key in {"name", "enabled", "visible"}:
            record[key] = value
        elif key == "render_behind_parent":
            if not isinstance(value, bool):
                raise ValueError("render_behind_parent must be a boolean")
            record[key] = value
            self._normalize_hierarchy_order()
        elif key in {"x", "y"}:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{key} must be numeric")
            x, y = position(record)
            self.move_selected(
                float(value) if key == "x" else x,
                float(value) if key == "y" else y,
            )
            return
        elif key in {"rotation", "scale_x", "scale_y"}:
            transform = record.setdefault("transform", {})
            if not isinstance(transform, dict):
                raise SceneFormatError("object transform must be an object")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{key} must be numeric")
            if key == "rotation":
                transform["rotation"] = value
            else:
                scale = transform.setdefault(
                    "scale",
                    cast(list[JsonValue], [1, 1]),
                )
                if not isinstance(scale, list) or len(scale) != 2:
                    scale = cast(list[JsonValue], [1, 1])
                    transform["scale"] = scale
                scale[0 if key == "scale_x" else 1] = value
        else:
            raise KeyError(key)
        self.mark_changed(f"Changed {key}")

    def set_object_properties(self, value: JsonValue) -> None:
        record = self.selected
        if record is None:
            return
        if not isinstance(value, dict):
            raise ValueError("object properties must be an object")
        record["properties"] = value
        self.mark_changed("Changed object properties")

    def set_object_script_properties(self, value: JsonValue) -> None:
        record = self.selected
        if record is None:
            return
        if not isinstance(value, dict):
            raise ValueError("game object properties must be an object")
        record["script_properties"] = value
        self.mark_changed("Changed game object properties")

    def set_component_value(
        self, component_index: int, name: str, value: JsonValue
    ) -> None:
        record = self.selected
        if record is None:
            return
        components = record.get("components")
        if not isinstance(components, list):
            raise SceneFormatError("object components must be an array")
        try:
            component = components[component_index]
        except IndexError:
            raise SceneFormatError("component index is out of range") from None
        if not isinstance(component, dict):
            raise SceneFormatError("component must be an object")
        properties = component.get("properties")
        if not isinstance(properties, dict):
            raise SceneFormatError("component properties must be an object")
        value_type = scene_types.component_type_for_name(str(component["type"]))
        metadata = inspector_fields(value_type).get(name)
        if metadata is not None:
            validate_field_value(metadata, value)
        if component.get("type") == "g.spline" and name in {"points", "closed", "tolerance"}:
            self.set_spline_geometry(component_index, {name: value})
            return
        if component.get("type") == "g.area" and name == "collider_index":
            _area_sensor(components, value)["mode"] = "sensor"
        if component.get("type") == "g.collider" and name == "shape":
            if value not in COLLIDER_SHAPES:
                raise ValueError(f"Unknown collider shape: {value!r}")
            validate_geometry(str(value), {**properties, "shape": value})
        if isinstance(value, str) and name == "mode" and value != "sensor" and component in _collider_records(components):
            collider_index = _collider_records(components).index(component)
            if any(
                isinstance(candidate, dict) and candidate.get("type") == "g.area"
                and isinstance(area_properties := candidate.get("properties"), dict)
                and area_properties.get("collider_index", 0) == collider_index
                for candidate in components
            ):
                raise ValueError("Area's selected collider must remain a sensor")
        properties[name] = value
        self._sync_linked_geometry(record, component, name, value)
        self.mark_changed(f"Changed {name}")

    def set_spline_geometry(
        self, component_index: int, values: Mapping[str, JsonValue],
    ) -> None:
        _, component, kind = self._selected_component(component_index)
        if kind != "g.spline":
            raise ValueError("Component is not a spline")
        if any(name not in {"points", "closed", "tolerance"} for name in values):
            raise ValueError("Unsupported spline geometry field")
        properties = component.get("properties")
        if not isinstance(properties, dict):
            raise SceneFormatError("Spline properties must be an object")
        defaults = scene_types.component_template("g.spline")["properties"]
        assert isinstance(defaults, dict)
        candidate = {**defaults, **properties, **values}
        raw_points = candidate["points"]
        if not isinstance(raw_points, list):
            raise ValueError("Spline points must be a list of coordinate pairs")
        points: list[tuple[float, float]] = []
        for point in raw_points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("Spline points must contain coordinate pairs")
            x, y = point
            if (
                isinstance(x, bool) or not isinstance(x, (int, float))
                or isinstance(y, bool) or not isinstance(y, (int, float))
                or not isfinite(x) or not isfinite(y)
            ):
                raise ValueError("Spline points must contain finite numbers")
            points.append((float(x), float(y)))
        closed, tolerance = candidate["closed"], candidate["tolerance"]
        if not isinstance(closed, bool):
            raise ValueError("Spline closed must be a boolean")
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
            raise ValueError("Spline tolerance must be a finite positive number")
        Spline(
            points=points, closed=closed, tolerance=tolerance,
        )
        if all(properties.get(name) == value for name, value in values.items()):
            return
        properties.update(deepcopy(dict(values)))
        self.mark_changed("Edited spline geometry")

    def set_collider_geometry(
        self, component_index: int, values: Mapping[str, JsonValue],
    ) -> None:
        _, component, kind = self._selected_component(component_index)
        if kind != "g.collider":
            raise ValueError("Component is not an editable collider")
        properties = component.get("properties")
        if not isinstance(properties, dict):
            raise SceneFormatError("Collider properties must be an object")
        shape = collider_shape(properties)
        if any(name not in GEOMETRY_FIELDS[shape] for name in values):
            raise ValueError("Unsupported collider geometry field")
        candidate = {**properties, **values}
        validate_geometry(shape, candidate)
        properties.update(deepcopy(dict(values)))
        self.mark_changed("Edited collider geometry")

    def set_collider_shape(self, component_index: int, shape: str) -> None:
        """Switch a collider's shape in place, retaining every other property."""
        _, component, kind = self._selected_component(component_index)
        if kind != "g.collider":
            raise ValueError("Component is not a collider")
        if shape not in COLLIDER_SHAPES:
            raise ValueError(f"Unknown collider shape: {shape!r}")
        properties = component.get("properties")
        if not isinstance(properties, dict):
            raise SceneFormatError("Collider properties must be an object")
        if properties.get("shape", "box") == shape:
            return
        validate_geometry(shape, {**properties, "shape": shape})
        properties["shape"] = shape
        self.mark_changed(f"Set collider shape to {shape}")

    def set_sprite_animator_configuration(
        self,
        component_index: int,
        states: JsonValue,
        *,
        initial_state: str | None = None,
        autoplay: bool | None = None,
        history_label: str = "Changed sprite animations",
    ) -> None:
        _, component, component_type = self._selected_component(
            component_index
        )
        if component_type != "g.sprite_animator":
            raise ValueError("component is not a sprite animator")
        if not isinstance(states, list) or not states:
            raise ValueError(
                "sprite animator must contain at least one state"
            )
        names: set[str] = set()
        first_name: str | None = None
        for state in states:
            if not isinstance(state, dict):
                raise ValueError(
                    "sprite animation state must be an object"
                )
            name = state.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    "sprite animation state name must not be empty"
                )
            if name in names:
                raise ValueError(
                    f"sprite animation state already exists: {name!r}"
                )
            names.add(name)
            if first_name is None:
                first_name = name
            if not isinstance(state.get("loop", True), bool):
                raise ValueError(
                    "sprite animation loop must be true or false"
                )
            if "image" in state:
                if "frames" in state:
                    raise ValueError("sprite state must use either an image asset or frames")
                if not isinstance(state.get("animated", False), bool):
                    raise ValueError("sprite state animated must be true or false")
                durations = state.get("durations")
                if durations is not None:
                    if not state.get("animated", False) or not state.get("image") or not isinstance(durations, list) or not durations:
                        raise ValueError("duration overrides require an animation asset")
                    if any(
                        not isinstance(duration, (int, float)) or isinstance(duration, bool)
                        or not isfinite(duration) or duration <= 0
                        for duration in durations
                    ):
                        raise ValueError("sprite animation frame duration must be positive")
                image = state["image"]
                if image is not None and (not isinstance(image, str) or not image):
                    raise ValueError("sprite state image must be an asset path or null")
                region = state.get("region")
                if region is not None and (
                    state.get("animated", False)
                    or image is None
                    or not isinstance(region, list)
                    or len(region) != 4
                    or any(
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        for value in region
                    )
                ):
                    raise ValueError(
                        "sprite state region must contain four integers "
                        "for a static image"
                    )
                continue
            frames = state.get("frames")
            if not isinstance(frames, list) or not frames:
                raise ValueError(
                    f"sprite animation state {name!r} must contain frames"
                )
            for frame in frames:
                if not isinstance(frame, dict):
                    raise ValueError(
                        "sprite animation frame must be an object"
                    )
                image = frame.get("image")
                region = frame.get("region")
                duration = frame.get("duration")
                if "image" not in frame or (image is not None and (not isinstance(image, str) or not image)):
                    raise ValueError(
                        "sprite animation frame image must not be empty"
                    )
                if region is not None and (
                    image is None
                    or not isinstance(region, list)
                    or len(region) != 4
                    or any(
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        for value in region
                    )
                ):
                    raise ValueError(
                        "sprite animation frame region must contain "
                        "four integers and an image"
                    )
                if (
                    not isinstance(duration, (int, float))
                    or isinstance(duration, bool)
                    or not isfinite(duration)
                    or duration <= 0
                ):
                    raise ValueError(
                        "sprite animation frame duration must be positive"
                    )
        properties = component.get("properties")
        if not isinstance(properties, dict):
            raise SceneFormatError(
                "component properties must be an object"
            )
        resolved_initial = (
            initial_state
            if initial_state is not None
            else properties.get("initial_state")
        )
        if not isinstance(resolved_initial, str):
            assert first_name is not None
            resolved_initial = first_name
        if resolved_initial not in names:
            raise ValueError(
                "sprite animator initial state must exist"
            )
        properties["states"] = deepcopy(states)
        properties["initial_state"] = resolved_initial
        if autoplay is not None:
            properties["autoplay"] = autoplay
        self.mark_changed(history_label)

    def set_sprite_animation_frame_image(
        self,
        component_index: int,
        state_index: int,
        frame_index: int,
        image: str | Path | None,
    ) -> None:
        relative = self._load_project_image(image)[0] if image is not None else None
        _, component, component_type = self._selected_component(
            component_index
        )
        if component_type != "g.sprite_animator":
            raise ValueError("component is not a sprite animator")
        properties = component.get("properties")
        if not isinstance(properties, dict):
            raise SceneFormatError(
                "component properties must be an object"
            )
        states = properties.get("states")
        if not isinstance(states, list):
            raise SceneFormatError(
                "sprite animator states must be an array"
            )
        try:
            state = states[state_index]
        except IndexError:
            raise ValueError(
                "sprite animation state is unavailable"
            ) from None
        if not isinstance(state, dict):
            raise SceneFormatError(
                "sprite animation state must be an object"
            )
        if frame_index == -1:
            if "image" not in state:
                raise ValueError("sprite state does not use an image asset")
            state["image"] = relative
            state.pop("durations", None)
            self.mark_changed("Changed sprite animation state asset")
            return
        frames = state.get("frames")
        if not isinstance(frames, list):
            raise SceneFormatError(
                "sprite animation frames must be an array"
            )
        try:
            frame = frames[frame_index]
        except IndexError:
            raise ValueError(
                "sprite animation frame is unavailable"
            ) from None
        if not isinstance(frame, dict):
            raise SceneFormatError(
                "sprite animation frame must be an object"
            )
        frame["image"] = relative
        frame.pop("region", None)
        self.mark_changed("Changed sprite animation frame image")

    def new(self) -> None:
        self._selected_layer_ids.clear()
        self.clear_tile_selection()
        self.document = new_scene_document()
        self.filename = None
        self.selected_id = None
        layer = default_layer(self.document)
        identifier = layer.get("id")
        self.selected_layer_id = (
            identifier if isinstance(identifier, str) else None
        )
        self.active_tileset_ids.clear()
        self.palette_tile_ids = [0]
        self.palette_selection_size = (1, 1)
        self._sampled_tile_cells = None
        self._sampled_tile_size = None
        self._reset_history()
        self.dirty = False
        self.revision += 1
        self.status = "New scene"
        self.error = None

    def open(self, filename: str | Path) -> None:
        self._selected_layer_ids.clear()
        path = Path(filename).expanduser().resolve()
        document = SceneDocument.read(path)
        self.clear_tile_selection()
        self.document = document
        self._normalize_scene_settings()
        self._normalize_hierarchy_order()
        self.filename = path
        self.asset_root = path.parent
        self.selected_id = None
        layer = default_layer(self.document)
        identifier = layer.get("id")
        self.selected_layer_id = (
            identifier if isinstance(identifier, str) else None
        )
        self.active_tileset_ids.clear()
        self.palette_tile_ids = [0]
        self.palette_selection_size = (1, 1)
        self._sampled_tile_cells = None
        self._sampled_tile_size = None
        self._reset_history()
        self.error = None
        self.synchronize_prefabs()
        self._reset_history()
        self.dirty = False
        self.revision += 1
        self.status = f"Opened {path.name}"

    def save(self, filename: str | Path | None = None) -> Path:
        if filename is not None:
            self.filename = Path(filename).expanduser().resolve()
        if self.filename is None:
            raise SceneFormatError("choose a filename before saving")
        self.document.write(self.filename)
        self._saved_document = deepcopy(self.document)
        self.dirty = False
        self.status = f"Saved {self.filename.name}"
        self.error = None
        return self.filename

    @staticmethod
    def parse_property(text: str, previous: object) -> JsonValue:
        if isinstance(previous, bool):
            normalized = text.strip().lower()
            if normalized not in {"true", "false"}:
                raise ValueError("enter true or false")
            return normalized == "true"
        if isinstance(previous, int):
            return int(text)
        if isinstance(previous, float):
            return float(text)
        if previous is None or isinstance(previous, (list, dict)):
            value = json.loads(text)
            if value is None or isinstance(
                value, (bool, int, float, str, list, dict)
            ):
                return cast(JsonValue, value)
            raise ValueError("value is not JSON-compatible")
        return text

    @staticmethod
    def _sync_linked_geometry(
        record: dict[str, JsonValue],
        source: Mapping[str, object],
        property_name: str,
        value: JsonValue,
    ) -> None:
        source_type = source.get("type")
        target_shape: str | None = None
        if (
            source_type == "g.rect_renderer"
            and property_name in {"width", "height"}
        ):
            target_shape = "box"
        elif (
            source_type == "g.circle_renderer"
            and property_name == "radius"
        ):
            target_shape = "circle"
        if target_shape is None:
            return
        components = record.get("components")
        if not isinstance(components, list):
            return
        for component in components:
            if (
                isinstance(component, dict)
                and component.get("type") == "g.collider"
            ):
                properties = component.get("properties")
                if (
                    isinstance(properties, dict)
                    and properties.get("shape", "box") == target_shape
                ):
                    properties[property_name] = value
