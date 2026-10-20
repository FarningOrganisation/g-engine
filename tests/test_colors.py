import unittest

import g
from g import colors


class ColorTests(unittest.TestCase):
    def test_colors_are_available_from_package(self) -> None:
        self.assertIs(g.colors, colors)
        self.assertEqual(colors.RED, (255, 0, 0))
        self.assertEqual(colors.BLUE, (0, 0, 255))
        self.assertEqual(colors.CORNFLOWER_BLUE, (100, 149, 237))

    def test_exported_color_values_are_rgb_integer_tuples(self) -> None:
        names = [
            name for name in vars(colors) if name.isupper()
        ]
        self.assertEqual(len(names), 148)
        for name in names:
            with self.subTest(name=name):
                value = getattr(colors, name)
                self.assertIsInstance(value, tuple)
                self.assertEqual(len(value), 3)
                self.assertTrue(
                    all(
                        isinstance(channel, int) and 0 <= channel <= 255
                        for channel in value
                    )
                )

    def test_css_aliases_are_available(self) -> None:
        self.assertEqual(colors.AQUA, colors.CYAN)
        self.assertEqual(colors.FUCHSIA, colors.MAGENTA)
        self.assertEqual(colors.DARK_SLATE_GRAY, colors.DARK_SLATE_GREY)
        self.assertEqual(colors.REBECCA_PURPLE, (102, 51, 153))


if __name__ == "__main__":
    unittest.main()
