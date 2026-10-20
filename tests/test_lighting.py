import unittest
from unittest.mock import patch

import pyglet
from pyglet import gl
from pyglet.image.buffer import Framebuffer
from pyglet.math import Mat4

from g import (
    Game,
    GameObject,
    Light2D,
    ObjectLayer,
    PointLight2D,
    PostProcess,
    PASSTHROUGH_FRAGMENT_SOURCE,
    SceneSerializer,
    ShadowOccluder2D,
    SpotLight2D,
)
from g.lighting import _ray_segment_distance, _visibility_polygon
from g.rendering import SceneRenderer


class LightingComponentTests(unittest.TestCase):
    def test_light_base_is_abstract(self) -> None:
        with self.assertRaises(TypeError):
            Light2D()

    def test_point_and_spot_validate_their_ranges_and_angles(self) -> None:
        with self.assertRaisesRegex(ValueError, "radius"):
            PointLight2D(0)
        with self.assertRaisesRegex(ValueError, "angle"):
            SpotLight2D(angle=361)
        with self.assertRaisesRegex(ValueError, "inner_angle"):
            SpotLight2D(angle=40, inner_angle=50)

        light = SpotLight2D(300, angle=50)

        self.assertEqual(light.maximum_range, 300)
        self.assertEqual(light.inner_angle, 40)

    def test_occluder_factories_and_world_transform(self) -> None:
        owner = GameObject(10, 20, rotation=90, scale=2)
        occluder = owner.add_component(
            ShadowOccluder2D.rectangle(10, 20)
        )

        self.assertEqual(len(occluder.world_segments), 4)
        self.assertEqual(
            {(round(x), round(y)) for x, y in occluder.world_points},
            {
                (-10, 30),
                (-10, 10),
                (30, 30),
                (30, 10),
            },
        )
        self.assertEqual(len(ShadowOccluder2D.circle(8).points), 16)

    def test_scene_lighting_is_automatically_opted_in(self) -> None:
        game = Game(100, 100)
        obj = game.add(GameObject())
        light = obj.add_component(PointLight2D())

        self.assertTrue(game.scenes.current.lighting_active)
        light.enabled = False
        self.assertFalse(game.scenes.current.lighting_active)

    def test_shadow_ray_stops_at_nearest_occluder_edge(self) -> None:
        distance = _ray_segment_distance(
            (0, 0),
            0,
            ((5, -2), (5, 2)),
            10,
        )

        self.assertEqual(distance, 5)

    def test_visibility_polygon_is_clipped_by_occluder(self) -> None:
        light_owner = GameObject()
        light = light_owner.add_component(
            PointLight2D(100, casts_shadows=True)
        )
        wall = GameObject(20, 0)
        occluder = wall.add_component(
            ShadowOccluder2D.rectangle(2, 40)
        )

        polygon = _visibility_polygon(light, (occluder,))
        forward = min(
            polygon,
            key=lambda point: abs(point[1]),
        )

        self.assertAlmostEqual(forward[0], 19, places=3)

    def test_lights_occluders_and_ambient_color_round_trip(self) -> None:
        game = Game(100, 100)
        scene = game.scenes.current
        scene.ambient_light = (12, 34, 56, 255)
        light_object = game.add(GameObject(20, 30, rotation=15))
        light_object.add_component(
            SpotLight2D(
                180,
                angle=60,
                inner_angle=40,
                color=(255, 180, 90),
                intensity=1.5,
                falloff=2,
                casts_shadows=True,
                shadow_mask=4,
            )
        )
        wall = game.add(GameObject(50, 40))
        wall.add_component(
            ShadowOccluder2D.rectangle(
                20,
                10,
                occlusion_mask=4,
            )
        )
        serializer = SceneSerializer()

        document = serializer.capture(scene, asset_root=".")
        restored = serializer.restore(game, document, asset_root=".")

        self.assertEqual(restored.ambient_light, (12, 34, 56, 255))
        restored_light = restored.objects[0].require_component(SpotLight2D)
        restored_wall = restored.objects[1].require_component(
            ShadowOccluder2D
        )
        self.assertEqual(restored_light.range, 180)
        self.assertEqual(restored_light.angle, 60)
        self.assertEqual(restored_light.inner_angle, 40)
        self.assertEqual(restored_light.shadow_mask, 4)
        self.assertEqual(restored_wall.occlusion_mask, 4)


class LightingRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = pyglet.window.Window(width=32, height=24, visible=False)
        self.addCleanup(self.window.close)
        self.window.switch_to()
        self.game = Game(32, 24, bg_color=(255, 255, 255, 255))
        self.scene = self.game.scenes.current
        self.scene.ambient_light = (0, 0, 0, 255)
        self.texture = pyglet.image.Texture.create(32, 24)
        self.addCleanup(self.texture.delete)
        self.framebuffer = Framebuffer()
        self.framebuffer.attach_texture(self.texture)
        self.addCleanup(self.framebuffer.delete)
        self.addCleanup(self._delete_objects)

    def _delete_objects(self) -> None:
        for layer in self.scene.layers:
            if isinstance(layer, ObjectLayer):
                layer.delete()

    def render(self) -> bytes:
        previous = gl.GLint()
        viewport = (gl.GLint * 4)()
        gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING, previous)
        gl.glGetIntegerv(gl.GL_VIEWPORT, viewport)
        projection, view = self.window.projection, self.window.view
        renderer = SceneRenderer()
        self.addCleanup(renderer.delete)
        try:
            self.framebuffer.bind()
            gl.glViewport(0, 0, 32, 24)
            self.window.projection = Mat4.orthogonal_projection(
                0, 32, 0, 24, -1, 1
            )
            self.window.view = Mat4()
            gl.glClearColor(0, 0, 0, 1)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            with patch.object(
                self.window,
                "get_framebuffer_size",
                return_value=(32, 24),
            ):
                renderer.render(self.game, self.window)
            return bytes(
                self.texture.get_image_data().get_data("RGBA", 32 * 4)
            )
        finally:
            self.window.projection, self.window.view = projection, view
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, previous.value)
            gl.glViewport(*viewport)

    @staticmethod
    def pixel(pixels: bytes, x: int, y: int) -> tuple[int, ...]:
        start = (y * 32 + x) * 4
        return tuple(pixels[start:start + 4])

    def test_point_light_brightens_center_but_not_distant_world(self) -> None:
        self.game.add_point_light(
            16,
            12,
            8,
            color=(255, 0, 0),
            intensity=1,
        )

        pixels = self.render()

        self.assertGreater(self.pixel(pixels, 16, 12)[0], 200)
        self.assertLess(self.pixel(pixels, 0, 0)[0], 10)

    def test_spotlight_uses_owner_rotation(self) -> None:
        self.game.add_spot_light(
            16,
            12,
            12,
            angle=45,
            inner_angle=30,
            rotation=0,
        )

        pixels = self.render()

        self.assertGreater(self.pixel(pixels, 21, 12)[0], 80)
        self.assertLess(self.pixel(pixels, 11, 12)[0], 10)

    def test_shadow_occluder_blocks_a_shadow_casting_light(self) -> None:
        self.game.add_point_light(
            8,
            12,
            24,
            casts_shadows=True,
        )
        wall = self.game.add(GameObject(16, 12))
        wall.add_component(
            ShadowOccluder2D.rectangle(2, 20)
        )

        pixels = self.render()

        self.assertGreater(self.pixel(pixels, 12, 12)[0], 100)
        self.assertLess(self.pixel(pixels, 24, 12)[0], 10)

    def test_lighting_composes_inside_existing_post_process(self) -> None:
        effect = PostProcess(PASSTHROUGH_FRAGMENT_SOURCE)
        self.addCleanup(effect.delete)
        self.scene.post_process = effect
        self.game.add_point_light(16, 12, 8)

        pixels = self.render()

        self.assertGreater(self.pixel(pixels, 16, 12)[0], 200)
        self.assertLess(self.pixel(pixels, 0, 0)[0], 10)
