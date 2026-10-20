from __future__ import annotations

from g.persistence import JsonValue, SceneFormatError
from imgui_bundle import icons_fontawesome_6, imgui

from ..panel_support import (
    ASSET_DRAG_PAYLOAD as _ASSET_DRAG_PAYLOAD,
    HIERARCHY_DRAG_PAYLOAD as _HIERARCHY_DRAG_PAYLOAD,
)
from ..prefab import PREFAB_SUFFIX
from ..state import layer_gui_elements, layer_objects
from ..ui import (
    color as _color,
    flag as _flag,
    icon_button,
)
from .base import EditorPanel


_LAYER_ICONS = {
    "object": icons_fontawesome_6.ICON_FA_CUBE,
    "tile": icons_fontawesome_6.ICON_FA_TABLE_CELLS_LARGE,
    "ui": icons_fontawesome_6.ICON_FA_WINDOW_MAXIMIZE,
}

_GUI_ICONS = {
    "panel": icons_fontawesome_6.ICON_FA_WINDOW_MAXIMIZE,
    "label": icons_fontawesome_6.ICON_FA_FONT,
    "button": icons_fontawesome_6.ICON_FA_SQUARE,
    "toggle": icons_fontawesome_6.ICON_FA_TOGGLE_ON,
    "slider": icons_fontawesome_6.ICON_FA_SLIDERS,
    "text_input": icons_fontawesome_6.ICON_FA_KEYBOARD,
    "stack": icons_fontawesome_6.ICON_FA_LAYER_GROUP,
    "vbox": icons_fontawesome_6.ICON_FA_ARROWS_UP_DOWN,
    "hbox": icons_fontawesome_6.ICON_FA_ARROWS_LEFT_RIGHT,
    "grid": icons_fontawesome_6.ICON_FA_TABLE_CELLS,
    "anchor": icons_fontawesome_6.ICON_FA_ANCHOR,
    "scroll": icons_fontawesome_6.ICON_FA_SCROLL,
}


