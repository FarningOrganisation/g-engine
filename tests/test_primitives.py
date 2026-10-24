import unittest
from unittest.mock import Mock, patch

from g import (
    Game,
    GameObject,
    LineRenderer,
    PolygonRenderer,
    CircleRenderer,
    RectRenderer,
    ShapeStyle,
    TriangleRenderer,
    Vec2,
    create_circle,
    create_line,
    create_polygon,
    create_rect,
    create_triangle,
)


class PrimitiveTests(unittest.TestCase):
    def test_shape_style_rejects_invisible_or_invalid_outlines(self) -> None:
        with self.assertRaisesRegex(ValueError, "fill or outline"):
            ShapeStyle(fill=None, outline=None)
        for thickness in (0, -1, float("inf"), float("nan")):
            with self.subTest(thickness=thickness):
                with self.assertRaisesRegex(
                    ValueError, "finite and positive"
                ):
                    ShapeStyle(outline_thickness=thickness)

    def test_shapes_support_outline_only_rendering(self) -> None:
        style = ShapeStyle(
            fill=None,
            outline=(240, 80, 40),
            outline_thickness=3,
        )
        shapes = (
            create_circle(20, 20, 10, (255, 255, 255), style=style),
            create_rect(20, 20, 20, 10, (255, 255, 255), style=style),
            create_triangle((0, 0), (20, 0), (10, 20), style=style),
            create_polygon([(0, 0), (20, 0), (20, 20), (0, 20)], style=style),
        )

        for shape in shapes:
            with self.subTest(shape=type(shape).__name__):
                renderer = next(
                    component
                    for component in shape.components
                    if isinstance(
                        component,
                        (CircleRenderer, RectRenderer, TriangleRenderer, PolygonRenderer),
                    )
                )
                self.assertIs(type(shape), GameObject)
                renderer.sync()
                self.assertFalse(renderer.fill_drawable.visible)
                self.assertTrue(
                    all(
                        drawable.visible
                        for drawable in renderer.outline_drawables
                    )
                )
                self.assertGreater(len(renderer.drawables), 1)

    def test_fill_and_outline_properties_are_mutable(self) -> None:
        circle = create_circle(
            10,
            20,
            5,
            (1, 2, 3),
            style=ShapeStyle(
                fill=(10, 20, 30),
                outline=(40, 50, 60),
                outline_thickness=2,
            ),
        )
        circle.scale_x = 3
        renderer = circle.require_component(CircleRenderer)
        renderer.fill_color = (70, 80, 90)
        renderer.outline_color = (100, 110, 120)
        renderer.outline_thickness = 4
        renderer.sync()

        self.assertEqual(renderer.color, (70, 80, 90, 255))
        self.assertEqual(renderer.fill_color, (70, 80, 90, 255))
        self.assertEqual(renderer.outline_color, (100, 110, 120, 255))
        self.assertTrue(renderer.fill_drawable.visible)
        self.assertTrue(renderer.outline_drawables[0].visible)
        self.assertEqual(renderer.outline_drawables[0].thickness, 12)

        renderer.fill_visible = False
        renderer.outline_visible = False
        renderer.sync()
        self.assertFalse(renderer.fill_drawable.visible)
        self.assertFalse(renderer.outline_drawables[0].visible)

    def test_scene_batches_and_deletes_all_shape_drawables(self) -> None:
        game = Game(320, 240)
        circle = game.add_circle(
            20,
            30,
            10,
            (255, 255, 255),
            style=ShapeStyle(outline=(255, 0, 0)),
        )

        renderer = circle.require_component(CircleRenderer)
        for drawable in renderer.drawables:
            self.assertIs(drawable.batch, game._batch)
            self.assertIsNotNone(drawable.group)

        with (
            patch.object(renderer.drawables[0], "delete") as delete_fill,
            patch.object(renderer.drawables[1], "delete") as delete_outline,
        ):
            game.remove(circle)
            delete_fill.assert_called_once_with()
            delete_outline.assert_called_once_with()

    @patch("g.components.line_renderer.pyglet.shapes.Line")
    def test_line_is_centered_and_syncs_endpoints(self, line_type: Mock) -> None:
        drawable = line_type.return_value
        line = create_line((10, 20), (30, 40), thickness=3)
        renderer = line.require_component(LineRenderer)

        self.assertEqual(line.position, Vec2(20, 30))
        self.assertEqual(renderer.start, (-10, -10))
        self.assertEqual(renderer.end, (10, 10))

        renderer.sync()

        self.assertEqual((drawable.x, drawable.y), (10, 20))
        self.assertEqual((drawable.x2, drawable.y2), (30, 40))
        self.assertEqual(drawable.thickness, 3)

    @patch("g.components.line_renderer.pyglet.shapes.Line")
    def test_line_thickness_scales_and_is_mutable(
        self, line_type: Mock
    ) -> None:
        drawable = line_type.return_value
        line = create_line((0, 0), (10, 0), thickness=2)
        renderer = line.require_component(LineRenderer)
        line.scale_y = 3
        renderer.thickness = 4

        renderer.sync()

        self.assertEqual(renderer.thickness, 4)
        self.assertEqual(drawable.thickness, 12)
        with self.assertRaisesRegex(ValueError, "thickness must be positive"):
            renderer.thickness = 0

    @patch("g.components.triangle_renderer.pyglet.shapes.Triangle")
    def test_triangle_is_centered_and_syncs_points(
        self, triangle_type: Mock
    ) -> None:
        drawable = triangle_type.return_value
        triangle = create_triangle((0, 0), (12, 0), (6, 9))
        renderer = triangle.require_component(TriangleRenderer)

        self.assertEqual(triangle.position, Vec2(6, 3))

        renderer.sync()

        self.assertEqual(drawable.position, (0, 0))
        self.assertEqual((drawable.x2, drawable.y2), (12, 0))
        self.assertEqual((drawable.x3, drawable.y3), (6, 9))

    @patch("g.components.polygon_renderer.pyglet.shapes.Polygon")
    def test_polygon_is_centered_and_rebuilds_only_for_scale(
        self, polygon_type: Mock
    ) -> None:
        first_drawable = Mock()
        first_drawable.color = (10, 20, 30, 255)
        first_drawable.opacity = 255
        second_drawable = Mock()
        polygon_type.side_effect = (first_drawable, second_drawable)
        polygon = create_polygon(
            [(0, 0), (20, 0), (20, 20), (0, 20)],
            color=(10, 20, 30),
        )

        self.assertEqual(polygon.position, Vec2(10, 10))
        renderer = polygon.require_component(PolygonRenderer)

        renderer.sync()
        self.assertEqual(polygon_type.call_count, 1)

        polygon.scale_x = 2
        renderer.sync()

        self.assertEqual(polygon_type.call_count, 2)
        first_drawable.delete.assert_called_once_with()
        self.assertEqual(second_drawable.position, Vec2(10, 10))

    @patch("g.components.line_renderer.pyglet.shapes.Line")
    def test_renderer_can_use_screen_space(self, line_type: Mock) -> None:
        line = create_line((0, 0), (10, 0), screen_space=True)

        self.assertIs(type(line), GameObject)
        self.assertTrue(line.require_component(LineRenderer).screen_space)
        line_type.assert_called_once()

    def test_invalid_primitive_geometry_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "endpoints must be different"):
            LineRenderer((0, 0), (0, 0))
        with self.assertRaisesRegex(ValueError, "points must be different"):
            TriangleRenderer(((0, 0), (0, 0), (1, 1)))
        with self.assertRaisesRegex(ValueError, "at least three"):
            create_polygon([(0, 0), (1, 1)])


if __name__ == "__main__":
    unittest.main()
