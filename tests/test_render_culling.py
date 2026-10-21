import unittest

import pyglet

from g import (
    CircleRenderer,
    Game,
    GameObject,
    ParticleEffect,
    SceneRenderer,
    TextRenderer,
)


class RenderCullingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = pyglet.window.Window(
            width=160,
            height=120,
            visible=False,
        )
        self.addCleanup(self.window.close)
        self.game = Game(160, 120)
        self.scene = self.game.scenes.current
        self.layer = self.scene.default_layer

    def test_world_renderer_detaches_outside_camera_and_reattaches(self) -> None:
        circle = self.game.add_circle(1000, 1000, (255, 255, 255), 10)
        renderer = circle.require_component(CircleRenderer)

        self.layer.draw(self.scene.camera)

        self.assertIsNone(renderer.drawable.batch)
        circle.position = (80, 60)

        self.layer.draw(self.scene.camera)

        self.assertIs(renderer.drawable.batch, self.game._batch)

    def test_rotated_camera_culling_is_conservative(self) -> None:
        camera = self.scene.camera
        camera.rotation = 45

        self.assertTrue(camera.bounds_visible((70, 50, 90, 70)))
        self.assertFalse(camera.bounds_visible((1000, 1000, 1010, 1010)))

    def test_screen_space_renderer_is_not_camera_culled(self) -> None:
        text = self.game.add_text(
            1000,
            1000,
            "Screen space",
            screen_space=True,
        )
        renderer = text.require_component(TextRenderer)

        self.layer.draw(self.scene.camera)

        self.assertIs(renderer.drawable.batch, self.layer._ui_batch)

    def test_offscreen_particle_effect_releases_its_batch_buffers(self) -> None:
        owner = GameObject(1000, 1000)
        effect = owner.add_component(ParticleEffect(autoplay=False))
        self.game.add(owner)

        self.layer.draw(self.scene.camera)

        self.assertIsNone(effect._batch)
        self.assertEqual(effect._vertex_lists, [])

        owner.position = (80, 60)
        self.layer.draw(self.scene.camera)

        self.assertIs(effect._batch, self.game._batch)
        self.assertTrue(effect._vertex_lists)

    def test_scene_renderer_reports_submitted_and_culled_work(self) -> None:
        self.game.add_circle(80, 60, (255, 255, 255), 10)
        self.game.add_circle(1000, 1000, (255, 255, 255), 10)
        renderer = SceneRenderer()
        self.addCleanup(renderer.delete)

        renderer.render(self.game, self.window)

        stats = renderer.stats
        self.assertEqual(stats.submitted_renderers, 1)
        self.assertEqual(stats.culled_renderers, 1)
        self.assertGreater(stats.submitted_vertices, 0)
        self.assertGreater(stats.batch_draws, 0)
        self.assertGreater(stats.approximate_draw_calls, 0)