class LayersPanel(EditorPanel):
    def _draw_layers(self) -> None:
        if self._begin_panel("Layers"):
            self._draw_world_layers()
        imgui.end()

    def _draw_ui_views(self) -> None:
        if self._begin_panel("UI Views"):
            if imgui.is_window_focused():
                self._shortcut_context = "layers"
            if (
                imgui.is_window_focused()
                and imgui.is_window_hovered()
                and imgui.is_mouse_clicked(
                    _flag(imgui.MouseButton_.left)
                )
            ):
                self._select_startup_ui_view()
            if imgui.button("+ UI View"):
                self.state.add_ui_view()
            imgui.same_line()
            imgui.text_disabled("Edit properties in Inspector")
            for view in self.state.document.ui_views:
                self._draw_layer_or_view_row(view, is_view=True)
            self._draw_layer_or_view_actions(is_view=True)
        imgui.end()

    def _select_startup_ui_view(self) -> None:
        if self.state.selected_ui_view is not None:
            return
        views = self.state.document.ui_views
        view = next(
            (
                candidate
                for candidate in views
                if candidate.get("startup") is True
            ),
            views[0] if views else None,
        )
        if view is None:
            return
        identifier = view.get("id")
        if isinstance(identifier, str):
            self.state.select_layer(identifier)

    def _draw_layer_content(self) -> None:
        layer = self.state.selected_layer
        is_tile_layer = (
            layer is not None and layer.get("type") == "tile"
        )
        is_gui_layer = (
            layer is not None and layer.get("type") == "ui"
        )
        if is_tile_layer:
            title = "Tilemap###LayerContent"
        elif is_gui_layer:
            title = "GUI###LayerContent"
        else:
            title = "Hierarchy###LayerContent"
        if self._begin_panel(title):
            content_minimum = imgui.get_cursor_screen_pos()
            content_size = imgui.get_content_region_avail()
            content_maximum = imgui.ImVec2(
                content_minimum.x + content_size.x,
                content_minimum.y + content_size.y,
            )
            self._layer_content_rect = (
                content_minimum.x,
                content_minimum.y,
                content_maximum.x,
                content_maximum.y,
            )
            if layer is None:
                imgui.text_disabled("Select a layer")
            elif is_tile_layer:
                self._draw_tile_layer_inspector(layer)
            elif is_gui_layer:
                self._draw_gui_layer_contents(layer)
            else:
                self._draw_hierarchy_tab(layer)
            if layer is not None and not is_tile_layer:
                self._draw_hierarchy_empty_space(
                    "gui" if is_gui_layer else "object",
                )
        imgui.end()

    def _draw_hierarchy_empty_space(self, kind: str) -> None:
        available = imgui.get_content_region_avail()
        imgui.invisible_button(
            f"##{kind}-hierarchy-empty-space",
            imgui.ImVec2(
                max(1.0, available.x),
                max(1.0, available.y),
            ),
        )
        if imgui.is_item_clicked(
            _flag(imgui.MouseButton_.left)
        ):
            self.state.select(None)
            self._shortcut_context = "hierarchy"
        self._draw_hierarchy_context_menu(kind, None)
        self._accept_hierarchy_drop(None, "root", kind)

    def _draw_hierarchy_tab(
        self,
        layer: dict[str, JsonValue],
    ) -> None:
        if imgui.is_window_focused():
            self._shortcut_context = "hierarchy"
        if imgui.button("+##add-object"):
            imgui.open_popup("Add Game Object")
        self._draw_add_object_popup()
        imgui.separator()
        objects = layer_objects(layer)
        if not objects:
            imgui.text_disabled("No game objects yet")
            return
        layer_identifier = str(layer.get("id", ""))
        ordered_ids = self._hierarchy_visible_orders.get(
            layer_identifier,
            self._object_hierarchy_order(objects),
        )
        self._navigate_hierarchy_selection(ordered_ids)
        visible_ids: list[str] = []
        for record in objects:
            self._draw_hierarchy_record(
                record,
                ordered_ids,
                visible_ids,
            )
        self._hierarchy_visible_orders[layer_identifier] = visible_ids

    def _draw_gui_layer_contents(
        self,
        layer: dict[str, JsonValue],
    ) -> None:
        if imgui.is_window_focused():
            self._shortcut_context = "hierarchy"
        if imgui.button("+##add-gui-element"):
            imgui.open_popup("Add GUI Element")
        self._draw_add_gui_element_popup()
        imgui.separator()
        elements = layer_gui_elements(layer)
        if not elements:
            imgui.text_disabled("No GUI elements yet")
            return
        layer_identifier = str(layer.get("id", ""))
        ordered_ids = self._hierarchy_visible_orders.get(
            layer_identifier,
            self._gui_hierarchy_order(elements),
        )
        self._navigate_hierarchy_selection(ordered_ids)
        visible_ids: list[str] = []
        for element in elements:
            self._draw_gui_hierarchy_record(
                element,
                ordered_ids,
                visible_ids,
            )
        self._hierarchy_visible_orders[layer_identifier] = visible_ids

    def _draw_add_object_popup(self) -> None:
        if not imgui.begin_popup("Add Game Object"):
            return
        for label, kind in (
            ("Empty Object", "empty"),
            ("Rectangle", "rectangle"),
            ("Circle", "circle"),
            ("Text", "text"),
            ("Box Area", "area_box"),
            ("Circle Area", "area_circle"),
        ):
            if imgui.menu_item(label, "", False)[0]:
                self.state.add_object(kind)
        if imgui.menu_item("Sprite", "", False)[0]:
            self._add_empty_sprite()
        if imgui.menu_item("Background", "", False)[0]:
            self.state.add_background()
        imgui.end_popup()

    def _draw_add_gui_element_popup(self) -> None:
        if not imgui.begin_popup("Add GUI Element"):
            return
        for label, kind in (
            ("Panel", "panel"),
            ("Label", "label"),
            ("Button", "button"),
            ("Toggle", "toggle"),
            ("Slider", "slider"),
            ("Text Input", "text_input"),
        ):
            if imgui.menu_item(label, "", False)[0]:
                try:
                    self.state.add_gui_element(kind)
                except ValueError as error:
                    self.state.error = str(error)
        imgui.separator()
        for label, kind in (
            ("Stack Layout", "stack"),
            ("Vertical Layout", "vbox"),
            ("Horizontal Layout", "hbox"),
            ("Grid Layout", "grid"),
            ("Anchor Layout", "anchor"),
            ("Scroll View", "scroll"),
        ):
            if imgui.menu_item(label, "", False)[0]:
                try:
                    self.state.add_gui_element(kind)
                except ValueError as error:
                    self.state.error = str(error)
        imgui.end_popup()

    def _draw_world_layers(self) -> None:
        if imgui.is_window_focused():
            self._shortcut_context = "layers"
        if imgui.button("+ Object Layer"):
            self.state.add_layer()
        imgui.same_line()
        if imgui.button("+ Tile Layer"):
            self.state.add_tile_layer()
        imgui.text_disabled("Top layers render in front")
        imgui.text_disabled("Cmd/Ctrl-click layers for mixed selection")
        for layer in reversed(self.state.document.layers):
            self._draw_layer_or_view_row(layer, is_view=False)
        self._draw_layer_or_view_actions(is_view=False)

    def _draw_layer_or_view_actions(self, *, is_view: bool) -> None:
        imgui.separator()
        layer = self.state.selected_layer
        selected_is_view = (
            layer is not None and layer.get("type") == "ui"
        )
        if layer is None or selected_is_view != is_view:
            imgui.text_disabled(
                "Select a UI view" if is_view else "Select a world layer"
            )
            return
        button_size = imgui.ImVec2(34, 30)
        if icon_button(
            icons_fontawesome_6.ICON_FA_ARROW_UP,
            "layer-move-up",
            (
                "Move UI View Up"
                if is_view
                else "Move Layer Up\nRender this layer further in front"
            ),
            size=button_size,
        ):
            self.state.move_selected_layer(1)
        imgui.same_line()
        if icon_button(
            icons_fontawesome_6.ICON_FA_ARROW_DOWN,
            "layer-move-down",
            (
                "Move UI View Down"
                if is_view
                else "Move Layer Down\nRender this layer further behind"
            ),
            size=button_size,
        ):
            self.state.move_selected_layer(-1)
        imgui.same_line()
        if icon_button(
            icons_fontawesome_6.ICON_FA_TRASH,
            "layer-delete",
            "Delete UI View" if is_view else "Delete Layer",
            size=button_size,
        ):
            try:
                self.state.delete_selected_layer()
            except ValueError as error:
                self.state.error = str(error)

    def _draw_layer_or_view_row(
        self,
        record: dict[str, JsonValue],
        *,
        is_view: bool,
    ) -> None:
        identifier = record.get("id")
        if not isinstance(identifier, str):
            return
        icon = _LAYER_ICONS.get(
            str(record.get("type", "object")),
            icons_fontawesome_6.ICON_FA_LAYER_GROUP,
        )
        marker = ""
        if record.get("default") is True:
            marker = "  DEFAULT"
        elif record.get("startup") is True:
            marker = "  STARTUP"
        if not is_view:
            visible = bool(record.get("visible", True))
            if icon_button(
                (
                    icons_fontawesome_6.ICON_FA_EYE
                    if visible
                    else icons_fontawesome_6.ICON_FA_EYE_SLASH
                ),
                f"layer-visible-{identifier}",
                "Hide Layer" if visible else "Show Layer",
                size=imgui.ImVec2(27, 23),
            ):
                self.state.set_layer_value_for(
                    identifier,
                    "visible",
                    not visible,
                )
            imgui.same_line()
        fallback = "UI View" if is_view else "Layer"
        name = str(record.get("name") or fallback)
        clicked, _ = imgui.selectable(
            f"{icon}  {name}{marker}##layer-{identifier}",
            any(layer.get("id") == identifier for layer in self.state.selected_layers),
        )
        item_minimum = imgui.get_item_rect_min()
        item_maximum = imgui.get_item_rect_max()
        if clicked:
            io = imgui.get_io()
            self.state.select_layer(identifier, toggle=bool(io.key_ctrl or io.key_super))
            if imgui.is_mouse_double_clicked(
                _flag(imgui.MouseButton_.left)
            ):
                self._begin_rename(
                    "layer",
                    identifier,
                    name,
                )
        self._draw_inline_rename(
            "layer",
            identifier,
            name,
            item_minimum,
            item_maximum,
        )
        if is_view and not self._is_renaming("layer", identifier):
            self._draw_ui_view_drag_source(identifier, name)
            self._accept_ui_view_asset_drop()

    def _draw_ui_view_drag_source(
        self,
        identifier: str,
        name: str,
    ) -> None:
        if not imgui.begin_drag_drop_source():
            return
        self.state.select_layer(identifier)
        self._hierarchy_drag_identifier = identifier
        imgui.set_drag_drop_payload_py_id(
            _HIERARCHY_DRAG_PAYLOAD,
            1,
        )
        imgui.text(f"UI View: {name}")
        imgui.end_drag_drop_source()

    def _accept_ui_view_asset_drop(self) -> None:
        if not imgui.begin_drag_drop_target():
            return
        payload = imgui.accept_drag_drop_payload_py_id(
            _ASSET_DRAG_PAYLOAD
        )
        if payload is not None and self._asset_drag_path is not None:
            path = self._asset_drag_path
            if not path.name.endswith(PREFAB_SUFFIX):
                self.state.error = "UI Views accept only prefab assets"
            else:
                try:
                    self.state.instantiate_ui_view_prefab(path)
                    self.preview.reload()
                except (OSError, ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
        imgui.end_drag_drop_target()

    def _draw_hierarchy_record(
        self,
        record: dict[str, JsonValue],
        ordered_ids: list[str],
        visible_ids: list[str],
    ) -> None:
        identifier = record.get("id")
        if not isinstance(identifier, str):
            return
        visible_ids.append(identifier)
        children = record.get("children")
        has_children = isinstance(children, list) and any(
            isinstance(child, dict) for child in children
        )
        flags = (
            _flag(imgui.TreeNodeFlags_.open_on_arrow)
            | _flag(imgui.TreeNodeFlags_.span_avail_width)
        )
        if has_children:
            flags |= _flag(imgui.TreeNodeFlags_.default_open)
        if not has_children:
            flags |= (
                _flag(imgui.TreeNodeFlags_.leaf)
                | _flag(imgui.TreeNodeFlags_.no_tree_push_on_open)
            )
        if self.state.is_selected(identifier):
            flags |= _flag(imgui.TreeNodeFlags_.selected)
        name = str(record.get("name") or record.get("type") or "Object")
        # EditorState owns arrow navigation and selection, not ImGui's nav cursor.
        imgui.push_item_flag(imgui.ItemFlags_.no_nav, True)
        opened = imgui.tree_node_ex(
            f"{icons_fontawesome_6.ICON_FA_CUBE}  {name}##{identifier}",
            flags,
        )
        imgui.pop_item_flag()
        self._scroll_keyboard_selection(identifier)
        item_minimum = imgui.get_item_rect_min()
        item_maximum = imgui.get_item_rect_max()
        if imgui.is_item_clicked():
            self._select_from_hierarchy(identifier, ordered_ids)
        self._draw_hierarchy_context_menu("object", identifier)
        renaming = self._is_renaming("object", identifier)
        self._draw_inline_rename(
            "object",
            identifier,
            name,
            item_minimum,
            item_maximum,
        )
        if not renaming:
            self._draw_hierarchy_drag_source(identifier, name)
            self._draw_hierarchy_drop_target(
                identifier,
                "object",
                item_minimum,
                item_maximum,
            )
        if opened and has_children and isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    self._draw_hierarchy_record(
                        child,
                        ordered_ids,
                        visible_ids,
                    )
            imgui.tree_pop()

    def _draw_gui_hierarchy_record(
        self,
        element: dict[str, JsonValue],
        ordered_ids: list[str],
        visible_ids: list[str],
    ) -> None:
        identifier = element.get("id")
        if not isinstance(identifier, str):
            return
        visible_ids.append(identifier)
        children = element.get("children")
        has_children = isinstance(children, list) and any(
            isinstance(child, dict) for child in children
        )
        flags = (
            _flag(imgui.TreeNodeFlags_.open_on_arrow)
            | _flag(imgui.TreeNodeFlags_.span_avail_width)
        )
        if has_children:
            flags |= _flag(imgui.TreeNodeFlags_.default_open)
        if not has_children:
            flags |= (
                _flag(imgui.TreeNodeFlags_.leaf)
                | _flag(imgui.TreeNodeFlags_.no_tree_push_on_open)
            )
        if self.state.is_selected(identifier):
            flags |= _flag(imgui.TreeNodeFlags_.selected)
        name = str(
            element.get("name")
            or element.get("type")
            or "Element"
        )
        element_type = str(element.get("type", ""))
        icon = _GUI_ICONS.get(
            element_type,
            icons_fontawesome_6.ICON_FA_SHAPES,
        )
        imgui.push_item_flag(imgui.ItemFlags_.no_nav, True)
        opened = imgui.tree_node_ex(
            f"{icon}  {name}##gui-element-{identifier}",
            flags,
        )
        imgui.pop_item_flag()
        self._scroll_keyboard_selection(identifier)
        item_minimum = imgui.get_item_rect_min()
        item_maximum = imgui.get_item_rect_max()
        if imgui.is_item_clicked():
            self._select_from_hierarchy(identifier, ordered_ids)
        self._draw_hierarchy_context_menu("gui", identifier)
        renaming = self._is_renaming("gui", identifier)
        self._draw_inline_rename(
            "gui",
            identifier,
            name,
            item_minimum,
            item_maximum,
        )
        if not renaming:
            self._draw_hierarchy_drag_source(identifier, name)
            self._draw_hierarchy_drop_target(
                identifier,
                "gui",
                item_minimum,
                item_maximum,
            )
        if opened and has_children and isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                self._draw_gui_hierarchy_record(
                    child,
                    ordered_ids,
                    visible_ids,
                )
            imgui.tree_pop()

    def _draw_hierarchy_context_menu(
        self,
        kind: str,
        identifier: str | None,
    ) -> None:
        if not imgui.begin_popup_context_item(
            f"##{kind}-context-{identifier or 'root'}"
        ):
            return
        self._shortcut_context = "hierarchy"
        if identifier is None:
            self.state.select(None)
        elif not self.state.is_selected(identifier):
            self.state.select(identifier)
        if imgui.begin_menu("Add"):
            if kind == "object":
                for label, object_kind in (
                    ("Empty Object", "empty"),
                    ("Rectangle", "rectangle"),
                    ("Circle", "circle"),
                    ("Text", "text"),
                    ("Box Area", "area_box"),
                    ("Circle Area", "area_circle"),
                ):
                    if imgui.menu_item(label, "", False)[0]:
                        self.state.add_object(object_kind)
                if imgui.menu_item("Sprite", "", False)[0]:
                    self._add_empty_sprite()
                if imgui.menu_item("Background", "", False)[0]:
                    self.state.add_background()
            else:
                for label, element_kind in (
                    ("Panel", "panel"),
                    ("Label", "label"),
                    ("Button", "button"),
                    ("Toggle", "toggle"),
                    ("Slider", "slider"),
                    ("Text Input", "text_input"),
                    ("Stack Layout", "stack"),
                    ("Vertical Layout", "vbox"),
                    ("Horizontal Layout", "hbox"),
                    ("Grid Layout", "grid"),
                    ("Anchor Layout", "anchor"),
                    ("Scroll View", "scroll"),
                ):
                    if imgui.menu_item(label, "", False)[0]:
                        try:
                            self.state.add_gui_element(element_kind)
                        except ValueError as error:
                            self.state.error = str(error)
            imgui.end_menu()
        has_selection = bool(self.state.selected_ids)
        single = len(self.state.selected_ids) == 1
        if imgui.menu_item("Rename", "", False, single)[0]:
            self._start_selected_rename()
        imgui.separator()
        if imgui.menu_item("Cut", "Cmd/Ctrl+X", False, has_selection)[0]:
            self._cut_selection()
        if imgui.menu_item("Copy", "Cmd/Ctrl+C", False, has_selection)[0]:
            self._copy_selection()
        if imgui.menu_item("Paste", "Cmd/Ctrl+V", False)[0]:
            self._paste_selection()
        if imgui.menu_item(
            "Duplicate",
            "Cmd/Ctrl+D",
            False,
            has_selection,
        )[0]:
            self._duplicate_selection()
        imgui.separator()
        if imgui.menu_item("Delete", "Delete", False, has_selection)[0]:
            self.state.delete_selected()
        imgui.end_popup()

    def _draw_hierarchy_drag_source(
        self,
        identifier: str,
        label: str,
    ) -> None:
        if not imgui.begin_drag_drop_source():
            return
        if not self.state.is_selected(identifier):
            self.state.select(identifier)
        self._hierarchy_drag_identifier = identifier
        imgui.set_drag_drop_payload_py_id(
            _HIERARCHY_DRAG_PAYLOAD,
            1,
        )
        count = len(self.state.selected_ids)
        imgui.text(
            label if count == 1 else f"{label} and {count - 1} more"
        )
        imgui.end_drag_drop_source()

    def _draw_hierarchy_drop_target(
        self,
        identifier: str,
        kind: str,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        if not imgui.begin_drag_drop_target():
            return
        payload = imgui.accept_drag_drop_payload_py_id(
            _HIERARCHY_DRAG_PAYLOAD
        )
        if payload is not None:
            pointer = imgui.get_mouse_pos()
            height = max(1.0, maximum.y - minimum.y)
            relative_y = (pointer.y - minimum.y) / height
            placement = (
                "before"
                if relative_y < 0.25
                else "after"
                if relative_y > 0.75
                else "inside"
            )
            draw_list = imgui.get_window_draw_list()
            color = _color(60, 196, 255)
            if placement == "inside":
                draw_list.add_rect(
                    minimum,
                    maximum,
                    color,
                    thickness=2,
                )
            else:
                y = minimum.y if placement == "before" else maximum.y
                draw_list.add_line(
                    imgui.ImVec2(minimum.x, y),
                    imgui.ImVec2(maximum.x, y),
                    color,
                    thickness=2,
                )
            self._perform_hierarchy_drop(
                identifier,
                placement,
                kind,
            )
        asset_payload = imgui.accept_drag_drop_payload_py_id(
            _ASSET_DRAG_PAYLOAD
        )
        if asset_payload is not None:
            self._perform_asset_object_drop(identifier, kind)
        imgui.end_drag_drop_target()

    def _accept_hierarchy_drop(
        self,
        identifier: str | None,
        placement: str,
        kind: str,
    ) -> None:
        if not imgui.begin_drag_drop_target():
            return
        payload = imgui.accept_drag_drop_payload_py_id(
            _HIERARCHY_DRAG_PAYLOAD
        )
        if payload is not None:
            self._perform_hierarchy_drop(
                identifier,
                placement,
                kind,
            )
        asset_payload = imgui.accept_drag_drop_payload_py_id(
            _ASSET_DRAG_PAYLOAD
        )
        if asset_payload is not None:
            self._perform_asset_object_drop(identifier, kind)
        imgui.end_drag_drop_target()

    def _perform_asset_object_drop(
        self,
        parent_identifier: str | None,
        kind: str,
    ) -> None:
        path = self._asset_drag_path
        if path is None:
            return
        if path.suffix.lower() == ".py":
            if kind != "object" or parent_identifier is None:
                self.state.error = (
                    "Scripts must be dropped on a game object"
                )
                return
            self._attach_project_script(path, parent_identifier)
            return
        if not path.name.endswith(PREFAB_SUFFIX):
            return
        if kind != "object":
            self.state.error = "Game-object prefabs require an object layer"
            return
        try:
            self.state.instantiate_prefab(
                path,
                parent_identifier=parent_identifier,
            )
            self.preview.reload()
        except (OSError, ValueError, SceneFormatError) as error:
            self.state.error = str(error)

    def _perform_hierarchy_drop(
        self,
        target_identifier: str | None,
        placement: str,
        kind: str,
    ) -> None:
        if self._hierarchy_drag_identifier is None:
            return
        try:
            if kind == "object":
                self.state.reparent_selected_objects(
                    target_identifier,
                    placement,
                )
            else:
                positions: dict[str, tuple[float, float]] = {}
                for element in self.state.selected_gui_elements:
                    identifier = element.get("id")
                    if not isinstance(identifier, str):
                        continue
                    bounds = self.preview.gui_element_bounds(identifier)
                    if bounds is not None:
                        positions[identifier] = (bounds[0], bounds[1])
                self.state.reparent_selected_gui_elements(
                    target_identifier,
                    placement,
                    screen_positions=positions,
                )
        except (ValueError, SceneFormatError) as error:
            self.state.error = str(error)
        finally:
            self._hierarchy_drag_identifier = None

    @staticmethod
    def _object_hierarchy_order(
        records: list[dict[str, JsonValue]],
    ) -> list[str]:
        identifiers: list[str] = []
        for record in records:
            identifier = record.get("id")
            if isinstance(identifier, str):
                identifiers.append(identifier)
            children = record.get("children")
            if isinstance(children, list):
                identifiers.extend(
                    LayersPanel._object_hierarchy_order(
                        [
                            child
                            for child in children
                            if isinstance(child, dict)
                        ]
                    )
                )
        return identifiers

    @staticmethod
    def _gui_hierarchy_order(
        records: list[dict[str, JsonValue]],
    ) -> list[str]:
        identifiers: list[str] = []
        for record in records:
            identifier = record.get("id")
            if isinstance(identifier, str):
                identifiers.append(identifier)
            children = record.get("children")
            if isinstance(children, list):
                identifiers.extend(
                    LayersPanel._gui_hierarchy_order(
                        [
                            child
                            for child in children
                            if isinstance(child, dict)
                        ]
                    )
                )
        return identifiers

    def _select_from_hierarchy(
        self,
        identifier: str,
        ordered_ids: list[str],
    ) -> None:
        self._shortcut_context = "hierarchy"
        io = imgui.get_io()
        command = io.key_super or io.key_ctrl
        if io.key_shift:
            self.state.select_range(
                identifier,
                ordered_ids,
                additive=command,
            )
        else:
            self.state.select(
                identifier,
                toggle=command,
            )
        if self.state.is_selected(identifier):
            self.preview.reveal_object_if_outside_viewport(
                identifier,
                self.render_target.width,
                self.render_target.height,
            )

    def _navigate_hierarchy_selection(self, ordered_ids: list[str]) -> None:
        io = imgui.get_io()
        if (
            not ordered_ids
            or not imgui.is_window_focused()
            or io.want_text_input
            or imgui.is_any_item_active()
            or imgui.is_popup_open("", _flag(imgui.PopupFlags_.any_popup_id))
            or self._rename_target is not None
            or self.preview.mode != "edit"
            or io.key_ctrl
            or io.key_super
            or io.key_alt
        ):
            return
        if imgui.is_key_pressed(imgui.Key.up_arrow, True):
            direction = -1
        elif imgui.is_key_pressed(imgui.Key.down_arrow, True):
            direction = 1
        else:
            return
        # Mouse clicks may leave a native nav ID behind; don't highlight it as well.
        imgui.set_nav_cursor_visible(False)
        current = self.state.selected_id
        if current in ordered_ids:
            index = max(0, min(len(ordered_ids) - 1, ordered_ids.index(current) + direction))
        else:
            index = 0 if direction > 0 else len(ordered_ids) - 1
        identifier = ordered_ids[index]
        if io.key_shift:
            self.state.select_range(identifier, ordered_ids)
        else:
            self.state.select(identifier)
        self._shortcut_context = "hierarchy"
        self._hierarchy_keyboard_selection = identifier
        self.preview.reveal_object_if_outside_viewport(
            identifier, self.render_target.width, self.render_target.height,
        )

    def _scroll_keyboard_selection(self, identifier: str) -> None:
        if self._hierarchy_keyboard_selection == identifier:
            if not imgui.is_item_visible():
                imgui.set_scroll_here_y()
            self._hierarchy_keyboard_selection = None
