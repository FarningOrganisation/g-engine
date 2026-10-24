import unittest
from unittest.mock import Mock

from g import Component, Game, GameObject, Node, RectRenderer, Vec2


class MovingRect(GameObject):
    def __init__(self) -> None:
        super().__init__(0, 0)
        self.add_component(RectRenderer(20, 100))
        self.updates: list[float] = []

    @property
    def top(self) -> float:
        return self.y + self.require_component(RectRenderer).height / 2

    def update(self, delta_time: float) -> None:
        self.updates.append(delta_time)
        self.position += Vec2(0, 100) * delta_time


class GameObjectUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(10, 10)

    def tearDown(self) -> None:
        for obj in self.game.objects:
            self.game.remove(obj)

    def test_base_hook_is_no_op(self) -> None:
        obj = self.game.add(GameObject(3, 4))
        self.game._update(0.25)
        self.assertEqual(obj.position, Vec2(3, 4))

    def test_base_update_moves_by_speed_and_elapsed_time(self) -> None:
        obj = self.game.add(GameObject(3, 4))
        obj.speed = Vec2(100, -40)
        self.game._update(0.25)
        self.assertEqual(obj.position, Vec2(28, -6))
        self.game._update(0.5)
        self.assertEqual(obj.position, Vec2(78, -26))

    def test_default_speeds_are_independent(self) -> None:
        first, second = GameObject(), GameObject()
        first.speed.x = 100
        self.assertEqual(second.speed, Vec2())

    def test_game_object_override_can_use_base_movement(self) -> None:
        class Paddle(GameObject):
            def __init__(self) -> None:
                super().__init__(0, 0)
                self.add_component(RectRenderer(20, 100))

            @property
            def top(self) -> float:
                return self.y + self.require_component(RectRenderer).height / 2

            def update(self, delta_time: float) -> None:
                self.speed.y = 100
                super().update(delta_time)
                height = self.require_component(RectRenderer).height
                self.y = min(80 - height / 2, self.y)

        obj = self.game.add(Paddle())
        self.game._update(0.5)
        self.assertEqual(obj.top, 80)
        self.assertEqual(obj.y, 30)

    def test_override_without_super_does_not_apply_speed(self) -> None:
        obj = self.game.add(MovingRect())
        obj.speed = Vec2(100, 100)
        self.game._update(0.25)
        self.assertEqual(obj.position, Vec2(0, 25))

    def test_speed_movement_is_local_and_respects_enabled_state(self) -> None:
        parent = self.game.add(GameObject(100, 200, scale_x=2, scale_y=3))
        child = GameObject()
        parent.add_child(child)
        child.speed = Vec2(10, 20)
        self.game._update(0.5)
        self.assertEqual(child.position, Vec2(5, 10))
        self.assertEqual(child.world_position, Vec2(110, 230))
        parent.enabled = False
        self.game._update(0.5)
        self.assertEqual(child.position, Vec2(5, 10))

    def test_rect_override_is_called_once_per_frame(self) -> None:
        obj = self.game.add(MovingRect())
        self.game._update(0.25)
        self.game._update(0.5)
        self.assertEqual(obj.updates, [0.25, 0.5])
        self.assertEqual(obj.position, Vec2(0, 75))

    def test_children_update_and_disabled_hierarchy_is_skipped(self) -> None:
        parent = self.game.add(GameObject())
        child = MovingRect()
        parent.add_child(child)
        self.game._update(0.1)
        parent.enabled = False
        self.game._update(0.2)
        parent.enabled = True
        child.enabled = False
        self.game._update(0.3)
        child.enabled = True
        self.game._update(0.4)
        self.assertEqual(child.updates, [0.1, 0.4])

    def test_hidden_objects_still_update(self) -> None:
        obj = self.game.add(MovingRect())
        obj.visible = False
        self.game._update(0.1)
        self.assertEqual(obj.updates, [0.1])

    def test_components_update_without_super_call(self) -> None:
        obj = self.game.add(MovingRect())
        component = obj.add_component(Component())
        component.update = Mock()
        disabled = obj.add_component(Component(enabled=False))
        disabled.update = Mock()
        self.game._update(0.1)
        component.update.assert_called_once_with(0.1)
        disabled.update.assert_not_called()

    def test_frame_order(self) -> None:
        obj = self.game.add(GameObject())
        component = obj.add_component(Component())
        calls = Mock()
        self.game.input._begin_frame = calls.input
        self.game.physics.step = calls.physics
        obj.update = calls.object
        component.update = calls.component
        self.game._dispatch_pressed_keys = calls.keys
        self.game._update(0.1)
        self.assertEqual(
            [call[0] for call in calls.mock_calls],
            ["input", "physics", "object", "component", "keys"],
        )

    def test_plain_nodes_do_not_need_update_hook(self) -> None:
        self.game.root.add_child(Node())
        self.game._update(0.1)

    def test_mutable_position_supports_rendering_and_physics(self) -> None:
        obj = self.game.grid.add_rect(2, 3, 20, 10, (255, 255, 255))
        renderer = obj.require_component(RectRenderer)
        obj.position.x = 120
        obj.position.y = 150
        self.assertEqual(obj.x - renderer.width / 2, 110)
        self.assertEqual(obj.y + renderer.height / 2, 155)
        self.game._draw()
        self.assertEqual(renderer.drawable.position, (120, 150))
        obj.add_physics()
        self.game.physics.refresh()
        self.game._update(1 / 60)
        self.assertIsInstance(obj.position, Vec2)
        self.assertEqual(obj.position, Vec2(120, 150))


if __name__ == "__main__":
    unittest.main()
