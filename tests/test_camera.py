import unittest
from unittest.mock import Mock

from g import Camera, Game, Vec2, Window


class CameraTests(unittest.TestCase):
    def test_game_camera_defaults_to_identity_view(self) -> None:
        game = Game.from_grid(10, 5, cell_size=20)

        self.assertEqual(game.camera.position, Vec2(100, 50))
        self.assertEqual(
            game.camera.world_to_screen((37, 42)),
            Vec2(37, 42),
        )

    def test_position_and_zoom_transform_world_coordinates(self) -> None:
        camera = Camera(200, 100, x=80, y=30, zoom=2)

        self.assertEqual(camera.world_to_screen((80, 30)), Vec2(100, 50))
        self.assertEqual(camera.world_to_screen((90, 35)), Vec2(120, 60))

    def test_rotation_is_clockwise_camera_rotation(self) -> None:
        camera = Camera(200, 100, x=80, y=30, rotation=90)
        screen = camera.world_to_screen((90, 30))

        self.assertAlmostEqual(screen.x, 100)
        self.assertAlmostEqual(screen.y, 60)

    def test_coordinate_conversion_round_trip(self) -> None:
        camera = Camera(320, 180, x=90, y=70, zoom=1.75, rotation=23)
        world = Vec2(143, -18)

        converted = camera.screen_to_world(camera.world_to_screen(world))

        self.assertAlmostEqual(converted.x, world.x)
        self.assertAlmostEqual(converted.y, world.y)

    def test_zoom_must_be_finite_and_positive(self) -> None:
        camera = Camera(200, 100)

        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError, "zoom must be finite and positive"
                ):
                    camera.zoom = value

    def test_reset_restores_default_view(self) -> None:
        camera = Camera(200, 100)
        camera.position = (40, 30)
        camera.zoom = 3
        camera.rotation = 45

        camera.reset()

        self.assertEqual(camera.position, Vec2(100, 50))
        self.assertEqual(camera.zoom, 1)
        self.assertEqual(camera.rotation, 0)

    def test_window_applies_camera_only_to_world_draw(self) -> None:
        window = Window.__new__(Window)
        original_view = object()
        camera_view = object()
        native_window = Mock()
        native_window.view = original_view
        game = Mock()
        scene = Mock()
        scene.camera.view_matrix = camera_view
        scene._draw_background.side_effect = lambda: self.assertIs(
            native_window.view, original_view
        )
        scene._draw.side_effect = lambda: self.assertIs(
            native_window.view, camera_view
        )
        scene._draw_ui.side_effect = lambda: self.assertIs(
            native_window.view, original_view
        )
        game._draw_direct.side_effect = lambda: self.assertIs(
            native_window.view, original_view
        )
        game.scenes.current = scene
        window._window = native_window
        window._game = game

        window._on_draw()

        native_window.clear.assert_called_once_with()
        scene._draw_background.assert_called_once_with()
        scene._draw.assert_called_once_with()
        scene._draw_ui.assert_called_once_with()
        game._draw_direct.assert_called_once_with()
        game._draw_transition.assert_called_once_with()
        self.assertIs(native_window.view, original_view)


if __name__ == "__main__":
    unittest.main()
