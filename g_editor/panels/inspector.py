from __future__ import annotations

from copy import deepcopy
import json
from math import hypot, isfinite
from typing import cast

from g.components import Collider, Script, Spline
from g.math import Vec2
from g.inspector import InspectorField, inspector_fields
from g.persistence import JsonValue, SceneFormatError, scene_types
from g.particles import PARTICLE_PRESETS
from imgui_bundle import icons_fontawesome_6, imgui

from ..code_editor import open_script_in_editor
from ..panel_support import ASSET_DRAG_PAYLOAD as _ASSET_DRAG_PAYLOAD, TileCollisionDrag
from ..state import (
    DEFAULT_SCENE_SIZE,
    SCENE_RESOLUTION_PRESETS,
    EditorState,
    position,
)
from ..ui import (
    BooleanField,
    ChoiceField,
    ColorField,
    FloatField,
    IntegerField,
    MaskField,
    OptionalColorField,
    SliderFloatField,
    TextField,
    Vec2Field,
    color as _color,
    flag as _flag,
    icon_button,
)
from ..collider_edit import (
    COLLIDER_SHAPES, GEOMETRY_FIELDS, box_resize_values, collider_shape, edge_projection, number,
)
from .base import EditorPanel


_FLOAT_FIELD_LIMIT = 3.402823466e38
_INTEGER_FIELD_LIMIT = 2_147_483_647
_TILE_HANDLE_HIT_RADIUS = 16


