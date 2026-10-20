import tempfile
import unittest
from pathlib import Path

import pyglet

from g import Game, GameObject, SceneSerializer, TrailRenderer


class TrailRendererTests(unittest.TestCase):
    def test_samples_world_movement_at_a_bounded_distance(self) -> None:
        owner = GameObject()
        trail = owner.add_component(TrailRenderer(
            minimum_distance=2,
            max_segments=3,
        ))

        trail.update(0)
        owner.x = 10
        trail.update(0)

        self.assertEqual(trail.segment_count, 3)
        self.assertEqual(trail._segments[0].start, (4.0, 0.0))
        self.assertEqual(trail._segments[-1].end, (10.0, 0.0))

    def test_stopping_emission_breaks_the_trail(self) -> None:
        owner = GameObject()
        trail = owner.add_component(TrailRenderer(minimum_distance=1))
        trail.update(0)
        owner.x = 2
        trail.update(0)
        self.assertEqual(trail.segment_count, 2)

        trail.emitting = False
        owner.x = 100
        trail.update(0)
        trail.emitting = True
        trail.update(0)
        owner.x = 101
        trail.update(0)

        self.assertEqual(trail.segment_count, 3)
        self.assertEqual(trail._segments[-1].start, (100.0, 0.0))

    def test_segments_expire_and_clear(self) -> None:
        owner = GameObject()
        trail = owner.add_component(TrailRenderer(
            minimum_distance=1,
            lifetime=1,
        ))
        trail.update(0)
        owner.x = 1
        trail.update(0)
        trail.update(1)
        self.assertEqual(trail.segment_count, 0)

        owner.x = 2
        trail.update(0)
        trail.clear()
        self.assertEqual(trail.segment_count, 0)

    def test_scene_round_trip_preserves_configuration(self) -> None:
        game = Game(100, 100)
        owner = game.add(GameObject())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "trail.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(8, 8).save(str(image_path))
            image = game.resources.load_image(image_path)
            owner.add_component(TrailRenderer(
                image=image,
                emitting=False,
                offset=(4, -12),
                width=6,
                minimum_distance=2,
                lifetime=8,
                color=(20, 22, 25, 190),
                fade=False,
                max_segments=400,
            ))
            path = root / "scene.scene.json"
            SceneSerializer().save(game.scenes.current, path)
            restored = SceneSerializer().load(game, path)
        trail = restored.objects[0].require_component(TrailRenderer)
        self.assertIsNotNone(trail.image)
        assert trail.image is not None
        self.assertEqual(trail.image._filename, image_path.resolve())
        self.assertFalse(trail.emitting)
        self.assertEqual(tuple(trail.offset), (4, -12))
        self.assertEqual(trail.width, 6)
        self.assertEqual(trail.minimum_distance, 2)
        self.assertEqual(trail.lifetime, 8)
        self.assertEqual(trail.color, (20, 22, 25, 190))
        self.assertFalse(trail.fade)
        self.assertEqual(trail.max_segments, 400)

    def test_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "width"):
            TrailRenderer(width=0)
        with self.assertRaisesRegex(ValueError, "max_segments"):
            TrailRenderer(max_segments=0)
        with self.assertRaisesRegex(TypeError, "image"):
            TrailRenderer(image="trail.png")  # type: ignore[arg-type]
        trail = TrailRenderer()
        with self.assertRaisesRegex(ValueError, "delta_time"):
            trail.update(-1)


if __name__ == "__main__":
    unittest.main()
