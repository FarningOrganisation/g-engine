from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pyglet
from pyglet import gl
from pyglet.image.buffer import Framebuffer
from pyglet.math import Mat4

from g import (
    Game, GameObject, Image, ObjectLayer, PASSTHROUGH_FRAGMENT_SOURCE,
    PostProcess, RepeatingImageRenderer, SceneSerializer, Vec2, create_background,
)
from g.inspector import inspector_fields
from g.resources import source_of
from g_editor.state import EditorState, layer_objects


RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)
YELLOW = (255, 255, 0, 255)
BLACK = (0, 0, 0, 255)


def pattern_image() -> Image:
    source = pyglet.image.ImageData(2, 2, "RGBA", bytes((*RED, *GREEN, *BLUE, *YELLOW)))
    image = Image._from_source(source)
    texture = source.get_texture()
    gl.glBindTexture(texture.target, texture.id)
    gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
    gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
    return image


class RepeatingImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = pyglet.window.Window(width=32, height=24, visible=False)
        self.addCleanup(self.window.close)
        self.window.switch_to()
        self.game = Game(32, 24)
        self.scene = self.game.scenes.current
        self.texture = pyglet.image.Texture.create(32, 24)
        self.addCleanup(self.texture.delete)
        self.framebuffer = Framebuffer()
        self.addCleanup(self.framebuffer.delete)
        self.framebuffer.attach_texture(self.texture)
        self.addCleanup(self._delete_objects)

    def _delete_objects(self) -> None:
        for layer in self.scene.layers:
            if isinstance(layer, ObjectLayer):
                layer.delete()

    def render(self, width: int = 32, height: int = 24) -> bytes:
        from g.rendering import SceneRenderer

        previous = gl.GLint()
        viewport = (gl.GLint * 4)()
        gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING, previous)
        gl.glGetIntegerv(gl.GL_VIEWPORT, viewport)
        projection, view = self.window.projection, self.window.view
        self.scene.camera.viewport_width = width
        self.scene.camera.viewport_height = height
        try:
            self.framebuffer.bind()
            gl.glViewport(0, 0, 32, 24)
            self.window.projection = Mat4.orthogonal_projection(0, width, 0, height, -1, 1)
            self.window.view = Mat4()
            gl.glClearColor(0, 0, 0, 1)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            with patch.object(self.window, "get_framebuffer_size", return_value=(32, 24)):
                SceneRenderer().render(self.game, self.window)
            return bytes(self.texture.get_image_data().get_data("RGBA", 32 * 4))
        finally:
            self.window.projection, self.window.view = projection, view
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, previous.value)
            gl.glViewport(*viewport)

    @staticmethod
    def pixel(pixels: bytes, x: int, y: int) -> tuple[int, ...]:
        start = (y * 32 + x) * 4
        return tuple(pixels[start:start + 4])

    def test_preset_is_plain_object_without_collider_and_creates_ordered_layer(self) -> None:
        default = self.scene.layers.default
        obj = self.game.add_background(pattern_image())
        self.assertIs(type(obj), GameObject)
        self.assertEqual(len(obj.components), 1)
        self.assertEqual(list(self.scene.layers)[0], obj.layer)
        assert obj.layer is not None
        self.assertEqual(obj.layer.name, "Background")
        self.assertIs(self.scene.layers.default, default)
        self.assertIs(self.scene.add_background().layer, obj.layer)
        custom = self.scene.layers.add("Scenery")
        other = self.game.add_background(layer=custom)
        self.assertIs(other.layer, custom)
        self.assertEqual(list(self.scene.layers)[-1], custom)

    def test_static_background_skips_redundant_uniform_uploads(self) -> None:
        obj = self.game.add_background(pattern_image())
        renderer = obj.require_component(RepeatingImageRenderer)
        renderer.sync()
        program_type = type(renderer._pattern_program)
        uploads = []
        set_uniform = program_type.__setitem__

        def capture(program, name, value):
            uploads.append(name)
            set_uniform(program, name, value)

        with patch.object(program_type, "__setitem__", new=capture):
            renderer.sync()
            self.assertEqual(uploads, [])
            renderer.offset.x += 10
            renderer.sync()
            self.assertEqual(uploads, ["pattern_offset"])
            uploads.clear()
            self.scene.camera.viewport_width += 10
            renderer.sync()
            self.assertEqual(uploads, ["viewport_size"])
        self.assertIs(type(create_background()), GameObject)
        fields = inspector_fields(RepeatingImageRenderer)
        self.assertNotIn("screen_space", fields)
        for name in ("tile_size", "offset", "parallax", "scroll_speed"):
            self.assertIsInstance(fields[name].default, Vec2)

    def test_repeated_pixels_fill_viewport_and_ignore_zoom_rotation(self) -> None:
        obj = self.game.add_background(pattern_image())
        renderer = obj.require_component(RepeatingImageRenderer)
        self.assertEqual(renderer.tile_size, Vec2(2, 2))
        assert obj.layer is not None
        for zoom, rotation in ((1, 0), (2, 37), (0.6, -90)):
            with self.subTest(zoom=zoom, rotation=rotation):
                self.scene.camera.zoom = zoom
                self.scene.camera.rotation = rotation
                pixels = self.render()
                for y in range(24):
                    for x in range(32):
                        self.assertEqual(self.pixel(pixels, x, y), ((RED, GREEN), (BLUE, YELLOW))[y % 2][x % 2])
        self.assertIs(renderer.drawable.batch, obj.layer._batch)
        self.assertIsNot(renderer.drawable.batch, obj.layer._ui_batch)

    def test_conflicting_nonobject_layer_name_uses_and_reuses_unique_layer(self) -> None:
        self.scene.layers.add_tile("Background")
        first = self.game.add_background()
        second = self.game.add_background()
        assert first.layer is not None
        self.assertEqual(first.layer.name, "Background 2")
        self.assertIs(first.layer, second.layer)
        self.assertEqual(list(self.scene.layers)[0], first.layer)

    def test_draw_time_parallax_offsets_and_negative_camera_positions(self) -> None:
        renderer = self.game.add_background(pattern_image(), parallax=(0.5, 1)).require_component(RepeatingImageRenderer)
        for camera_x, camera_y, offset in ((18, 12, (0, 0)), (14, 11, (0, 0)), (-2, -3, (-1, 2))):
            with self.subTest(camera=(camera_x, camera_y), offset=offset):
                self.scene.camera.position = (camera_x, camera_y)
                renderer.offset = Vec2(*offset)
                expected = Vec2(offset[0] - (camera_x - 16) * 0.5, offset[1] - (camera_y - 12))
                self.assertEqual(renderer.pattern_offset, expected)
                pixels = self.render()
                for y in range(24):
                    for x in range(32):
                        self.assertEqual(self.pixel(pixels, x, y), ((RED, GREEN), (BLUE, YELLOW))[int(y - expected.y) % 2][int(x - expected.x) % 2])

    def test_nonrepeated_axes_and_independent_background_uniforms(self) -> None:
        first = self.game.add_background(
            Image.solid(1, 1, RED), tile_size=(8, 6), repeat_x=False, repeat_y=False,
        )
        second = self.game.add_background(
            Image.solid(1, 1, GREEN), tile_size=(5, 4), offset=(12, 10),
            repeat_x=False, repeat_y=False, z_index=1,
        )
        self.assertIsNot(first.renderers[0].drawable.program, second.renderers[0].drawable.program)
        pixels = self.render()
        for y in range(24):
            for x in range(32):
                expected = RED if x < 8 and y < 6 else GREEN if 12 <= x < 17 and 10 <= y < 14 else BLACK
                self.assertEqual(self.pixel(pixels, x, y), expected)
        second.require_component(RepeatingImageRenderer).repeat_x = True
        pixels = self.render()
        self.assertEqual(self.pixel(pixels, 31, 11), GREEN)
        self.assertEqual(self.pixel(pixels, 31, 9), BLACK)

    def test_background_respects_layer_order_and_screen_space_ui_is_still_last(self) -> None:
        background = self.game.add_background(Image.solid(1, 1, RED))
        self.game.add_rect(0, 0, GREEN, 32, 24)
        ui = self.game.add_rect(4, 4, BLUE, 4, 4)
        ui.renderers[0].screen_space = True
        pixels = self.render()
        self.assertEqual(self.pixel(pixels, 0, 0), GREEN)
        self.assertEqual(self.pixel(pixels, 5, 5), BLUE)
        assert background.layer is not None
        self.scene.layers.move(background.layer, 1)
        pixels = self.render()
        self.assertEqual(self.pixel(pixels, 0, 0), RED)
        self.assertEqual(self.pixel(pixels, 5, 5), BLUE)

    def test_scrolling_visibility_disable_and_detachment(self) -> None:
        obj = self.game.add_background(pattern_image(), scroll_speed=(2, -4))
        renderer = obj.require_component(RepeatingImageRenderer)
        assert obj.layer is not None
        self.game._update(0.5)
        self.assertEqual(renderer.pattern_offset, Vec2(1, -2))
        for target in (renderer, obj, obj.layer):
            with self.subTest(target=type(target).__name__):
                target.enabled = False
                self.game._update(1)
                self.assertEqual(renderer.pattern_offset, Vec2(1, -2))
                if target is not obj.layer:
                    self.assertEqual(self.pixel(self.render(), 0, 0), BLACK)
                target.enabled = True
                target.visible = False
                self.assertEqual(self.pixel(self.render(), 0, 0), BLACK)
                target.visible = True
        obj.position = (3, 4)
        self.assertEqual(renderer.pattern_offset, Vec2(4, 2))
        renderer.image = None
        self.assertEqual(self.pixel(self.render(), 0, 0), BLACK)
        renderer.image = pattern_image()
        self.render()
        obj.remove_component(renderer)
        self.assertIsNot(renderer.drawable.batch, obj.layer._batch)
        self.assertEqual(self.pixel(self.render(), 0, 0), BLACK)
        obj.add_component(renderer)
        self.assertNotEqual(self.pixel(self.render(), 0, 0), BLACK)

    def test_atlas_regions_do_not_bleed_or_mutate_shared_image_anchors(self) -> None:
        pixels = bytearray(RED * 64)
        for y in range(2, 6):
            for x in range(2, 6):
                start = (y * 8 + x) * 4
                pixels[start:start + 4] = bytes((255, 255, 255, 255))
        atlas = pyglet.image.ImageData(8, 8, "RGBA", bytes(pixels)).get_texture()
        region = atlas.get_region(2, 2, 4, 4)
        region.anchor_x, region.anchor_y = 2, 3
        image = Image._from_source(region)
        self.game.add_background(image, tile_size=(7.3, 6.7), offset=(-0.2, 0.3))
        gl.glBindTexture(atlas.target, atlas.id)
        gl.glTexParameteri(atlas.target, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        rendered = self.render()
        self.assertEqual((region.anchor_x, region.anchor_y), (2, 3))
        for y in range(24):
            for x in range(32):
                self.assertEqual(self.pixel(rendered, x, y), (255, 255, 255, 255))

    def test_logical_viewport_and_postprocessing(self) -> None:
        self.game.add_background(pattern_image(), tile_size=(2, 2))
        pixels = self.render(16, 12)
        for y in range(24):
            for x in range(32):
                self.assertEqual(self.pixel(pixels, x, y), ((RED, GREEN), (BLUE, YELLOW))[(y // 2) % 2][(x // 2) % 2])
        effect = PostProcess(PASSTHROUGH_FRAGMENT_SOURCE)
        self.addCleanup(effect.delete)
        self.scene.post_process = effect
        self.assertEqual(self.render(16, 12), pixels)

    def test_invalid_settings_fail_explicitly(self) -> None:
        for value in ((0, 1), (1, -1), (float("inf"), 1), (1, float("nan"))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RepeatingImageRenderer(tile_size=value)
        with self.assertRaises(ValueError):
            RepeatingImageRenderer(offset=(float("nan"), 0))
        with self.assertRaises(ValueError):
            RepeatingImageRenderer(opacity=300)
        renderer = self.game.add_background().require_component(RepeatingImageRenderer)
        renderer.tile_size = Vec2(0, 1)
        with self.assertRaises(ValueError):
            renderer.sync()
        with self.assertRaises(ValueError):
            renderer.screen_space = False
        with self.assertRaises(TypeError):
            renderer.image = "not an image"  # type: ignore[assignment]

    def test_scene_persistence_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source_of(pattern_image()).save(str(root / "sky.png"))
            obj = self.game.add_background(
                Image(root / "sky.png"), tile_size=(7, 9), offset=(-2, 3),
                parallax=(0.2, 0.6), scroll_speed=(10, -5), repeat_y=False, opacity=123,
            )
            obj.position = (4, 5)
            serializer = SceneSerializer()
            document = serializer.capture(self.scene, asset_root=root)
            objects = layer_objects(document.layers[0])
            components = objects[0]["components"]
            assert isinstance(components, list) and isinstance(components[0], dict)
            component = components[0]
            properties = component["properties"]
            assert isinstance(properties, dict)
            self.assertEqual(properties["image"], "sky.png")
            self.assertEqual(properties["parallax"], {"$vec2": [0.2, 0.6]})
            self.assertNotIn("screen_space", properties)
            restored = serializer.restore(self.game, document, asset_root=root)
            layer = restored.layers.resolve_object("Background")
            self.addCleanup(layer.delete)
            renderer = layer.objects[0].require_component(RepeatingImageRenderer)
            self.assertEqual(renderer.tile_size, Vec2(7, 9))
            self.assertEqual(renderer.offset, Vec2(-2, 3))
            self.assertEqual(renderer.parallax, Vec2(0.2, 0.6))
            self.assertEqual(renderer.scroll_speed, Vec2(10, -5))
            self.assertEqual(renderer.pattern_offset, Vec2(2, 8))
            self.assertFalse(renderer.repeat_y)
            self.assertEqual(renderer.opacity, 123)


class BackgroundEditorTests(unittest.TestCase):
    def test_conflicting_tile_layer_name_creates_reusable_background_layer(self) -> None:
        state = EditorState()
        state.add_tile_layer("Background")
        first = state.add_background()
        layer = state.selected_layer
        assert layer is not None
        self.assertEqual(layer["name"], "Background 2")
        second = state.add_background()
        self.assertIs(state.selected_layer, layer)
        self.assertEqual(layer_objects(layer), [first, second])

    def test_creation_selection_single_undo_and_existing_layer_reuse(self) -> None:
        state = EditorState()
        initial = deepcopy(state.document.layers)
        state.add_object("rectangle")
        gameplay_layer = state.selected_layer
        assert gameplay_layer is not None
        obj = state.add_background()
        self.assertIs(state.selected, obj)
        self.assertEqual(state.document.layers[0]["name"], "Background")
        self.assertIs(state.selected_layer, state.document.layers[0])
        components = obj["components"]
        assert isinstance(components, list)
        self.assertEqual(len(components), 1)
        self.assertEqual(len(state.document.layers), len(initial) + 1)
        self.assertTrue(state.undo())
        self.assertEqual(len(state.document.layers), len(initial))
        self.assertTrue(state.redo())
        background_layer = state.document.layers[0]
        state.select(None)
        state.selected_layer_id = str(gameplay_layer["id"])
        state.add_background()
        self.assertIs(state.selected_layer, background_layer)
        self.assertEqual(len(layer_objects(background_layer)), 2)
        self.assertEqual(len(state.document.layers), len(initial) + 1)

    def test_asset_assignment_clear_rename_and_component_reset(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source_of(Image.solid(13, 7, RED)).save(str(root / "sky.png"))
            state = EditorState(asset_root=root)
            state.add_background()
            state.set_selected_background_image(root / "sky.png", component_index=0)
            properties = state._selected_background_properties(0)
            self.assertEqual(properties["image"], "sky.png")
            self.assertEqual(properties["tile_size"], {"$vec2": [13, 7]})
            properties["tile_size"] = {"$vec2": [50, 20]}
            state.set_selected_background_image(root / "sky.png", component_index=0)
            self.assertEqual(properties["tile_size"], {"$vec2": [50, 20]})
            self.assertEqual(state.rename_asset_references("sky.png", "renamed.png"), 1)
            self.assertEqual(properties["image"], "renamed.png")
            self.assertTrue(state.clear_selected_background_image(component_index=0))
            self.assertFalse(state.clear_selected_background_image(component_index=0))
            self.assertTrue(state.undo())
            self.assertEqual(state._selected_background_properties(0)["image"], "renamed.png")
            state.reset_component(0)
            self.assertIsNone(state._selected_background_properties(0)["image"])
            self.assertEqual(state._selected_background_properties(0)["tile_size"], {"$vec2": [64, 64]})
            with self.assertRaises(ValueError):
                state.set_selected_background_image(root / "sky.png", component_index=1)

    def test_inspector_asset_picker_vector_fields_and_paused_camera(self) -> None:
        from imgui_bundle import imgui
        from g_editor.application import ImGuiEditor, _AssetPickerTarget, _RenderWindow
        from g_editor.ui import Vec2Field

        editor = ImGuiEditor(visible=False)
        self.addCleanup(editor._close)
        editor.window.switch_to()
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source_of(Image.solid(8, 6, RED)).save(str(root / "sky.png"))
            editor.state.asset_root = root
            record = editor.state.add_background()
            target = _AssetPickerTarget("background-image:0", "Image", "image")
            editor._assign_asset(target, root / "sky.png")
            self.assertEqual(editor.state._selected_background_properties(0)["image"], "sky.png")
            properties = editor.state._selected_background_properties(0)
            properties["parallax"] = {"$vec2": [0.5, 0.25]}
            properties["scroll_speed"] = {"$vec2": [4, 8]}
            editor.preview.reload()
            self.assertIsNone(editor.preview.load_error)
            game = editor.preview.game
            self.assertIsNotNone(game)
            assert game is not None
            layer = game.scenes.current.layers.resolve_object("Background")
            renderer = layer.objects[0].require_component(RepeatingImageRenderer)
            editor.preview.editor_camera = [320.0, 180.0]
            render_window = _RenderWindow(editor.window, 640, 360)
            editor.preview.render(render_window)
            first_offset = renderer.pattern_offset
            editor.preview.editor_camera = [340.0, 160.0]
            editor.preview.render(render_window)
            second_offset = tuple(renderer.drawable.program["pattern_offset"])
            self.assertEqual(second_offset, (first_offset.x - 10, first_offset.y + 5))
            self.assertEqual(renderer._scroll_offset, Vec2())
            with patch.object(editor, "_draw_asset_reference_field") as asset_field:
                editor._draw_sprite_renderer_resource(properties, 0, background=True)
            self.assertEqual(asset_field.call_args.args[2], "background-image:0")
            fields = []
            original = Vec2Field.draw

            def capture(field: Vec2Field, value: Vec2):
                fields.append(field.label)
                return original(field, value)

            imgui.new_frame()
            imgui.set_next_window_size(imgui.ImVec2(800, 900))
            imgui.begin("Background fields")
            try:
                components = record["components"]
                assert isinstance(components, list) and isinstance(components[0], dict)
                with patch.object(Vec2Field, "draw", capture):
                    editor._draw_component_inspector(0, components[0])
            finally:
                imgui.end()
                imgui.render()
            self.assertTrue({"Tile Size", "Offset", "Parallax", "Scroll Speed"}.issubset(fields), fields)
            editor._clear_asset(target)
            self.assertIsNone(editor.state._selected_background_properties(0)["image"])
            editor.state.dirty = False

    def test_background_shortcut_in_all_object_add_menus(self) -> None:
        from imgui_bundle import imgui
        from g_editor.application import ImGuiEditor

        editor = ImGuiEditor(visible=False)
        self.addCleanup(editor._close)
        editor.window.switch_to()
        imgui.new_frame()
        imgui.begin("Background menus")
        try:
            with (
                patch.object(imgui, "begin_popup", return_value=True),
                patch.object(imgui, "begin_popup_context_item", return_value=True),
                patch.object(imgui, "begin_menu", return_value=True),
                patch.object(imgui, "end_popup"),
                patch.object(imgui, "end_menu"),
                patch.object(imgui, "is_item_clicked", return_value=False),
                patch.object(imgui, "menu_item", side_effect=lambda label, *_args: (label == "Background", False)),
                patch.object(editor.state, "add_background", wraps=editor.state.add_background) as add,
            ):
                editor._draw_add_object_popup()
                editor._draw_hierarchy_context_menu("object", None)
                editor._draw_viewport_context_menu(imgui.ImVec2(0, 0))
            self.assertEqual(add.call_count, 3)
            layer = editor.state.selected_layer
            assert layer is not None
            self.assertEqual(len(layer_objects(layer)), 3)
        finally:
            imgui.end()
            imgui.render()
            editor.state.dirty = False
