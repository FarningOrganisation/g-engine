import unittest
from unittest.mock import Mock

from g import FadeTransition, Game, Key, Scene, create_rect


class TrackingScene(Scene):
    def __init__(
        self,
        name: str,
        events: list[str],
    ) -> None:
        super().__init__()
        self.name = name
        self.events = events
        self.updates: list[float] = []

    def on_load(self) -> None:
        self.events.append(f"{self.name}:load")

    def on_enter(self, previous: Scene | None) -> None:
        previous_name = getattr(previous, "name", "none")
        self.events.append(f"{self.name}:enter:{previous_name}")

    def on_shown(self) -> None:
        self.events.append(f"{self.name}:shown")

    def on_exit(self, next_scene: Scene | None) -> None:
        next_name = getattr(next_scene, "name", "none")
        self.events.append(f"{self.name}:exit:{next_name}")

    def on_unload(self) -> None:
        self.events.append(f"{self.name}:unload")

    def update(self, delta_time: float) -> None:
        self.updates.append(delta_time)


class SceneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(320, 180)
        self.events: list[str] = []
        self.play = self.game.scenes.add(
            "play", TrackingScene("play", self.events)
        )
        self.pause = self.game.scenes.add(
            "pause",
            TrackingScene("pause", self.events),
        )

    def test_switch_runs_scene_lifecycle_and_delegates_game_state(self) -> None:
        self.game.scenes.switch("play")

        self.assertEqual(
            self.events,
            ["play:load", "play:enter:none", "play:shown"],
        )
        self.assertIs(self.game.scenes.current, self.play)
        self.assertIs(self.game.root, self.play.root)
        self.assertIs(self.game.camera, self.play.camera)
        self.assertIs(self.game.physics, self.play.physics)
        self.assertIs(self.game.grid, self.play.grid)
        self.assertIs(self.game.time, self.play.time)

        rect = self.game.add(create_rect(10, 20, (255, 255, 255), 5, 5))
        self.assertIn(rect, self.play.objects)

    def test_only_current_scene_updates(self) -> None:
        self.game.scenes.switch("play")

        self.game._update(0.25)

        self.assertEqual(self.play.updates, [0.25])
        self.assertEqual(self.pause.updates, [])

    def test_scene_time_follows_active_and_paused_scene(self) -> None:
        ticks: list[float] = []
        self.play.time.every(
            0.25,
            lambda: ticks.append(self.play.time.elapsed),
        )
        self.game.scenes.switch("play")

        self.game._update(0.25)
        self.play.gui.current.pauses_scene = True
        self.game._update(1)
        self.play.gui.current.pauses_scene = False
        self.game.scenes.switch("default")
        self.game._update(1)
        self.game.scenes.switch("play")
        self.game._update(0.25)

        self.assertEqual(ticks, [0.25, 0.5])
        self.assertEqual(self.play.time.elapsed, 0.5)

    def test_fade_swaps_at_midpoint_and_blocks_input(self) -> None:
        self.game.scenes.switch("play")
        target = self.game.scenes.add(
            "target", TrackingScene("target", self.events)
        )
        self.events.clear()
        transition = FadeTransition(duration=1)

        self.game.scenes.switch("target", transition=transition)
        self.game._handle_key_press(Key.SPACE, 0)

        self.assertTrue(self.game.scenes.input_blocked)
        self.assertFalse(self.game.input.pressed(Key.SPACE))
        self.assertEqual(transition.overlay_color, (0, 0, 0, 0))

        self.game._update(0.25)
        self.assertIs(self.game.scenes.current, self.play)
        self.assertEqual(transition.overlay_color, (0, 0, 0, 128))

        self.game._update(0.25)
        self.assertIs(self.game.scenes.current, target)
        self.assertIn("play:exit:target", self.events)
        self.assertIn("target:enter:play", self.events)
        self.assertNotIn("target:shown", self.events)
        self.assertEqual(transition.overlay_color, (0, 0, 0, 255))

        self.game._update(0.5)
        self.assertIsNone(self.game.scenes.transition)
        self.assertIn("target:shown", self.events)

    def test_scene_can_be_unloaded_after_it_becomes_inactive(self) -> None:
        self.game.scenes.switch("play")
        self.game.scenes.switch("default")
        timer = self.play.time.after(1, Mock())

        removed = self.game.scenes.remove("play")

        self.assertIs(removed, self.play)
        self.assertIn("play:unload", self.events)
        self.assertTrue(timer.cancelled)
        self.assertEqual(self.play.time.elapsed, 0)

    def test_cannot_switch_to_current_or_overlap_transitions(self) -> None:
        with self.assertRaisesRegex(ValueError, "already current"):
            self.game.scenes.switch("default")

        self.game.scenes.switch("play", transition=FadeTransition())
        with self.assertRaisesRegex(RuntimeError, "already active"):
            self.game.scenes.switch("pause", transition=FadeTransition())


class GameLifecycleTests(unittest.TestCase):
    def test_start_and_stop_hooks_run_once_per_cycle(self) -> None:
        events: list[str] = []

        class LifecycleGame(Game):
            def on_start(self) -> None:
                events.append("start")

            def on_stop(self) -> None:
                events.append("stop")

        game = LifecycleGame(320, 180)

        game._start()
        game._start()
        game._stop()
        game._stop()

        self.assertEqual(events, ["start", "stop"])


if __name__ == "__main__":
    unittest.main()
