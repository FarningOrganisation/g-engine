import unittest
from unittest.mock import Mock, call, patch

from g import Game, SceneTime
from g.timing import FixedStepAccumulator
from g.window import Window


class FixedStepAccumulatorTests(unittest.TestCase):
    def test_fast_render_frames_accumulate_into_one_update(self) -> None:
        update = Mock()
        accumulator = FixedStepAccumulator(1 / 60)

        self.assertEqual(accumulator.advance(1 / 120, update), 0)
        self.assertEqual(accumulator.advance(1 / 120, update), 1)

        update.assert_called_once_with(1 / 60)

    def test_slow_render_frame_runs_multiple_fixed_updates(self) -> None:
        update = Mock()
        accumulator = FixedStepAccumulator(1 / 60)

        self.assertEqual(accumulator.advance(1 / 30, update), 2)

        self.assertEqual(
            update.call_args_list,
            [call(1 / 60), call(1 / 60)],
        )

    def test_catch_up_work_is_capped_and_excess_time_is_dropped(self) -> None:
        update = Mock()
        accumulator = FixedStepAccumulator(
            1 / 60,
            max_steps_per_frame=4,
        )

        self.assertEqual(accumulator.advance(10, update), 4)
        self.assertLess(accumulator.alpha, 1)

    def test_reset_discards_partial_step(self) -> None:
        update = Mock()
        accumulator = FixedStepAccumulator(1 / 60)
        accumulator.advance(1 / 120, update)

        accumulator.reset()

        self.assertEqual(accumulator.alpha, 0)
        self.assertEqual(accumulator.advance(1 / 120, update), 0)

    def test_invalid_timing_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FixedStepAccumulator(0)
        with self.assertRaises(ValueError):
            FixedStepAccumulator(max_steps_per_frame=0)
        with self.assertRaises(ValueError):
            FixedStepAccumulator().advance(-0.1, Mock())


class SceneTimeTests(unittest.TestCase):
    def test_after_and_finite_every_use_simulation_time(self) -> None:
        scene_time = SceneTime()
        events: list[str] = []
        once = scene_time.after(0.2, lambda: events.append("once"))
        repeating = scene_time.every(
            0.1,
            lambda: events.append("repeat"),
            times=3,
        )

        scene_time._update(0.1)
        scene_time._update(0.1)
        scene_time._update(0.1)

        self.assertEqual(
            events,
            ["repeat", "once", "repeat", "repeat"],
        )
        self.assertTrue(once.done)
        self.assertTrue(repeating.done)
        self.assertAlmostEqual(scene_time.elapsed, 0.3)
        self.assertEqual(scene_time.delta, 0.1)

    def test_timer_handle_can_pause_resume_and_cancel(self) -> None:
        scene_time = SceneTime()
        callback = Mock()
        timer = scene_time.every(1, callback)
        scene_time._update(0.25)

        timer.pause()
        scene_time._update(2)
        self.assertAlmostEqual(timer.remaining, 0.75)
        callback.assert_not_called()

        timer.resume()
        scene_time._update(0.75)
        callback.assert_called_once_with()
        timer.cancel()
        scene_time._update(2)
        callback.assert_called_once_with()
        self.assertTrue(timer.cancelled)
        self.assertTrue(timer.done)

    def test_timers_created_by_callbacks_wait_for_next_update(self) -> None:
        scene_time = SceneTime()
        events: list[str] = []

        def first() -> None:
            events.append("first")
            scene_time.next_update(lambda: events.append("second"))

        scene_time.next_update(first)

        scene_time._update(0)
        self.assertEqual(events, ["first"])
        scene_time._update(0)
        self.assertEqual(events, ["first", "second"])

    def test_invalid_timer_values_are_rejected(self) -> None:
        scene_time = SceneTime()
        callback = Mock()

        for delay in (-1, float("inf"), float("nan")):
            with self.subTest(delay=delay):
                with self.assertRaises(ValueError):
                    scene_time.after(delay, callback)
        for interval in (0, -1, float("inf"), float("nan")):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    scene_time.every(interval, callback)
        with self.assertRaises(ValueError):
            scene_time.every(1, callback, times=0)
        with self.assertRaises(TypeError):
            scene_time.after(1, None)  # type: ignore[arg-type]


class WindowTimingTests(unittest.TestCase):
    @patch("g.window.sys.platform", "linux")
    @patch("g.window.pyglet.clock.unschedule")
    @patch("g.window.pyglet.clock.schedule")
    @patch("g.window.pyglet.app.run")
    def test_window_uses_frame_driver_with_fixed_simulation_steps(
        self,
        run: Mock,
        schedule: Mock,
        unschedule: Mock,
    ) -> None:
        game = Game(10, 10)
        game._start = Mock()
        game._stop = Mock()
        game._update = Mock()
        user_update = Mock()
        window = Window.__new__(Window)
        window._macos_fullscreen = None
        window._game = game
        window._surface = None

        def drive_frames(*, interval: float) -> None:
            self.assertEqual(interval, 0)
            process_frame = schedule.call_args.args[0]
            process_frame(1 / 120)
            process_frame(1 / 120)

        run.side_effect = drive_frames

        window.run(user_update, interval=1 / 60)

        game._start.assert_called_once_with()
        game._update.assert_called_once_with(1 / 60)
        user_update.assert_called_once_with(1 / 60)
        game._stop.assert_called_once_with()
        unschedule.assert_called_once_with(schedule.call_args.args[0])
        self.assertIsNone(window._interpolation)
        self.assertIsNone(window._accumulator)

    @patch("g.window.sys.platform", "linux")
    @patch("g.window.pyglet.clock.unschedule")
    @patch("g.window.pyglet.clock.schedule")
    @patch("g.window.pyglet.app.run")
    def test_window_pauses_updates_while_assets_advance(
        self,
        run: Mock,
        schedule: Mock,
        unschedule: Mock,
    ) -> None:
        game = Game(10, 10)
        game._start = Mock()
        game._stop = Mock()
        game._update = Mock()
        game._advance_loading = Mock(side_effect=(True, False))
        user_update = Mock()
        window = Window.__new__(Window)
        window._macos_fullscreen = None
        window._game = game
        window._surface = None

        def drive_frames(*, interval: float) -> None:
            self.assertEqual(interval, 0)
            process_frame = schedule.call_args.args[0]
            process_frame(1 / 60)
            game._update.assert_not_called()
            user_update.assert_not_called()
            process_frame(1 / 60)

        run.side_effect = drive_frames

        window.run(user_update, interval=1 / 60)

        self.assertEqual(game._advance_loading.call_count, 2)
        game._update.assert_called_once_with(1 / 60)
        user_update.assert_called_once_with(1 / 60)
        game._stop.assert_called_once_with()
        unschedule.assert_called_once_with(schedule.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
