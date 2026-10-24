import unittest

from g import Game, Grid, RectRenderer, CircleRenderer, LineRenderer, Vec2, create_rect


class GridTests(unittest.TestCase):
    def test_game_dimensions_are_world_pixels(self) -> None:
        game = Game(640, 480, grid_cell_size=32)

        self.assertEqual(game.width, 640)
        self.assertEqual(game.height, 480)
        self.assertEqual(game.pixel_width, 640)
        self.assertEqual(game.pixel_height, 480)
        self.assertEqual((game.grid.columns, game.grid.rows), (20, 15))

    def test_from_grid_calculates_pixel_dimensions(self) -> None:
        game = Game.from_grid(20, 15, cell_size=32)

        self.assertEqual((game.width, game.height), (640, 480))
        self.assertEqual(game.grid.cell_size, 32)
        self.assertEqual((game.grid.columns, game.grid.rows), (20, 15))
        self.assertEqual(
            (game.navigation.width, game.navigation.height),
            (game.grid.columns, game.grid.rows),
        )

    def test_grid_converts_and_snaps_coordinates(self) -> None:
        grid = Game.from_grid(10, 10, cell_size=20).grid

        self.assertEqual(grid.cell_origin(2, 3), (40, 60))
        self.assertEqual(grid.cell_center(2, 3), (50, 70))
        self.assertEqual(grid.world_to_cell(59, 79), (2, 3))
        self.assertEqual(grid.snap_to_origin((59, 79)), (40, 60))
        self.assertEqual(grid.snap_to_center((59, 79)), (50, 70))

    def test_grid_places_existing_object(self) -> None:
        game = Game.from_grid(10, 10, cell_size=20)
        rect = create_rect(0, 0, 10, 10, (255, 255, 255))

        result = game.grid.add(rect, 2, 3)

        self.assertIs(result, rect)
        self.assertEqual(rect.position, Vec2(50, 70))
        self.assertIs(rect.parent, game.root)

        game.grid.move(rect, 4, 5)
        self.assertEqual(rect.position, Vec2(90, 110))

    def test_grid_factories_use_cell_defaults(self) -> None:
        game = Game.from_grid(10, 10, cell_size=20)

        rect = game.grid.add_rect(2, 3, color=(255, 255, 255))
        circle = game.grid.add_circle(4, 5, color=(255, 255, 255))

        rect_renderer = rect.require_component(RectRenderer)
        circle_renderer = circle.require_component(CircleRenderer)
        self.assertEqual((rect_renderer.width, rect_renderer.height), (20, 20))
        self.assertEqual(rect.position, Vec2(50, 70))
        self.assertEqual(circle_renderer.radius, 10)
        self.assertEqual(circle.position, Vec2(90, 110))

    def test_grid_overlay_can_be_shown_and_hidden(self) -> None:
        game = Game.from_grid(2, 1, cell_size=10)

        lines = game.grid.show(thickness=2)

        self.assertTrue(game.grid.overlay_visible)
        self.assertEqual(len(lines), 5)
        self.assertTrue(all(line.parent is game.root for line in lines))
        self.assertTrue(
            all(line.require_component(LineRenderer).thickness == 2 for line in lines)
        )

        game.grid.hide()

        self.assertFalse(game.grid.overlay_visible)
        self.assertTrue(all(line.parent is None for line in lines))

    def test_grid_is_publicly_constructible(self) -> None:
        game = Game(100, 80)

        grid = Grid(game, 10, columns=10, rows=8)

        self.assertEqual(grid.cell_center(9, 7), (95, 75))


if __name__ == "__main__":
    unittest.main()
