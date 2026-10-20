import unittest
from unittest.mock import Mock

from g import (
    Game,
    PresentationSettings,
    Window,
    presentation_rect,
)


class PresentationTests(unittest.TestCase):
    def test_fit_centers_complete_virtual_game(self) -> None:
        rect = presentation_rect(800, 600, 1600, 900, "fit")

        self.assertEqual(
            (rect.x, rect.y, rect.width, rect.height),
            (200, 0, 1200, 900),
        )
        self.assertEqual(rect.to_virtual(200, 0), (0, 0))
        self.assertEqual(rect.to_virtual(1400, 900), (800, 600))
        self.assertIsNone(rect.to_virtual(100, 450))

    def test_fill_centers_crop_without_showing_more_world(self) -> None:
        rect = presentation_rect(800, 600, 1600, 900, "fill")

        self.assertEqual(
            (rect.x, rect.y, rect.width, rect.height),
            (0, -150, 1600, 1200),
        )
        self.assertEqual(rect.to_virtual(800, 450), (400, 300))

    def test_integer_fit_uses_largest_whole_scale(self) -> None:
        rect = presentation_rect(800, 600, 2560, 1440, "integer_fit")

        self.assertEqual(
            (rect.x, rect.y, rect.width, rect.height),
            (480, 120, 1600, 1200),
        )

    def test_stretch_maps_each_axis_independently(self) -> None:
        rect = presentation_rect(800, 600, 1600, 900, "stretch")

        self.assertEqual((rect.x, rect.y), (0, 0))
        self.assertEqual((rect.scale_x, rect.scale_y), (2, 1.5))
        self.assertEqual(rect.to_virtual(800, 450), (400, 300))

    def test_window_maps_mouse_to_virtual_coordinates(self) -> None:
        window = Window.__new__(Window)
        game = Game(800, 600)
        native = Mock()
        native.width = 1600
        native.height = 900
        window._game = game
        window._window = native

        game._handle_mouse_press = Mock()  # type: ignore[method-assign]
        window._on_mouse_press(800, 450, 1, 0)
        window._on_mouse_press(100, 450, 1, 0)

        game._handle_mouse_press.assert_called_once_with(400, 300, 1, 0)

    def test_game_presentation_does_not_resize_camera(self) -> None:
        game = Game(800, 600)
        game.presentation = PresentationSettings(
            "fit",
            (10, 20, 30, 255),
        )

        presentation_rect(800, 600, 3440, 1440, "fit")

        self.assertEqual(
            (game.camera.viewport_width, game.camera.viewport_height),
            (800, 600),
        )


if __name__ == "__main__":
    unittest.main()
