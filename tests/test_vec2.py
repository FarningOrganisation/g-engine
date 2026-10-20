import unittest
from math import pi

from pyglet.math import Vec2 as PygletVec2

from g import GameObject, Node, Vec2


class Vec2Tests(unittest.TestCase):
    def test_pyglet_vector_is_exposed(self) -> None:
        vector = Vec2(3, 4)
        self.assertEqual(vector, PygletVec2(3, 4))
        self.assertEqual(vector.length(), 5)
        self.assertEqual(vector + Vec2(1, 2), Vec2(4, 6))
        self.assertIsInstance(vector * 2, Vec2)
        self.assertAlmostEqual(vector.normalize().length(), 1)

    def test_position_reads_current_coordinates(self) -> None:
        obj = GameObject(3, 4)
        self.assertIsInstance(obj.position, Vec2)
        self.assertEqual(obj.position, Vec2(3, 4))
        obj.x = 10
        obj.y = 20
        self.assertEqual(obj.position, Vec2(10, 20))
        obj.position = (30, 40)
        self.assertEqual(obj.position, Vec2(30, 40))

    def test_vector_assignment_updates_coordinates(self) -> None:
        obj = GameObject()
        obj.position = Vec2(10, 20)
        self.assertEqual((obj.x, obj.y), (10, 20))
        self.assertIsInstance(obj.position, Vec2)
        x, y = obj.position
        self.assertEqual((x, y), (10, 20))

    def test_tuple_assignment_returns_vector(self) -> None:
        obj = GameObject()
        obj.position = (10, 20)
        self.assertIsInstance(obj.position, Vec2)
        self.assertEqual(obj.position, Vec2(10, 20))

    def test_augmented_assignment_moves_object(self) -> None:
        obj = GameObject(10, 20)
        previous_position = obj.position
        obj.position += Vec2(3, -4) * 2
        self.assertEqual(obj.position, Vec2(16, 12))
        self.assertEqual(previous_position, Vec2(10, 20))

    def test_position_remains_local_to_parent(self) -> None:
        parent = Node(100, 200, scale_x=2, scale_y=3)
        child = GameObject()
        parent.add_child(child)
        child.position = Vec2(5, 10)
        self.assertEqual(child.position, Vec2(5, 10))
        self.assertIsInstance(child.world_position, Vec2)
        self.assertEqual(child.world_position, Vec2(110, 230))
        self.assertEqual(parent.position, Vec2(100, 200))

    def test_unparented_world_position_is_vector(self) -> None:
        obj = GameObject(3, 4)
        self.assertIsInstance(obj.world_position, Vec2)
        self.assertEqual(obj.world_position, obj.position)

    def test_rotated_parent_world_position(self) -> None:
        parent = Node(100, 200, rotation=90, scale_x=2, scale_y=3)
        child = GameObject(5, 10)
        parent.add_child(child)
        self.assertIsInstance(child.world_position, Vec2)
        self.assertAlmostEqual(child.world_position.x, 130)
        self.assertAlmostEqual(child.world_position.y, 190)

    def test_world_position_can_be_written_through_parent_transform(
        self,
    ) -> None:
        parent = Node(
            100,
            200,
            rotation=90,
            scale_x=2,
            scale_y=3,
        )
        child = GameObject()
        parent.add_child(child)

        child.world_position = (130, 190)

        self.assertAlmostEqual(child.position.x, 5)
        self.assertAlmostEqual(child.position.y, 10)
        self.assertAlmostEqual(child.world_position.x, 130)
        self.assertAlmostEqual(child.world_position.y, 190)

    def test_world_rotation_can_be_written_relative_to_parent(self) -> None:
        parent = Node(rotation=30)
        child = GameObject(rotation=10)
        parent.add_child(child)

        child.world_rotation = 90

        self.assertEqual(child.rotation, 60)
        self.assertEqual(child.world_rotation, 90)

    def test_translate_supports_local_and_world_spaces(self) -> None:
        local = GameObject(rotation=90)
        local.translate(10, 0, space="local")
        self.assertAlmostEqual(local.x, 0)
        self.assertAlmostEqual(local.y, -10)

        parent = Node(100, 200, rotation=90, scale_x=2, scale_y=3)
        world = GameObject()
        parent.add_child(world)
        world.translate(10, -5, space="world")
        self.assertEqual(world.world_position, Vec2(110, 195))

        with self.assertRaisesRegex(ValueError, "transform space"):
            world.translate(1, 2, space="screen")

    def test_set_world_transform_updates_position_and_rotation(self) -> None:
        parent = Node(100, 200, rotation=30)
        child = GameObject()
        parent.add_child(child)

        child.set_world_transform(
            position=(300, 400),
            rotation=75,
        )

        self.assertEqual(child.world_position, Vec2(300, 400))
        self.assertEqual(child.world_rotation, 75)

    def test_pos_alias_is_removed(self) -> None:
        self.assertFalse(hasattr(GameObject(), "pos"))

    def test_vector_coordinates_are_writable(self) -> None:
        vector = Vec2(3, 4)
        vector.x = 10
        self.assertEqual(vector, Vec2(10, 4))
        vector.y += 2
        self.assertEqual(vector, Vec2(10, 6))
        self.assertEqual(tuple(vector), (10, 6))
        self.assertEqual(vector[0], 10)
        self.assertEqual(vector[-1], 6)
        self.assertEqual(vector[:], (10, 6))
        self.assertEqual(vector.yx, Vec2(6, 10))
        self.assertEqual(vector.dot((1, 1)), 16)

    def test_position_coordinate_mutation_moves_object(self) -> None:
        obj = GameObject(3, 4)
        position = obj.position
        obj.position.x = 10
        obj.position.y += 2
        self.assertEqual((obj.x, obj.y), (10, 6))
        obj.x = 30
        obj.y = 40
        self.assertEqual(position, Vec2(30, 40))

    def test_copy_preserves_saved_position(self) -> None:
        obj = GameObject(3, 4)
        previous = obj.position.copy()
        obj.x += 10
        obj.position.y += 20
        self.assertEqual(previous, Vec2(3, 4))
        obj.position = previous
        self.assertEqual(obj.position, Vec2(3, 4))
        previous.x = -1
        self.assertEqual(obj.x, 3)

    def test_assigning_position_does_not_share_vectors(self) -> None:
        vector = Vec2(3, 4)
        first, second = GameObject(), GameObject()
        first.position = vector
        second.position = first.position
        vector.x = 10
        first.position.y = 20
        self.assertEqual(first.position, Vec2(3, 20))
        self.assertEqual(second.position, Vec2(3, 4))

    def test_world_position_is_a_detached_snapshot(self) -> None:
        for parent in (None, Node(10, 20)):
            obj = GameObject(3, 4)
            obj.parent = parent
            world = obj.world_position
            world.x = 100
            world.y = 200
            self.assertEqual(obj.position, Vec2(3, 4))

    def test_mutable_vectors_are_unhashable(self) -> None:
        with self.assertRaises(TypeError):
            hash(Vec2(3, 4))

    def test_arithmetic_preserves_helpers(self) -> None:
        vector = Vec2(3, 4)
        results = (
            vector + (1, 2), (1, 2) + vector,
            vector - (1, 2), (1, 2) - vector,
            vector * 2, 2 * vector,
            vector / 2, 12 / vector,
            vector // 2, 12 // vector,
            vector % 2, vector ** 2,
            -vector, abs(vector), round(vector),
            vector.__ceil__(), vector.__floor__(), vector.__trunc__(),
            sum((vector, vector)),
        )
        for result in results:
            with self.subTest(result=result):
                self.assertIsInstance(result, Vec2)
                self.assertIsInstance(result.perpendicular(), Vec2)
        self.assertEqual((vector + Vec2(1, 2)).project((1, 0)), Vec2(4, 0))

    def test_pyglet_math_preserves_extended_type(self) -> None:
        vector = Vec2(3, 4)
        results = (
            vector.normalize(), Vec2().normalize(), vector.rotate(pi / 2),
            vector.lerp((5, 6), 0.5), vector.step((1, 1)),
            vector.clamp(0, 3), vector.clamp((0, 0), (3, 3)),
            Vec2.from_heading(0), Vec2.from_polar(0),
        )
        for result in results:
            self.assertIsInstance(result, Vec2)
        self.assertEqual(vector.dot((1, 2)), 11)
        self.assertEqual(vector.length_squared(), 25)
        self.assertEqual(vector.distance((0, 0)), 5)
        self.assertEqual(Vec2().normalize(), Vec2())

    def test_reflect_normalizes_normal(self) -> None:
        velocity = Vec2(3, -4)
        self.assertEqual(velocity.reflect((0, 1)), Vec2(3, 4))
        self.assertEqual(velocity.reflect((0, 10)), Vec2(3, 4))
        self.assertEqual(velocity.reflect((0, -10)), Vec2(3, 4))
        self.assertEqual(velocity.reflect((1, 0)), Vec2(-3, -4))
        self.assertEqual(velocity.reflect((-1, 0)), Vec2(-3, -4))
        diagonal = velocity.reflect((1, 1))
        self.assertAlmostEqual(diagonal.x, 4)
        self.assertAlmostEqual(diagonal.y, -3)
        self.assertAlmostEqual(diagonal.length(), velocity.length())
        self.assertEqual(velocity, Vec2(3, -4))

    def test_project_onto_direction(self) -> None:
        vector = Vec2(3, 4)
        self.assertEqual(vector.project((10, 0)), Vec2(3, 0))
        self.assertEqual(vector.project((-10, 0)), Vec2(3, 0))
        self.assertEqual(vector.project((0, 2)), Vec2(0, 4))
        projection = vector.project((1, 1))
        self.assertAlmostEqual(projection.x, 3.5)
        self.assertAlmostEqual(projection.y, 3.5)
        self.assertAlmostEqual((vector - projection).dot((1, 1)), 0)
        self.assertEqual(Vec2().project((1, 0)), Vec2())

    def test_project_on_infinite_line(self) -> None:
        self.assertEqual(Vec2(3, 4).project_on_line((0, 1), (10, 1)), Vec2(3, 1))
        self.assertEqual(Vec2(3, 4).project_on_line((1, 0), (1, 10)), Vec2(1, 4))
        self.assertEqual(Vec2(20, 4).project_on_line((0, 1), (10, 1)), Vec2(20, 1))
        result = Vec2(4, 1).project_on_line((1, 1), (3, 3))
        self.assertAlmostEqual(result.x, 2.5)
        self.assertAlmostEqual(result.y, 2.5)

    def test_degenerate_lines_and_normals_raise(self) -> None:
        vector = Vec2(3, 4)
        with self.assertRaises(ValueError):
            vector.reflect(Vec2())
        with self.assertRaises(ValueError):
            vector.project(Vec2())
        with self.assertRaises(ValueError):
            vector.project_on_line((1, 2), (1, 2))

    def test_perpendicular_and_cross(self) -> None:
        vector = Vec2(3, 4)
        normal = vector.perpendicular()
        self.assertEqual(normal, Vec2(-4, 3))
        self.assertEqual(vector.dot(normal), 0)
        self.assertEqual(vector.length(), normal.length())
        self.assertEqual(Vec2(1, 0).cross((0, 1)), 1)
        self.assertEqual(Vec2(0, 1).cross((1, 0)), -1)
        self.assertEqual(vector.cross(vector), 0)

    def test_move_towards_never_overshoots(self) -> None:
        origin = Vec2()
        self.assertEqual(origin.move_towards((3, 4), 2.5), Vec2(1.5, 2))
        self.assertEqual(origin.move_towards((3, 4), 5), Vec2(3, 4))
        self.assertEqual(origin.move_towards((3, 4), 10), Vec2(3, 4))
        self.assertEqual(origin.move_towards((3, 4), 0), origin)
        self.assertEqual(origin.move_towards(origin, 0), origin)
        for distance in (-1, float("nan"), float("inf")):
            with self.subTest(distance=distance), self.assertRaises(ValueError):
                origin.move_towards((3, 4), distance)


if __name__ == "__main__":
    unittest.main()
