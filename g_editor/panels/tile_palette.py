from __future__ import annotations

from math import ceil

import pyglet
from g.persistence import JsonValue
from imgui_bundle import icons_fontawesome_6, imgui

from ..panel_support import AssetPickerTarget
from ..ui import (
    color as _color,
    flag as _flag,
    icon_button,
)
from .base import EditorPanel


class TilePalettePanel(EditorPanel):
    _MIN_PALETTE_ZOOM = 0.25
    _MAX_PALETTE_ZOOM = 4.0
    _PALETTE_ZOOM_STEP = 1.2

    def _draw_tile_palette_panel(self) -> None:
        if self._begin_panel("Tile Palette"):
            layer = self.state.selected_layer
            if layer is None or layer.get("type") != "tile":
                imgui.text_disabled(
                    "Select a tile layer to show its palettes"
                )
            elif self.preview.mode != "edit":
                imgui.text_disabled(
                    "Stop Play mode to edit the tile palette"
                )
            else:
                self._draw_tile_palette_tabs(layer)
        imgui.end()

    def _draw_tile_palette_tabs(
        self,
        layer: dict[str, JsonValue],
    ) -> None:
        tilesets = self.state.tile_layer_tilesets(layer)
        if not tilesets:
            imgui.text_disabled(
                "Add a tileset in Tilemap to show its palette"
            )
            return
        if imgui.begin_tab_bar("##tile-palette-tabs"):
            for tileset in tilesets:
                identifier = str(tileset.get("id", ""))
                name = str(tileset.get("name", "Tileset"))
                active = self.state.active_tileset(layer)
                flags = (
                    imgui.TabItemFlags_.set_selected
                    if active is tileset
                    else imgui.TabItemFlags_.none
                )
                opened, _ = imgui.begin_tab_item(
                    f"{name}##palette-{identifier}",
                    None,
                    flags,
                )
                if imgui.is_item_clicked() and active is not tileset:
                    self.state.select_tileset(identifier)
                if opened:
                    self._draw_tile_palette(tileset)
                    imgui.end_tab_item()
            imgui.end_tab_bar()

    def _draw_tile_palette(
        self,
        tileset: dict[str, JsonValue],
    ) -> None:
        self._draw_palette_zoom_controls()
        if (size := self.state.sampled_tile_size) is not None:
            imgui.text_disabled(
                f"Stamp: {size[0]} x {size[1]}"
            )
        if tileset.get("type") == "color":
            self._draw_color_tile_palette(tileset)
            return
        if tileset.get("type") == "images":
            self._draw_image_collection_palette(tileset)
            return
        image_path = tileset.get("image")
        tile_size = tileset.get("tile_size")
        if (
            not isinstance(image_path, str)
            or not isinstance(tile_size, list)
            or len(tile_size) != 2
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
        try:
            texture = self._atlas_texture(image_path)
        except (OSError, ValueError) as error:
            self.state.error = f"Could not preview atlas: {error}"
            return

        imgui.separator_text("Tile Palette")
        available = imgui.get_content_region_avail()
        fit_scale = min(
            1.0,
            max(0.01, available.x / texture.width),
            max(0.01, 260 / texture.height),
        )
        scale = fit_scale * self._palette_zoom
        size = imgui.ImVec2(
            max(1, texture.width * scale),
            max(1, texture.height * scale),
        )
        texture_id = texture.id
        if texture_id is None:
            return
        imgui.image(
            imgui.ImTextureRef(texture_id),
            size,
            imgui.ImVec2(0, 1),
            imgui.ImVec2(1, 0),
        )
        self._apply_palette_wheel_zoom(imgui.is_item_hovered())
        minimum = imgui.get_item_rect_min()
        maximum = imgui.get_item_rect_max()
        margin = tileset.get("margin", 0)
        spacing = tileset.get("spacing", 0)
        columns = tileset.get("columns")
        tile_count = tileset.get("tile_count")
        if not isinstance(margin, int):
            margin = 0
        if not isinstance(spacing, int):
            spacing = 0
        if not isinstance(columns, int) or columns <= 0:
            return
        if not isinstance(tile_count, int) or tile_count <= 0:
            return

        first_gid = tileset.get("first_gid", 0)
        if not isinstance(first_gid, int):
            return
        left_button = _flag(imgui.MouseButton_.left)
        pointer = imgui.get_mouse_pos()
        tile_id = self._atlas_tile_at(
            (pointer.x - minimum.x) / scale,
            (pointer.y - minimum.y) / scale,
            tile_width,
            tile_height,
            columns,
            tile_count,
            margin,
            spacing,
        )
        preview_local = self._update_palette_drag(
            tile_id,
            first_gid,
            columns,
            tile_count,
            left_button,
        )

        committed_local = [
            tile_id - first_gid
            for tile_id in self.state.palette_tile_ids
            if first_gid <= tile_id < first_gid + tile_count
        ]
        placed_local = {
            tile_id - first_gid
            for tile_id in self.state.selected_placed_tile_ids
            if first_gid <= tile_id < first_gid + tile_count
        }
        showing_placed = (
            preview_local is None
            and bool(self.state.selected_tile_cells)
        )
        showing_individual_tiles = (
            showing_placed
            or (
                preview_local is None
                and self.state.sampled_tile_size is not None
            )
        )
        selected_local = (
            preview_local
            if preview_local is not None
            else sorted(placed_local)
            if showing_placed
            else committed_local
        )
        if not selected_local:
            return
        draw_list = imgui.get_window_draw_list()
        if showing_individual_tiles:
            for local_id in selected_local:
                column = local_id % columns
                row = local_id // columns
                left = minimum.x + (
                    margin + column * (tile_width + spacing)
                ) * scale
                top = minimum.y + (
                    margin + row * (tile_height + spacing)
                ) * scale
                draw_list.add_rect(
                    imgui.ImVec2(left, top),
                    imgui.ImVec2(
                        min(maximum.x, left + tile_width * scale),
                        min(maximum.y, top + tile_height * scale),
                    ),
                    _color(56, 200, 255, 255),
                    0,
                    3,
                    0,
                )
        else:
            first_selected = selected_local[0]
            selected_columns = [
                tile_id % columns for tile_id in selected_local
            ]
            selected_rows = [
                tile_id // columns for tile_id in selected_local
            ]
            min_column = min(selected_columns)
            max_column = max(selected_columns)
            min_row = min(selected_rows)
            max_row = max(selected_rows)
            left = minimum.x + (
                margin + min_column * (tile_width + spacing)
            ) * scale
            top = minimum.y + (
                margin + min_row * (tile_height + spacing)
            ) * scale
            right = minimum.x + (
                margin
                + max_column * (tile_width + spacing)
                + tile_width
            ) * scale
            selection_height = max_row - min_row + 1
            draw_list.add_rect(
                imgui.ImVec2(left, top),
                imgui.ImVec2(
                    min(maximum.x, right),
                    min(
                        maximum.y,
                        top
                        + selection_height
                        * (tile_height + spacing)
                        * scale
                        - spacing * scale,
                    ),
                ),
                _color(56, 200, 255, 255),
                0,
                2.5,
                0,
            )
        if showing_individual_tiles and len(selected_local) == 1:
            imgui.text(f"Selected tile {first_gid + selected_local[0]}")
        elif showing_individual_tiles:
            imgui.text(f"Selected {len(selected_local)} tile types")
        elif preview_local is None and len(committed_local) == 1:
            imgui.text(f"Selected tile {first_gid + committed_local[0]}")
        elif preview_local is None and committed_local:
            imgui.text(
                f"Selected {len(committed_local)} tiles "
                f"({self.state.palette_selection_size[0]} x "
                f"{self.state.palette_selection_size[1]})"
            )

    def _draw_color_tile_palette(
        self,
        tileset: dict[str, JsonValue],
    ) -> None:
        colors = tileset.get("colors")
        first_gid = tileset.get("first_gid")
        if (
            not isinstance(colors, list)
            or not colors
            or not isinstance(first_gid, int)
        ):
            return
        imgui.separator_text("Color Palette")
        available = imgui.get_content_region_avail()
        swatch_size = 36.0 * self._palette_zoom
        spacing = 6.0 * self._palette_zoom
        columns = max(
            1,
            int((available.x + spacing) // (swatch_size + spacing)),
        )
        rows = ceil(len(colors) / columns)
        canvas_height = rows * swatch_size + (rows - 1) * spacing
        imgui.invisible_button(
            f"##color-palette-{tileset.get('id', '')}",
            imgui.ImVec2(max(1.0, available.x), canvas_height),
        )
        minimum = imgui.get_item_rect_min()
        self._apply_palette_wheel_zoom(imgui.is_item_hovered())
        draw_list = imgui.get_window_draw_list()
        committed_local = {
            tile_id - first_gid
            for tile_id in self.state.palette_tile_ids
            if first_gid <= tile_id < first_gid + len(colors)
        }
        placed_local = {
            tile_id - first_gid
            for tile_id in self.state.selected_placed_tile_ids
            if first_gid <= tile_id < first_gid + len(colors)
        }
        left_button = _flag(imgui.MouseButton_.left)
        pointer = imgui.get_mouse_pos()
        local_id = self._color_tile_at(
            pointer.x - minimum.x,
            pointer.y - minimum.y,
            columns,
            len(colors),
            swatch_size,
            spacing,
        )
        preview_local = self._update_palette_drag(
            local_id,
            first_gid,
            columns,
            len(colors),
            left_button,
        )
        showing_placed = (
            preview_local is None
            and bool(self.state.selected_tile_cells)
        )
        selected_local = (
            set(preview_local)
            if preview_local is not None
            else placed_local
            if showing_placed
            else committed_local
        )
        for index, raw_color in enumerate(colors):
            if (
                not isinstance(raw_color, list)
                or len(raw_color) not in {3, 4}
            ):
                continue
            color = [
                int(channel)
                for channel in raw_color
                if isinstance(channel, int)
            ]
            if len(color) != len(raw_color):
                continue
            if len(color) == 3:
                color.append(255)
            column = index % columns
            row = index // columns
            left = minimum.x + column * (swatch_size + spacing)
            top = minimum.y + row * (swatch_size + spacing)
            first = imgui.ImVec2(left, top)
            second = imgui.ImVec2(
                left + swatch_size,
                top + swatch_size,
            )
            draw_list.add_rect_filled(
                first,
                second,
                _color(*color),
                3,
            )
            draw_list.add_rect(
                first,
                second,
                (
                    _color(56, 200, 255, 255)
                    if index in selected_local
                    else _color(95, 95, 105, 255)
                ),
                3,
                thickness=(
                    3 if index in selected_local else 1
                ),
            )

        if preview_local is None and len(selected_local) == 1:
            selected = next(iter(selected_local))
            imgui.text(f"Selected tile {first_gid + selected}")
            changed, color = self._color_widget(
                "Color",
                colors[selected],
            )
            if changed and isinstance(color, list):
                try:
                    self.state.set_tile_color(
                        first_gid + selected,
                        [
                            int(channel)
                            for channel in color
                            if isinstance(channel, int)
                        ],
                    )
                    self.preview.reload()
                except ValueError as error:
                    self.state.error = str(error)

    def _draw_image_collection_palette(
        self,
        tileset: dict[str, JsonValue],
    ) -> None:
        images = tileset.get("images")
        first_gid = tileset.get("first_gid")
        identifier = tileset.get("id")
        if (
                not isinstance(images, list)
                or not images
                or not isinstance(first_gid, int)
                or not isinstance(identifier, str)
        ):
                return
        imgui.separator_text("Image Collection")
        available = imgui.get_content_region_avail()
        tile_size = 52.0 * self._palette_zoom
        spacing = 6.0 * self._palette_zoom
        columns = max(
                1,
                int((available.x + spacing) // (tile_size + spacing)),
        )
        rows = ceil(len(images) / columns)
        canvas_height = rows * tile_size + (rows - 1) * spacing
        imgui.invisible_button(
                f"##image-collection-{identifier}",
                imgui.ImVec2(max(1.0, available.x), canvas_height),
        )
        minimum = imgui.get_item_rect_min()
        self._apply_palette_wheel_zoom(imgui.is_item_hovered())
        draw_list = imgui.get_window_draw_list()
        committed_local = {
                tile_id - first_gid
                for tile_id in self.state.palette_tile_ids
                if first_gid <= tile_id < first_gid + len(images)
        }
        placed_local = {
                tile_id - first_gid
                for tile_id in self.state.selected_placed_tile_ids
                if first_gid <= tile_id < first_gid + len(images)
        }
        left_button = _flag(imgui.MouseButton_.left)
        pointer = imgui.get_mouse_pos()
        local_id = self._color_tile_at(
                pointer.x - minimum.x,
                pointer.y - minimum.y,
                columns,
                len(images),
                tile_size,
                spacing,
        )
        preview_local = self._update_palette_drag(
                local_id,
                first_gid,
                columns,
                len(images),
                left_button,
        )
        showing_placed = (
                preview_local is None
                and bool(self.state.selected_tile_cells)
        )
        selected_local = (
                set(preview_local)
                if preview_local is not None
                else placed_local
                if showing_placed
                else committed_local
        )
        for index, raw_path in enumerate(images):
                if not isinstance(raw_path, str):
                    continue
                try:
                    texture = self._atlas_texture(raw_path)
                except (OSError, ValueError) as error:
                    self.state.error = (
                        f"Could not preview image tile: {error}"
                    )
                    continue
                texture_id = texture.id
                if texture_id is None:
                    continue
                column = index % columns
                row = index // columns
                left = minimum.x + column * (tile_size + spacing)
                top = minimum.y + row * (tile_size + spacing)
                first = imgui.ImVec2(left, top)
                second = imgui.ImVec2(left + tile_size, top + tile_size)
                scale = min(
                    tile_size / texture.width,
                    tile_size / texture.height,
                )
                width = texture.width * scale
                height = texture.height * scale
                image_minimum = imgui.ImVec2(
                    left + (tile_size - width) / 2,
                    top + (tile_size - height) / 2,
                )
                image_maximum = imgui.ImVec2(
                    image_minimum.x + width,
                    image_minimum.y + height,
                )
                draw_list.add_image(
                    imgui.ImTextureRef(texture_id),
                    image_minimum,
                    image_maximum,
                    imgui.ImVec2(0, 1),
                    imgui.ImVec2(1, 0),
                )
                draw_list.add_rect(
                    first,
                    second,
                    (
                        _color(56, 200, 255, 255)
                        if index in selected_local
                        else _color(95, 95, 105, 255)
                    ),
                    3,
                    thickness=(
                        3 if index in selected_local else 1
                    ),
                )

        if preview_local is None and len(selected_local) == 1:
                selected = next(iter(selected_local))
                path = images[selected]
                imgui.text(
                    str(path)
                    if isinstance(path, str)
                    else f"Tile {first_gid + selected}"
                )
        elif preview_local is None and committed_local:
                imgui.text(
                    f"Selected {len(selected_local)} image tiles "
                    f"({self.state.palette_selection_size[0]} x "
                    f"{self.state.palette_selection_size[1]})"
                )
        if icon_button(
                icons_fontawesome_6.ICON_FA_PLUS,
                "add-image-tile",
                "Add Image Tile\nChoose an image from Assets",
                size=imgui.ImVec2(34, 30),
        ):
                self._toggle_asset_picker(
                    AssetPickerTarget(
                        f"image-collection-add:{identifier}",
                        "Image Tile",
                        "image",
                    )
                )
        elif preview_local is None and committed_local:
            imgui.text(
                f"Selected {len(selected_local)} color tiles "
                f"({self.state.palette_selection_size[0]} x "
                f"{self.state.palette_selection_size[1]})"
            )
        if imgui.button(
            f"{icons_fontawesome_6.ICON_FA_PLUS} Add Color"
        ):
            try:
                self.state.add_color_tile()
                self.preview.reload()
            except ValueError as error:
                self.state.error = str(error)

    def _update_palette_drag(
        self,
        current_local_id: int | None,
        first_gid: int,
        columns: int,
        tile_count: int,
        left_button: int,
    ) -> list[int] | None:
        if imgui.is_item_clicked(left_button) and current_local_id is not None:
            io = imgui.get_io()
            if io.key_ctrl or io.key_super:
                self.state.toggle_palette_tile(
                    first_gid + current_local_id,
                    columns=columns,
                )
                self.state.clear_tile_selection()
                self._palette_drag_start = None
                return None
            self._palette_drag_start = current_local_id

        start = self._palette_drag_start
        if start is None:
            return None

        released = imgui.is_mouse_released(left_button)
        if released:
            selection = self._palette_selection_rectangle(
                start,
                current_local_id,
                columns,
                tile_count,
            )
            if selection is not None:
                local_ids, width, height = selection
                self.state.select_palette_tiles(
                    [first_gid + local_id for local_id in local_ids],
                    width,
                    height,
                )
                self.state.clear_tile_selection()
            self._palette_drag_start = None
            return None

        if imgui.is_mouse_down(left_button):
            selection = self._palette_selection_rectangle(
                start,
                current_local_id,
                columns,
                tile_count,
            )
            if selection is None:
                return []
            return selection[0]

        self._palette_drag_start = None
        return None

    def _draw_palette_zoom_controls(self) -> None:
        imgui.text("Zoom")
        imgui.same_line()
        if imgui.button("-##palette-zoom-out"):
            self._set_palette_zoom(
                self._palette_zoom / self._PALETTE_ZOOM_STEP
            )
        imgui.same_line()
        imgui.text(f"{self._palette_zoom:.0%}")
        imgui.same_line()
        if imgui.button("+##palette-zoom-in"):
            self._set_palette_zoom(
                self._palette_zoom * self._PALETTE_ZOOM_STEP
            )
        imgui.same_line()
        if imgui.small_button("Reset##palette-zoom-reset"):
            self._set_palette_zoom(1.0)
        imgui.same_line()
        imgui.text_disabled("Ctrl/Cmd + wheel over palette")

    def _apply_palette_wheel_zoom(self, hovered: bool) -> None:
        io = imgui.get_io()
        if hovered and (io.key_ctrl or io.key_super) and io.mouse_wheel:
            self._set_palette_zoom(
                self._palette_zoom
                * self._PALETTE_ZOOM_STEP ** io.mouse_wheel
            )

    def _set_palette_zoom(self, zoom: float) -> None:
        self._palette_zoom = self._clamp_palette_zoom(zoom)

    @classmethod
    def _clamp_palette_zoom(cls, zoom: float) -> float:
        return min(
            cls._MAX_PALETTE_ZOOM,
            max(cls._MIN_PALETTE_ZOOM, zoom),
        )

    @staticmethod
    def _palette_selection_rectangle(
        start: int,
        end: int | None,
        columns: int,
        tile_count: int,
    ) -> tuple[list[int], int, int] | None:
        if end is None or columns <= 0:
            return None
        left_column, right_column = sorted(
            (start % columns, end % columns)
        )
        top_row, bottom_row = sorted(
            (start // columns, end // columns)
        )
        width = right_column - left_column + 1
        height = bottom_row - top_row + 1
        local_ids = [
            row * columns + column
            for row in range(top_row, bottom_row + 1)
            for column in range(left_column, right_column + 1)
            if row * columns + column < tile_count
        ]
        if len(local_ids) != width * height:
            return None
        return local_ids, width, height

    @staticmethod
    def _color_tile_at(
        x: float,
        y: float,
        columns: int,
        tile_count: int,
        swatch_size: float,
        spacing: float,
    ) -> int | None:
        if x < 0 or y < 0:
            return None
        stride = swatch_size + spacing
        column, within_x = divmod(x, stride)
        row, within_y = divmod(y, stride)
        if within_x >= swatch_size or within_y >= swatch_size:
            return None
        tile_id = int(row) * columns + int(column)
        return tile_id if 0 <= tile_id < tile_count else None

    def _atlas_texture(self, image_path: str) -> pyglet.image.Texture:
        path = (self.state.asset_root / image_path).resolve()
        path.relative_to(self.state.asset_root)
        modified = path.stat().st_mtime_ns
        cached = self._atlas_textures.get(path)
        if cached is not None and cached[0] == modified:
            return cached[1]
        if cached is not None:
            cached[1].delete()
        texture = pyglet.image.load(str(path)).get_texture()
        self._atlas_textures[path] = (modified, texture)
        return texture

    @staticmethod
    def _atlas_tile_at(
        x: float,
        y: float,
        tile_width: int,
        tile_height: int,
        columns: int,
        tile_count: int,
        margin: int,
        spacing: int,
    ) -> int | None:
        local_x = x - margin
        local_y = y - margin
        if local_x < 0 or local_y < 0:
            return None
        stride_x = tile_width + spacing
        stride_y = tile_height + spacing
        column, within_x = divmod(local_x, stride_x)
        row, within_y = divmod(local_y, stride_y)
        if within_x >= tile_width or within_y >= tile_height:
            return None
        tile_id = int(row) * columns + int(column)
        return tile_id if 0 <= tile_id < tile_count else None
