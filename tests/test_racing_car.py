import unittest
from pathlib import Path
from unittest.mock import patch

from examples.racing.car import Car, CarController, CarEffects, OilSurface
from examples.racing.race import configure_race
from g import (
    BodyType,
    Collider,
    Game,
    GameObject,
    Key,
    Modifiers,
    PhysicsBody,
    ParticleEffect,
    SceneSerializer,
    TrailRenderer,
)
from g.components.renderer import set_vertex_attribute_region


class RacingCarTests(unittest.TestCase):
    def create_effects(self) -> tuple[GameObject, Car, CarEffects]:
        configure_race("bots")
        game = Game(800, 600)
        path = Path(__file__).parent.parent / "examples/racing/racing.scene.json"
        scene = SceneSerializer().load(game, path)
        owner = scene.require_child("Race Car 1")
        return owner, owner.require_component(Car), owner.require_component(CarEffects)

    def create_car(
        self,
    ) -> tuple[Game, GameObject, PhysicsBody, Car]:
        game = Game(320, 180)
        owner = game.add(GameObject())
        owner.add_component(Collider(width=24, height=40))
        body = owner.add_component(PhysicsBody(BodyType.DYNAMIC))
        car = owner.add_component(Car())
        car.on_ready()
        return game, owner, body, car

    def test_car_requires_dynamic_body_and_collider(self) -> None:
        owner = GameObject()
        with self.assertRaisesRegex(ValueError, "requires PhysicsBody"):
            owner.add_component(Car())

        owner.add_component(Collider(width=24, height=40))
        owner.add_component(PhysicsBody(BodyType.KINEMATIC))
        car = owner.add_component(Car())
        with self.assertRaisesRegex(ValueError, "dynamic PhysicsBody"):
            car.on_ready()

    def test_acceleration_braking_grip_and_steering(self) -> None:
        _, owner, body, car = self.create_car()

        car.drive(1, 1)
        car.update(0.5)

        self.assertEqual(tuple(body.velocity), (0, 260))
        self.assertGreater(body.angular_velocity, 0)
        self.assertEqual(body.gravity_scale, 0)
        self.assertFalse(body.fixed_rotation)

        body.velocity = (100, 200)
        car.drive(0, 0, brake=True)
        car.update(0.1)

        self.assertEqual(tuple(body.velocity), (28, 110))
        self.assertEqual(body.angular_velocity, 0)

        owner.rotation = 90
        body.velocity = (200, 0)
        car.drive(1, 0)
        car.update(0)
        self.assertEqual(tuple(body.velocity), (200, 0))

    def test_reverse_steering_turns_the_opposite_direction(self) -> None:
        _, _, body, car = self.create_car()
        body.velocity = (0, -100)

        car.drive(-1, 1)
        car.update(0)

        self.assertLess(body.angular_velocity, 0)

    def test_exposes_slip_telemetry_and_applies_oil_grip(self) -> None:
        _, _, body, car = self.create_car()
        body.velocity = (100, 200)
        car.drive(0, 0)
        car.update(0)

        self.assertEqual(car.forward_speed, 200)
        self.assertEqual(car.lateral_speed, 100)
        self.assertEqual(car.slip, 0.5)

        car.set_grip_multiplier(0.25)
        car.update(0.1)
        self.assertEqual(car.grip_multiplier, 0.25)
        self.assertEqual(tuple(body.velocity), (82, 182))
        with self.assertRaisesRegex(ValueError, "grip multiplier"):
            car.set_grip_multiplier(float("nan"))

    def test_controller_maps_default_axes_and_space_brake(self) -> None:
        game, owner, _, car = self.create_car()
        controller = owner.add_component(CarController())
        controller.on_ready()
        game.input._press_key(Key.W, Modifiers.NONE)
        game.input._press_key(Key.D, Modifiers.NONE)
        game.input._press_key(Key.SPACE, Modifiers.NONE)

        with patch.object(car, "drive") as drive:
            controller.update(1 / 60)

        drive.assert_called_once_with(1.0, 1.0, brake=True)
        game.input._release_key(Key.SPACE)
        controller.update(0.5)
        body = owner.require_component(PhysicsBody)
        self.assertEqual(tuple(body.velocity), (0, 260))
        self.assertGreater(body.angular_velocity, 0)

    def test_controls_and_parameters_reject_non_finite_values(self) -> None:
        _, _, _, car = self.create_car()

        with self.assertRaisesRegex(ValueError, "controls must be finite"):
            car.drive(float("nan"), 0)
        car.max_forward_speed = float("inf")
        with self.assertRaisesRegex(
            ValueError,
            "max_forward_speed must be finite and non-negative",
        ):
            car.update(1 / 60)

    def test_racing_scene_wires_drift_trails_sparks_and_oil(self) -> None:
        configure_race("bots")
        path = (
            Path(__file__).parent.parent
            / "examples"
            / "racing"
            / "racing.scene.json"
        )
        game = Game(800, 600)
        scene = SceneSerializer().load(game, path)
        owner = scene.require_child("Race Car 1")
        car = owner.require_component(Car)
        body = owner.require_component(PhysicsBody)
        effects = owner.require_component(CarEffects)
        left = owner.require_child("Rear Left Effects")
        self.assertTrue(left.render_behind_parent)
        self.assertLess(left.z_index, owner.z_index)
        trail = left.require_component(TrailRenderer)
        self.assertIsNotNone(trail.image)
        assert trail.image is not None
        self.assertEqual(
            trail.image._filename,
            (
                path.parent
                / "kenney_racing-pack"
                / "PNG"
                / "Objects"
                / "skidmark_short_1.png"
            ).resolve(),
        )
        particles = tuple(
            component for component in left.components
            if isinstance(component, ParticleEffect)
        )
        sparks = next(
            component for component in particles
            if component.effect == "Drift Sparks.particle.json"
        )
        oil_splash = next(
            component for component in particles
            if component.effect == "Oil Splash.particle.json"
        )

        body.velocity = (100, 180)
        car.drive(0, 0)
        car._update_motion(0)
        effects.update(0)
        self.assertTrue(trail.emitting)
        self.assertTrue(sparks.playing)

        effects.enter_oil()
        self.assertEqual(car.grip_multiplier, effects.oil_grip)
        self.assertEqual(oil_splash.particle_count, 1)
        effects.exit_oil()
        self.assertEqual(car.grip_multiplier, 0)
        effects.update(6)
        self.assertEqual(car.grip_multiplier, 1)

        oil = scene.require_child("oil")
        self.assertIsInstance(
            oil.require_component(OilSurface),
            OilSurface,
        )

    def test_oil_removes_lateral_grip_for_exactly_six_seconds_after_exit(self) -> None:
        owner, car, effects = self.create_effects()
        body = owner.require_component(PhysicsBody)
        owner.rotation = 0
        effects.enter_oil()
        self.assertEqual(effects.oil_grip, 0)
        self.assertEqual(effects.oil_duration, 6)
        body.velocity = (100, 180)
        car.drive(1, 1)
        car._update_motion(0.1)
        self.assertEqual(body.velocity.x, 100)
        self.assertGreater(body.velocity.y, 180)
        self.assertGreater(body.angular_velocity, 0)

        effects.update(20)
        self.assertEqual(car.grip_multiplier, 0)
        effects.exit_oil()
        effects.update(3)
        self.assertEqual(car.grip_multiplier, 0)
        effects.update(2)
        self.assertEqual(car.grip_multiplier, 0)
        effects.update(1)
        self.assertEqual(car.grip_multiplier, 1)
        car._update_motion(0.1)
        self.assertEqual(body.velocity.x, 28)

    def test_oil_refreshes_and_waits_for_all_overlapping_surfaces(self) -> None:
        _, car, effects = self.create_effects()
        effects.enter_oil()
        effects.enter_oil()
        effects.exit_oil()
        effects.update(10)
        self.assertEqual(car.grip_multiplier, 0)
        effects.stay_on_oil()
        effects.exit_oil()
        effects.update(5)
        self.assertEqual(car.grip_multiplier, 0)

        effects.enter_oil()
        effects.exit_oil()
        effects.update(5)
        self.assertEqual(car.grip_multiplier, 0)
        effects.update(1)
        self.assertEqual(car.grip_multiplier, 1)

    def test_oil_leaves_stationary_translucent_round_marks_instead_of_skids(self) -> None:
        owner, car, effects = self.create_effects()
        body = owner.require_component(PhysicsBody)
        owner.rotation = 0
        body.velocity = (100, 180)
        car._update_motion(0)
        effects.update(0)
        self.assertTrue(all(trail.emitting for trail in effects.trails))
        self.assertTrue(all(sparks.playing for sparks in effects.sparks))
        effects.enter_oil()
        effects.update(0)
        self.assertFalse(any(trail.emitting for trail in effects.trails))
        self.assertFalse(any(sparks.playing for sparks in effects.sparks))
        oil = effects.oil_splashes[0]
        before = oil.player.snapshots()["Oil"][0]
        self.assertEqual(before.velocity, (0, 0))
        self.assertLess(before.color[3], 100)
        self.assertGreaterEqual(before.size, 12)
        self.assertGreaterEqual(before.lifetime, 6)
        effects.update(0.1)
        self.assertEqual(oil.particle_count, 1)
        owner.x += 16
        effects.exit_oil()
        effects.update(0.1)
        self.assertEqual(oil.particle_count, 2)
        oil.update(1)
        after = oil.player.snapshots()["Oil"][0]
        self.assertEqual(before.position, after.position)
        self.assertLess(after.color[3], before.color[3])
        texture = oil._fallback
        width, height = texture.width, texture.height
        pixels = texture.get_image_data().get_bytes("RGBA", width * 4)
        self.assertEqual(pixels[3], 0)
        center_alpha = pixels[((height // 2) * width + width // 2) * 4 + 3]
        self.assertGreater(center_alpha, 240)
        with patch(
            "g.components.particle_effect.set_vertex_attribute_region",
            wraps=set_vertex_attribute_region,
        ) as upload:
            oil.sync()
        colors = next(
            call.args[2]
            for call in upload.call_args_list
            if call.args[1] == "colors"
        )
        self.assertEqual(tuple(colors[:4]), after.color)
        effects.update(5.9)
        self.assertTrue(all(trail.emitting for trail in effects.trails))
        self.assertTrue(all(sparks.playing for sparks in effects.sparks))
        oil.update(9)
        self.assertEqual(oil.particle_count, 0)

    def test_sparks_use_fire_colors_without_additive_whiteout(self) -> None:
        _, _, effects = self.create_effects()
        sparks = effects.sparks[0]
        appearance = sparks.definition.emitters[0].appearance
        self.assertEqual(appearance.blend_mode, "alpha")
        self.assertEqual(appearance.color, (255, 135, 20, 255))
        self.assertEqual(appearance.end_color, (210, 35, 2, 0))
        sparks.emit(1)
        sparks.update(0.1)
        color = sparks.player.snapshots()["Sparks"][0].color
        self.assertGreater(color[0], color[1])
        self.assertGreater(color[1], color[2])
        with patch(
            "g.components.particle_effect.set_vertex_attribute_region",
            wraps=set_vertex_attribute_region,
        ) as upload:
            sparks.sync()
        colors = next(
            call.args[2]
            for call in upload.call_args_list
            if call.args[1] == "colors"
        )
        self.assertEqual(tuple(colors[:4]), color)

    def test_oil_rejects_invalid_duration_grip_and_delta_time(self) -> None:
        _, _, effects = self.create_effects()
        for delta_time in (-1, float("nan"), float("inf")):
            with self.assertRaisesRegex(ValueError, "delta_time"):
                effects.update(delta_time)
        for duration in (0, -1, float("nan"), float("inf")):
            effects.oil_duration = duration
            with self.assertRaisesRegex(ValueError, "oil duration"):
                effects.enter_oil()
        effects.oil_duration = 6
        for grip in (-1, 2, float("nan"), float("inf")):
            effects.oil_grip = grip
            with self.assertRaisesRegex(ValueError, "oil grip"):
                effects.update(0)


if __name__ == "__main__":
    unittest.main()
