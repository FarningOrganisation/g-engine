import unittest
from unittest.mock import Mock

from g import (
    BehaviourRunner,
    BehaviourStatus,
    CircleRenderer,
    Game,
    GameObject,
    RectRenderer,
    ShapeStyle,
    Vec2,
    Wait,
    create_rect,
)


class BehaviourLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(200, 100)
        self.obj = self.game.add(GameObject())

    def test_replacing_completed_behaviour_starts_the_replacement(self) -> None:
        self.obj.behaviour = Wait(0)
        self.game._update(0)
        runner = self.obj.get_component(BehaviourRunner)
        if runner is None:
            self.fail("behaviour runner was not created")
        self.assertEqual(runner.status, BehaviourStatus.SUCCESS)

        replacement = Wait(1)
        self.obj.behaviour = replacement

        self.assertIs(self.obj.behaviour, replacement)
        self.assertEqual(runner.status, BehaviourStatus.RUNNING)
        self.game._update(0.5)
        self.assertEqual(runner.status, BehaviourStatus.RUNNING)

    def test_behaviour_can_pause_resume_cancel_and_restart(self) -> None:
        self.obj.behaviour = Wait(1)
        runner = self.obj.get_component(BehaviourRunner)
        if runner is None:
            self.fail("behaviour runner was not created")

        self.game._update(0.4)
        self.obj.pause_behaviour()
        self.game._update(1)
        self.assertEqual(runner.status, BehaviourStatus.RUNNING)

        self.obj.resume_behaviour()
        self.game._update(0.6)
        self.assertEqual(runner.status, BehaviourStatus.SUCCESS)

        self.obj.restart_behaviour()
        self.game._update(0.5)
        self.assertEqual(runner.status, BehaviourStatus.RUNNING)

        self.obj.cancel_behaviour()
        self.assertEqual(runner.status, BehaviourStatus.CANCELLED)
        self.game._update(1)
        self.assertEqual(runner.status, BehaviourStatus.CANCELLED)


class AnimationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(200, 100)
        self.obj = self.game.add(
            create_rect(0, 0, (255, 255, 255), 10, 10)
        )

    def test_animation_handle_controls_playback_and_completion(self) -> None:
        on_complete = Mock()
        handle = (
            self.obj.animate()
            .to(1, x=100)
            .start(on_complete=on_complete)
        )

        self.game._update(0.25)
        self.assertEqual(self.obj.x, 25)
        self.assertTrue(handle.running)

        handle.pause()
        self.game._update(0.5)
        self.assertEqual(self.obj.x, 25)
        self.assertTrue(handle.paused)

        handle.resume()
        self.game._update(0.75)
        self.assertEqual(self.obj.x, 100)
        self.assertTrue(handle.completed)
        on_complete.assert_called_once_with(self.obj)

        self.game._update(1)
        on_complete.assert_called_once_with(self.obj)

    def test_starting_an_animation_cancels_the_previous_one(self) -> None:
        first = self.obj.animate().to(1, x=100).start()
        self.game._update(0.25)

        second = self.obj.animate().to(1, x=50).start()

        self.assertTrue(first.cancelled)
        self.assertTrue(second.running)
        self.game._update(1)
        self.assertEqual(self.obj.x, 50)
        self.assertTrue(second.completed)

    def test_animation_can_be_cancelled_and_restarted(self) -> None:
        handle = self.obj.animate().to(1, x=100).start()
        self.game._update(0.25)
        handle.cancel()
        self.game._update(1)
        self.assertEqual(self.obj.x, 25)
        self.assertTrue(handle.cancelled)

        handle.restart()
        self.game._update(1)
        self.assertEqual(self.obj.x, 100)
        self.assertTrue(handle.completed)

    def test_animation_can_loop(self) -> None:
        callback = Mock()
        handle = self.obj.animate().call(callback).start(loop=True)

        self.game._update(0)
        self.game._update(0)

        self.assertEqual(callback.call_count, 2)
        self.assertTrue(handle.running)
        self.assertFalse(handle.completed)

    def test_builder_can_repeat_steps_a_finite_number_of_times(self) -> None:
        repeated = Mock()
        finished = Mock()
        handle = (
            self.obj.animate()
            .call(repeated)
            .repeat(3)
            .call(finished)
            .start()
        )

        self.game._update(0)
        self.game._update(0)
        self.assertEqual(repeated.call_count, 2)
        finished.assert_not_called()

        self.game._update(0)
        self.assertEqual(repeated.call_count, 3)
        finished.assert_called_once_with(self.obj)
        self.assertTrue(handle.completed)

    def test_builder_can_repeat_steps_indefinitely(self) -> None:
        callback = Mock()
        handle = self.obj.animate().call(callback).repeat().start()

        self.game._update(0)
        self.game._update(0)
        self.game._update(0)

        self.assertEqual(callback.call_count, 3)
        self.assertTrue(handle.running)

    def test_repeat_requires_an_existing_step(self) -> None:
        with self.assertRaisesRegex(ValueError, "step before repeat"):
            self.obj.animate().repeat()

    def test_parallel_builder_runs_subanimations_together(self) -> None:
        move = self.obj.animate().to(1, x=100)
        fade = self.obj.animate().to(0.5, opacity=0)
        handle = self.obj.animate().parallel(move, fade).start()

        self.game._update(0.5)
        self.assertEqual(self.obj.x, 50)
        self.assertEqual(
            self.obj.require_component(RectRenderer).opacity, 0
        )
        self.assertTrue(handle.running)

        self.game._update(0.5)
        self.assertEqual(self.obj.x, 100)
        self.assertTrue(handle.completed)

    def test_parallel_builder_rejects_another_target(self) -> None:
        other = self.game.add(GameObject())

        with self.assertRaisesRegex(ValueError, "same game object"):
            self.obj.animate().parallel(other.animate().to(1, x=10))

    def test_animation_interpolates_vec2_position(self) -> None:
        self.obj.position = Vec2(10, 20)
        self.obj.animate().to(
            1,
            position=Vec2(30, 60),
        ).start()

        self.game._update(0.5)

        self.assertEqual(self.obj.position, Vec2(20, 40))

    def test_animation_interpolates_rgb_and_preserves_alpha(self) -> None:
        renderer = self.obj.require_component(RectRenderer)
        renderer.color = (0, 100, 200, 100)
        self.obj.animate().to(1, color=(200, 0, 0)).start()

        self.game._update(0.5)

        self.assertEqual(renderer.color, (100, 50, 100, 100))

    def test_animation_interpolates_rgba_alpha(self) -> None:
        renderer = self.obj.require_component(RectRenderer)
        renderer.color = (0, 0, 0, 0)
        self.obj.animate().to(
            1,
            color=(100, 200, 50, 200),
        ).start()

        self.game._update(0.5)

        self.assertEqual(renderer.color, (50, 100, 25, 100))

    def test_component_animate_targets_its_renderer_explicitly(self) -> None:
        renderer = self.obj.require_component(RectRenderer)
        renderer.color = (0, 0, 0)
        handle = renderer.animate().to(1, color=(200, 0, 0)).start()

        self.game._update(0.5)

        self.assertEqual(renderer.color, (100, 0, 0, 255))
        self.assertTrue(handle.running)

    def test_game_object_animation_rejects_ambiguous_renderer_property(
        self,
    ) -> None:
        obj = self.game.add(GameObject())
        obj.add_component(RectRenderer(10, 10))
        obj.add_component(CircleRenderer(5))
        obj.animate().to(1, color=(0, 0, 0)).start()

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.game._update(0.5)

    def test_animation_interpolates_fill_and_outline_colors(self) -> None:
        shape = self.game.add_rect(
            50,
            50,
            (255, 255, 255),
            20,
            20,
            style=ShapeStyle(
                fill=(0, 0, 0),
                outline=(100, 100, 100),
            ),
        )
        renderer = shape.require_component(RectRenderer)
        shape.animate().to(
            1,
            fill_color=(100, 200, 50),
            outline_color=(200, 0, 100),
        ).start()

        self.game._update(0.5)

        self.assertEqual(renderer.fill_color, (50, 100, 25, 255))
        self.assertEqual(renderer.outline_color, (150, 50, 100, 255))

    def test_animation_rejects_color_animation_from_none(self) -> None:
        self.assertIsNone(
            self.obj.require_component(RectRenderer).outline_color
        )
        self.obj.animate().to(
            1,
            outline_color=(255, 0, 0),
        ).start()

        with self.assertRaisesRegex(TypeError, "from None"):
            self.game._update(0.5)

    def test_empty_animation_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one step"):
            self.obj.animate().start()


if __name__ == "__main__":
    unittest.main()
