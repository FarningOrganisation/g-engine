from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pyglet
from g.persistence import JsonValue
from imgui_bundle import imgui

from ..panel_support import AssetPickerTarget, RenameTarget, TileCollisionDrag
from ..state import EditorState
from g.project import ProjectConfig
from g import Collider

if TYPE_CHECKING:
    from ..application import EnginePreview, SceneRenderTarget, _ColliderDrag


class EditorPanel:
    state: EditorState
    project: ProjectConfig | None
    preview: EnginePreview
    render_target: SceneRenderTarget
    _asset_directories: tuple[Path, ...]
    _asset_drag_path: Path | None
    _asset_drag_paths: tuple[Path, ...]
    _selected_assets: set[Path]
    _asset_selection_anchor: Path | None
    _asset_field_button_rects: dict[
        str,
        tuple[float, float, float, float],
    ]
    _asset_field_targets: dict[str, AssetPickerTarget]
    _asset_files: tuple[Path, ...]
    _asset_scan: tuple[tuple[Path, ...], tuple[Path, ...]] | None
    _pending_asset_delete: tuple[Path, tuple[str, ...]] | None
    _asset_item_rects: dict[
        Path,
        tuple[float, float, float, float],
    ]
    _asset_clipboard: Path | None
    _component_clipboard: dict[str, JsonValue] | None
    _component_open: dict[str, bool]
    _asset_trash_root: Path
    _asset_picker: AssetPickerTarget | None
    _asset_picker_keep_alive: bool
    _assets_rect: tuple[float, float, float, float]
    _atlas_textures: dict[Path, tuple[int, pyglet.image.Texture]]
    _hierarchy_drag_identifier: str | None
    _hierarchy_visible_orders: dict[str, list[str]]
    _hierarchy_keyboard_selection: str | None
    _rename_target: RenameTarget | None
    _json_buffers: dict[str, str]
    _optional_color_values: dict[str, list[int]]
    _layer_content_rect: tuple[float, float, float, float]
    _palette_drag_start: int | None
    _palette_zoom: float
    _shift_pick_held: bool
    _path_buffer: str
    _pending_tileset_delete: str | None
    _popup_to_open: str | None
    _new_script_name: str
    _new_script_focus_pending: bool

    def _register_project_script(self, path: Path) -> None:
        raise NotImplementedError

    def _unregister_project_script(self, path: Path) -> None:
        raise NotImplementedError

    def _begin_create_python_script(
        self, *, attach_to_selected: bool = False
    ) -> None:
        raise NotImplementedError

    _project_trust_pending: bool

    def _reload_changed_scripts(self) -> None:
        raise NotImplementedError

    def _attach_project_script(
        self,
        path: Path,
        object_identifier: str | None = None,
    ) -> None:
        raise NotImplementedError
    _selected_asset: Path | None
    _selected_asset_directory: Path
    _shortcut_context: str | None
    _tile_config_buffers: dict[str, tuple[int, int, int, int]]
    _tile_collision_drag: TileCollisionDrag | None
    _tile_collision_target: tuple[str | None, int, str] | None
    _tile_collision_point: tuple[int, int] | None
    _tile_collision_add: bool
    _transform_space: str
    _transform_tool: str
    _viewport_tool: str
    _collider_component_index: int | None
    _collider_point: int | None
    _collider_drag: _ColliderDrag | None

    def _cancel_collider_drag(self) -> None:
        raise NotImplementedError

    def _editable_colliders(self) -> list[tuple[int, Collider]]:
        raise NotImplementedError

    def _activate_component_tool(self, name: str, component_index: int | None = None) -> None:
        raise NotImplementedError

    def _component_tool_is_active(self, name: str, index: int) -> bool:
        raise NotImplementedError

    def _component_tool_for_type(self, component_type: str) -> str | None:
        raise NotImplementedError

    def _component_tool_help(self) -> str:
        raise NotImplementedError

    def _begin_panel(
        self,
        title: str,
        *,
        title_bar: bool = True,
    ) -> bool:
        raise NotImplementedError

    def _begin_rename(
        self,
        kind: str,
        identifier: str,
        current_name: str,
    ) -> None:
        raise NotImplementedError

    def _is_renaming(self, kind: str, identifier: str) -> bool:
        raise NotImplementedError

    def _draw_inline_rename(
        self,
        kind: str,
        identifier: str,
        current_name: str,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        raise NotImplementedError

    def _request_action(self, action: str) -> None:
        raise NotImplementedError

    def _copy_selection(self) -> bool:
        raise NotImplementedError

    def _cut_selection(self) -> None:
        raise NotImplementedError

    def _paste_selection(self) -> None:
        raise NotImplementedError

    def _duplicate_selection(self) -> None:
        raise NotImplementedError

    def _start_selected_rename(self) -> bool:
        raise NotImplementedError

    def _apply_selected_prefab(self) -> None:
        raise NotImplementedError

    def _add_sprite_from_selected_asset(self) -> None:
        raise NotImplementedError

    def _add_empty_sprite(self) -> None:
        raise NotImplementedError

    def _atlas_texture(
        self,
        image_path: str,
    ) -> pyglet.image.Texture:
        raise NotImplementedError

    def _draw_layer_inspector(
        self,
        layer: dict[str, JsonValue],
        *,
        show_order_controls: bool = True,
        show_primary_fields: bool = True,
    ) -> None:
        raise NotImplementedError

    def _draw_tile_layer_inspector(
        self,
        layer: dict[str, JsonValue],
    ) -> None:
        raise NotImplementedError

    def _draw_asset_reference_field(
        self,
        label: str,
        current: str,
        field_id: str,
        *,
        kind: str,
    ) -> None:
        raise NotImplementedError

    def _toggle_asset_picker(
        self,
        target: AssetPickerTarget,
    ) -> None:
        raise NotImplementedError

    def _show_animation_panel(self) -> None:
        raise NotImplementedError

    def _show_particles_panel(self) -> None:
        raise NotImplementedError

    def _open_particle_asset(self, path: Path) -> None:
        raise NotImplementedError

    def _set_particle_image(self, path: str | None) -> None:
        raise NotImplementedError

    def _create_particle_asset_from_preset(
        self,
        preset: str,
    ) -> Path:
        raise NotImplementedError

    def _effective_transform_space(self) -> str:
        raise NotImplementedError

    @staticmethod
    def _bool_property(
        label: str,
        value: bool,
        identifier: str | None = None,
    ) -> tuple[bool, bool]:
        raise NotImplementedError

    @staticmethod
    def _int_property(
        label: str,
        value: int,
        identifier: str | None = None,
        *,
        minimum: int = 0,
        maximum: int = 0,
    ) -> tuple[bool, int]:
        raise NotImplementedError

    @staticmethod
    def _choice_property(
        label: str,
        index: int,
        values: list[str],
        identifier: str | None = None,
    ) -> tuple[bool, int]:
        raise NotImplementedError

    @staticmethod
    def _slider_property(
        label: str,
        value: float,
        minimum: float,
        maximum: float,
        identifier: str | None = None,
    ) -> tuple[bool, float]:
        raise NotImplementedError

    @staticmethod
    def _color_widget(
        label: str,
        value: JsonValue,
    ) -> tuple[bool, JsonValue]:
        raise NotImplementedError
