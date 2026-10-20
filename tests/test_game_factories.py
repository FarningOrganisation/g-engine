import unittest

from g import (
    SpriteAnimator,
    CircleRenderer,
    Game,
    GameObject,
    Image,
    LineRenderer,
    PolygonRenderer,
    RectRenderer,
    ShapeStyle,
    SpriteRenderer,
    StatefulGameObject,
    TextRenderer,
    TriangleRenderer,
    Vec2,
    create_circle,
    create_line,
    create_polygon,
    create_rect,
    create_sprite,
    create_stateful_sprite,
    create_text,
    create_triangle,
)


class GameFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(640, 480)

    def assert_added(self, game_object) -> None:
        self.assertIs(game_object.parent, self.game.root)
        self.assertIn(game_object, self.game.objects)

    def test_shape_helpers_use_world_pixel_coordinates(self) -> None:
        circle = self.game.add_circle(100, 120, (255, 255, 255), 12)
        rect = self.game.add_rect(
            200,
            220,
            (255, 0, 0),
            40,
            30,
            angle=15,
        )

        self.assertIs(type(circle), GameObject)
        self.assertIsInstance(circle.require_component(CircleRenderer), CircleRenderer)
        self.assertEqual(circle.position, Vec2(100, 120))
        self.assertEqual(circle.require_component(CircleRenderer).radius, 12)
        self.assert_added(circle)

        self.assertIs(type(rect), GameObject)
        self.assertIsInstance(rect.require_component(RectRenderer), RectRenderer)
        self.assertEqual(rect.position, Vec2(200, 220))
        renderer = rect.require_component(RectRenderer)
        self.assertEqual((renderer.width, renderer.height), (40, 30))
        self.assertEqual(rect.angle, 15)
        self.assert_added(rect)

    def test_image_and_text_helpers(self) -> None:
        image = Image.solid(8, 6, (255, 255, 255))

        sprite = self.game.add_sprite(50, 60, image)
        text = self.game.add_text(
            70,
            80,
            "Score",
            font_size=20,
            screen_space=True,
        )
        stateful = self.game.add_stateful_sprite(
            90,
            100,
            {"idle": image, "active": image},
            "idle",
        )

        self.assertIs(type(sprite), GameObject)
        self.assertIsInstance(sprite.require_component(SpriteRenderer), SpriteRenderer)
        self.assertEqual(sprite.position, Vec2(50, 60))
        self.assertIs(type(text), GameObject)
        self.assertIsInstance(text.require_component(TextRenderer), TextRenderer)
        self.assertEqual(text.position, Vec2(70, 80))
        self.assertTrue(text.require_component(TextRenderer).screen_space)
        self.assertIs(type(stateful), GameObject)
        self.assertIsInstance(stateful.require_component(SpriteAnimator), SpriteAnimator)
        self.assertEqual(stateful.position, Vec2(90, 100))
        self.assert_added(sprite)
        self.assert_added(text)
        self.assert_added(stateful)

    def test_stateful_game_object_helper(self) -> None:
        idle = create_rect(10, 20, (255, 255, 255), 10, 10)
        active = create_rect(10, 20, (255, 0, 0), 10, 10)

        stateful = self.game.add_stateful_game_object(
            {"idle": idle, "active": active},
            "idle",
        )

        self.assertIsInstance(stateful, StatefulGameObject)
        self.assertEqual(stateful.position, Vec2(10, 20))
        self.assert_added(stateful)

    def test_primitive_helpers(self) -> None:
        line = self.game.add_line((0, 0), (100, 50), thickness=3)
        triangle = self.game.add_triangle(
            (0, 0),
            (20, 0),
            (10, 20),
        )
        polygon = self.game.add_polygon(
            [(0, 0), (20, 0), (25, 10), (10, 20)],
        )

        self.assertIs(type(line), GameObject)
        self.assertEqual(line.require_component(LineRenderer).thickness, 3)
        self.assertIs(type(triangle), GameObject)
        self.assertIsInstance(triangle.require_component(TriangleRenderer), TriangleRenderer)
        self.assertIs(type(polygon), GameObject)
        self.assertIsInstance(polygon.require_component(PolygonRenderer), PolygonRenderer)
        self.assert_added(line)
        self.assert_added(triangle)
        self.assert_added(polygon)

    def test_scene_exposes_the_same_pixel_factory_surface(self) -> None:
        scene = self.game.scenes.current

        circle = scene.add_circle(30, 40, (255, 255, 255), 5)

        self.assertEqual(circle.position, Vec2(30, 40))
        self.assert_added(circle)

    def test_shape_helpers_propagate_styles(self) -> None:
        style = ShapeStyle(
            fill=None,
            outline=(10, 20, 30),
            outline_thickness=4,
        )

        circle = self.game.add_circle(
            30, 40, (255, 255, 255), 5, style=style
        )
        triangle = self.game.add_triangle(
            (0, 0), (20, 0), (10, 20), style=style
        )
        grid_rect = self.game.grid.add_rect(
            1, 2, (255, 255, 255), style=style
        )

        for shape, renderer_type in (
            (circle, CircleRenderer),
            (triangle, TriangleRenderer),
            (grid_rect, RectRenderer),
        ):
            renderer = shape.require_component(renderer_type)
            self.assertIsNone(renderer.fill_color)
            self.assertEqual(renderer.outline_color, (10, 20, 30, 255))
            self.assertEqual(renderer.outline_thickness, 4)

    def test_root_factories_create_plain_composed_game_objects(self) -> None:
        image = Image.solid(8, 6, (255, 255, 255))
        objects = (
            (create_circle(1, 2, (255, 255, 255), 3), CircleRenderer),
            (create_rect(1, 2, (255, 255, 255), 3, 4), RectRenderer),
            (create_sprite(image, 1, 2), SpriteRenderer),
            (create_text("Score", 1, 2), TextRenderer),
            (create_line((0, 0), (1, 1)), LineRenderer),
            (create_triangle((0, 0), (1, 0), (0, 1)), TriangleRenderer),
            (create_polygon([(0, 0), (1, 0), (0, 1)]), PolygonRenderer),
            (
                create_stateful_sprite(
                    {"idle": image, "active": image}, "idle", 1, 2
                ),
                SpriteAnimator,
            ),
        )
        for game_object, component_type in objects:
            with self.subTest(component=component_type.__name__):
                self.assertIs(type(game_object), GameObject)
                self.assertIsInstance(
                    game_object.require_component(component_type), component_type
                )


if __name__ == "__main__":
    unittest.main()
