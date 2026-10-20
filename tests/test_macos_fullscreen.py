from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

import pyglet

from g import Game
from g.macos_fullscreen import MacOSFullscreen
from g.window import Window


def rect(x, y, width, height):
    return SimpleNamespace(
        origin=SimpleNamespace(x=x, y=y),
        size=SimpleNamespace(width=width, height=height),
    )


class MacOSFullscreenTests(unittest.TestCase):
    def setUp(self) -> None:
        cocoa = SimpleNamespace(NSMakeRect=rect, get_selector=lambda name: name)
        self.enterContext(patch.dict("sys.modules", {"pyglet.libs.darwin.cocoapy": cocoa}))
        self.target = Mock()
        self.enterContext(patch("g.macos_fullscreen._make_target", return_value=self.target))
        self.window = Mock()
        self.native = self.window._nswindow
        self.native.collectionBehavior.return_value = 1 << 7
        self.native.styleMask.return_value = 15
        self.native.frame.return_value = rect(100, 200, 800, 600)
        self.screen_frame = rect(1512, 0, 1920, 1080)
        self.native.screen.return_value.frame.return_value = self.screen_frame
        self.controller = MacOSFullscreen(self.window)
        self.addCleanup(self.controller.close)

    def test_green_button_keeps_fullscreen_glyph_with_custom_action(self):
        button = self.native.standardWindowButton_.return_value
        button.setTarget_.assert_called_once_with(self.target)
        button.setAction_.assert_called_once_with("toggleBorderless:")
        self.native.setCollectionBehavior_.assert_called_once_with(1 << 7)

    def test_toggle_uses_current_monitor_and_restores_frame_and_style(self):
        self.controller.on_toggle_fullscreen()
        self.assertTrue(self.controller.fullscreen)
        self.native.setStyleMask_.assert_called_with(0)
        self.native.setFrame_display_.assert_called_with(self.screen_frame, True)
        self.native.frame.return_value = self.screen_frame
        self.controller.on_toggle_fullscreen()
        self.assertFalse(self.controller.fullscreen)
        restored = self.native.setFrame_display_.call_args.args[0]
        self.assertEqual((restored.origin.x, restored.origin.y), (100, 200))
        self.assertEqual((restored.size.width, restored.size.height), (800, 600))
        self.native.setStyleMask_.assert_called_with(15)
        self.assertEqual(self.window.context.update_geometry.call_count, 2)
        self.native.makeFirstResponder_.assert_called_with(self.native.firstResponder.return_value)

    def test_failed_keyboard_focus_restoration_is_reported(self):
        self.native.makeFirstResponder_.return_value = False
        with self.assertRaisesRegex(RuntimeError, "keyboard responder"):
            self.controller.set_fullscreen(True)

    def test_repeated_set_is_idempotent_and_each_entry_uses_current_screen(self):
        self.controller.set_fullscreen(True)
        self.controller.set_fullscreen(True)
        self.assertEqual(self.native.setStyleMask_.call_count, 1)
        self.controller.set_fullscreen(False)
        other_screen = rect(0, 0, 1512, 982)
        self.native.screen.return_value.frame.return_value = other_screen
        self.controller.set_fullscreen(True)
        self.native.setFrame_display_.assert_called_with(other_screen, True)

    def test_restoring_style_rewires_recreated_green_button(self):
        self.controller.set_fullscreen(True)
        replacement = Mock()
        self.native.standardWindowButton_.return_value = replacement
        self.controller.set_fullscreen(False)
        replacement.setTarget_.assert_called_once_with(self.target)
        replacement.setAction_.assert_called_once_with("toggleBorderless:")

    def test_cleanup_restores_button_and_collection_and_ignores_queued_toggle(self):
        button = self.native.standardWindowButton_.return_value
        target, action = button.target.return_value, button.action.return_value
        self.controller.close()
        self.controller.close()
        self.controller.on_toggle_fullscreen()
        button.setTarget_.assert_called_with(target)
        button.setAction_.assert_called_with(action)
        self.native.setCollectionBehavior_.assert_called_with(1 << 7)
        self.target.release.assert_called_once()
        self.native.setStyleMask_.assert_not_called()

    def test_cleanup_after_native_window_has_closed(self):
        self.window._nswindow = None
        self.controller.close()
        self.target.release.assert_called_once()

    def test_delivery_failure_is_reported_and_closed_mutation_is_rejected(self):
        self.controller._delivery_error = RuntimeError("post failed")
        with self.assertRaisesRegex(RuntimeError, "fullscreen action delivery failed") as raised:
            self.controller._check_error(0)
        self.assertEqual(str(raised.exception.__cause__), "post failed")
        self.controller.close()
        with self.assertRaisesRegex(RuntimeError, "controller has closed"):
            self.controller.set_fullscreen(True)


