import unittest

from examples.platformer.platform_movement import PlatformMovement
from g import BodyType, Collider, Game, GameObject, PhysicsBody, Script, SceneSerializer, PlatformerMotor


class MovingPlatformMotorTests(unittest.TestCase):
    def test_checkpoint_preserves_ground_support_and_pending_jump(self):
        game, player, motor, platform, body = self.create_scene()
        player.name, platform.name = "Player", "Platform"
        game._update(1 / 120)
        body.velocity = (30, 0)
        for _ in range(15):
            game._update(1 / 120)
        motor.jump()
        serializer = SceneSerializer()
        checkpoint = serializer.capture(game.scenes.current, asset_root=".", include_runtime_state=True)
        loaded_scene = serializer.restore(game, checkpoint, asset_root=".")
        self.addCleanup(loaded_scene.layers._clear)
        loaded = loaded_scene.require_child("Player").require_component(PlatformerMotor)
        self.assertTrue(loaded.grounded)
        self.assertIs(loaded._ground_object, loaded_scene.require_child("Platform"))
        self.assertEqual(loaded.ground_velocity, motor.ground_velocity)
        game._update(1 / 120)
        loaded_scene._update(1 / 120)
        self.assertEqual(loaded._jumping, motor._jumping)
        self.assertAlmostEqual(loaded.velocity[0], motor.velocity[0], delta=0.01)
        self.assertAlmostEqual(loaded.velocity[1], motor.velocity[1], delta=0.01)

    def create_scene(self, velocity=(0, 0), *, player_first=False):
        game = Game(320, 180)
        game.physics.gravity = (0, -600)
        player = GameObject(0, 28)
        player.add_component(Collider(width=16, height=16, friction=0))
        motor = player.add_platformer_motor(speed=40, acceleration=2000, deceleration=2000)
        platform = GameObject(0, 10)
        platform.add_component(Collider(width=200, height=20, friction=0))
        body = platform.add_component(PhysicsBody(BodyType.KINEMATIC, velocity=velocity))
        for item in (player, platform) if player_first else (platform, player):
            game.add(item)
            self.addCleanup(item.delete)
        return game, player, motor, platform, body

    def test_idle_rider_follows_horizontal_platform_and_reversal(self):
        for player_first in (False, True):
            for fps in (30, 60, 120):
                with self.subTest(player_first=player_first, fps=fps):
                    game, player, motor, platform, body = self.create_scene(player_first=player_first)
                    game._update(1 / fps)
                    movement = platform.add_component(PlatformMovement())
                    movement.distance = 20
                    movement.velocity = 30
                    for _ in range(fps * 4):
                        game._update(1 / fps)
                        self.assertTrue(motor.grounded)
                        self.assertAlmostEqual(player.x - platform.x, 0, delta=0.15)
                        self.assertAlmostEqual(motor.relative_velocity[0], 0, delta=0.01)
                        self.assertAlmostEqual(motor.velocity[0], body.velocity.x, delta=0.01)

    def test_lifts_up_and_down_keep_player_grounded(self):
        for vy in (-30, 30):
            with self.subTest(vy=vy):
                game, player, motor, platform, body = self.create_scene()
                game._update(1 / 120)
                body.velocity = (0, vy)
                for _ in range(120):
                    game._update(1 / 120)
                    self.assertTrue(motor.grounded)
                    self.assertAlmostEqual(player.y - platform.y, 18, delta=0.25)
                    self.assertAlmostEqual(motor.velocity[1], vy, delta=0.01)

    def test_walking_is_relative_and_jump_inherits_velocity_once(self):
        game, player, motor, platform, body = self.create_scene()
        game._update(1 / 120)
        body.velocity = (30, 20)
        for _ in range(6):
            motor.move(1)
            game._update(1 / 120)
        self.assertAlmostEqual(motor.velocity[0], 70, delta=0.01)
        self.assertAlmostEqual(motor.relative_velocity[0], 40, delta=0.01)
        motor.move(1)
        motor.jump()
        game._update(1 / 120)
        self.assertFalse(motor.grounded)
        self.assertEqual(motor.ground_velocity, (0, 0))
        self.assertAlmostEqual(motor.velocity[1], motor.jump_speed + 20)
        body.velocity = (-100, -100)
        for _ in range(10):
            game._update(1 / 120)
            self.assertFalse(motor.grounded)
        self.assertGreater(motor.velocity[0], 0)
        self.assertLess(motor.velocity[1], motor.jump_speed + 20)

    def test_walking_off_retains_support_momentum_and_support_deletion_is_safe(self):
        game, player, motor, platform, body = self.create_scene()
        game._update(1 / 120)
        body.velocity = (30, 0)
        for _ in range(6):
            game._update(1 / 120)
        platform.delete()
        for _ in range(20):
            game._update(1 / 120)
            self.assertFalse(motor.grounded)
            self.assertAlmostEqual(motor.velocity[0], 30, delta=0.01)

    def test_input_script_runs_before_motor_regardless_of_attachment_order(self):
        class Walk(Script):
            def update(self, delta_time):
                self.game_object.require_component(type(motor)).move(1)

        game, player, motor, _, _ = self.create_scene()
        player.add_component(Walk())
        game._update(1 / 120)
        self.assertGreater(motor.relative_velocity[0], 0)
        self.assertEqual(motor._horizontal_intent, 0)

    def test_landing_on_a_moving_platform_settles_to_support_velocity(self):
        game, player, motor, platform, body = self.create_scene(velocity=(30, 0))
        player.y = 100
        for _ in range(90):
            game._update(1 / 120)
        self.assertTrue(motor.grounded)
        self.assertIn(platform, motor.ground_objects)
        relative_x = player.x - platform.x
        for _ in range(60):
            game._update(1 / 120)
            self.assertTrue(motor.grounded)
            self.assertAlmostEqual(player.x - platform.x, relative_x, delta=0.1)
            self.assertAlmostEqual(motor.relative_velocity[0], 0, delta=0.01)

    def test_walk_left_and_stop_use_surface_relative_acceleration(self):
        game, player, motor, platform, body = self.create_scene()
        game._update(1 / 120)
        body.velocity = (30, 0)
        for _ in range(6):
            motor.move(-1)
            game._update(1 / 120)
        self.assertAlmostEqual(motor.velocity[0], -10, delta=0.01)
        self.assertAlmostEqual(motor.relative_velocity[0], -40, delta=0.01)
        for _ in range(6):
            game._update(1 / 120)
        self.assertAlmostEqual(motor.velocity[0], 30, delta=0.01)
        self.assertAlmostEqual(motor.relative_velocity[0], 0, delta=0.01)

    def test_surface_velocity_includes_angular_motion(self):
        game, player, motor, platform, body = self.create_scene()
        game._update(1 / 120)
        body.angular_velocity = 90
        player.x = 40
        game.physics.refresh_contacts()
        vx, vy = game.physics.ground_velocity(player.collider, platform)
        self.assertLess(vx, 20)
        self.assertLess(vy, -50)

    def test_ground_support_is_retained_when_contact_order_changes(self):
        game, player, motor, platform, body = self.create_scene()
        other = game.add(GameObject(0, 10))
        self.addCleanup(other.delete)
        other.add_component(Collider(width=200, height=20))
        game._update(1 / 120)
        chosen = motor._ground_object
        self.assertIsNotNone(chosen)
        from unittest.mock import patch
        with patch.object(game.physics, "ground_contacts", return_value=((other, platform) if chosen is platform else (platform, other), (0, 1))):
            motor.update(1 / 120)
        self.assertIs(motor._ground_object, chosen)
