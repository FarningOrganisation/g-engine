import tempfile
import unittest
from array import array
from pathlib import Path

from g_editor.application import ImGuiEditor
from g import Game, GameObject, ParticleEffect, SceneSerializer
from g.components.particle_effect import _soft_circle_image
from g.particles import (
    PARTICLE_PRESETS,
    FloatRange,
    ParticleAppearanceDefinition,
    ParticleBurst,
    ParticleCircle,
    ParticleEffectDefinition,
    ParticleEmitterDefinition,
    ParticleFormatError,
    ParticleLine,
    ParticleMotionDefinition,
    ParticlePlayer,
    ParticleRectangle,
    ParticleTransform,
    PythonParticleSimulation,
    particle_preset,
)


class ParticleDefinitionTests(unittest.TestCase):
    def test_built_in_presets_are_valid_fresh_definitions(self) -> None:
        self.assertEqual(
            PARTICLE_PRESETS,
            ("fire", "smoke", "sparks", "explosion", "rain", "snow"),
        )
        for name in PARTICLE_PRESETS:
            first = particle_preset(name)
            second = particle_preset(name)
            self.assertIsNot(first, second)
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first.emitters), 1)
        self.assertEqual(
            {emitter.name for emitter in particle_preset("explosion").emitters},
            {"Flash", "Debris", "Smoke"},
        )
        with self.assertRaisesRegex(KeyError, "unknown"):
            particle_preset("missing")

    def test_effect_requires_unique_named_emitters(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            ParticleEffectDefinition(())
        with self.assertRaisesRegex(ValueError, "unique"):
            ParticleEffectDefinition((
                ParticleEmitterDefinition(name="Smoke"),
                ParticleEmitterDefinition(name="Smoke"),
            ))

    def test_rejects_invalid_ranges_and_emitter_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "minimum"):
            FloatRange(2, 1)
        with self.assertRaisesRegex(ValueError, "lifetime"):
            ParticleEmitterDefinition(lifetime=FloatRange(0, 1))
        with self.assertRaisesRegex(ValueError, "spread"):
            ParticleEmitterDefinition(spread=361)
        with self.assertRaisesRegex(ValueError, "capacity"):
            ParticleEmitterDefinition(capacity=0)
        with self.assertRaisesRegex(ValueError, "duration"):
            ParticleEmitterDefinition(duration=0)

    def test_effect_asset_round_trips_multiple_emitters(self) -> None:
        definition = ParticleEffectDefinition((
            ParticleEmitterDefinition(
                name="Flash",
                duration=0.2,
                loop=False,
                bursts=(ParticleBurst(0, 1),),
                appearance=ParticleAppearanceDefinition(
                    color=(255, 200, 50, 255),
                    blend_mode="additive",
                ),
            ),
            ParticleEmitterDefinition(
                name="Smoke",
                rate=30,
                shape=ParticleCircle(8),
                motion=ParticleMotionDefinition((0, 12), 0.2),
            ),
        ))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "effect.particle.json"
            definition.write(path)
            restored = ParticleEffectDefinition.read(path)

        self.assertEqual(restored, definition)

    def test_invalid_asset_format_is_rejected(self) -> None:
        with self.assertRaisesRegex(ParticleFormatError, "format"):
            ParticleEffectDefinition.from_dict({})

    def test_spawn_shape_dimensions_must_be_non_negative(self) -> None:
        with self.assertRaisesRegex(ValueError, "dimensions"):
            ParticleRectangle(-1, 1)
        with self.assertRaisesRegex(ValueError, "radius"):
            ParticleCircle(-1)


class PythonParticleSimulationTests(unittest.TestCase):
    def test_storage_is_preallocated_and_capacity_is_bounded(self) -> None:
        simulation = PythonParticleSimulation(
            ParticleEmitterDefinition(capacity=3)
        )
        self.assertIsInstance(simulation._position_x, array)
        self.assertEqual(len(simulation._position_x), 3)
        self.assertEqual(simulation.emit(5), 3)
        self.assertEqual(simulation.dropped, 2)

    def test_fixed_seed_produces_repeatable_particles(self) -> None:
        emitter = ParticleEmitterDefinition(
            lifetime=FloatRange(1, 2),
            speed=FloatRange(10, 20),
            spread=180,
            shape=ParticleCircle(5),
        )
        first = PythonParticleSimulation(emitter, seed=42)
        second = PythonParticleSimulation(emitter, seed=42)
        first.emit(10)
        second.emit(10)
        self.assertEqual(first.snapshot(), second.snapshot())
        first.reset()
        first.emit(10)
        self.assertEqual(first.snapshot(), second.snapshot())

    def test_transform_applies_to_spawn_position_and_direction(self) -> None:
        simulation = PythonParticleSimulation(
            ParticleEmitterDefinition(
                lifetime=FloatRange(2, 2),
                speed=FloatRange(10, 10),
                direction=0,
                spread=0,
                shape=ParticleLine((1, 0), (1, 0)),
            )
        )
        simulation.emit(
            1,
            ParticleTransform((5, 10), rotation=90, scale=(2, 1)),
        )
        particle = simulation.snapshot()[0]
        self.assertAlmostEqual(particle.position[0], 5)
        self.assertAlmostEqual(particle.position[1], 8)
        self.assertAlmostEqual(particle.velocity[0], 0, places=5)
        self.assertAlmostEqual(particle.velocity[1], -10)

    def test_update_applies_motion_and_expires_particles(self) -> None:
        simulation = PythonParticleSimulation(
            ParticleEmitterDefinition(
                lifetime=FloatRange(1, 1),
                speed=FloatRange(10, 10),
                direction=0,
                spread=0,
                motion=ParticleMotionDefinition((0, 10)),
            )
        )
        simulation.emit(2)
        simulation.update(0.5)
        particle = simulation.snapshot()[0]
        self.assertAlmostEqual(particle.position[0], 5)
        self.assertAlmostEqual(particle.position[1], 2.5)
        simulation.update(0.5)
        self.assertEqual(simulation.count, 0)


