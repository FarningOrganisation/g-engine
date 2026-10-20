import unittest

from g import Game, GameObject, RectRenderer, SceneSerializer, SceneTypeRegistry, Script
from g.persistence import scene_types
from g_editor.state import EditorState, grid_coordinates
from g.editor import new_object_record, new_scene_document, walk_object_records


class EditorDocumentTests(unittest.TestCase):
    def test_new_document_uses_current_scene_format(self) -> None:
        document = new_scene_document()
        self.assertEqual(document.version, 1)
        self.assertEqual(document.objects, [])
        self.assertEqual(document.settings["grid_cell_size"], 32)

    def test_object_templates_are_runtime_compatible(self) -> None:
        rectangle = new_object_record("rectangle", x=10, y=20)
        circle = new_object_record("circle")
        text = new_object_record("text")
        self.assertEqual(rectangle["type"], "g.game_object")
        self.assertEqual(
            rectangle["transform"]["position"],  # type: ignore[index]
            {"$vec2": [10, 20]},
        )
        self.assertEqual(
            rectangle["components"][0]["type"],  # type: ignore[index]
            "g.rect_renderer",
        )
        self.assertEqual(circle["type"], "g.game_object")
        self.assertEqual(text["type"], "g.game_object")

    def test_walk_includes_nested_objects(self) -> None:
        parent = new_object_record()
        child = new_object_record()
        parent["children"] = [child]
        self.assertEqual(walk_object_records([parent]), (parent, child))

    def test_unknown_template_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown editor object"):
            new_object_record("unknown")

    def test_editor_document_restores_to_runtime_objects(self) -> None:
        document = new_scene_document()
        document.objects.append(new_object_record("rectangle", x=10, y=20))
        game = Game(320, 180)

        scene = SceneSerializer().restore(game, document, asset_root=".")

        self.assertEqual(len(scene.objects), 1)
        self.assertIs(type(scene.objects[0]), GameObject)
        self.assertIsInstance(
            scene.objects[0].require_component(RectRenderer), RectRenderer
        )
        self.assertEqual(tuple(scene.objects[0].position), (10, 20))
        scene.remove(scene.objects[0])

    def test_registered_script_template_uses_annotated_defaults(self) -> None:
        class Movement(Script):
            speed: float = 120
            axis: str = "horizontal"
            _runtime_total: float = 0

        registry = SceneTypeRegistry()
        registry.register_component("game.movement", Movement)

        self.assertEqual(
            registry.component_template("game.movement"),
            {
                "type": "game.movement",
                "properties": {
                    "speed": 120,
                    "axis": "horizontal",
                    "enabled": True,
                },
            },
        )

    def test_fieldless_script_is_attachable_and_ignores_removed_fields(
        self,
    ) -> None:
        class Controller(Script):
            pass

        scene_types.register_component("game.controller", Controller)
        try:
            self.assertIn(
                "game.controller", scene_types.editable_component_types
            )
            self.assertEqual(
                scene_types.component_template("game.controller"),
                {"type": "game.controller", "properties": {"enabled": True}},
            )
            document = new_scene_document()
            record = new_object_record("rectangle")
            record["components"].append(  # type: ignore[union-attr]
                {
                    "type": "game.controller",
                    "properties": {"speed": 120.0, "enabled": True},
                }
            )
            document.objects.append(record)

            scene = SceneSerializer().restore(
                Game(320, 180), document, asset_root="."
            )

            controller = scene.objects[0].get_component(Controller)
            self.assertIsNotNone(controller)
            self.assertFalse(hasattr(controller, "speed"))
        finally:
            scene_types.unregister_module(Controller.__module__)

    def test_sync_script_components_matches_current_fields(self) -> None:
        class Mover(Script):
            jump: float = 3.0

        scene_types.register_component("test.sync_mover", Mover)
        try:
            state = EditorState()
            record = new_object_record("rectangle")
            components = record["components"]
            assert isinstance(components, list)
            components.append(
                {
                    "type": "test.sync_mover",
                    "properties": {"speed": 120.0, "enabled": False},
                }
            )
            state.document.objects.append(record)

            self.assertTrue(state.sync_script_components())

            self.assertEqual(
                components[-1],
                {
                    "type": "test.sync_mover",
                    "properties": {"enabled": False, "jump": 3.0},
                },
            )
            self.assertFalse(state.sync_script_components())
        finally:
            scene_types.unregister_module(Mover.__module__)

    def test_grid_coordinates_are_anchored_to_origin(self) -> None:
        coordinates = grid_coordinates(2000, 32)
        self.assertIn(0, coordinates)
        self.assertIn(-32, coordinates)
        self.assertIn(32, coordinates)
        self.assertEqual(coordinates.start % 32, 0)


if __name__ == "__main__":
    unittest.main()
