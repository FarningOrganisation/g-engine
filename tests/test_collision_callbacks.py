import unittest
from math import hypot
from unittest.mock import Mock

from g import Collider, ColliderMode, CollisionContact, Game, GameObject, Script


class CollisionCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(200, 100)
        self.first = self.game.add_rect(
            50, 50, (255, 255, 255), 40, 40
        )
        self.second = self.game.add_circle(
            60, 50, (255, 255, 255), 10
        )

    def test_collision_begin_stay_and_end_callbacks(self) -> None:
        begin = Mock()
        other_begin = Mock()
        stay = Mock()
        end = Mock()
        self.first.on_collision(begin)
        self.second.on_collision(other_begin)
        self.first.on_collision_stay(stay)
        self.first.on_collision_end(end)

        self.game._update(0)
        contact = begin.call_args.args[0]
        reverse = other_begin.call_args.args[0]
        self.assertIs(contact.other, self.second)
        self.assertIs(reverse.other, self.first)
        begin.assert_called_once_with(contact)
        other_begin.assert_called_once_with(reverse)
        stay.assert_not_called()

        self.game._update(0)
        stay.assert_called_once_with(contact)
        begin.assert_called_once_with(contact)

        self.second.x = 150
        self.game._update(0)
        end.assert_called_once_with(contact)

    def test_collision_handler_can_be_used_as_a_decorator(self) -> None:
        collisions = []

        @self.first.on_collision
        def record(contact) -> None:
            collisions.append(contact.other)

        self.game._update(0)

        self.assertEqual(collisions, [self.second])

    def test_collision_handler_can_be_removed(self) -> None:
        handler = Mock()
        self.first.on_collision(handler)
        self.first.remove_collision_handler(handler)

        self.game._update(0)

        handler.assert_not_called()

    def test_contact_geometry_normals_angles_and_end_snapshot(self) -> None:
        game = Game(200, 100)
        first = game.add(GameObject(40, 40))
        second = game.add(GameObject(55, 40))
        first_collider = first.add_component(Collider(width=20, height=20))
        second_collider = second.add_component(Collider(width=20, height=20))
        begin, reverse, stay, end = Mock(), Mock(), Mock(), Mock()
        first.on_collision(begin)
        second.on_collision(reverse)
        first.on_collision_stay(stay)
        first.on_collision_end(end)
        game._update(0)
        contact = begin.call_args.args[0]
        opposite = reverse.call_args.args[0]
        self.assertIsInstance(contact, CollisionContact)
        self.assertIs(contact.other, second)
        self.assertIs(contact.collider, first_collider)
        self.assertIs(contact.other_collider, second_collider)
        self.assertEqual(contact.normal, (1, 0))
        self.assertAlmostEqual(hypot(*contact.normal), 1)
        self.assertAlmostEqual(contact.angle, 0)
        self.assertAlmostEqual(contact.penetration, 5)
        self.assertEqual(len(contact.points), 2)
        for x, y in contact.points:
            self.assertAlmostEqual(x, 47.5)
            self.assertIn(y, (30, 50))
        self.assertEqual(opposite.normal, (-1, 0))
        self.assertAlmostEqual(abs(opposite.angle), 180)
        self.assertIs(opposite.collider, second_collider)
        self.assertIs(opposite.other_collider, first_collider)
        self.assertEqual(opposite.points, contact.points)
        second.x = 57
        game._update(0)
        latest = stay.call_args.args[0]
        self.assertAlmostEqual(latest.penetration, 3)
        self.assertAlmostEqual(contact.penetration, 5)
        second.x = 150
        game._update(0)
        end.assert_called_once_with(latest)
        self.assertIs(end.call_args.args[0], latest)

    def test_script_and_registered_handler_receive_same_contact(self) -> None:
        class Capture(Script):
            contact: CollisionContact | None = None

            def on_collision(self, contact: CollisionContact) -> None:
                self.contact = contact

        script = self.first.add_component(Capture())
        registered = Mock()
        self.first.on_collision(registered)
        self.game._update(0)
        self.assertIs(script.contact, registered.call_args.args[0])

    def test_vertical_and_rotated_contacts_use_world_normal_angle(self) -> None:
        game = Game(200, 200)
        first = game.add(GameObject(60, 60))
        second = game.add(GameObject(60, 75))
        first.add_component(Collider(width=20, height=20))
        second.add_component(Collider(width=20, height=20))
        contacts = Mock()
        first.on_collision(contacts)
        first.on_collision_stay(contacts)
        game._update(0)
        contact = contacts.call_args.args[0]
        self.assertEqual(contact.normal, (0, 1))
        self.assertAlmostEqual(contact.angle, -90)
        first.rotation = second.rotation = 30
        second.position = (67.5, 60 + 15 * 3 ** 0.5 / 2)
        game._update(0)
        contact = contacts.call_args.args[0]
        self.assertAlmostEqual(contact.normal[0], 0.5)
        self.assertAlmostEqual(contact.normal[1], 3 ** 0.5 / 2)
        self.assertAlmostEqual(contact.angle, -60)

    def test_multiple_colliders_keep_one_event_and_stable_contact_choice(self) -> None:
        first_extra = self.first.add_component(Collider(width=40, height=40))
        second_extra = self.second.add_component(Collider(shape="circle", radius=10))
        begin = Mock()
        self.first.on_collision(begin)
        self.game._update(0)
        begin.assert_called_once()
        contact = begin.call_args.args[0]
        self.assertIsNot(contact.collider, first_extra)
        self.assertIsNot(contact.other_collider, second_extra)

    def test_direct_contact_matches_callback_geometry(self) -> None:
        begin = Mock()
        self.first.on_collision(begin)
        contact = self.first.collides_with(self.second)
        reverse = self.second.collides_with(self.first)
        assert contact is not None and reverse is not None
        self.assertIsInstance(contact, CollisionContact)
        self.assertIs(contact.other, self.second)
        self.assertIs(contact.collider, self.first.collider)
        self.assertIs(contact.other_collider, self.second.collider)
        self.assertEqual(contact.normal, (1, 0))
        self.assertAlmostEqual(contact.angle, 0)
        self.assertAlmostEqual(contact.penetration, 20)
        self.assertTrue(contact.points)
        self.assertEqual(reverse.normal, (-1, 0))
        self.assertEqual(reverse.points, contact.points)
        self.assertEqual(reverse.penetration, contact.penetration)
        self.assertIs(reverse.other, self.first)
        self.assertIs(reverse.collider, contact.other_collider)
        self.assertIs(reverse.other_collider, contact.collider)
        self.game._update(0)
        self.assertEqual(contact, begin.call_args.args[0])

    def test_direct_contact_uses_current_geometry_without_mutating_physics(self) -> None:
        self.game._update(0)
        collider = self.second.require_component(Collider)
        shape = collider._shape
        assert shape is not None and shape.body is not None
        body_position = tuple(shape.body.position)
        self.second.x = 150
        self.assertIsNone(self.first.collides_with(self.second))
        self.assertIs(collider._shape, shape)
        self.assertEqual(tuple(shape.body.position), body_position)
        self.second.x = 75
        collider.radius = 15
        contact = self.first.collides_with(self.second)
        assert contact is not None
        self.assertAlmostEqual(contact.penetration, 10)
        self.assertIs(collider._shape, shape)

    def test_direct_contacts_work_without_a_scene_and_remain_snapshots(self) -> None:
        first = GameObject(40, 40, rotation=30)
        second = GameObject(47.5, 40 + 15 * 3 ** 0.5 / 2, rotation=30)
        first.add_component(Collider(width=20, height=20))
        second.add_component(Collider(width=20, height=20))
        contact = first.collides_with(second)
        assert contact is not None
        self.assertAlmostEqual(contact.normal[0], 0.5)
        self.assertAlmostEqual(contact.normal[1], 3 ** 0.5 / 2)
        self.assertAlmostEqual(contact.angle, -60)
        self.assertAlmostEqual(contact.penetration, 5)
        second.position = (150, 150)
        self.assertIsNone(first.collides_with(second))
        self.assertAlmostEqual(contact.penetration, 5)

    def test_direct_contacts_match_callbacks_for_each_shape(self) -> None:
        for shape in Collider.SHAPES:
            with self.subTest(shape=shape):
                game = Game(200, 100)
                first = game.add(GameObject(50, 50))
                second = game.add(GameObject(60, 50))
                first.add_component(Collider(shape=shape))
                second.add_component(Collider(shape="circle", radius=10))
                contact = first.collides_with(second)
                reverse = second.collides_with(first)
                assert contact is not None and reverse is not None
                self.assertGreater(contact.penetration, 0)
                self.assertAlmostEqual(contact.penetration, reverse.penetration)
                begin = Mock()
                first.on_collision(begin)
                game._update(0)
                self.assertEqual(contact, begin.call_args.args[0])

    def test_direct_contact_respects_parent_transforms_scale_and_offsets(self) -> None:
        parent = GameObject(100, 50, rotation=90, scale_x=2, scale_y=2)
        first = GameObject(5, 0)
        parent.add_child(first)
        first.add_component(Collider(width=10, height=10, offset_x=5))
        second = GameObject(115, 30)
        second.add_component(Collider(width=20, height=20))
        contact = first.collides_with(second)
        assert contact is not None
        self.assertAlmostEqual(contact.normal[0], 1)
        self.assertAlmostEqual(contact.normal[1], 0)
        self.assertAlmostEqual(contact.penetration, 5)
        parent.rotation = 0
        touching = first.collides_with(second)
        assert touching is not None
        self.assertAlmostEqual(touching.penetration, 0)
        parent.x = 200
        self.assertIsNone(first.collides_with(second))

    def test_direct_contact_noncollisions_return_none(self) -> None:
        self.assertIsNone(self.first.collides_with(self.first))
        self.assertIsNone(self.first.collides_with(GameObject()))
        self.assertIsNone(GameObject().collides_with(self.first))
        collider = self.first.require_component(Collider)
        other = self.second.require_component(Collider)
        for component in (collider, other):
            component.enabled = False
            self.assertIsNone(self.first.collides_with(self.second))
            component.enabled = True
            for mode in (ColliderMode.SENSOR, ColliderMode.QUERY_ONLY):
                component.mode = mode
                self.assertIsNone(self.first.collides_with(self.second))
            component.mode = ColliderMode.SOLID
        with self.assertRaisesRegex(TypeError, "other must be a GameObject"):
            self.first.collides_with(None)  # type: ignore[arg-type]

    def test_direct_contacts_include_exact_tangency(self) -> None:
        for first_shape, second_shape in (
            ("circle", "circle"), ("circle", "box"), ("box", "circle"), ("box", "box"),
        ):
            with self.subTest(shapes=(first_shape, second_shape)):
                first, second = GameObject(), GameObject(20, 0)
                first.add_component(Collider(shape=first_shape, radius=10, width=20, height=20))
                second.add_component(Collider(shape=second_shape, radius=10, width=20, height=20))
                contact = first.collides_with(second)
                assert contact is not None
                self.assertEqual(contact.normal, (1, 0))
                self.assertEqual(contact.penetration, 0)
                self.assertTrue(contact.points)
                self.assertTrue(all(point[0] == 10 for point in contact.points))
                second.x = 20.001
                self.assertIsNone(first.collides_with(second))

    def test_direct_contact_preserves_first_collider_and_collection_queries(self) -> None:
        first = GameObject()
        second = GameObject(15, 0)
        primary = first.add_component(Collider(width=20, height=20, mask=0))
        other = second.add_component(Collider(width=20, height=20, mask=0))
        first.add_component(Collider(width=100, height=100))
        contact = first.collides_with(second)
        assert contact is not None
        self.assertIs(contact.collider, primary)
        self.assertIs(contact.other_collider, other)
        self.assertIs(first.collides_with_any([second]), True)
        self.assertEqual(first.collisions_with([first, second, GameObject()]), (second,))
        second.x = 40
        self.assertIsNone(first.collides_with(second))
        self.assertIs(first.collides_with_any([second]), False)
        self.assertEqual(first.collisions_with([second]), ())

    def test_removing_object_during_collision_callback_is_safe(self) -> None:
        @self.first.on_collision
        def remove(contact) -> None:
            self.game.remove(contact.other)

        self.game._update(0)
        self.game._update(0)

        self.assertNotIn(self.second, self.game.objects)


if __name__ == "__main__":
    unittest.main()