def _segment_projection(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[tuple[float, float], float]:
    distance, amount = edge_projection(point, start, end)
    return (
        start[0] + amount * (end[0] - start[0]),
        start[1] + amount * (end[1] - start[1]),
    ), distance

def _component_label(component_type: str) -> str:
    codec = scene_types.component_codec_for_name(component_type)
    if codec.display_name is not None:
        return codec.display_name
    name = codec.value_type.__name__
    ambiguous = any(
        other.value_type is not codec.value_type
        and other.value_type.__name__ == name
        for registered in scene_types.editable_component_types
        for other in (scene_types.component_codec_for_name(registered),)
    )
    return f"{name} ({component_type})" if ambiguous else name


def _component_field_label(field_name: str) -> str:
    return field_name.replace("_", " ").strip().title()


def _component_display_properties(
    component_type: str,
    properties: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    displayed = dict(properties)
    template = scene_types.component_template(component_type)
    defaults = template["properties"]
    assert isinstance(defaults, dict)
    result = {**defaults, **displayed}
    if (
        issubclass(scene_types.component_type_for_name(component_type), Collider)
        and "override_collision_filtering" not in properties
    ):
        result["override_collision_filtering"] = any(
            result.get(name) != default
            for name, default in (("categories", 0xFFFFFFFF), ("mask", 0xFFFFFFFF), ("group", 0))
        )
    return result


def _component_group(component_type: str) -> str:
    codec = scene_types.component_codec_for_name(component_type)
    if codec.category is not None:
        return codec.category
    if codec.builtin:
        return "Other"
    return "Scripts" if issubclass(codec.value_type, Script) else "Project Components"


def _component_icon(component_type: str) -> str:
    renderer_icons = {
        "g.rect_renderer": icons_fontawesome_6.ICON_FA_SQUARE,
        "g.circle_renderer": icons_fontawesome_6.ICON_FA_CIRCLE,
        "g.text_renderer": icons_fontawesome_6.ICON_FA_FONT,
        "g.sprite_renderer": icons_fontawesome_6.ICON_FA_IMAGE,
        "g.repeating_image_renderer": icons_fontawesome_6.ICON_FA_IMAGES,
        "g.line_renderer": icons_fontawesome_6.ICON_FA_RULER,
        "g.triangle_renderer": icons_fontawesome_6.ICON_FA_DRAW_POLYGON,
        "g.polygon_renderer": icons_fontawesome_6.ICON_FA_DRAW_POLYGON,
    }
    if component_type in renderer_icons:
        return renderer_icons[component_type]
    codec = scene_types.component_codec_for_name(component_type)
    if issubclass(codec.value_type, Collider):
        return icons_fontawesome_6.ICON_FA_VECTOR_SQUARE
    category_icons = {
        "Renderers": icons_fontawesome_6.ICON_FA_IMAGE,
        "Physics": icons_fontawesome_6.ICON_FA_ATOM,
        "Movement": icons_fontawesome_6.ICON_FA_ARROWS_UP_DOWN_LEFT_RIGHT,
        "Camera": icons_fontawesome_6.ICON_FA_VIDEO,
        "Animation": icons_fontawesome_6.ICON_FA_PLAY,
        "Scripts": icons_fontawesome_6.ICON_FA_CODE,
    }
    return category_icons.get(
        _component_group(component_type),
        (
            icons_fontawesome_6.ICON_FA_CODE
            if issubclass(codec.value_type, Script)
            else icons_fontawesome_6.ICON_FA_PUZZLE_PIECE
        ),
    )


class InspectorPanel(EditorPanel):
    def _draw_inspector(self) -> None:
        tile_ids = list(self.state.selected_placed_tile_ids) or self.state.palette_tile_ids
        target = self._tile_collision_target
        if target is not None and (
            self.state.selected_layer_id != target[0] or tile_ids != [target[1]]
            or self.state.selected_layer is None
            or self.state.selected_layer.get("type") != "tile"
        ):
            self._tile_collision_target = None
            self._tile_collision_drag = None
            self._tile_collision_point = None
            self._tile_collision_add = False
        if self._begin_panel("Inspector"):
            imgui.begin_group()
            record = self.state.selected
            gui_element = self.state.selected_gui_element
            layer = self.state.selected_layer
            if self.state.selected_ui_view is not None and gui_element is None:
                self._draw_ui_view_inspector(
                    self.state.selected_ui_view
                )
            elif layer is not None and layer.get("type") == "tile":
                self._draw_selected_tile_inspector()
            elif gui_element is not None:
                self._draw_gui_element_inspector(gui_element)
            elif record is None:
                self._draw_scene_inspector()
                imgui.separator()
                imgui.text_disabled(
                    "Select an object in Hierarchy to inspect it"
                )
            else:
                self._draw_object_inspector(record)
                self._draw_component_warnings(record)
                components = record.get("components")
                if isinstance(components, list):
                    for index, component in enumerate(components):
                        if isinstance(component, dict):
                            if self._draw_component_inspector(
                                index,
                                component,
                            ):
                                break
                self._draw_add_component()
            imgui.end_group()
            if imgui.begin_drag_drop_target():
                payload = imgui.accept_drag_drop_payload_py_id(
                    _ASSET_DRAG_PAYLOAD
                )
                if (
                    payload is not None
                    and self._asset_drag_path is not None
                ):
                    path = self._asset_drag_path
                    if path.suffix.lower() == ".py":
                        self._attach_project_script(path)
                    else:
                        self.state.error = (
                            "Only Python scripts can be attached here"
                        )
                imgui.end_drag_drop_target()
            if (
                record is not None
                and gui_element is None
                and self.state.selected_ui_view is None
                and (layer is None or layer.get("type") != "tile")
            ):
                self._draw_inspector_context_menu()
        imgui.end()

    def _draw_inspector_context_menu(self) -> None:
        if not imgui.begin_popup_context_window(
            "Inspector Context Menu",
            _flag(imgui.PopupFlags_.mouse_button_right)
            | _flag(imgui.PopupFlags_.no_open_over_items),
        ):
            return
        self._reload_changed_scripts()
        self._draw_paste_component_action()
        if imgui.begin_menu("Add Component"):
            self._draw_component_type_menu()
            imgui.end_menu()
        imgui.end_popup()

    def _draw_component_warnings(
        self, record: dict[str, JsonValue]
    ) -> None:
        warnings = self.state.object_component_warnings(record)
        if not warnings:
            return
        imgui.separator_text("Component warnings")
        imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(1, 0.75, 0.25, 1))
        for warning in warnings:
            imgui.text_wrapped(f"Warning: {warning}")
        imgui.pop_style_color()
        imgui.text_wrapped(
            "Add the required components using Add Component, or remove "
            "the dependent component."
        )

    def _draw_ui_view_inspector(
        self,
        view: dict[str, JsonValue],
    ) -> None:
        imgui.separator_text("UI View")
        imgui.text_wrapped(
            "This Inspector controls the selected UI view. "
            "Select a different view in Layers to edit its properties "
            "or elements."
        )
        imgui.spacing()
        self._draw_prefab_controls()
        changed, name = self._text_property(
            "Name",
            str(view.get("name") or ""),
        )
        if changed and name:
            try:
                self.state.set_layer_value("name", name)
            except ValueError as error:
                self.state.error = str(error)

        startup = bool(view.get("startup", False))
        changed, startup_value = self._bool_property(
            "Startup View",
            startup,
        )
        if changed and startup_value and not startup:
            self.state.make_selected_ui_view_startup()
        imgui.text_disabled(
            "The startup view is active when the scene opens."
        )

        properties = (
            (
                "Transparent",
                "transparent",
                True,
                "Show lower UI views behind this view.",
            ),
            (
                "Modal",
                "modal",
                False,
                "Block pointer input from reaching lower UI views.",
            ),
            (
                "Pause Lower Views",
                "pauses_below",
                True,
                "Stop lower UI views from updating.",
            ),
            (
                "Pause Scene",
                "pauses_scene",
                False,
                "Stop world simulation while this view is active.",
            ),
        )
        for label, key_name, default, help_text in properties:
            changed, value = self._bool_property(
                label,
                bool(view.get(key_name, default)),
            )
            if changed:
                self.state.set_layer_value(key_name, value)
            imgui.text_disabled(help_text)

        imgui.separator()
        elements = view.get("elements")
        count = len(elements) if isinstance(elements, list) else 0
        imgui.text(f"Root elements: {count}")
        imgui.text_disabled(
            "Use the GUI hierarchy panel to add and select elements."
        )

    def _draw_gui_element_inspector(
        self,
        element: dict[str, JsonValue],
    ) -> None:
        imgui.separator_text("GUI Element")
        changed, name = self._text_property(
            "Name",
            str(element.get("name") or ""),
        )
        if changed and name:
            self.state.set_gui_element_value("name", name)
        for label, key_name in (
            ("Visible", "visible"),
            ("Enabled", "enabled"),
        ):
            changed, value = self._bool_property(
                label,
                bool(element.get(key_name, True)),
            )
            if changed:
                self.state.set_gui_element_value(key_name, value)
        if self.state.gui_element_is_free_positioned(element):
            position_fields = (("X", "x"), ("Y", "y"))
            for label, key_name in position_fields:
                raw_value = element.get(key_name, 0)
                value = (
                    float(raw_value)
                    if isinstance(raw_value, (int, float))
                    and not isinstance(raw_value, bool)
                    else 0.0
                )
                changed, value = self._float_property(
                    label,
                    value,
                    speed=1,
                )
                if changed:
                    self.state.set_gui_element_value(key_name, value)
        else:
            imgui.text_disabled("Position controlled by parent layout")
        for label, key_name in (
            ("Width", "width"),
            ("Height", "height"),
        ):
            raw_value = element.get(key_name, 0)
            value = (
                float(raw_value)
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else 0.0
            )
            changed, value = self._float_property(
                label,
                value,
                speed=1,
            )
            if changed and value >= 1:
                self.state.set_gui_element_value(key_name, value)
        for label, key_name in (
            ("Minimum Width", "min_width"),
            ("Minimum Height", "min_height"),
            ("Grow", "grow"),
        ):
            raw_value = element.get(key_name, 0)
            value = (
                float(raw_value)
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else 0.0
            )
            changed, value = self._float_property(
                label,
                value,
                speed=0.1,
            )
            if changed and value >= 0:
                self.state.set_gui_element_value(key_name, value)
        self._draw_gui_box_values(element, "margin", "Margin")
        for label, key_name, default in (
            ("Offset X", "offset_x", 0.0),
            ("Offset Y", "offset_y", 0.0),
            ("Scale X", "scale_x", 1.0),
            ("Scale Y", "scale_y", 1.0),
            ("Opacity", "opacity", 255.0),
        ):
            raw_value = element.get(key_name, default)
            value = (
                float(raw_value)
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else default
            )
            changed, value = self._float_property(
                label,
                value,
                speed=0.05 if "Scale" in label else 1,
            )
            maximum = 255 if key_name == "opacity" else None
            if changed and value >= 0 and (
                maximum is None or value <= maximum
            ):
                self.state.set_gui_element_value(key_name, value)
        element_type = element.get("type")
        if element_type in {
            "label",
            "button",
            "toggle",
            "checkbox",
            "text_input",
        }:
            changed, text = self._text_property(
                "Text",
                str(element.get("text") or ""),
            )
            if changed:
                self.state.set_gui_element_value("text", text)
        if element_type in {
            "label",
            "button",
            "toggle",
            "checkbox",
            "dropdown",
        }:
            font = element.get("font")
            self._draw_asset_reference_field(
                "Font",
                font if isinstance(font, str) else "",
                f"gui-font:{element.get('id', '')}",
                kind="font",
            )
            raw_font_size = element.get("font_size", 16)
            font_size = (
                float(raw_font_size)
                if isinstance(raw_font_size, (int, float))
                else 16.0
            )
            changed, font_size = self._float_property(
                "Font Size",
                font_size,
                speed=0.5,
                minimum=1,
                maximum=200,
            )
            if changed:
                self.state.set_gui_element_value(
                    "font_size",
                    font_size,
                )
        if element_type in {"toggle", "checkbox"}:
            changed, value = self._bool_property(
                "Value",
                bool(element.get("value", False)),
            )
            if changed:
                self.state.set_gui_element_value("value", value)
        if element_type in {"slider", "progress_bar"}:
            raw_minimum = element.get("minimum", 0)
            raw_maximum = element.get("maximum", 1)
            raw_value = element.get("value", 0)
            minimum = (
                float(raw_minimum)
                if isinstance(raw_minimum, (int, float))
                and not isinstance(raw_minimum, bool)
                else 0.0
            )
            maximum = (
                float(raw_maximum)
                if isinstance(raw_maximum, (int, float))
                and not isinstance(raw_maximum, bool)
                else 1.0
            )
            value = (
                float(raw_value)
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else minimum
            )
            changed, new_minimum = self._float_property(
                "Minimum",
                minimum,
                speed=0.1,
            )
            if changed:
                if new_minimum < maximum:
                    self.state.set_gui_element_value(
                        "minimum",
                        new_minimum,
                    )
                    if value < new_minimum:
                        self.state.set_gui_element_value(
                            "value",
                            new_minimum,
                        )
                    minimum = new_minimum
                else:
                    self.state.error = (
                        f"{str(element_type).replace('_', ' ').title()} "
                        "minimum must be less than maximum"
                    )
            changed, new_maximum = self._float_property(
                "Maximum",
                maximum,
                speed=0.1,
            )
            if changed:
                if new_maximum > minimum:
                    self.state.set_gui_element_value(
                        "maximum",
                        new_maximum,
                    )
                    if value > new_maximum:
                        self.state.set_gui_element_value(
                            "value",
                            new_maximum,
                        )
                    maximum = new_maximum
                else:
                    self.state.error = (
                        f"{str(element_type).replace('_', ' ').title()} "
                        "maximum must be greater than minimum"
                    )
            changed, value = self._slider_property(
                "Value",
                min(maximum, max(minimum, value)),
                minimum,
                maximum,
            )
            if changed:
                self.state.set_gui_element_value("value", value)
        if element_type == "image":
            image = element.get("image")
            self._draw_asset_reference_field(
                "Image",
                image if isinstance(image, str) else "",
                f"gui-image:{element.get('id', '')}",
                kind="image",
            )
            fits = ["contain", "cover", "stretch", "native"]
            current_fit = str(element.get("fit", "contain"))
            fit_index = (
                fits.index(current_fit) if current_fit in fits else 0
            )
            changed, fit_index = self._choice_property(
                "Fit",
                fit_index,
                fits,
            )
            if changed:
                self.state.set_gui_element_value(
                    "fit",
                    fits[fit_index],
                )
        if element_type == "dropdown":
            raw_options = element.get("options", [])
            options = (
                [str(option) for option in raw_options]
                if isinstance(raw_options, list)
                else []
            )
            changed, text = self._text_property(
                "Options (comma separated)",
                ", ".join(options),
            )
            if changed:
                options = [
                    option.strip()
                    for option in text.split(",")
                    if option.strip()
                ]
                self.state.set_gui_element_value(
                    "options",
                    cast(JsonValue, options),
                )
                selected = element.get("selected_index")
                if isinstance(selected, int) and selected >= len(options):
                    self.state.set_gui_element_value(
                        "selected_index",
                        0 if options else None,
                    )
            if options:
                selected = element.get("selected_index", 0)
                selected_index = (
                    selected
                    if isinstance(selected, int)
                    and not isinstance(selected, bool)
                    and 0 <= selected < len(options)
                    else 0
                )
                changed, selected_index = self._choice_property(
                    "Selected",
                    selected_index,
                    options,
                )
                if changed:
                    self.state.set_gui_element_value(
                        "selected_index",
                        selected_index,
                    )
        if element_type in {"scroll", "list_view"}:
            raw_speed = element.get("scroll_speed", 32)
            scroll_speed = (
                float(raw_speed)
                if isinstance(raw_speed, (int, float))
                and not isinstance(raw_speed, bool)
                else 32
            )
            changed, scroll_speed = self._float_property(
                "Scroll Speed",
                scroll_speed,
                minimum=1,
            )
            if changed:
                self.state.set_gui_element_value(
                    "scroll_speed",
                    scroll_speed,
                )
            for label, key_name, default in (
                ("Scrollbar Width", "scrollbar_width", 10),
                ("Minimum Thumb", "minimum_thumb", 24),
            ):
                raw_value = element.get(key_name, default)
                value = (
                    float(raw_value)
                    if isinstance(raw_value, (int, float))
                    and not isinstance(raw_value, bool)
                    else default
                )
                changed, value = self._float_property(
                    label,
                    value,
                    minimum=1,
                )
                if changed:
                    self.state.set_gui_element_value(key_name, value)
        if element_type == "list_view":
            raw_spacing = element.get("spacing", 0)
            spacing = (
                float(raw_spacing)
                if isinstance(raw_spacing, (int, float))
                and not isinstance(raw_spacing, bool)
                else 0
            )
            changed, spacing = self._float_property(
                "Row Spacing",
                spacing,
                minimum=0,
            )
            if changed:
                self.state.set_gui_element_value("spacing", spacing)
        if element_type in {"stack", "vbox", "hbox", "grid"}:
            self._draw_gui_box_values(
                element,
                "padding",
                "Padding",
            )
        if element_type in {"vbox", "hbox"}:
            raw_spacing = element.get("spacing", 0)
            spacing = (
                float(raw_spacing)
                if isinstance(raw_spacing, (int, float))
                and not isinstance(raw_spacing, bool)
                else 0.0
            )
            changed, spacing = self._float_property(
                "Spacing",
                spacing,
                speed=0.5,
            )
            if changed and spacing >= 0:
                self.state.set_gui_element_value("spacing", spacing)
            alignments = ["start", "center", "end"]
            current_alignment = str(
                element.get("alignment", "center")
            )
            alignment_index = (
                alignments.index(current_alignment)
                if current_alignment in alignments
                else 1
            )
            changed, alignment_index = self._choice_property(
                "Alignment",
                alignment_index,
                alignments,
            )
            if changed:
                self.state.set_gui_element_value(
                    "alignment",
                    alignments[alignment_index],
                )
        if element_type == "grid":
            raw_columns = element.get("columns", 2)
            columns = (
                raw_columns
                if isinstance(raw_columns, int)
                and not isinstance(raw_columns, bool)
                else 2
            )
            changed, columns = self._int_property(
                "Columns",
                columns,
                minimum=1,
            )
            if changed and columns > 0:
                self.state.set_gui_element_value("columns", columns)
            for label, key_name in (
                ("Row Spacing", "row_spacing"),
                ("Column Spacing", "column_spacing"),
            ):
                raw_spacing = element.get(key_name, 0)
                spacing = (
                    float(raw_spacing)
                    if isinstance(raw_spacing, (int, float))
                    and not isinstance(raw_spacing, bool)
                    else 0.0
                )
                changed, spacing = self._float_property(
                    label,
                    spacing,
                    speed=0.5,
                )
                if changed and spacing >= 0:
                    self.state.set_gui_element_value(
                        key_name,
                        spacing,
                    )
        if element_type == "anchor":
            horizontal_values = ["left", "center", "right"]
            vertical_values = ["bottom", "center", "top"]
            for label, key_name, values in (
                (
                    "Horizontal Anchor",
                    "horizontal",
                    horizontal_values,
                ),
                (
                    "Vertical Anchor",
                    "vertical",
                    vertical_values,
                ),
            ):
                current = str(element.get(key_name, "center"))
                index = values.index(current) if current in values else 1
                changed, index = self._choice_property(
                    label,
                    index,
                    values,
                )
                if changed:
                    self.state.set_gui_element_value(
                        key_name,
                        values[index],
                    )
            self._draw_gui_box_values(
                element,
                "anchor_margin",
                "Anchor Margin",
            )
        color_keys = {
            "panel": (("Color", "color"),),
            "label": (("Color", "color"),),
            "button": (
                ("Background", "background"),
                ("Hover", "hover"),
                ("Pressed", "pressed"),
                ("Disabled", "disabled"),
                ("Text Color", "text_color"),
            ),
            "toggle": (
                ("Background", "background"),
                ("Hover", "hover"),
                ("Selected", "pressed"),
                ("Disabled", "disabled"),
                ("Text Color", "text_color"),
            ),
            "progress_bar": (
                ("Background", "background"),
                ("Fill", "fill"),
            ),
            "checkbox": (
                ("Box", "box"),
                ("Check", "check"),
                ("Disabled", "disabled"),
                ("Text Color", "text_color"),
            ),
            "dropdown": (
                ("Background", "background"),
                ("Options", "options_color"),
                ("Selected", "selected_color"),
                ("Disabled", "disabled"),
                ("Text Color", "text_color"),
            ),
            "scroll": (
                ("Scrollbar Track", "scrollbar_track"),
                ("Scrollbar Thumb", "scrollbar_thumb"),
            ),
            "list_view": (
                ("Selection", "selected_color"),
                ("Scrollbar Track", "scrollbar_track"),
                ("Scrollbar Thumb", "scrollbar_thumb"),
            ),
            "text_input": (
                ("Background", "color"),
                ("Text Color", "text_color"),
                ("Caret Color", "caret_color"),
            ),
        }.get(str(element_type), ())
        for label, key_name in color_keys:
            color = element.get(key_name)
            changed, color = self._color_widget(label, color)
            if changed:
                self.state.set_gui_element_value(key_name, color)

    def _draw_gui_box_values(
        self,
        element: dict[str, JsonValue],
        key_name: str,
        label: str,
    ) -> None:
        raw_values = element.get(key_name, [0, 0, 0, 0])
        if (
            isinstance(raw_values, list)
            and len(raw_values) == 4
            and all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                for value in raw_values
            )
        ):
            numeric_values = cast(list[float | int], raw_values)
            values = [float(value) for value in numeric_values]
        else:
            values = [0.0, 0.0, 0.0, 0.0]
        changed_any = False
        for index, side in enumerate(
            ("Left", "Bottom", "Right", "Top")
        ):
            changed, value = self._float_property(
                f"{label} {side}",
                values[index],
                speed=0.5,
            )
            if changed and value >= 0:
                values[index] = value
                changed_any = True
        if changed_any:
            self.state.set_gui_element_value(
                key_name,
                cast(JsonValue, values),
            )

    def _draw_layer_inspector(
        self,
        layer: dict[str, JsonValue],
        *,
        show_order_controls: bool = True,
        show_primary_fields: bool = True,
    ) -> None:
        imgui.separator_text("Scene Layer")
        if show_primary_fields:
            changed, name = self._text_property(
                "Name",
                str(layer.get("name") or ""),
            )
            if changed:
                try:
                    self.state.set_layer_value("name", name)
                except ValueError as error:
                    self.state.error = str(error)
            changed, value = self._bool_property(
                "Visible",
                bool(layer.get("visible", True)),
            )
            if changed:
                self.state.set_layer_value("visible", value)
        for label, key_name in (("Enabled", "enabled"),):
            changed, value = self._bool_property(
                label,
                bool(layer.get(key_name, True)),
            )
            if changed:
                self.state.set_layer_value(key_name, value)
        layer_type = layer.get("type", "object")
        imgui.text_disabled(f"Type: {layer_type}")
        if layer.get("default") is True:
            imgui.text_disabled("Default placement layer")
        elif layer_type == "object" and imgui.button("Make Default"):
            self.state.make_selected_layer_default()
        if show_order_controls:
            if imgui.button("Move Up"):
                self.state.move_selected_layer(1)
            imgui.same_line()
            if imgui.button("Move Down"):
                self.state.move_selected_layer(-1)

    def _draw_scene_inspector(self) -> None:
        imgui.separator_text("Scene")
        imgui.separator_text("Appearance")
        background = self.state.document.settings.get(
            "background",
            [30, 30, 36, 255],
        )
        changed, color = self._color_widget(
            "Background",
            background,
            tooltip="The color shown where no game object is drawn.",
        )
        if changed:
            self.state.set_scene_background(color)
        ambient = self.state.document.settings.get(
            "ambient_light",
            [64, 64, 64, 255],
        )
        changed, color = self._color_widget(
            "Ambient Light",
            ambient,
            tooltip=(
                "The base light color applied to the whole scene when "
                "native 2D lighting is active."
            ),
        )
        if changed:
            self.state.set_scene_ambient_light(color)
        imgui.text_disabled(
            "Applied when an enabled 2D light activates native lighting."
        )

        imgui.separator_text("Starting Camera")
        imgui.text_disabled(
            "Used when the scene is loaded."
        )
        camera = self.state.document.settings.get("camera", {})
        if not isinstance(camera, dict):
            camera = {}
        scene_size = self.state.document.settings.get(
            "size",
            list(DEFAULT_SCENE_SIZE),
        )
        default_position = [
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
            default_position = [
                float(scene_width) / 2,
                float(scene_height) / 2,
            ]
        encoded_position = camera.get("position")
        raw_position = (
            encoded_position.get("$vec2")
            if isinstance(encoded_position, dict)
            else None
        )
        if not isinstance(raw_position, list) or len(raw_position) != 2:
            raw_position = default_position
        raw_x = raw_position[0]
        raw_y = raw_position[1]
        position = [
            (
                float(raw_x)
                if isinstance(raw_x, (int, float))
                and not isinstance(raw_x, bool)
                else default_position[0]
            ),
            (
                float(raw_y)
                if isinstance(raw_y, (int, float))
                and not isinstance(raw_y, bool)
                else default_position[1]
            ),
        ]
        position_changed = False
        for index, label in enumerate(("Position X", "Position Y")):
            changed, value = self._float_property(
                label,
                position[index],
                speed=1,
                tooltip=(
                    "The starting camera center in scene coordinates."
                ),
            )
            if changed:
                position[index] = value
                position_changed = True
        if position_changed:
            self.state.set_scene_camera_value(
                "position",
                cast(JsonValue, position),
            )
        zoom = camera.get("zoom", 1)
        changed, zoom = self._float_property(
            "Zoom",
            (
                float(zoom)
                if isinstance(zoom, (int, float))
                and not isinstance(zoom, bool)
                else 1
            ),
            speed=0.01,
            minimum=0.001,
            tooltip=(
                "The starting camera magnification. Values above 1 zoom "
                "in; values below 1 zoom out."
            ),
        )
        if changed:
            self.state.set_scene_camera_value("zoom", zoom)
        rotation = camera.get("rotation", 0)
        changed, rotation = self._float_property(
            "Rotation",
            (
                float(rotation)
                if isinstance(rotation, (int, float))
                and not isinstance(rotation, bool)
                else 0
            ),
            speed=1,
            tooltip="The starting camera rotation in degrees.",
        )
        if changed:
            self.state.set_scene_camera_value("rotation", rotation)

        imgui.separator_text("Presentation")
        resolution = self.state.document.settings.get(
            "size",
            list(DEFAULT_SCENE_SIZE),
        )
        if (
            not isinstance(resolution, list)
            or len(resolution) != 2
            or not all(
                isinstance(value, int)
                and not isinstance(value, bool)
                and value > 0
                for value in resolution
            )
        ):
            resolution = list(DEFAULT_SCENE_SIZE)
        preset_labels = [
            "Custom",
            *[
                f"{width} x {height}"
                for width, height in SCENE_RESOLUTION_PRESETS
            ],
        ]
        valid_resolution = cast(list[int], resolution)
        current_size = (valid_resolution[0], valid_resolution[1])
        preset_index = (
            SCENE_RESOLUTION_PRESETS.index(current_size) + 1
            if current_size in SCENE_RESOLUTION_PRESETS
            else 0
        )
        changed, preset_index = self._choice_property(
            "Resolution Preset",
            preset_index,
            preset_labels,
            tooltip=(
                "Selects a common virtual game resolution. Choose Custom "
                "to enter the width and height yourself."
            ),
        )
        if changed and preset_index > 0:
            resolution = list(
                SCENE_RESOLUTION_PRESETS[preset_index - 1]
            )
            self.state.set_scene_size(*resolution)
        resolution_changed = False
        updated_resolution = cast(list[int], list(resolution))
        for index, label in enumerate(
            ("Resolution Width", "Resolution Height")
        ):
            changed, value = self._int_property(
                label,
                updated_resolution[index],
                minimum=1,
                tooltip=(
                    "The virtual game resolution used by the camera and "
                    "viewport, not necessarily the window's pixel size."
                ),
            )
            if changed:
                updated_resolution[index] = value
                resolution_changed = True
        if resolution_changed:
            self.state.set_scene_size(*updated_resolution)
        imgui.text_disabled(
            "Virtual game resolution used by the camera and viewport."
        )
        presentation = self.state.document.settings.get(
            "presentation",
            {},
        )
        if not isinstance(presentation, dict):
            presentation = {}
        scaling_modes = ["fit", "integer_fit", "fill", "stretch"]
        scaling = str(presentation.get("scaling", "fit"))
        selected_scaling = (
            scaling_modes.index(scaling)
            if scaling in scaling_modes
            else 0
        )
        changed, selected_scaling = self._choice_property(
            "Presentation Scaling",
            selected_scaling,
            scaling_modes,
            tooltip=(
                "Controls how the virtual game resolution fits inside the "
                "window when their aspect ratios differ."
            ),
        )
        if changed:
            self.state.set_presentation_value(
                "scaling",
                scaling_modes[selected_scaling],
            )
        matte = presentation.get(
            "matte_color",
            [30, 30, 36, 255],
        )
        changed, matte = self._color_widget(
            "Presentation Matte",
            matte,
            tooltip=(
                "The color used for empty bars around the game when the "
                "presentation does not fill the window."
            ),
        )
        if changed:
            self.state.set_presentation_value("matte_color", matte)
        for label, key_name, default, tooltip in (
            (
                "Resizable Window",
                "resizable",
                True,
                "Allows players to resize the game window.",
            ),
            (
                "Start Fullscreen",
                "fullscreen",
                False,
                "Starts the game in fullscreen mode.",
            ),
        ):
            changed, value = self._bool_property(
                label,
                bool(presentation.get(key_name, default)),
                tooltip=tooltip,
            )
            if changed:
                self.state.set_presentation_value(key_name, value)

        imgui.separator_text("Physics")
        physics = self.state.document.settings.get("physics", {})
        if not isinstance(physics, dict):
            physics = {}
        gravity = physics.get("gravity", [0, -980])
        if not isinstance(gravity, list) or len(gravity) != 2:
            gravity = [0, -980]
        updated_gravity = list(gravity)
        gravity_changed = False
        for index, label in enumerate(("Gravity X", "Gravity Y")):
            raw_value = gravity[index]
            changed, value = self._float_property(
                label,
                (
                    float(raw_value)
                    if isinstance(raw_value, (int, float))
                    and not isinstance(raw_value, bool)
                    else (0 if index == 0 else -980)
                ),
                speed=10,
            )
            if changed:
                updated_gravity[index] = value
                gravity_changed = True
        if gravity_changed:
            self.state.set_scene_physics_value(
                "gravity",
                cast(JsonValue, updated_gravity),
            )
        damping = physics.get("damping", 1)
        changed, damping = self._float_property(
            "Physics Damping",
            (
                float(damping)
                if isinstance(damping, (int, float))
                and not isinstance(damping, bool)
                else 1
            ),
            speed=0.01,
            minimum=0,
        )
        if changed:
            self.state.set_scene_physics_value("damping", damping)

    def _draw_selected_tile_inspector(self) -> None:
        placed_tile_count = len(self.state.selected_tile_cells)
        tile_ids = list(self.state.selected_placed_tile_ids)
        if not tile_ids:
            tile_ids = self.state.palette_tile_ids
        imgui.separator_text(
            "Selected Tile"
            if (placed_tile_count or len(tile_ids)) == 1
            else f"Selected Tiles ({placed_tile_count or len(tile_ids)})"
        )
        if not tile_ids:
            imgui.text_disabled("Select one or more palette tiles")
            return
        if len(tile_ids) > 1:
            self._draw_selected_tiles_inspector(tile_ids)
            return
        tile_id = tile_ids[0]
        try:
            definition = self.state.tile_definition(
                tile_id,
                create=False,
            )
        except ValueError:
            imgui.text_disabled("Select a palette tile")
            return
        if definition is None:
            definition = {"name": None, "tags": [], "properties": {}}
        changed, name = self._text_property(
            "Tile Name",
            str(definition.get("name") or ""),
        )
        if changed:
            self.state.set_tile_definition_value(
                tile_id,
                "name",
                name or None,
            )
        color = self.state.tile_color(tile_id)
        if color is not None:
            changed, updated_color = self._color_widget(
                "Color",
                cast(JsonValue, color),
            )
            if changed and isinstance(updated_color, list):
                self.state.set_tile_color(
                    tile_id,
                    cast(list[int], updated_color),
                )
                self.preview.reload()
        imgui.separator_text("Collision")
        collision = definition.get("collision")
        changed, has_collision = self._bool_property(
            "Has Collision",
            isinstance(collision, dict),
        )
        if changed:
            collision = (
                {
                    "type": "rectangle",
                    "x": 0,
                    "y": 0,
                    "width": None,
                    "height": None,
                }
                if has_collision
                else None
            )
            self.state.set_tile_definition_value(
                tile_id,
                "collision",
                collision,
            )
        self._draw_tile_collision_editor(
            tile_id,
            collision if isinstance(collision, dict) else None,
        )
        if isinstance(collision, dict):
            collision_types = ["rectangle", "polygon"]
            collision_type = str(collision.get("type", "rectangle"))
            selected_type = (
                collision_types.index(collision_type)
                if collision_type in collision_types
                else 0
            )
            changed, selected_type = self._choice_property(
                "Collision Shape",
                selected_type,
                ["Rectangle", "Polygon"],
            )
            if changed:
                if collision_types[selected_type] == "rectangle":
                    self.state.set_tile_definition_value(
                        tile_id,
                        "collision",
                        {
                            "type": "rectangle",
                            "x": 0,
                            "y": 0,
                            "width": None,
                            "height": None,
                        },
                    )
                else:
                    self.state.set_tile_definition_value(
                        tile_id,
                        "collision",
                        {
                            "type": "polygon",
                            "polygons": [
                                [
                                    [0.0, 0.0],
                                    [1.0, 0.0],
                                    [1.0, 1.0],
                                    [0.0, 1.0],
                                ]
                            ],
                            "generated_from_alpha": False,
                            "alpha_threshold": 255,
                        },
                    )
                self.preview.reload()
                return
            if collision_type == "polygon":
                polygons = collision.get("polygons", [])
                polygon_count = (
                    len(polygons)
                    if isinstance(polygons, list)
                    else 0
                )
                imgui.text_disabled(
                    f"{polygon_count} polygon"
                    f"{'' if polygon_count == 1 else 's'}"
                )
                if imgui.button("Regenerate from Alpha"):
                    try:
                        self.state.generate_tile_collisions(
                            [tile_id],
                            replace=True,
                        )
                        self.preview.reload()
                    except (OSError, ValueError) as error:
                        self.state.error = str(error)
            else:
                self._draw_rectangle_tile_collision(
                    tile_id,
                    collision,
                )
        imgui.separator_text("Navigation")
        navigation = definition.get("navigation")
        changed, has_navigation = self._bool_property(
            "Navigation Metadata",
            isinstance(navigation, dict),
        )
        if changed:
            navigation = (
                {"walkable": True, "cost": 1.0}
                if has_navigation
                else None
            )
            self.state.set_tile_definition_value(
                tile_id,
                "navigation",
                navigation,
            )
        if isinstance(navigation, dict):
            changed, walkable = self._bool_property(
                "Walkable",
                bool(navigation.get("walkable", True)),
            )
            if changed:
                updated = dict(navigation)
                updated["walkable"] = walkable
                self.state.set_tile_definition_value(
                    tile_id,
                    "navigation",
                    cast(JsonValue, updated),
                )
            cost = navigation.get("cost", 1.0)
            changed, cost = self._float_property(
                "Navigation Cost",
                float(cost)
                if isinstance(cost, (int, float))
                else 1,
                speed=0.1,
                minimum=0.1,
            )
            if changed and cost > 0:
                updated = dict(navigation)
                updated["cost"] = cost
                self.state.set_tile_definition_value(
                    tile_id,
                    "navigation",
                    cast(JsonValue, updated),
                )
        imgui.separator_text("Tags and Custom Properties")
        tags = definition.get("tags", [])
        tag_text = ", ".join(
            str(tag) for tag in tags
        ) if isinstance(tags, list) else ""
        changed, tag_text = self._text_property(
            "Tags (comma separated)",
            tag_text,
        )
        if changed:
            self.state.set_tile_definition_value(
                tile_id,
                "tags",
                [
                    tag.strip()
                    for tag in tag_text.split(",")
                    if tag.strip()
                ],
            )
        properties = definition.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        changed, properties = self._property_widget(
            "Custom Properties",
            cast(JsonValue, properties),
            f"tile-properties:{self.state.selected_layer_id}:{tile_id}",
        )
        if changed:
            self.state.set_tile_definition_value(
                tile_id,
                "properties",
                properties,
            )
        imgui.separator_text("Animation")
        animation = definition.get("animation")
        changed, animated = self._bool_property(
            "Animated",
            isinstance(animation, list),
        )
        if changed:
            animation = (
                [{"tile_id": tile_id, "duration": 0.1}]
                if animated
                else None
            )
            self.state.set_tile_definition_value(
                tile_id,
                "animation",
                cast(JsonValue, animation),
            )
        if isinstance(animation, list):
            changed, animation = self._property_widget(
                "Animation Frames",
                cast(JsonValue, animation),
                (
                    f"tile-animation:"
                    f"{self.state.selected_layer_id}:{tile_id}"
                ),
            )
            if changed:
                self.state.set_tile_definition_value(
                    tile_id,
                    "animation",
                    animation,
                )

    def _draw_tile_collision_editor(
        self,
        tile_id: int,
        collision: dict[str, JsonValue] | None,
    ) -> None:
        shape = str(collision.get("type", "rectangle")) if collision else ""
        target = (self.state.selected_layer_id, tile_id, shape)
        if target != self._tile_collision_target:
            self._tile_collision_target = target
            self._tile_collision_drag = None
            self._tile_collision_point = None
            self._tile_collision_add = False
        drag = self._tile_collision_drag
        if drag is not None and (
            drag.revision != self.state.revision or collision != drag.original
        ):
            self._tile_collision_drag = None
            self._tile_collision_point = None
        polygon = shape == "polygon"
        if collision is not None:
            if icon_button(
                icons_fontawesome_6.ICON_FA_SQUARE_PLUS,
                "tile-collision-add-polygon",
                "Add Polygon\nDrag on the tile to add a separate rectangle polygon\n"
                "Existing shapes are preserved; Escape cancels",
                size=imgui.ImVec2(34, 30),
                selected=self._tile_collision_add,
            ):
                self._tile_collision_add = not self._tile_collision_add
                self._tile_collision_drag = None
                self._tile_collision_point = None
            imgui.text_wrapped(
                "Drag gold handles or outlines to edit. Escape cancels a drag."
                + (" Shift+click an edge to add a point; select a point and "
                   "Cmd/Ctrl+Backspace/Delete to remove it." if polygon else "")
            )
        layer = self.state.selected_layer
        if layer is None or layer.get("type") != "tile":
            return
        raw_size = layer.get("cell_size", [1, 1])
        if (
            not isinstance(raw_size, list)
            or len(raw_size) != 2
            or not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value > 0
                for value in raw_size
            )
        ):
            return
        cell_width = float(cast(list[int | float], raw_size)[0])
        cell_height = float(cast(list[int | float], raw_size)[1])
        available = imgui.get_content_region_avail()
        width = max(80.0, min(240.0, available.x))
        height = max(
            80.0,
            min(240.0, width * cell_height / cell_width),
        )
        imgui.invisible_button(
            f"##tile-collision-canvas-{tile_id}",
            imgui.ImVec2(width, height),
        )
        minimum = imgui.get_item_rect_min()
        maximum = imgui.get_item_rect_max()
        hovered = imgui.is_item_hovered()
        clicked = imgui.is_item_clicked(_flag(imgui.MouseButton_.left))
        pointer = imgui.get_mouse_pos()
        normalized = (
            (pointer.x - minimum.x) / width,
            (maximum.y - pointer.y) / height,
        )
        if hovered:
            self._shortcut_context = "tile-collision"
        if imgui.is_key_pressed(imgui.Key.escape) and (
            self._tile_collision_drag is not None or self._tile_collision_add
        ):
            self._tile_collision_drag = None
            self._tile_collision_point = None
            self._tile_collision_add = False
        drag = self._tile_collision_drag
        displayed = drag.latest if drag is not None else collision
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(
            minimum,
            maximum,
            _color(24, 28, 36, 255),
        )
        self._draw_tile_collision_image(
            draw_list,
            tile_id,
            minimum,
            maximum,
        )
        draw_list.add_rect(
            minimum,
            maximum,
            _color(56, 200, 255, 255),
            thickness=2.5,
        )
        adding = self._tile_collision_add
        if adding and collision is not None:
            if hovered or self._tile_collision_drag is not None:
                imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
            if clicked and hovered:
                self._begin_tile_collision_drag("new", normalized, collision)
            drag = self._tile_collision_drag
            if drag is not None and drag.handle == "new":
                current = tuple(max(0.0, min(1.0, value)) for value in normalized)
                left, right = sorted((drag.start[0], current[0]))
                bottom, top = sorted((drag.start[1], current[1]))
                drag.latest = deepcopy(drag.original)
                if (right - left) * width >= 4 and (top - bottom) * height >= 4:
                    polygons = self._collision_polygons(drag.original)
                    if shape == "rectangle":
                        x = number(collision, "x") / cell_width
                        y = number(collision, "y") / cell_height
                        w = collision.get("width")
                        h = collision.get("height")
                        rw = float(w) / cell_width if isinstance(w, (int, float)) else 1.0
                        rh = float(h) / cell_height if isinstance(h, (int, float)) else 1.0
                        polygons = [[[x, y], [x + rw, y], [x + rw, y + rh], [x, y + rh]]]
                    polygons.append([[left, bottom], [right, bottom], [right, top], [left, top]])
                    try:
                        for points in polygons:
                            Collider.decompose(tuple((x, y) for x, y in points))
                        drag.latest = {
                            **drag.original, "type": "polygon",
                            "polygons": cast(JsonValue, polygons), "generated_from_alpha": False,
                        }
                        for field in ("x", "y", "width", "height"):
                            drag.latest.pop(field, None)
                        self.state.error = None
                    except ValueError as error:
                        self.state.error = str(error)
                displayed = drag.latest
                polygon = displayed.get("type") == "polygon"
        if polygon:
            assert displayed is not None
            self._draw_polygon_collision_overlay(
                draw_list,
                tile_id,
                displayed,
                minimum,
                maximum,
                normalized,
                hovered and not adding,
                clicked and not adding,
            )
        elif displayed is not None:
            self._draw_rectangle_collision_overlay(
                draw_list,
                tile_id,
                displayed,
                minimum,
                maximum,
                normalized,
                clicked and not adding,
                cell_width,
                cell_height,
            )
        drag = self._tile_collision_drag
        if drag is not None and not imgui.is_mouse_down(_flag(imgui.MouseButton_.left)):
            self._tile_collision_drag = None
            if drag.latest != drag.original:
                self._commit_tile_collision(tile_id, drag.latest)
                if drag.handle == "new":
                    self._tile_collision_add = False

    def _draw_tile_collision_image(
        self,
        draw_list: imgui.ImDrawList,
        tile_id: int,
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
    ) -> None:
        layer = self.state.selected_layer
        if layer is None:
            return
        tileset = next(
            (
                candidate
                for candidate in self.state.tile_layer_tilesets(layer)
                if isinstance(candidate.get("first_gid"), int)
                and isinstance(candidate.get("tile_count"), int)
                and cast(int, candidate["first_gid"])
                <= tile_id
                < cast(int, candidate["first_gid"])
                + cast(int, candidate["tile_count"])
            ),
            None,
        )
        if tileset is None:
            return
        first_gid = cast(int, tileset["first_gid"])
        local_id = tile_id - first_gid
        kind = tileset.get("type")
        if kind == "color":
            colors = tileset.get("colors")
            if (
                isinstance(colors, list)
                and 0 <= local_id < len(colors)
                and isinstance(colors[local_id], list)
            ):
                raw_channels = cast(list[JsonValue], colors[local_id])
                channels = [
                    int(channel)
                    for channel in raw_channels
                    if isinstance(channel, int)
                    and not isinstance(channel, bool)
                ]
                if len(channels) in {3, 4}:
                    rgba = [*channels, 255][:4]
                    draw_list.add_rect_filled(
                        minimum,
                        maximum,
                        _color(rgba[0], rgba[1], rgba[2], rgba[3]),
                    )
            return
        try:
            if kind == "images":
                images = tileset.get("images")
                if (
                    not isinstance(images, list)
                    or not 0 <= local_id < len(images)
                    or not isinstance(images[local_id], str)
                ):
                    return
                texture = self._atlas_texture(cast(str, images[local_id]))
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
                    or not all(isinstance(value, int) for value in tile_size)
                    or not isinstance(columns, int)
                    or columns <= 0
                ):
                    return
                tile_width, tile_height = cast(list[int], tile_size)
                margin = tileset.get("margin", 0)
                spacing = tileset.get("spacing", 0)
                margin = margin if isinstance(margin, int) else 0
                spacing = spacing if isinstance(spacing, int) else 0
                texture = self._atlas_texture(image)
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
        if texture.id is not None:
            draw_list.add_image(
                imgui.ImTextureRef(texture.id),
                minimum,
                maximum,
                uv_minimum,
                uv_maximum,
                _color(255, 255, 255, 190),
            )

    @staticmethod
    def _collision_polygons(
        collision: dict[str, JsonValue],
    ) -> list[list[list[float]]]:
        raw_polygons = collision.get("polygons")
        if not isinstance(raw_polygons, list):
            return []
        polygons: list[list[list[float]]] = []
        for raw_polygon in raw_polygons:
            if not isinstance(raw_polygon, list):
                continue
            polygon: list[list[float]] = []
            for raw_point in raw_polygon:
                if not isinstance(raw_point, list) or len(raw_point) != 2:
                    continue
                raw_x, raw_y = raw_point
                if (
                    isinstance(raw_x, (int, float))
                    and not isinstance(raw_x, bool)
                    and isinstance(raw_y, (int, float))
                    and not isinstance(raw_y, bool)
                ):
                    polygon.append([float(raw_x), float(raw_y)])
            if len(polygon) >= 3:
                polygons.append(polygon)
        return polygons

    def _set_collision_polygons(
        self,
        tile_id: int,
        collision: dict[str, JsonValue],
        polygons: list[list[list[float]]],
    ) -> None:
        updated = dict(collision)
        updated["polygons"] = cast(JsonValue, polygons)
        updated["generated_from_alpha"] = False
        for polygon in polygons:
            Collider.decompose(tuple((x, y) for x, y in polygon))
        self._commit_tile_collision(tile_id, updated)

    def _commit_tile_collision(
        self, tile_id: int, collision: dict[str, JsonValue],
    ) -> None:
        self.state.set_tile_definition_value(tile_id, "collision", collision, coalesce=False)
        self.preview.reload()

    def _begin_tile_collision_drag(
        self, handle: str, pointer: tuple[float, float], collision: dict[str, JsonValue],
    ) -> None:
        self._tile_collision_drag = TileCollisionDrag(
            handle, pointer, deepcopy(collision), deepcopy(collision), self.state.revision,
        )

    def _delete_tile_collision_point(
        self, tile_id: int, collision: dict[str, JsonValue],
    ) -> None:
        selected = self._tile_collision_point
        if selected is None:
            return
        polygons = self._collision_polygons(collision)
        polygon_index, point_index = selected
        if polygon_index >= len(polygons) or point_index >= len(polygons[polygon_index]):
            self._tile_collision_point = None
            return
        if len(polygons[polygon_index]) <= 3:
            raise ValueError("Tile collision polygon needs at least three vertices")
        del polygons[polygon_index][point_index]
        self._set_collision_polygons(tile_id, collision, polygons)
        self._tile_collision_point = None

    def _draw_polygon_collision_overlay(
        self,
        draw_list: imgui.ImDrawList,
        tile_id: int,
        collision: dict[str, JsonValue],
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
        pointer: tuple[float, float],
        hovered: bool,
        clicked: bool,
    ) -> None:
        polygons = self._collision_polygons(collision)
        if not polygons:
            return
        width = maximum.x - minimum.x
        height = maximum.y - minimum.y

        def screen(point: list[float]) -> imgui.ImVec2:
            return imgui.ImVec2(
                minimum.x + point[0] * width,
                maximum.y - point[1] * height,
            )

        closest_point: tuple[int, int, float] | None = None
        closest_segment: tuple[int, int, tuple[float, float], float] | None = (
            None
        )
        closest_center: tuple[int, float] | None = None
        screen_pointer = (minimum.x + pointer[0] * width, maximum.y - pointer[1] * height)
        color = _color(245, 196, 85, 255)
        for polygon_index, polygon in enumerate(polygons):
            center = screen([
                sum(point[0] for point in polygon) / len(polygon),
                sum(point[1] for point in polygon) / len(polygon),
            ])
            draw_list.add_circle_filled(center, 6, color)
            distance = hypot(screen_pointer[0] - center.x, screen_pointer[1] - center.y)
            if closest_center is None or distance < closest_center[1]:
                closest_center = (polygon_index, distance)
            for point_index, point in enumerate(polygon):
                next_point = polygon[(point_index + 1) % len(polygon)]
                first = screen(point)
                second = screen(next_point)
                draw_list.add_line(
                    first,
                    second,
                    color,
                    2,
                )
                selected = self._tile_collision_point == (polygon_index, point_index)
                draw_list.add_circle_filled(
                    first,
                    7 if selected else 6,
                    _color(255, 245, 210) if selected else color,
                )
                distance = hypot(
                    screen_pointer[0] - first.x,
                    screen_pointer[1] - first.y,
                )
                if closest_point is None or distance < closest_point[2]:
                    closest_point = (
                        polygon_index,
                        point_index,
                        distance,
                    )
                segment_distance, amount = edge_projection(
                    screen_pointer, (first.x, first.y), (second.x, second.y),
                )
                projection = (
                    point[0] + amount * (next_point[0] - point[0]),
                    point[1] + amount * (next_point[1] - point[1]),
                )
                if (
                    closest_segment is None
                    or segment_distance < closest_segment[3]
                ):
                    closest_segment = (
                        polygon_index,
                        point_index,
                        projection,
                        segment_distance,
                    )

        io = imgui.get_io()
        point_hovered = closest_point is not None and closest_point[2] <= _TILE_HANDLE_HIT_RADIUS
        center_hovered = closest_center is not None and closest_center[1] <= _TILE_HANDLE_HIT_RADIUS
        edge_hovered = closest_segment is not None and closest_segment[3] <= 8
        if not self._tile_collision_add and (
            hovered and (point_hovered or center_hovered or edge_hovered)
            or self._tile_collision_drag is not None
        ):
            imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
        if (
            self._tile_collision_drag is None
            and not self._tile_collision_add
            and imgui.is_window_focused()
            and not io.want_text_input
            and not imgui.is_any_item_active()
            and (io.key_ctrl or io.key_super)
            and (imgui.is_key_pressed(imgui.Key.backspace)
                 or imgui.is_key_pressed(imgui.Key.delete))
        ):
            try:
                self._delete_tile_collision_point(tile_id, collision)
            except ValueError as error:
                self.state.error = str(error)
            return
        if clicked and hovered:
            if (
                closest_point is not None
                and point_hovered
            ):
                self._tile_collision_point = closest_point[:2]
                self._begin_tile_collision_drag(
                    f"point:{closest_point[0]}:{closest_point[1]}", pointer, collision,
                )
            elif closest_center is not None and center_hovered and not io.key_shift:
                self._tile_collision_point = None
                self._begin_tile_collision_drag(f"move:{closest_center[0]}", pointer, collision)
            elif (
                closest_segment is not None
                and closest_segment[3] <= 8
            ):
                polygon_index, point_index, projection, _ = closest_segment
                if io.key_shift:
                    polygons[polygon_index].insert(point_index + 1, list(projection))
                    try:
                        self._set_collision_polygons(tile_id, collision, polygons)
                        self._tile_collision_point = (polygon_index, point_index + 1)
                    except ValueError as error:
                        self.state.error = str(error)
                else:
                    self._tile_collision_point = None
                    self._begin_tile_collision_drag(f"move:{polygon_index}", pointer, collision)
            else:
                self._tile_collision_point = None
        drag = self._tile_collision_drag
        if drag is not None and drag.handle != "new" and imgui.is_mouse_down(_flag(imgui.MouseButton_.left)):
            if pointer == drag.start:
                drag.latest = deepcopy(drag.original)
                self.state.error = None
                return
            polygons = self._collision_polygons(drag.original)
            parts = drag.handle.split(":")
            polygon = polygons[int(parts[1])]
            if parts[0] == "point":
                polygon[int(parts[2])] = [max(0.0, min(1.0, value)) for value in pointer]
            else:
                dx = max(-min(p[0] for p in polygon),
                         min(1 - max(p[0] for p in polygon), pointer[0] - drag.start[0]))
                dy = max(-min(p[1] for p in polygon),
                         min(1 - max(p[1] for p in polygon), pointer[1] - drag.start[1]))
                polygon[:] = [[x + dx, y + dy] for x, y in polygon]
            try:
                for polygon in polygons:
                    Collider.decompose(tuple((x, y) for x, y in polygon))
                drag.latest = deepcopy(drag.original)
                if polygons != self._collision_polygons(drag.original):
                    drag.latest.update(
                        polygons=cast(JsonValue, polygons), generated_from_alpha=False,
                    )
                self.state.error = None
            except ValueError as error:
                self.state.error = str(error)

    def _draw_rectangle_collision_overlay(
        self,
        draw_list: imgui.ImDrawList,
        tile_id: int,
        collision: dict[str, JsonValue],
        minimum: imgui.ImVec2,
        maximum: imgui.ImVec2,
        pointer: tuple[float, float],
        clicked: bool,
        cell_width: float,
        cell_height: float,
    ) -> None:
        raw_x = collision.get("x", 0)
        raw_y = collision.get("y", 0)
        raw_width = collision.get("width")
        raw_height = collision.get("height")
        x = float(raw_x) if isinstance(raw_x, (int, float)) else 0.0
        y = float(raw_y) if isinstance(raw_y, (int, float)) else 0.0
        width = (
            float(raw_width)
            if isinstance(raw_width, (int, float))
            else cell_width
        )
        height = (
            float(raw_height)
            if isinstance(raw_height, (int, float))
            else cell_height
        )
        left = max(0.0, min(1.0, x / cell_width))
        bottom = max(0.0, min(1.0, y / cell_height))
        right = max(0.0, min(1.0, (x + width) / cell_width))
        top = max(0.0, min(1.0, (y + height) / cell_height))
        canvas_width, canvas_height = maximum.x - minimum.x, maximum.y - minimum.y
        center = ((left + right) / 2, (bottom + top) / 2)
        handles = {"center": center}
        for dx, dy in ((-1, -1), (0, -1), (1, -1), (1, 0),
                       (1, 1), (0, 1), (-1, 1), (-1, 0)):
            handles[f"size:{dx}:{dy}"] = (
                center[0] + dx * (right - left) / 2,
                center[1] + dy * (top - bottom) / 2,
            )
        color = _color(245, 196, 85, 255)
        draw_list.add_rect(
            imgui.ImVec2(minimum.x + left * canvas_width, maximum.y - top * canvas_height),
            imgui.ImVec2(minimum.x + right * canvas_width, maximum.y - bottom * canvas_height),
            color,
            thickness=2,
        )
        for hx, hy in handles.values():
            draw_list.add_circle_filled(
                imgui.ImVec2(minimum.x + hx * canvas_width, maximum.y - hy * canvas_height),
                6,
                color,
            )
        closest = min(
            handles,
            key=lambda name: hypot(
                (pointer[0] - handles[name][0]) * canvas_width,
                (pointer[1] - handles[name][1]) * canvas_height,
            ),
        )
        handle_distance = hypot(
            (pointer[0] - handles[closest][0]) * canvas_width,
            (pointer[1] - handles[closest][1]) * canvas_height,
        )
        edge_distance = min(edge_projection(
            (pointer[0] * canvas_width, pointer[1] * canvas_height),
            (a[0] * canvas_width, a[1] * canvas_height),
            (b[0] * canvas_width, b[1] * canvas_height),
        )[0] for a, b in (
            ((left, bottom), (right, bottom)), ((right, bottom), (right, top)),
            ((right, top), (left, top)), ((left, top), (left, bottom)),
        ))
        if not self._tile_collision_add and (
            imgui.is_item_hovered() and (
                handle_distance <= _TILE_HANDLE_HIT_RADIUS or edge_distance <= 8
            ) or self._tile_collision_drag is not None
        ):
            imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
        if clicked:
            if handle_distance <= _TILE_HANDLE_HIT_RADIUS:
                self._begin_tile_collision_drag(closest, pointer, collision)
            elif edge_distance <= 8:
                self._begin_tile_collision_drag("center", pointer, collision)
        drag = self._tile_collision_drag
        if (
            drag is None
            or drag.handle == "new"
            or not imgui.is_mouse_down(_flag(imgui.MouseButton_.left))
        ):
            return
        if pointer == drag.start:
            drag.latest = deepcopy(drag.original)
            return
        original = drag.original
        ox = original.get("x", 0)
        oy = original.get("y", 0)
        ow = original.get("width")
        oh = original.get("height")
        x = float(ox) if isinstance(ox, (int, float)) else 0.0
        y = float(oy) if isinstance(oy, (int, float)) else 0.0
        width = float(ow) if isinstance(ow, (int, float)) else cell_width
        height = float(oh) if isinstance(oh, (int, float)) else cell_height
        original_geometry = (x, y, width, height)
        if drag.handle == "center":
            x = max(0, min(cell_width - width, x + (pointer[0] - drag.start[0]) * cell_width))
            y = max(0, min(cell_height - height, y + (pointer[1] - drag.start[1]) * cell_height))
        else:
            _, dx, dy = drag.handle.split(":")
            resized = box_resize_values(
                {"width": width, "height": height, "offset_x": x + width / 2,
                 "offset_y": y + height / 2},
                (max(0.0, min(1.0, pointer[0])) * cell_width,
                 max(0.0, min(1.0, pointer[1])) * cell_height), int(dx), int(dy),
            )
            width = float(resized["width"]) if "width" in resized else width
            height = float(resized["height"]) if "height" in resized else height
            if "offset_x" in resized:
                x = float(resized["offset_x"]) - width / 2
            if "offset_y" in resized:
                y = float(resized["offset_y"]) - height / 2
        drag.latest = deepcopy(original)
        if (x, y, width, height) != original_geometry:
            drag.latest.update(
                x=x, y=y, width=width, height=height, generated_from_alpha=False,
            )

    def _draw_rectangle_tile_collision(
        self,
        tile_id: int,
        collision: dict[str, JsonValue],
    ) -> None:
        for label, key_name in (
            ("Collision X", "x"),
            ("Collision Y", "y"),
        ):
            raw_value = collision.get(key_name, 0)
            changed, value = self._float_property(
                label,
                (
                    float(raw_value)
                    if isinstance(raw_value, (int, float))
                    and not isinstance(raw_value, bool)
                    else 0
                ),
                speed=1,
            )
            if changed:
                updated = dict(collision)
                updated[key_name] = value
                collision = updated
                self.state.set_tile_definition_value(
                    tile_id,
                    "collision",
                    cast(JsonValue, updated),
                )
        custom_size = (
            collision.get("width") is not None
            or collision.get("height") is not None
        )
        changed, custom_size = self._bool_property(
            "Custom Collision Size",
            custom_size,
        )
        if changed:
            updated = dict(collision)
            updated["width"] = 1 if custom_size else None
            updated["height"] = 1 if custom_size else None
            collision = updated
            self.state.set_tile_definition_value(
                tile_id,
                "collision",
                cast(JsonValue, updated),
            )
        if custom_size:
            for label, key_name in (
                ("Collision Width", "width"),
                ("Collision Height", "height"),
            ):
                raw_value = collision.get(key_name, 1)
                changed, value = self._float_property(
                    label,
                    (
                        float(raw_value)
                        if isinstance(raw_value, (int, float))
                        and not isinstance(raw_value, bool)
                        else 1
                    ),
                    speed=1,
                    minimum=0.01,
                )
                if changed and value > 0:
                    updated = dict(collision)
                    updated[key_name] = value
                    collision = updated
                    self.state.set_tile_definition_value(
                        tile_id,
                        "collision",
                        cast(JsonValue, updated),
                    )

    def _draw_selected_tiles_inspector(
        self,
        tile_ids: list[int],
    ) -> None:
        definitions = [
            self.state.tile_definition(tile_id, create=False)
            or {"tags": [], "properties": {}}
            for tile_id in tile_ids
        ]

        def common_value(name: str, default: JsonValue) -> JsonValue:
            values = [
                definition.get(name, default)
                for definition in definitions
            ]
            return (
                deepcopy(values[0])
                if all(value == values[0] for value in values)
                else None
            )

        colors = [
            self.state.tile_color(tile_id)
            for tile_id in tile_ids
        ]
        if all(color is not None for color in colors):
            first_color = colors[0]
            assert first_color is not None
            if any(color != first_color for color in colors[1:]):
                imgui.text_disabled("Color: Mixed")
            changed, updated_color = self._color_widget(
                "Color",
                cast(JsonValue, first_color),
            )
            if changed and isinstance(updated_color, list):
                self.state.set_tile_colors(
                    tile_ids,
                    cast(list[int], updated_color),
                )
                self.preview.reload()

        collision_values = [
            definition.get("collision")
            for definition in definitions
        ]
        collision_count = sum(
            isinstance(value, dict) for value in collision_values
        )
        if 0 < collision_count < len(tile_ids):
            imgui.text_disabled("Collision: Mixed")
        changed, has_collision = self._bool_property(
            "Has Collision",
            collision_count == len(tile_ids),
            "multi-tile-collision",
        )
        if changed:
            self.state.set_tile_definitions_value(
                tile_ids,
                "collision",
                (
                    {
                        "type": "rectangle",
                        "x": 0,
                        "y": 0,
                        "width": None,
                        "height": None,
                    }
                    if has_collision
                    else None
                ),
            )
            self.preview.reload()
            return
        if collision_count:
            if imgui.button("Generate Missing from Alpha"):
                try:
                    self.state.generate_tile_collisions(
                        tile_ids,
                        replace=False,
                    )
                    self.preview.reload()
                except (OSError, ValueError) as error:
                    self.state.error = str(error)
            imgui.same_line()
            if imgui.button("Regenerate All from Alpha"):
                try:
                    self.state.generate_tile_collisions(
                        tile_ids,
                        replace=True,
                    )
                    self.preview.reload()
                except (OSError, ValueError) as error:
                    self.state.error = str(error)

        navigation_values = [
            definition.get("navigation")
            for definition in definitions
        ]
        navigation_count = sum(
            isinstance(value, dict) for value in navigation_values
        )
        if 0 < navigation_count < len(tile_ids):
            imgui.text_disabled("Navigation: Mixed")
        changed, has_navigation = self._bool_property(
            "Navigation Metadata",
            navigation_count == len(tile_ids),
            "multi-tile-navigation",
        )
        if changed:
            self.state.set_tile_definitions_value(
                tile_ids,
                "navigation",
                (
                    {"walkable": True, "cost": 1.0}
                    if has_navigation
                    else None
                ),
            )
            return
        common_navigation = common_value("navigation", None)
        if isinstance(common_navigation, dict):
            changed, walkable = self._bool_property(
                "Walkable",
                bool(common_navigation.get("walkable", True)),
                "multi-tile-walkable",
            )
            if changed:
                updated = dict(common_navigation)
                updated["walkable"] = walkable
                self.state.set_tile_definitions_value(
                    tile_ids,
                    "navigation",
                    cast(JsonValue, updated),
                )
                return
            cost = common_navigation.get("cost", 1.0)
            changed, cost = self._float_property(
                "Navigation Cost",
                float(cost)
                if isinstance(cost, (int, float))
                else 1,
                "multi-tile-navigation-cost",
                speed=0.1,
                minimum=0.1,
            )
            if changed:
                updated = dict(common_navigation)
                updated["cost"] = cost
                self.state.set_tile_definitions_value(
                    tile_ids,
                    "navigation",
                    cast(JsonValue, updated),
                )
                return
        elif navigation_count:
            imgui.text_disabled(
                "Walkable and cost differ; edit to replace all"
            )

        imgui.separator_text("Tags and Custom Properties")
        common_tags = common_value("tags", cast(JsonValue, []))
        if common_tags is None:
            imgui.text_disabled("Tags: Mixed")
            tag_text = ""
        else:
            tag_text = ", ".join(
                str(tag) for tag in common_tags
            ) if isinstance(common_tags, list) else ""
        changed, tag_text = self._text_property(
            "Tags (comma separated)",
            tag_text,
            "multi-tile-tags",
        )
        if changed:
            self.state.set_tile_definitions_value(
                tile_ids,
                "tags",
                [
                    tag.strip()
                    for tag in tag_text.split(",")
                    if tag.strip()
                ],
            )
            return

        common_properties = common_value(
            "properties",
            cast(JsonValue, {}),
        )
        if common_properties is None:
            imgui.text_disabled("Custom Properties: Mixed")
            common_properties = {}
        changed, properties = self._property_widget(
            "Custom Properties",
            common_properties,
            (
                f"multi-tile-properties:"
                f"{self.state.selected_layer_id}:"
                f"{','.join(str(tile_id) for tile_id in tile_ids)}"
            ),
        )
        if changed:
            self.state.set_tile_definitions_value(
                tile_ids,
                "properties",
                properties,
            )

    def _draw_prefab_controls(self) -> None:
        metadata = self.state.selected_prefab_metadata
        if metadata is not None:
            imgui.separator_text("Prefab")
            source = str(metadata.get("source") or "Missing source")
            revision = metadata.get("revision", "?")
            overrides = self.state.selected_prefab_override_count()
            imgui.text_wrapped(source)
            imgui.text_disabled(
                f"Revision {revision}  |  {overrides} override"
                f"{'' if overrides == 1 else 's'}"
            )
            if imgui.button("Apply"):
                self._apply_selected_prefab()
            if imgui.is_item_hovered():
                imgui.set_tooltip(
                    "Write this instance to the prefab and update "
                    "all linked instances"
                )
            imgui.same_line()
            if imgui.button("Select Source"):
                path = (self.state.asset_root / source).resolve()
                if path.is_file():
                    self._selected_asset = path
                    self._selected_asset_directory = path.parent
                    self._shortcut_context = "assets"
                else:
                    self.state.error = f"Prefab source is missing: {source}"
            imgui.same_line()
            if imgui.button("Revert"):
                try:
                    if self.state.revert_selected_prefab():
                        self.preview.reload()
                except (OSError, ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
            imgui.same_line()
            if imgui.button("Unpack"):
                if self.state.unpack_selected_prefab():
                    self.preview.reload()
            imgui.separator()

    def _draw_object_inspector(
        self, record: dict[str, JsonValue]
    ) -> None:
        if not imgui.collapsing_header(
            "Game Object",
            _flag(imgui.TreeNodeFlags_.default_open),
        ):
            return
        self._draw_prefab_controls()
        changed, value = self._text_property(
            "Name",
            str(record.get("name") or ""),
            tooltip=(
                "A descriptive name used to identify this object in the "
                "scene hierarchy."
            ),
        )
        if changed:
            self.state.set_object_value("name", value)
        transform_space = self._effective_transform_space()
        if transform_space == "global":
            x, y, world_rotation, _, _ = (
                self.state.object_world_transform(record)
            )
        else:
            x, y = position(record)
            world_rotation = 0.0
        coordinate_prefix = (
            "World" if transform_space == "global" else "Local"
        )
        changed_x, x = self._float_property(
            f"{coordinate_prefix} X",
            x,
            speed=0.5,
            tooltip=(
                f"The object's horizontal {transform_space} position in "
                "scene units."
            ),
        )
        changed_y, y = self._float_property(
            f"{coordinate_prefix} Y",
            y,
            speed=0.5,
            tooltip=(
                f"The object's vertical {transform_space} position in "
                "scene units."
            ),
        )
        if changed_x or changed_y:
            if transform_space == "global":
                self.state.move_selected_world(x, y)
            else:
                self.state.move_selected(x, y)
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
        if not isinstance(scale, list) or len(scale) != 2:
            scale = [1, 1]
        rotation_value = (
            world_rotation
            if transform_space == "global"
            else float(rotation)
            if isinstance(rotation, (int, float))
            else 0
        )
        changed, value = self._float_property(
            f"{coordinate_prefix} Rotation",
            rotation_value,
            speed=0.5,
            tooltip=(
                f"The object's {transform_space} rotation in degrees."
            ),
        )
        if changed:
            if transform_space == "global":
                self.state.rotate_selected_world(value)
            else:
                self.state.set_object_value("rotation", value)
        for label, key_name, value in (
            ("Scale X", "scale_x", scale[0]),
            ("Scale Y", "scale_y", scale[1]),
        ):
            changed, numeric = self._float_property(
                label,
                float(value) if isinstance(value, (int, float)) else 1,
                speed=0.01,
                tooltip=(
                    "Multiplies the object's size along this axis. "
                    "A value of 1 keeps its original size."
                ),
            )
            if changed:
                self.state.set_object_value(key_name, numeric)
        object_layers = [
            layer
            for layer in self.state.document.layers
            if layer.get("type", "object") == "object"
        ]
        layer_ids = [
            str(layer.get("id"))
            for layer in object_layers
        ]
        layer_names = [
            str(layer.get("name") or "Layer")
            for layer in object_layers
        ]
        selected_layer_index = next(
            (
                index
                for index, identifier in enumerate(layer_ids)
                if identifier == self.state.selected_layer_id
            ),
            0,
        )
        changed, selected_layer_index = self._choice_property(
            "Layer",
            selected_layer_index,
            layer_names,
            tooltip=(
                "The scene layer that contains this object and controls "
                "its broad drawing order."
            ),
        )
        if changed:
            try:
                self.state.move_selected_to_layer(
                    layer_ids[selected_layer_index]
                )
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
        for label, key_name, tooltip in (
            (
                "Enabled",
                "enabled",
                "Controls whether this object's behavior and components "
                "are active.",
            ),
            (
                "Visible",
                "visible",
                "Controls whether this object and its renderers are drawn.",
            ),
            (
                "Render behind parent",
                "render_behind_parent",
                "Draws this child behind its parent instead of in front.",
            ),
        ):
            changed, value = self._bool_property(
                label,
                bool(record.get(key_name, key_name != "render_behind_parent")),
                tooltip=tooltip,
            )
            if changed:
                self.state.set_object_value(key_name, value)
        if record.get("type") != "g.game_object":
            imgui.text_disabled(f"Type: {record.get('type', '')}")
        type_properties = record.get("properties", {})
        if isinstance(type_properties, dict) and type_properties:
            changed, type_properties = self._property_widget(
                "Type Properties",
                cast(JsonValue, type_properties),
                f"object-type-properties:{record.get('id', '')}",
            )
            if changed:
                try:
                    self.state.set_object_properties(type_properties)
                except ValueError as error:
                    self.state.error = str(error)
        script_properties = record.get("script_properties", {})
        if not isinstance(script_properties, dict):
            script_properties = {}
        changed, script_properties = self._property_widget(
            "Properties",
            cast(JsonValue, script_properties),
            f"object-properties:{record.get('id', '')}",
        )
        if changed:
            try:
                self.state.set_object_script_properties(script_properties)
            except ValueError as error:
                self.state.error = str(error)

    def _draw_component_inspector(
        self,
        index: int,
        component: dict[str, JsonValue],
    ) -> bool:
        record = self.state.selected
        identifier = str(record.get("id", "object")) if record is not None else "object"
        imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(10, 10))
        try:
            visible = imgui.begin_child(
                f"##component-card-{identifier}-{index}",
                imgui.ImVec2(0, 0),
                _flag(imgui.ChildFlags_.borders)
                | _flag(imgui.ChildFlags_.auto_resize_y)
                | _flag(imgui.ChildFlags_.always_use_window_padding),
                _flag(imgui.WindowFlags_.no_scrollbar)
                | _flag(imgui.WindowFlags_.no_scroll_with_mouse),
            )
            try:
                return self._draw_component_contents(index, component) if visible else False
            finally:
                imgui.end_child()
        finally:
            imgui.pop_style_var()

    def _draw_component_contents(
        self,
        index: int,
        component: dict[str, JsonValue],
    ) -> bool:
        component_type = str(component.get("type", "Component"))
        component_label = _component_label(component_type)
        properties = component.get("properties")
        enabled = (
            bool(properties.get("enabled", True))
            if isinstance(properties, dict)
            else True
        )
        record = self.state.selected
        record_identifier = (
            str(record.get("id", "object"))
            if record is not None
            else "object"
        )
        header_key = f"{record_identifier}:{component_type}"
        opened = self._component_open.get(header_key, True)
        available_width = max(
            120.0,
            imgui.get_content_region_avail().x,
        )
        spacing = imgui.get_style().item_spacing.x
        control_width = imgui.get_frame_height()
        label_width = max(
            40.0,
            available_width - control_width * 2 - spacing * 2,
        )
        if not enabled:
            imgui.push_style_color(
                imgui.Col_.text,
                imgui.get_style_color_vec4(imgui.Col_.text_disabled),
            )
        imgui.push_style_var(imgui.StyleVar_.button_text_align, imgui.ImVec2(0, 0.5))
        header_clicked = imgui.button(
            (
                icons_fontawesome_6.ICON_FA_CHEVRON_DOWN
                if opened
                else icons_fontawesome_6.ICON_FA_CHEVRON_RIGHT
            )
            + f"  {component_label}  {_component_icon(component_type)}##component-{index}",
            imgui.ImVec2(label_width, 0),
        )
        imgui.pop_style_var()
        if not enabled:
            imgui.pop_style_color()
        context_requested = imgui.is_item_clicked(
            _flag(imgui.MouseButton_.right)
        )
        if header_clicked:
            opened = not opened
            self._component_open[header_key] = opened
        imgui.same_line()
        enabled_changed, enabled = imgui.checkbox(
            f"##component-enabled-{index}",
            enabled,
        )
        context_requested = context_requested or imgui.is_item_clicked(
            _flag(imgui.MouseButton_.right)
        )
        imgui.same_line()
        actions_clicked = icon_button(
            icons_fontawesome_6.ICON_FA_ELLIPSIS,
            f"component-actions-{index}",
            f"{component_label} component — actions",
            size=imgui.ImVec2(control_width, control_width),
        )
        context_requested = context_requested or imgui.is_item_clicked(
            _flag(imgui.MouseButton_.right)
        )
        if enabled_changed:
            try:
                self.state.set_component_value(
                    index,
                    "enabled",
                    enabled,
                )
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
        popup_id = f"Component Actions##component-{index}"
        if context_requested or actions_clicked:
            imgui.open_popup(popup_id)
        if self._draw_component_actions(
            popup_id,
            index,
            component_type,
            component_label,
            properties if isinstance(properties, dict) else None,
        ):
            return True
        if not opened:
            return False
        if not isinstance(properties, dict):
            imgui.text_colored(
                imgui.ImVec4(1, 0.35, 0.3, 1),
                "Invalid component properties",
            )
            return False
        imgui.indent(10)
        try:
            return self._draw_component_properties(index, component_type, properties, record)
        finally:
            imgui.unindent(10)

    def _draw_component_properties(
        self,
        index: int,
        component_type: str,
        properties: dict[str, JsonValue],
        record: dict[str, JsonValue] | None,
    ) -> bool:
        tool = self._component_tool_for_type(component_type)
        if tool is not None:
            active = self._component_tool_is_active(tool, index)
            imgui.begin_disabled(self.preview.mode != "edit" or len(self.state.selected_records) != 1)
            if imgui.button(
                f"{'Done Editing' if active else 'Edit in Viewport'}##component-tool-{index}"
            ):
                try:
                    self._activate_component_tool("transform" if active else tool, index)
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
            imgui.end_disabled()
            if component_type == "g.spline":
                owner = self.preview.selected_runtime_object()
                if owner is not None and index < len(owner.components):
                    spline = owner.components[index]
                    if isinstance(spline, Spline):
                        try:
                            imgui.text_disabled(f"World length: {spline.length:.2f}")
                        except ValueError as error:
                            self.state.error = str(error)
        metadata = inspector_fields(scene_types.component_type_for_name(component_type))
        image_editor = component_type in {"g.sprite_renderer", "g.repeating_image_renderer"} and any(
            field.widget == "image" for field in metadata.values()
        )
        animation_editor = component_type == "g.sprite_animator" and any(
            field.widget == "animation" for field in metadata.values()
        )
        particle_editor = component_type == "g.particle_effect" and any(
            field.widget == "particle" for field in metadata.values()
        )
        if image_editor:
            self._draw_sprite_renderer_resource(
                properties, index, background=component_type == "g.repeating_image_renderer",
            )
        elif animation_editor:
            states = properties.get("states")
            count = len(states) if isinstance(states, list) else 0
            imgui.text_disabled(f"{count} animation state{'s' if count != 1 else ''}")
            imgui.text_disabled(f"Default: {properties.get('initial_state', 'default')}")
            if imgui.button(f"Open Animation Panel##component-{index}"):
                self._show_animation_panel()
        elif particle_editor:
            effect = properties.get("effect")
            current = effect if isinstance(effect, str) else ""
            self._draw_asset_reference_field(
                "Effect",
                current,
                f"particle-effect:{index}",
                kind="particle",
            )
            if imgui.button(f"Open Particles Panel##component-{index}"):
                self._show_particles_panel()
        display_properties = _component_display_properties(
            component_type,
            properties,
        )
        components = record.get("components", []) if record is not None else []
        has_body = isinstance(components, list) and any(
            isinstance(item, dict) and item.get("type") == "g.physics_body"
            for item in components
        )
        is_collider = issubclass(scene_types.component_type_for_name(component_type), Collider)
        inherits_filter = is_collider and has_body and not display_properties.get("override_collision_filtering", False)
        hidden_geometry: set[str] = set()
        if is_collider:
            try:
                active_shape = collider_shape(display_properties)
            except ValueError as error:
                active_shape = None
                imgui.text_colored(imgui.ImVec4(1.0, 0.35, 0.3, 1.0), str(error))
            hidden_geometry = {
                name for shape, names in GEOMETRY_FIELDS.items() if shape != active_shape
                for name in names
            } - set(GEOMETRY_FIELDS.get(active_shape or "", ()))
            if "shape" in display_properties:
                # Shape drives which geometry fields apply, so it leads its group.
                display_properties = {"shape": display_properties["shape"], **display_properties}
        grouped: dict[str | None, list[tuple[str, JsonValue]]] = {}
        for name, value in display_properties.items():
            declaration = metadata.get(name)
            grouped.setdefault(declaration.group if declaration else None, []).append((name, value))
        current_group = None
        group_open = True
        ordered_groups = [grouped.get(None, []), *(items for group, items in grouped.items() if group is not None)]
        for name, value in (item for items in ordered_groups for item in items):
            declaration = metadata.get(name)
            group = declaration.group if declaration else None
            if group != current_group:
                current_group = group
                assert declaration is not None
                flags = _flag(imgui.TreeNodeFlags_.default_open) if declaration.group_expanded else 0
                imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(6, 2))
                imgui.push_style_color(imgui.Col_.header, imgui.ImVec4(0, 0, 0, 0))
                imgui.push_style_color(imgui.Col_.header_hovered, imgui.ImVec4(0.10, 0.14, 0.18, 1))
                imgui.push_style_color(imgui.Col_.header_active, imgui.ImVec4(0.13, 0.18, 0.23, 1))
                try:
                    group_open = imgui.collapsing_header(f"{group}##component-{index}-group-{group}", flags)
                finally:
                    imgui.pop_style_color(3)
                    imgui.pop_style_var()
            if not group_open:
                continue
            if name == "enabled":
                continue
            if is_collider:
                if name == "override_collision_filtering" and not has_body:
                    continue
                if inherits_filter and name in ("categories", "mask", "group"):
                    continue
                if name in hidden_geometry:
                    continue
            field = metadata.get(name)
            if field is not None and field.widget == "font":
                self._draw_asset_reference_field(
                    field.label or _component_field_label(name),
                    value if isinstance(value, str) else "",
                    f"font:{index}",
                    kind="font",
                )
                continue
            if field is not None and (
                (image_editor and field.widget == "image")
                or (animation_editor and field.widget == "animation")
                or (particle_editor and field.widget == "particle")
            ):
                continue
            optional_color_default = (
                list(field.optional_color)
                if field is not None and field.optional_color is not None else None
            )
            can_disable_color = True
            if optional_color_default is not None:
                assert field is not None
                can_disable_color = (
                    field.counterpart is None
                    or display_properties.get(field.counterpart) is not None
                )
            choices = field.choices if field is not None else ()
            if is_collider and name == "shape":
                choices = choices or COLLIDER_SHAPES
            field_label = field.label if field is not None and field.label else _component_field_label(name)
            imgui.begin_group()
            if choices:
                selected = (
                    choices.index(str(value))
                    if str(value) in choices
                    else 0
                )
                changed, selected = self._choice_property(
                    (
                        f"{field_label}"
                        f"##component-{index}-{name}"
                    ),
                    selected,
                    list(choices),
                    tooltip=field.tooltip if field is not None else None,
                )
                new_value: JsonValue = choices[selected]
            else:
                changed, new_value = self._property_widget(
                    (
                        f"{field_label}"
                        f"##component-{index}-{name}"
                    ),
                    value,
                    f"{index}:{name}",
                    optional_color_default=optional_color_default,
                    can_disable_color=can_disable_color,
                    field=field,
                )
            imgui.end_group()
            if field is not None and field.tooltip and imgui.is_item_hovered():
                imgui.set_tooltip(field.tooltip)
            if name == "override_collision_filtering" and inherits_filter:
                imgui.text_disabled("Inherits PhysicsBody filtering")
            if changed:
                try:
                    if is_collider and name == "shape":
                        self.state.set_collider_shape(index, str(new_value))
                    else:
                        self.state.set_component_value(
                            index,
                            name,
                            new_value,
                        )
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
        return False

    def _open_component_script(self, component_type: str) -> None:
        try:
            script_type = scene_types.component_type_for_name(component_type)
            if not issubclass(script_type, Script):
                raise ValueError(f"{component_type} is not a script component")
            path = open_script_in_editor(script_type)
        except (ValueError, SceneFormatError) as error:
            self.state.error = str(error)
        else:
            self.state.status = f"Opened {path.name} in VS Code"
            self.state.error = None

    def _draw_component_actions(
        self,
        popup_id: str,
        index: int,
        component_type: str,
        component_label: str,
        properties: dict[str, JsonValue] | None,
    ) -> bool:
        if not imgui.begin_popup(popup_id):
            return False
        removed = False
        if issubclass(scene_types.component_type_for_name(component_type), Script):
            if imgui.menu_item("Open Script in VS Code", "", False)[0]:
                self._open_component_script(component_type)
            imgui.separator()
        if imgui.menu_item("Reset to Defaults", "", False)[0]:
            try:
                self.state.reset_component(index)
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
        if imgui.menu_item(
            "Copy Component",
            "",
            False,
            properties is not None,
        )[0] and properties is not None:
            self._component_clipboard = {
                "type": component_type,
                "properties": deepcopy(properties),
            }
            self.state.status = f"Copied {component_label}"
            self.state.error = None
        clipboard = self._component_clipboard
        clipboard_properties = (
            clipboard.get("properties")
            if clipboard is not None
            and clipboard.get("type") == component_type
            else None
        )
        can_paste = isinstance(clipboard_properties, dict)
        if imgui.menu_item(
            "Paste Component Values",
            "",
            False,
            can_paste,
        )[0] and isinstance(clipboard_properties, dict):
            try:
                self.state.replace_component_properties(
                    index,
                    component_type,
                    clipboard_properties,
                )
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
        imgui.separator()
        try:
            removal_error = self.state.component_removal_error(index)
        except SceneFormatError as error:
            removal_error = str(error)
        imgui.begin_disabled(removal_error is not None)
        remove_selected = imgui.menu_item(
            "Remove Component",
            "",
            False,
        )[0]
        if (
            removal_error is not None
            and imgui.is_item_hovered(
                _flag(imgui.HoveredFlags_.allow_when_disabled)
            )
        ):
            imgui.set_tooltip(removal_error)
        imgui.end_disabled()
        if remove_selected:
            try:
                self.state.remove_component(index)
                removed = True
            except (ValueError, SceneFormatError) as error:
                self.state.error = str(error)
        imgui.end_popup()
        return removed

    def _draw_sprite_renderer_resource(
        self,
        properties: dict[str, JsonValue],
        component_index: int,
        *,
        background: bool = False,
    ) -> None:
        stored = str(properties.get("image") or "")
        key_name = f"background-image:{component_index}" if background else f"sprite-image:{component_index}"
        current = self._json_buffers.setdefault(key_name, stored)
        if stored != current and not imgui.is_any_item_active():
            current = stored
            self._json_buffers[key_name] = stored
        self._draw_asset_reference_field(
            "Image",
            current,
            key_name,
            kind="image",
        )

    def _property_widget(
        self,
        label: str,
        value: JsonValue,
        buffer_key: str,
        *,
        optional_color_default: list[int] | None = None,
        can_disable_color: bool = True,
        field: InspectorField | None = None,
    ) -> tuple[bool, JsonValue]:
        if field is not None and field.widget == "mask":
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF:
                self.state.error = f"{label.split('##', 1)[0]}: mask must be an unsigned 32-bit integer"
                return False, value
            result = MaskField(label, buffer_key, bit_names=field.bit_names).draw(value)
            return result.changed, result.value
        if optional_color_default is not None:
            return self._optional_color_widget(
                label,
                value,
                buffer_key,
                optional_color_default,
                can_disable=can_disable_color,
            )
        if (field is not None and field.widget == "color") or self._is_color_property(label, value):
            return self._color_widget(label, value)
        if isinstance(value, dict) and set(value) == {"$vec2"}:
            coordinates = value["$vec2"]
            if (
                not isinstance(coordinates, list)
                or len(coordinates) != 2
                or not all(
                    isinstance(item, (int, float)) and not isinstance(item, bool)
                    for item in coordinates
                )
            ):
                self.state.error = f"{label.split('##', 1)[0]}: invalid Vec2 value"
                return False, value
            x, y = coordinates
            assert isinstance(x, (int, float)) and isinstance(y, (int, float))
            result = Vec2Field(
                label, buffer_key,
                minimum=field.minimum if field and field.minimum is not None else -_FLOAT_FIELD_LIMIT,
                maximum=field.maximum if field and field.maximum is not None else _FLOAT_FIELD_LIMIT,
            ).draw(Vec2(x, y))
            return result.changed, {"$vec2": [result.value.x, result.value.y]}
        if isinstance(value, dict) and not any(key.startswith("$") for key in value):
            return self._json_properties_widget(label, value, buffer_key)
        if isinstance(value, bool):
            result = BooleanField(label, buffer_key).draw(value)
            return result.changed, result.value
        if isinstance(value, int):
            if not -_INTEGER_FIELD_LIMIT <= value <= _INTEGER_FIELD_LIMIT:
                stored = str(value)
                current = self._json_buffers.setdefault(buffer_key, stored)
                if current != stored and not imgui.is_any_item_active():
                    current = stored
                text = TextField(label, buffer_key, submit_on_enter=True).draw(current)
                self._json_buffers[buffer_key] = text.value
                if not text.changed:
                    return False, value
                try:
                    number = int(text.value)
                except ValueError:
                    self.state.error = f"{label.split('##', 1)[0]}: enter an integer"
                    return False, value
                return True, number
            result = IntegerField(
                label, buffer_key,
                minimum=int(field.minimum) if field and field.minimum is not None else -_INTEGER_FIELD_LIMIT,
                maximum=int(field.maximum) if field and field.maximum is not None else _INTEGER_FIELD_LIMIT,
            ).draw(value)
            return result.changed, result.value
        if isinstance(value, float):
            result = FloatField(
                label, buffer_key,
                minimum=field.minimum if field and field.minimum is not None else -_FLOAT_FIELD_LIMIT,
                maximum=field.maximum if field and field.maximum is not None else _FLOAT_FIELD_LIMIT,
            ).draw(value)
            return result.changed, result.value
        if isinstance(value, str):
            result = TextField(label, buffer_key).draw(value)
            return result.changed, result.value
        serialized = json.dumps(value)
        current = self._json_buffers.setdefault(buffer_key, serialized)
        if serialized != current and not imgui.is_any_item_active():
            current = serialized
            self._json_buffers[buffer_key] = current
        field_result = TextField(
            label,
            buffer_key,
            submit_on_enter=True,
        ).draw(current)
        submitted = field_result.changed
        current = field_result.value
        self._json_buffers[buffer_key] = current
        if not submitted:
            return False, value
        try:
            parsed = EditorState.parse_property(current, value)
        except (ValueError, json.JSONDecodeError) as error:
            self.state.error = f"{label.split('##', 1)[0]}: {error}"
            return False, value
        return True, parsed

    def _json_properties_widget(
        self,
        label: str,
        value: dict[str, JsonValue],
        buffer_key: str,
    ) -> tuple[bool, JsonValue]:
        if label.split("##", 1)[0]:
            imgui.text(label.split("##", 1)[0])
        flags = _flag(imgui.TableFlags_.sizing_stretch_prop)
        if imgui.begin_table(f"##json-properties-{buffer_key}", 4, flags):
            try:
                imgui.table_setup_column("Key", init_width_or_weight=2)
                imgui.table_setup_column("Type", init_width_or_weight=1)
                imgui.table_setup_column("Value", init_width_or_weight=2)
                imgui.table_setup_column("", _flag(imgui.TableColumnFlags_.width_fixed), 24)
                imgui.table_headers_row()
                for key, item in value.items():
                    identifier = f"{buffer_key}:{json.dumps(key)}"
                    imgui.table_next_row()
                    imgui.table_set_column_index(0)
                    key_buffer = f"json-key:{identifier}"
                    draft = self._json_buffers.setdefault(key_buffer, key)
                    result = TextField(
                        f"##key-{identifier}", key_buffer, submit_on_enter=True,
                    ).draw(draft)
                    self._json_buffers[key_buffer] = result.value
                    if result.changed and result.value != key:
                        if not result.value.strip():
                            self.state.error = "Property key must not be empty"
                        elif result.value in value:
                            self.state.error = f"Property key already exists: {result.value}"
                        else:
                            renamed = {
                                result.value if old_key == key else old_key: old_value
                                for old_key, old_value in value.items()
                            }
                            self._json_buffers.pop(key_buffer, None)
                            return True, renamed
                    imgui.table_set_column_index(1)
                    types = ["string", "int", "float", "bool"]
                    defaults: tuple[JsonValue, ...] = ("", 0, 0.0, False)
                    if isinstance(item, bool):
                        selected = 3
                    elif isinstance(item, int):
                        selected = 1
                    elif isinstance(item, float):
                        selected = 2
                    elif isinstance(item, str):
                        selected = 0
                    else:
                        selected = 4
                        types.append("JSON")
                    type_result = ChoiceField(
                        f"##type-{identifier}", types, f"json-type:{identifier}",
                    ).draw(selected)
                    if type_result.changed and type_result.value != selected:
                        updated = dict(value)
                        updated[key] = defaults[type_result.value]
                        return True, updated
                    imgui.table_set_column_index(2)
                    changed, new_value = self._property_widget(
                        f"##value-{identifier}", item, f"json-value:{identifier}",
                    )
                    if changed:
                        if isinstance(new_value, float) and not isfinite(new_value):
                            self.state.error = "Property value must be finite"
                        else:
                            updated = dict(value)
                            updated[key] = new_value
                            return True, updated
                    imgui.table_set_column_index(3)
                    if icon_button(
                        icons_fontawesome_6.ICON_FA_TRASH,
                        f"json-remove:{identifier}",
                        "Remove property",
                    ):
                        updated = dict(value)
                        del updated[key]
                        self._json_buffers.pop(key_buffer, None)
                        return True, updated
            finally:
                imgui.end_table()
        if imgui.button(f"Add Property##json-add:{buffer_key}"):
            key = "property"
            suffix = 2
            while key in value:
                key = f"property{suffix}"
                suffix += 1
            return True, {**value, key: ""}
        return False, value

    def _optional_color_widget(
        self,
        label: str,
        value: JsonValue,
        buffer_key: str,
        default: list[int],
        *,
        can_disable: bool,
    ) -> tuple[bool, JsonValue]:
        stored = self._optional_color_values.get(buffer_key, default)
        current = value if isinstance(value, list) else None
        if current is not None:
            if not all(
                isinstance(channel, (int, float))
                and not isinstance(channel, bool)
                and 0 <= channel <= 255
                for channel in current
            ):
                return False, value
            stored = [
                round(channel)
                for channel in cast(list[int | float], current)
            ]
            if len(stored) not in {3, 4}:
                return False, value
            self._optional_color_values[buffer_key] = stored
        has_alpha = len(stored) == 4
        normalized = [
            channel / 255
            for channel in (
                stored if has_alpha else [*stored, 255]
            )
        ]
        result = OptionalColorField(
            label,
            buffer_key,
            fallback=normalized,
            has_alpha=has_alpha,
            can_disable=can_disable,
        ).draw(
            normalized if current is not None else None
        )
        if not result.changed:
            return False, value
        if result.value is None:
            return True, None
        converted = [
            max(0, min(255, round(channel * 255)))
            for channel in result.value
        ]
        if not has_alpha:
            converted = converted[:3]
        self._optional_color_values[buffer_key] = converted
        return True, cast(JsonValue, converted)

    @staticmethod
    def _text_property(
        label: str,
        value: str,
        identifier: str | None = None,
        *,
        tooltip: str | None = None,
    ) -> tuple[bool, str]:
        field = TextField(label, identifier)
        result = (
            field.draw(value)
            if tooltip is None
            else field.draw(value, tooltip=tooltip)
        )
        return result.changed, result.value

    @staticmethod
    def _bool_property(
        label: str,
        value: bool,
        identifier: str | None = None,
        *,
        tooltip: str | None = None,
    ) -> tuple[bool, bool]:
        field = BooleanField(label, identifier)
        result = (
            field.draw(value)
            if tooltip is None
            else field.draw(value, tooltip=tooltip)
        )
        return result.changed, result.value

    @staticmethod
    def _int_property(
        label: str,
        value: int,
        identifier: str | None = None,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
        tooltip: str | None = None,
    ) -> tuple[bool, int]:
        field_minimum = (
            minimum if minimum is not None else -_INTEGER_FIELD_LIMIT
        )
        field_maximum = (
            maximum if maximum is not None else _INTEGER_FIELD_LIMIT
        )
        if minimum is None and maximum is None:
            field_minimum = 0
            field_maximum = 0
        field = IntegerField(
            label,
            identifier,
            minimum=field_minimum,
            maximum=field_maximum,
        )
        result = (
            field.draw(value)
            if tooltip is None
            else field.draw(value, tooltip=tooltip)
        )
        return result.changed, result.value

    @staticmethod
    def _float_property(
        label: str,
        value: float,
        identifier: str | None = None,
        *,
        speed: float = 0.1,
        minimum: float | None = None,
        maximum: float | None = None,
        tooltip: str | None = None,
    ) -> tuple[bool, float]:
        field_minimum = (
            minimum if minimum is not None else -_FLOAT_FIELD_LIMIT
        )
        field_maximum = (
            maximum if maximum is not None else _FLOAT_FIELD_LIMIT
        )
        if minimum is None and maximum is None:
            field_minimum = 0
            field_maximum = 0
        field = FloatField(
            label,
            identifier,
            speed=speed,
            minimum=field_minimum,
            maximum=field_maximum,
        )
        result = (
            field.draw(value)
            if tooltip is None
            else field.draw(value, tooltip=tooltip)
        )
        return result.changed, result.value

    @staticmethod
    def _slider_property(
        label: str,
        value: float,
        minimum: float,
        maximum: float,
        identifier: str | None = None,
        *,
        tooltip: str | None = None,
    ) -> tuple[bool, float]:
        field = SliderFloatField(
            label,
            identifier,
            minimum=minimum,
            maximum=maximum,
        )
        result = (
            field.draw(value)
            if tooltip is None
            else field.draw(value, tooltip=tooltip)
        )
        return result.changed, result.value

    @staticmethod
    def _choice_property(
        label: str,
        index: int,
        values: list[str],
        identifier: str | None = None,
        *,
        tooltip: str | None = None,
    ) -> tuple[bool, int]:
        field = ChoiceField(
            label,
            values,
            identifier,
        )
        result = (
            field.draw(index)
            if tooltip is None
            else field.draw(index, tooltip=tooltip)
        )
        return result.changed, result.value

    @staticmethod
    def _is_color_property(label: str, value: JsonValue) -> bool:
        name = label.split("##", 1)[0].replace("_", " ").lower()
        return (
            (
                "color" in name
                or name in {
                    "fill",
                    "outline",
                    "background",
                    "hover",
                    "pressed",
                    "disabled",
                }
            )
            and isinstance(value, list)
            and len(value) in {3, 4}
            and all(
                isinstance(channel, (int, float))
                and not isinstance(channel, bool)
                and 0 <= channel <= 255
                for channel in value
            )
        )

    @staticmethod
    def _color_widget(
        label: str,
        value: JsonValue,
        *,
        tooltip: str | None = None,
    ) -> tuple[bool, JsonValue]:
        if not isinstance(value, list) or len(value) not in {3, 4}:
            return False, value
        has_alpha = len(value) == 4
        channels = [
            float(channel) / 255
            for channel in (
                value if has_alpha else [*value, 255]
            )
            if isinstance(channel, (int, float))
        ]
        if len(channels) != 4:
            return False, value
        flags = (
            _flag(imgui.ColorEditFlags_.uint8)
            | _flag(imgui.ColorEditFlags_.display_hex)
            | _flag(imgui.ColorEditFlags_.alpha_preview_half)
        )
        if not has_alpha:
            flags |= _flag(imgui.ColorEditFlags_.no_alpha)
        del flags
        field = ColorField(
            label,
            label,
            has_alpha=has_alpha,
        )
        result = (
            field.draw(channels)
            if tooltip is None
            else field.draw(channels, tooltip=tooltip)
        )
        changed = result.changed
        channels = result.value
        if not changed:
            return False, value
        converted = [
            max(0, min(255, round(channel * 255)))
            for channel in channels
        ]
        return True, cast(
            JsonValue,
            converted if has_alpha else converted[:3],
        )

    def _draw_add_component(self) -> None:
        try:
            available = self.state.available_component_types
        except SceneFormatError as error:
            self.state.error = str(error)
            return
        imgui.separator()
        if not available:
            imgui.text_disabled("No additional components available")
        add_clicked = imgui.button(
            "Add Component",
            imgui.ImVec2(-1, 0),
        )
        if add_clicked:
            imgui.open_popup("Add Component Menu")
        if not imgui.begin_popup("Add Component Menu"):
            return
        self._reload_changed_scripts()
        if self._component_clipboard is not None:
            self._draw_paste_component_action()
            imgui.separator()
        self._draw_component_type_menu()
        imgui.end_popup()

    def _draw_paste_component_action(self) -> None:
        clipboard = self._component_clipboard
        paste_error = "Copy a component first"
        if clipboard is not None:
            try:
                paste_error = self.state.component_paste_error(clipboard)
            except SceneFormatError as error:
                paste_error = str(error)
        can_paste = paste_error is None
        if imgui.menu_item("Paste Component as New", "", False, can_paste)[0]:
            if clipboard is not None:
                try:
                    self.state.paste_component(clipboard)
                except (ValueError, SceneFormatError) as error:
                    self.state.error = str(error)
        if not can_paste and imgui.is_item_hovered(
            _flag(imgui.HoveredFlags_.allow_when_disabled)
        ):
            imgui.set_tooltip(paste_error or "Component cannot be added")

    def _draw_component_type_menu(self) -> None:
        try:
            available = self.state.available_component_types
        except SceneFormatError as error:
            self.state.error = str(error)
            return
        groups: dict[str, list[str]] = {}
        for component_type in available:
            groups.setdefault(
                _component_group(component_type),
                [],
            ).append(component_type)
        preferred_groups = (
            "Renderers",
            "Physics",
            "Movement",
            "Camera",
            "Animation",
            "Scripts",
            "Project Components",
            "Other",
        )
        for group_name in (
            *preferred_groups,
            *sorted(set(groups) - set(preferred_groups)),
        ):
            component_types = groups.get(group_name, [])
            if (
                not component_types and group_name != "Scripts"
            ) or not imgui.begin_menu(group_name):
                continue
            if group_name == "Scripts":
                if imgui.menu_item("New Script", "", False)[0]:
                    self._begin_create_python_script(attach_to_selected=True)
                if component_types:
                    imgui.separator()
                elif self._project_trust_pending:
                    imgui.text_disabled("Trust and load project scripts first")
            for component_type in component_types:
                if component_type == "g.particle_effect":
                    if imgui.begin_menu(
                        _component_label(component_type)
                    ):
                        if imgui.menu_item(
                            "Empty Effect",
                            "",
                            False,
                        )[0]:
                            self.state.add_component(component_type)
                        imgui.separator()
                        for preset in PARTICLE_PRESETS:
                            if imgui.menu_item(
                                preset.replace("_", " ").title(),
                                "",
                                False,
                            )[0]:
                                self._add_particle_component(preset)
                        imgui.end_menu()
                    continue
                if imgui.menu_item(
                    _component_label(component_type),
                    "",
                    False,
                )[0]:
                    try:
                        self.state.add_component(component_type)
                    except (ValueError, SceneFormatError) as error:
                        self.state.error = str(error)
            imgui.end_menu()

    def _add_particle_component(self, preset: str) -> None:
        try:
            path = self._create_particle_asset_from_preset(preset)
            self.state.add_component("g.particle_effect")
            record = self.state.selected
            if record is None:
                raise ValueError("select a game object first")
            components = record.get("components")
            if not isinstance(components, list) or not components:
                raise SceneFormatError(
                    "object components must be an array"
                )
            component_index = len(components) - 1
            relative = path.relative_to(
                self.state.asset_root
            ).as_posix()
            self.state.set_component_value(
                component_index,
                "effect",
                relative,
            )
        except (OSError, ValueError, SceneFormatError) as error:
            self.state.error = str(error)
