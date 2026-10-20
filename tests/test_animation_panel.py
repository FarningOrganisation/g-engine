import unittest
import sys
import gc
import weakref
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import MagicMock, patch

import pyglet
from pyglet import gl
from pyglet.window import key
from imgui_bundle import icons_fontawesome_6, imgui

from g_editor.application import ImGuiEditor
from g_editor.panels.animation import AnimationPreview
from g_editor.ui import icon_button
from g import Animation, SceneSerializer, SpriteAnimation, SpriteAnimationFrame, SpriteAnimator, SpriteRenderer
from g.persistence import JsonValue, SceneFormatError
from g.resources import source_of


GIF = bytes.fromhex(
    "47494638396101000100800000ffffff ff0000"
    "21f904000a0000002c0000000001000100000202440100"
    "21f904000a0000002c00000000010001000002024c01003b"
)


class AnimationPreviewTests(unittest.TestCase):
    def test_preview_loops_and_stops_without_a_game(self) -> None:
        frame = SpriteAnimationFrame(None, 0.1)
        preview = AnimationPreview(SpriteAnimation((frame, frame)), playing=True)
        preview.update(0.15)
        self.assertEqual(preview.frame_index, 1)
        preview.update(0.1)
        self.assertEqual(preview.frame_index, 0)
        preview.stop()
        self.assertEqual(preview.elapsed, 0)
        self.assertFalse(preview.playing)
        preview.clip = SpriteAnimation((frame, frame), loop=False)
        preview.playing = True
        preview.update(1)
        self.assertEqual(preview.frame_index, 1)
        self.assertFalse(preview.playing)
        with self.assertRaises(ValueError):
            preview.update(float("nan"))


class AnimationPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.images = tuple(self.root / f"frame-{index}.png" for index in range(3))
        for index, path in enumerate(self.images):
            pyglet.image.SolidColorImagePattern((index * 80, 100, 255, 255)).create_image(16, 16).save(str(path))
        self.gif = self.root / "animation.gif"
        self.gif.write_bytes(GIF)
        self.editor = ImGuiEditor(asset_root=self.root, visible=False)
        self.record = self.editor.state.add_object("empty")
        self.editor.state.add_component("g.sprite_animator")
        self.editor.on_draw()

    def tearDown(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()
        self.directory.cleanup()

    def selected(self) -> tuple[int, dict[str, JsonValue], list[dict[str, JsonValue]]]:
        selected = self.editor._sync_animation_selection()
        assert selected is not None
        return selected

    def clip(self) -> SpriteAnimation:
        clip = self.editor._animation_preview.clip
        assert clip is not None
        return clip

    def frames(self, states: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
        frames = states[0]["frames"]
        assert isinstance(frames, list)
        assert all(isinstance(frame, dict) for frame in frames)
        return cast(list[dict[str, JsonValue]], frames)

    def draw(self) -> None:
        self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)
        self.assertIsNone(self.editor.preview.load_error)

    def test_multiple_image_drop_creates_ordered_frames_and_supports_undo(self) -> None:
        before = deepcopy(self.editor.state.document)
        self.editor._drop_animation_assets(self.images)
        self.draw()
        _, _, states = self.selected()
        self.assertEqual(states[0]["frames"], [
            {"image": path.name, "duration": 0.1} for path in self.images
        ])
        runtime = self.editor.preview.selected_runtime_object()
        assert runtime is not None
        animator = runtime.require_component(SpriteAnimator)
        self.editor._animation_preview.playing = True
        self.editor._animation_preview.update(0.15)
        self.assertEqual(self.editor._animation_preview.frame_index, 1)
        self.assertEqual(animator.frame_index, 0)
        self.assertTrue(self.editor.state.undo())
        self.assertEqual(self.editor.state.document, before)

    def test_state_add_and_selection_are_separate_from_runtime_default(self) -> None:
        index, properties, states = self.selected()
        self.editor._add_animation_state(index, properties, states)
        self.draw()
        _, properties, states = self.selected()
        self.assertEqual([state["name"] for state in states], ["default", "animation"])
        self.assertEqual(self.editor._animation_state_name, "animation")
        self.assertEqual(properties["initial_state"], "default")
        other = self.editor.state.add_object("empty")
        self.draw()
        self.assertIsNone(self.editor._animation_preview.clip)
        self.editor.state.select(str(self.record["id"]))
        self.draw()
        self.assertEqual(self.editor._animation_state_name, "default")
        self.assertNotEqual(other["id"], self.record["id"])

    def test_state_name_is_editable_without_expanding_settings(self) -> None:
        self.draw()
        input_text = imgui.input_text

        def rename(label, value, *args):
            if label == "##animation-name":
                return True, "idle"
            return input_text(label, value, *args)

        with patch("g_editor.panels.animation.imgui.input_text", side_effect=rename):
            self.draw()
        _, properties, states = self.selected()
        self.assertEqual(states[0]["name"], "idle")
        self.assertEqual(properties["initial_state"], "idle")
        self.assertEqual(self.editor._animation_state_name, "idle")
        self.assertTrue(self.editor.state.undo())
        self.draw()
        _, properties, states = self.selected()
        self.assertEqual(states[0]["name"], "default")
        self.assertEqual(properties["initial_state"], "default")

    def test_empty_animation_drop_hint_is_inside_timeline(self) -> None:
        self.draw()
        get_draw_list = imgui.get_window_draw_list
        hints = []

        def draw_list():
            original = get_draw_list()
            proxy = MagicMock(wraps=original)

            def add_text(position, color, text, *args):
                if text == "Drop images or a GIF here":
                    window = imgui.internal.get_current_window()
                    hints.append((position, window.name, window.inner_clip_rect))
                return original.add_text(position, color, text, *args)

            proxy.add_text.side_effect = add_text
            return proxy

        with patch("g_editor.panels.animation.imgui.get_window_draw_list", side_effect=draw_list):
            self.draw()
        self.assertEqual(len(hints), 1)
        position, window_name, rectangle = hints[0]
        self.assertIn("##animation-timeline", window_name)
        self.assertGreaterEqual(position.x, rectangle.min.x)
        self.assertLess(position.x, rectangle.max.x)
        self.assertGreaterEqual(position.y, rectangle.min.y)
        self.assertLess(position.y, rectangle.max.y)

    def test_state_list_focus_routes_native_rename_shortcuts(self) -> None:
        rectangles = []
        selectable = imgui.selectable

        def row(label, *args):
            result = selectable(label, *args)
            if "##animation-state-default" in label:
                rectangles.append((imgui.get_item_rect_min(), imgui.get_item_rect_max()))
            return result

        with patch("g_editor.panels.animation.imgui.selectable", side_effect=row):
            for _ in range(3):
                self.draw()
        minimum, maximum = rectangles[-1]
        io = imgui.get_io()
        io.add_mouse_pos_event((minimum.x + maximum.x) / 2, (minimum.y + maximum.y) / 2)
        io.add_mouse_button_event(0, True)
        self.draw()
        io.add_mouse_button_event(0, False)
        self.draw()
        self.assertEqual(self.editor._shortcut_context, "animation-states")
        for platform_name, symbol in (("darwin", key.ENTER), ("win32", key.F2), ("linux", key.F2)):
            with self.subTest(platform=platform_name):
                with patch("g_editor.application.sys.platform", platform_name):
                    self.editor.on_key_press(symbol, 0)
                self.assertTrue(self.editor._is_renaming("animation-state", "default"))
                self.editor.on_key_press(key.ESCAPE, 0)
                self.assertIsNone(self.editor._rename_target)
        object_name = self.record["name"]
        self.editor.on_key_press(key.F2 if sys.platform != "darwin" else key.ENTER, 0)
        self.editor._rename_buffer = "idle"
        self.editor.on_key_press(key.ENTER, 0)
        self.draw()
        _, properties, states = self.selected()
        self.assertEqual(states[0]["name"], "idle")
        self.assertEqual(properties["initial_state"], "idle")
        self.assertEqual(self.record["name"], object_name)
        self.assertTrue(self.editor.state.undo())
        self.draw()
        self.assertEqual(self.selected()[2][0]["name"], "default")

    def test_state_deletion_shortcuts_preserve_object_and_last_state(self) -> None:
        index, properties, states = self.selected()
        self.editor._add_animation_state(index, properties, states)
        self.draw()
        self.editor._animation_state_name = "default"
        self.editor._shortcut_context = "animation-states"
        self.editor.on_key_press(key.BACKSPACE, key.MOD_COMMAND)
        self.draw()
        _, properties, states = self.selected()
        self.assertEqual([state["name"] for state in states], ["animation"])
        self.assertEqual(properties["initial_state"], "animation")
        self.assertIs(self.editor.state.selected, self.record)
        self.assertTrue(self.editor.state.undo())
        self.draw()
        self.assertEqual(len(self.selected()[2]), 2)
        self.editor._shortcut_context = "animation-states"
        self.editor.on_key_press(key.DELETE, 0)
        self.draw()
        self.editor._shortcut_context = "animation-states"
        self.editor.on_key_press(key.DELETE, 0)
        self.assertIn("at least one state", self.editor.state.error or "")
        self.assertEqual(len(self.selected()[2]), 1)
        selected = self.editor.state.selected
        assert selected is not None
        self.assertEqual(selected["id"], self.record["id"])

    def test_state_shortcuts_do_not_edit_in_play_or_text_fields(self) -> None:
        before = deepcopy(self.editor.state.document)
        self.editor._shortcut_context = "animation-states"
        imgui.get_io().want_text_input = True
        self.editor.on_key_press(key.DELETE, 0)
        self.assertEqual(self.editor.state.document, before)
        imgui.get_io().want_text_input = False
        self.editor.preview.mode = "play"
        with patch("g_editor.application.sys.platform", "darwin"):
            self.editor.on_key_press(key.ENTER, 0)
        self.editor.on_key_press(key.BACKSPACE, key.MOD_COMMAND)
        self.assertIsNone(self.editor._rename_target)
        self.assertEqual(self.editor.state.document, before)
        self.editor.preview.mode = "edit"

    def test_state_context_menu_sets_default_and_deletes_clicked_state(self) -> None:
        index, properties, states = self.selected()
        self.editor._add_animation_state(index, properties, states)
        self.draw()
        self.editor._animation_state_name = "default"
        action = "Set default"

        def context(identifier):
            if identifier != "##animation-state-context-animation":
                return False
            imgui.open_popup(identifier)
            return imgui.begin_popup(identifier)

        def menu(label, shortcut, selected, enabled):
            return label == action and enabled, selected

        with (
            patch("g_editor.panels.animation.imgui.begin_popup_context_item", side_effect=context),
            patch("g_editor.panels.animation.imgui.menu_item", side_effect=menu),
        ):
            self.draw()
            self.assertEqual(self.selected()[1]["initial_state"], "animation")
            self.assertEqual(self.editor._animation_state_name, "animation")
            action = "Delete"
            self.draw()
        _, properties, states = self.selected()
        self.assertEqual([state["name"] for state in states], ["default"])
        self.assertEqual(properties["initial_state"], "default")
        self.assertTrue(self.editor.state.undo())
        self.draw()
        self.assertEqual(self.selected()[1]["initial_state"], "animation")

    def test_state_context_menu_disables_last_state_deletion_and_play_edits(self) -> None:
        enabled_actions = {}

        def context(identifier):
            if not identifier.startswith("##animation-state-context-"):
                return False
            imgui.open_popup(identifier)
            return imgui.begin_popup(identifier)

        def menu(label, shortcut, selected, enabled):
            enabled_actions[label] = enabled
            return False, selected

        with (
            patch("g_editor.panels.animation.imgui.begin_popup_context_item", side_effect=context),
            patch("g_editor.panels.animation.imgui.menu_item", side_effect=menu),
        ):
            self.draw()
            self.assertEqual(enabled_actions, {"Set default": False, "Delete": False})
            index, properties, states = self.selected()
            self.editor._add_animation_state(index, properties, states)
            self.editor.preview.mode = "play"
            self.draw()
            self.assertEqual(enabled_actions, {"Set default": False, "Delete": False})
        self.editor.preview.mode = "edit"

    def test_preview_remains_visible_with_many_states(self) -> None:
        index, properties, states = self.selected()
        for number in range(12):
            states.append({"name": f"extra-{number}", "image": None, "loop": True})
        self.editor.state.set_sprite_animator_configuration(index, cast(JsonValue, states))
        self.editor._drop_animation_assets(self.images)
        for _ in range(3):
            self.draw()
        rectangles = []
        image = imgui.image

        def render_image(texture, size, *args):
            image(texture, size, *args)
            window = imgui.internal.get_current_window()
            if "##animation-preview" in window.name:
                rectangles.append((imgui.get_item_rect_min(), imgui.get_item_rect_max(), window.inner_clip_rect))

        with patch("g_editor.panels.animation.imgui.image", side_effect=render_image):
            self.draw()
        self.assertEqual(len(rectangles), 1)
        minimum, maximum, clip = rectangles[0]
        self.assertGreaterEqual(minimum.y, clip.min.y)
        self.assertLessEqual(maximum.y, clip.max.y)
        self.assertGreater(maximum.y - minimum.y, 0)
        self.assertLessEqual(maximum.y - minimum.y, 90)
        preview = imgui.get_current_context().windows
        detail_windows = [window for window in preview if "##animation-frame-details" in window.name]
        self.assertEqual(len(detail_windows), 1)
        details = detail_windows[0]
        self.assertGreaterEqual(minimum.x, details.pos.x + details.size.x)

    def test_preview_icon_buttons_play_pause_and_stop(self) -> None:
        self.editor._drop_animation_assets(self.images)
        self.draw()
        buttons = []
        clicked = "animation-playback"

        def button(icon, identifier, tooltip, **kwargs):
            buttons.append((icon, identifier, tooltip))
            icon_button(icon, identifier, tooltip, **kwargs)
            return identifier == clicked

        with patch("g_editor.panels.animation.icon_button", side_effect=button):
            self.draw()
            self.assertTrue(self.editor._animation_preview.playing)
            self.assertIn((icons_fontawesome_6.ICON_FA_PLAY, "animation-playback", "Play preview"), buttons)
            buttons.clear()
            self.draw()
            self.assertFalse(self.editor._animation_preview.playing)
            self.assertIn((icons_fontawesome_6.ICON_FA_PAUSE, "animation-playback", "Pause preview"), buttons)
            clicked = "animation-stop"
            self.editor._animation_preview.elapsed = 0.15
            self.editor._animation_preview.playing = True
            self.draw()
            self.assertFalse(self.editor._animation_preview.playing)
            self.assertEqual(self.editor._animation_preview.elapsed, 0)
            self.assertIn((icons_fontawesome_6.ICON_FA_STOP, "animation-stop", "Stop preview"), buttons)

    def test_preview_play_pause_and_stop_are_mirrored_only_during_viewport_render(self) -> None:
        self.editor._drop_animation_assets(self.images)
        self.draw()
        runtime = self.editor.preview.selected_runtime_object()
        assert runtime is not None
        renderer = runtime.require_component(SpriteRenderer)
        animator = runtime.require_component(SpriteAnimator)
        original = renderer.image
        document = deepcopy(self.editor.state.document)
        revision = self.editor.state.revision
        clicked = "animation-playback"
        render = self.editor.render_target.render
        rendered = []

        def button(icon, identifier, tooltip, **kwargs):
            icon_button(icon, identifier, tooltip, **kwargs)
            return identifier == clicked

        def viewport(preview):
            rendered.append(renderer.image)
            render(preview)

        with (
            patch("g_editor.panels.animation.icon_button", side_effect=button),
            patch.object(self.editor.render_target, "render", side_effect=viewport),
        ):
            self.draw()
            self.assertIs(rendered[-1], self.clip().frames[0].image)
            self.assertIs(renderer.image, original)
            self.editor._animation_preview.update(0.15)
            self.draw()
            self.assertFalse(self.editor._animation_preview.playing)
            self.assertIs(rendered[-1], self.clip().frames[1].image)
            self.assertIs(renderer.image, original)
            clicked = "animation-stop"
            self.draw()
            self.assertIs(rendered[-1], original)
        self.assertEqual(animator.state, "default")
        self.assertEqual(animator.frame_index, 0)
        self.assertEqual(self.editor.state.document, document)
        self.assertEqual(self.editor.state.revision, revision)

    def test_selecting_another_state_continues_viewport_preview_and_stop_restores(self) -> None:
        self.editor._drop_animation_assets(self.images)
        index, properties, states = self.selected()
        self.editor._add_animation_state(index, properties, states)
        self.editor._drop_animation_assets((self.images[2],))
        self.draw()
        self.editor._animation_preview.playing = True
        self.editor._animation_viewport_active = True
        selectable = imgui.selectable

        def select(label, *args):
            result = selectable(label, *args)
            return (True, True) if "##animation-state-default" in label else result

        with patch("g_editor.panels.animation.imgui.selectable", side_effect=select):
            self.draw()
        self.assertEqual(self.editor._animation_state_name, "default")
        self.assertTrue(self.editor._animation_preview.playing)
        self.assertTrue(self.editor._animation_viewport_active)
        runtime = self.editor.preview.selected_runtime_object()
        assert runtime is not None
        renderer = runtime.require_component(SpriteRenderer)
        original = renderer.image
        with self.assertRaisesRegex(RuntimeError, "render failed"):
            with self.editor._animation_viewport_override():
                self.assertIs(renderer.image, self.clip().frames[0].image)
                raise RuntimeError("render failed")
        self.assertIs(renderer.image, original)
        self.editor.state.add_object("empty")
        self.draw()
        self.assertFalse(self.editor._animation_viewport_active)

    def test_viewport_preview_does_not_override_gameplay(self) -> None:
        self.editor._drop_animation_assets(self.images)
        self.draw()
        self.editor._animation_viewport_active = True
        self.editor.preview.set_mode("play")
        runtime = self.editor.preview.selected_runtime_object()
        assert runtime is not None
        renderer = runtime.require_component(SpriteRenderer)
        original = renderer.image
        with self.editor._animation_viewport_override():
            self.assertIs(renderer.image, original)
        self.assertFalse(self.editor._animation_viewport_active)
        self.editor.preview.set_mode("edit")

    def test_scrubbing_mirrors_frame_after_runtime_reload(self) -> None:
        self.editor._drop_animation_assets(self.images)
        self.draw()
        slider = imgui.slider_float

        def scrub(label, value, *args):
            result = slider(label, value, *args)
            return (True, 0.15) if label == "##animation-time" else result

        with patch("g_editor.panels.animation.imgui.slider_float", side_effect=scrub):
            self.draw()
        self.assertFalse(self.editor._animation_preview.playing)
        self.assertTrue(self.editor._animation_viewport_active)
        self.editor.preview.reload()
        runtime = self.editor.preview.selected_runtime_object()
        assert runtime is not None
        renderer = runtime.require_component(SpriteRenderer)
        original = renderer.image
        with self.editor._animation_viewport_override():
            self.assertIs(renderer.image, self.clip().frames[1].image)
        self.assertIs(renderer.image, original)

    def test_removing_state_does_not_submit_previous_preview_texture(self) -> None:
        self.editor._drop_animation_assets(self.images)
        index, properties, states = self.selected()
        self.editor._add_animation_state(index, properties, states)
        self.draw()
        self.editor._animation_state_name = "default"
        self.editor._reset_animation_preview()
        self.draw()
        image = self.clip().frames[0].image
        assert image is not None
        preview_texture = weakref.ref(source_of(image).get_texture())
        del image
        button = imgui.button
        render = self.editor.imgui_renderer.render
        invalid = []
        checked = []
        texture_alive = []

        def remove(label, *args):
            result = button(label, *args)
            return True if label == "Remove" else result

        def verify_textures(draw_data):
            gc.collect()
            texture_alive.append(preview_texture() is not None)
            self.editor.window.switch_to()
            for commands in draw_data.cmd_lists:
                for command in commands.cmd_buffer:
                    if command.elem_count:
                        texture_id = command.tex_ref.get_tex_id()
                        checked.append(texture_id)
                        if not gl.glIsTexture(texture_id):
                            invalid.append(texture_id)
            if not invalid:
                render(draw_data)

        with (
            patch("g_editor.panels.animation.imgui.button", side_effect=remove),
            patch.object(self.editor.imgui_renderer, "render", side_effect=verify_textures),
        ):
            self.draw()
        self.assertTrue(checked)
        self.assertEqual(texture_alive, [False])
        self.assertEqual(invalid, [])
        self.assertEqual(self.editor._animation_frame_textures, [])
        gc.collect()
        self.assertIsNone(preview_texture())
        self.draw()
        self.assertEqual(len(self.selected()[2]), 1)

    def test_duration_edit_keeps_preview_and_timeline_textures_alive_until_render(self) -> None:
        self.editor._drop_animation_assets(self.images)
        self.draw()
        textures = [
            weakref.ref(source_of(frame.image).get_texture())
            for frame in self.clip().frames if frame.image is not None
        ]
        frame_settings = self.editor._draw_animation_frame_settings
        render = self.editor.imgui_renderer.render
        alive = []

        def edit_duration(index, states, state_index):
            frame_settings(index, states, state_index)
            self.editor._set_animation_frame_duration(index, states, state_index, 0, 0.25)
            updated = self.selected()[2]
            self.editor._refresh_animation_clip(updated[state_index])

        def verify_textures(draw_data):
            gc.collect()
            alive.extend(texture() is not None for texture in textures)
            render(draw_data)

        with (
            patch.object(self.editor, "_draw_animation_frame_settings", side_effect=edit_duration),
            patch.object(self.editor.imgui_renderer, "render", side_effect=verify_textures),
        ):
            self.draw()
        self.assertEqual(alive, [True, True, True])
        self.assertEqual(self.editor._animation_frame_textures, [])
        gc.collect()
        self.assertTrue(all(texture() is None for texture in textures))
        self.draw()
        self.assertEqual(self.clip().frames[0].duration, 0.25)

    def test_gif_timing_edits_round_trip_without_changing_asset(self) -> None:
        self.editor._drop_animation_assets((self.gif,))
        self.draw()
        index, _, states = self.selected()
        self.assertTrue(states[0]["animated"])
        self.assertEqual(len(self.clip().frames), 2)
        self.editor._set_animation_frame_duration(index, states, 0, 0, 0.25)
        self.draw()
        _, _, states = self.selected()
        self.assertEqual(states[0]["durations"], [0.25, 0.1])
        runtime = self.editor.preview.selected_runtime_object()
        assert runtime is not None
        animator = runtime.require_component(SpriteAnimator)
        self.assertEqual(animator.states["default"].frames[0].duration, 0.25)
        animator.update(0.15)
        self.assertEqual(animator.frame_index, 0)
        serializer = SceneSerializer()
        game = self.editor.preview.game
        assert game is not None
        document = serializer.capture(game.scenes.current, asset_root=self.root)
        restored = serializer.restore(game, document, asset_root=self.root, initialize_components=False)
        self.assertEqual(restored.objects[0].require_component(SpriteAnimator).states["default"].frames[0].duration, 0.25)
        self.assertEqual(self.gif.read_bytes(), GIF)

    def test_gif_duration_overrides_validate_count_and_timing(self) -> None:
        animation = Animation(self.gif)
        with self.assertRaisesRegex(ValueError, "frame count"):
            SpriteAnimation.from_animation(animation, durations=[0.1])
        for duration in (0, -1, float("nan"), float("inf")):
            with self.subTest(duration=duration), self.assertRaisesRegex(ValueError, "positive"):
                SpriteAnimation.from_animation(animation, durations=[duration, 0.1])
        self.editor._drop_animation_assets((self.gif,))
        index, _, states = self.selected()
        states = deepcopy(states)
        states[0]["durations"] = [0.1]
        self.editor.state.set_sprite_animator_configuration(index, cast(JsonValue, states))
        game = self.editor.preview.game
        assert game is not None
        with self.assertRaisesRegex(SceneFormatError, "frame count"):
            SceneSerializer().restore(
                game, self.editor.state.document, asset_root=self.root, initialize_components=False,
            )

    def test_frame_reordering_and_numeric_duration_edit(self) -> None:
        self.editor._drop_animation_assets(self.images)
        index, _, states = self.selected()
        self.editor._move_animation_frame(index, states, 0, 0, 2)
        index, _, states = self.selected()
        self.assertEqual(self.frames(states)[2]["image"], self.images[0].name)
        self.editor._set_animation_frame_duration(index, states, 0, 2, 0.035)
        self.draw()
        self.assertAlmostEqual(self.clip().frames[2].duration, 0.035)
        with self.assertRaisesRegex(ValueError, "positive"):
            self.editor._set_animation_frame_duration(index, states, 0, 2, 0)

    def test_timeline_widths_are_proportional_and_drag_can_shorten_frame(self) -> None:
        self.editor._drop_animation_assets(self.images)
        index, _, states = self.selected()
        for frame, duration in zip(self.frames(states), (0.1, 0.2, 0.4)):
            frame["duration"] = duration
        self.editor.state.set_sprite_animator_configuration(index, cast(JsonValue, states))
        self.editor._animation_zoom = 500
        self.draw()
        self.draw()
        widths = [right - left for left, _, right, _ in self.editor._animation_frame_rects.values()]
        self.assertEqual(widths, [50, 100, 200])
        invisible = imgui.invisible_button
        current = ""
        phase = "activate"
        mouse_x = 100.0

        def button(label, size, *args):
            nonlocal current
            current = label
            return invisible(label, size, *args)

        def handle():
            return current == "##animation-resize-0"

        with (
            patch("g_editor.panels.animation.imgui.invisible_button", side_effect=button),
            patch("g_editor.panels.animation.imgui.is_item_activated", side_effect=lambda: handle() and phase == "activate"),
            patch("g_editor.panels.animation.imgui.is_item_active", side_effect=lambda: handle() and phase in ("activate", "move")),
            patch("g_editor.panels.animation.imgui.is_item_deactivated", side_effect=lambda: handle() and phase == "release"),
            patch("g_editor.panels.animation.imgui.get_mouse_pos", side_effect=lambda: imgui.ImVec2(mouse_x, 0)),
        ):
            self.draw()
            phase, mouse_x = "move", 75
            self.draw()
            self.assertAlmostEqual(self.editor._animation_resize_duration, 0.05)
            phase = "release"
            self.draw()
        self.assertIsNone(self.editor._animation_resize)
        _, _, updated = self.selected()
        self.assertAlmostEqual(self.editor._load_animation_clip(updated[0]).frames[0].duration, 0.05)
        self.assertTrue(self.editor.state.undo())
        _, _, undone = self.selected()
        self.assertAlmostEqual(self.editor._load_animation_clip(undone[0]).frames[0].duration, 0.1)

    def test_default_layout_keeps_timeline_frames_visible(self) -> None:
        self.editor._drop_animation_assets(self.images)
        for _ in range(5):
            self.draw()
        rectangle = self.editor._animation_rect
        assert rectangle is not None
        _, top, _, bottom = rectangle
        self.assertEqual(len(self.editor._animation_frame_rects), 3)
        for _, frame_top, _, frame_bottom in self.editor._animation_frame_rects.values():
            self.assertGreaterEqual(frame_top, top)
            self.assertLessEqual(frame_bottom, bottom)

    def test_default_layout_tabs_animation_with_other_bottom_panels(self) -> None:
        for _ in range(5):
            self.draw()
        windows = [
            imgui.internal.find_window_by_name(name)
            for name in ("Animation", "Tile Palette", "Console")
        ]
        self.assertTrue(all(window is not None for window in windows))
        dock_ids = {window.dock_id for window in windows if window is not None}
        self.assertEqual(len(dock_ids), 1)
        self.assertNotIn(0, dock_ids)
        self.editor._show_animation_panel()
        for _ in range(3):
            self.draw()
        self.assertIsNotNone(self.editor._animation_rect)

    def test_native_mouse_drag_shortens_timeline_frame(self) -> None:
        self.editor._drop_animation_assets(self.images)
        self.editor._animation_zoom = 500
        for _ in range(5):
            self.draw()
        _, top, right, bottom = self.editor._animation_frame_rects[0]
        io = imgui.get_io()
        io.add_mouse_pos_event(right - 3, (top + bottom) / 2)
        io.add_mouse_button_event(0, True)
        self.draw()
        self.assertIsNotNone(self.editor._animation_resize)
        io.add_mouse_pos_event(right - 28, (top + bottom) / 2)
        self.draw()
        io.add_mouse_button_event(0, False)
        self.draw()
        _, _, states = self.selected()
        self.assertAlmostEqual(self.editor._load_animation_clip(states[0]).frames[0].duration, 0.05)

    def test_native_mouse_drag_reorders_frames(self) -> None:
        self.editor._drop_animation_assets(self.images)
        for _ in range(5):
            self.draw()
        first = self.editor._animation_frame_rects[0]
        third = self.editor._animation_frame_rects[2]
        io = imgui.get_io()
        io.add_mouse_pos_event(first[0] + 15, first[1] + 30)
        io.add_mouse_button_event(0, True)
        self.draw()
        io.add_mouse_pos_event(first[0] + 30, first[1] + 30)
        self.draw()
        io.add_mouse_pos_event(third[0] + 20, third[1] + 30)
        self.draw()
        io.add_mouse_button_event(0, False)
        self.draw()
        _, _, states = self.selected()
        self.assertEqual([frame["image"] for frame in self.frames(states)], [
            self.images[1].name, self.images[2].name, self.images[0].name,
        ])

    def test_native_drag_drops_multiple_selected_assets_onto_timeline(self) -> None:
        self.editor._select_asset(self.images[0])
        self.editor._select_asset(self.images[2], range_select=True)
        for _ in range(5):
            self.draw()
        asset = self.editor._asset_item_rects[self.images[0]]
        frame = self.editor._animation_frame_rects[0]
        io = imgui.get_io()
        io.add_mouse_pos_event(asset[0] + 20, (asset[1] + asset[3]) / 2)
        io.add_mouse_button_event(0, True)
        self.draw()
        io.add_mouse_pos_event(asset[0] + 40, (asset[1] + asset[3]) / 2)
        self.draw()
        self.assertEqual(self.editor._asset_drag_paths, self.images)
        io.add_mouse_pos_event(frame[0] + 30, frame[1] + 30)
        self.draw()
        io.add_mouse_button_event(0, False)
        self.draw()
        _, _, states = self.selected()
        self.assertEqual([item["image"] for item in self.frames(states)], [path.name for path in self.images])

    def test_missing_asset_can_be_replaced_from_the_panel(self) -> None:
        self.editor._drop_animation_assets(self.images)
        self.images[0].unlink()
        self.editor.preview.reload()
        self.editor.on_draw()
        self.assertIsNotNone(self.editor._animation_error)
        target = self.editor._asset_field_targets["sprite-animation-frame:1:0:0"]
        self.editor._toggle_asset_picker(target)
        self.editor._assign_asset_picker(self.images[1])
        self.draw()
        self.assertIsNone(self.editor._animation_error)
        self.assertIsNotNone(self.clip().frames[0].image)

    def test_asset_multi_selection_and_drag_payload_preserve_file_order(self) -> None:
        self.editor._select_asset(self.images[0])
        self.editor._select_asset(self.images[2], additive=True)
        self.assertEqual(self.editor._selected_assets, {self.images[0], self.images[2]})
        self.editor._select_asset(self.images[0], range_select=True)
        self.assertEqual(self.editor._selected_assets, set(self.images))
        imgui.new_frame()
        imgui.begin("Test Drag")
        with (
            patch("g_editor.panels.assets.imgui.begin_drag_drop_source", return_value=True),
            patch("g_editor.panels.assets.imgui.set_drag_drop_payload_py_id"),
            patch("g_editor.panels.assets.imgui.end_drag_drop_source"),
        ):
            self.editor._draw_asset_drag_source(self.images[0])
        imgui.end()
        imgui.end_frame()
        self.assertEqual(self.editor._asset_drag_paths, self.images)

    def test_external_image_drop_imports_and_animates_multiple_files(self) -> None:
        self.draw()
        rectangle = self.editor._animation_rect
        assert rectangle is not None
        left, top, _, _ = rectangle
        with TemporaryDirectory() as directory:
            external = Path(directory) / "external.png"
            pyglet.image.SolidColorImagePattern((255, 255, 255, 255)).create_image(8, 8).save(str(external))
            self.editor.on_file_drop(
                int(left + 20), int(self.editor.window.height - top - 20),
                [str(external), str(self.images[0])],
            )
        self.draw()
        _, _, states = self.selected()
        self.assertEqual(states[0]["frames"], [
            {"image": "external.png", "duration": 0.1},
            {"image": self.images[0].name, "duration": 0.1},
        ])
        self.assertTrue((self.root / "external.png").is_file())

    def test_invalid_drop_is_atomic_and_play_mode_rejects_edits(self) -> None:
        before = deepcopy(self.editor.state.document)
        with self.assertRaisesRegex(ValueError, "on its own"):
            self.editor._drop_animation_assets((self.gif, self.images[0]))
        self.assertEqual(self.editor.state.document, before)
        self.editor.preview.set_mode("play")
        with self.assertRaisesRegex(ValueError, "Edit mode"):
            self.editor._drop_animation_assets(self.images)


if __name__ == "__main__":
    unittest.main()
