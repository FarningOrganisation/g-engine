import unittest
from unittest.mock import Mock, patch

from g import (
    Collider,
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
    TextureFilter,
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
        circle = self.game.add_circle(100, 120, 12, (255, 255, 255))
        rect = self.game.add_rect(
            200,
            220,
            40,
            30,
            (255, 0, 0),
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

    def test_shape_helpers_default_to_white(self) -> None:
        scene = self.game.scenes.current
        shapes = (
            (self.game.add_circle(10, 10, 5), CircleRenderer),
            (self.game.add_rect(20, 20, 8, 6), RectRenderer),
            (scene.add_circle(30, 30, 5), CircleRenderer),
            (scene.add_rect(40, 40, 8, 6), RectRenderer),
            (self.game.grid.add_circle(1, 1), CircleRenderer),
            (self.game.grid.add_rect(2, 2), RectRenderer),
            (create_circle(50, 50, 5), CircleRenderer),
            (create_rect(60, 60, 8, 6), RectRenderer),
        )

        for shape, renderer_type in shapes:
            with self.subTest(shape=type(shape).__name__):
                self.assertEqual(
                    shape.require_component(renderer_type).color,
                    (255, 255, 255, 255),
                )

    def test_direct_draw_helpers_render_and_delete_primitives(self) -> None:
        constructors = {
            name: patch(f"g.game.pyglet.shapes.{name}")
            for name in ("Circle", "Rectangle", "Line", "Triangle", "Polygon")
        }
        mocks = {name: manager.start() for name, manager in constructors.items()}
        self.addCleanup(
            lambda: [manager.stop() for manager in constructors.values()]
        )

        def draw() -> None:
            self.game.draw_circle(10, 20, 5, (1, 2, 3))
            self.game.draw_rect(30, 40, 20, 10, (4, 5, 6), angle=15)
            self.game.draw_line((1, 2), (3, 4), (7, 8, 9), thickness=2)
            self.game.draw_triangle(
                (0, 0), (10, 0), (5, 8), (10, 11, 12)
            )
            self.game.draw_polygon(
                ((0, 0), (10, 0), (10, 10), (0, 10)),
                (13, 14, 15),
            )

        self.game._draw_handler = draw
        with patch.object(
            self.game._direct_draw_batch,
            "draw",
        ) as draw_batch:
            self.game._draw_direct()
            self.game._draw_direct()
        self.assertEqual(draw_batch.call_count, 2)

        mocks["Circle"].assert_called_once_with(
            10, 20, 5, color=(1, 2, 3),
            batch=self.game._direct_draw_batch,
        )
        mocks["Rectangle"].assert_called_once_with(
            30, 40, 20, 10, color=(4, 5, 6),
            batch=self.game._direct_draw_batch,
        )
        rectangle = mocks["Rectangle"].return_value
        self.assertEqual(rectangle.anchor_position, (10, 5))
        self.assertEqual(rectangle.rotation, 15)
        mocks["Line"].assert_called_once_with(
            1, 2, 3, 4, thickness=2, color=(7, 8, 9),
            batch=self.game._direct_draw_batch,
        )
        mocks["Triangle"].assert_called_once_with(
            0, 0, 10, 0, 5, 8, color=(10, 11, 12),
            batch=self.game._direct_draw_batch,
        )
        mocks["Polygon"].assert_called_once_with(
            (0, 0),
            (10, 0),
            (10, 10),
            (0, 10),
            color=(13, 14, 15),
            batch=self.game._direct_draw_batch,
        )
        for constructor in mocks.values():
            constructor.return_value.draw.assert_not_called()
            constructor.return_value.delete.assert_not_called()

        self.game._clear_direct_draw_cache()

        for constructor in mocks.values():
            constructor.return_value.delete.assert_called_once_with()

    @patch("g.game.pyglet.shapes.Arc")
    @patch("g.game.pyglet.shapes.Circle")
    def test_direct_circle_batches_fill_and_outline(
        self,
        circle_type: Mock,
        arc_type: Mock,
    ) -> None:
        style = ShapeStyle(
            fill=(1, 2, 3),
            outline=(4, 5, 6),
            outline_thickness=3,
        )

        self.game._draw_handler = lambda: self.game.draw_circle(
            10, 20, 5, style=style
        )
        self.game._draw_direct()

        arc_type.assert_called_once_with(
            10,
            20,
            5,
            closed=True,
            thickness=3,
            color=(4, 5, 6),
            batch=self.game._direct_draw_batch,
        )
        circle_type.return_value.draw.assert_not_called()
        arc_type.return_value.draw.assert_not_called()
        self.game._clear_direct_draw_cache()
        self.assertEqual(
            [circle_type.return_value.delete.call_count,
             arc_type.return_value.delete.call_count],
            [1, 1],
        )

    def test_direct_draw_helpers_validate_geometry(self) -> None:
        invalid_calls = (
            lambda: self.game.draw_circle(0, 0, -1),
            lambda: self.game.draw_rect(0, 0, -1, 1),
            lambda: self.game.draw_line((0, 0), (0, 0)),
            lambda: self.game.draw_line((0, 0), (1, 0), thickness=0),
            lambda: self.game.draw_triangle((0, 0), (0, 0), (1, 0)),
            lambda: self.game.draw_polygon(((0, 0), (1, 0))),
        )

        for invalid_call in invalid_calls:
            with self.subTest(call=invalid_call), self.assertRaises(ValueError):
                invalid_call()

    def test_direct_draw_helpers_reject_headless_games(self) -> None:
        game = Game(10, 10, presentation_enabled=False)

        with self.assertRaisesRegex(RuntimeError, "presentation is disabled"):
            game.draw_circle(5, 5, 2)

    def test_direct_draw_helpers_require_draw_callback(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "only be called"):
            self.game.draw_circle(5, 5, 2)

    @patch("g.game.source_of")
    @patch("g.game.pyglet.sprite.Sprite")
    def test_image_draw_reuses_sprite_between_frames(
        self,
        sprite_type: Mock,
        source_of: Mock,
    ) -> None:
        image = Image.__new__(Image)
        image._source = None
        image._width = 18
        image._height = 18
        image.filtering = TextureFilter.LINEAR
        source = source_of.return_value
        source.get_texture.return_value.width = 18
        source.get_texture.return_value.height = 18
        sprite = sprite_type.return_value
        sprite.z = 0
        self.game._draw_handler = lambda: image.draw(
            20,
            30,
            width=36,
            height=9,
        )

        with patch.object(
            self.game._direct_draw_batch,
            "draw",
        ) as draw_batch:
            self.game._draw_direct()
            self.game._draw_direct()

        source_of.assert_called_once_with(image)
        source.blit.assert_not_called()
        sprite_type.assert_called_once()
        self.assertEqual(sprite.position, (20, 30, 0))
        self.assertEqual(sprite.scale_x, 2)
        self.assertEqual(sprite.scale_y, 0.5)
        sprite.draw.assert_not_called()
        self.assertEqual(draw_batch.call_count, 2)

    @patch("g.game.pyglet.shapes.Line")
    def test_direct_draw_line_supports_dash_pattern(
        self,
        line_type: Mock,
    ) -> None:
        drawables = [Mock(), Mock(), Mock()]
        line_type.side_effect = drawables
        self.game._draw_handler = lambda: self.game.draw_line(
            (0, 0),
            (20, 0),
            thickness=2,
            dash_pattern=(5, 3),
        )

        with patch.object(
            self.game._direct_draw_batch,
            "draw",
        ) as draw_batch:
            self.game._draw_direct()

        self.assertEqual(
            [
                (drawable.x, drawable.y, drawable.x2, drawable.y2)
                for drawable in drawables
            ],
            [
                (0, 0, 5, 0),
                (8, 0, 13, 0),
                (16, 0, 20, 0),
            ],
        )
        for drawable in drawables:
            drawable.draw.assert_not_called()
        draw_batch.assert_called_once_with()

    @patch("g.game.pyglet.shapes.Circle")
    def test_direct_draw_removes_commands_missing_from_next_frame(
        self,
        circle_type: Mock,
    ) -> None:
        self.game._draw_handler = lambda: self.game.draw_circle(5, 5, 2)
        self.game._draw_direct()
        self.game._draw_handler = None

        self.game._draw_direct()

        circle_type.return_value.delete.assert_called_once_with()

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

    def test_visual_factories_only_add_colliders_when_requested(self) -> None:
        image = Image.solid(8, 6, (255, 255, 255))

        visuals = (
            self.game.add_circle(10, 10, 5, (255, 255, 255)),
            self.game.add_rect(20, 20, 8, 6, (255, 255, 255)),
            self.game.add_sprite(30, 30, image),
        )
        physical = (
            self.game.add_circle(
                40,
                40,
                5,
                (255, 255, 255),
                collider=True,
            ),
            self.game.add_rect(
                50,
                50,
                8,
                6,
                (255, 255, 255),
                collider=True,
            ),
            self.game.add_sprite(60, 60, image, collider=True),
        )

        self.assertTrue(all(item.collider is None for item in visuals))
        self.assertTrue(
            all(isinstance(item.collider, Collider) for item in physical)
        )

    def test_sprite_helpers_resolve_resource_paths(self) -> None:
        image = Image.solid(8, 6, (255, 255, 255))
        with patch.object(
            self.game.resources,
            "load_image",
            return_value=image,
        ) as load_image:
            sprite = self.game.add_sprite(
                50,
                60,
                "player.png",
                collider=True,
            )

        load_image.assert_called_once_with("player.png", filtering=None)
        self.assertIs(
            sprite.require_component(SpriteRenderer).image,
            image,
        )
        self.assertIsInstance(sprite.collider, Collider)

    def test_stateful_game_object_helper(self) -> None:
        idle = create_rect(10, 20, 10, 10, (255, 255, 255))
        active = create_rect(10, 20, 10, 10, (255, 0, 0))

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

        circle = scene.add_circle(30, 40, 5, (255, 255, 255))

        self.assertEqual(circle.position, Vec2(30, 40))
        self.assert_added(circle)

    def test_shape_helpers_propagate_styles(self) -> None:
        style = ShapeStyle(
            fill=None,
            outline=(10, 20, 30),
            outline_thickness=4,
        )

        circle = self.game.add_circle(
            30,
            40,
            5,
            (255, 255, 255),
            style=style,
        )
        triangle = self.game.add_triangle(
            (0, 0), (20, 0), (10, 20), style=style
        )
        grid_rect = self.game.grid.add_rect(
            1,
            2,
            style=style,
            color=(255, 255, 255),
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
            (create_circle(1, 2, 3, (255, 255, 255)), CircleRenderer),
            (create_rect(1, 2, 3, 4, (255, 255, 255)), RectRenderer),
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
