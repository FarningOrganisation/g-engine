from threading import Thread, get_ident
import unittest
from unittest.mock import Mock, patch

from g import Game
from g.macos_frames import MacOSFrameDriver, _DisplayLink
from g.window import Window


class MacOSFrameDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = Mock()
        self.window.vsync = True
        self.window.get_framebuffer_size.return_value = (800, 600)
        self.update = Mock()
        self.reset = Mock()
        self.display = self.enterContext(patch.object(MacOSFrameDriver, "_current_display", return_value=1))
        self.link_type = self.enterContext(patch("g.macos_frames._DisplayLink"))
        self.post = self.enterContext(patch("g.macos_frames.pyglet.app.platform_event_loop.post_event"))
        self.enterContext(patch("g.macos_frames.pyglet.clock.schedule_interval"))
        self.enterContext(patch("g.macos_frames.pyglet.clock.unschedule"))
        self.driver = MacOSFrameDriver(self.window, self.update, self.reset)
        self.addCleanup(self.driver.close)

    def test_background_requests_coalesce_without_updating_or_drawing(self) -> None:
        self.driver.start()

        def request() -> None:
            for _ in range(10):
                self.driver._request_frame()

        worker = Thread(target=request)
        worker.start()
        worker.join()
        self.post.assert_called_once_with(self.driver, "on_display_frame")
        self.update.assert_not_called()
        self.window.draw.assert_not_called()
        main_thread = get_ident()
        seen = []
        self.update.side_effect = lambda elapsed: seen.append(("update", get_ident()))
        self.window.draw.side_effect = lambda elapsed: seen.append(("draw", get_ident()))
        self.driver.on_display_frame()
        self.assertEqual(seen, [("update", main_thread), ("draw", main_thread)])
        self.driver._request_frame()
        self.assertEqual(self.post.call_count, 2)

    def test_elapsed_time_drives_simulation_immediately_before_draw(self) -> None:
        self.driver.start()
        self.driver._previous = 100
        with patch("g.macos_frames.perf_counter", return_value=100.02):
            self.driver.on_display_frame()
        self.assertAlmostEqual(self.update.call_args.args[0], 0.02)
        self.assertEqual(self.update.call_args, self.window.draw.call_args)
        self.window.set_vsync.assert_called_once_with(False)

    def test_display_ticks_during_slow_frame_are_dropped(self) -> None:
        self.driver.start()
        self.driver._request_frame()

        def draw(elapsed: float) -> None:
            del elapsed
            self.driver._request_frame()

        self.window.draw.side_effect = draw
        self.driver.on_display_frame()

        self.post.assert_called_once_with(
            self.driver,
            "on_display_frame",
        )
        self.driver._request_frame()
        self.assertEqual(self.post.call_count, 2)

    def test_display_and_resize_changes_reset_without_catch_up(self) -> None:
        self.driver.start()
        self.driver._previous = 100
        self.window.get_framebuffer_size.return_value = (3024, 1964)
        self.display.return_value = 2
        with patch("g.macos_frames.perf_counter", return_value=110):
            self.driver.on_display_frame()
        self.link_type.return_value.set_display.assert_called_once_with(2)
        self.reset.assert_called_once()
        self.update.assert_called_once_with(0)
        self.driver.on_display_frame()
        self.assertEqual(self.reset.call_count, 1)

    def test_close_ignores_queued_frames_and_restores_vsync(self) -> None:
        self.driver.start()
        self.driver._request_frame()
        self.driver.close()
        self.driver.on_display_frame()
        self.driver._request_frame()
        self.update.assert_not_called()
        self.window.draw.assert_not_called()
        self.window.set_vsync.assert_called_with(True)

    def test_callback_delivery_errors_are_reported_on_main_thread(self) -> None:
        self.driver.start()
        self.post.side_effect = RuntimeError("queue failed")
        self.driver._request_frame()
        with self.assertRaisesRegex(RuntimeError, "Display-link frame delivery failed") as raised:
            self.driver._check_error(0)
        self.assertEqual(str(raised.exception.__cause__), "queue failed")

    def test_failed_start_disables_requests_and_restores_vsync(self) -> None:
        self.link_type.return_value.start.side_effect = RuntimeError("start failed")
        with self.assertRaisesRegex(RuntimeError, "start failed"):
            self.driver.start()
        self.driver._request_frame()
        self.post.assert_not_called()
        self.window.set_vsync.assert_called_with(True)

    def test_reactivation_resets_elapsed_time(self) -> None:
        self.driver.start()
        self.driver._previous = 1
        self.driver._reset_timing()
        with patch("g.macos_frames.perf_counter", return_value=10):
            self.driver.on_display_frame()
        self.update.assert_called_once_with(0)
        self.reset.assert_called_once()

    def test_window_close_disables_queued_requests(self) -> None:
        self.driver.start()
        self.driver._request_frame()
        self.driver._on_close()
        self.driver.on_display_frame()
        self.window.draw.assert_not_called()


class DisplayLinkLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = Mock()
        for name in (
            "CVDisplayLinkCreateWithCGDisplay", "CVDisplayLinkSetOutputCallback",
            "CVDisplayLinkSetCurrentCGDisplay", "CVDisplayLinkStart", "CVDisplayLinkStop",
        ):
            getattr(self.library, name).return_value = 0
        self.enterContext(patch("g.macos_frames.ctypes.CDLL", return_value=self.library))

    def test_native_callback_only_requests_frame_and_native_link_closes_once(self) -> None:
        request = Mock()
        link = _DisplayLink(1, request)
        self.assertEqual(link._callback(None, None, None, 0, None, None), 0)
        request.assert_called_once()
        link.start()
        link.set_display(2)
        link.close()
        link.close()
        self.library.CVDisplayLinkStart.assert_called_once()
        self.library.CVDisplayLinkStop.assert_called_once()
        self.library.CVDisplayLinkRelease.assert_called_once()

    def test_callback_registration_failure_releases_native_link(self) -> None:
        self.library.CVDisplayLinkSetOutputCallback.return_value = -1
        with self.assertRaisesRegex(RuntimeError, "set callback failed"):
            _DisplayLink(1, Mock())
        self.library.CVDisplayLinkRelease.assert_called_once()

    def test_native_start_failure_can_be_cleaned_up_without_stopping_unstarted_link(self) -> None:
        link = _DisplayLink(1, Mock())
        self.library.CVDisplayLinkStart.return_value = -1
        with self.assertRaisesRegex(RuntimeError, "start failed"):
            link.start()
        link.close()
        self.library.CVDisplayLinkStop.assert_not_called()
        self.library.CVDisplayLinkRelease.assert_called_once()

class MacOSWindowIntegrationTests(unittest.TestCase):
    @patch("g.window.sys.platform", "darwin")
    @patch("g.macos_frames.MacOSFrameDriver")
    @patch("g.window.pyglet.app.run")
    def test_native_driver_replaces_independent_simulation_and_redraw_timers(self, run: Mock, driver_type: Mock) -> None:
        window = Window.__new__(Window)
        window._macos_fullscreen = None
        window._window = Mock()
        game = Game(10, 10)
        window._game = game
        window._surface = None
        game._update = Mock()

        def drive(*, interval) -> None:
            self.assertIsNone(interval)
            _, frame, reset = driver_type.call_args.args
            frame(1 / 120)
            frame(1 / 120)
            game._update.assert_called_once_with(1 / 60)
            frame(1 / 120)
            reset()
            frame(1 / 120)
            self.assertEqual(game._update.call_count, 1)
            frame(1 / 120)
            self.assertEqual(game._update.call_count, 2)

        run.side_effect = drive
        with patch("g.window.pyglet.clock.schedule") as schedule:
            window.run()
        schedule.assert_not_called()
        driver_type.return_value.start.assert_called_once()
        driver_type.return_value.close.assert_called_once()
        self.assertIsNone(window._interpolation)
        self.assertFalse(game._started)

    @patch("g.window.sys.platform", "darwin")
    @patch("g.macos_frames.MacOSFrameDriver")
    @patch("g.window.pyglet.app.run")
    def test_cleanup_failure_still_stops_game_and_deletes_surface(self, run: Mock, driver_type: Mock) -> None:
        window = Window.__new__(Window)
        window._macos_fullscreen = None
        window._window = Mock()
        window._game = Game(10, 10)
        surface = Mock()
        window._surface = surface
        driver_type.return_value.close.side_effect = RuntimeError("stop failed")
        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            window.run()
        surface.delete.assert_called_once()
        self.assertIsNone(window._surface)
        self.assertFalse(window._game._started)
