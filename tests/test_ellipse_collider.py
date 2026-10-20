import tempfile
import unittest
from math import cos, sin, tau
from pathlib import Path

import pymunk

from g import Area, BodyType, Collider, ColliderMode, Game, GameObject, PhysicsBody, PlatformerMotor, SceneSerializer, TopDownMotor
from g.components.renderer_utils import _transform_point
from g.inspector import inspector_fields
from g.persistence import scene_types


class ColliderShapeTests(unittest.TestCase):
    def test_geometry_and_physics_use_the_same_transformed_outline(self) -> None:
        game = Game(320, 180)
        parent = game.add(GameObject(40, 50))
        parent.rotation = 25
        parent.scale_x, parent.scale_y = -2, 3
        owner = GameObject(10, -4)
        parent.add_child(owner)
        owner.rotation = 15
        collider = owner.add_component(Collider(shape="ellipse", radius_x=20, radius_y=8, offset_x=3, offset_y=-2))
        self.assertEqual(len(collider.geometry.points), 32)
        expected_outline = tuple(
            (20 * cos(tau * index / 32), 8 * sin(tau * index / 32))
            for index in range(32)
        )
        for point, expected in zip(collider.geometry.points, expected_outline):
            world = _transform_point(owner, (expected[0] + 3, expected[1] - 2))
            self.assertAlmostEqual(point[0], world[0])
            self.assertAlmostEqual(point[1], world[1])
        center = _transform_point(owner, (3, -2))
        self.assertTrue(collider.contains_point(*center))
        self.assertFalse(collider.contains_point(1000, 1000))
        game.physics.refresh()
        self.assertIsInstance(collider._shape, pymunk.Poly)
        assert isinstance(collider._shape, pymunk.Poly)
        self.assertEqual(len(collider._shape.get_vertices()), 32)
        physical_points = [
            collider._shape.body.local_to_world(vertex)
            for vertex in collider._shape.get_vertices()
        ]
        for x, y in collider.geometry.points:
            self.assertLess(min((x - p.x) ** 2 + (y - p.y) ** 2 for p in physical_points), 1e-14)
        self.assertIn(owner, game.physics.overlap_point(center))

    def test_radius_edits_rebuild_physics_and_support_area_modes(self) -> None:
        game = Game(320, 180)
        owner = game.add(GameObject(40, 40))
        collider = owner.add_component(Collider(shape="ellipse", radius_x=10, radius_y=5, mode=ColliderMode.SENSOR))
        owner.add_component(Area())
        owner.add_component(PhysicsBody(body_type=BodyType.DYNAMIC))
        game.physics.refresh()
        old_shape = collider._shape
        self.assertNotIn(owner, game.physics.overlap_point((60, 40)))
        collider.radius_x = 30
        game.physics.refresh()
        self.assertIsNot(collider._shape, old_shape)
        self.assertIn(owner, game.physics.overlap_point((60, 40)))
        assert collider._shape is not None
        self.assertTrue(collider._shape.sensor)
        self.assertGreater(collider._shape.mass, 0)

    def test_round_trip_and_inspector_template(self) -> None:
        game = Game(100, 100)
        owner = game.add(GameObject())
        owner.add_component(Collider(
            shape="ellipse", width=73, height=51, radius=19,
            radius_x=45, radius_y=12, points=((-8, -6), (8, -6), (0, 9)),
            offset_x=5, offset_y=-7,
            mode=ColliderMode.QUERY_ONLY, categories=2, mask=4, group=3,
            friction=0.2, elasticity=0.4, density=2, enabled=False,
        ))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.scene.json"
            SceneSerializer().save(game.scenes.current, path)
            restored = SceneSerializer().load(game, path)
        collider = restored.objects[0].require_component(Collider)
        self.assertEqual(collider.shape, "ellipse")
        self.assertEqual((collider.width, collider.height, collider.radius), (73, 51, 19))
        self.assertEqual((collider.radius_x, collider.radius_y), (45, 12))
        self.assertEqual(collider.points, ((-8, -6), (8, -6), (0, 9)))
        self.assertEqual((collider.offset_x, collider.offset_y), (5, -7))
        self.assertEqual(collider.mode, ColliderMode.QUERY_ONLY)
        self.assertEqual((collider.categories, collider.mask, collider.group), (2, 4, 3))
        self.assertTrue(collider.override_collision_filtering)
        self.assertFalse(collider.enabled)
        self.assertEqual(collider.density, 2)
        self.assertEqual(collider.friction, 0.2)
        self.assertEqual(collider.elasticity, 0.4)
        self.assertIn("radius_x", inspector_fields(Collider))
        template = scene_types.component_template("g.collider")
        properties = template["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["shape"], "box")
        self.assertEqual(properties["radius_x"], 32)
        self.assertEqual(properties["radius_y"], 16)

    def test_rejects_invalid_radii_at_construction_and_assignment(self) -> None:
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "radius"):
                    Collider(shape="ellipse", radius_x=value, radius_y=1)
                ellipse = Collider()
                with self.assertRaisesRegex(ValueError, "radius"):
                    ellipse.radius_y = value

    def test_motors_accept_ellipse_and_existing_circles_keep_their_shape(self) -> None:
        for shape in ("box", "circle", "ellipse", "polygon"):
            for motor_type in (PlatformerMotor, TopDownMotor):
                with self.subTest(shape=shape, motor=motor_type):
                    owner = GameObject()
                    owner.add_component(Collider(shape=shape))
                    owner.add_component(motor_type())
                    self.assertIsNotNone(owner.get_component(PhysicsBody))
        owner = GameObject()
        owner.scale_x, owner.scale_y = 2, 4
        circle = owner.add_component(Collider(shape="circle", radius=10))
        self.assertEqual(circle.geometry.radius, 40)

    def test_shape_switch_reuses_collider_area_and_body(self) -> None:
        game = Game(320, 180)
        owner = game.add(GameObject(40, 40))
        collider = owner.add_component(Collider(shape="circle", radius=12))
        area = owner.add_component(Area())
        body = owner.add_component(PhysicsBody(body_type=BodyType.DYNAMIC))
        game.physics.refresh()
        self.assertIsInstance(collider._shape, pymunk.Circle)
        self.assertIs(area.collider, collider)
        self.assertIs(collider._shape.body, body._body)
        for shape in ("ellipse", "polygon", "box"):
            with self.subTest(shape=shape):
                old_shape = collider._shape
                collider.shape = shape
                game.physics.refresh()
                self.assertIsNot(collider._shape, old_shape)
                self.assertIsInstance(collider._shape, pymunk.Poly)
                self.assertIs(area.collider, collider)
                self.assertIs(collider._shape.body, body._body)

    def test_direct_intersection_uses_new_shape_before_physics_refresh(self) -> None:
        owner = GameObject()
        collider = owner.add_component(
            Collider(shape="box", width=40, height=20)
        )
        target = GameObject(15, 8)
        other = target.add_component(Collider(shape="circle", radius=1))

        self.assertTrue(collider.intersects(other))
        collider.shape = "circle"
        collider.radius = 4

        self.assertFalse(collider.intersects(other))


if __name__ == "__main__":
    unittest.main()
