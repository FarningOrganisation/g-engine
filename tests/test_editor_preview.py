import unittest
import os
from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY, Mock, PropertyMock, call, patch

import pyglet
from pyglet.window import key, mouse
from g import (
    ColorTileSet,
    GameObject,
    Panel,
    Slider,
    SpriteAnimator,
    SpriteRenderer,
    TextInput,
    TileCell,
    TileFlags,
    ImageCollectionTileSet,
    TileLayer,
    Toggle,
    VBox,
)
from g.export import ExportResult
from g.persistence import scene_types

try:
    from imgui_bundle import icons_fontawesome_6, imgui
    from imgui_bundle import imguizmo  # pyright: ignore[reportMissingModuleSource]
except ImportError as error:
    raise unittest.SkipTest(
        "ImGui editor dependency is not installed"
    ) from error

from g_editor.application import (
    ImGuiEditor,
    _AssetPickerTarget,
    _FrameRateTracker,
    _MacSaveDialog,
    _ConsoleBuffer,
    _TeeStream,
    run_editor,
)
from g_editor.prefab import PrefabDocument
from g_editor.state import layer_objects, position
from g_editor.ui import (
    AssetField,
    BooleanField,
    ColorField,
    FloatField,
    IntegerField,
    OptionalColorField,
    PropertyField,
    TextField,
    icon_button,
)
from g_editor.ui.fields import FieldResult


