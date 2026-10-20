import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pyglet
from pyglet import gl
from pyglet.image.buffer import Framebuffer

from g import (
    Game,
    Image,
    CircleRenderer,
    PolygonRenderer,
    SpriteRenderer,
    TextRenderer,
    PASSTHROUGH_FRAGMENT_SOURCE,
    PostProcess,
    PostProcessPipeline,
    Shader,
    ShapeStyle,
)
from g.window import Window


SHAPE_FRAGMENT_SOURCE = """
#version 150 core
in vec4 vertex_colors;
out vec4 final_color;
uniform float strength;

void main()
{
    final_color = vec4(vertex_colors.rgb * strength, vertex_colors.a);
}
"""


class ShaderTests(unittest.TestCase):
    def test_shader_compiles_sources_and_exposes_uniforms(self) -> None:
        shader = Shader.for_shapes(SHAPE_FRAGMENT_SOURCE)

        shader["strength"] = 0.5

        self.assertIn("strength", shader.uniforms)
        self.assertAlmostEqual(shader["strength"], 0.5)

    def test_shape_shader_can_be_changed_and_restored(self) -> None:
        game = Game(100, 100)
        circle = game.add_circle(
            50,
            50,
            (255, 255, 255),
            10,
            style=ShapeStyle(outline=(255, 0, 0)),
        )
        renderer = circle.require_component(CircleRenderer)
        defaults = tuple(drawable.program for drawable in renderer.drawables)
        shader = Shader.for_shapes(SHAPE_FRAGMENT_SOURCE)

        renderer.shader = shader

        self.assertIs(renderer.shader, shader)
        self.assertTrue(
            all(
                drawable.program is shader.program
                for drawable in renderer.drawables
            )
        )

        renderer.shader = None
        self.assertEqual(
            tuple(drawable.program for drawable in renderer.drawables),
            defaults,
        )

    def test_polygon_keeps_shader_when_scale_rebuilds_fill(self) -> None:
        game = Game(100, 100)
        polygon = game.add_polygon(
            [(0, 0), (20, 0), (20, 20), (0, 20)]
        )
        renderer = polygon.require_component(PolygonRenderer)
        shader = Shader.for_shapes(SHAPE_FRAGMENT_SOURCE)
        renderer.shader = shader

        polygon.scale_x = 2
        renderer.sync()

        self.assertTrue(
            all(
                drawable.program is shader.program
                for drawable in renderer.drawables
            )
        )

    def test_sprite_and_text_accept_matching_pyglet_shaders(self) -> None:
        game = Game(100, 100)
        sprite = game.add_sprite(
            20, 20, Image.solid(8, 8, (255, 255, 255))
        )
        text = game.add_text(50, 50, "Shader")
        sprite_shader = Shader.for_sprites(pyglet.sprite.fragment_source)
        text_shader = Shader.for_text(
            pyglet.text.layout.layout_fragment_source
        )

        sprite_renderer = sprite.require_component(SpriteRenderer)
        text_renderer = text.require_component(TextRenderer)
        sprite_renderer.shader = sprite_shader
        text_renderer.shader = text_shader

        self.assertIs(sprite_renderer.drawable.program, sprite_shader.program)
        self.assertIs(text_renderer.drawable.program, text_shader.program)

    def test_empty_shader_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            Shader("", SHAPE_FRAGMENT_SOURCE)


