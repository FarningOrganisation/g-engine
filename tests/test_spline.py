import math
import unittest

from g import (
    CatmullRomSpline,
    Game,
    GameObject,
    SceneSerializer,
    Spline,
)
from g.components.renderer_utils import _transform_point
from g.inspector import inspector_fields
from g.persistence import scene_types


class CatmullRomSplineTests(unittest.TestCase):
    def test_point_snapshot_endpoints_and_exposed_samples(self) -> None:
        source = [[0, 0], [10, 10], [20, 0]]
        curve = CatmullRomSpline(source, tolerance=0.1)
        source[1][0] = 500
        self.assertEqual(curve.points, ((0.0, 0.0), (10.0, 10.0), (20.0, 0.0)))
        self.assertEqual(curve.point_at(0), (0.0, 0.0))
        self.assertEqual(curve.point_at(1), (20.0, 0.0))
        self.assertEqual(curve.point_at(-2), curve.point_at(0))
        self.assertEqual(curve.point_at(2), curve.point_at(1))
        self.assertEqual(len(curve.sampled_points), len(curve.sample_parameters))
        self.assertEqual(curve.sample_parameters[0], 0.0)
        self.assertEqual(curve.sample_parameters[-1], 1.0)
        self.assertEqual(curve.sample(), tuple(zip(curve.sample_parameters, curve.sampled_points)))
        self.assertGreater(len(curve.sampled_points), 3)
        self.assertAlmostEqual(math.hypot(*curve.tangent_at(0.5)), 1.0)

    def test_two_point_open_curve_reflects_endpoints(self) -> None:
        curve = CatmullRomSpline(((0, 0), (10, 0)), tolerance=0.1)
        self.assertEqual(curve.point_at(0.25), (2.5, 0.0))
        self.assertEqual(curve.point_at(0.75), (7.5, 0.0))
        self.assertEqual(curve.tangent_at(0), (1.0, 0.0))
        self.assertAlmostEqual(curve.point_at_distance(-20)[0], 0.0)
        self.assertAlmostEqual(curve.point_at_distance(curve.length + 20)[0], 10.0)

    def test_distance_mapping_has_near_uniform_speed(self) -> None:
        curve = CatmullRomSpline(
            ((0, 0), (15, 35), (45, 20), (60, -10)),
            tolerance=0.05,
        )
        count = 120
        positions = [
            curve.point_at_distance(curve.length * index / count)
            for index in range(count + 1)
        ]
        steps = [
            math.dist(first, second)
            for first, second in zip(positions, positions[1:])
        ]
        self.assertLess(max(steps) / min(steps), 1.2)

    def test_collinear_uneven_knots_still_have_constant_distance_speed(self) -> None:
        curve = CatmullRomSpline(((0, 0), (1, 0), (100, 0)), tolerance=0.001)
        for distance in range(101):
            self.assertAlmostEqual(
                curve.point_at_distance(distance)[0], distance, delta=0.003
            )

    def test_adaptive_length_and_distance_speed_match_dense_reference(self) -> None:
        points = ((0, 0), (4, 55), (70, 20), (90, -30))
        for closed in (False, True):
            with self.subTest(closed=closed):
                curve = CatmullRomSpline(points, closed=closed, tolerance=0.001)
                reference = [curve.point_at(index / 10000) for index in range(10001)]
                reference_length = sum(
                    math.dist(first, second)
                    for first, second in zip(reference, reference[1:])
                )
                self.assertLess(abs(curve.length - reference_length) / reference_length, 0.0001)
                count = 200
                positions = [
                    curve.point_at_distance(curve.length * index / count)
                    for index in range(count + 1)
                ]
                step = curve.length / count
                self.assertLess(
                    max(abs(math.dist(a, b) - step) / step for a, b in zip(positions, positions[1:])),
                    0.02,
                )

    def test_cusp_tangent_is_explicitly_undefined(self) -> None:
        curve = CatmullRomSpline(((0, 0), (10, 0), (0, 0)), tolerance=0.001)
        self.assertGreater(curve.length, 0)
        with self.assertRaisesRegex(ValueError, "undefined"):
            curve.tangent_at(0.5)

    def test_closed_curve_wraps_distances_and_parameter(self) -> None:
        curve = CatmullRomSpline(
            ((0, 0), (20, 0), (10, 20)),
            closed=True,
            tolerance=0.1,
        )
        self.assertEqual(curve.point_at(0), curve.point_at(1))
        self.assertEqual(curve.point_at_distance(0), curve.point_at_distance(curve.length))
        self.assertEqual(
            curve.point_at_distance(-curve.length * 0.25),
            curve.point_at_distance(curve.length * 0.75),
        )

    def test_rejects_degenerate_points_and_nonfinite_arguments(self) -> None:
        invalid_curves = (
            ((), False),
            (((0, 0),), False),
            (((0, 0), (1, 1), (0, 0)), True),
            (((0, 0), (1, 1), (1, 1)), False),
            (((0, 0), (math.inf, 1)), False),
        )
        for points, closed in invalid_curves:
            with self.subTest(points=points, closed=closed):
                with self.assertRaises((TypeError, ValueError)):
                    CatmullRomSpline(points, closed=closed)
        curve = CatmullRomSpline(((0, 0), (1, 1)))
        for query in (math.nan, math.inf, -math.inf):
            with self.subTest(query=query):
                for method in (
                    curve.point_at, curve.tangent_at,
                    curve.point_at_distance, curve.tangent_at_distance,
                ):
                    with self.assertRaises(ValueError):
                        method(query)
        for tolerance in (0, -1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                CatmullRomSpline(((0, 0), (1, 1)), tolerance=tolerance)


class SplineComponentTests(unittest.TestCase):
    def test_local_and_world_curve_caches_follow_geometry_and_hierarchy(self) -> None:
        game = Game(320, 180)
        parent = GameObject(30, -10, rotation=25, scale_x=2, scale_y=3)
        child = GameObject(12, 8, rotation=-10, scale_x=0.5, scale_y=2)
        parent.add_child(child)
        game.add(parent)
        spline = child.add_component(Spline())
        local = spline.curve
        world = spline.world_curve
        self.assertIs(spline.curve, local)
        self.assertIs(spline.world_curve, world)
        self.assertAlmostEqual(
            world.point_at(0)[0], _transform_point(child, spline.points[0])[0]
        )
        self.assertAlmostEqual(
            world.point_at(0)[1], _transform_point(child, spline.points[0])[1]
        )
        self.assertNotAlmostEqual(spline.length, local.length)
        for parameter in (0.13, 0.29, 0.51, 0.82):
            expected = _transform_point(child, local.point_at(parameter))
            actual = world.point_at(parameter)
            self.assertAlmostEqual(actual[0], expected[0])
            self.assertAlmostEqual(actual[1], expected[1])
        self.assertTrue(
            all(0.0 <= value <= 1.0 for value in world.sample_parameters)
        )

        child.x += 4
        moved_world = spline.world_curve
        self.assertIsNot(moved_world, world)
        self.assertAlmostEqual(
            moved_world.point_at(0)[0],
            _transform_point(child, spline.points[0])[0],
        )
        self.assertAlmostEqual(
            moved_world.point_at(0)[1],
            _transform_point(child, spline.points[0])[1],
        )

        spline.points = ((0, 0), (10, 20), (30, 0))
        self.assertIsNot(spline.curve, local)
        self.assertIsNot(spline.world_curve, moved_world)
        self.assertEqual(spline.points, ((0.0, 0.0), (10.0, 20.0), (30.0, 0.0)))

    def test_parent_transform_changes_and_detach_keep_cached_snapshots_stable(self) -> None:
        parent = GameObject(15, 25, rotation=30, scale_x=2, scale_y=0.5)
        owner = GameObject(3, 8)
        parent.add_child(owner)
        spline = owner.add_component(Spline(tolerance=0.01))
        local = spline.curve
        for attribute, value in (("x", 45), ("rotation", 80), ("scale_x", 4), ("scale_y", 3)):
            previous = spline.world_curve
            previous_position = previous.point_at(0.37)
            setattr(parent, attribute, value)
            world = spline.world_curve
            self.assertIsNot(world, previous)
            self.assertIs(spline.curve, local)
            self.assertEqual(previous.point_at(0.37), previous_position)
            expected = _transform_point(owner, local.point_at(0.37))
            self.assertAlmostEqual(world.point_at(0.37)[0], expected[0])
            self.assertAlmostEqual(world.point_at(0.37)[1], expected[1])
        previous = spline.world_curve
        parent.remove_child(owner)
        self.assertIsNot(spline.world_curve, previous)
        owner.remove_component(spline)
        self.assertIs(spline.world_curve, local)

    def test_nonuniform_world_distance_table_matches_transformed_reference(self) -> None:
        owner = GameObject(12, -5, scale_x=5, scale_y=0.3, rotation=35)
        spline = owner.add_component(
            Spline(((0, 0), (20, 60), (60, -20), (90, 30)), tolerance=0.001)
        )
        world = spline.world_curve
        reference = [
            _transform_point(owner, spline.curve.point_at(index / 10000))
            for index in range(10001)
        ]
        reference_length = sum(math.dist(a, b) for a, b in zip(reference, reference[1:]))
        self.assertLess(abs(spline.length - reference_length) / reference_length, 0.0001)
        step = spline.length / 200
        positions = [spline.point_at_distance(step * index) for index in range(201)]
        self.assertLess(
            max(abs(math.dist(a, b) - step) / step for a, b in zip(positions, positions[1:])),
            0.02,
        )
        self.assertAlmostEqual(math.hypot(*spline.tangent_at_distance(step * 75)), 1.0)

    def test_invalid_property_edits_leave_geometry_and_caches_unchanged(self) -> None:
        spline = Spline(((0, 0), (10, 0)))
        previous = spline.curve
        for name, value in (
            ("closed", True),
            ("points", ((0, 0), (0, 0))),
            ("tolerance", math.nan),
        ):
            with self.assertRaises(ValueError):
                setattr(spline, name, value)
            self.assertIs(spline.curve, previous)
        self.assertFalse(spline.closed)
        self.assertEqual(spline.tolerance, 0.5)

    def test_closed_and_tolerance_updates_rebuild_curves(self) -> None:
        spline = Spline()
        previous = spline.curve
        spline.tolerance = 0.1
        self.assertIsNot(spline.curve, previous)
        previous = spline.curve
        spline.closed = True
        self.assertIsNot(spline.curve, previous)
        self.assertEqual(spline.curve.point_at(0), spline.curve.point_at(1))

    def test_spline_codec_round_trip_and_inspector_metadata(self) -> None:
        game = Game(320, 180)
        obj = game.add(GameObject(5, 7))
        spline = obj.add_component(
            Spline(((1, 2), (15, 30), (40, -3)), closed=True, tolerance=0.125)
        )
        spline.enabled = False
        serializer = SceneSerializer()
        document = serializer.capture(game.scenes.current, asset_root=".")
        restored = serializer.restore(game, document, asset_root=".")
        loaded = restored.objects[0].require_component(Spline)
        self.assertEqual(loaded.points, spline.points)
        self.assertTrue(loaded.closed)
        self.assertEqual(loaded.tolerance, 0.125)
        self.assertFalse(loaded.enabled)

        codec = scene_types.component_codec_for_name("g.spline")
        self.assertEqual(codec.category, "Movement")
        self.assertEqual(codec.fields, ("points", "closed", "tolerance"))
        self.assertIn("g.spline", scene_types.editable_component_types)
        metadata = inspector_fields(Spline)
        self.assertEqual(metadata["points"].group, "Movement")
        self.assertEqual(metadata["closed"].group, "Movement")
        self.assertEqual(metadata["tolerance"].default, 0.5)
        template = scene_types.component_template("g.spline")
        properties = template["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["points"], [[-64.0, 0.0], [0.0, 64.0], [64.0, 0.0]])
        self.assertEqual(properties["tolerance"], 0.5)


if __name__ == "__main__":
    unittest.main()
