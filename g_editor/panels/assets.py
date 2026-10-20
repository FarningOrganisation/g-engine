from __future__ import annotations

import keyword
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile

from g.persistence import SceneFormatError
from g.project import ProjectConfig, ProjectFormatError
from g.resources import Font
from imgui_bundle import imgui

from ..asset_references import find_asset_references
from ..panel_support import (
    ASSET_DRAG_PAYLOAD as _ASSET_DRAG_PAYLOAD,
    HIERARCHY_DRAG_PAYLOAD as _HIERARCHY_DRAG_PAYLOAD,
    IMAGE_SUFFIXES as _IMAGE_SUFFIXES,
    AssetPickerTarget as _AssetPickerTarget,
)
from ..prefab import PREFAB_SUFFIX, PrefabDocument
from ..state import EditorSnapshot, new_scene_document
from ..ui import (
    AssetField,
    color as _color,
    flag as _flag,
)
from .base import EditorPanel
from g.particles import ParticleEffectDefinition, particle_preset


class AssetsPanel(EditorPanel):
    def _draw_assets(self) -> None:
        if self._begin_panel("Assets"):
            self._asset_item_rects.clear()
            if imgui.is_window_focused():
                self._shortcut_context = "assets"
            window_position = imgui.get_window_pos()
            window_size = imgui.get_window_size()
            self._assets_rect = (
                window_position.x,
                window_position.y,
                window_position.x + window_size.x,
                window_position.y + window_size.y,
            )
            if self._asset_panel_clicked():
                self._asset_picker_keep_alive = True
            picker = self._asset_picker
            if picker is not None:
                imgui.text_colored(
                    imgui.ImVec4(0.25, 0.78, 1.0, 1.0),
                    f"Select {picker.label}  -  Esc to cancel",
                )
                if self._asset_field_clearable(picker.field_id):
                    imgui.same_line()
                    if imgui.button("None##asset-picker-none"):
                        try:
                            self._clear_asset(picker)
                        except (ValueError, SceneFormatError) as error:
                            self.state.error = str(error)
            else:
                imgui.text_disabled(str(self.state.asset_root))
            imgui.same_line()
            if imgui.button("+##asset-actions"):
                imgui.open_popup("Asset Actions")
            if imgui.begin_popup("Asset Actions"):
                if imgui.menu_item("New Folder", "", False)[0]:
                    try:
                        directory = self._create_asset_folder()
                        self._begin_rename(
                            "asset",
                            str(directory),
                            directory.name,
                        )
                    except (OSError, ValueError) as error:
                        self.state.error = str(error)
                if imgui.menu_item("New Python Script", "", False)[0]:
                    self._begin_create_python_script()
                if imgui.menu_item("New Scene", "", False)[0]:
                    self._create_asset_scene()
                if imgui.menu_item("New Particle Effect", "", False)[0]:
                    self._create_particle_asset()
                if imgui.menu_item(
                    "Paste",
                    "Cmd/Ctrl+V",
                    False,
                    self._asset_clipboard is not None,
                )[0]:
                    self._paste_asset(self._selected_asset_directory)
                if imgui.menu_item("Refresh", "", False)[0]:
                    self._refresh_assets()
                imgui.end_popup()
            imgui.separator()
            available = imgui.get_content_region_avail()
            preview_width = min(
                260.0,
                max(150.0, available.x * 0.36),
            )
            browser_width = max(
                120.0,
                available.x
                - preview_width
                - imgui.get_style().item_spacing.x,
            )
            if imgui.begin_child(
                "##asset-tree",
                imgui.ImVec2(browser_width, available.y),
            ):
                self._draw_asset_directory_contents(self.state.asset_root)
                available_space = imgui.get_content_region_avail()
                imgui.invisible_button(
                    "##asset-empty-space",
                    imgui.ImVec2(
                        max(1.0, available_space.x),
                        max(1.0, available_space.y),
                    ),
                )
                self._draw_asset_context_menu(
                    None,
                    empty_directory=self.state.asset_root,
                )
                self._accept_prefab_hierarchy_drop(self.state.asset_root)
            imgui.end_child()
            imgui.same_line()
            if imgui.begin_child(
                "##asset-preview",
                imgui.ImVec2(0, available.y),
                _flag(imgui.ChildFlags_.borders),
            ):
                self._draw_asset_preview()
            imgui.end_child()
            if picker is not None:
                draw_list = imgui.get_window_draw_list()
                draw_list.add_rect(
                    imgui.ImVec2(
                        self._assets_rect[0] + 1,
                        self._assets_rect[1] + 1,
                    ),
                    imgui.ImVec2(
                        self._assets_rect[2] - 1,
                        self._assets_rect[3] - 1,
                    ),
                    _color(60, 196, 255),
                    thickness=2.5,
                )
        imgui.end()

    @staticmethod
    def _asset_panel_clicked() -> bool:
        return imgui.is_window_hovered(
            _flag(imgui.HoveredFlags_.child_windows)
        ) and imgui.is_mouse_clicked(
            _flag(imgui.MouseButton_.left)
        )

    def _draw_asset_directory(
        self,
        directory: Path,
        label: str,
        *,
        default_open: bool = False,
    ) -> None:
        flags = (
            _flag(imgui.TreeNodeFlags_.open_on_arrow)
            | _flag(imgui.TreeNodeFlags_.span_avail_width)
        )
        if directory == self._selected_asset_directory:
            flags |= _flag(imgui.TreeNodeFlags_.selected)
        if default_open:
            imgui.set_next_item_open(
                True,
                _flag(imgui.Cond_.once),
            )
        opened = imgui.tree_node_ex(
            f"{label}##asset-dir-{directory}",
            flags,
        )
        item_minimum = imgui.get_item_rect_min()
        item_maximum = imgui.get_item_rect_max()
        if imgui.is_item_clicked() and not imgui.is_item_toggled_open():
            self._selected_asset_directory = directory
            self._selected_asset = None
        self._draw_asset_context_menu(directory)
        self._accept_prefab_hierarchy_drop(directory)
        if directory != self.state.asset_root:
            self._draw_inline_rename(
                "asset",
                str(directory),
                directory.name,
                item_minimum,
                item_maximum,
            )
        if not opened:
            return
        self._draw_asset_directory_contents(directory)
        imgui.tree_pop()

    def _draw_asset_directory_contents(self, directory: Path) -> None:
        for child in self._asset_directories:
            if child.parent == directory:
                self._draw_asset_directory(child, child.name)
        for path in self._asset_files:
            if path.parent == directory:
                self._draw_asset_file(path)

    def _draw_asset_file(self, path: Path) -> None:
        picker = self._asset_picker
        compatible = (
            picker is None
            or self._asset_matches_kind(path, picker.kind)
        )
        imgui.begin_disabled(not compatible)
        clicked, _ = imgui.selectable(
            f"{path.name}##asset-{path}",
            path in self._selected_assets or path == self._selected_asset,
            _flag(imgui.SelectableFlags_.allow_double_click),
        )
        item_minimum = imgui.get_item_rect_min()
        item_maximum = imgui.get_item_rect_max()
        self._asset_item_rects[path] = (
            item_minimum.x,
            item_minimum.y,
            item_maximum.x,
            item_maximum.y,
        )
        if clicked:
            io = imgui.get_io()
            self._select_asset(path, additive=io.key_ctrl or io.key_super, range_select=io.key_shift)
            if picker is not None:
                try:
                    self._assign_asset_picker(path)
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
            elif imgui.is_mouse_double_clicked(
                _flag(imgui.MouseButton_.left)
            ):
                if self._is_scene_asset(path) or self._is_particle_asset(path):
                    self._open_asset(path)
                elif (
                    path.suffix.lower() in _IMAGE_SUFFIXES
                    and self.state.selected_layer is not None
                    and self.state.selected_layer.get(
                        "type",
                        "object",
                    )
                    == "object"
                ):
                    self._add_sprite_from_selected_asset()
        renaming = self._is_renaming("asset", str(path))
        self._draw_inline_rename(
            "asset",
            str(path),
            path.name,
            item_minimum,
            item_maximum,
        )
        if compatible and not renaming:
            self._draw_asset_drag_source(path)
        imgui.end_disabled()
        if not compatible and imgui.is_item_hovered(
            _flag(imgui.HoveredFlags_.allow_when_disabled)
        ):
            imgui.set_tooltip(
                f"This field requires {picker.label}"
                if picker is not None
                else "Incompatible asset"
            )
        self._draw_asset_context_menu(path)
        if path.name.endswith(PREFAB_SUFFIX):
            self._accept_prefab_hierarchy_drop(path)

    def _draw_asset_context_menu(
        self,
        path: Path | None,
        *,
        empty_directory: Path | None = None,
    ) -> None:
        popup_id = (
            "empty"
            if path is None
            else str(path)
        )
        if not imgui.begin_popup_context_item(
            f"##asset-context-{popup_id}"
        ):
            return
        self._shortcut_context = "assets"
        target_directory = (
            path
            if path is not None and path.is_dir()
            else path.parent
            if path is not None
            else empty_directory or self._selected_asset_directory
        )
        if path is None or path.is_dir():
            self._selected_asset = None
            self._selected_asset_directory = target_directory
        else:
            self._selected_asset = path
            self._selected_asset_directory = path.parent
        if path is not None and path.is_file():
            action_label = (
                "Load Scene"
                if self._is_scene_asset(path)
                else "Open"
            )
            if imgui.menu_item(action_label, "", False)[0]:
                self._open_asset(path)
            if path.suffix.lower() == ".py":
                project = self.project
                try:
                    module = (
                        project.module_for_path(path)
                        if project is not None
                        else None
                    )
                except ProjectFormatError:
                    module = None
                registered = (
                    module in project.script_modules
                    if project is not None and module is not None
                    else False
                )
                label = (
                    "Unregister Project Script"
                    if registered
                    else "Register Project Script"
                )
                if imgui.menu_item(label, "", False)[0]:
                    if registered:
                        self._unregister_project_script(path)
                    else:
                        self._register_project_script(path)
            imgui.separator()
        if imgui.begin_menu("Create"):
            if imgui.menu_item("Folder", "", False)[0]:
                directory = self._create_asset_folder()
                self._begin_rename(
                    "asset",
                    str(directory),
                    directory.name,
                )
            if imgui.menu_item("Python Script", "", False)[0]:
                self._begin_create_python_script()
            if imgui.menu_item("Scene", "", False)[0]:
                self._create_asset_scene()
            if imgui.menu_item("Particle Effect", "", False)[0]:
                self._create_particle_asset()
            imgui.end_menu()
        if path is not None and path != self.state.asset_root:
            if imgui.menu_item("Rename", "", False)[0]:
                self._begin_rename("asset", str(path), path.name)
            if imgui.menu_item("Copy", "Cmd/Ctrl+C", False)[0]:
                self._copy_asset(path)
            if imgui.menu_item("Duplicate", "Cmd/Ctrl+D", False)[0]:
                self._duplicate_asset(path)
        if imgui.menu_item(
            "Paste",
            "Cmd/Ctrl+V",
            False,
            self._asset_clipboard is not None,
        )[0]:
            self._paste_asset(target_directory)
        if path is not None:
            imgui.separator()
            if (
                path != self.state.asset_root
                and imgui.menu_item("Delete", "Delete", False)[0]
            ):
                self._request_delete_asset(path)
            if imgui.menu_item("Reveal in Finder", "", False)[0]:
                self._reveal_asset(path)
        else:
            imgui.separator()
            if imgui.menu_item("Refresh", "", False)[0]:
                self._refresh_assets()
        imgui.end_popup()

    def _draw_asset_drag_source(self, path: Path) -> None:
        if not imgui.begin_drag_drop_source():
            return
        self._asset_drag_path = path
        self._asset_drag_paths = (
            tuple(asset for asset in self._asset_files if asset in self._selected_assets)
            if path in self._selected_assets else (path,)
        )
        imgui.set_drag_drop_payload_py_id(_ASSET_DRAG_PAYLOAD, 1)
        imgui.text(f"{len(self._asset_drag_paths)} assets" if len(self._asset_drag_paths) > 1 else path.name)
        imgui.end_drag_drop_source()

    def _select_asset(self, path: Path, *, additive: bool = False, range_select: bool = False) -> None:
        anchor = self._asset_selection_anchor
        if range_select and anchor is not None and anchor.parent == path.parent:
            siblings = [asset for asset in self._asset_files if asset.parent == path.parent]
            if path in siblings and anchor in siblings:
                start, end = sorted((siblings.index(path), siblings.index(anchor)))
                self._selected_assets = set(siblings[start:end + 1])
        elif additive:
            if path in self._selected_assets:
                self._selected_assets.remove(path)
            else:
                self._selected_assets.add(path)
        else:
            self._selected_assets = {path}
        if not range_select:
            self._asset_selection_anchor = path
        self._selected_asset = (
            next((asset for asset in reversed(self._asset_files) if asset in self._selected_assets), None)
            if additive and path not in self._selected_assets else path
        )

    def _accept_prefab_hierarchy_drop(self, target: Path) -> None:
        if not imgui.begin_drag_drop_target():
            return
        payload = imgui.accept_drag_drop_payload_py_id(
            _HIERARCHY_DRAG_PAYLOAD
        )
        if payload is not None:
            try:
                if target.is_dir():
                    record = self.state.selected_prefab_record
                    if record is None:
                        raise ValueError(
                            "select one game object or UI view "
                            "to create a prefab"
                        )
                    name = str(record.get("name") or "Prefab")
                    safe_name = "".join(
                        character
                        for character in name
                        if character not in '<>:"/\\|?*'
                    ).strip() or "Game Object"
                    destination = self._unique_asset_destination(
                        target / f"{safe_name}{PREFAB_SUFFIX}"
                    )
                else:
                    destination = target
                self._write_selected_prefab(destination)
            except (OSError, ValueError, SceneFormatError) as error:
                self.state.error = str(error)
            finally:
                self._hierarchy_drag_identifier = None
        imgui.end_drag_drop_target()

    def _write_selected_prefab(self, destination: Path) -> Path:
        record = self.state.selected_prefab_record
        if record is None:
            raise ValueError("select exactly one game object or UI view")
        if (
            record.get("type") != "ui"
            and len(self.state.selected_ids) != 1
        ):
            raise ValueError("select exactly one game object")
        destination = destination.resolve()
        destination.relative_to(self.state.asset_root)
        existing = (
            PrefabDocument.read(destination)
            if destination.is_file()
            else None
        )
        before = self.state.begin_history_change()
        old_bytes = (
            destination.read_bytes()
            if destination.is_file()
            else None
        )
        prefab = self.state.prefab_document_for_record(record, existing)
        prefab.write(destination)
        source = destination.relative_to(
            self.state.asset_root
        ).as_posix()
        if existing is None:
            self.state.link_selected_to_prefab(prefab, source)
        else:
            self.state.refresh_prefab_instances(
                prefab,
                source,
                record_history=False,
            )
        new_bytes = destination.read_bytes()
        if old_bytes is None:
            self._commit_created_assets(
                (destination,),
                f"Created prefab {destination.name}",
                before,
            )
        else:
            def undo_write() -> None:
                self._replace_asset_bytes(destination, old_bytes)

            def redo_write() -> None:
                self._replace_asset_bytes(destination, new_bytes)

            self.state.commit_history_change(
                f"Updated prefab {destination.name}",
                before,
                undo_effect=undo_write,
                redo_effect=redo_write,
            )
        self._selected_asset = destination
        self._selected_asset_directory = destination.parent
        self._refresh_assets()
        self.preview.reload()
        return destination

    @staticmethod
    def _replace_asset_bytes(path: Path, content: bytes) -> None:
        with NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)

    def _apply_selected_prefab(self) -> None:
        metadata = self.state.selected_prefab_metadata
        if metadata is None:
            return
        source = metadata.get("source")
        if not isinstance(source, str):
            self.state.error = "Prefab source is missing"
            return
        try:
            self._write_selected_prefab(self.state.asset_root / source)
        except (OSError, ValueError, SceneFormatError) as error:
            self.state.error = str(error)

    def _draw_asset_preview(self) -> None:
        selected = self._selected_asset
        if selected is None:
            imgui.text_disabled("Select an asset to preview it")
            return
        try:
            relative = selected.relative_to(self.state.asset_root)
        except ValueError:
            self._selected_asset = None
            return
        imgui.text_wrapped(relative.as_posix())
        imgui.separator()
        if self._asset_matches_kind(selected, "font"):
            try:
                font = Font(selected, presentation_enabled=False)
            except (OSError, ValueError) as error:
                self.state.error = f"Could not preview font: {error}"
                return
            imgui.text(f"Family: {font.family}")
            imgui.text(f"Style: {font.style}")
            return
        if selected.suffix.lower() not in _IMAGE_SUFFIXES:
            imgui.text_disabled(selected.suffix.lower() or "File")
            return
        try:
            texture = self._atlas_texture(relative.as_posix())
        except (OSError, ValueError) as error:
            self.state.error = f"Could not preview asset: {error}"
            return
        available = imgui.get_content_region_avail()
        scale = min(
            1.0,
            max(0.01, available.x / texture.width),
            max(0.01, max(1.0, available.y - 32) / texture.height),
        )
        texture_id = texture.id
        if texture_id is not None:
            imgui.image(
                imgui.ImTextureRef(texture_id),
                imgui.ImVec2(
                    max(1.0, texture.width * scale),
                    max(1.0, texture.height * scale),
                ),
                imgui.ImVec2(0, 1),
                imgui.ImVec2(1, 0),
            )
        imgui.text_disabled(f"{texture.width} x {texture.height}")

    def _scan_assets(
        self,
    ) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        root = self.state.asset_root
        if not root.is_dir():
            raise OSError(f"asset folder does not exist: {root}")
        directories: list[Path] = []
        files: list[Path] = []
        for current, directory_names, file_names in os.walk(root):
            directory_names[:] = [
                name
                for name in directory_names
                if not name.startswith(".") and name != "__pycache__"
            ]
            base = Path(current)
            directories.extend(base / name for name in directory_names)
            files.extend(
                base / name
                for name in file_names
                if not name.startswith(".")
            )
        return tuple(sorted(directories)), tuple(sorted(files))

    def _refresh_assets(self) -> None:
        try:
            scan = self._scan_assets()
        except OSError as error:
            self._asset_scan = None
            self._asset_directories = ()
            self._asset_files = ()
            self.state.error = f"Could not scan assets: {error}"
            return
        self._apply_asset_scan(scan)

    def _poll_asset_changes(self) -> bool:
        """Rescan the asset folder and refresh when entries changed."""
        try:
            scan = self._scan_assets()
        except OSError:
            return False
        if scan == self._asset_scan:
            return False
        self._apply_asset_scan(scan)
        return True

    def _apply_asset_scan(
        self,
        scan: tuple[tuple[Path, ...], tuple[Path, ...]],
    ) -> None:
        self._asset_scan = scan
        directories, files = scan
        self._asset_directories = directories
        self._asset_files = files
        self._selected_assets.intersection_update(files)
        if not self._selected_asset_directory.is_dir():
            self._selected_asset_directory = self.state.asset_root
        if (
            self._selected_asset is not None
            and not self._selected_asset.exists()
        ):
            self._selected_asset = None

    def _create_asset_folder(self) -> Path:
        before = self.state.begin_history_change()
        directory = self._unique_asset_destination(
            self._selected_asset_directory / "New Folder"
        )
        directory.mkdir()
        self._commit_created_assets(
            (directory,),
            "Created asset folder",
            before,
        )
        self._selected_asset_directory = directory
        self._refresh_assets()
        self.state.status = f"Created asset folder {directory.name}"
        return directory

    def _rename_asset(self, source: Path, name: str) -> Path:
        before = self.state.begin_history_change()
        source = source.resolve()
        source.relative_to(self.state.asset_root)
        if source == self.state.asset_root:
            raise ValueError("the asset root cannot be renamed")
        destination = source.with_name(name)
        if destination.exists():
            raise ValueError(f"asset already exists: {name}")
        old_relative = source.relative_to(self.state.asset_root)
        new_relative = destination.relative_to(self.state.asset_root)
        selected_asset = self._selected_asset
        selected_directory = self._selected_asset_directory
        source.rename(destination)
        self.state.rename_asset_references(
            old_relative.as_posix(),
            new_relative.as_posix(),
            record_history=False,
        )

        def undo_rename() -> None:
            destination.rename(source)

        def redo_rename() -> None:
            source.rename(destination)

        self.state.commit_history_change(
            f"Renamed asset to {name}",
            before,
            undo_effect=undo_rename,
            redo_effect=redo_rename,
        )
        if selected_asset is not None:
            try:
                remainder = selected_asset.relative_to(source)
            except ValueError:
                pass
            else:
                self._selected_asset = destination / remainder
        try:
            remainder = selected_directory.relative_to(source)
        except ValueError:
            pass
        else:
            self._selected_asset_directory = destination / remainder
        for cached_path, cached in tuple(self._atlas_textures.items()):
            try:
                remainder = cached_path.relative_to(source)
            except ValueError:
                continue
            del self._atlas_textures[cached_path]
            self._atlas_textures[destination / remainder] = cached
        self._refresh_assets()
        self.state.status = f"Renamed asset to {name}"
        return destination

    def _create_asset_file(self, name: str, content: str) -> Path:
        before = self.state.begin_history_change()
        destination = self._unique_asset_destination(
            self._selected_asset_directory / name
        )
        destination.write_text(content, encoding="utf-8")
        self._commit_created_assets(
            (destination,),
            f"Created asset {destination.name}",
            before,
        )
        self._selected_asset = destination
        self._refresh_assets()
        return destination

    def _create_python_script(self, name: str, *, template: str = "Empty Script") -> Path:
        from ..script_templates import script_source
        class_name = name.strip()
        if not class_name.isidentifier() or keyword.iskeyword(class_name):
            raise ValueError(
                "script name must be a valid Python class name, "
                "for example PlayerController"
            )
        filename_stem = re.sub(
            r"(?<!^)(?=[A-Z])",
            "_",
            class_name,
        ).lower()
        destination = self._selected_asset_directory / (
            f"{filename_stem}.py"
        )
        if destination.exists():
            raise ValueError(
                f"a script named {destination.name} already exists"
            )
        project = self.project or ProjectConfig.create(
            self.state.asset_root
        )
        module = project.module_for_path(destination)
        component_type = f"{module}.{class_name}"
        content = script_source(class_name, component_type, template)
        created = self._create_asset_file(destination.name, content)
        self._register_project_script(created)
        return created

    def _create_asset_scene(self) -> Path:
        before = self.state.begin_history_change()
        destination = self._unique_asset_destination(
            self._selected_asset_directory / "New Scene.scene.json"
        )
        new_scene_document().write(destination)
        self._commit_created_assets(
            (destination,),
            f"Created scene {destination.name}",
            before,
        )
        self._selected_asset = destination
        self._refresh_assets()
        return destination

    def _create_particle_asset(self) -> Path:
        before = self.state.begin_history_change()
        destination = self._unique_asset_destination(
            self._selected_asset_directory
            / "New Particle Effect.particle.json"
        )
        ParticleEffectDefinition().write(destination)
        self._commit_created_assets(
            (destination,),
            f"Created particle effect {destination.name}",
            before,
        )
        self._selected_asset = destination
        self._refresh_assets()
        self._open_particle_asset(destination)
        return destination

    def _create_particle_asset_from_preset(
        self,
        preset: str,
    ) -> Path:
        before = self.state.begin_history_change()
        display_name = preset.replace("_", " ").title()
        destination = self._unique_asset_destination(
            self._selected_asset_directory
            / f"{display_name}.particle.json"
        )
        particle_preset(preset).write(destination)
        self._commit_created_assets(
            (destination,),
            f"Created {display_name} particle effect",
            before,
        )
        self._selected_asset = destination
        self._refresh_assets()
        self._open_particle_asset(destination)
        return destination

    def _copy_asset(self, path: Path) -> None:
        path = path.resolve()
        path.relative_to(self.state.asset_root)
        self._asset_clipboard = path
        self.state.status = f"Copied asset {path.name}"
        self.state.error = None

    def _paste_asset(self, directory: Path) -> Path | None:
        source = self._asset_clipboard
        if source is None:
            return None
        if not source.exists():
            raise ValueError(f"copied asset no longer exists: {source.name}")
        directory = directory.resolve()
        directory.relative_to(self.state.asset_root)
        if not directory.is_dir():
            raise ValueError("asset paste destination must be a folder")
        if source.is_dir():
            try:
                directory.relative_to(source)
            except ValueError:
                pass
            else:
                raise ValueError("cannot paste a folder inside itself")
        before = self.state.begin_history_change()
        destination = self._unique_asset_destination(
            directory / source.name
        )
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
            if source.name.endswith(PREFAB_SUFFIX):
                copied_prefab = PrefabDocument.read(destination)
                PrefabDocument.create(
                    copied_prefab.template,
                    kind=copied_prefab.kind,
                ).write(destination)
        self._commit_created_assets(
            (destination,),
            f"Pasted asset {destination.name}",
            before,
        )
        self._selected_asset = (
            destination if destination.is_file() else None
        )
        self._selected_asset_directory = (
            destination if destination.is_dir() else destination.parent
        )
        self._refresh_assets()
        return destination

    def _duplicate_asset(self, path: Path) -> Path:
        self._copy_asset(path)
        duplicated = self._paste_asset(path.parent)
        if duplicated is None:
            raise ValueError("asset could not be duplicated")
        return duplicated

    def _request_delete_asset(self, path: Path) -> None:
        """Delete ``path``, asking first if anything still refers to it."""
        source = path.resolve()
        if source == self.state.asset_root:
            raise ValueError("the asset root cannot be deleted")
        references = find_asset_references(
            self.state.asset_root,
            source,
            document=self.state.document,
            document_path=self.state.filename,
            project=self.project,
        )
        if not references:
            self._delete_asset(source)
            return
        self._pending_asset_delete = (source, references)
        self._popup_to_open = "Delete Referenced Asset"

    def _confirm_pending_asset_delete(self) -> None:
        pending = self._pending_asset_delete
        self._pending_asset_delete = None
        if pending is None:
            return
        source, _ = pending
        if not source.exists():
            return
        project = self.project
        if project is not None:
            for module in project.script_modules:
                base = project.root.joinpath(*module.split("."))
                script = next(
                    (
                        candidate
                        for candidate in (
                            base.with_suffix(".py"),
                            base / "__init__.py",
                        )
                        if candidate.exists()
                        and candidate.resolve().is_relative_to(source)
                    ),
                    None,
                )
                if script is not None:
                    self._unregister_project_script(script)
        self._delete_asset(source)
        self.preview.reload()

    def _delete_asset(self, path: Path) -> None:
        source = path.resolve()
        source.relative_to(self.state.asset_root)
        if source == self.state.asset_root:
            raise ValueError("the asset root cannot be deleted")
        before = self.state.begin_history_change()
        trash = self._unique_asset_destination(
            self._asset_trash_root / source.name
        )
        source.rename(trash)

        def undo_delete() -> None:
            trash.rename(source)

        def redo_delete() -> None:
            source.rename(trash)

        self.state.commit_history_change(
            f"Deleted asset {source.name}",
            before,
            undo_effect=undo_delete,
            redo_effect=redo_delete,
        )
        self._selected_asset = None
        self._selected_asset_directory = (
            source.parent
            if source.parent.is_dir()
            else self.state.asset_root
        )
        self._refresh_assets()

    def _commit_created_assets(
        self,
        paths: tuple[Path, ...],
        label: str,
        before: EditorSnapshot,
    ) -> None:
        trash_paths = tuple(
            self._unique_asset_destination(
                self._asset_trash_root / path.name
            )
            for path in paths
        )

        def undo() -> None:
            for path, trash in zip(paths, trash_paths, strict=True):
                path.rename(trash)

        def redo() -> None:
            for path, trash in zip(paths, trash_paths, strict=True):
                trash.rename(path)

        self.state.commit_history_change(
            label,
            before,
            undo_effect=undo,
            redo_effect=redo,
        )

    def _open_asset(self, path: Path) -> None:
        if self._is_scene_asset(path):
            self._load_scene_asset(path)
            return
        if self._is_particle_asset(path):
            self._open_particle_asset(path)
            return
        self._reveal_asset(path)

    @staticmethod
    def _is_scene_asset(path: Path) -> bool:
        return path.is_file() and path.name.lower().endswith(".scene.json")

    @staticmethod
    def _is_particle_asset(path: Path) -> bool:
        return (
            path.is_file()
            and path.name.lower().endswith(".particle.json")
        )

    def _load_scene_asset(self, path: Path) -> None:
        self._path_buffer = str(path)
        self._request_action("open_selected")

    @staticmethod
    def _reveal_asset(path: Path) -> None:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])

    @staticmethod
    def _asset_matches_kind(path: Path, kind: str) -> bool:
        if kind == "image":
            return path.suffix.lower() in _IMAGE_SUFFIXES
        if kind == "particle":
            return path.name.lower().endswith(".particle.json")
        if kind == "font":
            return path.suffix.lower() in {".ttf", ".otf"}
        return False

    def _draw_asset_reference_field(
        self,
        label: str,
        current: str,
        field_id: str,
        *,
        kind: str,
    ) -> None:
        target = _AssetPickerTarget(field_id, kind, kind)
        active = (
            self._asset_picker is not None
            and self._asset_picker.field_id == field_id
        )
        result = AssetField(
            label,
            field_id,
            active=active,
            payload_type=_ASSET_DRAG_PAYLOAD,
            kind=kind,
            clearable=self._asset_field_clearable(field_id),
            missing=(
                bool(current)
                and not (self.state.asset_root / current).exists()
            ),
        ).draw_asset(current)
        self._asset_field_button_rects[field_id] = (
            *result.button_minimum,
            *result.button_maximum,
        )
        self._asset_field_targets[field_id] = target
        if result.cleared:
            try:
                self._clear_asset(target)
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
            return
        if result.dropped and self._asset_drag_path is not None:
            try:
                self._assign_asset(target, self._asset_drag_path)
                self._asset_picker = None
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)

    @staticmethod
    def _asset_field_clearable(field_id: str) -> bool:
        return field_id.startswith((
            "sprite-image:", "background-image:",
            "sprite-animation-state:", "sprite-animation-frame:",
            "particle-effect:",
            "particle-image:",
            "font:",
            "gui-font:",
        ))

    def _clear_asset(self, target: _AssetPickerTarget) -> None:
        if not self._asset_field_clearable(target.field_id):
            raise ValueError(f"{target.label.title()} cannot be empty")
        if target.field_id.startswith(("sprite-animation-state:", "sprite-animation-frame:")):
            _, component, state, frame = target.field_id.split(":")
            self.state.set_sprite_animation_frame_image(int(component), int(state), int(frame), None)
        elif target.field_id.startswith("background-image:"):
            self.state.clear_selected_background_image(component_index=int(target.field_id.split(":")[1]))
        elif target.field_id.startswith("particle-effect:"):
            self.state.set_component_value(
                int(target.field_id.split(":")[1]),
                "effect",
                "",
            )
        elif target.field_id.startswith("particle-image:"):
            self._set_particle_image(None)
        elif target.field_id.startswith("font:"):
            self.state.set_component_value(
                int(target.field_id.split(":")[1]),
                "font",
                None,
            )
        elif target.field_id.startswith("gui-font:"):
            self.state.set_gui_element_value("font", None)
        else:
            self.state.clear_selected_sprite_image()
        self._json_buffers[target.field_id] = ""
        if self._asset_picker == target:
            self._asset_picker = None

    def _toggle_asset_picker(
        self,
        target: _AssetPickerTarget,
    ) -> None:
        active = (
            self._asset_picker is not None
            and self._asset_picker.field_id == target.field_id
        )
        self._asset_picker_keep_alive = True
        self._asset_picker = None if active else target
        if (
            not active
            and self._selected_asset is not None
            and not self._asset_matches_kind(
                self._selected_asset,
                target.kind,
            )
        ):
            self._selected_asset = None

    @staticmethod
    def _abbreviate_asset_path(path: str, limit: int = 42) -> str:
        return AssetField.abbreviate_path(path, limit)

    def _assign_asset_picker(self, path: Path) -> None:
        target = self._asset_picker
        if target is None:
            return
        self._assign_asset(target, path)
        self._asset_picker = None

    def _assign_asset(
        self,
        target: _AssetPickerTarget,
        path: Path,
    ) -> None:
        if not self._asset_matches_kind(path, target.kind):
            raise ValueError(
                f"{target.label.title()} requires an asset of type "
                f"{target.kind}"
            )
        relative = path.resolve().relative_to(
            self.state.asset_root
        ).as_posix()
        if target.kind == "font":
            Font(path, presentation_enabled=False)
        if target.field_id.startswith("sprite-image:"):
            self.state.set_selected_sprite_image(path)
        elif target.field_id.startswith("background-image:"):
            self.state.set_selected_background_image(path, component_index=int(target.field_id.split(":")[1]))
        elif target.field_id.startswith(("sprite-animation-frame:", "sprite-animation-state:")):
            parts = target.field_id.split(":")
            if len(parts) != 4:
                raise ValueError(
                    "invalid sprite animation frame target"
                )
            self.state.set_sprite_animation_frame_image(
                int(parts[1]),
                int(parts[2]),
                int(parts[3]),
                path,
            )
        elif target.field_id.startswith("particle-effect:"):
            component_index = int(target.field_id.split(":")[1])
            self.state.set_component_value(
                component_index,
                "effect",
                relative,
            )
            self._open_particle_asset(path)
        elif target.field_id.startswith("particle-image:"):
            self._set_particle_image(relative)
        elif target.field_id.startswith("font:"):
            self.state.set_component_value(
                int(target.field_id.split(":")[1]),
                "font",
                relative,
            )
        elif target.field_id.startswith("gui-font:"):
            self.state.set_gui_element_value("font", relative)
        elif target.field_id.startswith("tileset-path:"):
            self._json_buffers[target.field_id] = relative
        elif target.field_id == "image-collection-new":
            self.state.add_image_tileset(relative)
            self.preview.reload()
        elif target.field_id.startswith("image-collection-add:"):
            identifier = target.field_id.removeprefix(
                "image-collection-add:"
            )
            self.state.add_image_tile(
                relative,
                tileset_identifier=identifier,
            )
            self.preview.reload()
        else:
            raise ValueError("unknown asset field")
        self._selected_asset = path
        self.state.error = None
        self.state.status = f"Selected asset {relative}"

    def _import_external_assets(
        self,
        paths: list[str] | tuple[str, ...],
    ) -> tuple[Path, ...]:
        before = self.state.begin_history_change()
        destination_directory = self._selected_asset_directory.resolve()
        destination_directory.relative_to(self.state.asset_root)
        imported: list[Path] = []
        for raw_path in paths:
            source = Path(raw_path).expanduser().resolve()
            if not source.exists():
                raise ValueError(f"dropped asset does not exist: {source}")
            try:
                source.relative_to(self.state.asset_root)
            except ValueError:
                pass
            else:
                self._selected_asset = source if source.is_file() else None
                continue
            destination = self._unique_asset_destination(
                destination_directory / source.name
            )
            if source.is_dir():
                shutil.copytree(source, destination)
            elif source.is_file():
                shutil.copy2(source, destination)
            else:
                raise ValueError(
                    f"unsupported dropped asset: {source}"
                )
            imported.append(destination)
        self._refresh_assets()
        if imported:
            self._commit_created_assets(
                tuple(imported),
                (
                    f"Imported {len(imported)} asset"
                    f"{'' if len(imported) == 1 else 's'}"
                ),
                before,
            )
            last = imported[-1]
            self._selected_asset = last if last.is_file() else None
            self.state.status = (
                f"Imported {len(imported)} asset"
                f"{'' if len(imported) == 1 else 's'}"
            )
            for imported_path in imported:
                scripts = (
                    (imported_path,)
                    if imported_path.is_file()
                    and imported_path.suffix.lower() == ".py"
                    else tuple(imported_path.rglob("*.py"))
                    if imported_path.is_dir()
                    else ()
                )
                for script in scripts:
                    self._register_project_script(script)
        return tuple(imported)

    @staticmethod
    def _unique_asset_destination(destination: Path) -> Path:
        if not destination.exists():
            return destination
        stem = destination.stem
        suffix = destination.suffix
        index = 2
        while True:
            candidate = destination.with_name(
                f"{stem} {index}{suffix}"
            )
            if not candidate.exists():
                return candidate
            index += 1

    def _add_empty_sprite(self) -> None:
        try:
            self.state.add_sprite()
        except ValueError as error:
            self.state.error = str(error)

    def _add_sprite_from_selected_asset(self) -> None:
        selected = self._selected_asset
        if (
            selected is None
            or selected.suffix.lower() not in _IMAGE_SUFFIXES
        ):
            self.state.error = (
                "Select a PNG, JPG, JPEG, or BMP image in Assets first"
            )
            return
        try:
            self.state.add_sprite(selected)
        except (ValueError, OSError) as error:
            self.state.error = str(error)
