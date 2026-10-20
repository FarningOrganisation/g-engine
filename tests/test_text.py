import unittest
from unittest.mock import Mock, patch

from g import Font, Game, GameObject, TextRenderer, create_text


class TextTests(unittest.TestCase):
    @patch("g.components.text_renderer.pyglet.text.Label")
    def test_creates_centered_pyglet_label(self, label_type: Mock) -> None:
        text = create_text(
            "Score",
            10,
            20,
            font=self._font("Game Font"),
            font_size=18,
            color=(255, 220, 80),
        )

        label_type.assert_called_once_with(
            "Score",
            0,
            0,
            anchor_x="center",
            anchor_y="center",
            font_name="Game Font",
            font_size=18,
            color=(255, 220, 80),
            align="center",
            batch=None,
        )
        self.assertIs(type(text), GameObject)
        self.assertIsInstance(text.require_component(TextRenderer), TextRenderer)

    @patch("g.components.text_renderer.pyglet.text.Label")
    def test_enables_multiline_layout_for_newlines(
        self,
        label_type: Mock,
    ) -> None:
        label = label_type.return_value

        create_text("Move left\nMove right", 10, 20)

        self.assertTrue(label.multiline)
        self.assertIsNotNone(label.width)

    @patch("g.components.text_renderer.pyglet.text.Label")
    def test_exposes_mutable_text_properties(self, label_type: Mock) -> None:
        label = label_type.return_value
        text = create_text("Score", 10, 20)
        renderer = text.require_component(TextRenderer)
        font = self._font("Game Font")

        renderer.text = "Game Over"
        renderer.font = font
        renderer.font_size = 24
        renderer.color = (10, 20, 30)
        renderer.opacity = 128
        renderer.alignment = "right"

        self.assertEqual(label.text, "Game Over")
        self.assertEqual(label.font_name, "Game Font")
        self.assertIs(renderer.font, font)
        self.assertEqual(label.font_size, 24)
        self.assertEqual(label.color, (10, 20, 30, 255))
        self.assertEqual(label.opacity, 128)
        self.assertEqual(renderer.alignment, "right")
        self.assertEqual(label.anchor_x, "right")

    @patch("g.components.text_renderer.pyglet.text.Label")
    def test_rejects_non_positive_font_size(self, label_type: Mock) -> None:
        with self.assertRaisesRegex(ValueError, "font_size must be positive"):
            create_text("Score", 10, 20, font_size=0)
        label_type.assert_not_called()

    @patch("g.components.text_renderer.pyglet.text.Label")
    def test_rejects_unknown_alignment(self, label_type: Mock) -> None:
        with self.assertRaisesRegex(ValueError, "alignment must be"):
            create_text("Score", 10, 20, alignment="middle")
        label_type.assert_not_called()

    @patch("g.components.text_renderer.pyglet.text.Label")
    def test_syncs_transform_scale_and_visibility(
        self, label_type: Mock
    ) -> None:
        label = label_type.return_value
        label.font_size = 18
        text = create_text("Score", 10, 20, font_size=18, angle=30)
        renderer = text.require_component(TextRenderer)
        text.scale_x = 2
        text.visible = False

        renderer.sync()

        self.assertEqual((label.x, label.y), (10, 20))
        self.assertEqual(label.rotation, 30)
        self.assertEqual(label.font_size, 36)
        self.assertFalse(label.visible)

    @patch("g.components.text_renderer.pyglet.text.Label")
    def test_game_add_text_uses_grid_coordinates(
        self, label_type: Mock
    ) -> None:
        game = Game.from_grid(10, 10, cell_size=20)

        text = game.grid.add_text(
            2,
            3,
            "Score",
            font=self._font("Game Font"),
            alignment="left",
        )

        self.assertEqual(text.position, game.grid.cell_center(2, 3))
        self.assertIs(text.parent, game.root)
        self.assertEqual(
            text.require_component(TextRenderer).alignment,
            "left",
        )
        label_type.assert_called_once()

    @patch("g.components.text_renderer.pyglet.text.Label")
    def test_screen_space_text_uses_ui_batch(self, label_type: Mock) -> None:
        game = Game(10, 10)
        text = game.add(create_text("Score", 20, 20, screen_space=True))
        renderer = text.require_component(TextRenderer)

        game._prepare_subtree(text)

        self.assertTrue(renderer.screen_space)
        self.assertIs(renderer.drawable.batch, game._ui_batch)
        label_type.assert_called_once()

    @staticmethod
    def _font(family: str) -> Font:
        font = Mock(spec=Font)
        font.family = family
        return font


if __name__ == "__main__":
    unittest.main()
