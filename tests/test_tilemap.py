from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pyglet
from pyglet import gl
from pyglet.image.buffer import Framebuffer
from pyglet.math import Mat4

from g import (
    ColorTileSet,
    Game,
    Image,
    ImageCollectionTileSet,
    ObjectLayer,
    SceneSerializer,
    TileAnimation,
    TileAnimationFrame,
    TileCell,
    TileCollision,
    TileFlags,
    TileLayer,
    TileNavigation,
    TileSet,
)


class TileMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(128, 128)
        self.scene = self.game.scenes.current
        self.image = Image.solid(64, 64, (255, 255, 255))
        self.tileset = TileSet(self.image, 16)

    def test_scene_supports_ordered_object_and_tile_layers(self) -> None:
        ground = self.scene.layers.add_tile("Ground", self.tileset)
        actors = self.scene.layers.add_object("Actors")

        self.assertIsInstance(self.scene.layers[0], ObjectLayer)
        self.assertIs(self.scene.layers[1], ground)
        self.assertIs(self.scene.layers[2], actors)
        self.scene.layers.move(ground, 0)
        self.assertIs(self.scene.layers[0], ground)

        with self.assertRaisesRegex(ValueError, "object layers"):
            self.scene.add(self.game_object(), layer=ground)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "default layer"):
            self.scene.default_layer = ground  # type: ignore[assignment]

    def test_tiles_are_compact_chunk_data_not_game_objects(self) -> None:
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            chunk_size=4,
        )

        ground.set_tile(1, 2, 3)
        ground.set_tile(
            -1,
            -1,
            2,
            flags=TileFlags.FLIP_HORIZONTAL,
        )

        self.assertEqual(ground.tile_at(1, 2), TileCell(3))
        self.assertEqual(
            ground.tile_at(-1, -1),
            TileCell(2, TileFlags.FLIP_HORIZONTAL),
        )
        self.assertEqual(len(ground.chunks), 2)
        self.assertEqual(self.scene.objects, ())

        ground.set_tile(1, 2, None)
        self.assertIsNone(ground.tile_at(1, 2))
        self.assertEqual(len(ground.chunks), 1)

    def test_tileset_has_typed_and_custom_properties(self) -> None:
        animation = TileAnimation(
            (
                TileAnimationFrame(1, 0.1),
                TileAnimationFrame(2, 0.2),
            )
        )
        definition = self.tileset.define(
            0,
            name="grass",
            collision=TileCollision(),
            navigation=TileNavigation(walkable=True, cost=1.5),
            animation=animation,
            tags={"ground", "grass"},
            properties={"footstep_sound": "grass", "damage": 0},
        )

        self.assertEqual(definition.name, "grass")
        self.assertIsNotNone(definition.collision)
        self.assertEqual(definition.navigation, TileNavigation(True, 1.5))
        self.assertEqual(definition.properties["footstep_sound"], "grass")
        self.assertEqual(self.tileset.displayed_tile_id(0, 0), 1)
        self.assertEqual(self.tileset.displayed_tile_id(0, 0.15), 2)

    def test_color_tileset_renders_and_updates_without_assets(self) -> None:
        colors = ColorTileSet(
            [
                (40, 120, 60),
                (70, 90, 180, 200),
            ]
        )
        colors.define(0, name="grass", collision=TileCollision())
        ground = self.scene.layers.add_tile("Ground", colors)
        ground.set_tile(0, 0, 0)
        ground.set_tile(1, 0, 1)

        ground.draw(self.scene.camera)

        chunk = ground.chunks[(0, 0)]
        self.assertEqual(colors.colors[0], (40, 120, 60, 255))
        self.assertEqual(colors.colors[1], (70, 90, 180, 200))
        self.assertFalse(chunk.dirty)

        colors.set_color(0, (200, 80, 30))
        ground.update(0)

        self.assertEqual(colors.colors[0], (200, 80, 30, 255))
        self.assertTrue(chunk.dirty)

    def test_color_tileset_round_trip_preserves_colors_and_metadata(
        self,
    ) -> None:
        colors = ColorTileSet(
            [
                (40, 120, 60),
                (70, 90, 180, 200),
            ]
        )
        colors.define(0, name="ground", collision=TileCollision())
        ground = self.scene.layers.add_tile(
            "Prototype",
            colors,
            collision_enabled=True,
        )
        ground.set_tile(2, 3, 0)

        serializer = SceneSerializer()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = serializer.capture(self.scene, asset_root=root)
            restored = serializer.restore(
                self.game,
                document,
                asset_root=root,
            )

        loaded = restored.layers["Prototype"]
        self.assertIsInstance(loaded, TileLayer)
        assert isinstance(loaded, TileLayer)
        self.assertIsInstance(loaded.tileset, ColorTileSet)
        assert isinstance(loaded.tileset, ColorTileSet)
        self.assertEqual(
            loaded.tileset.colors,
            (
                (40, 120, 60, 255),
                (70, 90, 180, 200),
            ),
        )
        self.assertEqual(loaded.tileset.definition(0).name, "ground")
        self.assertIsNotNone(loaded.tileset.definition(0).collision)
        self.assertEqual(loaded.tile_at(2, 3), TileCell(0))

    def test_polygon_collision_round_trip_preserves_generated_metadata(
        self,
    ) -> None:
        collision = TileCollision(
            shape="polygon",
            polygons=(
                ((0, 0), (0.5, 0), (0, 0.5)),
                ((0.75, 0.75), (1, 0.75), (1, 1)),
            ),
            generated_from_alpha=True,
            alpha_threshold=200,
        )
        colors = ColorTileSet([(255, 255, 255, 255)])
        colors.define(0, collision=collision)
        ground = self.scene.layers.add_tile(
            "Ground",
            colors,
            collision_enabled=True,
        )
        ground.set_tile(0, 0, 0)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = SceneSerializer().capture(
                self.scene,
                asset_root=root,
            )
            restored = SceneSerializer().restore(
                self.game,
                document,
                asset_root=root,
            )

        loaded = restored.layers["Ground"]
        assert isinstance(loaded, TileLayer)
        assert loaded.tileset is not None
        definition = loaded.tileset.definition(0)
        assert definition is not None
        self.assertEqual(
            definition.collision,
            collision,
        )

    def test_image_collection_tileset_combines_individual_images(
        self,
    ) -> None:
        collection = ImageCollectionTileSet(
            [
                Image.solid(12, 20, (255, 0, 0)),
                Image.solid(24, 10, (0, 255, 0)),
            ]
        )
        layer = self.scene.layers.add_tile("Objects", collection)
        layer.set_tile(0, 0, 0)
        layer.set_tile(1, 0, 1)

        layer.draw(self.scene.camera)

        self.assertEqual(collection.tile_count, 2)
        self.assertEqual(collection.tile_width, 24)
        self.assertEqual(collection.tile_height, 20)
        self.assertEqual(collection.texture_region(0).width, 24)
        self.assertEqual(collection.texture_region(1).height, 20)
        self.assertIsNotNone(layer.chunks[(0, 0)]._vertex_list)

    def test_image_collection_round_trip_preserves_image_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, color, size in (
                ("crate.png", (150, 90, 40, 255), (12, 20)),
                ("key.png", (240, 210, 50, 255), (24, 10)),
            ):
                pyglet.image.SolidColorImagePattern(color).create_image(
                    *size
                ).save(str(root / name))
            collection = ImageCollectionTileSet(
                [
                    self.game.resources.load_image(root / "crate.png"),
                    self.game.resources.load_image(root / "key.png"),
                ]
            )
            collection.define(
                0,
                name="crate",
                collision=TileCollision(),
            )
            layer = self.scene.layers.add_tile(
                "Objects",
                collection,
                collision_enabled=True,
            )
            layer.set_tile(2, 3, 0)
            serializer = SceneSerializer()

            document = serializer.capture(self.scene, asset_root=root)
            restored = serializer.restore(
                self.game,
                document,
                asset_root=root,
            )

        loaded = restored.layers["Objects"]
        self.assertIsInstance(loaded, TileLayer)
        assert isinstance(loaded, TileLayer)
        self.assertIsInstance(loaded.tileset, ImageCollectionTileSet)
        assert isinstance(loaded.tileset, ImageCollectionTileSet)
        self.assertEqual(
            [
                image._filename.name
                for image in loaded.tileset.images
                if image._filename is not None
            ],
            ["crate.png", "key.png"],
        )
        self.assertEqual(loaded.tileset.definition(0).name, "crate")
        self.assertEqual(loaded.tile_at(2, 3), TileCell(0))

    def test_collision_enabled_tiles_are_physics_targets(self) -> None:
        self.tileset.define(0, collision=TileCollision())
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(2, 1, 0)

        self.assertEqual(
            self.game.physics.overlap_point((40, 24)),
            (ground,),
        )
        self.assertIs(
            self.game.physics.raycast((40, 80), (40, 0)),
            ground,
        )

    def test_partial_rectangle_tile_collision_uses_its_geometry(
        self,
    ) -> None:
        self.tileset.define(
            0,
            collision=TileCollision(width=16, height=8),
        )
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(0, 0, 0)

        self.assertEqual(
            self.game.physics.overlap_point((8, 4)),
            (ground,),
        )
        self.assertEqual(
            self.game.physics.overlap_point((8, 12)),
            (),
        )

    def test_polygon_tile_collision_uses_normalized_geometry(self) -> None:
        self.tileset.define(
            0,
            collision=TileCollision.polygon(
                ((0, 0), (1, 0), (0, 1))
            ),
        )
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(0, 0, 0)

        self.assertEqual(
            self.game.physics.overlap_point((2, 2)),
            (ground,),
        )
        self.assertEqual(
            self.game.physics.overlap_point((14, 14)),
            (),
        )

    def test_polygon_tile_collision_supports_multiple_components(
        self,
    ) -> None:
        collision = TileCollision(
            shape="polygon",
            polygons=(
                ((0, 0), (0.25, 0), (0.25, 0.25), (0, 0.25)),
                ((0.75, 0.75), (1, 0.75), (1, 1), (0.75, 1)),
            ),
        )
        self.tileset.define(0, collision=collision)
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(0, 0, 0)

        self.assertEqual(
            self.game.physics.overlap_point((2, 2)),
            (ground,),
        )
        self.assertEqual(
            self.game.physics.overlap_point((14, 14)),
            (ground,),
        )
        self.assertEqual(
            self.game.physics.overlap_point((8, 8)),
            (),
        )

    def test_tile_flags_transform_polygon_collision(self) -> None:
        self.tileset.define(
            0,
            collision=TileCollision.polygon(
                ((0, 0), (0.5, 0), (0, 0.5))
            ),
        )
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(
            0,
            0,
            0,
            flags=TileFlags.FLIP_HORIZONTAL,
        )

        self.assertEqual(
            self.game.physics.overlap_point((14, 2)),
            (ground,),
        )
        self.assertEqual(
            self.game.physics.overlap_point((2, 2)),
            (),
        )

    def test_rotated_tile_flags_rotate_polygon_collision_clockwise(
        self,
    ) -> None:
        self.tileset.define(
            0,
            collision=TileCollision.polygon(
                ((0, 0), (0.5, 0), (0, 0.5))
            ),
        )
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(
            0,
            0,
            0,
            flags=(
                TileFlags.FLIP_DIAGONAL
                | TileFlags.FLIP_HORIZONTAL
            ),
        )

        self.assertEqual(
            self.game.physics.overlap_point((2, 14)),
            (ground,),
        )
        self.assertEqual(
            self.game.physics.overlap_point((14, 2)),
            (),
        )

    def test_adjacent_full_tile_collisions_remain_merged(self) -> None:
        self.tileset.define(0, collision=TileCollision())
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(0, 0, 0)
        ground.set_tile(1, 0, 0)

        self.game.physics.refresh()

        _, _, shapes = self.game.physics._tile_physics[ground]
        self.assertEqual(len(shapes), 1)

    def test_tile_collision_rebuilds_after_live_edits(self) -> None:
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(0, 0, 0)

        self.assertEqual(self.game.physics.overlap_point((8, 8)), ())

        self.tileset.define(0, collision=TileCollision())
        self.assertEqual(
            self.game.physics.overlap_point((8, 8)),
            (ground,),
        )

        ground.set_tile(0, 0, None)
        self.assertEqual(self.game.physics.overlap_point((8, 8)), ())

    def test_tile_collision_respects_layer_switches(self) -> None:
        self.tileset.define(0, collision=TileCollision())
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=False,
        )
        ground.set_tile(0, 0, 0)

        self.assertEqual(self.game.physics.overlap_point((8, 8)), ())

        ground.collision_enabled = True
        self.assertEqual(
            self.game.physics.overlap_point((8, 8)),
            (ground,),
        )

        ground.enabled = False
        self.assertEqual(self.game.physics.overlap_point((8, 8)), ())

    def test_platformer_motor_grounds_on_tile_collision(self) -> None:
        self.tileset.define(0, collision=TileCollision())
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(0, 0, 0)
        player = self.game.add_rect(
            8,
            26,
            (255, 255, 255),
            12,
            20,
        )
        controller = player.add_platformer_motor()

        self.game._update(0)

        self.assertTrue(controller.grounded)
        self.assertEqual(controller.ground_objects, (ground,))

    def test_tile_collision_callbacks_receive_cell_contacts(self) -> None:
        self.tileset.define(0, collision=TileCollision())
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            collision_enabled=True,
        )
        ground.set_tile(0, 0, 0)
        ground.set_tile(1, 0, 0)
        player = self.game.add_rect(
            24,
            18,
            (255, 255, 255),
            8,
            8,
        )
        object_collision = Mock()
        tile_collision = Mock()
        tile_stay = Mock()
        tile_end = Mock()
        player.on_collision(object_collision)
        player.on_tile_collision(tile_collision)
        player.on_tile_collision_stay(tile_stay)
        player.on_tile_collision_end(tile_end)

        self.game._update(0)

        object_collision.assert_not_called()
        tile_collision.assert_called_once()
        contact = tile_collision.call_args.args[0]
        self.assertIs(contact.layer, ground)
        self.assertEqual((contact.column, contact.row), (1, 0))
        self.assertEqual(contact.cell, TileCell(0))
        self.assertIsInstance(contact.point, tuple)
        self.assertIsInstance(contact.normal, tuple)
        self.assertLess(contact.normal[1], 0)

        self.game._update(0)
        tile_stay.assert_called_once()

        player.position = (24, 40)
        self.game._update(0)
        tile_end.assert_called_once()

    def test_chunk_renderer_builds_one_mesh_per_nonempty_chunk(self) -> None:
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            chunk_size=4,
        )
        ground.set_tile(0, 0, 0)
        ground.set_tile(1, 0, 1)

        ground.draw(self.scene.camera)

        chunk = ground.chunks[(0, 0)]
        self.assertIsNotNone(chunk._vertex_list)
        assert chunk._vertex_list is not None
        self.assertEqual(chunk._vertex_list.count, 8)
        self.assertFalse(chunk.dirty)

    def test_adjacent_tiles_do_not_sample_transparent_atlas_neighbors(self) -> None:
        window = pyglet.window.Window(width=64, height=64, visible=False)
        self.addCleanup(window.close)
        window.switch_to()
        texture = pyglet.image.Texture.create(64, 64)
        self.addCleanup(texture.delete)
        framebuffer = Framebuffer()
        self.addCleanup(framebuffer.delete)
        framebuffer.attach_texture(texture)
        previous_framebuffer = gl.GLint()
        viewport = (gl.GLint * 4)()
        gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING, previous_framebuffer)
        gl.glGetIntegerv(gl.GL_VIEWPORT, viewport)
        # An opaque atlas cell surrounded by transparent, unused cells.
        pixels = bytearray(24 * 24 * 4)
        for y in range(8, 16):
            for x in range(8, 16):
                start = (y * 24 + x) * 4
                pixels[start : start + 4] = bytes((255, 255, 255, 255))
        image = Image._from_source(
            pyglet.image.ImageData(24, 24, "RGBA", bytes(pixels))
        )
        tileset = TileSet(image, 8)
        ground = self.scene.layers.add_tile("Seam test", tileset, chunk_size=1)
        for row in range(4):
            for column in range(4):
                ground.set_tile(
                    column, row, 4,
                    flags=TileFlags((row * 4 + column) % 8),
                )
        camera = self.scene.camera
        camera.viewport_width = camera.viewport_height = 64
        try:
            framebuffer.bind()
            gl.glViewport(0, 0, 64, 64)
            window.projection = Mat4.orthogonal_projection(0, 64, 0, 64, -1, 1)
            for zoom in (0.75, 1, 1.3, 2):
                for offset in (0, 0.25, 0.6):
                    with self.subTest(zoom=zoom, offset=offset):
                        camera.zoom = zoom
                        camera.position = (16 + offset, 16 - offset)
                        window.view = camera.view_matrix
                        gl.glClearColor(1, 0, 0, 1)
                        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
                        ground.draw(camera)
                        rendered = texture.get_image_data().get_data("RGBA", 64 * 4)
                        lower = camera.world_to_screen((8, 8))
                        upper = camera.world_to_screen((24, 24))
                        for y in range(int(lower.y) + 1, int(upper.y)):
                            for x in range(int(lower.x) + 1, int(upper.x)):
                                start = (y * 64 + x) * 4
                                self.assertEqual(
                                    tuple(rendered[start : start + 4]),
                                    (255, 255, 255, 255),
                                    (x, y),
                                )
        finally:
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, previous_framebuffer.value)
            gl.glViewport(*viewport)

    def test_texture_regions_inset_sampling_without_changing_source_pixels(self) -> None:
        tileset = TileSet(
            Image.solid(16, 12, (10, 20, 30, 128)),
            4, 3, margin=1, spacing=1,
        )
        region = tileset.texture_region(4)
        expected = (
            6.5 / 16, 4.5 / 12, 0,
            9.5 / 16, 4.5 / 12, 0,
            9.5 / 16, 6.5 / 12, 0,
            6.5 / 16, 6.5 / 12, 0,
        )
        for actual, target in zip(region.tex_coords, expected, strict=True):
            self.assertAlmostEqual(actual, target)
        self.assertEqual((region.width, region.height), (4, 3))
        self.assertIs(tileset.texture_region(4), region)
        self.assertEqual(
            region.get_image_data().get_data("RGBA", 4 * 4),
            bytes((10, 20, 30, 128)) * 12,
        )

    def test_one_pixel_tiles_sample_the_texel_center(self) -> None:
        tileset = ColorTileSet([(255, 0, 0), (0, 255, 0)])
        region = tileset.texture_region(0)
        points = [
            region.tex_coords[index : index + 3]
            for index in range(0, 12, 3)
        ]
        self.assertTrue(all(point == points[0] for point in points))

    def test_chunk_renderer_partitions_geometry_by_tileset(self) -> None:
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            chunk_size=4,
        )
        alternate = TileSet(
            Image.solid(32, 32, (255, 0, 0)),
            16,
        )
        ground.add_tileset(
            alternate,
            identifier="alternate",
            name="Alternate",
            first_gid=self.tileset.tile_count,
        )
        ground.set_tile(0, 0, 0)
        ground.set_tile(1, 0, self.tileset.tile_count)

        ground.draw(self.scene.camera)

        chunk = ground.chunks[(0, 0)]
        self.assertEqual(len(chunk._vertex_lists), 2)
        self.assertEqual(
            [vertex_list.count for vertex_list in chunk._vertex_lists],
            [4, 4],
        )

    def test_renderer_culls_chunks_outside_camera(self) -> None:
        ground = self.scene.layers.add_tile(
            "Ground",
            self.tileset,
            chunk_size=2,
        )
        ground.set_tile(0, 0, 0)
        ground.set_tile(100, 100, 0)

        ground.draw(self.scene.camera)

        near = ground.chunks[(0, 0)]
        far = ground.chunks[(50, 50)]
        assert near._group is not None
        assert far._group is not None
        self.assertTrue(near._group.visible)
        self.assertFalse(far._group.visible)

    def test_unchanged_chunk_visibility_does_not_rebuild_batch_draw_list(self) -> None:
        ground = self.scene.layers.add_tile("Ground", self.tileset, chunk_size=2)
        ground.set_tile(0, 0, 0)
        ground.set_tile(100, 0, 0)
        ground.draw(self.scene.camera)
        with (
            patch.object(ground._batch, "invalidate", wraps=ground._batch.invalidate) as invalidate,
            patch.object(ground._batch, "_update_draw_list", wraps=ground._batch._update_draw_list) as update_list,
        ):
            for _ in range(10):
                ground.draw(self.scene.camera)
            invalidate.assert_not_called()
            update_list.assert_not_called()
            self.scene.camera.position = (1608, 8)
            ground.draw(self.scene.camera)
            self.assertEqual(invalidate.call_count, 2)
            update_list.assert_called_once()
            self.assertFalse(ground.chunks[(0, 0)]._group.visible)
            self.assertTrue(ground.chunks[(50, 0)]._group.visible)
            ground.draw(self.scene.camera)
            self.assertEqual(invalidate.call_count, 2)
            self.assertEqual(update_list.call_count, 1)

    def test_culled_chunks_do_not_hide_visible_chunks_when_panning_and_zooming(self) -> None:
        window = pyglet.window.Window(width=64, height=64, visible=False)
        self.addCleanup(window.close)
        window.switch_to()
        texture = pyglet.image.Texture.create(64, 64)
        self.addCleanup(texture.delete)
        framebuffer = Framebuffer()
        self.addCleanup(framebuffer.delete)
        framebuffer.attach_texture(texture)
        ground = self.scene.layers.add_tile(
            "Culling test", TileSet(Image.solid(64, 64, (255, 255, 255, 255)), 64),
            chunk_size=1,
        )
        for column in (0, 10, 20):
            ground.set_tile(column, 0, 0)
        camera = self.scene.camera
        camera.viewport_width = camera.viewport_height = 64
        previous = gl.GLint()
        viewport = (gl.GLint * 4)()
        gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING, previous)
        gl.glGetIntegerv(gl.GL_VIEWPORT, viewport)
        try:
            framebuffer.bind()
            gl.glViewport(0, 0, 64, 64)
            window.projection = Mat4.orthogonal_projection(0, 64, 0, 64, -1, 1)
            for column in (0, 10, 20, 10, 0):
                for zoom in (1, 4, 16):
                    with self.subTest(column=column, zoom=zoom):
                        camera.position = (column * 64 + 32, 32)
                        camera.zoom = zoom
                        window.view = camera.view_matrix
                        gl.glClearColor(1, 0, 0, 1)
                        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
                        ground.draw(camera)
                        pixels = texture.get_image_data().get_data("RGBA", 64 * 4)
                        start = (32 * 64 + 32) * 4
                        self.assertEqual(tuple(pixels[start:start + 4]), (255, 255, 255, 255))
        finally:
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, previous.value)
            gl.glViewport(*viewport)

    def test_scene_draw_preserves_heterogeneous_layer_order(self) -> None:
        ground = self.scene.layers.add_tile("Ground", self.tileset)
        foreground = self.scene.layers.add_object("Foreground")

        with (
            patch.object(self.scene.default_layer, "draw") as default_draw,
            patch.object(ground, "draw") as ground_draw,
            patch.object(foreground, "draw") as foreground_draw,
        ):
            manager = Mock()
            manager.attach_mock(default_draw, "default")
            manager.attach_mock(ground_draw, "ground")
            manager.attach_mock(foreground_draw, "foreground")

            self.scene._draw()

        self.assertEqual(
            [call[0] for call in manager.mock_calls],
            ["default", "ground", "foreground"],
        )

    def test_scene_round_trip_preserves_tile_layers(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "tiles.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(32, 32).save(str(image_path))
            tileset = TileSet(
                self.game.resources.load_image(image_path),
                16,
                properties={"theme": "forest"},
            )
            tileset.define(
                0,
                name="grass",
                navigation=TileNavigation(),
                properties={"sound": "grass"},
            )
            ground = self.scene.layers.add_tile(
                "Ground",
                tileset,
                cell_width=24,
                cell_height=24,
                chunk_size=4,
                opacity=0.5,
                collision_enabled=True,
                properties={"biome": "forest"},
            )
            alternate = TileSet(
                self.game.resources.load_image(image_path),
                16,
            )
            ground.add_tileset(
                alternate,
                identifier="details",
                name="Details",
                first_gid=4,
            )
            ground.set_tile(2, 3, 0)
            ground.set_tile(3, 3, 4)

            serializer = SceneSerializer()
            document = serializer.capture(self.scene, asset_root=root)
            restored = serializer.restore(
                self.game,
                document,
                asset_root=root,
            )

        loaded = restored.layers["Ground"]
        self.assertIsInstance(loaded, TileLayer)
        assert isinstance(loaded, TileLayer)
        self.assertEqual(loaded.tile_at(2, 3), TileCell(0))
        self.assertEqual(loaded.tile_at(3, 3), TileCell(4))
        self.assertEqual(len(loaded.tilesets), 2)
        self.assertEqual(loaded.tilesets[1].identifier, "details")
        self.assertEqual(loaded.cell_width, 24)
        self.assertEqual(loaded.opacity, 0.5)
        self.assertTrue(loaded.collision_enabled)
        self.assertEqual(loaded.properties["biome"], "forest")
        assert loaded.tileset is not None
        self.assertEqual(
            loaded.tileset.definition(0).properties["sound"],
            "grass",
        )

    @staticmethod
    def game_object():
        from g import GameObject

        return GameObject()


if __name__ == "__main__":
    unittest.main()
