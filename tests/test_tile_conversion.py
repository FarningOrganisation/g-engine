from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pyglet
from pyglet import gl
from pyglet.image.buffer import Framebuffer
from pyglet.math import Mat4

from g_editor.state import EditorState, layer_objects, position
from g_editor.application import EnginePreview
from g import (
    BodyType, Collider, Game, PhysicsBody,
    SceneSerializer, TileCell, TileFlags, TileRenderer,
)
from g.geometry import BoxGeometry, contains_point


class TileConversionTests(unittest.TestCase):
    def setUp(self):
        self.state = EditorState()
        self.destination = self.state.selected_layer_id
        self.source = self.state.add_tile_layer("Ground")
        self.source["cell_size"] = [16, 16]
        self.source["offset"] = [8, -4]
        self.state.add_color_tileset()
        for x, flag in ((0, TileFlags.FLIP_HORIZONTAL), (1, TileFlags.FLIP_DIAGONAL), (3, TileFlags.NONE)):
            self.state._set_tile_cell(self.source, x, 0, TileCell(0, flag))
        self.state.mark_changed("Seed tiles")
        self.state.select_tile_rectangle((0, 0), (3, 0))

    def definitions(self, collision):
        for record in self.source["tilesets"]:
            record["tiles"] = [{"id": 0, "collision": collision}]

    def convert(self, **options):
        return self.state.convert_selected_tiles("Platform", self.destination, **options)

    def load(self):
        game = Game(128, 128, asset_root=self.state.asset_root)
        scene = SceneSerializer().restore(game, self.state.document, asset_root=self.state.asset_root)
        item = next(item for item in scene.get_children(recursive=True) if item.name == "Platform")
        return game, scene, item

    def test_visual_conversion_preserves_flags_spacing_world_position_and_undo(self):
        before = deepcopy(self.state.document)
        count = len(self.state._history)
        record = self.convert()
        self.assertEqual(position(record), (40, 4))
        self.assertEqual(self.state.selected_layer_id, self.destination)
        self.assertEqual(self.state.selected_id, record["id"])
        self.assertEqual(len(self.state._history), count + 1)
        self.assertFalse(self.source["chunks"])
        self.assertEqual([c["type"] for c in record["components"]], ["g.tile_renderer"])
        _, _, item = self.load()
        renderer = item.get_component(TileRenderer)
        self.assertEqual(len(renderer.drawables), 3)
        self.assertEqual(renderer.patches[0].tile_at(0, 0).flags, TileFlags.FLIP_HORIZONTAL)
        self.assertEqual(renderer.patches[0].tile_at(1, 0).flags, TileFlags.FLIP_DIAGONAL)
        renderer.sync()
        self.assertEqual(item._world_bounds(), (8, -4, 72, 12))
        self.assertTrue(EnginePreview._renderer_contains(item, 10, 0))
        self.assertFalse(EnginePreview._renderer_contains(item, 48, 0))
        item.delete()
        self.assertTrue(self.state.undo())
        self.assertEqual(self.state.document, before)
        self.assertEqual(set(self.state.selected_tile_cells), {(0, 0), (1, 0), (3, 0)})
        self.assertTrue(self.state.redo())
        self.assertEqual(len(layer_objects(self.state.selected_layer)), 1)

    def test_full_rectangles_merge_without_filling_holes_and_ignore_flip_flags(self):
        self.definitions({"type": "rectangle"})
        record = self.convert(include_collisions=True)
        shapes = record["components"][1:]
        self.assertEqual(len(shapes), 2)
        self.assertEqual(sorted(s["properties"]["width"] for s in shapes), [16, 32])
        _, scene, item = self.load()
        boxes = [c for c in item.components if isinstance(c, Collider) and c.shape == "box"]
        self.assertEqual(len(boxes), 2)
        self.assertFalse(any(contains_point(c.geometry, 48, 0) for c in boxes))
        body = item.add_component(PhysicsBody(BodyType.KINEMATIC))
        scene.physics.refresh()
        self.assertTrue(all(c._shape.body is body._body for c in boxes))
        item.x += 50
        scene.physics.step(0)
        self.assertEqual(tuple(body._body.position), tuple(item.world_position))
        self.assertTrue(any(contains_point(c.geometry, 60, 0) for c in boxes))
        item.delete()

    def test_flipped_slopes_and_concave_polygons_preserve_area(self):
        self.definitions({"type": "polygon", "polygons": [[[0, 0], [1, 0], [0, 1]]]})
        record = self.convert(include_collisions=True)
        self.assertEqual(len(record["components"][1:]), 3)
        _, _, item = self.load()
        polygons = [c for c in item.components if isinstance(c, Collider) and c.shape == "polygon"]
        self.assertTrue(contains_point(polygons[0].geometry, 22, -2))
        self.assertFalse(contains_point(polygons[0].geometry, 10, 10))
        item.delete()
        pieces = Collider.decompose(((0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)))
        import pymunk.util
        self.assertAlmostEqual(sum(abs(pymunk.util.calc_area(list(p))) for p in pieces), 3)
        self.assertFalse(any(contains_point(BoxGeometry(p), 1.5, 1.5) for p in pieces))

    def test_no_collision_definitions_does_not_make_visual_tiles_solid(self):
        record = self.convert(include_collisions=True)
        self.assertEqual(len(record["components"]), 1)

    def test_new_layer_creation_and_destination_defaults_are_atomic(self):
        self.assertEqual(self.state.tile_conversion_destination(), self.destination)
        before = deepcopy(self.state.document)
        self.state.convert_selected_tiles("Platform", None, new_layer_name="Platforms")
        self.assertEqual(self.state.selected_layer["name"], "Platforms")
        self.assertTrue(self.state.undo())
        self.assertEqual(self.state.document, before)
        self.state.select_layer(self.destination, toggle=True)
        self.assertEqual(self.state.tile_conversion_destination(), self.destination)

    def test_invalid_inputs_leave_scene_and_selection_unchanged(self):
        before = deepcopy(self.state.document)
        selection = deepcopy(self.state.selected_tile_groups)
        for options in (
            {"name": "", "destination_layer_id": self.destination},
            {"name": "Platform", "destination_layer_id": self.source["id"]},
            {"name": "Platform", "destination_layer_id": None, "new_layer_name": "Ground"},
        ):
            with self.assertRaises(ValueError):
                self.state.convert_selected_tiles(**options)
            self.assertEqual(self.state.document, before)
            self.assertEqual(self.state.selected_tile_groups, selection)
        self.definitions({"type": "polygon", "polygons": [[[0, 0], [1, 1], [0, 1], [1, 0]]]})
        before = deepcopy(self.state.document)
        with self.assertRaisesRegex(ValueError, "self-intersect"):
            self.convert(include_collisions=True)
        self.assertEqual(self.state.document, before)

    def test_saved_scene_roundtrip_and_transformed_tile_renderer(self):
        self.convert()
        game, scene, item = self.load()
        renderer = item.get_component(TileRenderer)
        item.scale_x, item.scale_y = 2, -1
        item.rotation = 90
        renderer.sync()
        self.assertEqual(renderer.drawable.rotation, 90)
        self.assertEqual(renderer.drawable.scale_x, 32 / renderer.drawable.image.width)
        self.assertEqual(renderer.drawable.scale_y, -16 / renderer.drawable.image.height)
        with TemporaryDirectory() as directory:
            filename = Path(directory) / "converted.scene.json"
            serializer = SceneSerializer()
            serializer.save(scene, filename, asset_root=self.state.asset_root)
            loaded = serializer.load(game, filename, asset_root=self.state.asset_root)
            restored = next(o for o in loaded.get_children(recursive=True) if o.name == "Platform")
            self.assertEqual(restored.get_component(TileRenderer).world_quads(), renderer.world_quads())
            restored.delete()
        item.delete()

    def test_polygon_collider_shares_body_and_queries_follow_rotation_and_scale(self):
        self.definitions({"type": "polygon", "polygons": [[[0, 0], [1, 0], [0, 1]]]})
        self.convert(include_collisions=True)
        _, scene, item = self.load()
        body = item.add_component(PhysicsBody(BodyType.KINEMATIC, categories=2))
        polygons = [c for c in item.components if isinstance(c, Collider) and c.shape == "polygon"]
        item.rotation = 90
        item.scale_x, item.scale_y = 2, -1
        item.x += 100
        scene.physics.step(0)
        self.assertTrue(all(c._shape.body is body._body for c in polygons))
        self.assertTrue(all(c._shape.filter.categories == 2 for c in polygons))
        for collider in polygons:
            centroid = tuple(sum(p[i] for p in collider.geometry.points) / len(collider.points) for i in (0, 1))
            self.assertIn(item, scene.physics.overlap_point(centroid))
        item.delete()
        self.assertFalse(scene.physics._space.shapes)

    def test_multiple_source_tilemaps_preserve_world_offsets_and_default_layer_choice(self):
        other = self.state.add_tile_layer("Details")
        other["cell_size"] = [8, 8]
        other["offset"] = [-4, 2]
        self.state.add_color_tileset()
        self.state._set_tile_cell(other, 0, 0, TileCell(0))
        self.state.select_layer(str(self.source["id"]), toggle=True)
        self.state._tile_selection_layer_id = str(self.source["id"])
        self.state._tile_selection = {(0, 0), (1, 0), (3, 0)}
        self.state._extra_tile_selections = {str(other["id"]): {(0, 0)}}
        self.convert()
        _, _, item = self.load()
        renderer = item.get_component(TileRenderer)
        self.assertEqual(len(renderer.patches), 2)
        self.assertEqual(len(renderer.world_quads()), 4)
        self.assertEqual(item._world_bounds(), (-4, -4, 72, 12))
        item.delete()

    def test_atlas_pixels_match_before_and_after_conversion_for_every_flip(self):
        window = pyglet.window.Window(width=128, height=32, visible=False)
        self.addCleanup(window.close)
        window.switch_to()
        texture = pyglet.image.Texture.create(128, 32)
        self.addCleanup(texture.delete)
        framebuffer = Framebuffer()
        self.addCleanup(framebuffer.delete)
        framebuffer.attach_texture(texture)
        previous = gl.GLint()
        viewport = (gl.GLint * 4)()
        gl.glGetIntegerv(gl.GL_FRAMEBUFFER_BINDING, previous)
        gl.glGetIntegerv(gl.GL_VIEWPORT, viewport)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pixels = bytes(channel for y in range(8) for x in range(8)
                           for channel in (x * 30, y * 30, 120, 255))
            with (root / "tiles.png").open("wb") as output:
                pyglet.image.ImageData(8, 8, "RGBA", pixels).save(file=output)
            self.state.asset_root = root
            self.source["chunks"] = []
            self.source["cell_size"] = [8, 8]
            self.source["offset"] = [8, 8]
            self.source["opacity"] = 0.75
            self.source["tilesets"] = [{
                "type": "atlas", "id": "atlas", "name": "Atlas", "first_gid": 0,
                "image": "tiles.png", "tile_size": [8, 8], "tile_count": 1, "columns": 1,
            }]
            for x in range(8):
                self.state._set_tile_cell(self.source, x, 0, TileCell(0, TileFlags(x)))
            self.state.select_tile_rectangle((0, 0), (7, 0))
            game = Game(128, 32, asset_root=root)
            serializer = SceneSerializer()
            before = serializer.restore(game, self.state.document, asset_root=root)
            self.convert()
            after = serializer.restore(game, self.state.document, asset_root=root)

            def render(scene):
                scene.camera.viewport_width = 128
                scene.camera.viewport_height = 32
                scene.camera.position = (64, 16)
                scene.camera.zoom = 1
                framebuffer.bind()
                gl.glViewport(0, 0, 128, 32)
                window.projection = Mat4.orthogonal_projection(0, 128, 0, 32, -1, 1)
                window.view = Mat4()
                gl.glClearColor(0, 0, 0, 1)
                gl.glClear(gl.GL_COLOR_BUFFER_BIT)
                for layer in scene.layers:
                    layer.draw(scene.camera)
                return bytes(texture.get_image_data().get_data("RGBA", 128 * 4))

            try:
                self.assertEqual(render(before), render(after))
                self.assertEqual({p.name for p in root.iterdir()}, {"tiles.png"})
                captured = serializer.capture(after, asset_root=root)
                object_layer = next(layer for layer in captured.layers if layer.get("type", "object") == "object")
                patch = object_layer["objects"][0]["components"][0]["properties"]["patches"][0]
                self.assertEqual(patch["tilesets"][0]["image"], "tiles.png")
            finally:
                gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, previous.value)
                gl.glViewport(*viewport)
                for scene in (before, after):
                    for item in scene.get_children(recursive=True):
                        item.delete()

    def test_image_collection_animation_and_polygon_persistence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, color in (("a.png", (255, 0, 0, 255)), ("b.png", (0, 255, 0, 255))):
                with (root / name).open("wb") as output:
                    pyglet.image.SolidColorImagePattern(color).create_image(2, 2).save(file=output)
            self.state.asset_root = root
            self.source["tilesets"] = [{
                "type": "images", "id": "images", "name": "Collection", "first_gid": 0,
                "images": ["a.png", "b.png"], "tile_count": 2, "tiles": [{
                    "id": 0, "animation": [{"tile_id": 0, "duration": 0.1}, {"tile_id": 1, "duration": 0.1}],
                    "collision": {"type": "polygon", "polygons": [[[0, 0], [1, 0], [0, 1]]]},
                }],
            }]
            self.convert(include_collisions=True)
            game, scene, item = self.load()
            renderer = item.get_component(TileRenderer)
            initial = bytes(renderer.drawable.image.get_image_data().get_data("RGBA", 8))
            renderer.update(0.11)
            renderer.sync()
            self.assertNotEqual(bytes(renderer.drawable.image.get_image_data().get_data("RGBA", 8)), initial)
            captured = SceneSerializer().capture(scene, asset_root=root)
            loaded = SceneSerializer().restore(game, captured, asset_root=root)
            restored = next(o for o in loaded.get_children(recursive=True) if o.name == "Platform")
            restored_polygons = [
                c for c in restored.components
                if isinstance(c, Collider) and c.shape == "polygon"
            ]
            item_polygons = [
                c for c in item.components
                if isinstance(c, Collider) and c.shape == "polygon"
            ]
            self.assertEqual(len(restored_polygons), 3)
            self.assertEqual(
                [collider.points for collider in restored_polygons],
                [collider.points for collider in item_polygons],
            )
            restored.delete()
            item.delete()