class PostProcessTests(unittest.TestCase):
    def test_passthrough_post_process_renders_framebuffer(self) -> None:
        effect = PostProcess(PASSTHROUGH_FRAGMENT_SOURCE)
        self.addCleanup(effect.delete)

        effect.begin(64, 32)
        effect.end()
        effect.draw(64, 32)

        texture = effect._texture
        if texture is None:
            self.fail("post-process texture was not created")
        self.assertEqual(
            (texture.width, texture.height),
            (64, 32),
        )

    def test_post_process_restores_enclosing_framebuffer(self) -> None:
        texture = pyglet.image.Texture.create(64, 32)
        framebuffer = Framebuffer()
        framebuffer.attach_texture(texture)
        effect = PostProcess(PASSTHROUGH_FRAGMENT_SOURCE)
        self.addCleanup(effect.delete)
        self.addCleanup(framebuffer.delete)
        self.addCleanup(texture.delete)
        framebuffer.bind()
        try:
            effect.begin(64, 32)
            effect.end()
            active = (gl.GLint * 1)()
            gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING, active)
            self.assertEqual(active[0], framebuffer.id)
        finally:
            framebuffer.unbind()

    def test_scene_updates_post_process_time(self) -> None:
        game = Game(100, 100)
        effect = Mock(enabled=True)
        game.scenes.current.post_process = effect

        game._update(0.25)

        effect.update.assert_called_once_with(0.25)

    @patch("g.window.pyglet.gl.glViewport")
    @patch("g.window.pyglet.gl.glGetIntegerv")
    def test_window_routes_scene_through_post_process(
        self,
        get_viewport: Mock,
        set_viewport: Mock,
    ) -> None:
        def provide_viewport(name, values) -> None:
            del name
            values[:] = (10, 20, 180, 80)

        get_viewport.side_effect = provide_viewport
        effect = PostProcess.__new__(PostProcess)
        effect.enabled = True
        effect.begin = Mock()
        effect.end = Mock()
        effect.draw = Mock()
        scene = SimpleNamespace(
            post_process=effect,
            camera=SimpleNamespace(view_matrix="camera"),
            _draw_background=Mock(),
            _draw=Mock(),
            _draw_ui=Mock(),
        )
        game = SimpleNamespace(
            scenes=SimpleNamespace(current=scene),
            _draw_transition=Mock(),
        )
        pyglet_window = Mock()
        pyglet_window.view = "normal"
        pyglet_window.get_framebuffer_size.return_value = (200, 100)
        window = Window.__new__(Window)
        window._window = pyglet_window
        window._game = cast(Game, game)

        window._on_draw()

        effect.begin.assert_called_once_with(200, 100)
        effect.end.assert_called_once_with()
        effect.draw.assert_called_once_with(200, 100)
        game._draw_transition.assert_called_once_with()
        set_viewport.assert_called_with(10, 20, 180, 80)

    @patch("g.window.pyglet.gl.glViewport")
    @patch("g.window.pyglet.gl.glGetIntegerv")
    def test_window_does_not_touch_viewport_without_post_process(
        self,
        get_viewport: Mock,
        set_viewport: Mock,
    ) -> None:
        scene = SimpleNamespace(
            post_process=None,
            camera=SimpleNamespace(view_matrix="camera"),
            _draw_background=Mock(),
            _draw=Mock(),
            _draw_ui=Mock(),
        )
        game = SimpleNamespace(
            scenes=SimpleNamespace(current=scene),
            _draw_transition=Mock(),
        )
        pyglet_window = Mock()
        pyglet_window.view = "normal"
        window = Window.__new__(Window)
        window._window = pyglet_window
        window._game = cast(Game, game)

        window._on_draw()

        get_viewport.assert_not_called()
        set_viewport.assert_not_called()


class PostProcessPipelineTests(unittest.TestCase):
    def make_effect(self) -> PostProcess:
        effect = PostProcess(PASSTHROUGH_FRAGMENT_SOURCE)
        self.addCleanup(effect.delete)
        return effect

    def test_pipeline_collection_can_be_modified(self) -> None:
        first = self.make_effect()
        second = self.make_effect()
        third = self.make_effect()
        pipeline = PostProcessPipeline(first)
        self.addCleanup(pipeline.delete)

        pipeline.add(second)
        pipeline.insert(1, third)
        self.assertEqual(list(pipeline), [first, third, second])

        pipeline.move(second, 0)
        self.assertEqual(list(pipeline), [second, first, third])
        self.assertIs(pipeline.remove(first), first)
        self.assertEqual(pipeline[:], [second, third])

        with self.assertRaisesRegex(ValueError, "already"):
            pipeline.add(second)

        pipeline.clear()
        self.assertEqual(len(pipeline), 0)

    def test_pipeline_updates_only_enabled_effects(self) -> None:
        first = self.make_effect()
        disabled = self.make_effect()
        disabled.enabled = False
        pipeline = PostProcessPipeline(first, disabled)
        self.addCleanup(pipeline.delete)

        pipeline.update(0.5)

        self.assertEqual(first.time, 0.5)
        self.assertEqual(disabled.time, 0)

    def test_pipeline_renders_enabled_effects_in_order(self) -> None:
        first = self.make_effect()
        disabled = self.make_effect()
        disabled.enabled = False
        second = self.make_effect()
        third = self.make_effect()
        pipeline = PostProcessPipeline(first, disabled, second, third)
        self.addCleanup(pipeline.delete)
        calls = Mock()

        pipeline.begin(64, 32)
        pipeline.end()
        with (
            patch.object(
                first,
                "_draw_texture",
                wraps=first._draw_texture,
            ) as draw_first,
            patch.object(
                second,
                "_draw_texture",
                wraps=second._draw_texture,
            ) as draw_second,
            patch.object(
                third,
                "_draw_texture",
                wraps=third._draw_texture,
            ) as draw_third,
        ):
            calls.attach_mock(draw_first, "first")
            calls.attach_mock(draw_second, "second")
            calls.attach_mock(draw_third, "third")
            pipeline.draw(64, 32)

        self.assertEqual(
            [call[0] for call in calls.mock_calls],
            ["first", "second", "third"],
        )

    def test_empty_pipeline_draws_passthrough(self) -> None:
        pipeline = PostProcessPipeline()
        self.addCleanup(pipeline.delete)

        pipeline.begin(64, 32)
        pipeline.end()
        pipeline.draw(64, 32)

        self.assertIsNotNone(pipeline._capture._texture)


if __name__ == "__main__":
    unittest.main()