class ImGuiEditorTests(unittest.TestCase):
    def test_project_validation_uses_unsaved_open_scene(self) -> None:
        from g import ProjectConfig

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.editor.project = ProjectConfig.create(root)
            self.editor.state.asset_root = root
            self.editor.state.filename = root / "main.scene.json"
            self.editor.state.document.settings["image"] = "missing.png"

            self.assertFalse(self.editor._validate_project("preview"))

            self.assertIn(
                "missing image asset: missing.png",
                self.editor.state.error or "",
            )
            self.assertIn("1 error", self.editor.state.status)

    def test_run_editor_uses_scene_directory_as_working_and_asset_root(
        self,
    ) -> None:
        original = Path.cwd()
        try:
            with TemporaryDirectory() as directory:
                scene = Path(directory) / "scenes" / "race.scene.json"
                scene.parent.mkdir()
                resolved_scene = scene.resolve()
                with (
                    patch(
                        "g_editor.application.ImGuiEditor"
                    ) as editor_type,
                    patch("g_editor.splash.EditorSplash") as splash_type,
                ):
                    editor_type.return_value.run.return_value = 0
                    self.assertEqual(run_editor(scene), 0)
                self.assertEqual(Path.cwd().resolve(), resolved_scene.parent)
                editor_type.assert_called_once_with(
                    resolved_scene,
                    resolved_scene.parent,
                    (),
                    start_hidden=True,
                    preferences=ANY,
                    preferences_path=(
                        Path.home() / ".g-editor" / "settings.json"
                    ),
                )
                (
                    splash_type.return_value.__enter__.return_value.finish
                    .assert_called_once_with()
                )
                editor_type.return_value.window.set_visible.assert_called_once_with(
                    True
                )
                editor_type.return_value.window.activate.assert_called_once_with()
        finally:
            os.chdir(original)

    def test_mouse_then_arrow_navigation_has_one_selection_and_rename_target(self) -> None:
        for kind in ("object", "gui"):
            with self.subTest(kind=kind):
                editor = self.editor
                if kind == "gui":
                    editor.state.add_ui_view("Keyboard selection")
                records = []
                for _ in range(3):
                    editor.state.select(None)
                    records.append(
                        editor.state.add_object("empty") if kind == "object"
                        else editor.state.add_gui_element("button")
                    )
                ids = [str(record["id"]) for record in records]
                rows: dict[str, tuple[float, float]] = {}
                row_item_flags: dict[str, int] = {}
                row_ids: set[int] = set()
                selected_rows: list[str] = []
                tree_node = imgui.tree_node_ex

                def capture_row(label, flags):
                    result = tree_node(label, flags)
                    for identifier in ids:
                        if label.endswith(identifier):
                            row_ids.add(imgui.get_id(label))
                            row_item_flags[identifier] = int(imgui.get_item_flags())
                            minimum, maximum = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                            rows[identifier] = (minimum.x + 50, (minimum.y + maximum.y) / 2)
                            if flags & int(imgui.TreeNodeFlags_.selected):
                                selected_rows.append(identifier)
                    return result

                io = imgui.get_io()

                def draw() -> None:
                    selected_rows.clear()
                    editor.on_draw()

                with patch("g_editor.panels.layers.imgui.tree_node_ex", side_effect=capture_row):
                    for _ in range(4):
                        draw()
                    io.add_mouse_pos_event(*rows[ids[0]])
                    io.add_mouse_button_event(0, True)
                    draw()
                    io.add_mouse_button_event(0, False)
                    draw()
                    draw()
                    self.assertEqual(editor.state.selected_ids, [ids[0]])
                    io.add_key_event(imgui.Key.down_arrow, True)
                    draw()
                    io.add_key_event(imgui.Key.down_arrow, False)
                    draw()
                    draw()
                    self.assertEqual(editor.state.selected_ids, [ids[1]])
                    self.assertEqual(selected_rows, [ids[1]])
                    for identifier in ids:
                        self.assertTrue(
                            row_item_flags[identifier] & int(imgui.ItemFlags_.no_nav),
                            "Hierarchy rows must not acquire a second native navigation selection",
                        )
                    context = imgui.get_current_context()
                    self.assertFalse(
                        context.nav_cursor_visible and context.nav_id in row_ids,
                        "A stale mouse-focus row must not get a second highlight",
                    )
                    self.assertEqual(editor.state.selected_id, ids[1])
                    with patch("g_editor.application.sys.platform", "darwin"):
                        editor.on_key_press(key.ENTER, 0)
                    assert editor._rename_target is not None
                    self.assertEqual(editor._rename_target.identifier, ids[1])
                    self.assertEqual(editor.state.selected_ids, [ids[1]])
                    editor._rename_target = None
                    io.add_key_event(imgui.Key.up_arrow, True)
                    draw()
                    io.add_key_event(imgui.Key.up_arrow, False)
                    draw()
                    draw()
                    self.assertEqual(editor.state.selected_ids, [ids[0]])
                    self.assertEqual(selected_rows, [ids[0]])
                    io.add_key_event(imgui.Key.mod_shift, True)
                    draw()
                    io.add_key_event(imgui.Key.down_arrow, True)
                    draw()
                    io.add_key_event(imgui.Key.down_arrow, False)
                    draw()
                    io.add_key_event(imgui.Key.mod_shift, False)
                    draw()
                    self.assertEqual(set(editor.state.selected_ids), set(ids[:2]))
                    io.add_key_event(imgui.Key.down_arrow, True)
                    draw()
                    io.add_key_event(imgui.Key.down_arrow, False)
                    draw()
                    draw()
                    self.assertEqual(editor.state.selected_ids, [ids[2]])
                    self.assertEqual(selected_rows, [ids[2]])

    def test_hierarchy_arrow_selection_and_shift_range_for_objects_and_gui(self) -> None:
        for kind in ("object", "gui"):
            with self.subTest(kind=kind):
                if kind == "gui":
                    self.editor.state.add_ui_view("Keyboard HUD")
                records = []
                for _ in range(4):
                    self.editor.state.select(None)
                    records.append(
                        self.editor.state.add_object("empty")
                        if kind == "object"
                        else self.editor.state.add_gui_element("button")
                    )
                ids = [str(record["id"]) for record in records]
                self.editor.state.select(ids[0])
                io = imgui.get_io()
                io.key_ctrl = io.key_super = io.key_alt = io.key_shift = False
                try:
                    with (
                        patch("g_editor.panels.layers.imgui.is_window_focused", return_value=True),
                        patch("g_editor.panels.layers.imgui.is_any_item_active", return_value=False),
                        patch("g_editor.panels.layers.imgui.is_popup_open", return_value=False),
                        patch("g_editor.panels.layers.imgui.is_key_pressed", side_effect=lambda pressed, repeat: pressed == imgui.Key.down_arrow),
                        patch.object(self.editor.preview, "reveal_object_if_outside_viewport"),
                    ):
                        self.editor._navigate_hierarchy_selection(ids)
                        self.assertEqual(self.editor.state.selected_ids, [ids[1]])
                        io.key_shift = True
                        self.editor._navigate_hierarchy_selection(ids)
                        self.editor._navigate_hierarchy_selection(ids)
                        self.assertEqual(set(self.editor.state.selected_ids), set(ids[1:]))
                        self.assertEqual(self.editor.state.selection_anchor_id, ids[1])
                        with patch("g_editor.panels.layers.imgui.is_key_pressed", side_effect=lambda pressed, repeat: pressed == imgui.Key.up_arrow):
                            self.editor._navigate_hierarchy_selection(ids)
                        self.assertEqual(set(self.editor.state.selected_ids), set(ids[1:3]))
                        io.key_shift = False
                        self.editor._navigate_hierarchy_selection(ids)
                        self.assertEqual(self.editor.state.selected_ids, [ids[3]])
                        self.editor._navigate_hierarchy_selection(ids)
                        self.assertEqual(self.editor.state.selected_id, ids[3])
                        self.editor.state.select(None)
                        self.editor._navigate_hierarchy_selection(ids)
                        self.assertEqual(self.editor.state.selected_id, ids[0])
                        with patch("g_editor.panels.layers.imgui.is_window_focused", return_value=False):
                            self.editor._navigate_hierarchy_selection(ids)
                        self.assertEqual(self.editor.state.selected_id, ids[0])
                        with patch("g_editor.panels.layers.imgui.is_any_item_active", return_value=True):
                            self.editor._navigate_hierarchy_selection(ids)
                        self.assertEqual(self.editor.state.selected_id, ids[0])
                        with patch("g_editor.panels.layers.imgui.is_popup_open", return_value=True):
                            self.editor._navigate_hierarchy_selection(ids)
                        self.assertEqual(self.editor.state.selected_id, ids[0])
                finally:
                    io.key_shift = False

    def setUp(self) -> None:
        self.editor = ImGuiEditor(visible=False)
        self.editor.window.switch_to()

    def tearDown(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        project_temporary = getattr(self, "_project_temporary", None)
        if project_temporary is not None:
            project_temporary.cleanup()

    def test_queued_draw_after_close_is_ignored(self) -> None:
        self.editor._close()
        self.editor.on_draw()
        self.editor._close()

    def test_viewport_component_tool_dropdown_and_snap_toggle(self) -> None:
        editor = self.editor
        editor.state.add_object("rectangle")
        buttons: dict[str, tuple[float, float]] = {}
        controls: dict[str, tuple[str, bool]] = {}
        io = imgui.get_io()
        begin_combo = imgui.begin_combo
        selectable = imgui.selectable

        def capture_rect(identifier: str) -> None:
            minimum, maximum = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            buttons[identifier] = (
                (minimum.x + maximum.x) / 2, (minimum.y + maximum.y) / 2,
            )

        def capture_combo(label, preview_value, *args, **kwargs):
            result = begin_combo(label, preview_value, *args, **kwargs)
            if label == "##viewport-tool":
                capture_rect("viewport-tool")
                controls["viewport-tool"] = (preview_value, False)
            return result

        def capture_selectable(label, *args, **kwargs):
            result = selectable(label, *args, **kwargs)
            if label in {"Transform", "Edit Collider", "Edit Spline"}:
                capture_rect(label)
            return result

        def capture_button(icon, identifier, tooltip, **kwargs):
            result = icon_button(icon, identifier, tooltip, **kwargs)
            minimum, maximum = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            buttons[identifier] = (
                (minimum.x + maximum.x) / 2, (minimum.y + maximum.y) / 2,
            )
            controls[identifier] = (tooltip, kwargs.get("selected", False))
            return result

        def click(identifier: str) -> None:
            io.add_mouse_pos_event(*buttons[identifier])
            io.add_mouse_button_event(0, True)
            editor.on_draw()
            io.add_mouse_button_event(0, False)
            editor.on_draw()
            editor.on_draw()

        with (
            patch("g_editor.application.icon_button", side_effect=capture_button),
            patch("g_editor.component_tools.imgui.begin_combo", side_effect=capture_combo),
            patch("g_editor.component_tools.imgui.selectable", side_effect=capture_selectable),
        ):
            for _ in range(4):
                editor.on_draw()
            self.assertEqual(controls["viewport-tool"][0], "Transform")
            click("viewport-tool")
            click("Edit Collider")
            self.assertTrue(editor._collider_edit_mode)
            self.assertEqual(controls["viewport-tool"][0], "Edit Collider")
            click("viewport-tool")
            click("Transform")
            self.assertFalse(editor._collider_edit_mode)

            snap = editor.state.snap_enabled
            click("viewport-grid-snap")
            self.assertEqual(editor.state.snap_enabled, not snap)
            self.assertEqual(controls["viewport-grid-snap"][1], not snap)
            click("viewport-grid-snap")
            self.assertEqual(editor.state.snap_enabled, snap)

            editor.state.select(None)
            editor.on_draw()
            click("viewport-tool")
            click("Edit Collider")
            self.assertFalse(editor._collider_edit_mode)
            self.assertEqual(controls["viewport-tool"][0], "Transform")

            editor.preview.set_mode("play")
            editor.on_draw()
            click("viewport-grid-snap")
            self.assertEqual(editor.state.snap_enabled, snap)
            click("viewport-tool")
            click("Edit Collider")
            self.assertFalse(editor._collider_edit_mode)
            self.assertIn("Stop Play", controls["viewport-grid-snap"][0])

    def test_tile_collision_canvas_mouse_drag_and_keyboard_insertion_deletion(self) -> None:
        editor = self.editor
        editor.state.add_tile_layer("Collision gestures")
        editor.state.add_color_tileset()
        editor.state.set_tile_definition_value(0, "collision", {
            "type": "polygon",
            "polygons": [[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]],
        })
        canvas: list[tuple[float, float, float, float]] = []
        add_button: list[tuple[float, float]] = []
        invisible_button = imgui.invisible_button

        def capture_canvas(label, size, *args):
            result = invisible_button(label, size, *args)
            if label == "##tile-collision-canvas-0":
                minimum, maximum = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                canvas[:] = [(minimum.x, minimum.y, maximum.x, maximum.y)]
            return result

        def capture_add_button(icon, identifier, tooltip, **kwargs):
            result = icon_button(icon, identifier, tooltip, **kwargs)
            if identifier == "tile-collision-add-polygon":
                minimum, maximum = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                add_button[:] = [((minimum.x + maximum.x) / 2, (minimum.y + maximum.y) / 2)]
            return result

        io = imgui.get_io()

        def pointer(x: float, y: float) -> None:
            left, top, right, bottom = canvas[0]
            io.add_mouse_pos_event(left + x * (right - left), bottom - y * (bottom - top))

        with (
            patch("g_editor.panels.inspector.imgui.invisible_button", side_effect=capture_canvas),
            patch("g_editor.panels.inspector.icon_button", side_effect=capture_add_button),
        ):
            for _ in range(4):
                editor.on_draw()
            self.assertTrue(canvas)
            history = editor.state._history_cursor
            pointer(0.2, 0.2)
            io.add_mouse_button_event(0, True)
            editor.on_draw()
            pointer(0.1, 0.1)
            editor.on_draw()
            self.assertIsNotNone(editor._tile_collision_drag)
            self.assertEqual(editor.state._history_cursor, history)
            io.add_mouse_button_event(0, False)
            editor.on_draw()
            self.assertEqual(editor.state._history_cursor, history + 1)
            self.assertIsNone(editor._tile_collision_drag)
            pointer(0.5, 0.8)
            io.add_key_event(imgui.Key.mod_shift, True)
            editor.on_draw()
            self.assertTrue(io.key_shift)
            io.add_mouse_button_event(0, True)
            editor.on_draw()
            self.assertEqual(editor._tile_collision_point, (0, 3))
            io.add_mouse_button_event(0, False)
            io.add_key_event(imgui.Key.mod_shift, False)
            editor.on_draw()
            self.assertEqual(editor._tile_collision_point, (0, 3))
            definition = editor.state.tile_definition(0, create=False)
            assert definition is not None
            collision = definition["collision"]
            assert isinstance(collision, dict)
            self.assertEqual(len(editor._collision_polygons(collision)[0]), 5)
            io.add_key_event(imgui.Key.mod_ctrl, True)
            io.add_key_event(imgui.Key.delete, True)
            editor.on_draw()
            io.add_key_event(imgui.Key.delete, False)
            io.add_key_event(imgui.Key.mod_ctrl, False)
            editor.on_draw()
            definition = editor.state.tile_definition(0, create=False)
            assert definition is not None
            collision = definition["collision"]
            assert isinstance(collision, dict)
            self.assertEqual(len(editor._collision_polygons(collision)[0]), 4)
            io.add_mouse_pos_event(*add_button[0])
            io.add_mouse_button_event(0, True)
            editor.on_draw()
            io.add_mouse_button_event(0, False)
            editor.on_draw()
            editor.on_draw()
            self.assertTrue(editor._tile_collision_add)
            pointer(0.65, 0.1)
            io.add_mouse_button_event(0, True)
            editor.on_draw()
            pointer(0.9, 0.4)
            editor.on_draw()
            io.add_mouse_button_event(0, False)
            editor.on_draw()
            definition = editor.state.tile_definition(0, create=False)
            assert definition is not None
            collision = definition["collision"]
            assert isinstance(collision, dict)
            self.assertEqual(len(editor._collision_polygons(collision)), 2)
            self.assertFalse(editor._tile_collision_add)

    def test_tile_conversion_dialog_creates_destination_and_reloads_preview(self) -> None:
        editor = self.editor
        source = editor.state.add_tile_layer("Convert source")
        editor.state.add_color_tileset()
        editor.state._set_tile_cell(source, 0, 0, TileCell(0))
        editor.state.select_tile_rectangle((0, 0), (0, 0))
        editor._tile_conversion_name = "Platform"
        editor._tile_conversion_destination = ""
        editor._tile_conversion_new_layer = "Platforms"
        editor._tile_conversion_revision = editor.state.revision
        editor._tile_conversion_selection = editor.state.selected_tile_groups
        with (
            patch.object(imgui, "begin_popup_modal", side_effect=lambda name, *args: (name == "Convert Tiles to GameObject", True)),
            patch.object(imgui, "input_text", side_effect=lambda label, value: (False, value)),
            patch.object(editor, "_choice_property", side_effect=lambda label, index, values: (False, index)),
            patch.object(imgui, "checkbox", return_value=(False, False)),
            patch.object(imgui, "text_disabled"), patch.object(imgui, "same_line"),
            patch.object(imgui, "button", side_effect=lambda label: label == "Convert"),
            patch.object(imgui, "close_current_popup") as close_popup,
            patch.object(imgui, "end_popup"), patch.object(editor.preview, "reload") as reload,
        ):
            editor._draw_modals()
        self.assertIsNone(editor.state.error)
        self.assertEqual(editor.state.selected_layer["name"], "Platforms")
        self.assertEqual(editor.state.selected_records[0]["name"], "Platform")
        self.assertFalse(source["chunks"])
        reload.assert_called_once()
        close_popup.assert_called_once()

    def test_ready_runs_only_in_play_and_can_find_later_scene_objects(self) -> None:
        from g import Script

        ready_calls = []

        class PlayReadyScript(Script):
            def on_ready(self) -> None:
                ready_calls.append(self.scene.require_child("PlayerFellArea"))

        type_id = "editor_ready_test.PlayReadyScript"
        scene_types.register_component(type_id, PlayReadyScript)
        try:
            self.editor.state.add_object("empty")
            self.editor.state.add_component(type_id)
            self.editor.state.select(None)
            area = self.editor.state.add_object("area_box")
            area["name"] = "PlayerFellArea"
            self.editor.state.mark_changed("Named fall area")
            self.editor.preview.reload()
            self.editor.on_draw()
            self.assertEqual(ready_calls, [])
            self.assertIsNone(self.editor.preview.load_error)
            self.editor.preview.set_mode("play")
            self.assertEqual(len(ready_calls), 1)
            self.editor.on_draw()
            self.assertEqual(len(ready_calls), 1)
            self.editor.preview.set_mode("edit")
            self.editor.on_draw()
            self.assertEqual(len(ready_calls), 1)
            self.editor.preview.set_mode("play")
            self.assertEqual(len(ready_calls), 2)
        finally:
            scene_types.unregister_module("editor_ready_test")

    def test_play_scripts_load_resources_from_editor_asset_root(self) -> None:
        from g import Image, Script

        loaded_images = []

        class ResourceScript(Script):
            def on_ready(self) -> None:
                loaded_images.append(self.resources.load_image("coin.png"))

        type_id = "editor_resource_root_test.ResourceScript"
        scene_types.register_component(type_id, ResourceScript)
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                Image.solid(3, 5, (255, 255, 0))._source.save(str(root / "coin.png"))
                self.editor.state.asset_root = root
                self.editor.state.add_object("empty")
                self.editor.state.add_component(type_id)
                self.editor.preview.reload()
                self.assertIsNone(self.editor.preview.load_error)
                self.assertEqual(loaded_images, [])
                self.editor.preview.set_mode("play")
                self.assertIsNone(self.editor.preview.load_error)
                self.assertEqual(len(loaded_images), 1)
                self.assertEqual(loaded_images[0]._filename, root / "coin.png")
                self.assertEqual((loaded_images[0].width, loaded_images[0].height), (3, 5))
                self.editor.preview.set_mode("edit")
        finally:
            scene_types.unregister_module("editor_resource_root_test")

    def test_failed_ready_returns_to_edit_and_never_updates_uninitialized_scripts(self) -> None:
        from g import Script

        updates = []
        fail = [True]

        class FailingReady(Script):
            def on_ready(self):
                if fail[0]:
                    raise RuntimeError("Original ready failure")
                self.initialized = True

            def update(self, delta_time):
                updates.append(self.initialized)

        type_id = "editor_failed_ready_test.FailingReady"
        scene_types.register_component(type_id, FailingReady)
        try:
            self.editor.state.add_object("empty")
            self.editor.state.add_component(type_id)
            self.editor.preview.reload()
            previous = self.editor.preview.game
            with patch("sys.stderr", new_callable=StringIO) as console:
                self.editor.preview.set_mode("play")
            self.assertIn("RuntimeError: Original ready failure", console.getvalue())
            self.assertEqual(self.editor.preview.mode, "edit")
            self.assertEqual(self.editor.state.runtime_mode, "edit")
            self.assertIn("Original ready failure", self.editor.state.error)
            self.assertIsNotNone(self.editor.preview.game)
            self.assertIsNot(self.editor.preview.game, previous)
            self.editor.preview.update(0.1)
            self.assertEqual(updates, [])
            fail[0] = False
            self.editor.preview.set_mode("play")
            self.assertIsNone(self.editor.state.error)
            self.editor.preview.update(0.1)
            self.assertTrue(updates)
            self.assertTrue(all(updates))
            fail[0] = True
            with patch("sys.stderr", new_callable=StringIO):
                self.editor.preview.reload()
            self.assertEqual(self.editor.preview.mode, "edit")
            count = len(updates)
            self.editor.preview.update(0.1)
            self.assertEqual(len(updates), count)
        finally:
            scene_types.unregister_module("editor_failed_ready_test")

    def test_failed_game_start_does_not_publish_candidate_runtime(self) -> None:
        with (
            patch("g.Game._start", side_effect=RuntimeError("Start failed")),
            patch("sys.stderr", new_callable=StringIO) as console,
        ):
            self.editor.preview.set_mode("play")
        self.assertEqual(self.editor.preview.mode, "edit")
        self.assertFalse(self.editor.preview.game._started)
        self.assertIn("Start failed", self.editor.state.error)
        self.assertIn("Start failed", console.getvalue())
        self.editor.preview.update(0.1)

    def test_editor_updates_framebuffer_scale_between_screens_without_resize(self) -> None:
        io = imgui.get_io()
        original_size = tuple(io.display_size)
        original_scale = tuple(io.display_framebuffer_scale)
        render = self.editor.imgui_renderer.render
        rendered = []

        def draw_interface() -> None:
            imgui.begin("Display scale test")
            imgui.text("Same logical size on either screen")
            imgui.end()

        def capture_render(draw_data) -> None:
            with patch("imgui_bundle.python_backends.opengl_backend_programmable.gl.glViewport") as viewport:
                render(draw_data)
            rendered.append((
                tuple(draw_data.display_size),
                tuple(draw_data.framebuffer_scale),
                viewport.call_args_list,
            ))

        try:
            with (
                patch.object(self.editor.window, "get_size", return_value=(800, 600)),
                patch.object(self.editor.window, "get_framebuffer_size") as framebuffer_size,
                patch.object(self.editor, "_draw_interface", side_effect=draw_interface),
                patch.object(self.editor.render_target, "render"),
                patch.object(self.editor.imgui_renderer, "render", side_effect=capture_render),
            ):
                io.display_size = imgui.ImVec2(800, 600)
                io.display_framebuffer_scale = imgui.ImVec2(2, 2)
                for pixels, scale in (
                    ((1600, 1200), (2, 2)),
                    ((800, 600), (1, 1)),
                    ((1600, 1200), (2, 2)),
                    ((1200, 900), (1.5, 1.5)),
                ):
                    with self.subTest(framebuffer=pixels):
                        framebuffer_size.return_value = pixels
                        self.editor.on_draw()
                        self.assertIsNone(self.editor.state.error)
                        size, framebuffer_scale, viewports = rendered[-1]
                        self.assertEqual(size, (800, 600))
                        self.assertEqual(framebuffer_scale, scale)
                        self.assertIn(call(0, 0, *pixels), viewports)
        finally:
            io.display_size = imgui.ImVec2(*original_size)
            io.display_framebuffer_scale = imgui.ImVec2(*original_scale)

    def test_failed_editor_frame_is_finalized_before_next_draw(self) -> None:
        def fail_during_frame() -> None:
            imgui.begin("Interrupted frame")
            imgui.begin_group()
            raise RuntimeError("test frame failure")

        with patch.object(
            self.editor,
            "_draw_interface",
            side_effect=fail_during_frame,
        ):
            self.editor.on_draw()

        self.assertEqual(self.editor.state.error, "test frame failure")
        self.editor.on_draw()
        self.assertEqual(self.editor.state.error, "test frame failure")

    def test_editor_skips_render_target_when_scene_panel_is_hidden(self) -> None:
        self.editor._viewport_visible = False
        with (
            patch.object(self.editor, "_draw_interface"),
            patch.object(self.editor.render_target, "render") as render,
        ):
            self.editor.on_draw()

        render.assert_not_called()

    def test_one_sided_numeric_fields_pass_valid_imgui_bounds(self) -> None:
        with patch(
            "g_editor.panels.inspector.FloatField"
        ) as float_field:
            float_field.return_value.draw.return_value = FieldResult(
                False,
                1.0,
            )

            self.editor._float_property(
                "Zoom",
                1.0,
                minimum=0.001,
            )

        float_field.assert_called_once_with(
            "Zoom",
            None,
            speed=0.1,
            minimum=0.001,
            maximum=3.402823466e38,
        )

    def test_frame_rate_tracker_reports_recent_render_rate(self) -> None:
        tracker = _FrameRateTracker()
        for frame in range(61):
            tracker.record(frame / 60)

        self.assertAlmostEqual(tracker.fps, 60)
        tracker.reset()
        self.assertEqual(tracker.fps, 0)

    def test_register_project_script_creates_undoable_manifest(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name)
        script = root / "player.py"
        script.write_text("VALUE = 1\n", encoding="utf-8")
        self.editor = ImGuiEditor(asset_root=root, visible=False)

        self.editor._register_project_script(script)

        manifest = root / "g.project.json"
        self.assertTrue(manifest.is_file())
        project = self.editor.project
        assert project is not None
        self.assertEqual(
            project.script_modules,
            ("player",),
        )
        self.assertEqual(self.editor._popup_to_open, "Project Scripts")
        self.assertTrue(self.editor.state.undo())
        self.assertFalse(manifest.exists())

    def test_asset_scan_has_no_file_limit_and_skips_cache_and_git_directories(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name).resolve()
        assets = root / "sprites"
        assets.mkdir()
        expected = tuple(assets / f"sprite-{index:04d}.png" for index in range(510))
        for path in expected:
            path.touch()
        leaf = root / "zz-last" / "leaf.png"
        leaf.parent.mkdir()
        leaf.touch()
        for directory in (root / "__pycache__", assets / "__pycache__", root / ".git"):
            directory.mkdir()
            (directory / "ignored.txt").touch()

        self.editor = ImGuiEditor(asset_root=root, visible=False)
        self.editor._refresh_assets()

        self.assertEqual(self.editor._asset_files, (*expected, leaf))
        self.assertEqual(self.editor._asset_directories, (assets, leaf.parent))
        with patch.object(self.editor, "_draw_asset_file") as draw_file:
            self.editor._draw_asset_directory_contents(leaf.parent)
        draw_file.assert_called_once_with(leaf)
        (assets / "__pycache__" / "new.pyc").touch()
        (root / ".git" / "new-object").touch()
        self.assertFalse(self.editor._poll_asset_changes())
        added = leaf.parent / "new.png"
        added.touch()
        self.assertTrue(self.editor._poll_asset_changes())
        self.assertIn(added, self.editor._asset_files)
        self.assertEqual(len(self.editor._asset_files), 512)

    def test_asset_panel_picks_up_external_file_changes(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name).resolve()
        (root / "kept.txt").write_text("", encoding="utf-8")
        self.editor = ImGuiEditor(asset_root=root, visible=False)
        self.editor._refresh_assets()
        self.assertFalse(self.editor._poll_asset_changes())

        added = root / "levels" / "added.txt"
        added.parent.mkdir()
        added.write_text("", encoding="utf-8")
        (root / ".hidden").write_text("", encoding="utf-8")
        self.assertTrue(self.editor._poll_asset_changes())
        self.assertIn(added, self.editor._asset_files)
        self.assertIn(added.parent, self.editor._asset_directories)
        self.assertNotIn(root / ".hidden", self.editor._asset_files)

        self.editor._selected_asset = added
        self.editor._selected_asset_directory = added.parent
        added.unlink()
        added.parent.rmdir()
        self.assertTrue(self.editor._poll_asset_changes())
        self.assertNotIn(added, self.editor._asset_files)
        self.assertNotIn(added.parent, self.editor._asset_directories)
        self.assertIsNone(self.editor._selected_asset)
        self.assertEqual(self.editor._selected_asset_directory, root)
        self.assertFalse(self.editor._poll_asset_changes())

    def test_deleting_referenced_asset_asks_and_preview_survives(
        self,
    ) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name).resolve()
        image = root / "hero.png"
        unused = root / "unused.png"
        for path in (image, unused):
            pyglet.image.SolidColorImagePattern(
                (255, 0, 0, 255)
            ).create_image(8, 8).save(str(path))
        self.editor = ImGuiEditor(asset_root=root, visible=False)
        self.editor.state.add_sprite(image)
        self.editor.preview.reload()

        self.editor._request_delete_asset(unused)
        self.assertFalse(unused.exists())
        self.assertIsNone(self.editor._pending_asset_delete)

        self.editor._request_delete_asset(image)
        self.assertTrue(image.exists())
        self.assertEqual(
            self.editor._popup_to_open, "Delete Referenced Asset"
        )
        pending = self.editor._pending_asset_delete
        assert pending is not None
        self.assertEqual(pending[0], image)
        self.assertEqual(pending[1], ("Open scene: hero",))

        self.editor._confirm_pending_asset_delete()
        self.assertFalse(image.exists())
        self.assertIsNone(self.editor._pending_asset_delete)
        self.assertIsNotNone(self.editor.preview.game)
        self.editor.on_draw()
        self.assertIsNotNone(self.editor.preview.game)

        target = _AssetPickerTarget("sprite-image:0", "image", "image")
        self.editor._clear_asset(target)
        record = self.editor.state.selected
        assert record is not None
        components = record["components"]
        assert isinstance(components, list)
        renderer = components[0]
        assert isinstance(renderer, dict)
        properties = renderer["properties"]
        assert isinstance(properties, dict)
        self.assertIsNone(properties["image"])
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            self.editor._clear_asset(
                _AssetPickerTarget("tileset-path:a:b", "image", "image")
            )

        self.assertTrue(self.editor.state.undo())
        self.assertTrue(self.editor.state.undo())
        self.assertTrue(image.exists())

    def test_macos_save_dialog_separates_location_and_filename(
        self,
    ) -> None:
        process = Mock()
        process.poll.return_value = None
        with patch(
            "g_editor.application.subprocess.Popen",
            return_value=process,
        ) as popen:
            _MacSaveDialog(
                "Save Scene As",
                Path("/projects/my game"),
                "level.scene.json",
            )

        command = popen.call_args.args[0]
        script = command[2]
        self.assertIn(
            'default location (POSIX file "/projects/my game")',
            script,
        )
        self.assertIn(
            'default name "level.scene.json"',
            script,
        )

    def test_create_python_script_uses_name_and_registers_module(
        self,
    ) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name)
        self.editor = ImGuiEditor(asset_root=root, visible=False)

        script = self.editor._create_python_script("PlayerController")

        self.assertEqual(script.name, "player_controller.py")
        content = script.read_text(encoding="utf-8")
        self.assertIn(
            '@scene_component("player_controller.PlayerController")',
            content,
        )
        self.assertIn("class PlayerController(Script):", content)
        self.assertNotIn("speed: float", content)
        project = self.editor.project
        assert project is not None
        self.assertEqual(project.script_modules, ("player_controller",))
        self.assertEqual(self.editor._popup_to_open, "Project Scripts")

    def test_registered_script_drop_adds_component_to_object(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name)
        self.editor = ImGuiEditor(asset_root=root, visible=False)
        with patch.object(
            self.editor._trust_store,
            "is_trusted",
            return_value=True,
        ):
            script = self.editor._create_python_script(
                "DragAttachBehavior"
            )
            record = self.editor.state.add_object("empty")
            identifier = str(record["id"])
            self.editor._attach_project_script(script, identifier)

        components = record.get("components")
        assert isinstance(components, list)
        component_types = [
            component.get("type")
            for component in components
            if isinstance(component, dict)
        ]
        self.assertEqual(
            component_types,
            ["drag_attach_behavior.DragAttachBehavior"],
        )
        self.assertIsNone(self.editor.state.error)

    def test_script_reload_failure_logs_to_console_and_success_clears_only_its_error(self) -> None:
        from g.project import ProjectImportError

        project = Mock()
        project.script_modules = ("player",)
        project.changed_scripts.return_value = ("player",)
        project.import_scripts.side_effect = ProjectImportError("SyntaxError: invalid syntax")
        self.editor.project = project
        self.editor._project_trust_pending = False
        with (
            patch.object(self.editor._trust_store, "is_trusted", return_value=True),
            patch.object(self.editor.state, "sync_script_components"),
            patch.object(self.editor.preview, "reload") as reload_preview,
            patch("sys.stderr", new_callable=StringIO) as console,
        ):
            self.editor._reload_changed_scripts()
            self.assertIn("SyntaxError: invalid syntax", console.getvalue())
            self.assertEqual(self.editor.state.error, "SyntaxError: invalid syntax")
            reload_preview.assert_not_called()
            project.import_scripts.side_effect = None
            self.editor._reload_changed_scripts()
            self.assertIsNone(self.editor.state.error)
            reload_preview.assert_called_once()
            project.import_scripts.side_effect = ProjectImportError("Another script error")
            self.editor._reload_changed_scripts()
            self.editor.state.error = "Unrelated asset error"
            project.import_scripts.side_effect = None
            self.editor._reload_changed_scripts()
            self.assertEqual(self.editor.state.error, "Unrelated asset error")
        self.editor.project = None

    def test_viewport_does_not_display_general_script_reload_errors(self) -> None:
        self.editor.state.error = "SyntaxError: unfinished autosaved edit"
        with (
            patch.object(self.editor, "_draw_interface", side_effect=self.editor._draw_viewport),
            patch.object(imgui, "text_wrapped", wraps=imgui.text_wrapped) as messages,
        ):
            self.editor.on_draw()
        self.assertFalse(any(
            "SyntaxError" in str(entry.args) for entry in messages.call_args_list
        ))

    def test_component_scripts_menu_always_offers_new_script(self) -> None:
        record = self.editor.state.add_object("empty")
        with (
            patch.object(type(self.editor.state), "available_component_types", new_callable=PropertyMock, return_value=()),
            patch.object(self.editor, "_reload_changed_scripts") as reload_scripts,
            patch("g_editor.panels.inspector.imgui.separator"),
            patch("g_editor.panels.inspector.imgui.text_disabled"),
            patch("g_editor.panels.inspector.imgui.button", return_value=True),
            patch("g_editor.panels.inspector.imgui.open_popup"),
            patch("g_editor.panels.inspector.imgui.begin_popup", return_value=True),
            patch("g_editor.panels.inspector.imgui.end_popup"),
            patch("g_editor.panels.inspector.imgui.begin_menu", side_effect=lambda label: label == "Scripts") as menu,
            patch("g_editor.panels.inspector.imgui.end_menu"),
            patch("g_editor.panels.inspector.imgui.menu_item", side_effect=lambda label, *args: (label == "New Script", False)),
        ):
            self.editor._draw_add_component()
        reload_scripts.assert_called_once()
        self.assertIn(call("Scripts"), menu.call_args_list)
        self.assertEqual(self.editor._new_script_object_id, record["id"])
        self.assertEqual(self.editor._popup_to_open, "Create Python Script")

    def test_component_menu_supports_custom_script_category_and_display_name(self) -> None:
        from g import Script, scene_component
        from g_editor.panels.inspector import _component_group, _component_label
        name = "tests.custom_category_script.Behavior"

        class Behavior(Script):
            pass

        Behavior.__module__ = "custom_category_script"
        scene_component(name, category="Characters", display_name="Hero Behavior")(Behavior)

        self.editor.state.add_object("empty")
        try:
            self.assertEqual(_component_group(name), "Characters")
            self.assertEqual(_component_label(name), "Hero Behavior")
            with (
                patch("g_editor.panels.inspector.imgui.begin_menu", return_value=True) as groups,
                patch("g_editor.panels.inspector.imgui.end_menu"),
                patch("g_editor.panels.inspector.imgui.separator"),
                patch("g_editor.panels.inspector.imgui.text_disabled"),
                patch("g_editor.panels.inspector.imgui.menu_item", return_value=(False, False)) as entries,
            ):
                self.editor._draw_component_type_menu()
            self.assertIn(call("Characters"), groups.call_args_list)
            self.assertIn(call("Scripts"), groups.call_args_list)
            self.assertIn(call("Hero Behavior", "", False), entries.call_args_list)
            self.assertIn(call("New Script", "", False), entries.call_args_list)
            self.assertIn(call("Renderers"), groups.call_args_list)
            self.assertIn(call("Circle Renderer", "", False), entries.call_args_list)
            self.assertIn(call("Sprite Renderer", "", False), entries.call_args_list)
        finally:
            scene_types.unregister_module(Behavior.__module__)

    def test_create_dialog_platformer_template_attaches_motor_and_project_script(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name)
        self.editor = ImGuiEditor(asset_root=root, visible=False)
        record = self.editor.state.add_object("rectangle")
        module = "template_player"
        try:
            with patch.object(self.editor._trust_store, "is_trusted", return_value=True):
                self.editor._begin_create_python_script(attach_to_selected=True)
                self.editor._new_script_name = "TemplatePlayer"
                self.editor._new_script_template = 1
                original = imgui.button

                def create(label, size=imgui.ImVec2(0, 0)):
                    clicked = original(label, size)
                    return label == "Create" or clicked

                with patch("g_editor.application.imgui.button", side_effect=create):
                    self.editor.on_draw()
            self.assertIsNone(self.editor.state.error)
            source = (root / f"{module}.py").read_text(encoding="utf-8")
            self.assertIn("class TemplatePlayer(Script)", source)
            self.assertIn("requires_components(PlatformerMotor, auto_add=True)", source)
            components = record["components"]
            assert isinstance(components, list)
            names = [item["type"] for item in components if isinstance(item, dict)]
            self.assertIn("g.platformer_motor", names)
            self.assertIn("g.physics_body", names)
            self.assertIn(f"{module}.TemplatePlayer", names)
        finally:
            scene_types.unregister_module(module)
            import sys
            sys.modules.pop(module, None)

    def test_add_component_menu_pastes_clipboard_as_new_component(self) -> None:
        record = self.editor.state.add_object("empty")
        self.editor._component_clipboard = {
            "type": "g.sprite_renderer",
            "properties": {"image": None, "enabled": False},
        }
        def choose_paste(label, shortcut, selected, enabled=True):
            return label == "Paste Component as New" and enabled, False
        with (
            patch.object(self.editor, "_reload_changed_scripts"),
            patch("g_editor.panels.inspector.imgui.separator"),
            patch("g_editor.panels.inspector.imgui.button", return_value=True),
            patch("g_editor.panels.inspector.imgui.open_popup"),
            patch("g_editor.panels.inspector.imgui.begin_popup", return_value=True),
            patch("g_editor.panels.inspector.imgui.end_popup"),
            patch("g_editor.panels.inspector.imgui.begin_menu", return_value=False),
            patch("g_editor.panels.inspector.imgui.is_item_hovered", return_value=False),
            patch("g_editor.panels.inspector.imgui.menu_item", side_effect=choose_paste) as menu,
        ):
            self.editor._draw_add_component()
            self.assertEqual(record["components"], [self.editor._component_clipboard])
            menu.reset_mock()
            self.editor._draw_add_component()
            self.assertIn(
                call("Paste Component as New", "", False, False),
                menu.call_args_list,
            )
            self.assertEqual(record["components"], [self.editor._component_clipboard])

    def test_inspector_context_menu_adds_component(self) -> None:
        record = self.editor.state.add_object("empty")
        with (
            patch.object(self.editor, "_reload_changed_scripts") as refresh,
            patch("g_editor.panels.inspector.imgui.begin_popup_context_window", return_value=True) as popup,
            patch("g_editor.panels.inspector.imgui.end_popup"),
            patch("g_editor.panels.inspector.imgui.begin_menu", side_effect=lambda label: label in {"Add Component", "Physics"}),
            patch("g_editor.panels.inspector.imgui.end_menu"),
            patch("g_editor.panels.inspector.imgui.is_item_hovered", return_value=False),
            patch("g_editor.panels.inspector.imgui.menu_item", side_effect=lambda label, *args: (label == "Physics Body", False)),
        ):
            self.editor._draw_inspector_context_menu()
        refresh.assert_called_once()
        popup.assert_called_once_with(
            "Inspector Context Menu",
            int(imgui.PopupFlags_.mouse_button_right)
            | int(imgui.PopupFlags_.no_open_over_items),
        )
        components = record["components"]
        assert isinstance(components, list)
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0]["type"], "g.physics_body")

    def test_inspector_context_menu_pastes_component_and_disables_without_copy(self) -> None:
        record = self.editor.state.add_object("empty")
        with (
            patch.object(self.editor, "_reload_changed_scripts"),
            patch("g_editor.panels.inspector.imgui.begin_popup_context_window", return_value=True),
            patch("g_editor.panels.inspector.imgui.end_popup"),
            patch("g_editor.panels.inspector.imgui.begin_menu", return_value=False),
            patch("g_editor.panels.inspector.imgui.is_item_hovered", return_value=False),
            patch("g_editor.panels.inspector.imgui.menu_item", side_effect=lambda label, shortcut, selected, enabled: (enabled, False)) as menu,
        ):
            self.editor._draw_inspector_context_menu()
            menu.assert_called_with("Paste Component as New", "", False, False)
            self.assertEqual(record["components"], [])
            self.editor._component_clipboard = {
                "type": "g.sprite_renderer", "properties": {"image": None},
            }
            self.editor._draw_inspector_context_menu()
            menu.assert_called_with("Paste Component as New", "", False, True)
            self.assertEqual(record["components"], [self.editor._component_clipboard])

    def test_sprite_renderer_inspector_flips_refresh_preview_and_support_undo(self) -> None:
        self.editor.state.add_object("empty")
        self.editor.state.add_component("g.sprite_renderer")
        draw_property = self.editor._property_widget
        labels = []

        def flip(label, value, identifier, **kwargs):
            labels.append(label.split("##")[0])
            changed, result = draw_property(label, value, identifier, **kwargs)
            if label.split("##")[0] in {"Flip X", "Flip Y"}:
                return True, True
            return changed, result

        self.editor.on_draw()
        with patch.object(self.editor, "_property_widget", side_effect=flip):
            self.editor.on_draw()
        self.assertIn("Flip X", labels)
        self.assertIn("Flip Y", labels)
        runtime = self.editor.preview.selected_runtime_object()
        assert runtime is not None
        renderer = runtime.require_component(SpriteRenderer)
        self.assertTrue(renderer.flip_x)
        self.assertTrue(renderer.flip_y)
        self.assertTrue(self.editor.state.undo())
        self.editor.on_draw()
        runtime = self.editor.preview.selected_runtime_object()
        assert runtime is not None
        renderer = runtime.require_component(SpriteRenderer)
        self.assertTrue(renderer.flip_x)
        self.assertFalse(renderer.flip_y)
        self.assertTrue(self.editor.state.undo())
        self.editor.on_draw()
        runtime = self.editor.preview.selected_runtime_object()
        assert runtime is not None
        renderer = runtime.require_component(SpriteRenderer)
        self.assertFalse(renderer.flip_x)
        self.assertFalse(renderer.flip_y)

    def test_new_component_script_is_created_loaded_and_attached(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name)
        self.editor = ImGuiEditor(asset_root=root, visible=False)
        record = self.editor.state.add_object("empty")
        module = "component_menu_behavior"
        try:
            with patch.object(self.editor._trust_store, "is_trusted", return_value=True):
                self.editor._begin_create_python_script(attach_to_selected=True)
                self.editor._new_script_name = "ComponentMenuBehavior"
                original_button = imgui.button

                def create_script(
                    label: str, size: imgui.ImVec2 = imgui.ImVec2(0, 0)
                ) -> bool:
                    clicked = original_button(label, size)
                    return label == "Create" or clicked

                with patch("g_editor.application.imgui.button", side_effect=create_script):
                    self.editor.on_draw()
            self.assertTrue((root / f"{module}.py").is_file())
            components = record["components"]
            assert isinstance(components, list)
            self.assertTrue(any(
                isinstance(component, dict)
                and component.get("type") == f"{module}.ComponentMenuBehavior"
                for component in components
            ))
            self.assertIsNone(self.editor.state.error)
        finally:
            import sys
            scene_types.unregister_module(module)
            sys.modules.pop(module, None)

    def test_new_component_script_waits_for_trust_before_attaching(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name)
        self.editor = ImGuiEditor(asset_root=root, visible=False)
        record = self.editor.state.add_object("empty")
        module = "trust_menu_behavior"
        original_button = imgui.button

        def click_create(
            label: str, size: imgui.ImVec2 = imgui.ImVec2(0, 0)
        ) -> bool:
            clicked = original_button(label, size)
            return label == "Create" or clicked

        def click_trust(
            label: str, size: imgui.ImVec2 = imgui.ImVec2(0, 0)
        ) -> bool:
            clicked = original_button(label, size)
            return label == "Trust and Load" or clicked

        try:
            with patch.object(self.editor._trust_store, "is_trusted", return_value=False):
                self.editor._begin_create_python_script(attach_to_selected=True)
                self.editor._new_script_name = "TrustMenuBehavior"
                with patch("g_editor.application.imgui.button", side_effect=click_create):
                    self.editor.on_draw()
            self.assertEqual(record["components"], [])
            self.assertTrue(self.editor._project_trust_pending)
            self.assertEqual(
                self.editor._pending_script_attachment,
                ((root / f"{module}.py").resolve(), str(record["id"])),
            )
            with (
                patch.object(self.editor._trust_store, "trust") as trust,
                patch.object(self.editor._trust_store, "is_trusted", return_value=True),
                patch("g_editor.application.imgui.button", side_effect=click_trust),
            ):
                self.editor.on_draw()
            trust.assert_called_once()
            self.assertIsNone(self.editor._pending_script_attachment)
            self.assertFalse(self.editor._project_trust_pending)
            components = record["components"]
            assert isinstance(components, list)
            self.assertTrue(any(
                isinstance(component, dict)
                and component.get("type") == f"{module}.TrustMenuBehavior"
                for component in components
            ))
            self.assertIsNone(self.editor.state.error)
        finally:
            import sys
            scene_types.unregister_module(module)
            sys.modules.pop(module, None)

    def test_imported_python_script_is_registered(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name)
        self.editor = ImGuiEditor(asset_root=root, visible=False)
        with TemporaryDirectory() as source_directory:
            source = Path(source_directory) / "imported_behavior.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")

            imported = self.editor._import_external_assets([str(source)])

        self.assertEqual(
            imported,
            (root.resolve() / "imported_behavior.py",),
        )
        project = self.editor.project
        assert project is not None
        self.assertEqual(
            project.script_modules,
            ("imported_behavior",),
        )

    def test_enter_commits_asset_folder_rename(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name)
        self.editor = ImGuiEditor(asset_root=root, visible=False)
        directory = self.editor._create_asset_folder()
        self.editor._begin_rename(
            "asset",
            str(directory),
            directory.name,
        )
        self.editor._rename_buffer = "Scripts"

        self.editor.on_key_press(key.ENTER, 0)

        self.assertFalse(directory.exists())
        self.assertTrue((root / "Scripts").is_dir())
        self.assertIsNone(self.editor._rename_target)

    def test_builtin_component_templates_restore_in_preview(self) -> None:
        component_types = (
            "g.circle_renderer",
            "g.rect_renderer",
            "g.text_renderer",
            "g.line_renderer",
            "g.triangle_renderer",
            "g.polygon_renderer",
            "g.collider",
            "g.area",
            "g.sprite_animator",
            "g.physics_body",
            "g.top_down_motor",
            "g.platformer_motor",
        )

        for component_type in component_types:
            with self.subTest(component_type=component_type):
                self.editor.state.new()
                self.editor.state.add_object("empty")
                if component_type in {
                    "g.top_down_motor",
                    "g.platformer_motor",
                    "g.area",
                }:
                    self.editor.state.add_component("g.collider")
                self.editor.state.add_component(component_type)
                self.editor.state.error = None
                self.editor.preview.reload()
                self.assertIsNone(self.editor.state.error)
                self.assertIsNotNone(self.editor.preview.game)

    def test_editor_renders_engine_texture_and_picks_scene_object(self) -> None:
        record = self.editor.state.add_object(
            "rectangle",
            x=640,
            y=360,
        )

        self.editor.on_draw()
        self.editor.on_draw()

        self.assertIsNone(self.editor.state.error)
        self.assertGreater(self.editor.render_target.width, 1)
        self.assertGreater(self.editor.render_target.height, 1)
        screen_x, screen_y = self.editor.preview.world_to_screen(640, 360)
        self.assertEqual(
            self.editor.preview.pick(screen_x, screen_y),
            record["id"],
        )
        texture = self.editor.render_target.texture
        self.assertIsNotNone(texture)
        assert texture is not None
        pixels = texture.get_image_data().get_data(
            "RGBA",
            texture.width * 4,
        )
        corner_colors = {
            tuple(
                pixels[(y * texture.width + x) * 4 + channel]
                for channel in range(4)
            )
            for x, y in (
                (1, 1),
                (texture.width - 2, 1),
                (1, texture.height - 2),
                (texture.width - 2, texture.height - 2),
            )
        }
        self.assertEqual(len(corner_colors), 1)
        white_pixels = [
            (x, y)
            for y in range(texture.height)
            for x in range(texture.width)
            if all(
                pixels[(y * texture.width + x) * 4 + channel] > 240
                for channel in range(3)
            )
        ]
        self.assertEqual(len(white_pixels), 64 * 64)
        center_x = sum(point[0] for point in white_pixels) / len(
            white_pixels
        )
        center_y = sum(point[1] for point in white_pixels) / len(
            white_pixels
        )
        self.assertAlmostEqual(center_x, texture.width / 2, delta=1)
        self.assertAlmostEqual(center_y, texture.height / 2, delta=1)

    def test_play_mode_forwards_keys_without_viewport_hover(self) -> None:
        self.editor.preview.set_mode("play")
        self.editor._viewport_hovered = False
        with patch.object(
            self.editor.preview,
            "forward_key",
        ) as forward_key:
            self.editor.on_key_press(key.SPACE, 0)
            self.editor.on_key_release(key.SPACE, 0)

        self.assertEqual(
            forward_key.call_args_list,
            [
                call(True, key.SPACE, 0),
                call(False, key.SPACE, 0),
            ],
        )

    def test_console_captures_python_output(self) -> None:
        print("hello from a game script")

        self.assertIn(
            "hello from a game script",
            self.editor._console_buffer.snapshot(),
        )

    def test_console_preserves_stdout_stderr_colors_and_trims_history(self) -> None:
        buffer = _ConsoleBuffer(maximum_characters=12)
        stdout = _TeeStream(StringIO(), buffer)
        stderr = _TeeStream(StringIO(), buffer, error=True)
        stdout.write("hello")
        stdout.write("\n")
        stderr.write("error\n")
        self.assertEqual(buffer.entries(), (("hello\n", False), ("error\n", True)))
        stdout.write("ok\n")
        self.assertEqual(buffer.snapshot(), "lo\nerror\nok\n")
        self.assertEqual(
            buffer.entries(), (("lo\n", False), ("error\n", True), ("ok\n", False))
        )
        buffer.clear()
        self.assertEqual(buffer.entries(), ())
        self.assertEqual(buffer.snapshot(), "")

    def test_console_draws_stdout_white_and_errors_red(self) -> None:
        self.editor._console_buffer.clear()
        self.editor._console_stdout.write("normal output\n")
        self.editor._console_stderr.write("error output\n")
        with (
            patch.object(self.editor, "_begin_panel", return_value=True),
            patch("g_editor.application.imgui.button", return_value=False),
            patch("g_editor.application.imgui.same_line"),
            patch("g_editor.application.imgui.checkbox", return_value=(False, False)),
            patch("g_editor.application.imgui.separator"),
            patch("g_editor.application.imgui.begin_child", return_value=True),
            patch("g_editor.application.imgui.end_child"),
            patch("g_editor.application.imgui.end"),
            patch("g_editor.application.imgui.set_scroll_here_y"),
            patch("g_editor.application.imgui.push_font") as push_font,
            patch("g_editor.application.imgui.pop_font") as pop_font,
            patch("g_editor.application.imgui.push_style_color") as color,
            patch("g_editor.application.imgui.pop_style_color") as pop,
            patch("g_editor.application.imgui.text_unformatted") as text,
        ):
            self.editor._draw_console()
        self.assertEqual(
            text.call_args_list, [call("normal output"), call("error output")]
        )
        push_font.assert_called_once_with(
            self.editor._console_font,
            0.0,
        )
        pop_font.assert_called_once_with()
        colors = [entry.args[1] for entry in color.call_args_list]
        self.assertEqual((colors[0].x, colors[0].y, colors[0].z), (1, 1, 1))
        self.assertAlmostEqual(colors[1].x, 1)
        self.assertAlmostEqual(colors[1].y, 0.35)
        self.assertAlmostEqual(colors[1].z, 0.3)
        self.assertEqual(pop.call_count, 2)

    def test_preview_load_errors_reach_console_and_object_inspector(self) -> None:
        record = self.editor.state.add_object("empty")
        record["name"] = "Player object"
        components = record["components"]
        assert isinstance(components, list)
        components.append({
            "type": "g.platformer_motor",
            "properties": {"enabled": True},
        })
        self.editor.state.mark_changed("Added invalid controller")
        self.editor.preview.synchronize()

        expected = "PlatformerMotor requires Collider"
        self.assertIsNotNone(self.editor.preview.game)
        self.assertIsNone(self.editor.preview.load_error)
        output = self.editor._console_buffer.snapshot()
        self.assertIn("Edit preview warning", output)
        self.assertIn(expected, output)
        self.editor.preview.synchronize()
        self.assertEqual(self.editor._console_buffer.snapshot(), output)
        self.editor.on_draw()
        with (
            patch("g_editor.panels.inspector.imgui.text_wrapped", wraps=imgui.text_wrapped) as text,
        ):
            self.editor.on_draw()
        self.assertIn(call(f"Warning: {expected}"), text.call_args_list)
        self.assertFalse(any(
            entry.args and str(entry.args[0]).startswith("Scene preview unavailable:")
            for entry in text.call_args_list
        ))
        self.assertGreater(
            self.editor._viewport_rect[3] - self.editor._viewport_rect[1], 100
        )
        self.assertEqual(len(components), 1)
        self.editor.preview.set_mode("play")
        self.assertEqual(self.editor.preview.mode, "edit")
        self.assertIsNotNone(self.editor.preview.game)
        self.assertIn(expected, self.editor.state.error or "")
        self.assertIn("game object 'Player object'", self.editor.state.error or "")
        self.assertIn(str(record["id"]), self.editor.state.error or "")
        self.assertIn("Traceback", self.editor._console_buffer.snapshot())

        self.editor.state.add_component("g.collider")
        self.editor.on_draw()
        self.assertIsNotNone(self.editor.preview.game)
        self.assertIsNone(self.editor.preview.load_error)
        self.assertEqual(self.editor.state.object_component_warnings(record), ())
        self.editor.preview.set_mode("play")
        self.assertEqual(self.editor.preview.mode, "play")

    def test_missing_asset_preview_errors_are_logged_to_console(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["tilesets"] = [{
            "id": "missing",
            "type": "atlas",
            "first_gid": 0,
            "image": "does-not-exist.png",
            "tile_size": [16, 16],
            "columns": 1,
            "tile_count": 1,
            "tiles": [],
        }]
        self.editor.state.mark_changed("Added missing atlas")
        self.editor.preview.synchronize()
        self.assertIsNone(self.editor.preview.game)
        self.assertIn(
            "does-not-exist.png", self.editor._console_buffer.snapshot()
        )
        self.assertIsNotNone(self.editor.preview.load_error)

    def test_export_does_not_focus_console_after_worker_starts(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self._project_temporary = TemporaryDirectory()
        root = Path(self._project_temporary.name)
        self.editor = ImGuiEditor(asset_root=root, visible=False)
        self.editor.state.save(root / "scene.scene.json")
        self.editor._export_startup_scene = "scene.scene.json"

        def export(project: Path, *, output: object) -> ExportResult:
            del project
            assert callable(output)
            output("build output")
            return ExportResult(root / "dist" / "Game", root / "Game")

        with (
            patch("g_editor.application.export_project", side_effect=export),
            patch.object(imgui, "set_window_focus") as focus,
        ):
            self.assertTrue(self.editor._start_export(run_after=False))
            assert self.editor._export_thread is not None
            self.editor._export_thread.join()
            self.editor._poll_export()

        focus.assert_not_called()
        self.assertIn(
            "[export] build output",
            self.editor._console_buffer.snapshot(),
        )

    def test_inter_font_and_default_dock_layout_are_active(self) -> None:
        font = imgui.get_io().font_default
        self.assertIsNotNone(font)
        assert font is not None
        self.assertEqual(
            font.get_debug_name(),
            "Inter_18pt-Regular.ttf",
        )
        self.assertEqual(
            self.editor._console_font.get_debug_name(),
            "JetBrainsMono-Regular.ttf",
        )
        self.assertTrue(
            font.is_glyph_in_font(
                ord(icons_fontawesome_6.ICON_FA_PLAY)
            )
        )
        self.assertTrue(
            font.is_glyph_in_font(
                ord(icons_fontawesome_6.ICON_FA_STOP)
            )
        )
        for icon in (
            icons_fontawesome_6.ICON_FA_ARROWS_UP_DOWN_LEFT_RIGHT,
            icons_fontawesome_6.ICON_FA_ROTATE,
            icons_fontawesome_6.ICON_FA_MAXIMIZE,
            icons_fontawesome_6.ICON_FA_GLOBE,
            icons_fontawesome_6.ICON_FA_CUBE,
        ):
            with self.subTest(icon=icon):
                self.assertTrue(font.is_glyph_in_font(ord(icon)))
        self.assertTrue(
            imgui.get_io().config_flags
            & imgui.ConfigFlags_.docking_enable.value
        )

        self.editor.on_draw()
        self.editor.on_draw()

        for name in (
            "Layers",
            "UI Views",
            "Hierarchy###LayerContent",
            "Assets",
            "Tile Palette",
            "Scene",
            "Inspector",
            "Console",
        ):
            window = imgui.internal.find_window_by_name(name)
            self.assertIsNotNone(window)
            assert window is not None
            self.assertNotEqual(window.dock_id, 0)
        self.assertIsNone(imgui.internal.find_window_by_name("Tools"))
        palette = imgui.internal.find_window_by_name("Tile Palette")
        status = imgui.internal.find_window_by_name("##editor-status-bar")
        assert palette is not None
        assert status is not None
        self.assertEqual(status.dock_id, 0)
        self.assertIsNone(imgui.internal.find_window_by_name("Status"))
        viewport = imgui.get_main_viewport()
        self.assertAlmostEqual(status.pos.y + status.size.y, viewport.pos.y + viewport.size.y)
        self.assertAlmostEqual(status.size.x, viewport.size.x)
        self.assertAlmostEqual(status.size.y, imgui.get_frame_height())
        self.assertLessEqual(palette.pos.y + palette.size.y, status.pos.y)
        layers = imgui.internal.find_window_by_name("Layers")
        ui_views = imgui.internal.find_window_by_name("UI Views")
        assert layers is not None
        assert ui_views is not None
        self.assertEqual(layers.dock_id, ui_views.dock_id)

    def test_focusing_ui_views_selects_startup_view(self) -> None:
        startup = next(
            view
            for view in self.editor.state.document.ui_views
            if view.get("startup") is True
        )
        self.assertIsNone(self.editor.state.selected_ui_view)

        self.editor._select_startup_ui_view()

        self.assertIs(self.editor.state.selected_ui_view, startup)

    def test_ui_view_uses_virtual_screen_in_edit_viewport(self) -> None:
        startup = next(
            view
            for view in self.editor.state.document.ui_views
            if view.get("startup") is True
        )
        self.editor.state.select_layer(str(startup["id"]))
        self.editor.state.add_gui_element(
            "button",
            x=100,
            y=100,
        )

        self.editor.on_draw()
        self.editor.on_draw()

        game = self.editor.preview.game
        self.assertIsNotNone(game)
        assert game is not None
        self.assertEqual(
            (
                self.editor.render_target.width,
                self.editor.render_target.height,
            ),
            (game.width, game.height),
        )
        left, bottom, right, top = self.editor._viewport_rect
        self.assertAlmostEqual(
            (right - left) / (top - bottom),
            game.width / game.height,
            places=2,
        )

    def test_status_bar_message_and_metadata_do_not_overlap(self) -> None:
        text_unformatted = imgui.text_unformatted
        text_colored = imgui.text_colored
        text_disabled = imgui.text_disabled
        rectangles = {}

        def capture(kind):
            if imgui.internal.get_current_window().name == "##editor-status-bar":
                rectangles[kind] = (imgui.get_item_rect_min(), imgui.get_item_rect_max())

        def message(text):
            text_unformatted(text)
            capture("message")

        def error(color, text):
            text_colored(color, text)
            capture("message")

        def metadata(text):
            text_disabled(text)
            capture("metadata")

        with (
            patch("g_editor.application.imgui.text_unformatted", side_effect=message),
            patch("g_editor.application.imgui.text_colored", side_effect=error),
            patch("g_editor.application.imgui.text_disabled", side_effect=metadata),
        ):
            for active_error in (None, "Could not preview animation:\nmissing resource"):
                with self.subTest(error=active_error):
                    self.editor.state.status = "Saved scene"
                    self.editor.state.error = active_error
                    self.editor.on_draw()
                    message_minimum, message_maximum = rectangles["message"]
                    metadata_minimum, _ = rectangles["metadata"]
                    self.assertGreaterEqual(metadata_minimum.x, message_maximum.x)
                    self.assertEqual(message_minimum.y, metadata_minimum.y)
                    self.assertEqual(message_maximum.y - message_minimum.y, imgui.get_text_line_height())

    def test_status_bar_caps_errors_and_retains_full_text_in_console(self) -> None:
        full_error = "Could not load animation:\n" + "very long error details " * 100
        self.editor.state.error = full_error
        colored = imgui.text_colored
        summaries = []

        def error(color, text):
            colored(color, text)
            if imgui.internal.get_current_window().name == "##editor-status-bar":
                summaries.append(text)

        with patch("g_editor.application.imgui.text_colored", side_effect=error):
            self.editor.on_draw()
            self.editor.on_draw()
        self.assertEqual(len(summaries), 2)
        for summary in summaries:
            self.assertNotIn("\n", summary)
            self.assertIn("...", summary)
            self.assertTrue(summary.endswith("(see Console)"))
            self.assertLessEqual(len(summary), 137)
            self.assertLessEqual(imgui.calc_text_size(summary).x, self.editor.window.width * 0.5)
        self.assertEqual(self.editor._console_buffer.snapshot().count(full_error), 1)
        self.assertTrue(any(full_error in text and is_error for text, is_error in self.editor._console_buffer.entries()))

    def test_layer_content_panel_adapts_to_selected_layer(self) -> None:
        self.editor.on_draw()
        self.editor.on_draw()

        hierarchy = imgui.internal.find_window_by_name(
            "Hierarchy###LayerContent"
        )
        self.assertIsNotNone(hierarchy)
        assert hierarchy is not None
        content_dock_id = hierarchy.dock_id

        self.editor.state.add_tile_layer("Ground")
        self.editor.on_draw()

        tilemap = imgui.internal.find_window_by_name(
            "Tilemap###LayerContent"
        )
        self.assertIsNotNone(tilemap)
        assert tilemap is not None
        self.assertEqual(tilemap.dock_id, content_dock_id)

    def test_empty_hierarchy_space_clears_object_selection(self) -> None:
        self.editor.state.add_object("rectangle")
        self.editor.on_draw()
        self.editor.on_draw()
        minimum_x, minimum_y, maximum_x, maximum_y = (
            self.editor._layer_content_rect
        )
        io = imgui.get_io()

        io.add_mouse_pos_event(
            maximum_x - 8,
            maximum_y - 8,
        )
        io.add_mouse_button_event(0, True)
        self.editor.on_draw()
        io.add_mouse_button_event(0, False)
        self.editor.on_draw()

        self.assertIsNone(self.editor.state.selected)
        self.assertIsNotNone(self.editor.state.selected_layer)
        self.assertGreater(maximum_x, minimum_x)
        self.assertGreater(maximum_y, minimum_y)

    def test_empty_gui_hierarchy_space_clears_gui_selection(self) -> None:
        self.editor.state.add_ui_view("Test HUD")
        self.editor.state.add_gui_element("button")
        self.editor.on_draw()
        self.editor.on_draw()
        _, _, maximum_x, maximum_y = self.editor._layer_content_rect
        io = imgui.get_io()

        io.add_mouse_pos_event(
            maximum_x - 8,
            maximum_y - 8,
        )
        io.add_mouse_button_event(0, True)
        self.editor.on_draw()
        io.add_mouse_button_event(0, False)
        self.editor.on_draw()

        self.assertIsNone(self.editor.state.selected_gui_element)
        self.assertIsNotNone(self.editor.state.selected_layer)

    def test_hierarchy_selection_reveals_offscreen_object(self) -> None:
        visible = self.editor.state.add_object(
            "rectangle",
            x=400,
            y=300,
        )
        offscreen = self.editor.state.add_object(
            "rectangle",
            x=5000,
            y=-3000,
        )
        self.editor.state.select(str(visible["id"]))
        self.editor.on_draw()
        self.editor.on_draw()

        self.editor._select_from_hierarchy(
            str(offscreen["id"]),
            [str(visible["id"]), str(offscreen["id"])],
        )

        self.assertEqual(
            self.editor.preview.editor_camera,
            [5400, -2700],
        )
        runtime = self.editor.preview.selected_runtime_object()
        self.assertIsNotNone(runtime)
        assert runtime is not None
        bounds = self.editor.preview.object_screen_bounds(runtime)
        self.assertGreaterEqual(bounds[2], 0)
        self.assertLessEqual(bounds[0], self.editor.render_target.width)

    def test_hierarchy_selection_keeps_camera_for_visible_object(
        self,
    ) -> None:
        record = self.editor.state.add_object(
            "rectangle",
            x=400,
            y=300,
        )
        self.editor.on_draw()
        self.editor.on_draw()
        before = list(self.editor.preview.editor_camera)

        self.editor._select_from_hierarchy(
            str(record["id"]),
            [str(record["id"])],
        )

        self.assertEqual(self.editor.preview.editor_camera, before)

    def test_imgui_clipboard_uses_pyglet_window(self) -> None:
        platform = imgui.get_platform_io()
        get_clipboard = platform.platform_get_clipboard_text_fn
        set_clipboard = platform.platform_set_clipboard_text_fn
        context = imgui.get_current_context()
        original = self.editor.window.get_clipboard_text()
        try:
            set_clipboard(context, "assets/terrain.png")
            self.assertEqual(
                get_clipboard(context),
                "assets/terrain.png",
            )
        finally:
            self.editor.window.set_clipboard_text(original)

    def test_atlas_selection_uses_margin_spacing_and_bounds(self) -> None:
        select = self.editor._atlas_tile_at

        self.assertEqual(select(2, 2, 16, 16, 3, 6, 2, 1), 0)
        self.assertEqual(select(20, 2, 16, 16, 3, 6, 2, 1), 1)
        self.assertEqual(select(2, 20, 16, 16, 3, 6, 2, 1), 3)
        self.assertIsNone(select(1, 2, 16, 16, 3, 6, 2, 1))
        self.assertIsNone(select(18, 2, 16, 16, 3, 6, 2, 1))
        self.assertIsNone(select(40, 40, 16, 16, 3, 6, 2, 1))

    def test_native_atlas_dialog_updates_selected_tile_layer(self) -> None:
        class CompletedDialog:
            def __init__(self, result: list[str]) -> None:
                self._result = result

            def ready(self) -> bool:
                return True

            def result(self) -> list[str]:
                return self._result

        layer = self.editor.state.add_tile_layer("Ground")
        atlas = self.editor.state.asset_root / "tiles.png"
        self.editor._native_dialog = CompletedDialog([str(atlas)])
        self.editor._path_dialog = "atlas"

        self.editor._poll_native_dialog()

        layer_id = str(layer["id"])
        self.assertEqual(
            self.editor._json_buffers[f"tileset-path:{layer_id}"],
            "tiles.png",
        )

    def test_color_property_recognizes_rgb_and_rgba(self) -> None:
        detect = self.editor._is_color_property

        self.assertTrue(detect("color##renderer", [255, 0, 128, 255]))
        self.assertTrue(detect("Tint Color", [255, 255, 255]))
        self.assertFalse(detect("position", [10, 20, 30]))
        self.assertFalse(detect("color", [300, 0, 0]))

    def test_empty_left_drag_marquee_selects_without_panning(self) -> None:
        first = self.editor.state.add_object(
            "rectangle",
            x=350,
            y=300,
        )
        self.editor.state.select(None)
        second = self.editor.state.add_object(
            "rectangle",
            x=450,
            y=300,
        )
        self.editor.on_draw()
        self.editor.on_draw()
        minimum_x, _, _, maximum_y = (
            self.editor._viewport_rect
        )
        game = self.editor.preview.game
        assert game is not None
        identifiers = {str(first["id"]), str(second["id"])}
        bounds = [
            self.editor.preview.object_screen_bounds(item)
            for item in game.scenes.current.walk()
            if isinstance(item, GameObject)
            and item.scene_id in identifiers
        ]
        start_x = min(bound[0] for bound in bounds) - 5
        start_y = min(bound[1] for bound in bounds) - 5
        end_x = max(bound[2] for bound in bounds) + 5
        end_y = max(bound[3] for bound in bounds) + 5
        before = tuple(self.editor.preview.editor_camera)
        io = imgui.get_io()
        self.assertIsNotNone(self.editor.state.selected)

        io.add_mouse_pos_event(
            minimum_x + start_x,
            maximum_y - start_y,
        )
        io.add_mouse_button_event(0, True)
        self.editor.on_draw()
        io.add_mouse_pos_event(
            minimum_x + end_x,
            maximum_y - end_y,
        )
        self.editor.on_draw()
        io.add_mouse_button_event(0, False)
        self.editor.on_draw()

        self.assertEqual(
            set(self.editor.state.selected_ids),
            identifiers,
        )
        self.assertEqual(
            self.editor.preview.editor_camera,
            list(before),
        )

    def test_space_left_drag_pans_viewport(self) -> None:
        self.editor.on_draw()
        self.editor.on_draw()
        minimum_x, minimum_y, maximum_x, maximum_y = (
            self.editor._viewport_rect
        )
        center_x = (minimum_x + maximum_x) / 2
        center_y = (minimum_y + maximum_y) / 2
        before = tuple(self.editor.preview.editor_camera)
        io = imgui.get_io()

        self.editor.on_key_press(key.SPACE, 0)
        io.add_mouse_pos_event(center_x, center_y)
        io.add_mouse_button_event(0, True)
        self.editor.on_draw()
        io.add_mouse_pos_event(center_x + 20, center_y - 10)
        self.editor.on_draw()
        io.add_mouse_button_event(0, False)
        self.editor.on_draw()
        self.editor.on_key_release(key.SPACE, 0)

        self.assertEqual(
            self.editor.preview.editor_camera,
            [before[0] - 20, before[1] - 10],
        )

    def test_right_drag_pans_without_opening_context_menu(self) -> None:
        self.editor.on_draw()
        self.editor.on_draw()
        minimum_x, minimum_y, maximum_x, maximum_y = (
            self.editor._viewport_rect
        )
        center_x = (minimum_x + maximum_x) / 2
        center_y = (minimum_y + maximum_y) / 2
        before = tuple(self.editor.preview.editor_camera)
        io = imgui.get_io()

        with patch.object(
            self.editor,
            "_open_viewport_context_menu",
        ) as open_context:
            io.add_mouse_pos_event(center_x, center_y)
            io.add_mouse_button_event(1, True)
            self.editor.on_draw()
            io.add_mouse_pos_event(center_x + 24, center_y - 12)
            self.editor.on_draw()
            io.add_mouse_button_event(1, False)
            self.editor.on_draw()

        self.assertEqual(
            self.editor.preview.editor_camera,
            [before[0] - 24, before[1] - 12],
        )
        open_context.assert_not_called()

    def test_right_click_still_opens_viewport_context_menu(self) -> None:
        self.editor.on_draw()
        self.editor.on_draw()
        minimum_x, minimum_y, maximum_x, maximum_y = (
            self.editor._viewport_rect
        )
        center_x = (minimum_x + maximum_x) / 2
        center_y = (minimum_y + maximum_y) / 2
        io = imgui.get_io()

        with patch.object(
            self.editor,
            "_open_viewport_context_menu",
        ) as open_context:
            io.add_mouse_pos_event(center_x, center_y)
            io.add_mouse_button_event(1, True)
            self.editor.on_draw()
            io.add_mouse_button_event(1, False)
            self.editor.on_draw()

        open_context.assert_called_once_with()

    def test_hierarchy_copy_paste_uses_selected_parent(self) -> None:
        parent = self.editor.state.add_object("empty")
        self.editor.state.select(None)
        source = self.editor.state.add_object("rectangle", x=20, y=30)
        self.editor.preview.synchronize()
        self.editor._shortcut_context = "hierarchy"

        self.editor.on_key_press(key.C, key.MOD_CTRL)
        self.editor.state.select(str(parent["id"]))
        self.editor.on_key_press(key.V, key.MOD_CTRL)
        self.editor.on_key_press(key.V, key.MOD_CTRL)

        children = parent["children"]
        assert isinstance(children, list)
        self.assertEqual(len(children), 2)
        clone = children[0]
        assert isinstance(clone, dict)
        self.assertNotEqual(clone["id"], source["id"])
        self.assertEqual(position(clone), (20, 30))
        second_clone = children[1]
        assert isinstance(second_clone, dict)
        self.assertEqual(position(second_clone), (20, 30))

    def test_command_enter_deletes_hierarchy_selection(self) -> None:
        record = self.editor.state.add_object("rectangle")
        self.editor._shortcut_context = "hierarchy"

        self.editor.on_key_press(key.ENTER, key.MOD_COMMAND)

        self.assertNotIn(record, self.editor.state.document.objects)

    def test_command_enter_deletes_selected_ui_view(self) -> None:
        view = self.editor.state.add_ui_view("Temporary")
        self.editor._shortcut_context = "layers"

        self.editor.on_key_press(key.ENTER, key.MOD_COMMAND)

        self.assertNotIn(view, self.editor.state.document.ui_views)

    def test_delete_key_deletes_selected_ui_view(self) -> None:
        view = self.editor.state.add_ui_view("Temporary")
        self.editor._shortcut_context = "layers"

        self.editor.on_key_press(key.DELETE, 0)

        self.assertNotIn(view, self.editor.state.document.ui_views)

    def test_hierarchy_copy_paste_without_new_parent_creates_sibling(
        self,
    ) -> None:
        parent = self.editor.state.add_object("empty")
        source = self.editor.state.add_object("rectangle", x=20, y=30)
        self.editor.preview.synchronize()
        self.editor._shortcut_context = "hierarchy"

        self.editor.on_key_press(key.C, key.MOD_CTRL)
        self.editor.on_key_press(key.V, key.MOD_CTRL)
        self.editor.on_key_press(key.V, key.MOD_CTRL)

        children = parent["children"]
        assert isinstance(children, list)
        self.assertEqual(len(children), 3)
        self.assertIs(children[0], source)
        self.assertEqual(source.get("children", []), [])
        self.assertTrue(
            all(
                isinstance(child, dict)
                and child.get("id") != source["id"]
                for child in children[1:]
            )
        )

    def test_viewport_copy_paste_offsets_then_uses_clicked_position(
        self,
    ) -> None:
        source = self.editor.state.add_object(
            "rectangle",
            x=100,
            y=120,
        )
        self.editor.preview.synchronize()
        self.editor._shortcut_context = "viewport"

        self.editor.on_key_press(key.C, key.MOD_CTRL)
        self.editor.on_key_press(key.V, key.MOD_CTRL)

        first_clone = self.editor.state.selected
        self.assertIsNotNone(first_clone)
        assert first_clone is not None
        self.assertNotEqual(first_clone["id"], source["id"])
        self.assertEqual(position(first_clone), (116, 136))

        click_x, click_y = self.editor.preview.world_to_screen(300, 250)
        self.editor._viewport_click_serial += 1
        self.editor._viewport_paste_position = (click_x, click_y)
        self.editor.on_key_press(key.V, key.MOD_CTRL)

        clicked_clone = self.editor.state.selected
        self.assertIsNotNone(clicked_clone)
        assert clicked_clone is not None
        self.assertEqual(position(clicked_clone), (300, 250))

    def test_hierarchy_to_viewport_paste_preserves_transformed_parent(self) -> None:
        editor = self.editor
        parent = editor.state.add_object("empty", x=100, y=80)
        editor.state.set_object_value("rotation", 35)
        editor.state.set_object_value("scale_x", 2)
        editor.state.set_object_value("scale_y", 3)
        source = editor.state.add_object("rectangle", x=20, y=30)
        editor.preview.synchronize()
        origin = editor.state.object_world_transform(source)
        editor._shortcut_context = "hierarchy"
        editor.on_key_press(key.C, key.MOD_CTRL)
        editor._shortcut_context = "viewport"
        history = editor.state._history_cursor
        before = deepcopy(editor.state.document.to_dict())
        editor.on_key_press(key.V, key.MOD_CTRL)
        clone = editor.state.selected
        assert clone is not None
        self.assertIs(editor.state.object_parent(clone), parent)
        world = editor.state.object_world_transform(clone)
        self.assertAlmostEqual(world[0], origin[0] + 16)
        self.assertAlmostEqual(world[1], origin[1] + 16)
        self.assertEqual(world[2:], origin[2:])
        editor._viewport_click_serial += 1
        editor._viewport_paste_position = editor.preview.world_to_screen(300, 250)
        editor.on_key_press(key.V, key.MOD_CTRL)
        clone = editor.state.selected
        assert clone is not None
        self.assertIs(editor.state.object_parent(clone), parent)
        world = editor.state.object_world_transform(clone)
        self.assertAlmostEqual(world[0], 300)
        self.assertAlmostEqual(world[1], 250)
        self.assertEqual(world[2:], origin[2:])
        self.assertEqual(editor.state._history_cursor, history + 2)
        editor.state.undo()
        editor.state.undo()
        self.assertEqual(editor.state.document.to_dict(), before)
        editor.state.redo()
        editor.state.redo()
        self.assertEqual(len(editor.state.records[0]["children"]), 3)

    def test_viewport_paste_preserves_each_source_parent_in_multi_selection(self) -> None:
        editor = self.editor
        first_parent = editor.state.add_object("empty", x=100, y=100)
        first = editor.state.add_object("rectangle", x=10, y=20)
        editor.state.select(None)
        second_parent = editor.state.add_object("empty", x=300, y=300)
        second = editor.state.add_object("circle", x=30, y=40)
        editor.state.select(None)
        root = editor.state.add_object("rectangle", x=500, y=500)
        sources = (first, second, root)
        editor.state.select_many([str(record["id"]) for record in sources])
        origins = [editor.state.object_world_transform(record) for record in sources]
        editor.preview.synchronize()
        editor._shortcut_context = "hierarchy"
        editor.on_key_press(key.C, key.MOD_CTRL)
        editor._shortcut_context = "viewport"
        editor.on_key_press(key.V, key.MOD_CTRL)
        clones = editor.state.selected_records
        self.assertEqual(len(clones), 3)
        for clone, parent, origin in zip(
            clones, (first_parent, second_parent, None), origins, strict=True,
        ):
            self.assertIs(editor.state.object_parent(clone), parent)
            world = editor.state.object_world_transform(clone)
            self.assertAlmostEqual(world[0], origin[0] + 16)
            self.assertAlmostEqual(world[1], origin[1] + 16)
        editor.preview.synchronize()
        runtime = editor.preview.selected_runtime_objects()
        self.assertEqual(len(runtime), 3)
        for clone, parent in zip(runtime, (first_parent, second_parent, None), strict=True):
            self.assertEqual(
                clone.parent.scene_id if isinstance(clone.parent, GameObject) else None,
                str(parent["id"]) if parent is not None else None,
            )

    def test_cut_child_then_viewport_paste_keeps_parent(self) -> None:
        editor = self.editor
        parent = editor.state.add_object("empty", x=100, y=200)
        child = editor.state.add_object("rectangle", x=10, y=20)
        editor._shortcut_context = "hierarchy"
        editor.on_key_press(key.X, key.MOD_CTRL)
        self.assertEqual(parent["children"], [])
        editor._shortcut_context = "viewport"
        editor.on_key_press(key.V, key.MOD_CTRL)
        clone = editor.state.selected
        assert clone is not None
        self.assertIs(editor.state.object_parent(clone), parent)
        self.assertNotEqual(clone["id"], child["id"])
        self.assertEqual(position(clone), (26, 36))

    def test_parented_physics_object_can_be_pasted_and_played(self) -> None:
        editor = self.editor
        parent = editor.state.add_object("empty", x=100, y=80)
        editor.state.set_object_value("rotation", 30)
        editor.state.set_object_value("scale_x", 2)
        editor.state.set_object_value("scale_y", 3)
        editor.state.add_object("rectangle", x=40, y=50)
        editor.state.add_component("g.physics_body")
        editor.preview.synchronize()
        editor._shortcut_context = "hierarchy"
        editor.on_key_press(key.C, key.MOD_CTRL)
        editor._shortcut_context = "viewport"
        editor.on_key_press(key.V, key.MOD_CTRL)
        record = editor.state.selected
        assert record is not None
        editor.preview.set_mode("play")
        runtime = editor.preview.selected_runtime_object()
        assert runtime is not None
        self.assertIsInstance(runtime.parent, GameObject)
        assert isinstance(runtime.parent, GameObject)
        self.assertEqual(runtime.parent.scene_id, parent["id"])
        assert editor.preview.game is not None
        editor.preview.game.physics.step(1 / 60)
        from g import PhysicsBody
        body = runtime.get_component(PhysicsBody)
        assert body is not None
        self.assertAlmostEqual(runtime.world_position.x, body._body.position.x)
        self.assertAlmostEqual(runtime.world_position.y, body._body.position.y)
        editor.preview.set_mode("edit")
        self.assertEqual(editor.state.selected, record)

    def test_viewport_paste_missing_parent_reports_error_without_partial_changes(self) -> None:
        editor = self.editor
        parent = editor.state.add_object("empty")
        editor.state.add_object("rectangle")
        editor._shortcut_context = "hierarchy"
        editor.on_key_press(key.C, key.MOD_CTRL)
        editor.state.select(str(parent["id"]))
        editor.state.delete_selected()
        before = deepcopy(editor.state.document.to_dict())
        editor._shortcut_context = "viewport"
        editor.on_key_press(key.V, key.MOD_CTRL)
        self.assertIn("paste parent is unavailable", editor.state.error or "")
        self.assertEqual(editor.state.document.to_dict(), before)

    def test_cut_and_paste_restores_object_at_hierarchy_root(self) -> None:
        source = self.editor.state.add_object("circle", x=40, y=50)
        self.editor.preview.synchronize()
        self.editor._shortcut_context = "hierarchy"

        self.editor.on_key_press(key.X, key.MOD_CTRL)

        self.assertEqual(self.editor.state.document.objects, [])
        self.assertEqual(self.editor.state.selected_ids, [])

        self.editor.on_key_press(key.V, key.MOD_CTRL)

        self.assertEqual(len(self.editor.state.document.objects), 1)
        pasted = self.editor.state.document.objects[0]
        self.assertNotEqual(pasted["id"], source["id"])
        self.assertEqual(position(pasted), (40, 50))

    def test_gui_hierarchy_shortcuts_paste_into_container(self) -> None:
        layer = self.editor.state.add_ui_view("Test HUD")
        panel = self.editor.state.add_gui_element("panel")
        self.editor.state.select(None)
        source = self.editor.state.add_gui_element("button")
        self.editor.preview.synchronize()
        self.editor._shortcut_context = "hierarchy"

        self.editor.on_key_press(key.C, key.MOD_CTRL)
        self.editor.state.select(str(panel["id"]))
        self.editor.on_key_press(key.V, key.MOD_CTRL)
        self.editor.on_key_press(key.V, key.MOD_CTRL)

        children = panel["children"]
        assert isinstance(children, list)
        self.assertEqual(len(children), 2)
        clone = children[0]
        assert isinstance(clone, dict)
        self.assertNotEqual(clone["id"], source["id"])
        self.assertEqual(layer["elements"], [panel, source])

    def test_gui_copy_paste_without_new_parent_creates_sibling(
        self,
    ) -> None:
        self.editor.state.add_ui_view("Test HUD")
        panel = self.editor.state.add_gui_element("panel")
        source = self.editor.state.add_gui_element("button")
        self.editor.preview.synchronize()
        self.editor._shortcut_context = "hierarchy"

        self.editor.on_key_press(key.C, key.MOD_CTRL)
        self.editor.on_key_press(key.V, key.MOD_CTRL)
        self.editor.on_key_press(key.V, key.MOD_CTRL)

        children = panel["children"]
        assert isinstance(children, list)
        self.assertEqual(len(children), 3)
        self.assertIs(children[0], source)
        self.assertEqual(source.get("children", []), [])
        self.assertTrue(
            all(
                isinstance(child, dict)
                and child.get("id") != source["id"]
                for child in children[1:]
            )
        )

    def test_hierarchy_drop_reorders_object_siblings(self) -> None:
        first = self.editor.state.add_object("rectangle")
        self.editor.state.select(None)
        second = self.editor.state.add_object("circle")
        self.editor.state.select(str(second["id"]))
        self.editor._hierarchy_drag_identifier = str(second["id"])

        self.editor._perform_hierarchy_drop(
            str(first["id"]),
            "before",
            "object",
        )

        self.assertEqual(
            self.editor.state.document.objects,
            [second, first],
        )
        second_z = second["z_index"]
        first_z = first["z_index"]
        assert isinstance(second_z, int)
        assert isinstance(first_z, int)
        self.assertGreater(second_z, first_z)

    def test_gui_root_drop_preserves_arranged_screen_position(self) -> None:
        layer = self.editor.state.add_ui_view("Menu")
        layout = self.editor.state.add_gui_element("vbox")
        child = self.editor.state.add_gui_element("button")
        self.editor.on_draw()
        self.editor.on_draw()
        bounds = self.editor.preview.gui_element_bounds(
            str(child["id"])
        )
        self.assertIsNotNone(bounds)
        assert bounds is not None
        self.editor.state.select(str(child["id"]))
        self.editor._hierarchy_drag_identifier = str(child["id"])

        self.editor._perform_hierarchy_drop(None, "root", "gui")

        self.assertEqual(layout["children"], [])
        self.assertEqual(layer["elements"], [layout, child])
        self.assertEqual(
            (child["x"], child["y"]),
            (bounds[0], bounds[1]),
        )

    def test_zoomed_out_grid_uses_an_adaptive_visible_step(self) -> None:
        visible_step = self.editor._visible_grid_step

        self.assertEqual(visible_step(32, 1), 32)
        self.assertEqual(visible_step(32, 0.5), 32)
        self.assertEqual(visible_step(32, 0.2), 64)
        self.assertEqual(visible_step(32, 0.1), 128)
        self.assertGreaterEqual(visible_step(32, 0.01) * 0.01, 12)

    def test_viewport_grid_uses_subdued_transparency_without_changing_line_width(self) -> None:
        self.editor.preview.synchronize()
        minimum, maximum = imgui.ImVec2(10.25, 20.25), imgui.ImVec2(650.25, 380.25)
        for zoom in (1, 0.2):
            with self.subTest(zoom=zoom):
                game = self.editor.preview.game
                assert game is not None
                game.camera.zoom = zoom
                game.camera.position = (0, 0)
                draw_list = Mock()
                with (
                    patch.object(imgui, "get_window_draw_list", return_value=draw_list),
                    patch.object(self.editor, "_draw_imguizmo"),
                    patch.object(self.editor, "_draw_tile_placement_preview"),
                    patch.object(self.editor, "_draw_tile_selection"),
                ):
                    self.editor._draw_viewport_overlay(minimum, maximum)
                self.assertGreater(draw_list.add_line.call_count, 0)
                draw_list.add_rect_filled.assert_not_called()
                alphas = set()
                for drawing in draw_list.add_line.call_args_list:
                    self.assertEqual(len(drawing.args), 3)
                    self.assertEqual(drawing.kwargs, {})
                    alphas.add((drawing.args[2] >> 24) & 255)
                self.assertEqual(alphas, {85, 140})

    def test_editor_camera_can_zoom_out_to_one_percent(self) -> None:
        preview = self.editor.preview
        anchor = (240.0, 180.0)
        before = preview.screen_to_world(*anchor)

        preview.zoom_at(0.001, *anchor)

        self.assertEqual(preview.editor_zoom, 0.01)
        after = preview.screen_to_world(*anchor)
        self.assertAlmostEqual(after[0], before[0])
        self.assertAlmostEqual(after[1], before[1])

    def test_starting_camera_settings_update_runtime_preview(self) -> None:
        self.editor.state.set_scene_camera_value(
            "position",
            [125, 275],
        )
        self.editor.state.set_scene_camera_value("zoom", 1.5)
        self.editor.state.set_scene_camera_value("rotation", -20)

        self.editor.preview.synchronize()

        game = self.editor.preview.game
        assert game is not None
        scene = game.scenes.current
        assert scene is not None
        self.assertEqual((scene.camera.x, scene.camera.y), (125, 275))
        self.assertEqual(scene.camera.zoom, 1.5)
        self.assertEqual(scene.camera.rotation, -20)

    def test_sprite_animator_inspector_and_play_preview(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            root = Path(directory)
            first = root / "idle.png"
            second = root / "walk.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(12, 12).save(str(first))
            pyglet.image.SolidColorImagePattern(
                (255, 0, 0, 255)
            ).create_image(12, 12).save(str(second))
            record = self.editor.state.add_sprite(first)
            self.editor.state.add_component("g.sprite_animator")
            components = record["components"]
            assert isinstance(components, list)
            component_index = len(components) - 1
            relative_first = first.relative_to(
                self.editor.state.asset_root
            ).as_posix()
            relative_second = second.relative_to(
                self.editor.state.asset_root
            ).as_posix()
            self.editor.state.set_sprite_animator_configuration(
                component_index,
                [
                    {
                        "name": "walk",
                        "loop": True,
                        "frames": [
                            {
                                "image": relative_first,
                                "duration": 0.05,
                            },
                            {
                                "image": relative_second,
                                "duration": 0.05,
                            },
                        ],
                    }
                ],
                initial_state="walk",
            )

            self.editor.on_draw()
            self.assertIsNone(self.editor.state.error)
            self.editor.preview.update(0.06)
            edit_game = self.editor.preview.game
            assert edit_game is not None
            self.assertEqual(edit_game.objects[0].require_component(SpriteAnimator).frame_index, 0)
            self.editor.preview.set_mode("play")
            self.editor.preview.update(0.06)

            game = self.editor.preview.game
            assert game is not None
            scene = game.scenes.current
            assert scene is not None
            animator = scene.objects[0].require_component(
                SpriteAnimator
            )
            self.assertEqual(animator.state, "walk")
            self.assertEqual(animator.frame_index, 1)

    def test_sprite_animator_static_asset_assignment_clear_and_rename(self) -> None:
        with TemporaryDirectory(dir=self.editor.state.asset_root) as directory:
            root = Path(directory)
            image = root / "idle.png"
            pyglet.image.SolidColorImagePattern((255, 255, 255, 255)).create_image(12, 12).save(str(image))
            record = self.editor.state.add_object("empty")
            self.editor.state.add_component("g.sprite_animator")
            target = _AssetPickerTarget("sprite-animation-state:1:0:-1", "image", "image")
            self.editor._assign_asset(target, image)
            components = record["components"]
            assert isinstance(components, list)
            component = components[1]
            assert isinstance(component, dict)
            properties = component["properties"]
            assert isinstance(properties, dict)
            states = properties["states"]
            assert isinstance(states, list)
            state = states[0]
            assert isinstance(state, dict)
            self.assertEqual(state["image"], image.relative_to(self.editor.state.asset_root).as_posix())
            self.editor.on_draw()
            self.assertIsNone(self.editor.state.error)
            renamed = image.with_name("renamed.png")
            image.rename(renamed)
            self.assertEqual(self.editor.state.rename_asset_references(
                image.relative_to(self.editor.state.asset_root).as_posix(),
                renamed.relative_to(self.editor.state.asset_root).as_posix(),
            ), 1)
            self.assertEqual(state["image"], renamed.relative_to(self.editor.state.asset_root).as_posix())
            self.editor._clear_asset(target)
            self.assertIsNone(state["image"])
            self.assertTrue(self.editor.state.undo())
            self.editor.preview.reload()
            self.assertIsNone(self.editor.preview.load_error)

    def test_sprite_animator_panel_source_selector(self) -> None:
        record = self.editor.state.add_object("empty")
        self.editor.state.add_component("g.sprite_animator")
        components = record["components"]
        assert isinstance(components, list)
        component = components[1]
        assert isinstance(component, dict)
        properties = component["properties"]
        assert isinstance(properties, dict)
        original_choice = self.editor._choice_property
        original_header = imgui.collapsing_header

        for selected_mode in (2, 1, 0):
            def choice(
                label,
                selected,
                choices,
                buffer_key=None,
                *,
                mode=selected_mode,
                **kwargs,
            ):
                if label == "Source":
                    return True, mode
                return original_choice(
                    label,
                    selected,
                    choices,
                    buffer_key,
                    **kwargs,
                )

            with (
                patch.object(self.editor, "_choice_property", side_effect=choice),
                patch("g_editor.panels.animation.imgui.collapsing_header", side_effect=lambda label, *args: True if label == "State Settings##animation" else original_header(label, *args)),
            ):
                self.editor.on_draw()
            states = properties["states"]
            assert isinstance(states, list)
            state = states[0]
            assert isinstance(state, dict)
            if selected_mode == 2:
                self.assertEqual(state["frames"], [{"image": None, "duration": 0.1}])
                self.assertNotIn("image", state)
            else:
                self.assertIsNone(state["image"])
                self.assertEqual(state["animated"], selected_mode == 1)
                self.assertNotIn("frames", state)
            self.assertIsNone(self.editor.state.error)
            self.editor.preview.reload()
            self.assertIsNone(self.editor.preview.load_error)

    def test_sprite_source_switch_discards_old_asset_click_targets(self) -> None:
        with TemporaryDirectory(dir=self.editor.state.asset_root) as directory:
            image = Path(directory) / "frame.png"
            pyglet.image.SolidColorImagePattern((255, 255, 255, 255)).create_image(8, 8).save(str(image))
            self.editor.state.add_object("empty")
            self.editor.state.add_component("g.sprite_animator")
            self.editor.on_draw()
            old_id = "sprite-animation-state:1:0:-1"
            self.assertIn(old_id, self.editor._asset_field_targets)
            self.editor._toggle_asset_picker(self.editor._asset_field_targets[old_id])
            self.editor.state.set_sprite_animator_configuration(
                1, [{"name": "default", "loop": True, "frames": [{"image": None, "duration": 0.1}]}],
            )
            self.editor.on_draw()
            self.assertNotIn(old_id, self.editor._asset_field_targets)
            self.assertNotIn(old_id, self.editor._asset_field_button_rects)
            self.assertIsNone(self.editor._asset_picker)
            frame_id = "sprite-animation-frame:1:0:0"
            target = self.editor._asset_field_targets[frame_id]
            self.editor._toggle_asset_picker(target)
            self.editor._assign_asset_picker(image)
            self.assertIsNone(self.editor._asset_picker)
            self.assertIsNone(self.editor.state.error)
            self.editor.preview.reload()
            self.assertIsNone(self.editor.preview.load_error)
            runtime = self.editor.preview.selected_runtime_object()
            assert runtime is not None
            frame = runtime.require_component(SpriteAnimator).states["default"].frames[0]
            self.assertIsNotNone(frame.image)

    def test_runtime_transform_updates_position_rotation_and_scale(self) -> None:
        record = self.editor.state.add_object("rectangle", x=100, y=100)
        self.editor.preview.synchronize()
        self.editor.state.set_object_value("x", 160.0)
        self.editor.state.set_object_value("y", 192.0)
        self.editor.state.set_object_value("rotation", 35.0)
        self.editor.state.set_object_value("scale_x", 1.5)
        self.editor.state.set_object_value("scale_y", 0.75)

        self.editor.preview.apply_selected_transform()

        runtime_object = next(
            item
            for item in self.editor.preview.game.objects  # type: ignore[union-attr]
            if item.scene_id == record["id"]
        )
        self.assertEqual(tuple(runtime_object.position), (176, 208))
        self.assertEqual(runtime_object.rotation, 35)
        self.assertEqual(runtime_object.scale_x, 1.5)
        self.assertEqual(runtime_object.scale_y, 0.75)

    def test_collider_offsets_from_inspector_rebuild_preview_geometry(self) -> None:
        record = self.editor.state.add_object("rectangle", x=100, y=100)
        components = record["components"]
        assert isinstance(components, list)
        index = next(
            index for index, component in enumerate(components)
            if isinstance(component, dict) and component.get("type") == "g.collider"
        )
        self.editor.state.set_component_value(index, "offset_x", 80.0)
        self.editor.state.set_component_value(index, "offset_y", -10.0)
        self.editor.preview.synchronize()
        obj = self.editor.preview.selected_runtime_object()
        assert obj is not None and obj.collider is not None
        self.assertTrue(obj.collider.contains_point(180, 90))
        self.assertFalse(obj.collider.contains_point(100, 100))

    def test_nested_object_uses_parent_world_transform_in_preview(
        self,
    ) -> None:
        self.editor.state.add_object("empty", x=100, y=120)
        child = self.editor.state.add_object("rectangle", x=20, y=30)
        self.editor.preview.synchronize()

        world_transform = self.editor.preview.selected_world_transform()

        self.assertIsNotNone(world_transform)
        assert world_transform is not None
        self.assertEqual(world_transform[:2], (120, 150))
        self.assertIs(self.editor.state.selected, child)

    def test_selected_objects_move_together_in_world_space(self) -> None:
        first = self.editor.state.add_object(
            "rectangle",
            x=100,
            y=120,
        )
        self.editor.state.select(None)
        second = self.editor.state.add_object(
            "rectangle",
            x=200,
            y=220,
        )
        self.editor.state.select(str(first["id"]))
        self.editor.state.select(
            str(second["id"]),
            additive=True,
        )
        self.editor.preview.synchronize()

        self.editor.preview.move_selected_objects_by_world(15, -10)

        self.assertEqual(position(first), (115, 110))
        self.assertEqual(position(second), (215, 210))

    def test_object_gizmo_grid_snap_uses_world_units_at_every_zoom(self) -> None:
        self.editor.state.document.settings["grid_cell_size"] = 128
        self.editor.state.set_snap_enabled(True)
        parent = self.editor.state.add_object("empty", x=100, y=120)
        self.editor.state.set_object_value("scale_x", 2.0)
        self.editor.state.set_object_value("scale_y", 3.0)
        child = self.editor.state.add_object("rectangle", x=20, y=30)
        self.editor.preview.reload()
        game = self.editor.preview.game
        assert game is not None
        self.editor._transform_tool = "move"

        def move_one_snap(*args: object, snap: object) -> bool:
            matrix = args[4]
            assert isinstance(matrix, imguizmo.im_guizmo.Matrix16)
            assert isinstance(snap, imguizmo.im_guizmo.Matrix3)
            matrix.values[12] += snap.values[0]
            matrix.values[13] -= snap.values[1]
            return True

        for zoom in (0.25, 0.5, 1.0, 2.0):
            with self.subTest(zoom=zoom):
                game.scenes.current.camera.zoom = zoom
                game.scenes.current.camera.position = (333, -77)
                before = self.editor.preview.selected_world_transform()
                assert before is not None
                with (
                    patch.object(imguizmo.im_guizmo, "set_orthographic"),
                    patch.object(imguizmo.im_guizmo, "set_drawlist"),
                    patch.object(imguizmo.im_guizmo, "set_rect"),
                    patch.object(
                        imguizmo.im_guizmo, "manipulate",
                        side_effect=move_one_snap,
                    ) as manipulate,
                ):
                    self.editor._draw_imguizmo(
                        imgui.ImVec2(10, 20), imgui.ImVec2(810, 620)
                    )
                after = self.editor.preview.selected_world_transform()
                assert after is not None
                self.assertEqual(
                    after[:2],
                    self.editor.state.grid_cell_center(
                        after[0],
                        after[1],
                    ),
                )
                self.assertEqual(
                    manipulate.call_args.kwargs["snap"].values.tolist(),
                    [128 * zoom] * 3,
                )
                self.assertIs(self.editor.state.selected, child)
                self.assertEqual(position(parent), (100, 120))

    def test_gizmo_snaps_primary_to_cell_center_and_preserves_group_spacing(
        self,
    ) -> None:
        self.editor.state.document.settings["grid_cell_size"] = 20
        self.editor.state.set_snap_enabled(True)
        first = self.editor.state.add_object("rectangle", x=100, y=120)
        self.editor.state.select(None)
        second = self.editor.state.add_object("rectangle", x=200, y=220)
        self.editor.state.select(str(first["id"]))
        self.editor.state.select(str(second["id"]), additive=True)
        self.editor.preview.synchronize()
        self.editor._transform_tool = "move"

        with (
            patch.object(imguizmo.im_guizmo, "set_orthographic"),
            patch.object(imguizmo.im_guizmo, "set_drawlist"),
            patch.object(imguizmo.im_guizmo, "set_rect"),
            patch.object(
                imguizmo.im_guizmo,
                "manipulate",
                return_value=True,
            ),
            patch.object(
                self.editor.preview,
                "screen_to_world",
                return_value=(33, 49),
            ),
        ):
            self.editor._draw_imguizmo(
                imgui.ImVec2(10, 20),
                imgui.ImVec2(810, 620),
            )

        self.assertEqual(position(second), (30, 50))
        self.assertEqual(position(first), (-70, -50))
        self.assertEqual(
            (
                position(second)[0] - position(first)[0],
                position(second)[1] - position(first)[1],
            ),
            (100, 100),
        )

    def test_transform_space_is_forced_for_scale_and_multi_selection(
        self,
    ) -> None:
        first = self.editor.state.add_object("rectangle")
        self.editor._transform_space = "local"
        self.editor._transform_tool = "move"

        self.assertEqual(
            self.editor._effective_transform_space(),
            "local",
        )

        self.editor.state.select(None)
        second = self.editor.state.add_object("circle")
        self.editor.state.select(str(first["id"]))
        self.editor.state.select(str(second["id"]), additive=True)

        self.assertEqual(
            self.editor._effective_transform_space(),
            "global",
        )

        self.editor.state.select(str(first["id"]))
        self.editor._transform_tool = "scale"

        self.assertEqual(
            self.editor._effective_transform_space(),
            "local",
        )

    def test_play_mode_rebuilds_the_authored_scene(self) -> None:
        self.editor.state.add_object("circle", x=100, y=100)
        self.editor.preview.synchronize()

        self.editor.preview.set_mode("play")
        self.assertEqual(self.editor.preview.mode, "play")
        self.assertTrue(self.editor.preview.game._started)  # type: ignore[union-attr]
        self.editor.preview.set_mode("edit")
        self.assertEqual(self.editor.preview.mode, "edit")
        self.assertFalse(self.editor.preview.game._started)  # type: ignore[union-attr]
        with self.assertRaisesRegex(ValueError, "unknown runtime mode"):
            self.editor.preview.set_mode("simulate")

    def test_grid_overlay_is_not_drawn_in_play_and_returns_in_edit(self) -> None:
        self.editor.on_draw()
        self.editor.on_draw()
        with patch.object(
            self.editor, "_draw_viewport_overlay",
            wraps=self.editor._draw_viewport_overlay,
        ) as overlay:
            self.editor.on_draw()
            self.assertGreater(overlay.call_count, 0)
            overlay.reset_mock()
            self.editor.preview.set_mode("play")
            self.assertEqual(self.editor.preview.mode, "play")
            self.editor.on_draw()
            self.editor.on_draw()
            overlay.assert_not_called()
            self.editor.preview.set_mode("edit")
            self.editor.on_draw()
            self.assertGreater(overlay.call_count, 0)

    def test_platform_controller_falls_in_editor_play_mode(self) -> None:
        self.editor.state.add_object("rectangle", x=100, y=200)
        self.editor.state.add_component("g.platformer_motor")
        self.editor.preview.set_mode("play")
        runtime_object = self.editor.preview.selected_runtime_object()
        assert runtime_object is not None
        initial_y = runtime_object.y

        for _ in range(10):
            self.editor.preview.update(1 / 60)

        self.assertLess(runtime_object.y, initial_y)
        game = self.editor.preview.game
        assert game is not None
        self.assertEqual(game.physics.gravity, (0.0, -980.0))

    def test_play_viewport_uses_authored_camera_pixel_bounds(self) -> None:
        self.editor.preview.set_mode("play")
        self.editor.on_draw()
        self.editor.on_draw()

        self.assertEqual(
            (
                self.editor.render_target.width,
                self.editor.render_target.height,
            ),
            (1280, 720),
        )
        minimum_x, minimum_y, maximum_x, maximum_y = (
            self.editor._viewport_rect
        )
        self.assertAlmostEqual(
            (maximum_x - minimum_x)
            / (maximum_y - minimum_y),
            1280 / 720,
            places=6,
        )
        lower_left = self.editor._window_to_preview(
            minimum_x,
            self.editor.window.height - maximum_y,
        )
        upper_right = self.editor._window_to_preview(
            maximum_x,
            self.editor.window.height - minimum_y,
        )
        assert lower_left is not None
        assert upper_right is not None
        self.assertAlmostEqual(lower_left[0], 0)
        self.assertAlmostEqual(lower_left[1], 0)
        self.assertAlmostEqual(upper_right[0], 1280)
        self.assertAlmostEqual(upper_right[1], 720)

    def test_scene_resolution_updates_preview_game_size(self) -> None:
        self.editor.state.set_scene_size(320, 180)

        self.editor.preview.synchronize()

        game = self.editor.preview.game
        assert game is not None
        self.assertEqual((game.width, game.height), (320, 180))

    def test_tile_painting_updates_runtime_preview(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            image_path = Path(directory) / "tiles.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(32, 32).save(str(image_path))
            layer = self.editor.state.add_tile_layer("Ground")
            relative_path = image_path.relative_to(
                self.editor.state.asset_root
            ).as_posix()
            self.editor.state.configure_tile_layer(
                relative_path,
                16,
                16,
            )
            tileset = layer["tileset"]
            assert isinstance(tileset, dict)
            self.assertEqual(tileset["tile_count"], 4)
            texture = self.editor._atlas_texture(relative_path)
            self.assertEqual((texture.width, texture.height), (32, 32))
            self.editor.preview.synchronize()

            column, row = self.editor.state.paint_tile(20, 36, 3)
            self.editor.preview.apply_tile(column, row, 3)

            game = self.editor.preview.game
            assert game is not None
            runtime_layer = game.scenes.current.layers[
                str(layer["id"])
            ]
            assert isinstance(runtime_layer, TileLayer)
            cell = runtime_layer.tile_at(1, 2)
            assert cell is not None
            self.assertEqual(cell.tile_id, 3)

    def test_tile_placement_preview_repeats_stamp_over_rectangle(self) -> None:
        preview = self.editor._tile_placement_preview_cells(
            (2, 3),
            (4, 4),
            [TileCell(tile_id) for tile_id in (10, 11, 12, 13)],
            (2, 2),
        )

        self.assertEqual(
            preview,
            [
                (2, 3, TileCell(12)),
                (3, 3, TileCell(13)),
                (4, 3, TileCell(12)),
                (2, 4, TileCell(10)),
                (3, 4, TileCell(11)),
                (4, 4, TileCell(10)),
            ],
        )

    def test_tile_selection_and_move_tools_render_and_update_runtime(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        for column in (0, 1, 3):
            self.editor.state.paint_tile(column * 16 + 1, 1, 0)
        self.editor.preview.reload()
        self.editor.state.tile_tool = "select"
        revision = self.editor.state.revision
        self.editor._begin_tile_edit_drag((0, 0))
        self.editor._update_tile_edit_drag((1, 0), down=True)
        self.assertEqual(
            set(self.editor.state.selected_tile_cells), {(0, 0), (1, 0)}
        )
        self.assertEqual(self.editor.state.revision, revision)
        with patch.object(self.editor, "_handle_viewport_input"):
            self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)
        self.editor._update_tile_edit_drag((1, 0), down=False)
        self.assertIsNone(self.editor._tile_edit_drag)

        self.editor.state.tile_tool = "move"
        self.editor._begin_tile_edit_drag((1, 0))
        self.editor._update_tile_edit_drag((3, 1), down=True)
        self.assertEqual(self.editor.state.tile_at(0, 0), TileCell(0))
        self.assertIsNone(self.editor.state.tile_at(2, 1))
        with patch.object(self.editor, "_handle_viewport_input"):
            self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)
        self.editor._update_tile_edit_drag((3, 1), down=False)
        self.assertIsNone(self.editor._tile_edit_drag)
        self.assertIsNone(self.editor.state.tile_at(0, 0))
        self.assertIsNone(self.editor.state.tile_at(1, 0))
        self.assertEqual(self.editor.state.tile_at(2, 1), TileCell(0))
        self.assertEqual(self.editor.state.tile_at(3, 1), TileCell(0))
        game = self.editor.preview.game
        assert game is not None
        runtime_layer = game.scenes.current.layers[str(layer["id"])]
        assert isinstance(runtime_layer, TileLayer)
        self.assertIsNone(runtime_layer.tile_at(0, 0))
        self.assertEqual(runtime_layer.tile_at(3, 1), TileCell(0))
        self.editor._undo()
        self.assertEqual(self.editor.state.tile_at(0, 0), TileCell(0))
        self.assertIsNone(self.editor.state.tile_at(3, 1))

    def test_command_click_toggles_viewport_tile_selection(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        self.editor.state._set_tile_cell(layer, 0, 0, TileCell(0))
        self.editor.state._set_tile_cell(layer, 2, 0, TileCell(0))
        self.editor.state.select_tile_rectangle((0, 0), (0, 0))
        self.editor.state.tile_tool = "select"
        self.editor._viewport_hovered = True
        io = imgui.get_io()
        previous_super = io.key_super
        io.key_super = True
        try:
            with (
                patch(
                    "g_editor.application.imgui.get_mouse_pos",
                    return_value=imgui.ImVec2(10, 10),
                ),
                patch(
                    "g_editor.application.imgui.is_mouse_clicked",
                    side_effect=lambda button: button == 0,
                ),
                patch(
                    "g_editor.application.imgui.is_mouse_down",
                    return_value=False,
                ),
                patch.object(
                    self.editor.preview,
                    "screen_to_world",
                    return_value=(33, 1),
                ),
            ):
                self.editor._handle_viewport_input(
                    imgui.ImVec2(0, 0),
                    imgui.ImVec2(100, 100),
                )
        finally:
            io.key_super = previous_super

        self.assertEqual(
            set(self.editor.state.selected_tile_cells),
            {(0, 0), (2, 0)},
        )
        self.assertIsNone(self.editor._tile_edit_drag)

    def test_command_click_toggles_sparse_palette_selection(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        self.editor.state.add_color_tileset()
        for color in (
            [255, 0, 0, 255],
            [0, 255, 0, 255],
            [0, 0, 255, 255],
        ):
            self.editor.state.add_color_tile(color)
        self.editor.state.select_palette_tiles([0], 1, 1)
        self.editor.state._set_tile_cell(layer, 0, 0, TileCell(0))
        self.editor.state.select_tile_rectangle((0, 0), (0, 0))
        io = imgui.get_io()
        previous_super = io.key_super
        io.key_super = True
        try:
            with patch(
                "g_editor.panels.tile_palette.imgui.is_item_clicked",
                return_value=True,
            ):
                self.editor._update_palette_drag(
                    3,
                    first_gid=0,
                    columns=2,
                    tile_count=4,
                    left_button=0,
                )
        finally:
            io.key_super = previous_super

        self.assertEqual(self.editor.state.palette_tile_ids, [0, 3])
        self.assertEqual(
            self.editor.state.palette_stamp,
            (
                [TileCell(0), None, None, TileCell(3)],
                (2, 2),
            ),
        )
        self.assertEqual(self.editor.state.selected_tile_cells, {})

    def test_tile_toolbar_order_gap_and_right_aligned_random(self) -> None:
        self.editor.state.add_tile_layer("Ground")
        with (
            patch("g_editor.panels.tools.icon_button", return_value=False) as button,
            patch("g_editor.panels.tools.imgui.same_line") as same_line,
            patch("g_editor.panels.tools.imgui.get_cursor_pos_x", return_value=220),
            patch("g_editor.panels.tools.imgui.get_window_width", return_value=600),
            patch("g_editor.panels.tools.imgui.get_style") as style,
            patch("g_editor.panels.tools.imgui.set_cursor_pos_x") as cursor,
        ):
            style.return_value.window_padding.x = 8
            self.editor._draw_tile_tools()

        self.assertEqual(
            [entry.args[1] for entry in button.call_args_list],
            [
                "tile-pencil",
                "tile-rectangle-fill",
                "tile-eraser",
                "tile-select",
                "tile-move",
                "tile-rotate-clockwise",
                "tile-flip-horizontal",
                "tile-flip-vertical",
                "tile-random",
            ],
        )
        self.assertEqual(
            button.call_args_list[3].args[0],
            icons_fontawesome_6.ICON_FA_ARROW_POINTER,
        )
        self.assertEqual(
            same_line.call_args_list,
            [
                call(),
                call(),
                call(0, 20),
                call(),
                call(0, 20),
                call(),
                call(),
                call(0, 20),
            ],
        )
        cursor.assert_called_once_with(558)

    def test_placed_tile_selection_drives_inspector_tile_ids(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        self.editor.state.add_color_tileset()
        self.editor.state.add_color_tile([255, 0, 0, 255])
        self.editor.state._set_tile_cell(layer, 0, 0, TileCell(0))
        self.editor.state._set_tile_cell(layer, 1, 0, TileCell(1))
        self.editor.state.select_tile_rectangle((0, 0), (1, 0))

        with (
            patch("g_editor.panels.inspector.imgui.separator_text"),
            patch.object(
                self.editor,
                "_draw_selected_tiles_inspector",
            ) as draw_selected,
        ):
            self.editor._draw_selected_tile_inspector()

        draw_selected.assert_called_once_with([0, 1])

    def test_tile_paint_tools_clear_current_selection(self) -> None:
        self.editor.state.add_tile_layer("Ground")
        self.editor.state.add_color_tileset()
        self.editor.state.paint_tile(1, 1, 0)

        for identifier, tool in (
            ("tile-pencil", "pencil"),
            ("tile-rectangle-fill", "rectangle"),
            ("tile-eraser", "eraser"),
        ):
            with self.subTest(tool=tool):
                self.editor.state.tile_tool = "select"
                self.editor.state.select_tile_rectangle((0, 0), (0, 0))
                with (
                    patch(
                        "g_editor.panels.tools.icon_button",
                        side_effect=lambda _icon, button_id, _tooltip, **_kwargs:
                            button_id == identifier,
                    ),
                    patch("g_editor.panels.tools.imgui.same_line"),
                    patch(
                        "g_editor.panels.tools.imgui.get_cursor_pos_x",
                        return_value=220,
                    ),
                    patch(
                        "g_editor.panels.tools.imgui.get_window_width",
                        return_value=600,
                    ),
                    patch("g_editor.panels.tools.imgui.get_style") as style,
                    patch("g_editor.panels.tools.imgui.set_cursor_pos_x"),
                    patch("g_editor.panels.tools.imgui.text_disabled"),
                ):
                    style.return_value.window_padding.x = 8
                    self.editor._draw_tile_tools()

                self.assertEqual(self.editor.state.tile_tool, tool)
                self.assertEqual(self.editor.state.selected_tile_cells, {})

    def test_pencil_button_toggles_persistent_picker_mode(self) -> None:
        self.editor.state.add_tile_layer("Ground")

        def draw_with_click() -> None:
            with (
                patch(
                    "g_editor.panels.tools.icon_button",
                    side_effect=lambda _icon, identifier, _tooltip, **_kwargs:
                        identifier == "tile-pencil",
                ),
                patch("g_editor.panels.tools.imgui.same_line"),
                patch(
                    "g_editor.panels.tools.imgui.get_cursor_pos_x",
                    return_value=220,
                ),
                patch(
                    "g_editor.panels.tools.imgui.get_window_width",
                    return_value=600,
                ),
                patch("g_editor.panels.tools.imgui.get_style") as style,
                patch("g_editor.panels.tools.imgui.set_cursor_pos_x"),
            ):
                style.return_value.window_padding.x = 8
                self.editor._draw_tile_tools()

        draw_with_click()
        self.assertEqual(self.editor.state.tile_tool, "picker")
        draw_with_click()
        self.assertEqual(self.editor.state.tile_tool, "pencil")

    def test_tile_tool_keyboard_shortcuts(self) -> None:
        self.editor.state.add_tile_layer("Ground")
        self.editor.state.add_color_tileset()
        self.editor.state.paint_tile(1, 1, 0)

        for symbol, tool in (
            (key.S, "pencil"),
            (key.D, "eraser"),
            (key.F, "rectangle"),
        ):
            with self.subTest(tool=tool):
                self.editor.state.select_tile_rectangle((0, 0), (0, 0))
                self.editor._tile_fill_start = (0, 0)
                self.editor.on_key_press(symbol, 0)
                self.assertEqual(self.editor.state.tile_tool, tool)
                self.assertEqual(self.editor.state.selected_tile_cells, {})
                self.assertIsNone(self.editor._tile_fill_start)

        for symbol, attribute in (
            (key.R, "tile_rotation"),
            (key.H, "tile_flip_horizontal"),
            (key.V, "tile_flip_vertical"),
        ):
            with self.subTest(transform=attribute):
                self.editor.state.select_tile_rectangle((0, 0), (0, 0))
                with patch.object(self.editor.preview, "reload") as reload:
                    self.editor.on_key_press(symbol, 0)
                self.assertTrue(getattr(self.editor.state, attribute))
                self.assertEqual(
                    set(self.editor.state.selected_tile_cells), {(0, 0)}
                )
                reload.assert_called_once_with()

    def test_shift_drag_samples_exact_viewport_stamp(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        self.editor.state.add_color_tile()
        horizontal = TileFlags.FLIP_HORIZONTAL
        vertical = TileFlags.FLIP_VERTICAL
        self.editor.state._set_tile_cell(
            layer, 0, 1, TileCell(0, horizontal)
        )
        self.editor.state._set_tile_cell(
            layer, 1, 0, TileCell(1, vertical)
        )
        self.editor.state.tile_tool = "pencil"
        self.editor._viewport_hovered = True
        io = imgui.get_io()
        original_text_input = io.want_text_input
        io.want_text_input = False
        self.editor.on_key_press(key.LSHIFT, 0)
        try:
            with (
                patch.object(
                    self.editor,
                    "_window_to_preview",
                    side_effect=[
                        (1, 17),
                        (17, 1),
                        (17, 1),
                    ],
                ),
                patch.object(
                    self.editor.preview,
                    "screen_to_world",
                    side_effect=lambda x, y: (x, y),
                ),
            ):
                self.editor.on_mouse_press(
                    10, 10, mouse.LEFT, key.MOD_SHIFT
                )
                self.assertEqual(self.editor._tile_pick_start, (0, 1))
                self.editor.on_mouse_drag(
                    20,
                    20,
                    10,
                    10,
                    mouse.LEFT,
                    key.MOD_SHIFT,
                )
                self.assertEqual(self.editor._tile_pick_current, (1, 0))
                draw_list = Mock()
                with (
                    patch(
                        "g_editor.application.imgui.get_window_draw_list",
                        return_value=draw_list,
                    ),
                    patch("g_editor.application.imgui.set_mouse_cursor"),
                    patch.object(
                        self.editor.preview,
                        "world_to_screen",
                        side_effect=lambda x, y: (x, y),
                    ),
                ):
                    self.editor._draw_tile_pick_preview(
                        imgui.ImVec2(0, 0),
                        imgui.ImVec2(100, 100),
                    )
                draw_list.add_rect_filled.assert_called_once()
                draw_list.add_rect.assert_called_once()
                self.editor.on_mouse_release(
                    20, 20, mouse.LEFT, key.MOD_SHIFT
                )
        finally:
            self.editor.on_key_release(key.LSHIFT, 0)
            io.want_text_input = original_text_input

        self.assertIsNone(self.editor._tile_pick_start)
        self.assertEqual(
            self.editor.state.palette_stamp,
            (
                [
                    TileCell(0, horizontal),
                    None,
                    None,
                    TileCell(1, vertical),
                ],
                (2, 2),
            ),
        )
        self.assertEqual(self.editor.state.tile_tool, "pencil")
        self.assertFalse(self.editor._shift_pick_held)

    def test_shift_click_samples_one_viewport_tile(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        flipped = TileCell(0, TileFlags.FLIP_HORIZONTAL)
        self.editor.state._set_tile_cell(layer, 2, -1, flipped)
        self.editor.state.tile_tool = "pencil"

        with (
            patch.object(
                self.editor,
                "_window_to_preview",
                return_value=(33, -15),
            ),
            patch.object(
                self.editor.preview,
                "screen_to_world",
                side_effect=lambda x, y: (x, y),
            ),
        ):
            self.editor.on_mouse_press(
                10, 10, mouse.LEFT, key.MOD_SHIFT
            )
            self.editor.on_mouse_release(
                10, 10, mouse.LEFT, key.MOD_SHIFT
            )

        self.assertEqual(
            self.editor.state.palette_stamp,
            ([flipped], (1, 1)),
        )

    def test_persistent_picker_samples_without_shift(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        layer["offset"] = [8, -4]
        self.editor.state.add_color_tileset()
        sampled = TileCell(0, TileFlags.FLIP_VERTICAL)
        self.editor.state._set_tile_cell(layer, 2, 1, sampled)
        self.editor.state.tile_tool = "picker"

        with (
            patch.object(
                self.editor,
                "_window_to_preview",
                return_value=(100, 80),
            ),
            patch.object(
                self.editor.preview,
                "screen_to_world",
                return_value=(41, 20),
            ) as screen_to_world,
        ):
            self.editor.on_mouse_press(10, 10, mouse.LEFT, 0)
            self.editor.on_mouse_release(10, 10, mouse.LEFT, 0)

        screen_to_world.assert_called()
        self.assertEqual(
            self.editor.state.palette_stamp,
            ([sampled], (1, 1)),
        )

    def test_shift_changes_pencil_button_to_tile_picker(self) -> None:
        self.editor.state.add_tile_layer("Ground")
        io = imgui.get_io()
        original_text_input = io.want_text_input
        io.want_text_input = False
        self.editor.on_key_press(key.LSHIFT, 0)
        try:
            with (
                patch(
                    "g_editor.panels.tools.icon_button",
                    return_value=False,
                ) as button,
                patch("g_editor.panels.tools.imgui.same_line"),
                patch(
                    "g_editor.panels.tools.imgui.get_cursor_pos_x",
                    return_value=220,
                ),
                patch(
                    "g_editor.panels.tools.imgui.get_window_width",
                    return_value=600,
                ),
                patch("g_editor.panels.tools.imgui.get_style") as style,
                patch("g_editor.panels.tools.imgui.set_cursor_pos_x"),
            ):
                style.return_value.window_padding.x = 8
                self.editor._draw_tile_tools()
        finally:
            self.editor.on_key_release(key.LSHIFT, 0)
            io.want_text_input = original_text_input

        pencil = button.call_args_list[0]
        self.assertEqual(
            pencil.args[0],
            icons_fontawesome_6.ICON_FA_EYE_DROPPER,
        )
        self.assertIn("Tile Picker", pencil.args[2])

    def test_tile_move_click_selects_one_tile_and_escape_cancels(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        self.editor.state.paint_tile(1, 1, 0)
        self.editor.state.paint_tile(33, 1, 0)
        self.editor.preview.reload()
        self.editor._shortcut_context = "viewport"
        self.editor.on_key_press(key.Q, 0)
        self.assertEqual(self.editor.state.tile_tool, "select")
        self.editor.state.select_tile_rectangle((0, 0), (0, 0))
        self.editor.on_key_press(key.W, 0)
        self.assertEqual(self.editor.state.tile_tool, "move")
        self.editor._begin_tile_edit_drag((2, 0))
        self.assertEqual(set(self.editor.state.selected_tile_cells), {(2, 0)})
        self.editor._update_tile_edit_drag((3, 0), down=True)
        self.editor.on_key_press(key.ESCAPE, 0)
        self.assertIsNone(self.editor._tile_edit_drag)
        self.assertEqual(self.editor.state.tile_at(2, 0), TileCell(0))
        self.assertIsNone(self.editor.state.tile_at(3, 0))
        self.assertEqual(self.editor.state.selected_tile_cells, {})
        self.editor._begin_tile_edit_drag((10, 10))
        self.assertIsNone(self.editor._tile_edit_drag)

    def test_selected_tiles_delete_with_command_delete_and_context_menu(self) -> None:
        self.editor.state.add_tile_layer("Ground")
        self.editor.state.add_color_tileset()
        self.editor.state.paint_tile(1, 1, 0)
        self.editor.state.select_tile_rectangle((0, 0), (0, 0))
        self.editor._shortcut_context = "viewport"
        io = imgui.get_io()
        original_text_input = io.want_text_input
        try:
            io.want_text_input = True
            self.editor.on_key_press(key.BACKSPACE, key.MOD_COMMAND)
            self.assertEqual(self.editor.state.tile_at(0, 0), TileCell(0))
            io.want_text_input = False
            self.editor.on_key_press(key.BACKSPACE, key.MOD_COMMAND)
            self.assertIsNone(self.editor.state.tile_at(0, 0))
            self.assertTrue(self.editor.state.undo())
            self.editor.on_key_press(key.DELETE, key.MOD_CTRL)
            self.assertIsNone(self.editor.state.tile_at(0, 0))
            self.assertTrue(self.editor.state.undo())
            entries = []

            def menu_item(label, shortcut="", selected=False, enabled=True):
                entries.append((label, shortcut, enabled))
                return label == "Delete" and enabled, False

            with (
                patch.object(imgui, "begin_popup", return_value=True),
                patch.object(imgui, "begin_menu", return_value=False),
                patch.object(imgui, "end_popup"),
                patch.object(imgui, "separator"),
                patch.object(imgui, "is_item_clicked", return_value=False),
                patch.object(imgui, "menu_item", side_effect=menu_item),
            ):
                self.editor._draw_viewport_context_menu(imgui.ImVec2(0, 0))
                self.assertIn(("Delete", "Cmd/Ctrl+Delete", True), entries)
                self.assertIsNone(self.editor.state.tile_at(0, 0))
                entries.clear()
                self.editor._draw_viewport_context_menu(imgui.ImVec2(0, 0))
                self.assertIn(("Delete", "Cmd/Ctrl+Delete", False), entries)
        finally:
            io.want_text_input = original_text_input

    def test_tile_move_release_outside_viewport_finishes_drag(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        self.editor.state.paint_tile(1, 1, 0)
        self.editor.preview.reload()
        self.editor.state.tile_tool = "move"
        self.editor._viewport_hovered = True
        pointer = imgui.ImVec2(10, 10)
        with (
            patch("g_editor.application.imgui.get_mouse_pos", return_value=pointer),
            patch("g_editor.application.imgui.is_mouse_clicked", side_effect=lambda button: button == 0),
            patch("g_editor.application.imgui.is_mouse_down", return_value=True),
            patch.object(self.editor.preview, "screen_to_world", return_value=(1, 1)),
        ):
            self.editor._handle_viewport_input(
                imgui.ImVec2(0, 0), imgui.ImVec2(100, 100)
            )
        self.assertIsNotNone(self.editor._tile_edit_drag)
        self.editor._viewport_hovered = False
        with (
            patch("g_editor.application.imgui.get_mouse_pos", return_value=pointer),
            patch("g_editor.application.imgui.is_mouse_down", return_value=False),
            patch.object(self.editor.preview, "screen_to_world", return_value=(-17, -17)),
        ):
            self.editor._handle_viewport_input(
                imgui.ImVec2(0, 0), imgui.ImVec2(100, 100)
            )
        self.assertIsNone(self.editor._tile_edit_drag)
        self.assertIsNone(self.editor.state.tile_at(0, 0))
        self.assertEqual(self.editor.state.tile_at(-2, -2), TileCell(0))

    def test_tile_drag_is_cancelled_on_layer_tool_or_revision_change(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        self.editor.state.paint_tile(1, 1, 0)
        self.editor.preview.reload()
        for change in ("tool", "layer", "revision", "mode"):
            with self.subTest(change=change):
                self.editor.state.select_layer(str(layer["id"]))
                self.editor.state.tile_tool = "move"
                self.editor.preview.mode = "edit"
                self.editor._begin_tile_edit_drag((0, 0))
                if change == "tool":
                    self.editor.state.tile_tool = "pencil"
                elif change == "layer":
                    self.editor.state.add_tile_layer("Other")
                elif change == "revision":
                    self.editor.state.paint_tile(17, 1, 0)
                else:
                    self.editor.preview.mode = "play"
                self.editor._update_tile_edit_drag((3, 3), down=False)
                self.assertIsNone(self.editor._tile_edit_drag)
                self.editor.preview.mode = "edit"
                self.editor.state.select_layer(str(layer["id"]))
                self.assertEqual(self.editor.state.tile_at(0, 0), TileCell(0))
                self.assertIsNone(self.editor.state.tile_at(3, 3))

    def test_tile_move_draws_runtime_tiles_and_only_outlines_at_drag_offset(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        self.editor.state.add_color_tileset()
        self.editor.state.paint_tile(1, 1, 0)
        self.editor.preview.reload()
        self.editor.state.tile_tool = "move"
        self.editor._begin_tile_edit_drag((0, 0))
        self.editor._update_tile_edit_drag((-2, 3), down=True)
        draw_list = Mock()
        with (
            patch("g_editor.application.imgui.get_window_draw_list", return_value=draw_list),
            patch.object(self.editor, "_tile_cell_viewport_bounds", return_value=(imgui.ImVec2(10, 20), imgui.ImVec2(30, 40))) as bounds,
            patch.object(self.editor, "_draw_tile_placement_preview_cell") as preview,
        ):
            self.editor._draw_tile_selection(
                imgui.ImVec2(0, 0), imgui.ImVec2(100, 100)
            )
        self.assertEqual(bounds.call_args.args[1:3], (-2, 3))
        preview.assert_not_called()
        draw_list.add_rect_filled.assert_not_called()
        game = self.editor.preview.game
        assert game is not None
        runtime_layer = game.scenes.current.layers[str(layer["id"])]
        self.assertIsNone(runtime_layer.tile_at(0, 0))
        self.assertEqual(runtime_layer.tile_at(-2, 3), TileCell(0))
        draw_list.push_clip_rect.assert_called_once()
        draw_list.pop_clip_rect.assert_called_once()

    def test_live_tile_move_restores_overwritten_destinations_and_escape_cancels(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        self.editor.state._set_tile_cell(layer, 0, 0, TileCell(0, TileFlags.FLIP_HORIZONTAL))
        self.editor.state._set_tile_cell(layer, 1, 0, TileCell(0, TileFlags.FLIP_DIAGONAL))
        self.editor.state.mark_changed("Seed drag")
        self.editor.preview.reload()
        self.editor.state.select_tile_rectangle((0, 0), (0, 0))
        self.editor.state.tile_tool = "move"
        revision = self.editor.state.revision
        self.editor._begin_tile_edit_drag((0, 0))
        self.editor._update_tile_edit_drag((1, 0), down=True)
        runtime = self.editor.preview.game.scenes.current.layers[str(layer["id"])]
        self.assertIsNone(runtime.tile_at(0, 0))
        self.assertEqual(runtime.tile_at(1, 0), TileCell(0, TileFlags.FLIP_HORIZONTAL))
        self.editor._update_tile_edit_drag((2, 0), down=True)
        self.assertEqual(runtime.tile_at(1, 0), TileCell(0, TileFlags.FLIP_DIAGONAL))
        self.assertEqual(self.editor.state.revision, revision)
        self.assertEqual(self.editor.state.tile_at(0, 0), TileCell(0, TileFlags.FLIP_HORIZONTAL))
        self.editor._shortcut_context = "viewport"
        self.editor.on_key_press(key.ESCAPE, 0)
        runtime = self.editor.preview.game.scenes.current.layers[str(layer["id"])]
        self.assertEqual(runtime.tile_at(0, 0), TileCell(0, TileFlags.FLIP_HORIZONTAL))
        self.assertIsNone(runtime.tile_at(2, 0))
        self.assertEqual(self.editor.state.revision, revision)

    def test_mixed_rectangle_move_and_clipboard_use_both_selected_layers(self) -> None:
        objects = self.editor.state.selected_layer
        obj = self.editor.state.add_object("rectangle", x=8, y=8)
        tiles = self.editor.state.add_tile_layer("Ground")
        tiles["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        self.editor.state.paint_tile(1, 1, 0)
        self.editor.state.select_layer(str(objects["id"]), toggle=True)
        self.editor.preview.reload()
        self.editor.state.tile_tool = "select"
        self.editor._begin_tile_edit_drag((0, 0))
        self.editor._update_tile_edit_drag((0, 0), down=False)
        self.assertIn(str(obj["id"]), self.editor.state.selected_ids)
        self.assertTrue(self.editor.state.mixed_layer_selection)
        with patch.object(self.editor, "_handle_viewport_input"):
            self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)
        self.editor.state.tile_tool = "move"
        self.editor._begin_tile_edit_drag((0, 0))
        self.editor._update_tile_edit_drag((2, 1), down=True)
        target = self.editor.preview.selected_runtime_objects()[0]
        self.assertEqual(tuple(target.world_position), (40, 24))
        self.assertEqual(position(obj), (8, 8))
        with patch.object(self.editor, "_handle_viewport_input"):
            self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)
        self.editor._update_tile_edit_drag((2, 1), down=False)
        self.assertEqual(position(obj), (40, 24))
        self.assertEqual(self.editor.state.tile_at(2, 1), TileCell(0))
        self.editor._shortcut_context = "viewport"
        self.assertTrue(self.editor._copy_selection())
        self.editor._paste_selection()
        self.assertEqual(self.editor.state.tile_at(3, 2), TileCell(0))
        self.assertEqual(position(self.editor.state.selected_records[0]), (56, 40))
        self.editor._undo()
        self.assertEqual(len(self.editor.state.records), 1)
        self.editor._undo()
        self.assertEqual(position(self.editor.state.selected_records[0]), (8, 8))
        self.assertEqual(self.editor.state.tile_at(0, 0), TileCell(0))

    def test_mixed_toolbar_has_only_shared_selection_actions(self) -> None:
        objects = self.editor.state.selected_layer
        self.editor.state.add_tile_layer("Ground")
        self.editor.state.select_layer(str(objects["id"]), toggle=True)
        with (
            patch("g_editor.panels.tools.icon_button", return_value=False) as button,
            patch("g_editor.panels.tools.imgui.same_line"),
            patch("g_editor.panels.tools.imgui.text_disabled"),
        ):
            self.editor._draw_mixed_tools()
        self.assertEqual(
            [entry.args[1] for entry in button.call_args_list],
            ["mixed-select", "mixed-move"],
        )

    def test_tile_clipboard_pastes_at_clicked_world_position(self) -> None:
        layer = self.editor.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        self.editor.state.add_color_tileset()
        self.editor.state.paint_tile(1, 1, 0)
        self.editor.state.select_tile_rectangle((0, 0), (0, 0))
        self.editor.preview.reload()
        self.editor._shortcut_context = "viewport"
        self.assertTrue(self.editor._copy_selection())
        self.editor._viewport_click_serial += 1
        self.editor._viewport_paste_position = (20, 30)
        with patch.object(self.editor.preview, "screen_to_world", return_value=(-32, 48)):
            self.editor._paste_selection()
        self.assertEqual(self.editor.state.tile_at(-2, 3), TileCell(0))
        self.editor._undo()
        self.assertIsNone(self.editor.state.tile_at(-2, 3))

    def test_tile_stamp_preview_anchors_at_upper_left_and_draws_downward(
        self,
    ) -> None:
        preview = self.editor._tile_placement_preview_cells(
            (2, 3),
            (2, 3),
            [TileCell(tile_id) for tile_id in (10, 11, 12, 13)],
            (2, 2),
            stamp_from_top_left=True,
        )

        self.assertEqual(
            preview,
            [
                (2, 3, TileCell(10)),
                (3, 3, TileCell(11)),
                (2, 2, TileCell(12)),
                (3, 2, TileCell(13)),
            ],
        )

    def test_atlas_tile_preview_keeps_individual_tiles_upright(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            image_path = Path(directory) / "tiles.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(32, 32).save(str(image_path))
            layer = self.editor.state.add_tile_layer("Ground")
            self.editor.state.configure_tile_layer(
                image_path.relative_to(
                    self.editor.state.asset_root
                ).as_posix(),
                16,
                16,
            )
            for tile_id in range(4):
                with self.subTest(tile_id=tile_id):
                    draw_list = Mock()
                    self.editor._draw_tile_placement_preview_cell(
                        draw_list,
                        layer,
                        tile_id,
                        imgui.ImVec2(0, 0),
                        imgui.ImVec2(16, 16),
                    )

                    draw_list.add_image.assert_called_once()
                    uv_top_left = draw_list.add_image.call_args.args[3]
                    uv_bottom_right = draw_list.add_image.call_args.args[4]
                    column, row = tile_id % 2, tile_id // 2
                    self.assertEqual(uv_top_left.x, column / 2)
                    self.assertEqual(uv_top_left.y, 1 - row / 2)
                    self.assertEqual(uv_bottom_right.x, (column + 1) / 2)
                    self.assertEqual(
                        uv_bottom_right.y,
                        1 - (row + 1) / 2,
                    )
            flipped = TileCell(
                0, TileFlags.FLIP_HORIZONTAL | TileFlags.FLIP_DIAGONAL
            )
            self.editor.state._set_tile_cell(layer, 0, 0, flipped)
            self.editor.state.mark_changed("Seed flipped tile")
            self.editor.preview.reload()
            self.editor.state.tile_tool = "move"
            self.editor._begin_tile_edit_drag((0, 0))
            self.editor._update_tile_edit_drag((1, 0), down=True)
            with patch.object(self.editor, "_handle_viewport_input"):
                self.editor.on_draw()
            self.assertIsNone(self.editor.state.error)
            draw_list = Mock()
            self.editor._draw_tile_placement_preview_cell(
                draw_list, layer, 0,
                imgui.ImVec2(0, 0), imgui.ImVec2(16, 16),
                flags=TileFlags.FLIP_HORIZONTAL,
            )
            draw_list.add_image_quad.assert_called_once()
            args = draw_list.add_image_quad.call_args.args
            self.assertEqual((args[5].x, args[5].y), (0.5, 1))
            self.assertEqual((args[6].x, args[6].y), (0, 1))
            self.assertEqual((args[7].x, args[7].y), (0, 0.5))
            self.assertEqual((args[8].x, args[8].y), (0.5, 0.5))
            self.editor._update_tile_edit_drag((1, 0), down=False)
            game = self.editor.preview.game
            assert game is not None
            runtime_layer = game.scenes.current.layers[str(layer["id"])]
            assert isinstance(runtime_layer, TileLayer)
            self.assertEqual(runtime_layer.tile_at(1, 0), flipped)

    def test_random_tile_preview_is_stable_and_uses_selected_tiles(self) -> None:
        first = self.editor._tile_placement_preview_cells(
            (-2, -1),
            (2, 1),
            [TileCell(tile_id) for tile_id in (4, 8, 12)],
            (3, 1),
            randomize=True,
        )
        second = self.editor._tile_placement_preview_cells(
            (-2, -1),
            (2, 1),
            [TileCell(tile_id) for tile_id in (4, 8, 12)],
            (3, 1),
            randomize=True,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 15)
        self.assertTrue(
            {cell.tile_id for _, _, cell in first} <= {4, 8, 12}
        )

    def test_color_tiles_render_in_runtime_preview_without_assets(self) -> None:
        layer = self.editor.state.add_tile_layer("Prototype")
        palette = self.editor.state.add_color_tileset(
            color=[40, 120, 60, 255],
        )
        tile_id = self.editor.state.add_color_tile(
            [70, 90, 180, 255]
        )
        self.editor.state.paint_tile(1, 1, tile_id)

        self.editor.preview.synchronize()

        game = self.editor.preview.game
        assert game is not None
        runtime_layer = game.scenes.current.layers[str(layer["id"])]
        self.assertIsInstance(runtime_layer, TileLayer)
        assert isinstance(runtime_layer, TileLayer)
        runtime_tileset = runtime_layer.tileset
        self.assertIsInstance(runtime_tileset, ColorTileSet)
        assert isinstance(runtime_tileset, ColorTileSet)
        self.assertEqual(
            runtime_tileset.colors,
            (
                (40, 120, 60, 255),
                (70, 90, 180, 255),
            ),
        )
        self.assertEqual(
            runtime_layer.tile_at(0, 0),
            TileCell(tile_id),
        )
        self.assertEqual(palette["tile_count"], 2)

    def test_image_collection_tiles_render_in_runtime_preview(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            root = Path(directory)
            for name, color, size in (
                ("crate.png", (150, 90, 40, 255), (12, 20)),
                ("key.png", (240, 210, 50, 255), (24, 10)),
            ):
                pyglet.image.SolidColorImagePattern(color).create_image(
                    *size
                ).save(str(root / name))
            crate_path = root.relative_to(
                self.editor.state.asset_root
            ) / "crate.png"
            key_path = root.relative_to(
                self.editor.state.asset_root
            ) / "key.png"
            layer = self.editor.state.add_tile_layer("Items")
            collection = self.editor.state.add_image_tileset(
                crate_path.as_posix()
            )
            tile_id = self.editor.state.add_image_tile(
                key_path.as_posix()
            )
            self.editor.state.paint_tile(1, 1, tile_id)

            self.editor.preview.synchronize()

            game = self.editor.preview.game
            assert game is not None
            runtime_layer = game.scenes.current.layers[str(layer["id"])]
            self.assertIsInstance(runtime_layer, TileLayer)
            assert isinstance(runtime_layer, TileLayer)
            self.assertIsInstance(
                runtime_layer.tileset,
                ImageCollectionTileSet,
            )
            assert isinstance(
                runtime_layer.tileset,
                ImageCollectionTileSet,
            )
            self.assertEqual(
                [
                    image._filename.name
                    for image in runtime_layer.tileset.images
                    if image._filename is not None
                ],
                ["crate.png", "key.png"],
            )
            self.assertEqual(
                runtime_layer.tile_at(0, 0),
                TileCell(tile_id),
            )
            self.assertEqual(collection["tile_count"], 2)

    def test_asset_picker_creates_and_extends_image_collection(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            root = Path(directory)
            first = root / "crate.png"
            second = root / "key.png"
            pyglet.image.SolidColorImagePattern(
                (150, 90, 40, 255)
            ).create_image(12, 20).save(str(first))
            pyglet.image.SolidColorImagePattern(
                (240, 210, 50, 255)
            ).create_image(24, 10).save(str(second))
            layer = self.editor.state.add_tile_layer("Items")

            self.editor._assign_asset(
                _AssetPickerTarget(
                    "image-collection-new",
                    "Image Tile",
                    "image",
                ),
                first,
            )
            collection = self.editor.state.active_tileset(layer)
            assert collection is not None
            identifier = str(collection["id"])
            self.editor._assign_asset(
                _AssetPickerTarget(
                    f"image-collection-add:{identifier}",
                    "Image Tile",
                    "image",
                ),
                second,
            )

        self.assertEqual(collection["type"], "images")
        self.assertEqual(collection["tile_count"], 2)
        self.assertEqual(len(collection["images"]), 2)  # type: ignore[arg-type]

    def test_asset_picker_assigns_and_clears_project_fonts(self) -> None:
        font_path = (
            self.editor.state.asset_root
            / "g_editor/assets/fonts/Inter/static/Inter_18pt-Regular.ttf"
        )
        text = self.editor.state.add_object("text")
        target = _AssetPickerTarget("font:0", "Font", "font")

        self.editor._assign_asset(target, font_path)

        properties = text["components"][0]["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(
            properties["font"],
            font_path.relative_to(
                self.editor.state.asset_root
            ).as_posix(),
        )
        self.editor._clear_asset(target)
        self.assertIsNone(properties["font"])

        view = self.editor.state.document.ui_views[0]
        self.editor.state.select_layer(str(view["id"]))
        label = self.editor.state.add_gui_element("label")
        gui_target = _AssetPickerTarget(
            f"gui-font:{label['id']}",
            "Font",
            "font",
        )
        self.editor._assign_asset(gui_target, font_path)
        self.assertEqual(
            label["font"],
            font_path.relative_to(
                self.editor.state.asset_root
            ).as_posix(),
        )
        self.editor._clear_asset(gui_target)
        self.assertIsNone(label["font"])

    def test_sprite_creation_uses_real_engine_renderer_and_picking(
        self,
    ) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            image_path = Path(directory) / "hero.png"
            pyglet.image.SolidColorImagePattern(
                (240, 80, 60, 255)
            ).create_image(40, 50).save(str(image_path))
            relative = image_path.relative_to(
                self.editor.state.asset_root
            )

            record = self.editor.state.add_sprite(
                relative,
                x=400,
                y=300,
            )
            self.editor.preview.synchronize()

            runtime = self.editor.preview.selected_runtime_object()
            self.assertIsNotNone(runtime)
            assert runtime is not None
            self.assertIs(type(runtime), GameObject)
            self.assertIsNone(runtime.collider)
            renderer = runtime.require_component(SpriteRenderer)
            self.assertEqual(
                (renderer.drawable.width, renderer.drawable.height),
                (40, 50),
            )
            self.assertEqual(
                self.editor.preview.pick(400, 300),
                record["id"],
            )

    def test_external_assets_import_into_selected_folder(self) -> None:
        with (
            TemporaryDirectory(
                dir=self.editor.state.asset_root
            ) as destination_directory,
            TemporaryDirectory() as source_directory,
        ):
            destination = Path(destination_directory)
            source = Path(source_directory) / "hero.png"
            source.write_bytes(b"first")
            self.editor._selected_asset_directory = destination

            first = self.editor._import_external_assets([str(source)])
            second = self.editor._import_external_assets([str(source)])
            folder = Path(source_directory) / "characters"
            folder.mkdir()
            (folder / "player.txt").write_text("player")
            imported_folder = self.editor._import_external_assets(
                [str(folder)]
            )

            self.assertEqual(first[0].name, "hero.png")
            self.assertEqual(second[0].name, "hero 2.png")
            self.assertEqual(imported_folder[0].name, "characters")
            self.assertTrue(
                (imported_folder[0] / "player.txt").is_file()
            )

    def test_asset_picker_assigns_image_and_exits(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            image_path = Path(directory) / "replacement.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(24, 30).save(str(image_path))
            self.editor.state.add_sprite(image_path)
            target = _AssetPickerTarget(
                "sprite-image:0",
                "image",
                "image",
            )
            self.editor._asset_picker = target

            self.editor._assign_asset_picker(image_path)

            self.assertIsNone(self.editor._asset_picker)
            self.editor.on_draw()
            self.assertIsNone(self.editor.state.error)
            selected = self.editor.state.selected
            assert selected is not None
            renderer = selected["components"][0]  # type: ignore[index]
            properties = renderer["properties"]  # type: ignore[index]
            self.assertEqual(
                properties["image"],  # type: ignore[index]
                image_path.relative_to(
                    self.editor.state.asset_root
                ).as_posix(),
            )
            self.editor._asset_picker = target
            self.editor.on_key_press(key.ESCAPE, 0)
            self.assertIsNone(self.editor._asset_picker)

    def test_asset_picker_stays_active_for_asset_child_clicks(
        self,
    ) -> None:
        with (
            patch.object(
                imgui,
                "is_window_hovered",
                return_value=True,
            ) as is_window_hovered,
            patch.object(
                imgui,
                "is_mouse_clicked",
                return_value=True,
            ),
        ):
            clicked = self.editor._asset_panel_clicked()

        is_window_hovered.assert_called_with(
            int(imgui.HoveredFlags_.child_windows.value)
        )
        self.assertTrue(clicked)

    def test_asset_picker_none_clears_image_and_supports_undo(self) -> None:
        with TemporaryDirectory(dir=self.editor.state.asset_root) as directory:
            image_path = Path(directory) / "hero.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(8, 8).save(str(image_path))
            self.editor.state.add_sprite(image_path)
            target = _AssetPickerTarget("sprite-image:0", "image", "image")
            self.editor._asset_picker = target
            self.editor._json_buffers[target.field_id] = "hero.png"
            original_button = imgui.button

            def pick_none(
                label: str, size: imgui.ImVec2 = imgui.ImVec2(0, 0)
            ) -> bool:
                clicked = original_button(label, size)
                return label == "None##asset-picker-none" or clicked

            with patch(
                "g_editor.panels.assets.imgui.button",
                side_effect=pick_none,
            ) as button:
                self.editor.on_draw()
            self.assertIn(
                call("None##asset-picker-none"), button.call_args_list
            )
            self.assertIsNone(self.editor._asset_picker)
            self.assertEqual(self.editor._json_buffers[target.field_id], "")
            selected = self.editor.state.selected
            assert selected is not None
            components = selected["components"]
            assert isinstance(components, list)
            renderer = components[0]
            assert isinstance(renderer, dict)
            properties = renderer["properties"]
            assert isinstance(properties, dict)
            self.assertIsNone(properties["image"])
            self.editor.on_draw()
            self.assertIsNone(self.editor.state.error)
            self.assertIsNotNone(self.editor.preview.game)
            self.assertTrue(self.editor.state.undo())
            selected = self.editor.state.selected
            assert selected is not None
            components = selected["components"]
            assert isinstance(components, list)
            renderer = components[0]
            assert isinstance(renderer, dict)
            properties = renderer["properties"]
            assert isinstance(properties, dict)
            self.assertEqual(
                properties["image"],
                image_path.relative_to(self.editor.state.asset_root).as_posix(),
            )

    def test_asset_picker_required_asset_does_not_offer_none(self) -> None:
        self.editor._asset_picker = _AssetPickerTarget(
            "tileset-path:ground:atlas", "image", "image"
        )
        with patch(
            "g_editor.panels.assets.imgui.button", wraps=imgui.button
        ) as button:
            self.editor.on_draw()
        self.assertNotIn(
            call("None##asset-picker-none"), button.call_args_list
        )

    def test_asset_picker_abbreviates_long_local_paths(self) -> None:
        self.assertEqual(
            self.editor._abbreviate_asset_path("sprites/hero.png"),
            "sprites/hero.png",
        )
        abbreviated = self.editor._abbreviate_asset_path(
            "characters/playable/heroes/forest/hero.png",
            28,
        )
        self.assertIn("/.../", abbreviated)
        self.assertTrue(abbreviated.endswith("hero.png"))
        self.assertLessEqual(len(abbreviated), 28)

    def test_property_fields_share_common_layout_base(self) -> None:
        from g_editor.ui import Vec2Field

        for field_type in (
            AssetField,
            BooleanField,
            ColorField,
            FloatField,
            IntegerField,
            OptionalColorField,
            TextField,
            Vec2Field,
        ):
            self.assertTrue(issubclass(field_type, PropertyField))

    def test_inspector_property_helpers_forward_tooltips(self) -> None:
        with patch("g_editor.panels.inspector.TextField") as text_field:
            text_field.return_value.draw.return_value = FieldResult(
                False,
                "Player",
            )
            self.editor._text_property(
                "Name",
                "Player",
                tooltip="A descriptive object name.",
            )
        text_field.return_value.draw.assert_called_once_with(
            "Player",
            tooltip="A descriptive object name.",
        )

    def test_vec2_field_edits_both_coordinates_without_mutating_input(self) -> None:
        from g_editor.ui import Vec2Field
        from g import Vec2

        original = Vec2(10, -20)
        with patch("g_editor.ui.fields.imgui.drag_float2", return_value=(True, (-3.5, 4.25))) as drag:
            changed, value = Vec2Field("Offset")._draw_value("##offset", original)
        drag.assert_called_once_with("##offset", [10, -20], 0.1, 0, 0)
        self.assertTrue(changed)
        self.assertEqual(value, Vec2(-3.5, 4.25))
        self.assertEqual(original, Vec2(10, -20))

    def test_component_inspector_uses_source_labels_bounds_and_tooltips(self) -> None:
        from g import Script, inspector_field

        class Behavior(Script):
            speed: float = inspector_field(
                20.0, label="Travel Speed", tooltip="Speed in pixels per second.",
                minimum=0, maximum=100,
            )
            ordinary: int = 4

        Behavior.__module__ = "source_inspector_ui_test"
        name = "source_inspector_ui_test.Behavior"
        scene_types.register_component(name, Behavior)
        try:
            self.editor.state.add_object("empty")
            self.editor.state.add_component(name)
            with (
                patch("g_editor.panels.inspector.FloatField", wraps=FloatField) as fields,
                patch("g_editor.panels.inspector.imgui.is_item_hovered", return_value=True),
                patch("g_editor.panels.inspector.imgui.is_mouse_hovering_rect", return_value=True),
                patch("g_editor.panels.inspector.imgui.set_tooltip", wraps=imgui.set_tooltip) as tooltips,
            ):
                self.editor.on_draw()
            self.assertIsNone(self.editor.state.error)
            self.assertIn(call(
                "Travel Speed##component-0-speed", "0:speed", minimum=0, maximum=100,
            ), fields.call_args_list)
            self.assertIn(call("Speed in pixels per second."), tooltips.call_args_list)
        finally:
            scene_types.unregister_module(Behavior.__module__)

    def test_vec2_property_uses_vector_field_and_preserves_serialization_tag(self) -> None:
        from g_editor.ui import Vec2Field
        from g import Vec2

        with patch.object(Vec2Field, "draw", return_value=FieldResult(True, Vec2(-3, 4))) as draw:
            changed, value = self.editor._property_widget(
                "Offset", {"$vec2": [10, 20]}, "0:offset",
            )
        draw.assert_called_once_with(Vec2(10, 20))
        self.assertTrue(changed)
        self.assertEqual(value, {"$vec2": [-3, 4]})

    def test_physics_body_velocity_uses_vec2_controls_and_supports_undo_reset(self) -> None:
        from g_editor.ui import Vec2Field
        from g import Vec2

        self.editor.state.add_object("empty")
        self.editor.state.add_component("g.physics_body")
        components = self.editor.state.selected["components"]
        assert isinstance(components, list) and isinstance(components[0], dict)
        properties = components[0]["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["velocity"], {"$vec2": [0, 0]})
        with patch.object(Vec2Field, "draw", return_value=FieldResult(True, Vec2(12, -7))) as draw:
            self.editor.on_draw()
        draw.assert_called_once_with(Vec2())
        self.assertEqual(properties["velocity"], {"$vec2": [12, -7]})
        self.assertTrue(self.editor.state.undo())
        components = self.editor.state.selected["components"]
        self.assertEqual(components[0]["properties"]["velocity"], {"$vec2": [0, 0]})
        self.editor.state.set_component_value(0, "velocity", {"$vec2": [5, 6]})
        self.editor.state.reset_component(0)
        self.assertEqual(components[0]["properties"]["velocity"], {"$vec2": [0, 0]})

    def test_component_tooltips_appear_over_label_and_control(self) -> None:
        from g import Script, inspector_field

        class Behavior(Script):
            speed: float = inspector_field(20.0, tooltip="Speed tooltip")
            mode: str = inspector_field("walk", choices=("walk", "run"), tooltip="Mode tooltip")

        Behavior.__module__ = "label_tooltip_test"
        name = "label_tooltip_test.Behavior"
        scene_types.register_component(name, Behavior)
        rectangles = {}
        original_text = imgui.text

        def capture(text):
            original_text(text)
            if text in ("Speed", "Mode"):
                minimum, maximum = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                rectangles[text] = ((minimum.x + maximum.x) / 2, (minimum.y + maximum.y) / 2)

        try:
            record = self.editor.state.add_object("empty")
            self.editor.state.add_component(name)
            components = record["components"]
            assert isinstance(components, list) and isinstance(components[0], dict)

            def draw():
                imgui.set_next_window_pos(imgui.ImVec2(40, 40))
                imgui.set_next_window_size(imgui.ImVec2(500, 400))
                imgui.begin("Label hover test")
                self.editor._draw_component_inspector(0, components[0])
                imgui.end()

            with (
                patch.object(self.editor, "_draw_interface", side_effect=draw),
                patch("g_editor.ui.fields.imgui.text", side_effect=capture),
            ):
                self.editor.on_draw()
                self.editor.on_draw()
                self.assertEqual(set(rectangles), {"Speed", "Mode"})
                for label in ("Speed", "Mode"):
                    imgui.get_io().add_mouse_pos_event(*rectangles[label])
                    with patch("g_editor.panels.inspector.imgui.set_tooltip", wraps=imgui.set_tooltip) as tooltips:
                        self.editor.on_draw()
                    self.assertIn(call(f"{label} tooltip"), tooltips.call_args_list)
                    imgui.get_io().add_mouse_pos_event(rectangles[label][0] + 180, rectangles[label][1])
                    with patch("g_editor.panels.inspector.imgui.set_tooltip", wraps=imgui.set_tooltip) as tooltips:
                        self.editor.on_draw()
                    self.assertIn(call(f"{label} tooltip"), tooltips.call_args_list)
            self.assertIsNone(self.editor.state.error)
        finally:
            scene_types.unregister_module(Behavior.__module__)

    def test_value_field_backgrounds_are_brighter_with_distinct_hover_states(self) -> None:
        style = imgui.get_style()
        normal = style.color_(imgui.Col_.frame_bg)
        hovered = style.color_(imgui.Col_.frame_bg_hovered)
        active = style.color_(imgui.Col_.frame_bg_active)
        for index, component in enumerate(("x", "y", "z")):
            self.assertGreater(getattr(normal, component), (0.080, 0.096, 0.124)[index])
            self.assertLess(getattr(normal, component), (0.16, 0.19, 0.24)[index])
            self.assertGreater(getattr(hovered, component), getattr(normal, component))
            self.assertGreater(getattr(active, component), getattr(hovered, component))

    def test_mask_field_toggles_high_bit_and_supports_all_none(self) -> None:
        from g_editor.ui import MaskField

        for action, expected in (("bit", 1 << 31), ("all", 0xFFFFFFFF), ("none", 0)):
            with (
                patch("g_editor.ui.fields.imgui.begin_combo", return_value=True),
                patch("g_editor.ui.fields.imgui.end_combo"),
                patch("g_editor.ui.fields.imgui.same_line"),
                patch("g_editor.ui.fields.imgui.button", side_effect=lambda label: label.endswith(f"-{action}")),
                patch("g_editor.ui.fields.imgui.checkbox", side_effect=lambda label, enabled: (
                    (True, True) if action == "bit" and label.endswith("-bit-31") else (False, enabled)
                )) as boxes,
            ):
                changed, value = MaskField("Mask", bit_names=("Player",))._draw_value("mask", 0)
            self.assertTrue(changed)
            self.assertEqual(value, expected)
            self.assertEqual(boxes.call_count, 32)
            self.assertTrue(boxes.call_args_list[0].args[0].startswith("Player##"))

    def test_collider_groups_hide_advanced_fields_until_expanded(self) -> None:
        from g_editor.ui import MaskField

        self.editor.state.add_object("empty")
        self.editor.state.add_component("g.collider")
        original = imgui.collapsing_header
        with (
            patch("g_editor.panels.inspector.imgui.collapsing_header", wraps=original) as headers,
            patch.object(MaskField, "draw", wraps=MaskField("Mask").draw) as masks,
        ):
            self.editor.on_draw()
        masks.assert_not_called()
        groups = [call.args[0].split("##")[0] for call in headers.call_args_list]
        for group in ("Shape", "Material", "Collision Filtering", "Advanced"):
            self.assertIn(group, groups)
        def expand(label, *args, **kwargs):
            opened = original(label, *args, **kwargs)
            return label.startswith("Collision Filtering##") or opened
        with (
            patch("g_editor.panels.inspector.imgui.collapsing_header", side_effect=expand),
            patch.object(MaskField, "draw", return_value=FieldResult(False, 0xFFFFFFFF)) as masks,
        ):
            self.editor.on_draw()
        self.assertEqual(masks.call_count, 2)
        self.assertIsNone(self.editor.state.error)

    def test_collider_filter_controls_follow_body_inheritance_toggle(self) -> None:
        from g_editor.ui import MaskField

        self.editor.state.add_object("empty")
        self.editor.state.add_component("g.collider")
        original = imgui.collapsing_header

        def expand(label, *args, **kwargs):
            opened = original(label, *args, **kwargs)
            return label.startswith("Collision Filtering##") or opened

        def mask_labels():
            def draw():
                imgui.set_next_window_pos(imgui.ImVec2(20, 20))
                imgui.set_next_window_size(imgui.ImVec2(500, 700))
                imgui.begin("Filtering controls test")
                record = self.editor.state.selected
                assert record is not None
                components = record["components"]
                assert isinstance(components, list)
                for index, component in enumerate(components):
                    assert isinstance(component, dict)
                    self.editor._draw_component_inspector(index, component)
                imgui.end()

            with (
                patch.object(self.editor, "_draw_interface", side_effect=draw),
                patch("g_editor.panels.inspector.imgui.collapsing_header", side_effect=expand),
                patch.object(MaskField, "draw", autospec=True, return_value=FieldResult(False, 0xFFFFFFFF)) as masks,
            ):
                self.editor.on_draw()
            return [entry.args[0].identifier for entry in masks.call_args_list]

        self.assertEqual(len(mask_labels()), 2)
        self.editor.state.add_component("g.physics_body")
        self.assertEqual(len(mask_labels()), 2)  # Body controls only.
        self.editor.state.set_component_value(0, "override_collision_filtering", True)
        self.assertEqual(len(mask_labels()), 4)
        self.assertTrue(self.editor.state.undo())
        self.assertEqual(len(mask_labels()), 2)
        self.editor.state.remove_component(1)
        self.assertEqual(len(mask_labels()), 2)  # Standalone collider controls.
        self.assertIsNone(self.editor.state.error)

    def test_hierarchy_game_objects_always_use_cube_icons(self) -> None:
        component_types = (
            "g.rect_renderer", "g.circle_renderer", "g.text_renderer",
            "g.sprite_renderer", "g.area", "g.repeating_image_renderer",
            "g.physics_body", "g.platformer_motor",
        )
        records = [
            {
                "id": f"icon-object-{index}",
                "name": f"Object {index}",
                "type": "g.game_object",
                "components": [{"type": component_type}],
                "children": [],
            }
            for index, component_type in enumerate(component_types)
        ]
        root = {
            "id": "empty-icon-object", "name": "Empty",
            "type": "g.game_object", "components": [], "children": records,
        }

        def draw():
            imgui.begin("Hierarchy icon test")
            self.editor._draw_hierarchy_record(
                root, [str(record["id"]) for record in [root, *records]], [],
            )
            imgui.end()

        with (
            patch.object(self.editor, "_draw_interface", side_effect=draw),
            patch("g_editor.panels.layers.imgui.tree_node_ex", wraps=imgui.tree_node_ex) as nodes,
        ):
            self.editor.on_draw()
        self.assertEqual(nodes.call_count, len(records) + 1)
        for node in nodes.call_args_list:
            self.assertTrue(node.args[0].startswith(f"{icons_fontawesome_6.ICON_FA_CUBE}  "))
        self.assertIsNone(self.editor.state.error)

    def test_component_kind_icons_cover_categories_and_project_scripts(self) -> None:
        from g_editor.panels.inspector import _component_icon
        from g import Component, Script

        expected = {
            "g.rect_renderer": icons_fontawesome_6.ICON_FA_SQUARE,
            "g.circle_renderer": icons_fontawesome_6.ICON_FA_CIRCLE,
            "g.text_renderer": icons_fontawesome_6.ICON_FA_FONT,
            "g.sprite_renderer": icons_fontawesome_6.ICON_FA_IMAGE,
            "g.repeating_image_renderer": icons_fontawesome_6.ICON_FA_IMAGES,
            "g.line_renderer": icons_fontawesome_6.ICON_FA_RULER,
            "g.triangle_renderer": icons_fontawesome_6.ICON_FA_DRAW_POLYGON,
            "g.polygon_renderer": icons_fontawesome_6.ICON_FA_DRAW_POLYGON,
            "g.physics_body": icons_fontawesome_6.ICON_FA_ATOM,
            "g.area": icons_fontawesome_6.ICON_FA_ATOM,
            "g.collider": icons_fontawesome_6.ICON_FA_VECTOR_SQUARE,
            "g.top_down_motor": icons_fontawesome_6.ICON_FA_ARROWS_UP_DOWN_LEFT_RIGHT,
            "g.platformer_motor": icons_fontawesome_6.ICON_FA_ARROWS_UP_DOWN_LEFT_RIGHT,
            "g.camera_follow": icons_fontawesome_6.ICON_FA_VIDEO,
            "g.sprite_animator": icons_fontawesome_6.ICON_FA_PLAY,
        }
        for component_type, icon in expected.items():
            with self.subTest(component_type=component_type):
                self.assertEqual(_component_icon(component_type), icon)
        category_types = (
            "g.sprite_renderer", "g.physics_body", "g.collider",
            "g.top_down_motor", "g.camera_follow", "g.sprite_animator",
        )
        category_icons = {_component_icon(name) for name in category_types}
        self.assertEqual(len(category_icons), len(category_types))

        class Behavior(Script):
            pass

        class CustomComponent(Component):
            pass

        for value_type in (Behavior, CustomComponent):
            value_type.__module__ = "component_icon_test"
            scene_types.register_component(
                f"component_icon_test.{value_type.__name__}", value_type,
                category="Gameplay" if value_type is Behavior else None,
            )
        try:
            script_icon = _component_icon("component_icon_test.Behavior")
            self.assertEqual(script_icon, icons_fontawesome_6.ICON_FA_CODE)
            self.assertNotIn(script_icon, category_icons)
            self.assertEqual(
                _component_icon("component_icon_test.CustomComponent"),
                icons_fontawesome_6.ICON_FA_PUZZLE_PIECE,
            )
        finally:
            scene_types.unregister_module("component_icon_test")

    def test_component_header_orders_collapse_name_enabled_and_kind_actions(self) -> None:
        from g_editor.panels.inspector import icon_button

        record = self.editor.state.add_object("empty")
        self.editor.state.add_component("g.rect_renderer")
        component = record["components"][0]
        header_key = f"{record['id']}:g.rect_renderer"
        controls = []
        interaction = None
        original_button = imgui.button
        original_checkbox = imgui.checkbox

        def button(label, *args, **kwargs):
            clicked = original_button(label, *args, **kwargs)
            controls.append((
                label, imgui.get_item_rect_min().x,
                imgui.get_style().button_text_align.x,
            ))
            if label.endswith("##component-0"):
                return interaction == "header" or clicked
            if label.endswith("##component-actions-0"):
                return interaction == "actions" or clicked
            return clicked

        def checkbox(label, enabled):
            result = original_checkbox(label, enabled)
            controls.append((label, imgui.get_item_rect_min().x, None))
            return (True, False) if interaction == "enabled" else result

        def draw():
            imgui.set_next_window_size(imgui.ImVec2(500, 400))
            imgui.begin("Component header test")
            self.editor._draw_component_inspector(0, component)
            imgui.end()

        with (
            patch.object(self.editor, "_draw_interface", side_effect=draw),
            patch("g_editor.panels.inspector.imgui.button", side_effect=button),
            patch("g_editor.panels.inspector.imgui.checkbox", side_effect=checkbox),
            patch("g_editor.panels.inspector.icon_button", wraps=icon_button) as icons,
            patch.object(self.editor, "_draw_component_properties", return_value=False) as properties,
            patch.object(self.editor, "_draw_component_actions", return_value=False) as actions,
            patch("g_editor.panels.inspector.imgui.open_popup", wraps=imgui.open_popup) as popups,
        ):
            self.editor.on_draw()
            self.assertEqual(len(controls), 3)
            self.assertEqual(
                controls[0][0],
                f"{icons_fontawesome_6.ICON_FA_CHEVRON_DOWN}  Rect Renderer  {icons_fontawesome_6.ICON_FA_SQUARE}##component-0",
            )
            self.assertEqual(controls[0][2], 0)
            self.assertEqual(controls[1][0], "##component-enabled-0")
            self.assertEqual(
                controls[2][0],
                f"{icons_fontawesome_6.ICON_FA_ELLIPSIS}##component-actions-0",
            )
            self.assertLess(controls[0][1], controls[1][1])
            self.assertLess(controls[1][1], controls[2][1])
            self.assertIn("component", icons.call_args.args[2])
            self.assertIn("actions", icons.call_args.args[2])
            properties.assert_called_once()
            popups.assert_not_called()

            interaction = "header"
            properties.reset_mock()
            self.editor.on_draw()
            self.assertFalse(self.editor._component_open[header_key])
            properties.assert_not_called()
            controls.clear()
            self.editor.on_draw()
            self.assertTrue(self.editor._component_open[header_key])
            self.assertTrue(controls[0][0].startswith(icons_fontawesome_6.ICON_FA_CHEVRON_RIGHT))
            properties.assert_called_once()

            interaction = "enabled"
            self.editor.on_draw()
            self.assertFalse(component["properties"]["enabled"])
            self.assertTrue(self.editor.state.undo())

            interaction = "actions"
            self.editor.on_draw()
            popups.assert_called_once_with("Component Actions##component-0")
            self.assertEqual(actions.call_args.args[1:4], (0, "g.rect_renderer", "Rect Renderer"))
            popups.reset_mock()
            interaction = None
            with patch("g_editor.panels.inspector.imgui.is_item_clicked", return_value=True):
                self.editor.on_draw()
            popups.assert_called_once_with("Component Actions##component-0")
        self.assertIsNone(self.editor.state.error)

    def test_component_cards_have_padding_and_subordinate_group_headers(self) -> None:
        self.editor.state.add_object("empty")
        self.editor.state.add_component("g.collider")
        card_styles = []
        group_styles = []
        original_child = imgui.begin_child
        original_header = imgui.collapsing_header

        def child(identifier, size=None, child_flags=0, window_flags=0):
            if identifier.startswith("##component-card-"):
                padding = imgui.get_style().window_padding
                card_styles.append((child_flags, padding.x, padding.y))
            return original_child(identifier, size, child_flags, window_flags)

        def header(label, flags=0):
            if "##component-" in label:
                style = imgui.get_style()
                group_styles.append((style.frame_padding.y, style.color_(imgui.Col_.header).w))
            return original_header(label, flags)

        with (
            patch("g_editor.panels.inspector.imgui.begin_child", side_effect=child),
            patch("g_editor.panels.inspector.imgui.collapsing_header", side_effect=header),
        ):
            self.editor.on_draw()
            self.editor.on_draw()
        self.assertTrue(card_styles)
        for flags, x, y in card_styles:
            self.assertTrue(flags & int(imgui.ChildFlags_.borders))
            self.assertTrue(flags & int(imgui.ChildFlags_.auto_resize_y))
            self.assertEqual((x, y), (10, 10))
        self.assertTrue(group_styles)
        self.assertTrue(all(padding == 2 and alpha == 0 for padding, alpha in group_styles))
        self.assertEqual(imgui.get_style().frame_padding.y, 5)
        self.assertIsNone(self.editor.state.error)

    def test_vec2_field_draws_in_real_imgui_frame(self) -> None:
        from g_editor.ui import Vec2Field
        from g import Vec2

        results = []

        def draw() -> None:
            imgui.begin("Vector field test")
            results.append(Vec2Field("Offset", "test-offset").draw(Vec2(10, -20)))
            imgui.end()

        with patch.object(self.editor, "_draw_interface", side_effect=draw):
            self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].changed)
        self.assertEqual(results[0].value, Vec2(10, -20))

    def _draw_test_json_properties(self, properties):
        results = []

        def draw() -> None:
            imgui.begin("JSON properties test")
            results.append(self.editor._property_widget(
                "Custom Properties", properties, "test-json",
            ))
            imgui.end()

        with patch.object(self.editor, "_draw_interface", side_effect=draw):
            self.editor.on_draw()
        self.assertEqual(len(results), 1)
        return results[0]

    def test_json_properties_draws_scalar_and_nested_values_without_changes(self) -> None:
        properties = {
            "name": "Hero", "count": 3, "speed": 2.5, "active": True,
            "nested": {"a": 1}, "items": [1, 2], "optional": None,
            "offset": {"$vec2": [10, 20]},
        }
        changed, value = self._draw_test_json_properties(properties)
        self.assertIsNone(self.editor.state.error)
        self.assertFalse(changed)
        self.assertEqual(value, properties)

    def test_json_properties_adds_unique_keys_and_preserves_nested_values(self) -> None:
        original = imgui.button

        def add(label, *args, **kwargs):
            clicked = original(label, *args, **kwargs)
            return label == "Add Property##json-add:test-json" or clicked

        properties = {"property": {"nested": [1, True]}, "property2": 4}
        with patch("g_editor.panels.inspector.imgui.button", side_effect=add):
            changed, value = self._draw_test_json_properties(properties)
        self.assertTrue(changed)
        self.assertEqual(value, {**properties, "property3": ""})
        self.assertEqual(properties, {"property": {"nested": [1, True]}, "property2": 4})

    def test_json_properties_renames_keys_and_rejects_duplicate_or_empty_keys(self) -> None:
        properties = {"first": 2, "second": False}
        original = TextField.draw
        for key_name in ("renamed", "second", " "):
            self.editor._json_buffers.clear()

            def rename(field, value):
                if field.identifier == 'json-key:test-json:"first"':
                    return FieldResult(True, key_name)
                return original(field, value)

            with patch.object(TextField, "draw", rename):
                changed, value = self._draw_test_json_properties(properties)
            if key_name == "renamed":
                self.assertTrue(changed)
                self.assertEqual(value, {"renamed": 2, "second": False})
            else:
                self.assertFalse(changed)
                self.assertEqual(value, properties)
                self.assertIsNotNone(self.editor.state.error)
            self.editor.state.error = None

    def test_json_properties_type_selection_preserves_scalar_types(self) -> None:
        from g_editor.ui import ChoiceField

        for index, expected in enumerate(("", 0, 0.0, False)):
            with self.subTest(type=index):
                with patch.object(ChoiceField, "draw", return_value=FieldResult(True, index)):
                    changed, value = self._draw_test_json_properties({"data": [1, 2]})
                self.assertTrue(changed)
                self.assertEqual(value, {"data": expected})
                self.assertIs(type(value["data"]), type(expected))

    def test_json_properties_edits_scalar_values(self) -> None:
        for field, before, after in (
            (TextField, "old", "new"),
            (IntegerField, 1, -3),
            (FloatField, 1.5, -3.25),
            (BooleanField, False, True),
        ):
            original = field.draw

            def edit(control, value):
                if control.identifier == 'json-value:test-json:"data"':
                    return FieldResult(True, after)
                return original(control, value)

            with patch.object(field, "draw", edit):
                changed, value = self._draw_test_json_properties({"data": before})
            self.assertTrue(changed)
            self.assertEqual(value, {"data": after})
            self.assertIs(type(value["data"]), type(after))

    def test_json_properties_removes_only_selected_pair(self) -> None:
        original = imgui.button

        def remove(label, *args, **kwargs):
            clicked = original(label, *args, **kwargs)
            return '##json-remove:test-json:"first"' in label or clicked

        with patch("g_editor.ui.components.imgui.button", side_effect=remove):
            changed, value = self._draw_test_json_properties({"first": 1, "second": [2]})
        self.assertTrue(changed)
        self.assertEqual(value, {"second": [2]})

    def test_object_json_property_actions_persist_and_undo(self) -> None:
        from g import SceneDocument

        record = self.editor.state.add_object("empty")
        identifier = f"object-properties:{record['id']}"
        original = imgui.button

        def add(label, *args, **kwargs):
            clicked = original(label, *args, **kwargs)
            return label == f"Add Property##json-add:{identifier}" or clicked

        with patch("g_editor.panels.inspector.imgui.button", side_effect=add):
            self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)
        self.assertEqual(record["script_properties"], {"property": ""})
        self.assertTrue(self.editor.state.undo())
        self.assertEqual(
            self.editor.state.selected["script_properties"],
            {},
        )
        self.assertTrue(self.editor.state.redo())
        self.assertEqual(
            self.editor.state.selected["script_properties"],
            {"property": ""},
        )
        self.editor.state.set_object_script_properties({
            "name": "Hero", "lives": 3, "speed": 2.5, "active": True,
            "nested": {"items": [1, None]},
        })
        with TemporaryDirectory() as directory:
            filename = Path(directory) / "properties.scene.json"
            self.editor.state.document.write(filename)
            document = SceneDocument.read(filename)
        saved = next(item for item in document.objects if item["id"] == record["id"])
        self.assertEqual(
            saved["script_properties"],
            self.editor.state.selected["script_properties"],
        )
        properties = saved["script_properties"]
        assert isinstance(properties, dict)
        self.assertIs(type(properties["lives"]), int)
        self.assertIs(type(properties["speed"]), float)
        self.assertIs(type(properties["active"]), bool)

    def test_invalid_vec2_property_reports_error(self) -> None:
        for coordinates in ([1], [True, 2], ["x", 2]):
            with self.subTest(coordinates=coordinates):
                changed, _ = self.editor._property_widget(
                    "Offset", {"$vec2": coordinates}, "0:offset",
                )
                self.assertFalse(changed)
                self.assertEqual(self.editor.state.error, "Offset: invalid Vec2 value")

    def test_optional_renderer_color_uses_typed_color_field(self) -> None:
        self.assertTrue(
            self.editor._is_color_property(
                "fill##renderer",
                [255, 255, 255, 255],
            )
        )
        with patch.object(
            OptionalColorField,
            "draw",
            return_value=FieldResult(
                True,
                [0.25, 0.5, 0.75, 1.0],
            ),
        ):
            changed, value = self.editor._property_widget(
                "outline##renderer",
                None,
                "0:outline",
                optional_color_default=[0, 0, 0, 255],
            )

        self.assertTrue(changed)
        self.assertEqual(value, [64, 128, 191, 255])

    def test_asset_picker_rejects_incompatible_file_type(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            text_path = Path(directory) / "notes.txt"
            text_path.write_text("not an image")
            target = _AssetPickerTarget(
                "tileset-path:test",
                "image",
                "image",
            )
            self.editor._asset_picker = target

            with self.assertRaisesRegex(
                ValueError,
                "requires an asset of type image",
            ):
                self.editor._assign_asset_picker(text_path)

            self.assertIs(self.editor._asset_picker, target)

    def test_sprite_menu_adds_empty_sprite_to_hierarchy(self) -> None:
        self.editor._selected_asset = None
        self.editor._add_empty_sprite()
        record = self.editor.state.selected
        assert record is not None
        self.assertEqual(record["type"], "g.game_object")
        self.assertEqual(record["name"], "Sprite")
        layer = self.editor.state.selected_layer
        assert layer is not None
        self.assertIn(record, layer_objects(layer))
        self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)

    def test_asset_picker_button_selects_compatible_asset(self) -> None:
        root = self.editor.state.asset_root
        first = root / "000-picker-source.png"
        second = root / "000-picker-target.png"
        try:
            for path, color in (
                (first, (255, 0, 0, 255)),
                (second, (0, 255, 0, 255)),
            ):
                pyglet.image.SolidColorImagePattern(
                    color
                ).create_image(16, 16).save(str(path))
            self.editor._refresh_assets()
            record = self.editor.state.add_sprite(first)
            self.editor.on_draw()
            self.editor.on_draw()
            left, top, right, bottom = (
                self.editor._asset_field_button_rects[
                    "sprite-image:0"
                ]
            )
            button_x = (left + right) / 2
            button_y = (top + bottom) / 2
            window_y = self.editor.window.height - button_y
            self.editor.on_mouse_press(
                button_x,
                window_y,
                mouse.LEFT,
                0,
            )
            self.editor.on_draw()
            self.assertIsNone(self.editor._asset_picker)
            self.editor.on_mouse_release(
                button_x,
                window_y,
                mouse.LEFT,
                0,
            )
            self.assertIsNotNone(
                self.editor._asset_picker,
                "release did not activate picker mode",
            )
            self.editor.on_draw()

            self.assertIsNotNone(
                self.editor._asset_picker,
                (
                    f"button rect={(left, top, right, bottom)}, "
                    f"mouse={imgui.get_io().mouse_pos}"
                ),
            )
            left, top, right, bottom = (
                self.editor._asset_item_rects[second]
            )
            item_x = (left + right) / 2
            item_y = (top + bottom) / 2
            window_y = self.editor.window.height - item_y
            self.editor.on_mouse_press(
                item_x,
                window_y,
                mouse.LEFT,
                0,
            )
            self.editor.on_draw()
            self.editor.on_mouse_release(
                item_x,
                window_y,
                mouse.LEFT,
                0,
            )
            self.editor.on_draw()

            self.assertIsNone(self.editor._asset_picker)
            renderer = record["components"][0]  # type: ignore[index]
            properties = renderer["properties"]  # type: ignore[index]
            self.assertEqual(
                properties["image"],  # type: ignore[index]
                second.relative_to(root).as_posix(),
            )
        finally:
            for path in (first, second):
                path.unlink(missing_ok=True)
            self.editor._refresh_assets()

    def test_asset_folder_creation_uses_unique_default_names(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            self.editor._selected_asset_directory = Path(directory)

            first = self.editor._create_asset_folder()
            self.editor._selected_asset_directory = Path(directory)
            second = self.editor._create_asset_folder()

            self.assertEqual(first.name, "New Folder")
            self.assertEqual(second.name, "New Folder 2")

    def test_scene_asset_load_uses_document_open_workflow(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            scene_path = Path(directory) / "Level.scene.json"
            self.editor._selected_asset_directory = Path(directory)
            self.editor._create_asset_scene().replace(scene_path)

            self.editor._open_asset(scene_path)

            self.assertEqual(self.editor.state.filename, scene_path.resolve())
            self.assertEqual(
                self.editor.state.status,
                "Opened Level.scene.json",
            )

    def test_scene_asset_load_prompts_before_discarding_changes(
        self,
    ) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            scene_path = Path(directory) / "Level.scene.json"
            self.editor._selected_asset_directory = Path(directory)
            self.editor._create_asset_scene().replace(scene_path)
            self.editor.state.add_object("rectangle")

            self.editor._open_asset(scene_path)

            self.assertEqual(
                self.editor._pending_action,
                "open_selected",
            )
            self.assertEqual(self.editor._path_buffer, str(scene_path))
            self.assertNotEqual(
                self.editor.state.filename,
                scene_path.resolve(),
            )

    def test_non_scene_json_asset_is_not_loaded_as_scene(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            metadata = Path(directory) / "metadata.json"
            metadata.write_text("{}", encoding="utf-8")

            with patch.object(self.editor, "_reveal_asset") as reveal:
                self.editor._open_asset(metadata)

            reveal.assert_called_once_with(metadata)
            self.assertIsNone(self.editor.state.filename)

    def test_asset_folder_creation_can_be_undone_and_redone(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            self.editor._selected_asset_directory = Path(directory)
            created = self.editor._create_asset_folder()

            self.editor._undo()
            self.assertFalse(created.exists())

            self.editor._redo()
            self.assertTrue(created.is_dir())

    def test_asset_rename_updates_open_scene_references(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            image_path = Path(directory) / "hero.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(16, 16).save(str(image_path))
            record = self.editor.state.add_sprite(image_path)

            renamed = self.editor._rename_asset(
                image_path,
                "player.png",
            )

            self.assertTrue(renamed.is_file())
            renderer = record["components"][0]  # type: ignore[index]
            properties = renderer["properties"]  # type: ignore[index]
            self.assertEqual(
                properties["image"],  # type: ignore[index]
                renamed.relative_to(
                    self.editor.state.asset_root
                ).as_posix(),
            )

            self.editor._undo()
            self.assertTrue(image_path.is_file())
            self.assertFalse(renamed.exists())
            restored = self.editor.state.selected
            assert restored is not None
            restored_renderer = restored["components"][0]  # type: ignore[index]
            restored_properties = restored_renderer["properties"]  # type: ignore[index]
            self.assertEqual(
                restored_properties["image"],  # type: ignore[index]
                image_path.relative_to(
                    self.editor.state.asset_root
                ).as_posix(),
            )

            self.editor._redo()
            self.assertFalse(image_path.exists())
            self.assertTrue(renamed.is_file())

    def test_asset_delete_and_duplicate_are_reversible(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            source = Path(directory) / "notes.txt"
            source.write_text("hello", encoding="utf-8")

            duplicate = self.editor._duplicate_asset(source)
            self.assertTrue(duplicate.is_file())
            self.editor._undo()
            self.assertFalse(duplicate.exists())
            self.editor._redo()
            self.assertEqual(
                duplicate.read_text(encoding="utf-8"),
                "hello",
            )

            self.editor._delete_asset(source)
            self.assertFalse(source.exists())
            self.editor._undo()
            self.assertEqual(
                source.read_text(encoding="utf-8"),
                "hello",
            )
            self.editor._redo()
            self.assertFalse(source.exists())

    def test_prefab_asset_overwrite_updates_linked_instances(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            source = self.editor.state.add_object("rectangle")
            source_id = str(source["id"])
            prefab_path = Path(directory) / "Platform.prefab.json"
            self.editor._write_selected_prefab(prefab_path)
            self.assertTrue(prefab_path.is_file())
            linked_source = self.editor.state.selected
            assert linked_source is not None
            source_id = str(linked_source["id"])

            instance = self.editor.state.instantiate_prefab(
                prefab_path,
                position=(300, 250),
            )
            instance_id = str(instance["id"])
            self.editor.state.set_object_value("name", "Local Platform")
            self.editor.state.select(source_id)
            source_components = linked_source["components"]
            assert isinstance(source_components, list)
            source_renderer = source_components[0]
            assert isinstance(source_renderer, dict)
            source_properties = source_renderer["properties"]
            assert isinstance(source_properties, dict)
            source_properties["width"] = 144

            self.editor._write_selected_prefab(prefab_path)
            self.editor.state.select(instance_id)

            refreshed = self.editor.state.selected
            assert refreshed is not None
            self.assertEqual(position(refreshed), (300, 250))
            self.assertEqual(refreshed["name"], "Local Platform")
            refreshed_components = refreshed["components"]
            assert isinstance(refreshed_components, list)
            refreshed_renderer = refreshed_components[0]
            assert isinstance(refreshed_renderer, dict)
            refreshed_properties = refreshed_renderer["properties"]
            assert isinstance(refreshed_properties, dict)
            self.assertEqual(refreshed_properties["width"], 144)

            self.editor._undo()
            restored = next(
                record
                for record in self.editor.state.records
                if record.get("id") == instance_id
            )
            restored_components = restored["components"]
            assert isinstance(restored_components, list)
            restored_renderer = restored_components[0]
            assert isinstance(restored_renderer, dict)
            restored_properties = restored_renderer["properties"]
            assert isinstance(restored_properties, dict)
            self.assertEqual(restored_properties["width"], 64)

    def test_writing_selected_ui_view_creates_linked_prefab(self) -> None:
        with TemporaryDirectory(
            dir=self.editor.state.asset_root
        ) as directory:
            view = self.editor.state.document.ui_views[0]
            self.editor.state.select_layer(str(view["id"]))
            self.editor.state.add_gui_element("button")
            self.editor.state.select_layer(str(view["id"]))
            prefab_path = Path(directory) / "HUD.prefab.json"

            self.editor._write_selected_prefab(prefab_path)

            prefab = PrefabDocument.read(prefab_path)
            self.assertEqual(prefab.kind, "ui_view")
            linked = self.editor.state.selected_ui_view
            assert linked is not None
            metadata = linked["prefab"]
            assert isinstance(metadata, dict)
            expected_source = prefab_path.relative_to(
                self.editor.state.asset_root
            ).as_posix()
            self.assertEqual(metadata["source"], expected_source)
            self.assertEqual(metadata["kind"], "ui_view")
            self.assertTrue(linked["startup"])

    def test_selected_object_can_enter_and_commit_rename(self) -> None:
        record = self.editor.state.add_object("empty")
        self.editor._shortcut_context = "hierarchy"

        self.assertTrue(self.editor._start_selected_rename())
        self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)
        self.editor._rename_buffer = "Renamed Object"
        self.editor._commit_rename()

        self.assertEqual(record["name"], "Renamed Object")

    def test_ui_view_renders_real_elements(self) -> None:
        view_record = self.editor.state.add_ui_view("Pause")
        panel_record = self.editor.state.add_gui_element("panel")
        element = self.editor.state.add_gui_element("button")
        toggle = self.editor.state.add_gui_element("toggle")
        slider = self.editor.state.add_gui_element("slider")
        text_input = self.editor.state.add_gui_element("text_input")

        self.editor.on_draw()
        self.editor.on_draw()

        game = self.editor.preview.game
        assert game is not None
        runtime_view = next(
            view
            for view in game.scenes.current.gui.views
            if view.id == view_record["id"]
        )
        self.assertEqual(len(runtime_view.root.children), 1)
        panel_widget = runtime_view.root.children[0]
        self.assertIsInstance(panel_widget, Panel)
        assert isinstance(panel_widget, Panel)
        self.assertEqual(panel_widget.id, panel_record["id"])
        self.assertEqual(panel_widget.children[0].id, element["id"])
        self.assertEqual(
            [child.id for child in panel_widget.children[1:]],
            [toggle["id"], slider["id"], text_input["id"]],
        )
        self.assertIsInstance(panel_widget.children[1], Toggle)
        self.assertIsInstance(panel_widget.children[2], Slider)
        self.assertIsInstance(panel_widget.children[3], TextInput)
        panel = imgui.internal.find_window_by_name(
            "GUI###LayerContent"
        )
        self.assertIsNotNone(panel)

    def test_play_mode_forwards_text_to_focused_text_input(self) -> None:
        view = self.editor.state.document.ui_views[0]
        self.editor.state.select_layer(str(view["id"]))
        self.editor.state.add_gui_element("text_input")
        self.editor.preview.set_mode("play")
        game = self.editor.preview.game
        assert game is not None
        runtime_view = game.scenes.current.gui.current
        runtime_input = runtime_view.root.children[0]
        self.assertIsInstance(runtime_input, TextInput)
        assert isinstance(runtime_input, TextInput)

        self.editor.on_key_press(key.TAB, 0)
        self.editor.on_text("hello")
        self.editor.on_text_motion(key.MOTION_BACKSPACE)
        self.editor.on_text("!")

        self.assertEqual(runtime_input.text, "hell!")

    def test_gui_element_can_be_selected_and_dragged_in_viewport(self) -> None:
        self.editor.state.add_ui_view("Pause")
        element = self.editor.state.add_gui_element("button")
        self.editor.state.set_snap_enabled(False)
        self.editor.on_draw()
        self.editor.on_draw()
        minimum_x, _, _, maximum_y = self.editor._viewport_rect
        raw_x = element["x"]
        raw_y = element["y"]
        assert isinstance(raw_x, (int, float))
        assert isinstance(raw_y, (int, float))
        io = imgui.get_io()

        display_width = self.editor._viewport_rect[2] - minimum_x
        display_height = maximum_y - self.editor._viewport_rect[1]
        scale_x = display_width / self.editor.render_target.width
        scale_y = display_height / self.editor.render_target.height
        x = (float(raw_x) + 10) * scale_x
        y = (float(raw_y) + 10) * scale_y
        io.add_mouse_pos_event(minimum_x + x, maximum_y - y)
        self.editor.on_draw()
        minimum_x, _, maximum_x, maximum_y = (
            self.editor._viewport_rect
        )
        scale_x = (
            maximum_x - minimum_x
        ) / self.editor.render_target.width
        scale_y = (
            maximum_y - self.editor._viewport_rect[1]
        ) / self.editor.render_target.height
        x = (float(raw_x) + 10) * scale_x
        y = (float(raw_y) + 10) * scale_y
        io.add_mouse_pos_event(minimum_x + x, maximum_y - y)
        io.add_mouse_button_event(0, True)
        self.editor.on_draw()
        io.add_mouse_pos_event(minimum_x + x + 20, maximum_y - y - 10)
        self.editor.on_draw()
        io.add_mouse_button_event(0, False)
        self.editor.on_draw()

        self.assertIs(self.editor.state.selected_gui_element, element)
        self.assertAlmostEqual(
            float(element["x"]),
            310 + 20 / scale_x,
        )
        self.assertAlmostEqual(
            float(element["y"]),
            276 + 10 / scale_y,
        )

    def test_automatic_layout_uses_runtime_bounds_for_picking(self) -> None:
        view_record = self.editor.state.add_ui_view("Menu")
        layout = self.editor.state.add_gui_element("vbox")
        first = self.editor.state.add_gui_element("button")
        second = self.editor.state.add_gui_element("toggle")
        self.editor.on_draw()
        self.editor.on_draw()

        game = self.editor.preview.game
        assert game is not None
        runtime_view = next(
            view
            for view in game.scenes.current.gui.views
            if view.id == view_record["id"]
        )
        runtime_layout = runtime_view.root.children[0]
        self.assertIsInstance(runtime_layout, VBox)
        first_bounds = self.editor.preview.gui_element_bounds(
            str(first["id"])
        )
        second_bounds = self.editor.preview.gui_element_bounds(
            str(second["id"])
        )
        self.assertIsNotNone(first_bounds)
        self.assertIsNotNone(second_bounds)
        assert first_bounds is not None
        assert second_bounds is not None
        self.assertNotEqual(first_bounds[1], second_bounds[1])
        center_x = (first_bounds[0] + first_bounds[2]) / 2
        center_y = (first_bounds[1] + first_bounds[3]) / 2
        self.assertEqual(
            self.editor.preview.pick_gui(
                str(self.editor.state.selected_layer_id),
                center_x,
                center_y,
            ),
            first["id"],
        )
        self.assertEqual(layout["type"], "vbox")


if __name__ == "__main__":
    unittest.main()
