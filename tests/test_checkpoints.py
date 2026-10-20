from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from g import (BodyType, Collider, Game, GameObject, PhysicsBody,
               SceneSerializer, SceneTypeRegistry, Script, SpriteAnimator,
               SpriteAnimation, SpriteAnimationFrame, SpriteRenderer)
from g.persistence import SceneDocument, SceneFormatError, _register_builtins


class Progress(Script):
    speed: float = 10

    def on_ready(self):
        self._progress = 0
        self.game_object.require_component(PhysicsBody).velocity = (0, 0)

    def save_state(self):
        return {"progress": self._progress, "target": getattr(self, "_target", None)}

    def load_state(self, state):
        self._progress = state["progress"]
        self._target = state["target"]


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = SceneTypeRegistry()
        _register_builtins(self.registry)
        self.registry.register_component("test.Progress", Progress)
        self.serializer = SceneSerializer(self.registry)
        self.game = Game(320, 180, asset_root=self.root)
        owner = self.game.add(GameObject(20, 50))
        owner.name = "Player"
        owner.add_component(Collider(width=16, height=16))
        owner.add_component(PhysicsBody(BodyType.KINEMATIC))
        owner.add_component(Progress())
        coin = self.game.add(GameObject(80, 50))
        coin.name = "Coin"
        original = self.serializer.capture(self.game.scenes.current, asset_root=self.root)
        owner.delete()
        coin.delete()
        scene = self.serializer.restore(self.game, original, asset_root=self.root)
        self.game.scenes.add("level", scene)
        self.game.scenes.switch("level")
        self.addCleanup(lambda: self.game.scenes.current.layers._clear())

    def test_checkpoint_restores_destroyed_objects_physics_and_private_state_after_ready(self):
        scene = self.game.scenes.current
        owner = scene.require_child("Player")
        coin = scene.require_child("Coin")
        script = owner.require_component(Progress)
        script._progress = 42
        script._target = owner
        coin.delete()
        owner.x = 100
        owner.require_component(PhysicsBody).velocity = (-25, 12)
        scene.time.elapsed = 15
        path = self.game.scenes.save_checkpoint(directory=self.root)
        self.assertTrue(path.is_file())
        snapshot = SceneDocument.read(path)
        self.assertIn("checkpoint", snapshot.settings)
        self.game.scenes.load_checkpoint(directory=self.root)
        restored = self.game.scenes.current
        new_owner = restored.require_child("Player")
        self.assertIsNot(restored, scene)
        self.assertIsNone(restored.get_child("Coin"))
        self.assertEqual(new_owner.x, 100)
        self.assertEqual(tuple(new_owner.require_component(PhysicsBody).velocity), (-25, 12))
        self.assertEqual(new_owner.require_component(Progress)._progress, 42)
        self.assertIs(new_owner.require_component(Progress)._target, new_owner)
        self.assertEqual(restored.time.elapsed, 15)
        self.assertFalse(scene.layers._layers)
        self.game.scenes.restart()
        restarted = self.game.scenes.current
        self.assertEqual(restarted.require_child("Player").x, 20)
        self.assertIsNotNone(restarted.get_child("Coin"))
        self.assertEqual(restarted.require_child("Player").require_component(Progress)._progress, 0)

    def test_normal_capture_and_save_omit_runtime_state(self):
        document = self.serializer.capture(self.game.scenes.current, asset_root=self.root)
        self.assertNotIn("checkpoint", document.settings)
        component = document.layers[0]["objects"][0]["components"][-1]
        self.assertNotIn("runtime_state", component)

    def test_restore_from_disk_in_new_game_retains_original_restart_source(self):
        self.game.scenes.current.require_child("Coin").delete()
        self.game.scenes.save_checkpoint("slot1", directory=self.root)
        second = Game(320, 180, asset_root=self.root)
        restored = self.serializer.load(second, self.root / "slot1.scene.json", asset_root=self.root)
        second.scenes.add("level", restored)
        second.scenes.switch("level")
        self.assertIsNone(restored.get_child("Coin"))
        second.scenes.restart()
        self.assertIsNotNone(second.scenes.current.get_child("Coin"))
        second.scenes.current.layers._clear()

    def test_operations_requested_in_collision_are_deferred(self):
        original = self.game.scenes.current
        owner = original.require_child("Player")
        other = original.require_child("Coin")
        other.position = owner.position
        other.add_component(Collider(width=16, height=16))
        seen = []

        def restart(other):
            self.game.scenes.restart()
            seen.append(self.game.scenes.current is original)

        owner.on_collision(restart)
        self.game._update(0)
        self.assertEqual(seen, [True])
        self.assertIsNot(self.game.scenes.current, original)

    def test_bad_slots_missing_saves_and_unsupported_versions_fail_explicitly(self):
        for slot in ("../outside", "", "a/b"):
            with self.assertRaises(ValueError):
                self.game.scenes.checkpoint_path(slot, directory=self.root)
        current = self.game.scenes.current
        with self.assertRaises(SceneFormatError):
            self.game.scenes.load_checkpoint(directory=self.root)
        self.assertIs(self.game.scenes.current, current)
        document = self.serializer.capture(current, asset_root=self.root, include_runtime_state=True)
        document.settings["checkpoint"]["version"] = 99
        document.write(self.root / "checkpoint.scene.json")
        with self.assertRaisesRegex(SceneFormatError, "version"):
            self.game.scenes.load_checkpoint(directory=self.root)
        self.assertIs(self.game.scenes.current, current)

    def test_default_path_is_user_directory_and_scene_asset_is_not_overwritten(self):
        path = self.game.scenes.checkpoint_path(application_name="GCheckpointTest")
        self.assertFalse(path.is_relative_to(self.root))
        self.assertEqual(path.name, "checkpoint.scene.json")

    def test_active_callback_timer_is_rejected_instead_of_silently_lost(self):
        scene = self.game.scenes.current
        scene.time.after(2, lambda: None)
        with self.assertRaisesRegex(SceneFormatError, "timers"):
            self.serializer.capture(scene, asset_root=self.root, include_runtime_state=True)

    def test_sprite_animation_elapsed_pause_and_frame_are_restored(self):
        owner = self.game.scenes.current.require_child("Coin")
        owner.add_component(SpriteRenderer())
        animator = owner.add_component(SpriteAnimator({
            "walk": SpriteAnimation((
                SpriteAnimationFrame(None, 0.1), SpriteAnimationFrame(None, 0.2),
            )),
        }, "walk"))
        animator.update(0.15)
        animator.pause()
        self.game.scenes.save_checkpoint(directory=self.root)
        self.game.scenes.load_checkpoint(directory=self.root)
        loaded = self.game.scenes.current.require_child("Coin").require_component(SpriteAnimator)
        self.assertEqual(loaded.frame_index, 1)
        self.assertTrue(loaded.paused)
        self.assertAlmostEqual(loaded._elapsed, 0.15)
        loaded.update(0.2)
        self.assertEqual(loaded.frame_index, 1)
        loaded.resume()
        loaded.update(0.17)
        self.assertEqual(loaded.frame_index, 0)

    def test_hook_failure_keeps_current_scene_and_releases_candidate_objects(self):
        current = self.game.scenes.current
        self.game.scenes.save_checkpoint(directory=self.root)
        with patch.object(Progress, "load_state", side_effect=ValueError("invalid progress")):
            with self.assertRaisesRegex(ValueError, "invalid progress"):
                self.game.scenes.load_checkpoint(directory=self.root)
        self.assertIs(self.game.scenes.current, current)
        self.game._update(1 / 60)
        self.assertIsNotNone(current.require_child("Player").scene)

    def test_serialization_failure_does_not_replace_existing_save(self):
        path = self.game.scenes.save_checkpoint(directory=self.root)
        previous = path.read_bytes()
        script = self.game.scenes.current.require_child("Player").require_component(Progress)
        script._progress = object()
        with self.assertRaises(SceneFormatError):
            self.game.scenes.save_checkpoint(directory=self.root)
        self.assertEqual(path.read_bytes(), previous)
