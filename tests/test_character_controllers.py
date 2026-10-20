import unittest
from math import hypot, isfinite, sqrt

from g import (
    BodyType, Collider, ColliderMode, ColorTileSet,
    Game, GameObject, PlatformerMotor, TileCollision, TileFlags, TileLayer,
)


class CharacterControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(320, 180)

    def slope_player(
        self, *, circle: bool = False, max_slope: float = 50,
        mirrored: bool = False,
    ) -> tuple[GameObject, PlatformerMotor, TileLayer]:
        self.game.physics.gravity = (0, -600)
        tileset = ColorTileSet([(255, 255, 255)])
        tileset.define(
            0,
            collision=TileCollision(
                shape="polygon", polygons=(((0, 0), (1, 0), (1, 1)),),
            ),
        )
        ground = self.game.scenes.current.layers.add_tile(
            "Hill", tileset, cell_width=128, cell_height=128,
            collision_enabled=True,
        )
        ground.set_tile(
            0, 0, 0,
            flags=TileFlags.FLIP_HORIZONTAL if mirrored else TileFlags.NONE,
        )
        player = self.game.add(GameObject(48, 140 if mirrored else 90))
        player.add_component(Collider(shape="circle", radius=8) if circle else Collider(width=16, height=16))
        self.assertEqual(player.require_component(Collider).shape, "circle" if circle else "box")
        controller = player.add_platformer_motor(
            speed=40, acceleration=2000, deceleration=2000, max_slope=max_slope,
        )
        for _ in range(90):
            self.game._update(1 / 120)
        return player, controller, ground

    def test_walks_up_and_down_triangle_at_constant_surface_speed(self) -> None:
        for circle, mirrored in (
            (False, False), (True, False), (False, True), (True, True),
        ):
            for direction in (-1, 1):
                with self.subTest(circle=circle, direction=direction, mirrored=mirrored):
                    self.game = Game(320, 180)
                    player, controller, ground = self.slope_player(
                        circle=circle, mirrored=mirrored,
                    )
                    self.assertTrue(controller.grounded)
                    self.assertIn(ground, controller.ground_objects)
                    self.assertAlmostEqual(
                        controller.ground_angle, 45 if mirrored else -45, delta=0.1,
                    )
                    start_x, start_y = player.position
                    for _ in range(45):
                        controller.move(direction)
                        self.game._update(1 / 120)
                        self.assertTrue(controller.grounded)
                    self.assertAlmostEqual(hypot(*controller.velocity), 40, delta=0.1)
                    self.assertAlmostEqual(
                        controller.velocity[0], direction * 40 / sqrt(2), delta=0.1,
                    )
                    self.assertAlmostEqual(
                        player.x - start_x,
                        (player.y - start_y) * (-1 if mirrored else 1), delta=1,
                    )
                    self.assertEqual(player.rotation, 0)

    def test_jump_from_slope_is_not_snapped_back_or_grounded_while_rising(self) -> None:
        player, controller, _ = self.slope_player()
        start_y = player.y
        controller.jump()
        self.game._update(1 / 120)
        for _ in range(12):
            self.game._update(1 / 120)
            self.assertFalse(controller.grounded)
            self.assertEqual(controller.ground_angle, 0)
        self.assertGreater(player.y, start_y + 20)

    def test_stopping_on_slope_does_not_slide(self) -> None:
        player, controller, _ = self.slope_player()
        start_x, start_y = player.position
        for _ in range(120):
            self.game._update(1 / 120)
            self.assertTrue(controller.grounded)
        self.assertAlmostEqual(player.x, start_x, delta=0.5)
        self.assertAlmostEqual(player.y, start_y, delta=0.5)
        self.assertEqual(controller.velocity, (0, 0))

    def test_slope_limit_rejects_triangle_ground(self) -> None:
        _, controller, _ = self.slope_player(max_slope=30)
        self.assertFalse(controller.grounded)

    def test_crosses_triangle_tile_join(self) -> None:
        player, controller, ground = self.slope_player()
        ground.set_tile(1, 1, 0)
        for _ in range(420):
            controller.move(1)
            self.game._update(1 / 120)
            self.assertTrue(controller.grounded)
        self.assertGreater(player.x, 135)
        self.assertAlmostEqual(controller.ground_angle, -45, delta=0.1)

    def test_large_fast_player_keeps_surface_angle_across_triangle_joins(self) -> None:
        for fps in (30, 60, 120):
            with self.subTest(fps=fps):
                self.game = Game(800, 600)
                self.game.physics.gravity = (0, -980)
                tileset = ColorTileSet([(255, 255, 255)])
                tileset.define(
                    0, collision=TileCollision(
                        shape="polygon", polygons=(((0, 0), (1, 0), (1, 1)),),
                    ),
                )
                ground = self.game.scenes.current.layers.add_tile(
                    "Hill", tileset, cell_width=128, cell_height=128,
                    collision_enabled=True,
                )
                for index in range(20):
                    ground.set_tile(index, index, 0)
                player = self.game.add(GameObject(192, 400))
                player.add_component(Collider(width=128, height=160, offset_y=-51.4))
                controller = player.add_platformer_motor(
                    speed=500, acceleration=3000,
                )
                for _ in range(fps):
                    self.game._update(1 / fps)
                for _ in range(fps * 4):
                    controller.move(1)
                    self.game._update(1 / fps)
                    self.assertTrue(controller.grounded)
                    self.assertAlmostEqual(controller.ground_angle, -45, delta=0.1)
                self.assertGreater(player.x, 1200)
                for _ in range(fps * 2):
                    controller.move(-1)
                    self.game._update(1 / fps)
                    self.assertTrue(controller.grounded)
                    self.assertAlmostEqual(controller.ground_angle, -45, delta=0.1)
                controller.jump()
                self.game._update(1 / fps)
                self.assertFalse(controller.grounded)
                self.assertEqual(controller.ground_angle, 0)
                self.assertEqual(controller.velocity[1], controller.jump_speed)

    def test_generated_slope_insets_do_not_expose_flat_ground_during_ascent(self) -> None:
        for fps in (30, 60, 120):
            for mirrored in (False, True):
                with self.subTest(fps=fps, mirrored=mirrored):
                    self._check_generated_slope_ascent(fps, mirrored)

    def _check_generated_slope_ascent(self, fps: int, mirrored: bool) -> None:
        self.game = Game(800, 600)
        self.game.physics.gravity = (0, -980)
        tileset = ColorTileSet([(255, 255, 255), (255, 255, 255)])
        tileset.define(
            0, collision=TileCollision(
                shape="polygon",
                polygons=(((1/128, 0), (1, 0), (1, 127/128),
                           (127/128, 127/128), (1/128, 1/128)),),
            ),
        )
        tileset.define(1, collision=TileCollision())
        ground = self.game.scenes.current.layers.add_tile(
            "Generated hill", tileset, cell_width=128, cell_height=128,
            collision_enabled=True,
        )
        for index in range(12):
            ground.set_tile(
                -index if mirrored else index, index, 0,
                flags=TileFlags.FLIP_HORIZONTAL if mirrored else TileFlags.NONE,
            )
            ground.set_tile(-index - 1 if mirrored else index + 1, index, 1)
        player = self.game.add(GameObject(-52 if mirrored else 180, 400))
        player.add_component(Collider(width=128, height=160, offset_y=-51.4))
        controller = player.add_platformer_motor(speed=500, acceleration=3000)
        for _ in range(fps):
            self.game._update(1/fps)
        for _ in range(fps * 2):
            controller.move(-1 if mirrored else 1)
            self.game._update(1/fps)
            self.assertTrue(controller.grounded)
            self.assertAlmostEqual(controller.ground_angle, 45 if mirrored else -45, delta=0.1)
        self.assertGreater(128 - player.x if mirrored else player.x, 700)

    def test_uphill_support_recovery_does_not_push_player_into_ceiling(self) -> None:
        player, controller, _ = self.slope_player()
        body, collider = player.body, player.collider
        assert body is not None and collider is not None
        body.teleport(48, 63)
        self.game.add_rect(
            48, 81.5, (255, 255, 255), 32, 20, collider=True
        )
        self.game.physics.refresh_contacts()
        objects, _ = self.game.physics._snap_to_ground(
            collider, body, distance=8, max_slope=50,
        )
        self.assertEqual(objects, ())
        self.assertEqual(player.y, 63)

    def test_crosses_flat_slope_transitions_in_both_directions(self) -> None:
        player, controller, ground = self.slope_player()
        ground.add_tileset(
            ColorTileSet([(255, 255, 255)]),
            identifier="flat", name="Flat", first_gid=1,
        )
        ground.tilesets[1].tileset.define(0, collision=TileCollision())
        ground.set_tile(-1, -1, 1)
        ground.set_tile(1, 0, 1)
        controller.speed = 80
        for _ in range(200):
            controller.move(1)
            self.game._update(1 / 120)
            self.assertTrue(controller.grounded)
        self.assertGreater(player.x, 130)
        self.assertAlmostEqual(controller.ground_angle, 0, delta=0.1)
        for _ in range(400):
            controller.move(-1)
            self.game._update(1 / 120)
            self.assertTrue(controller.grounded)
        self.assertLess(player.x, -10)
        self.assertAlmostEqual(controller.ground_angle, 0, delta=0.1)

    def test_snap_ignores_sensors_and_respects_collision_mask(self) -> None:
        floor = self.game.add_rect(
            160, 10, (255, 255, 255), 320, 20, collider=True
        )
        player = self.game.add_rect(80, 30, (255, 255, 255), 20, 20)
        controller = player.add_platformer_motor()
        body, collider = player.body, floor.collider
        assert body is not None and collider is not None
        for mode, mask in ((ColliderMode.SENSOR, collider.mask),
                           (ColliderMode.SOLID, 0)):
            collider.mode = ColliderMode.SOLID
            collider.mask = 0xFFFFFFFF
            body.teleport(80, 30)
            self.game._update(0)
            self.assertTrue(controller.grounded)
            collider.mode = mode
            collider.mask = mask
            body.teleport(80, 35)
            self.game._update(0)
            self.assertFalse(controller.grounded)
            self.assertEqual(player.y, 35)

    def test_ground_snap_only_follows_nearby_ground(self) -> None:
        self.game.physics.gravity = (0, -600)
        self.game.add_rect(
            160, 10, (255, 255, 255), 320, 20, collider=True
        )
        player = self.game.add_rect(80, 30, (255, 255, 255), 20, 20)
        controller = player.add_platformer_motor(ground_snap_distance=8)
        self.game._update(0)
        body = player.body
        assert body is not None
        body.teleport(80, 35)
        self.game._update(0)
        self.assertTrue(controller.grounded)
        self.assertAlmostEqual(player.y, 30)
        body.teleport(80, 50)
        self.game._update(0)
        self.assertFalse(controller.grounded)
        self.assertEqual(player.y, 50)
        body.teleport(80, 35)
        self.game._update(0)
        self.assertFalse(controller.grounded)
        self.assertEqual(player.y, 35)

    def test_ground_snap_can_be_disabled(self) -> None:
        self.game.add_rect(
            160, 10, (255, 255, 255), 320, 20, collider=True
        )
        player = self.game.add_rect(80, 30, (255, 255, 255), 20, 20)
        controller = player.add_platformer_motor(ground_snap_distance=0)
        self.game._update(0)
        self.assertTrue(controller.grounded)
        body = player.body
        assert body is not None
        body.teleport(80, 35)
        self.game._update(0)
        self.assertFalse(controller.grounded)
        self.assertEqual(player.y, 35)

    def test_ground_probe_at_exact_triangle_surface_has_finite_normal(self) -> None:
        player, controller, ground = self.slope_player()
        body, collider = player.body, player.collider
        assert body is not None and collider is not None
        # Right foot is exactly on y=x; a zero-radius Pymunk cast here
        # reports an undefined normal rather than the surface normal.
        body.teleport(48, 64)
        self.game.physics.refresh_contacts()
        objects, normal = self.game.physics._snap_to_ground(
            collider, body, distance=8, max_slope=50,
        )
        self.assertEqual(objects, (ground,))
        self.assertTrue(all(isfinite(value) for value in normal))
        self.assertAlmostEqual(normal[0], -1 / sqrt(2))
        self.assertAlmostEqual(normal[1], 1 / sqrt(2))
        self.assertAlmostEqual(player.y, 64, delta=0.001)
        for _ in range(30):
            controller.move(1)
            self.game._update(1 / 120)
            self.assertTrue(
                all(isfinite(value) for value in (*player.position, *controller.velocity)),
            )

    def test_ground_snap_distance_validation(self) -> None:
        for distance in (-1, float("nan"), float("inf")):
            with self.subTest(distance=distance):
                player = self.game.add_rect(80, 80, (255, 255, 255), 20, 20)
                with self.assertRaisesRegex(ValueError, "ground_snap_distance"):
                    player.add_platformer_motor(ground_snap_distance=distance)

    def test_top_down_motor_normalizes_and_accelerates(self) -> None:
        player = self.game.add_rect(
            80,
            60,
            (255, 255, 255),
            20,
            20,
        )
        controller = player.add_top_down_motor(
            speed=100,
            acceleration=200,
            deceleration=400,
        )
        body = player.body
        assert body is not None

        controller.move((1, 1))
        self.game._update(0.25)

        self.assertIs(body.body_type, BodyType.DYNAMIC)
        self.assertEqual(body.gravity_scale, 0)
        self.assertTrue(body.fixed_rotation)
        self.assertAlmostEqual(hypot(*body.velocity), 50)

        controller.move((1, 1))
        self.game._update(0.25)
        self.assertAlmostEqual(hypot(*body.velocity), 100)

        self.game._update(0.25)
        self.assertEqual(body.velocity, (0, 0))

    def test_top_down_motor_ignores_world_gravity(self) -> None:
        self.game.physics.gravity = (0, -500)
        player = self.game.add_rect(
            80,
            100,
            (255, 255, 255),
            20,
            20,
        )
        player.add_top_down_motor()

        for _ in range(10):
            self.game._update(1 / 60)

        self.assertAlmostEqual(player.y, 100)

    def test_platformer_motor_detects_ground_and_jumps(self) -> None:
        self.game.physics.gravity = (0, -600)
        floor = self.game.add_rect(
            160,
            10,
            (255, 255, 255),
            320,
            20,
            collider=True,
        )
        player = self.game.add_rect(
            80,
            30,
            (255, 255, 255),
            20,
            20,
        )
        controller = player.add_platformer_motor(
            speed=120,
            jump_speed=300,
        )

        self.game._update(0)

        self.assertTrue(controller.grounded)
        self.assertEqual(controller.ground_objects, (floor,))
        self.assertGreater(controller.ground_normal[1], 0.9)

        controller.jump()
        self.game._update(1 / 60)

        self.assertFalse(controller.grounded)
        self.assertEqual(controller.velocity[1], 300)

    def test_double_jump_is_optional_and_only_allows_one_air_jump(self) -> None:
        for enabled in (False, True):
            for falling in (False, True):
                with self.subTest(enabled=enabled, falling=falling):
                    self.game = Game(320, 180)
                    self.game.physics.gravity = (0, -600)
                    self.game.add_rect(
                        160, 10, (255, 255, 255), 320, 20, collider=True
                    )
                    player = self.game.add_rect(80, 30, (255, 255, 255), 20, 20)
                    motor = player.add_platformer_motor(
                        jump_speed=300, allow_double_jump=enabled,
                    )
                    self.game._update(0)
                    motor.jump()
                    self.game._update(1 / 60)
                    self.assertEqual(motor.velocity[1], 300)
                    for _ in range(35 if falling else 5):
                        self.game._update(1 / 60)
                    self.assertEqual(motor.falling, falling)
                    motor.jump()
                    self.game._update(1 / 60)
                    if enabled:
                        self.assertEqual(motor.velocity[1], 300)
                    else:
                        self.assertLess(motor.velocity[1], 300)
                    motor.jump()
                    self.game._update(1 / 60)
                    self.assertLess(motor.velocity[1], 300)
                    self.assertFalse(motor.grounded)

    def test_double_jump_resets_on_landing(self) -> None:
        self.game.physics.gravity = (0, -600)
        self.game.add_rect(
            160, 10, (255, 255, 255), 320, 20, collider=True
        )
        player = self.game.add_rect(80, 30, (255, 255, 255), 20, 20)
        motor = player.add_platformer_motor(jump_speed=300, allow_double_jump=True)
        self.game._update(0)
        for cycle in range(2):
            with self.subTest(cycle=cycle):
                for _ in range(2):
                    motor.jump()
                    self.game._update(1 / 60)
                    self.assertEqual(motor.velocity[1], 300)
                    self.assertFalse(motor.grounded)
                for _ in range(150):
                    self.game._update(1 / 60)
                    if motor.grounded:
                        break
                self.assertTrue(motor.grounded)

    def test_double_jump_after_coyote_jump_or_walking_off_ledge(self) -> None:
        for coyote_jump in (False, True):
            with self.subTest(coyote_jump=coyote_jump):
                self.game = Game(320, 180)
                self.game.physics.gravity = (0, -600)
                self.game.add_rect(
                    50, 10, (255, 255, 255), 100, 20, collider=True
                )
                player = self.game.add_rect(50, 30, (255, 255, 255), 20, 20)
                motor = player.add_platformer_motor(
                    jump_speed=300, coyote_time=0.15, allow_double_jump=True,
                )
                self.game._update(0)
                body = player.body
                assert body is not None
                body.teleport(200, 100)
                if coyote_jump:
                    motor.jump()
                    self.game._update(1 / 60)
                    self.assertEqual(motor.velocity[1], 300)
                else:
                    for _ in range(12):
                        self.game._update(1 / 60)
                motor.jump()
                self.game._update(1 / 60)
                self.assertEqual(motor.velocity[1], 300)
                motor.jump()
                self.game._update(1 / 60)
                self.assertLess(motor.velocity[1], 300)

    def test_ground_jump_works_with_coyote_time_disabled(self) -> None:
        self.game.add_rect(
            160, 10, (255, 255, 255), 320, 20, collider=True
        )
        player = self.game.add_rect(80, 30, (255, 255, 255), 20, 20)
        motor = player.add_platformer_motor(coyote_time=0)
        self.game._update(0)
        self.assertTrue(motor.grounded)
        motor.jump()
        self.game._update(1 / 60)
        self.assertEqual(motor.velocity[1], motor.jump_speed)

    def test_exhausted_double_jump_still_buffers_jump_on_landing(self) -> None:
        self.game.physics.gravity = (0, -600)
        self.game.add_rect(
            160, 10, (255, 255, 255), 320, 20, collider=True
        )
        player = self.game.add_rect(80, 30, (255, 255, 255), 20, 20)
        motor = player.add_platformer_motor(
            jump_speed=300, jump_buffer_time=0.35, allow_double_jump=True,
        )
        self.game._update(0)
        for _ in range(2):
            motor.jump()
            self.game._update(1 / 60)
        body = player.body
        assert body is not None
        body.teleport(80, 45)
        body.velocity = (0, -100)
        motor.jump()
        self.game._update(1 / 60)
        self.assertLess(motor.velocity[1], 0)
        for _ in range(30):
            self.game._update(1 / 60)
            if motor.velocity[1] == 300:
                break
        self.assertEqual(motor.velocity[1], 300)
        motor.jump()
        self.game._update(1 / 60)
        self.assertEqual(motor.velocity[1], 300)

    def test_platformer_motor_moves_and_limits_fall_speed(self) -> None:
        self.game.physics.gravity = (0, -2000)
        player = self.game.add_rect(
            80,
            140,
            (255, 255, 255),
            20,
            20,
        )
        controller = player.add_platformer_motor(
            speed=100,
            air_acceleration=200,
            max_fall_speed=150,
        )

        for _ in range(30):
            controller.move(1)
            self.game._update(1 / 60)

        self.assertAlmostEqual(controller.velocity[0], 100)
        self.assertGreaterEqual(controller.velocity[1], -150)
        self.assertTrue(controller.falling)

    def test_platformer_motor_supports_coyote_time(self) -> None:
        self.game.physics.gravity = (0, -600)
        self.game.add_rect(
            50,
            10,
            (255, 255, 255),
            100,
            20,
            collider=True,
        )
        player = self.game.add_rect(
            50,
            30,
            (255, 255, 255),
            20,
            20,
        )
        controller = player.add_platformer_motor(
            jump_speed=300,
            coyote_time=0.15,
        )
        self.game._update(0)
        body = player.body
        assert body is not None

        body.teleport(200, 30)
        controller.jump()
        self.game._update(1 / 60)

        self.assertEqual(controller.velocity[1], 300)

    def test_platformer_motor_buffers_jump_before_landing(self) -> None:
        self.game.physics.gravity = (0, -600)
        self.game.add_rect(
            160,
            10,
            (255, 255, 255),
            320,
            20,
            collider=True,
        )
        player = self.game.add_rect(
            80,
            45,
            (255, 255, 255),
            20,
            20,
        )
        controller = player.add_platformer_motor(
            jump_speed=300,
            jump_buffer_time=0.35,
        )
        controller.jump()

        jumped = False
        for _ in range(30):
            self.game._update(1 / 60)
            if controller.velocity[1] == 300:
                jumped = True
                break

        self.assertTrue(jumped)

    def test_only_one_character_controller_can_be_added(self) -> None:
        player = self.game.add_rect(
            80,
            60,
            (255, 255, 255),
            20,
            20,
        )
        player.add_top_down_motor()

        with self.assertRaisesRegex(
            ValueError,
            "already has a movement motor",
        ):
            player.add_platformer_motor()


if __name__ == "__main__":
    unittest.main()
