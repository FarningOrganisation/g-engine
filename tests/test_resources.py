import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, call, patch

from g import Game, Image, ResourceManager, Sound, TextureFilter
from g.resources import clamp_texture_edges
from pyglet import gl


class TextureTests(unittest.TestCase):
    @patch("g.resources.gl")
    def test_render_target_edges_are_clamped(
        self,
        gl_api: Mock,
    ) -> None:
        gl_api.GL_TEXTURE_2D = gl.GL_TEXTURE_2D
        gl_api.GL_TEXTURE_BINDING_2D = gl.GL_TEXTURE_BINDING_2D
        gl_api.GL_TEXTURE_WRAP_S = gl.GL_TEXTURE_WRAP_S
        gl_api.GL_TEXTURE_WRAP_T = gl.GL_TEXTURE_WRAP_T
        gl_api.GL_CLAMP_TO_EDGE = gl.GL_CLAMP_TO_EDGE
        gl_api.GLint.return_value.value = 0
        texture = Mock(target=gl.GL_TEXTURE_2D, id=17)

        clamp_texture_edges(texture)

        gl_api.glGetIntegerv.assert_called_once()
        self.assertEqual(
            gl_api.glTexParameteri.call_args_list,
            [
                call(
                    gl.GL_TEXTURE_2D,
                    gl.GL_TEXTURE_WRAP_S,
                    gl.GL_CLAMP_TO_EDGE,
                ),
                call(
                    gl.GL_TEXTURE_2D,
                    gl.GL_TEXTURE_WRAP_T,
                    gl.GL_CLAMP_TO_EDGE,
                ),
            ],
        )
        self.assertEqual(
            gl_api.glBindTexture.call_args_list,
            [
                call(gl.GL_TEXTURE_2D, 17),
                call(gl.GL_TEXTURE_2D, 0),
            ],
        )


class SoundTests(unittest.TestCase):
    @patch("g.resources.pyglet.media.load")
    def test_loads_reusable_static_source(self, load: Mock) -> None:
        Sound(Path("assets") / "laser.wav")

        load.assert_called_once_with("assets/laser.wav", streaming=False)

    @patch("g.resources.pyglet.media.load")
    def test_play_returns_pyglet_player_with_default_settings(
        self, load: Mock
    ) -> None:
        player = Mock()
        load.return_value.play.return_value = player

        result = Sound("laser.wav").play()

        load.return_value.play.assert_called_once_with()
        self.assertIs(result, player)
        self.assertEqual(player.volume, 1.0)
        self.assertEqual(player.pitch, 1.0)
        self.assertFalse(player.loop)

    @patch("g.resources.pyglet.media.load")
    def test_play_applies_custom_settings(self, load: Mock) -> None:
        player = Mock()
        load.return_value.play.return_value = player

        Sound("engine.wav").play(volume=0.4, pitch=1.25, loop=True)

        self.assertEqual(player.volume, 0.4)
        self.assertEqual(player.pitch, 1.25)
        self.assertTrue(player.loop)


