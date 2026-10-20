from __future__ import annotations

from imgui_bundle import icons_fontawesome_6, imgui

from ..ui import flag as _flag, icon_button
from .base import EditorPanel


class ToolsPanel(EditorPanel):
    def _draw_viewport_tools(self) -> None:
        toolbar_visible = imgui.begin_child(
            "##ViewportTools",
            imgui.ImVec2(0, 42),
            _flag(imgui.ChildFlags_.frame_style),
            _flag(imgui.WindowFlags_.no_scrollbar),
        )
        if toolbar_visible:
            layer = self.state.selected_layer
            if layer is None:
                imgui.text_disabled("Select a layer to show its tools")
            elif self.state.mixed_layer_selection and self.preview.mode == "edit":
                self._draw_mixed_tools()
            elif layer.get("type", "object") == "object" and self._viewport_tool != "transform":
                imgui.text_disabled(self._component_tool_help())
            elif layer.get("type", "object") == "object":
                button_size = imgui.ImVec2(34, 30)
                for icon, identifier, tooltip, tool in (
                    (
                        icons_fontawesome_6
                        .ICON_FA_ARROWS_UP_DOWN_LEFT_RIGHT,
                        "transform-move",
                        "Move (W)\nTranslate the selection",
                        "move",
                    ),
                    (
                        icons_fontawesome_6.ICON_FA_ROTATE,
                        "transform-rotate",
                        "Rotate (E)\nRotate the selection",
                        "rotate",
                    ),
                    (
                        icons_fontawesome_6.ICON_FA_MAXIMIZE,
                        "transform-scale",
                        "Scale (R)\nScale in local space",
                        "scale",
                    ),
                ):
                    if icon_button(
                        icon,
                        identifier,
                        tooltip,
                        size=button_size,
                        selected=self._transform_tool == tool,
                    ):
                        self._transform_tool = tool
                    imgui.same_line()
                effective_space = self._effective_transform_space()
                scale_locked = self._transform_tool == "scale"
                multi_locked = len(self.state.selected_records) > 1
                space_button_size = imgui.ImVec2(92, 30)
                space_controls_width = (
                    space_button_size.x * 2
                    + imgui.get_style().item_spacing.x
                )
                space_controls_x = max(
                    imgui.get_cursor_pos_x() + 12,
                    imgui.get_window_width()
                    - space_controls_width
                    - imgui.get_style().window_padding.x,
                )
                imgui.set_cursor_pos_x(space_controls_x)
                imgui.begin_disabled(scale_locked)
                if icon_button(
                    (
                        f"{icons_fontawesome_6.ICON_FA_GLOBE}"
                        "  Global"
                    ),
                    "transform-global",
                    (
                        "Global Space\n"
                        "Align move and rotate handles with world axes"
                        if not scale_locked
                        else "Global Space unavailable\n"
                        "Scale currently supports Local Space only"
                    ),
                    size=space_button_size,
                    selected=effective_space == "global",
                ):
                    self._transform_space = "global"
                imgui.end_disabled()
                imgui.same_line()
                imgui.begin_disabled(scale_locked or multi_locked)
                local_tooltip = (
                    "Local Space unavailable\n"
                    "Multiple selections use Global Space"
                    if multi_locked
                    else "Local Space unavailable\n"
                    "Scale already uses Local Space"
                    if scale_locked
                    else "Local Space\n"
                    "Align move and rotate handles with the selected object"
                )
                if icon_button(
                    (
                        f"{icons_fontawesome_6.ICON_FA_CUBE}"
                        "  Local"
                    ),
                    "transform-local",
                    local_tooltip,
                    size=space_button_size,
                    selected=effective_space == "local",
                ):
                    self._transform_space = "local"
                imgui.end_disabled()
            elif (
                layer.get("type") == "tile"
                and self.preview.mode == "edit"
            ):
                self._draw_tile_tools()
            else:
                imgui.text_disabled("No viewport tools for this layer")
        imgui.end_child()

    def _draw_mixed_tools(self) -> None:
        for icon, name, tooltip, tool in (
            (icons_fontawesome_6.ICON_FA_OBJECT_GROUP, "mixed-select",
             "Rectangle Select (Q)\nSelect tiles and objects in the selected layers", "select"),
            (icons_fontawesome_6.ICON_FA_ARROWS_UP_DOWN_LEFT_RIGHT, "mixed-move",
             "Move Selection (W)\nMove tiles and objects together on the tile grid", "move"),
        ):
            if icon_button(icon, name, tooltip, size=imgui.ImVec2(34, 30),
                           selected=self.state.tile_tool == tool):
                self.state.tile_tool = tool
            imgui.same_line()
        count = sum(len(cells) for cells in self.state.selected_tile_groups.values())
        imgui.text_disabled(f"{count} tiles, {len(self.state.selected_records)} objects")

    def _draw_tile_tools(self) -> None:
        button_size = imgui.ImVec2(34, 30)
        picker_active = (
            self.state.tile_tool == "picker"
            or (
                self.state.tile_tool == "pencil"
                and self._shift_pick_held
            )
        )
        if icon_button(
            (
                icons_fontawesome_6.ICON_FA_EYE_DROPPER
                if picker_active
                else icons_fontawesome_6.ICON_FA_PENCIL
            ),
            "tile-pencil",
            (
                "Tile Picker\n"
                "Click or drag to sample from the viewport\n"
                "Click this button to return to Pencil"
                if picker_active
                else (
                    "Pencil (S)\n"
                    "Paint the selected tile stamp\n"
                    "Click again or hold Shift for Tile Picker"
                )
            ),
            size=button_size,
            selected=self.state.tile_tool in {"pencil", "picker"},
        ):
            self.state.tile_tool = (
                "picker"
                if self.state.tile_tool == "pencil"
                else "pencil"
            )
            self.state.clear_tile_selection()
        imgui.same_line()
        for icon, identifier, tooltip, tool in (
            (
                icons_fontawesome_6.ICON_FA_VECTOR_SQUARE,
                "tile-rectangle-fill",
                "Rectangle Fill (F)\nFill an area with the selected tile stamp",
                "rectangle",
            ),
            (
                icons_fontawesome_6.ICON_FA_ERASER,
                "tile-eraser",
                "Eraser (D)\nErase painted tiles",
                "eraser",
            ),
        ):
            if icon_button(
                icon,
                identifier,
                tooltip,
                size=button_size,
                selected=self.state.tile_tool == tool,
            ):
                self.state.tile_tool = tool
                self.state.clear_tile_selection()
            if tool == "eraser":
                imgui.same_line(0, 20)
            else:
                imgui.same_line()
        for icon, identifier, tooltip, tool in (
            (
                icons_fontawesome_6.ICON_FA_ARROW_POINTER,
                "tile-select",
                "Rectangle Select (Q)\nDrag to select placed tiles",
                "select",
            ),
            (
                icons_fontawesome_6.ICON_FA_ARROWS_UP_DOWN_LEFT_RIGHT,
                "tile-move",
                "Move Tiles (W)\nDrag a tile or selection; snap to the grid",
                "move",
            ),
        ):
            if icon_button(
                icon,
                identifier,
                tooltip,
                size=button_size,
                selected=self.state.tile_tool == tool,
            ):
                self.state.tile_tool = tool
            imgui.same_line(0, 20) if tool == "move" else imgui.same_line()
        rotation_arrows = ("↑", "→", "↓", "←")
        if icon_button(
            rotation_arrows[self.state.tile_rotation],
            "tile-rotate-clockwise",
            "Rotate 90° Clockwise (R)\nTransforms the palette stamp or selected tiles",
            size=button_size,
            selected=self.state.tile_rotation != 0,
        ):
            if self.state.rotate_tiles_clockwise():
                self.preview.reload()
        imgui.same_line()
        if icon_button(
            icons_fontawesome_6.ICON_FA_LEFT_RIGHT,
            "tile-flip-horizontal",
            "Flip Horizontally (H)\nTransforms the palette stamp or selected tiles",
            size=button_size,
            selected=self.state.tile_flip_horizontal,
        ):
            if self.state.toggle_tile_flip("horizontal"):
                self.preview.reload()
        imgui.same_line()
        if icon_button(
            icons_fontawesome_6.ICON_FA_UP_DOWN,
            "tile-flip-vertical",
            "Flip Vertically (V)\nTransforms the palette stamp or selected tiles",
            size=button_size,
            selected=self.state.tile_flip_vertical,
        ):
            if self.state.toggle_tile_flip("vertical"):
                self.preview.reload()
        imgui.same_line(0, 20)
        selected_count = len(self.state.selected_tile_cells)
        if selected_count:
            imgui.text_disabled(f"{selected_count} tiles selected")
            imgui.same_line()
        imgui.set_cursor_pos_x(
            max(
                imgui.get_cursor_pos_x(),
                imgui.get_window_width()
                - imgui.get_style().window_padding.x
                - button_size.x,
            )
        )
        if icon_button(
            icons_fontawesome_6.ICON_FA_DICE,
            "tile-random",
            "Random Brush\nChoose a random selected tile for each cell",
            size=button_size,
            selected=self.state.random_tile_brush,
        ):
            self.state.random_tile_brush = (
                not self.state.random_tile_brush
            )

    def _effective_transform_space(self) -> str:
        if self._transform_tool == "scale":
            return "local"
        if len(self.state.selected_records) > 1:
            return "global"
        return self._transform_space
