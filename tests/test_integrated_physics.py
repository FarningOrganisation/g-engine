import unittest
import warnings
from math import radians
from unittest.mock import Mock, call

from g import (
    Area,
    BodyType,
    Collider,
    Collider,
    ColliderMode,
    Game,
    GameObject,
    PhysicsBody,
    PhysicsTransformWarning,
    Vec2,
    create_area_box,
)


class IntegratedPhysicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(320, 180)

    def test_nested_body_integrates_world_motion_under_transformed_parent(self) -> None:
        parent = self.game.add(GameObject(100, 80))
        parent.rotation = 35
        parent.scale_x, parent.scale_y = -2, 3
        child = GameObject(10, 20)
        child.rotation = 12
        parent.add_child(child)
        collider = child.add_component(Collider(width=10, height=12))
        body = child.add_component(PhysicsBody(
            gravity_scale=0, velocity=(30, -20), angular_velocity=20,
        ))
        origin = child.world_position
        rotation = child.world_rotation
        self.game.physics.step(0.1)
        self.assertAlmostEqual(child.world_position.x, origin.x + 3)
        self.assertAlmostEqual(child.world_position.y, origin.y - 2)
        self.assertAlmostEqual(child.world_rotation, rotation + 2)
        self.assertEqual(parent.position, Vec2(100, 80))
        self.assertIn(child, self.game.physics.overlap_point(tuple(child.world_position)))
        self.assertTrue(collider.contains_point(*child.world_position))
        self.assertAlmostEqual(body._body.position.x, child.world_position.x)

    def test_nested_kinematic_and_static_bodies_follow_parent_transforms(self) -> None:
        for kind in (BodyType.KINEMATIC, BodyType.STATIC):
            with self.subTest(kind=kind):
                parent = self.game.add(GameObject(40, 50))
                child = GameObject(10, 20)
                parent.add_child(child)
                child.add_component(Collider(width=8, height=8))
                body = child.add_component(PhysicsBody(kind))
                self.game.physics.step(0)
                parent.rotation = 90
                parent.scale_x, parent.scale_y = 2, 3
                expected = child.world_position
                self.game.physics.step(0)
                self.assertEqual(child.position, Vec2(10, 20))
                self.assertAlmostEqual(body._body.position.x, expected.x)
                self.assertAlmostEqual(body._body.position.y, expected.y)
                self.assertAlmostEqual(child.world_rotation, 90)

    def test_nested_teleport_uses_world_position_and_angle(self) -> None:
        parent = self.game.add(GameObject(100, 80))
        parent.rotation = 35
        parent.scale_x, parent.scale_y = 2, -3
        child = GameObject(10, 20)
        parent.add_child(child)
        child.add_component(Collider(width=10, height=10))
        body = child.add_component(PhysicsBody(gravity_scale=0))
        self.game.physics.refresh()
        body.teleport(200, 150, angle=70)
        self.assertAlmostEqual(child.world_position.x, 200)
        self.assertAlmostEqual(child.world_position.y, 150)
        self.assertAlmostEqual(child.world_rotation, 70)
        self.assertAlmostEqual(child.rotation, 35)
        self.game.physics.step(0)
        self.assertAlmostEqual(child.world_position.x, 200)
        self.assertAlmostEqual(child.world_rotation, 70)

    def test_game_object_teleport_keeps_dynamic_body_synchronized(self) -> None:
        owner = self.game.add(GameObject(20, 30))
        owner.add_component(Collider(width=10, height=10))
        body = owner.add_component(PhysicsBody(gravity_scale=0))
        self.game.physics.step(0)

        owner.teleport(120, 90, angle=25)
        self.game.physics.step(0)

        self.assertEqual(owner.world_position, (120, 90))
        self.assertAlmostEqual(owner.world_rotation, 25)
        self.assertEqual(tuple(body._body.position), (120, 90))

    def test_game_object_teleport_without_body_updates_world_transform(self) -> None:
        parent = self.game.add(GameObject(100, 80, rotation=30))
        child = GameObject(10, 20)
        parent.add_child(child)

        child.teleport(200, 150, angle=70)

        self.assertAlmostEqual(child.world_position.x, 200)
        self.assertAlmostEqual(child.world_position.y, 150)
        self.assertAlmostEqual(child.world_rotation, 70)

    def test_dynamic_body_warns_and_discards_direct_transform_changes(self) -> None:
        owner = self.game.add(GameObject(40, 50))
        owner.name = "Player"
        owner.add_component(Collider(width=10, height=10))
        body = owner.add_component(PhysicsBody(gravity_scale=0))
        self.game.physics.step(0)

        owner.position.x = 120
        owner.rotation = 45
        with self.assertWarnsRegex(
            PhysicsTransformWarning,
            "Dynamic PhysicsBody on 'Player'.*teleport",
        ):
            self.game.physics.step(0)

        self.assertEqual(owner.position, (40, 50))
        self.assertEqual(owner.rotation, 0)
        self.assertEqual(tuple(body._body.position), (40, 50))

    def test_fixed_dynamic_body_accepts_direct_rotation_changes(self) -> None:
        owner = self.game.add(GameObject(40, 50))
        owner.add_component(Collider(width=10, height=10))
        body = owner.add_component(
            PhysicsBody(gravity_scale=0, fixed_rotation=True)
        )
        self.game.physics.step(0)

        owner.rotation = 45
        with warnings.catch_warnings():
            warnings.simplefilter("error", PhysicsTransformWarning)
            self.game.physics.step(0)

        self.assertEqual(owner.rotation, 45)
        self.assertAlmostEqual(body._body.angle, -radians(45))

    def test_physics_parent_and_child_sync_parent_first(self) -> None:
        parent = self.game.add(GameObject(80, 60))
        parent.add_component(Collider(width=8, height=8))
        parent_body = parent.add_component(PhysicsBody(gravity_scale=0, velocity=(10, 0)))
        child = GameObject(40, 30)
        parent.add_child(child)
        child.add_component(Collider(width=8, height=8))
        child_body = child.add_component(PhysicsBody(gravity_scale=0, velocity=(0, 20)))
        self.game.physics.refresh()
        child_origin = child.world_position
        for _ in range(5):
            self.game.physics.step(0.1)
            self.assertAlmostEqual(parent.world_position.x, parent_body._body.position.x)
            self.assertAlmostEqual(child.world_position.x, child_body._body.position.x)
            self.assertAlmostEqual(child.world_position.y, child_body._body.position.y)
        self.assertAlmostEqual(child.world_position.x, child_origin.x)
        self.assertAlmostEqual(child.world_position.y, child_origin.y + 10)

    def test_nested_fixed_rotation_retains_world_angle(self) -> None:
        parent = self.game.add(GameObject(80, 60))
        parent.rotation = 25
        child = GameObject(20, 30)
        child.rotation = 10
        parent.add_child(child)
        child.add_component(Collider(width=8, height=8))
        body = child.add_component(PhysicsBody(gravity_scale=0, fixed_rotation=True))
        self.game.physics.refresh()
        body.angular_velocity = 100
        self.game.physics.step(0.1)
        self.assertAlmostEqual(child.world_rotation, 35)
        self.assertAlmostEqual(child.rotation, 10)
        self.assertAlmostEqual(body.angular_velocity, 0)

    def test_body_velocity_is_vec2_and_accepts_vector_or_tuple_assignment(self) -> None:
        body = PhysicsBody(velocity=Vec2(12, -8))
        self.assertIsInstance(body.velocity, Vec2)
        self.assertEqual(body.velocity, Vec2(12, -8))
        body.velocity = (7, 3)
        self.assertEqual(body.velocity, Vec2(7, 3))
        body.velocity = Vec2(-4, 9)
        self.assertEqual(tuple(body._body.velocity), (-4, 9))

    def test_body_filter_is_shared_and_updates_all_inheriting_shapes(self) -> None:
        owner = self.game.add(GameObject(40, 40))
        box = owner.add_component(Collider(width=10, height=10))
        circle = owner.add_component(Collider(shape="circle", radius=5, sensor=True))
        self.assertEqual((box.shape, circle.shape), ("box", "circle"))
        body = owner.add_component(PhysicsBody(categories=2, mask=4, group=7))
        for expected in ((2, 4, 7), (8, 16, 9)):
            body.categories, body.mask, body.group = expected
            self.game.physics.refresh()
            for collider in (box, circle):
                assert collider._shape is not None
                filtering = collider._shape.filter
                self.assertEqual((filtering.categories, filtering.mask, filtering.group), expected)
        body.enabled = False
        self.game.physics.refresh()
        self.assertEqual(box.effective_filter.categories, 8)
        self.assertEqual(circle.effective_filter.categories, 8)

    def test_collider_override_and_body_removal_preserve_local_settings(self) -> None:
        owner = self.game.add(GameObject())
        box = owner.add_component(Collider(width=10, height=10, categories=2, mask=4, group=3))
        body = owner.add_component(PhysicsBody(categories=8, mask=16, group=9))
        self.assertTrue(box.override_collision_filtering)
        self.game.physics.refresh()
        assert box._shape is not None
        self.assertEqual(box._shape.filter.categories, 2)
        box.override_collision_filtering = False
        self.game.physics.refresh()
        assert box._shape is not None
        self.assertEqual(box._shape.filter.categories, 8)
        owner.remove_component(body)
        self.game.physics.refresh()
        assert box._shape is not None
        self.assertEqual(box._shape.filter.categories, 2)
        self.assertEqual((box.categories, box.mask, box.group), (2, 4, 3))

    def test_body_filter_affects_queries_and_collider_can_override(self) -> None:
        owner = self.game.add(GameObject(40, 40))
        circle = owner.add_component(Collider(shape="circle", radius=5))
        owner.add_component(PhysicsBody(categories=2, mask=4))
        self.assertNotIn(owner, self.game.physics.overlap_point((40, 40), categories=8, mask=2))
        self.assertIn(owner, self.game.physics.overlap_point((40, 40), categories=4, mask=2))
        circle.override_collision_filtering = True
        self.assertIn(owner, self.game.physics.overlap_point((40, 40), categories=8, mask=2))

    def test_add_physics_accepts_shared_filter_defaults(self) -> None:
        owner = self.game.add_rect(40, 40, (255, 255, 255), 10, 10)
        body = owner.add_physics(categories=2, mask=4, group=3)
        self.assertEqual((body.categories, body.mask, body.group), (2, 4, 3))
        assert owner.collider is not None
        self.assertFalse(owner.collider.override_collision_filtering)
        self.assertEqual(owner.collider.effective_filter.categories, 2)

    def test_collider_offsets_follow_parent_scale_rotation_and_physics(self) -> None:
        for shape, collider in (
            ("box", Collider(width=4, height=6, offset_x=10, offset_y=-5)),
            ("circle", Collider(shape="circle", radius=2, offset_x=10, offset_y=-5)),
        ):
            with self.subTest(shape=shape):
                self.assertEqual(collider.shape, shape)
                parent = self.game.add(GameObject(100, 80))
                parent.scale_x = 2
                parent.scale_y = 3
                parent.rotation = 90
                child = GameObject()
                parent.add_child(child)
                child.add_component(collider)
                # Clockwise rotation: local (20, -15) becomes (-15, -20).
                expected = (85, 60)
                self.assertTrue(collider.contains_point(*expected))
                self.assertFalse(collider.contains_point(100, 80))
                self.game.physics.refresh()
                shape = collider._shape
                assert shape is not None
                self.assertLess(shape.point_query(expected).distance, 0)
                self.assertGreater(shape.point_query((100, 80)).distance, 0)
                self.assertIn(child, self.game.physics.overlap_point(expected))
                collider.offset_x = -10
                collider.offset_y = 5
                self.game.physics.refresh()
                self.assertIsNot(collider._shape, shape)
                self.assertTrue(collider.contains_point(115, 100))
                self.assertIn(child, self.game.physics.overlap_point((115, 100)))
                self.assertNotIn(child, self.game.physics.overlap_point(expected))

    def test_collider_offsets_follow_negative_scale_and_dynamic_body(self) -> None:
        for shape, collider in (
            ("box", Collider(width=4, height=4, offset_x=10, offset_y=5)),
            ("circle", Collider(shape="circle", radius=2, offset_x=10, offset_y=5)),
        ):
            with self.subTest(shape=shape):
                self.assertEqual(collider.shape, shape)
                owner = self.game.add(GameObject(60, 60))
                owner.scale_x = -2
                owner.scale_y = 3
                owner.add_component(collider)
                owner.add_component(PhysicsBody(BodyType.DYNAMIC, gravity_scale=0))
                self.game.physics.refresh()
                expected = (40, 75)
                self.assertTrue(collider.contains_point(*expected))
                self.assertIn(owner, self.game.physics.overlap_point(expected))

    def test_nonfinite_collider_offsets_are_rejected(self) -> None:
        for factory in (lambda **kw: Collider(width=4, height=4, **kw), lambda **kw: Collider(shape="circle", radius=2, **kw)):
            with self.assertRaisesRegex(ValueError, "offsets must be finite"):
                factory(offset_x=float("nan"))
            with self.assertRaisesRegex(ValueError, "offsets must be finite"):
                factory(offset_y=float("inf"))

    def test_collider_is_registered_without_explicit_physics(self) -> None:
        rectangle = self.game.add_rect(
            80,
            60,
            (255, 255, 255),
            40,
            30,
            collider=True,
        )

        self.game._update(0)

        collider = rectangle.collider
        assert collider is not None
        shape = collider._shape
        assert shape is not None
        self.assertEqual(shape.body.body_type, BodyType.STATIC.value)

    def test_add_physics_uses_existing_collider_shape(self) -> None:
        rectangle = self.game.add_rect(
            80,
            60,
            (255, 255, 255),
            40,
            30,
            collider=True,
        )

        body = rectangle.add_physics(BodyType.DYNAMIC)
        self.game._update(0)

        collider = rectangle.collider
        assert collider is not None
        shape = collider._shape
        assert shape is not None
        self.assertIs(shape.body, body._body)

        body.enabled = False
        self.game._update(0)
        shape = collider._shape
        assert shape is not None
        self.assertEqual(
            shape.body.body_type,
            BodyType.STATIC.value,
        )

    def test_area_reports_enter_stay_and_exit(self) -> None:
        player = self.game.add_rect(
            50,
            50,
            (255, 255, 255),
            20,
            20,
            collider=True,
        )
        area_object = self.game.add(create_area_box(50, 50, 60, 60))
        area = area_object.require_component(Area)
        entered = Mock()
        stayed = Mock()
        exited = Mock()
        area.on_enter(entered)
        area.on_stay(stayed)
        area.on_exit(exited)

        self.game._update(0)
        entered.assert_called_once_with(player)
        self.assertEqual(area.overlapping_objects, (player,))

        self.game._update(0)
        stayed.assert_called_once_with(player)

        player.position = (200, 50)
        self.game._update(0)
        exited.assert_called_once_with(player)
        self.assertEqual(area.overlapping_objects, ())

    def test_area_uses_only_selected_sensor_collider_for_events(self) -> None:
        area_object = self.game.add(GameObject(50, 50))
        area_object.add_component(Collider(width=20, height=20))
        sensor = area_object.add_component(
            Collider(shape="circle", radius=12, sensor=True, offset_x=100)
        )
        area = area_object.add_component(Area(collider_index=1))
        unrelated = self.game.add_rect(
            50, 50, (255, 255, 255), 10, 10, collider=True
        )
        target = self.game.add_rect(
            150, 50, (255, 255, 255), 10, 10, collider=True
        )
        entered, stayed, exited = Mock(), Mock(), Mock()
        area.on_enter(entered)
        area.on_stay(stayed)
        area.on_exit(exited)

        self.game._update(0)

        entered.assert_called_once_with(target)
        self.assertNotIn(unrelated, area.overlapping_objects)
        self.assertEqual(area.overlapping_objects, (target,))
        self.assertIs(sensor.mode, ColliderMode.SENSOR)

        self.game._update(0)
        stayed.assert_called_once_with(target)

        target.position = (250, 50)
        self.game._update(0)
        exited.assert_called_once_with(target)
        self.assertEqual(area.overlapping_objects, ())

    def test_area_filters_masks_and_disable_reenable_events(self) -> None:
        area_object = self.game.add(GameObject(80, 80))
        sensor = area_object.add_component(
            Collider(shape="circle", radius=20, categories=0b0010, mask=0b0100, sensor=True)
        )
        area = area_object.add_component(Area())
        target = self.game.add_circle(
            80, 80, (255, 255, 255), 8, collider=True
        )
        target_collider = target.require_component(Collider)
        target_collider.categories = 0b1000
        target_collider.mask = 0b0010
        entered, stayed, exited = Mock(), Mock(), Mock()
        area.on_enter(entered)
        area.on_stay(stayed)
        area.on_exit(exited)

        self.game._update(0)
        entered.assert_not_called()

        target_collider.categories = 0b0100
        self.game._update(0)
        entered.assert_called_once_with(target)

        area.enabled = False
        self.game._update(0)
        stayed.assert_not_called()

        area.enabled = True
        self.game._update(0)
        entered.assert_has_calls([call(target), call(target)])
        stayed.assert_not_called()

        self.game._update(0)
        stayed.assert_called_once_with(target)

        target.position = (140, 80)
        self.game._update(0)
        exited.assert_called_once_with(target)
        self.assertIs(sensor.mode, ColliderMode.SENSOR)

    def test_area_events_observe_positions_after_game_object_updates(self) -> None:
        class MovingTarget(GameObject):
            def update(self, _delta_time: float) -> None:
                self.position = (150, 50)

        area_object = self.game.add(create_area_box(150, 50, 60, 60))
        area = area_object.require_component(Area)
        target = self.game.add(MovingTarget())
        target.add_component(Collider(width=10, height=10))
        entered = Mock()
        area.on_enter(entered)

        self.game._update(0)

        entered.assert_called_once_with(target)

    def test_query_only_collider_has_no_automatic_events(self) -> None:
        first = self.game.add_rect(
            50,
            50,
            (255, 255, 255),
            20,
            20,
            collider=True,
        )
        second = self.game.add_rect(
            50,
            50,
            (255, 255, 255),
            20,
            20,
            collider=True,
        )
        assert first.collider is not None
        first.collider.mode = ColliderMode.QUERY_ONLY
        collided = Mock()
        overlapped = Mock()
        first.on_collision(collided)
        first.on_overlap(overlapped)

        self.game._update(0)

        self.assertTrue(first.overlaps(second))
        self.assertFalse(first.collides_with(second))
        collided.assert_not_called()
        overlapped.assert_not_called()

    def test_point_query_returns_engine_objects(self) -> None:
        rectangle = self.game.add_rect(
            80,
            60,
            (255, 255, 255),
            40,
            30,
            collider=True,
        )

        matches = self.game.physics.overlap_point((80, 60))

        self.assertEqual(matches, (rectangle,))

    def test_shape_queries_and_raycast_return_engine_objects(self) -> None:
        first = self.game.add_rect(
            80,
            60,
            (255, 255, 255),
            40,
            30,
            collider=True,
        )
        second = self.game.add_circle(
            200,
            60,
            (255, 255, 255),
            12,
            collider=True,
        )

        self.assertEqual(
            self.game.physics.overlap_circle((80, 60), 30),
            (first,),
        )
        self.assertEqual(
            self.game.physics.overlap_box((200, 60), (30, 30)),
            (second,),
        )
        self.assertIs(
            self.game.physics.raycast((0, 60), (300, 60)),
            first,
        )
        self.assertEqual(
            self.game.physics.raycast_all((0, 60), (300, 60)),
            (first, second),
        )

    def test_raycast_hits_include_geometry_and_support_exclusions(self) -> None:
        first = self.game.add_rect(
            80,
            60,
            (255, 255, 255),
            40,
            30,
            collider=True,
        )
        second = self.game.add_circle(
            200,
            60,
            (255, 255, 255),
            12,
            collider=True,
        )

        hits = self.game.physics.raycast_all_hits(
            (0, 60),
            (300, 60),
        )

        self.assertEqual(tuple(hit.target for hit in hits), (first, second))
        self.assertEqual(hits[0].point, (60, 60))
        self.assertEqual(hits[0].normal, (-1, 0))
        self.assertAlmostEqual(hits[0].fraction, 0.2)
        self.assertAlmostEqual(hits[0].distance, 60)
        excluded = self.game.physics.raycast_hit(
            (0, 60),
            (300, 60),
            exclude=(first,),
        )
        self.assertIsNotNone(excluded)
        assert excluded is not None
        self.assertIs(excluded.target, second)
        self.assertIs(
            self.game.physics.raycast(
                (0, 60),
                (300, 60),
                exclude=(first,),
            ),
            second,
        )

    def test_nearest_returns_surface_information_and_honors_limits(self) -> None:
        first = self.game.add_rect(
            80,
            60,
            (255, 255, 255),
            40,
            30,
            collider=True,
        )
        second = self.game.add_circle(
            200,
            60,
            (255, 255, 255),
            12,
            collider=True,
        )

        hit = self.game.physics.nearest((0, 60))

        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertIs(hit.target, first)
        self.assertEqual(hit.point, (60, 60))
        self.assertEqual(hit.gradient, (-1, 0))
        self.assertAlmostEqual(hit.distance, 60)
        self.assertIsNone(
            self.game.physics.nearest((0, 60), max_distance=59)
        )
        excluded = self.game.physics.nearest(
            (0, 60),
            exclude=(first,),
        )
        self.assertIsNotNone(excluded)
        assert excluded is not None
        self.assertIs(excluded.target, second)
        with self.assertRaisesRegex(
            ValueError,
            "max_distance must be non-negative",
        ):
            self.game.physics.nearest((0, 60), max_distance=-1)

    def test_polygon_and_rich_overlap_queries_return_contact_data(self) -> None:
        rectangle = self.game.add_rect(
            80,
            60,
            (255, 255, 255),
            40,
            30,
            collider=True,
        )
        outside = self.game.add_circle(
            200,
            60,
            (255, 255, 255),
            12,
            collider=True,
        )

        polygon_matches = self.game.physics.overlap_polygon(
            ((50, 40), (110, 40), (80, 90)),
        )
        hits = self.game.physics.overlap_circle_hits((50, 60), 20)

        self.assertEqual(polygon_matches, (rectangle,))
        self.assertNotIn(outside, polygon_matches)
        self.assertEqual(len(hits), 1)
        self.assertIs(hits[0].target, rectangle)
        self.assertEqual(hits[0].normal, (1, 0))
        self.assertEqual(hits[0].points, ((65, 60),))
        self.assertAlmostEqual(hits[0].penetration, 10)
        self.assertEqual(
            self.game.physics.overlap_circle(
                (50, 60),
                20,
                exclude=(rectangle,),
            ),
            (),
        )
        with self.assertRaisesRegex(
            ValueError,
            "at least three distinct vertices",
        ):
            self.game.physics.overlap_polygon(((0, 0), (1, 1)))


if __name__ == "__main__":
    unittest.main()
