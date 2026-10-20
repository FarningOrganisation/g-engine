import unittest

from examples.platformer.platform_movement import PlatformMovement
from g import BodyType, Collider, Game, GameObject, PhysicsBody, SceneSerializer
from g.inspector import inspector_fields


class PlatformMovementTests(unittest.TestCase):
    def create_platform(self, *, distance=100, velocity=60, body_type=BodyType.KINEMATIC):
        game = Game(320, 180)
        owner = game.add(GameObject(70, 20))
        self.addCleanup(owner.delete)
        owner.add_component(Collider(width=16, height=8))
        body = owner.add_component(PhysicsBody(body_type))
        script = owner.add_component(PlatformMovement())
        script.distance = distance
        script.velocity = velocity
        script.on_ready()
        return game, owner, body, script

    def test_ping_pong_reaches_exact_endpoints_without_drift_or_overshoot(self):
        game, owner, body, script = self.create_platform(distance=10, velocity=7)
        start = owner.world_position.x
        positions = []
        for _ in range(301):
            game.physics.step(0.1)
            positions.append(owner.world_position.x)
            script.update(0.1)
            self.assertLessEqual(abs(body.velocity.x), 7)
            self.assertEqual(body.velocity.y, 0)
        self.assertAlmostEqual(min(positions), start)
        self.assertAlmostEqual(max(positions), start + 10)
        self.assertGreater(sum(abs(x - start) < 1e-7 for x in positions), 5)
        self.assertGreater(sum(abs(x - start - 10) < 1e-7 for x in positions), 5)
        owner.delete()

    def test_zero_speed_pauses_and_resumes_in_same_direction(self):
        game, owner, body, script = self.create_platform()
        script.update(0.1)
        game.physics.step(0.1)
        x = owner.world_position.x
        script.velocity = 0
        script.update(0.1)
        game.physics.step(0.1)
        self.assertEqual(owner.world_position.x, x)
        script.velocity = 20
        script.update(0.1)
        self.assertEqual(body.velocity.x, 20)
        script.distance = 0
        script.update(0.1)
        self.assertEqual(tuple(body.velocity), (0, 0))
        owner.delete()

    def test_large_fixed_step_stays_within_travel_and_zero_dt_does_not_divide(self):
        game, owner, body, script = self.create_platform(distance=3, velocity=100)
        start = owner.world_position.x
        script.update(2)
        game.physics.step(2)
        self.assertAlmostEqual(owner.world_position.x, start + 3)
        script.update(2)
        script.update(0)
        game.physics.step(2)
        self.assertAlmostEqual(owner.world_position.x, start)
        owner.delete()

    def test_invalid_body_and_parameters_report_errors(self):
        with self.assertRaisesRegex(ValueError, "kinematic"):
            self.create_platform(body_type=BodyType.DYNAMIC)
        for field in ("distance", "velocity"):
            for invalid in (-1, float("inf"), float("nan")):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaisesRegex(ValueError, field):
                        self.create_platform(**{field: invalid})

    def test_controlling_parameters_have_inspector_metadata(self):
        fields = inspector_fields(PlatformMovement)
        self.assertEqual(fields["distance"].default, 100)
        self.assertEqual(fields["velocity"].default, 60)

    def test_checkpoint_mid_return_trip_preserves_direction_origin_and_trigger(self):
        game, owner, body, script = self.create_platform(distance=10, velocity=7)
        owner.name = "Platform"
        script.trigger_on_player_touch = True
        script._triggered = True
        for _ in range(20):
            game._update(0.1)
        self.assertEqual(script._direction, -1)
        serializer = SceneSerializer()
        checkpoint = serializer.capture(game.scenes.current, asset_root=".", include_runtime_state=True)
        restored = serializer.restore(game, checkpoint, asset_root=".")
        self.addCleanup(restored.layers._clear)
        loaded = restored.require_child("Platform")
        movement = loaded.require_component(PlatformMovement)
        self.assertTrue(movement._triggered)
        self.assertEqual(movement._direction, script._direction)
        self.assertEqual(movement._start_x, script._start_x)
        for _ in range(60):
            game._update(0.1)
            restored._update(0.1)
            self.assertAlmostEqual(loaded.x, owner.x)
            self.assertEqual(movement._direction, script._direction)
