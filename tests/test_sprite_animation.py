import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pyglet
from pyglet import gl
from pyglet.image.buffer import Framebuffer
from pyglet.math import Mat4
from g import (
    Game,
    Animation,
    Collider,
    GameObject,
    Image,
    SceneSerializer,
    SpriteAnimation,
    SpriteAnimationFrame,
    SpriteAnimator,
    SpriteRenderer,
    create_sprite,
    create_stateful_sprite,
)
from g.resources import source_of
from g.inspector import inspector_fields
from g.persistence import SceneFormatError, scene_types


class SpriteRendererTests(unittest.TestCase):
    def test_static_sprite_can_be_hidden_and_shown_again(self) -> None:
        sprite = create_sprite(
            Image.solid(8, 6, (255, 0, 0)),
            10,
            20,
        )
        renderer = sprite.require_component(SpriteRenderer)

        renderer.sync()
        sprite.visible = False
        renderer.sync()
        self.assertFalse(renderer.drawable.visible)

        sprite.visible = True
        renderer.sync()
        self.assertTrue(renderer.drawable.visible)

    def test_flips_compose_with_world_scale_without_changing_transform_or_collider(self) -> None:
        sprite = create_sprite(Image.solid(8, 6, (255, 0, 0)), 10, 20)
        renderer = sprite.require_component(SpriteRenderer)
        collider = sprite.require_component(Collider)
        self.assertEqual(collider.shape, "box")
        sprite.scale_x, sprite.scale_y = 2, 3
        parent = GameObject(5, 7)
        parent.scale_x, parent.scale_y = -2, 4
        parent.add_child(sprite)
        geometry = collider.geometry
        for flip_x, flip_y in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(flip_x=flip_x, flip_y=flip_y):
                renderer.flip_x, renderer.flip_y = flip_x, flip_y
                renderer.sync()
                self.assertEqual(renderer.drawable.scale_x, 4 if flip_x else -4)
                self.assertEqual(renderer.drawable.scale_y, -12 if flip_y else 12)
                self.assertEqual(sprite.world_scale, (-4, 12))
                self.assertEqual(collider.geometry, geometry)

    def test_flip_properties_are_inspectable_and_round_trip(self) -> None:
        game = Game(32, 32)
        sprite = game.add(GameObject())
        sprite.add_component(SpriteRenderer(flip_x=True, flip_y=True))
        serializer = SceneSerializer()
        document = serializer.capture(game.scenes.current, asset_root=".")
        loaded = serializer.restore(game, document, asset_root=".")
        renderer = loaded.objects[0].require_component(SpriteRenderer)
        self.assertTrue(renderer.flip_x)
        self.assertTrue(renderer.flip_y)
        metadata = inspector_fields(SpriteRenderer)
        self.assertFalse(metadata["flip_x"].default)
        self.assertFalse(metadata["flip_y"].default)
        template = scene_types.component_template("g.sprite_renderer")
        self.assertFalse(template["properties"]["flip_x"])
        self.assertFalse(template["properties"]["flip_y"])
        components = document.objects[0]["components"]
        assert isinstance(components, list)
        component = components[0]
        assert isinstance(component, dict)
        properties = component["properties"]
        assert isinstance(properties, dict)
        for name in ("flip_x", "flip_y"):
            properties.pop(name)
        loaded = serializer.restore(game, document, asset_root=".")
        renderer = loaded.objects[0].require_component(SpriteRenderer)
        self.assertFalse(renderer.flip_x)
        self.assertFalse(renderer.flip_y)
        properties["flip_x"] = "yes"
        with self.assertRaisesRegex(SceneFormatError, "flip_x must be a boolean"):
            serializer.restore(game, document, asset_root=".")

    def test_atlas_uvs_are_inset_without_mutating_shared_texture(self) -> None:
        atlas = Image.solid(8, 8, (255, 0, 0))
        texture = source_of(atlas).get_texture()
        frame = texture.get_region(2, 2, 4, 4)
        original = frame.tex_coords
        renderer = SpriteRenderer(Image._from_source(frame))
        coordinates = renderer.drawable.image.tex_coords
        self.assertEqual(frame.tex_coords, original)
        self.assertEqual(min(coordinates[0::3]), 2.5 / 8)
        self.assertEqual(max(coordinates[0::3]), 5.5 / 8)
        self.assertEqual(min(coordinates[1::3]), 2.5 / 8)
        self.assertEqual(max(coordinates[1::3]), 5.5 / 8)

    def test_animation_frame_changes_preserve_flips_and_center(self) -> None:
        sprite = create_sprite(Image.solid(4, 4, (255, 0, 0)), 0, 0)
        renderer = sprite.require_component(SpriteRenderer)
        renderer.flip_x = True
        next_frame = Image.solid(6, 8, (0, 255, 0))
        animator = sprite.add_component(SpriteAnimator({"walk": SpriteAnimation((
            SpriteAnimationFrame(renderer.image, 0.1), SpriteAnimationFrame(next_frame, 0.1),
        ))}, "walk"))
        animator.update(0.1)
        renderer.sync()
        self.assertIs(renderer.image, next_frame)
        self.assertEqual(renderer.drawable.scale_x, -1)
        self.assertEqual((renderer.drawable.image.anchor_x, renderer.drawable.image.anchor_y), (3, 4))

    def test_linear_filtered_edges_and_flip_output(self) -> None:
        window = pyglet.window.Window(width=32, height=32, visible=False)
        self.addCleanup(window.close)
        window.switch_to()
        target = pyglet.image.Texture.create(32, 32)
        self.addCleanup(target.delete)
        framebuffer = Framebuffer()
        self.addCleanup(framebuffer.delete)
        framebuffer.attach_texture(target)
        source = pyglet.image.ImageData(2, 2, "RGBA", bytes((
            255, 0, 0, 255, 0, 255, 0, 255,
            0, 0, 255, 255, 255, 255, 0, 255,
        )))
        sprite = GameObject(16, 16)
        sprite.scale_x = sprite.scale_y = 16
        renderer = sprite.add_component(SpriteRenderer(Image._from_source(source)))
        self.addCleanup(renderer.drawable.delete)
        texture = source.get_texture()
        gl.glBindTexture(texture.target, texture.id)
        for parameter in (gl.GL_TEXTURE_MIN_FILTER, gl.GL_TEXTURE_MAG_FILTER):
            value = gl.GLint()
            gl.glGetTexParameteriv(texture.target, parameter, value)
            self.assertEqual(value.value, gl.GL_LINEAR)
        previous = gl.GLint()
        gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING, previous)
        try:
            framebuffer.bind()
            gl.glViewport(0, 0, 32, 32)
            window.projection = Mat4.orthogonal_projection(0, 32, 0, 32, -1, 1)
            window.view = Mat4()
            for flip_x, flip_y, expected in (
                (False, False, (255, 0, 0)), (True, False, (0, 255, 0)),
                (False, True, (0, 0, 255)), (True, True, (255, 255, 0)),
            ):
                renderer.flip_x, renderer.flip_y = flip_x, flip_y
                renderer.sync()
                gl.glClear(gl.GL_COLOR_BUFFER_BIT)
                renderer.drawable.draw()
                pixels = target.get_image_data().get_data("RGBA", 32 * 4)
                actual = tuple(pixels[:3])
                with self.subTest(flip_x=flip_x, flip_y=flip_y):
                    for channel, desired in zip(actual, expected):
                        self.assertLessEqual(abs(channel - desired), 8)
        finally:
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, previous.value)


class SpriteAnimatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.idle = Image.solid(8, 8, (255, 255, 255))
        self.walk_a = Image.solid(8, 8, (255, 0, 0))
        self.walk_b = Image.solid(8, 8, (0, 255, 0))
        self.sprite = create_sprite(self.idle, 0, 0)
        self.assertIs(type(self.sprite), GameObject)
        self.animator = self.sprite.add_component(
            SpriteAnimator(
                {
                    "idle": SpriteAnimation(
                        (SpriteAnimationFrame(self.idle, 0.5),)
                    ),
                    "walk": SpriteAnimation(
                        (
                            SpriteAnimationFrame(self.walk_a, 0.1),
                            SpriteAnimationFrame(self.walk_b, 0.2),
                        )
                    ),
                    "attack": SpriteAnimation(
                        (
                            SpriteAnimationFrame(self.walk_a, 0.1),
                            SpriteAnimationFrame(self.walk_b, 0.1),
                        ),
                        loop=False,
                    ),
                },
                "idle",
            )
        )

    def test_animator_requires_sprite_renderer(self) -> None:
        renderer = self.sprite.require_component(SpriteRenderer)

        self.assertIs(renderer.image, self.idle)

    def test_play_switches_and_loops_animation_states(self) -> None:
        renderer = self.sprite.require_component(SpriteRenderer)

        self.animator.play("walk")
        self.assertIs(renderer.image, self.walk_a)
        self.animator.update(0.1)
        self.assertIs(renderer.image, self.walk_b)
        self.animator.update(0.2)
        self.assertIs(renderer.image, self.walk_a)

    def test_playing_current_state_does_not_restart_without_request(
        self,
    ) -> None:
        self.animator.play("walk")
        self.animator.update(0.1)

        self.animator.play("walk")
        self.assertEqual(self.animator.frame_index, 1)
        self.animator.play("walk", restart=True)
        self.assertEqual(self.animator.frame_index, 0)

    def test_non_looping_state_stops_on_last_frame(self) -> None:
        self.animator.play("attack")

        self.animator.update(1)

        self.assertFalse(self.animator.playing)
        self.assertEqual(self.animator.frame_index, 1)
        self.assertIs(
            self.sprite.require_component(SpriteRenderer).image, self.walk_b
        )

    def test_pause_and_resume_control_frame_advancement(self) -> None:
        self.animator.play("walk")
        self.animator.pause()
        self.animator.update(0.1)
        self.assertEqual(self.animator.frame_index, 0)

        self.animator.resume()
        self.animator.update(0.1)
        self.assertEqual(self.animator.frame_index, 1)

    def test_animation_data_is_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "duration"):
            SpriteAnimationFrame(self.idle, 0)
        with self.assertRaisesRegex(ValueError, "contain frames"):
            SpriteAnimation(())
        with self.assertRaisesRegex(ValueError, "unknown initial"):
            SpriteAnimator(
                {
                    "idle": SpriteAnimation(
                        (SpriteAnimationFrame(self.idle, 0.1),)
                    )
                },
                "missing",
            )

    def test_static_and_empty_states_do_not_resize_colliders(self) -> None:
        large = Image.solid(32, 24, (255, 0, 0))
        sprite = create_stateful_sprite({"idle": self.idle, "large": large, "hidden": None}, "idle", 0, 0)
        collider = sprite.require_component(Collider)
        self.assertEqual(collider.shape, "box")
        renderer = sprite.require_component(SpriteRenderer)
        sprite.set_state("large")
        self.assertIs(renderer.image, large)
        self.assertEqual((collider.width, collider.height), (8, 8))
        sprite.set_state("hidden")
        self.assertIsNone(renderer.image)
        sprite.set_state("idle")
        self.assertIs(renderer.image, self.idle)

    def test_animation_assets_use_simulation_not_pyglet_clock(self) -> None:
        asset = Animation.from_images([self.walk_a, self.walk_b], 0.1)
        with patch("pyglet.clock.schedule_once") as schedule:
            sprite = create_sprite(asset, 0, 0)
            animator = sprite.require_component(SpriteAnimator)
            renderer = sprite.require_component(SpriteRenderer)
            self.assertIsInstance(renderer.image, Image)
            self.assertEqual(animator.frame_index, 0)
            animator.update(0.1)
            self.assertEqual(animator.frame_index, 1)
            schedule.assert_not_called()
        assert renderer.image is not None
        self.assertEqual(source_of(renderer.image), source_of(self.walk_b))

    def test_decoded_non_looping_asset_holds_final_frame(self) -> None:
        asset = Animation.from_images([self.walk_a, self.walk_b], 0.1, loop=False)
        animator = create_sprite(asset, 0, 0).require_component(SpriteAnimator)
        self.assertFalse(animator.states["default"].loop)
        animator.update(10)
        self.assertEqual(animator.frame_index, 1)
        self.assertFalse(animator.playing)

    def test_renderer_rejects_independent_animation_playback(self) -> None:
        asset = Animation.from_images([self.walk_a, self.walk_b], 0.1)
        with self.assertRaisesRegex(TypeError, "SpriteAnimator"):
            SpriteRenderer(asset)  # pyright: ignore[reportArgumentType]
        renderer = self.sprite.require_component(SpriteRenderer)
        with self.assertRaisesRegex(TypeError, "SpriteAnimator"):
            renderer.image = asset  # pyright: ignore[reportAttributeAccessIssue]
        self.assertIs(renderer.image, self.idle)

    def test_disabled_component_and_object_freeze_animation(self) -> None:
        game = Game(320, 180)
        game.add(self.sprite)
        scene = game.scenes.current
        assert scene is not None
        self.animator.play("walk")
        self.animator.enabled = False
        scene._update(0.1)
        self.assertEqual(self.animator.frame_index, 0)
        self.animator.enabled = True
        self.sprite.enabled = False
        scene._update(0.1)
        self.assertEqual(self.animator.frame_index, 0)
        self.sprite.enabled = True
        scene._update(0.1)
        self.assertEqual(self.animator.frame_index, 1)

    def test_image_and_animation_asset_states_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "idle.png"
            source_of(self.idle).save(str(image_path))
            # A two-frame 1x1 GIF with 100ms frame durations.
            (root / "run.gif").write_bytes(bytes.fromhex(
                "47494638396101000100800000ffffff ff0000"
                "21f904000a0000002c0000000001000100000202440100"
                "21f904000a0000002c00000000010001000002024c01003b"
            ))
            game = Game(320, 180)
            sprite = game.add(create_stateful_sprite(
                {"idle": Image(image_path), "run": Animation(root / "run.gif"), "hidden": None},
                "run", 0, 0,
            ))
            serializer = SceneSerializer()
            document = serializer.capture(game.scenes.current, asset_root=root)
            restored = serializer.restore(game, document, asset_root=root)
            animator = restored.objects[0].require_component(SpriteAnimator)
            self.assertIsInstance(animator.states["idle"].source, Image)
            self.assertIsInstance(animator.states["run"].source, Animation)
            self.assertIsNone(animator.states["hidden"].frames[0].image)
            self.assertEqual(len(animator.states["run"].frames), 2)
            self.assertEqual(animator.state, "run")
            animator.update(0.1)
            self.assertEqual(animator.frame_index, 1)
            self.assertEqual(len(sprite.components), len(restored.objects[0].components))

    def test_animator_states_round_trip_through_scene_document(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            idle_path = root / "idle.png"
            walk_path = root / "walk.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(8, 8).save(str(idle_path))
            pyglet.image.SolidColorImagePattern(
                (255, 0, 0, 255)
            ).create_image(8, 8).save(str(walk_path))
            game = Game(320, 180)
            sprite = game.add(
                create_sprite(Image(idle_path), 10, 20)
            )
            sprite.add_component(
                SpriteAnimator(
                    {
                        "idle": SpriteAnimation(
                            (
                                SpriteAnimationFrame(
                                    Image(idle_path),
                                    0.25,
                                ),
                            )
                        ),
                        "walk": SpriteAnimation(
                            (
                                SpriteAnimationFrame(
                                    Image(idle_path),
                                    0.1,
                                ),
                                SpriteAnimationFrame(
                                    Image(walk_path),
                                    0.2,
                                ),
                            ),
                            loop=False,
                        ),
                    },
                    "idle",
                    autoplay=False,
                )
            )
            serializer = SceneSerializer()

            document = serializer.capture(
                game.scenes.current,
                asset_root=root,
            )
            restored = serializer.restore(
                game,
                document,
                asset_root=root,
            )

        loaded = restored.objects[0].require_component(SpriteAnimator)
        self.assertEqual(loaded.initial_state, "idle")
        self.assertFalse(loaded.autoplay)
        self.assertEqual(tuple(loaded.states), ("idle", "walk"))
        self.assertFalse(loaded.states["walk"].loop)
        self.assertEqual(
            [frame.duration for frame in loaded.states["walk"].frames],
            [0.1, 0.2],
        )


if __name__ == "__main__":
    unittest.main()
