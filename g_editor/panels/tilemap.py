from __future__ import annotations

from g.persistence import JsonValue
from imgui_bundle import imgui

from ..panel_support import AssetPickerTarget
from .base import EditorPanel


class TilemapPanel(EditorPanel):
    def _draw_tile_layer_inspector(
        self,
        layer: dict[str, JsonValue],
    ) -> None:
        imgui.separator_text("Tile Layer")
        tilesets = self.state.tile_layer_tilesets(layer)
        tileset_record = self.state.active_tileset(layer) or {}
        layer_id = str(layer.get("id", "tile"))
        active_id = str(tileset_record.get("id", "new"))
        if (
            tilesets
            and tileset_record
            and imgui.begin_tab_bar("##tileset-config-tabs")
        ):
            for tileset in tilesets:
                identifier = str(tileset.get("id", ""))
                name = str(tileset.get("name", "Tileset"))
                flags = (
                    imgui.TabItemFlags_.set_selected
                    if tileset is tileset_record
                    else imgui.TabItemFlags_.none
                )
                opened, _ = imgui.begin_tab_item(
                    f"{name}##config-{identifier}",
                    None,
                    flags,
                )
                if (
                    imgui.is_item_clicked()
                    and tileset is not tileset_record
                ):
                    self.state.select_tileset(identifier)
                    tileset_record = tileset
                    active_id = identifier
                if opened:
                    imgui.end_tab_item()
            imgui.end_tab_bar()
        if not tileset_record and tilesets:
            if imgui.button("Cancel New Tileset"):
                self.state.select_tileset(
                    str(tilesets[0].get("id", ""))
                )
                tileset_record = tilesets[0]
                active_id = str(tileset_record.get("id", ""))
        else:
            if imgui.button("Add Image Atlas"):
                self.state.begin_new_tileset()
                tileset_record = {}
                active_id = "new"
            imgui.same_line()
            if imgui.button("Add Image Collection"):
                self._toggle_asset_picker(
                    AssetPickerTarget(
                        "image-collection-new",
                        "Image Tile",
                        "image",
                    )
                )
            imgui.same_line()
            if imgui.button("Add Color Palette"):
                tileset_record = self.state.add_color_tileset()
                active_id = str(tileset_record.get("id", ""))
                self.preview.reload()
        if tileset_record:
            imgui.same_line()
            if imgui.button("Remove Tileset"):
                identifier = str(tileset_record.get("id", ""))
                usage = self.state.tileset_usage_count(identifier)
                if usage:
                    self._pending_tileset_delete = identifier
                    self._popup_to_open = "Delete Used Tileset"
                else:
                    self.state.remove_tileset(identifier)
                    self.preview.reload()

        config_key = f"{layer_id}:{active_id}"
        if tileset_record.get("type") in {"color", "images"}:
            self._draw_tile_layer_settings(layer)
            return

        path_key = f"tileset-path:{config_key}"
        stored_path = str(tileset_record.get("image") or "")
        current_path = self._json_buffers.setdefault(path_key, stored_path)
        self._draw_asset_reference_field(
            "Atlas Image",
            current_path,
            path_key,
            kind="image",
        )
        current_path = self._json_buffers.get(path_key, current_path)
        tile_size = tileset_record.get(
            "tile_size",
            layer.get("cell_size", [32, 32]),
        )
        if not isinstance(tile_size, list) or len(tile_size) != 2:
            tile_size = [32, 32]
        raw_tile_width, raw_tile_height = tile_size
        initial_tile_width = (
            int(raw_tile_width)
            if isinstance(raw_tile_width, (int, float))
            and not isinstance(raw_tile_width, bool)
            else 32
        )
        initial_tile_height = (
            int(raw_tile_height)
            if isinstance(raw_tile_height, (int, float))
            and not isinstance(raw_tile_height, bool)
            else 32
        )
        initial_margin = tileset_record.get("margin", 0)
        initial_spacing = tileset_record.get("spacing", 0)
        tile_width, tile_height, margin, spacing = (
            self._tile_config_buffers.setdefault(
                config_key,
                (
                    initial_tile_width,
                    initial_tile_height,
                    (
                        int(initial_margin)
                        if isinstance(initial_margin, int)
                        else 0
                    ),
                    (
                        int(initial_spacing)
                        if isinstance(initial_spacing, int)
                        else 0
                    ),
                ),
            )
        )
        _, tile_width = self._int_property(
            "Tile Width",
            tile_width,
        )
        _, tile_height = self._int_property(
            "Tile Height",
            tile_height,
        )
        _, margin = self._int_property(
            "Atlas Margin",
            margin,
        )
        _, spacing = self._int_property(
            "Atlas Spacing",
            spacing,
        )
        self._tile_config_buffers[config_key] = (
            tile_width,
            tile_height,
            margin,
            spacing,
        )
        if imgui.button("Apply Tileset"):
            try:
                self.state.configure_tile_layer(
                    current_path,
                    tile_width,
                    tile_height,
                    margin=margin,
                    spacing=spacing,
                )
                self._tile_config_buffers[config_key] = (
                    tile_width,
                    tile_height,
                    margin,
                    spacing,
                )
            except ValueError as error:
                self.state.error = str(error)

        self._draw_tile_layer_settings(layer)

    def _draw_tile_layer_settings(
        self,
        layer: dict[str, JsonValue],
    ) -> None:
        opacity = layer.get("opacity", 1.0)
        changed, opacity = self._slider_property(
            "Opacity",
            float(opacity)
            if isinstance(opacity, (int, float))
            else 1,
            0,
            1,
        )
        if changed:
            self.state.set_tile_layer_value("opacity", opacity)
        collision_modes = ["alpha", "rectangle", "existing"]
        collision_labels = [
            "Opaque Pixels",
            "Full Tile",
            "Existing Metadata Only",
        ]
        collision_mode = str(
            layer.get("collision_generation", "alpha")
        )
        collision_index = (
            collision_modes.index(collision_mode)
            if collision_mode in collision_modes
            else 0
        )
        changed, collision_index = self._choice_property(
            "Collision Source",
            collision_index,
            collision_labels,
        )
        if changed:
            self.state.set_tile_layer_value(
                "collision_generation",
                collision_modes[collision_index],
            )
        if collision_modes[collision_index] == "alpha":
            threshold = layer.get("collision_alpha_threshold", 255)
            changed, threshold = self._int_property(
                "Alpha Threshold",
                int(threshold) if isinstance(threshold, int) else 255,
                minimum=1,
                maximum=255,
            )
            if changed:
                self.state.set_tile_layer_value(
                    "collision_alpha_threshold",
                    threshold,
                )
            inverted = layer.get("collision_alpha_inverted") is True
            changed, inverted = self._bool_property(
                "Use Transparent Pixels",
                inverted,
            )
            if changed:
                self.state.set_tile_layer_value(
                    "collision_alpha_inverted",
                    inverted,
                )
        if imgui.button("Generate Collision"):
            try:
                self.state.set_tile_layer_value(
                    "collision_enabled",
                    True,
                )
                self.state.generate_tile_collisions(replace=True)
                self.preview.reload()
            except (OSError, ValueError) as error:
                self.state.error = str(error)
        imgui.same_line()
        if imgui.button("Generate Navigation"):
            try:
                self.state.set_tile_layer_value(
                    "navigation_enabled",
                    True,
                )
                self.state.generate_tile_navigation()
                self.preview.reload()
            except (OSError, ValueError) as error:
                self.state.error = str(error)