class FullscreenWindowTests(unittest.TestCase):
    @patch("g.window.sys.platform", "darwin")
    @patch("g.macos_fullscreen.MacOSFullscreen")
    @patch("g.window.pyglet.window.Window")
    @patch("g.window._PresentationSurface")
    def test_startup_fullscreen_waits_until_game_size_is_applied(
        self, surface, native_window, controller,
    ):
        window = Window("Game", fullscreen=True)
        self.assertFalse(native_window.call_args.kwargs["fullscreen"])
        controller.return_value.set_fullscreen.assert_not_called()
        game = Game(800, 600)
        window.show_game(game)
        native_window.return_value.set_size.assert_called_once_with(800, 600)
        controller.return_value.set_fullscreen.assert_called_once_with(True)
        window.show_game(game)
        controller.return_value.set_fullscreen.assert_called_once_with(True)

    def make_window(self):
        window = Window.__new__(Window)
        window._window = Mock()
        window._game = Mock()
        window._macos_fullscreen = Mock()
        window._macos_fullscreen.fullscreen = False
        return window

    def test_mac_shortcuts_and_escape_are_consumed_before_game_input(self):
        window = self.make_window()
        key = pyglet.window.key
        for symbol, modifiers in ((key.F11, 0), (key.F, key.MOD_CTRL | key.MOD_COMMAND)):
            self.assertTrue(window._on_key_press(symbol, modifiers))
            window._macos_fullscreen.set_fullscreen.assert_called_with(True)
        window._macos_fullscreen.fullscreen = True
        self.assertTrue(window._on_key_press(key.ESCAPE, 0))
        window._macos_fullscreen.set_fullscreen.assert_called_with(False)
        window._game._handle_key_press.assert_not_called()

    def test_normal_keys_and_windowed_escape_keep_game_behavior(self):
        window = self.make_window()
        key = pyglet.window.key
        for symbol, modifiers in ((key.F, 0), (key.F, key.MOD_CTRL), (key.ESCAPE, 0)):
            window._on_key_press(symbol, modifiers)
            window._game._handle_key_press.assert_called_with(symbol, modifiers)
        window._macos_fullscreen.set_fullscreen.assert_not_called()

    def test_command_m_restores_window_before_minimizing(self):
        window = self.make_window()
        window._macos_fullscreen.fullscreen = True
        calls = Mock()
        calls.attach_mock(window._macos_fullscreen.set_fullscreen, "restore")
        calls.attach_mock(window._window.minimize, "minimize")
        key = pyglet.window.key
        self.assertTrue(window._on_key_press(key.M, key.MOD_COMMAND))
        self.assertEqual([item[0] for item in calls.mock_calls], ["restore", "minimize"])
        window._game._handle_key_press.assert_not_called()

    def test_other_platforms_keep_native_fullscreen_and_key_behavior(self):
        window = self.make_window()
        window._macos_fullscreen = None
        window._window.fullscreen = False
        window.toggle_fullscreen()
        window._window.set_fullscreen.assert_called_once_with(True)
        window._on_key_press(pyglet.window.key.F11, 0)
        window._game._handle_key_press.assert_called_once_with(pyglet.window.key.F11, 0)


@unittest.skipUnless(sys.platform == "darwin", "Requires a real Cocoa window")
class CocoaFullscreenIntegrationTests(unittest.TestCase):
    def send_key(self, window, character, key_code):
        from pyglet.libs.darwin.cocoapy import NSPoint, ObjCClass, get_NSString

        native = window._window._nswindow
        event = ObjCClass("NSEvent").keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            10, NSPoint(0, 0), 0, 0, native.windowNumber(), None,
            get_NSString(character), get_NSString(character), False, key_code,
        )
        native.sendEvent_(event)

    def test_real_green_button_repeatedly_enters_borderless_and_escape_restores(self):
        window = Window("Fullscreen regression", width=800, height=600)
        window._window._enable_event_queue = False
        game = Mock()
        window._game = game
        self.addCleanup(window._window.close)
        controller = window._macos_fullscreen
        assert controller is not None
        self.addCleanup(controller.close)
        native = window._window._nswindow
        self.assertTrue(native.collectionBehavior() & (1 << 7))
        self.assertFalse(native.collectionBehavior() & (1 << 9))
        initial = window._window.get_size(), window._window.get_location(), native.styleMask()
        for _ in range(2):
            responder = native.firstResponder()
            native.standardWindowButton_(2).performClick_(None)
            pyglet.app.platform_event_loop.dispatch_posted_events()
            self.assertTrue(window.fullscreen)
            self.assertEqual(native.styleMask(), 0)
            self.assertEqual(native.firstResponder(), responder)
            self.send_key(window, "d", 2)
            game._handle_key_press.assert_called_with(pyglet.window.key.D, 0)
            self.send_key(window, "\x1b", 53)
            self.assertFalse(window.fullscreen)
            restored = window._window.get_size(), window._window.get_location(), native.styleMask()
            self.assertEqual(restored, initial)
            self.assertEqual(native.firstResponder(), responder)

    def test_real_native_callback_delivery_error_reaches_python(self):
        window = Window("Fullscreen callback regression", width=800, height=600)
        self.addCleanup(window._window.close)
        controller = window._macos_fullscreen
        assert controller is not None
        self.addCleanup(controller.close)
        with patch(
            "g.macos_fullscreen.pyglet.app.platform_event_loop.post_event",
            side_effect=RuntimeError("post failed"),
        ):
            window._window._nswindow.standardWindowButton_(2).performClick_(None)
        with self.assertRaisesRegex(RuntimeError, "fullscreen action delivery failed"):
            controller._check_error(0)
