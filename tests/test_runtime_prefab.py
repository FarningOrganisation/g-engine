import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from g import Game, SceneFormatError, instantiate_prefab


class RuntimePrefabTests(unittest.TestCase):
    def test_instantiates_unique_game_objects_without_readiness_in_staging(
        self,
    ) -> None:
        template = {
            "id": "template-root",
            "type": "g.game_object",
            "name": "Prefab",
            "enabled": True,
            "visible": True,
            "z_index": 0,
            "render_behind_parent": False,
            "offset_x": 0,
            "offset_y": 0,
            "scale_x": 1,
            "scale_y": 1,
            "opacity": 255,
            "transform": {
                "position": {"$vec2": [0, 0]},
                "rotation": 0,
                "scale": [1, 1],
            },
            "speed": {"$vec2": [0, 0]},
            "properties": {},
            "script_properties": {
                "team": "enemy",
                "settings": {"damage": 3},
            },
            "components": [],
            "children": [{
                "id": "template-child",
                "type": "g.game_object",
                "name": "Child",
                "enabled": True,
                "visible": True,
                "z_index": 0,
                "render_behind_parent": False,
                "offset_x": 0,
                "offset_y": 0,
                "scale_x": 1,
                "scale_y": 1,
                "opacity": 255,
                "transform": {
                    "position": {"$vec2": [0, 0]},
                    "rotation": 0,
                    "scale": [1, 1],
                },
                "speed": {"$vec2": [0, 0]},
                "properties": {},
                "script_properties": {},
                "components": [],
                "children": [],
            }],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "object.prefab.json"
            path.write_text(json.dumps({
                "format": "g.prefab",
                "version": 2,
                "prefab_id": "test",
                "revision": 1,
                "kind": "game_object",
                "template": template,
            }))
            game = Game(100, 100)
            first = instantiate_prefab(game, path, scene_id="first")
            second = instantiate_prefab(game, path, scene_id="second")

        self.assertEqual(first.scene_id, "first")
        self.assertEqual(second.scene_id, "second")
        self.assertNotEqual(
            first.children[0].scene_id,
            second.children[0].scene_id,
        )
        self.assertEqual(
            first.properties,
            {"team": "enemy", "settings": {"damage": 3}},
        )
        first.properties["settings"]["damage"] = 5
        self.assertEqual(second.properties["settings"]["damage"], 3)
        self.assertIsNone(first.scene)
        game.add(first)
        self.assertIs(first.scene, game.scenes.current)

    def test_rejects_non_game_object_prefab(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "view.prefab.json"
            path.write_text(json.dumps({
                "format": "g.prefab",
                "version": 2,
                "kind": "ui_view",
                "template": {},
            }))
            with self.assertRaisesRegex(
                SceneFormatError,
                "must contain a game object",
            ):
                instantiate_prefab(Game(100, 100), path)


if __name__ == "__main__":
    unittest.main()