class ResourceManagerTests(unittest.TestCase):
    def test_game_provides_resource_manager(self) -> None:
        self.assertIsInstance(Game(10, 10).resources, ResourceManager)

    @patch("g.resources.Image")
    def test_load_image_caches_equivalent_paths(self, image_type: Mock) -> None:
        manager = ResourceManager()

        first = manager.load_image("assets/player.png")
        second = manager.load_image(Path("assets/player.png"))
        third = manager.load_image(Path("assets/player.png").resolve())

        self.assertIs(first, second)
        self.assertIs(first, third)
        image_type.assert_called_once_with(Path("assets/player.png").resolve())

    @patch("g.resources.Image")
    def test_image_filtering_has_distinct_cached_variants(
        self,
        image_type: Mock,
    ) -> None:
        image_type.side_effect = [Mock(), Mock()]
        manager = ResourceManager()

        linear = manager.load_image("player.png")
        nearest = manager.load_image(
            "player.png",
            filtering=TextureFilter.NEAREST,
        )

        self.assertIsNot(linear, nearest)
        self.assertEqual(image_type.call_count, 2)
        image_type.assert_any_call(Path("player.png").resolve())
        image_type.assert_any_call(
            Path("player.png").resolve(),
            filtering=TextureFilter.NEAREST,
        )

    @patch("g.resources.Image")
    def test_game_configures_default_texture_filter(
        self,
        image_type: Mock,
    ) -> None:
        game = Game(
            10,
            10,
            texture_filter=TextureFilter.NEAREST,
        )

        game.resources.load_image("player.png")

        image_type.assert_called_once_with(
            Path("player.png").resolve(),
            filtering=TextureFilter.NEAREST,
        )

    @patch("g.resources.Sound")
    def test_load_sound_caches_resource(self, sound_type: Mock) -> None:
        manager = ResourceManager()

        first = manager.load_sound("assets/jump.wav")
        second = manager.load_sound("assets/jump.wav")

        self.assertIs(first, second)
        sound_type.assert_called_once_with(Path("assets/jump.wav").resolve())

    @patch("g.resources.pyglet.font.add_file")
    @patch("g.resources.TTFont")
    def test_load_font_returns_cached_resource(
        self, font_type: Mock, add_file: Mock
    ) -> None:
        manager = ResourceManager()
        font = MagicMock()
        font_type.return_value.__enter__.return_value = font
        font.__getitem__.return_value.getBestFamilyName.return_value = (
            "Game Font"
        )
        font.__getitem__.return_value.getBestSubFamilyName.return_value = (
            "Bold"
        )

        result = manager.load_font(Path("assets/fonts/game.ttf"))

        self.assertEqual(result.family, "Game Font")
        self.assertEqual(result.style, "Bold")
        self.assertEqual(
            result.filename,
            Path("assets/fonts/game.ttf").resolve(),
        )
        self.assertIs(
            result,
            manager.load_font(Path("assets/fonts/game.ttf")),
        )
        font_type.assert_called_once_with(
            Path("assets/fonts/game.ttf").resolve(), lazy=True
        )
        font.__getitem__.assert_called_once_with("name")
        add_file.assert_called_once_with(str(Path("assets/fonts/game.ttf").resolve()))

    def test_asset_root_loads_real_image_and_unloads_by_relative_path(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            filename = root / "player.png"
            Image.solid(3, 5, (255, 0, 0))._source.save(str(filename))
            game = Game(10, 10, asset_root=root)

            with patch("pathlib.Path.cwd", return_value=root / "unrelated"):
                image = game.resources.load_image("player.png")
                self.assertEqual((image.width, image.height), (3, 5))
                self.assertEqual(image._filename, filename)
                self.assertIs(image, game.resources.load_image(filename))
                game.resources.unload("player.png")
                self.assertIsNot(image, game.resources.load_image(filename))

    def test_all_loaders_resolve_against_asset_root(self) -> None:
        root = Path("/tmp/game-assets").resolve()
        manager = ResourceManager(root)
        for method, resource_type, filename in (
            (manager.load_image, "Image", "player.png"),
            (manager.load_animation, "Animation", "walk.gif"),
            (manager.load_sound, "Sound", "jump.wav"),
        ):
            with self.subTest(resource=resource_type), patch(f"g.resources.{resource_type}") as constructor:
                self.assertIs(method(filename), method(root / filename))
                constructor.assert_called_once_with(root / filename)
                manager.unload(filename)
                method(filename)
                self.assertEqual(constructor.call_count, 2)

    @patch("g.resources.pyglet.font.add_file")
    @patch("g.resources.TTFont")
    def test_font_resolves_against_asset_root(self, font_type: Mock, add_file: Mock) -> None:
        root = Path("/tmp/game-assets").resolve()
        manager = ResourceManager(root)
        manager.load_font("fonts/game.ttf")
        font_type.assert_called_once_with(root / "fonts/game.ttf", lazy=True)
        add_file.assert_called_once_with(str(root / "fonts/game.ttf"))

    @patch("g.resources.Image")
    def test_changing_root_does_not_reuse_another_projects_cached_image(self, image_type: Mock) -> None:
        manager = ResourceManager("/tmp/first-project")
        manager.load_image("player.png")
        manager.asset_root = "/tmp/second-project"
        manager.load_image("player.png")
        self.assertEqual(image_type.call_count, 2)

    @patch("g.resources.pyglet.font.add_file")
    @patch("g.resources.TTFont")
    def test_load_font_rejects_missing_family_name(
        self, font_type: Mock, add_file: Mock
    ) -> None:
        font = MagicMock()
        font_type.return_value.__enter__.return_value = font
        font.__getitem__.return_value.getBestFamilyName.return_value = None

        with self.assertRaisesRegex(ValueError, "no family name"):
            ResourceManager().load_font("assets/fonts/broken.ttf")

        add_file.assert_not_called()

    @patch("g.resources.Animation")
    def test_unload_evicts_matching_resource(
        self, animation_type: Mock
    ) -> None:
        manager = ResourceManager()
        manager.load_animation("assets/walk.gif")

        manager.unload(Path("assets/walk.gif"))
        manager.load_animation("assets/walk.gif")

        self.assertEqual(animation_type.call_count, 2)

    @patch("g.resources.Image")
    @patch("g.resources.Sound")
    def test_clear_evicts_all_resources(
        self, sound_type: Mock, image_type: Mock
    ) -> None:
        manager = ResourceManager()
        manager.load_image("assets/player.png")
        manager.load_sound("assets/jump.wav")

        manager.clear()
        manager.load_image("assets/player.png")
        manager.load_sound("assets/jump.wav")

        self.assertEqual(image_type.call_count, 2)
        self.assertEqual(sound_type.call_count, 2)


if __name__ == "__main__":
    unittest.main()