class ParticlePlayerTests(unittest.TestCase):
    def test_player_runs_continuous_and_burst_emitters(self) -> None:
        player = ParticlePlayer(ParticleEffectDefinition((
            ParticleEmitterDefinition(
                name="Burst",
                duration=0.5,
                loop=False,
                rate=2,
                bursts=(ParticleBurst(0, 3),),
                lifetime=FloatRange(2, 2),
            ),
        )), seed=1)
        player.play()
        player.update(0.5)
        self.assertEqual(player.particle_count, 4)
        player.update(2)
        self.assertEqual(player.particle_count, 0)
        self.assertFalse(player.playing)

    def test_player_targets_named_emitter(self) -> None:
        player = ParticlePlayer(ParticleEffectDefinition((
            ParticleEmitterDefinition(name="A"),
            ParticleEmitterDefinition(name="B"),
        )))
        self.assertEqual(player.emit(2, emitter="B"), 2)
        snapshots = player.snapshots()
        self.assertEqual(len(snapshots["A"]), 0)
        self.assertEqual(len(snapshots["B"]), 2)
        with self.assertRaises(KeyError):
            player.emit(1, emitter="missing")

    def test_pause_stop_and_clear_have_distinct_behavior(self) -> None:
        player = ParticlePlayer(ParticleEffectDefinition((
            ParticleEmitterDefinition(
                rate=10,
                lifetime=FloatRange(2, 2),
            ),
        )))
        player.play()
        player.update(0.1)
        player.pause()
        player.update(1)
        self.assertEqual(player.particle_count, 1)
        player.stop()
        player.update(0.5)
        self.assertEqual(player.particle_count, 1)
        player.stop(clear=True)
        self.assertEqual(player.particle_count, 0)

    def test_warmup_prerolls_each_emitter_once(self) -> None:
        player = ParticlePlayer(ParticleEffectDefinition((
            ParticleEmitterDefinition(
                warmup=0.5,
                rate=0,
                bursts=(ParticleBurst(0, 1),),
                lifetime=FloatRange(2, 2),
            ),
        )))
        player.play()
        player.update(0)
        particle = next(iter(player.snapshots().values()))[0]
        self.assertAlmostEqual(particle.age, 0.5, places=6)
        player.update(0)
        self.assertEqual(player.particle_count, 1)


class ParticleComponentTests(unittest.TestCase):
    def test_untextured_particle_fallback_is_circular(self) -> None:
        image = _soft_circle_image(8)
        pixels = image.get_bytes("RGBA", 8 * 4)

        def alpha(x: int, y: int) -> int:
            return pixels[(y * 8 + x) * 4 + 3]

        self.assertEqual(alpha(0, 0), 0)
        self.assertEqual(alpha(7, 7), 0)
        self.assertGreater(alpha(3, 3), 240)

    def test_component_round_trips_effect_asset_and_plays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effect_path = root / "smoke.particle.json"
            ParticleEffectDefinition((
                ParticleEmitterDefinition(
                    name="Smoke",
                    rate=10,
                    lifetime=FloatRange(2, 2),
                ),
            )).write(effect_path)
            game = Game(100, 100)
            obj = GameObject()
            component = obj.add_component(ParticleEffect(
                ParticleEffectDefinition.read(effect_path),
                effect=effect_path.name,
                autoplay=True,
            ))
            game.add(obj)
            game.scenes.current._initialize_components()
            game._update(0.1)
            self.assertEqual(component.particle_count, 1)

            scene_path = root / "scene.scene.json"
            SceneSerializer().save(game.scenes.current, scene_path)
            restored = SceneSerializer().load(game, scene_path)
            loaded = restored.objects[0].require_component(ParticleEffect)
            self.assertEqual(loaded.effect, effect_path.name)
            self.assertEqual(loaded.definition.emitters[0].name, "Smoke")


class ParticlePanelTests(unittest.TestCase):
    def test_create_edit_preview_and_save_particle_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            editor = ImGuiEditor(
                asset_root=Path(directory),
                visible=False,
            )
            try:
                path = editor._create_particle_asset()
                editor._add_particle_emitter()
                player = editor._particle_player
                assert player is not None
                player.play()
                editor._update_particles_preview(0.2)
                editor.on_draw()
                editor._save_particle_asset()

                restored = ParticleEffectDefinition.read(path)
                self.assertEqual(len(restored.emitters), 2)
                self.assertFalse(editor._particle_dirty)
                self.assertIsNone(editor.state.error)
            finally:
                editor.state.dirty = False
                editor._close()

    def test_adding_component_from_preset_creates_editable_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = ImGuiEditor(asset_root=root, visible=False)
            try:
                record = editor.state.add_object("empty")
                editor._add_particle_component("fire")
                components = record.get("components")
                assert isinstance(components, list)
                component = components[-1]
                assert isinstance(component, dict)
                properties = component.get("properties")
                assert isinstance(properties, dict)
                effect = properties.get("effect")
                self.assertEqual(effect, "Fire.particle.json")
                created = root / str(effect)
                self.assertTrue(created.is_file())
                self.assertEqual(
                    ParticleEffectDefinition.read(created),
                    particle_preset("fire"),
                )
                self.assertEqual(
                    editor._particle_path,
                    created.resolve(),
                )
            finally:
                editor.state.dirty = False
                editor._close()


if __name__ == "__main__":
    unittest.main()
