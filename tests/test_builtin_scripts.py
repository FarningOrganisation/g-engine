from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

from g_editor.panels.inspector import _component_group, _component_label
from g_editor.script_templates import SCRIPT_TEMPLATES, script_source
from g_editor.state import EditorState
from g import (
    CameraFollow, Game, GameObject, PlatformerMotor, ProjectConfig,
    SceneSerializer, SceneTypeRegistry, Script, TopDownMotor,
)
from g.persistence import _register_builtins, scene_types


class BuiltinScriptTests(unittest.TestCase):
    def test_default_component_labels_use_class_names_and_only_qualify_collisions(self) -> None:
        from unittest.mock import patch

        registry = SceneTypeRegistry()
        first = type("PlatformMovement", (Script,), {"__module__": "platform_label_first"})
        second = type("PlatformMovement", (Script,), {"__module__": "platform_label_second"})
        registry.register_component("platform_label_first.PlatformMovement", first)
        with patch("g_editor.panels.inspector.scene_types", registry):
            self.assertEqual(_component_label("platform_label_first.PlatformMovement"), "PlatformMovement")
            registry.register_component("platform_label_second.PlatformMovement", second)
            self.assertEqual(
                _component_label("platform_label_first.PlatformMovement"),
                "PlatformMovement (platform_label_first.PlatformMovement)",
            )
            self.assertEqual(
                _component_label("platform_label_second.PlatformMovement"),
                "PlatformMovement (platform_label_second.PlatformMovement)",
            )
            registry.unregister_module("platform_label_second")
            self.assertEqual(_component_label("platform_label_first.PlatformMovement"), "PlatformMovement")
            registry.register_component(
                "platform_label_second.PlatformMovement", second, display_name="Moving Platform",
            )
            self.assertEqual(_component_label("platform_label_second.PlatformMovement"), "Moving Platform")

    def test_all_builtin_scripts_live_in_scripts_package(self) -> None:
        from g import scripts

        expected = {
            "g.camera_follow": scripts.CameraFollow,
            "g.platformer_motor": scripts.PlatformerMotor,
            "g.top_down_motor": scripts.TopDownMotor,
        }
        actual = {
            name: codec.value_type
            for name in scene_types.editable_component_types
            if (codec := scene_types.component_codec_for_name(name)).builtin
            and issubclass(codec.value_type, Script)
        }
        self.assertEqual(actual, expected)
        self.assertIs(scripts.CameraFollow, CameraFollow)
        self.assertIs(scripts.PlatformerMotor, PlatformerMotor)
        self.assertIs(scripts.TopDownMotor, TopDownMotor)
        for value_type in actual.values():
            self.assertTrue(value_type.__module__.startswith("g.scripts."))

    def test_metadata_decorator_and_registration_share_defaults(self) -> None:
        registry = SceneTypeRegistry()

        @registry.component("test.Player", category="Characters", display_name="Player")
        class Player(Script):
            speed: float = 10

        codec = registry.component_codec_for_name("test.Player")
        self.assertEqual((codec.category, codec.display_name, codec.builtin),
                         ("Characters", "Player", False))
        self.assertEqual(registry.component_template("test.Player")["properties"],
                         {"speed": 10, "enabled": True})
        for category, display_name in (("", None), (" Characters ", None), (None, " ")):
            with self.assertRaises(ValueError):
                registry.register_component(
                    "test.Invalid", Player, category=category, display_name=display_name,
                )

    def test_builtin_categories_and_motor_script_identity(self) -> None:
        self.assertTrue(issubclass(PlatformerMotor, Script))
        self.assertTrue(issubclass(TopDownMotor, Script))
        self.assertEqual(_component_group("g.platformer_motor"), "Movement")
        self.assertEqual(_component_label("g.platformer_motor"), "Platformer Motor")
        self.assertEqual(_component_group("g.top_down_motor"), "Movement")
        self.assertEqual(_component_group("g.camera_follow"), "Camera")
        self.assertEqual(_component_group("g.collider"), "Physics")
        self.assertEqual(_component_group("g.sprite_renderer"), "Renderers")
        state = EditorState()
        state.add_object("rectangle")
        state.add_component("g.platformer_motor")
        before = state.revision
        self.assertFalse(state.sync_script_components())
        self.assertEqual(state.revision, before)
        state.select(None)
        state.add_object("empty")
        state.add_component("g.camera_follow")
        self.assertFalse(state.sync_script_components())

    def test_camera_follow_offsets_smoothing_and_persistence(self) -> None:
        game = Game(320, 180)
        owner = game.add(GameObject(60, 70))
        follow = owner.add_component(CameraFollow())
        follow.offset_x, follow.offset_y = 10, -5
        follow.update(1 / 60)
        self.assertEqual(tuple(game.camera.position), (70, 65))
        follow.smoothing = 8
        results = []
        for fps in (30, 60, 120):
            game.camera.position = (0, 0)
            for _ in range(fps):
                follow.update(1 / fps)
            results.append(tuple(game.camera.position))
        for x, y in results:
            self.assertTrue(isclose(x, results[0][0]))
            self.assertTrue(isclose(y, results[0][1]))
        registry = SceneTypeRegistry()
        _register_builtins(registry)
        serializer = SceneSerializer(registry)
        document = serializer.capture(game.scenes.current, asset_root=".")
        restored = serializer.restore(game, document, asset_root=".")
        loaded = restored.objects[0].require_component(CameraFollow)
        self.assertEqual((loaded.offset_x, loaded.offset_y, loaded.smoothing), (10, -5, 8))
        follow.smoothing = -1
        with self.assertRaises(ValueError):
            follow.update(1 / 60)

    def test_templates_are_project_scripts_and_add_motor_dependencies(self) -> None:
        for index, template in enumerate(SCRIPT_TEMPLATES):
            module = f"generated_player_template_{index}"
            with self.subTest(template=template), TemporaryDirectory() as directory:
                root = Path(directory)
                source = script_source("Player", f"{module}.Player", template)
                (root / f"{module}.py").write_text(source, encoding="utf-8")
                project = ProjectConfig.create(root).with_module(module)
                try:
                    project.import_scripts()
                    self.assertEqual(_component_group(f"{module}.Player"), "Scripts")
                    state = EditorState()
                    state.add_object("rectangle")
                    state.add_component(f"{module}.Player")
                    game = Game(320, 180)
                    scene = SceneSerializer().restore(game, state.document, asset_root=root)
                    game.scenes.add("test", scene)
                    game.scenes.switch("test")
                    game._start()
                    game._update(1 / 60)
                    if template == "Platformer Player":
                        self.assertIsNotNone(scene.objects[0].get_component(PlatformerMotor))
                    elif template == "Top-Down Player":
                        self.assertIsNotNone(scene.objects[0].get_component(TopDownMotor))
                    game._stop()
                finally:
                    scene_types.unregister_module(module)
                    sys.modules.pop(module, None)

    def test_template_adds_nested_motor_dependencies_on_empty_object(self) -> None:
        registry = scene_types
        name = "tests.missing_motor_collider.Player"
        from g import requires_components

        @registry.component(name)
        @requires_components(PlatformerMotor, auto_add=True)
        class Player(Script):
            pass

        try:
            state = EditorState()
            record = state.add_object("empty")
            state.add_component(name)
            components = record["components"]
            assert isinstance(components, list)
            names = [item["type"] for item in components if isinstance(item, dict)]
            self.assertEqual(set(names), {"g.collider", "g.physics_body", "g.platformer_motor", name})
            self.assertEqual(len(names), 4)
            self.assertTrue(state.undo())
            assert state.selected is not None
            self.assertEqual(state.selected["components"], [])
        finally:
            registry.unregister_module(Player.__module__)

    def test_project_reload_updates_category_and_display_name(self) -> None:
        module = "categorized_reload_script"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / f"{module}.py"
            project = ProjectConfig.create(root).with_module(module)
            try:
                for category, label in (("Characters", "Hero"), ("Enemies", "Enemy Behavior")):
                    source.write_text(
                        "from g import Script, scene_component, inspector_field\n"
                        f"@scene_component('{module}.Behavior', category={category!r}, "
                        f"display_name={label!r})\n"
                        "class Behavior(Script):\n"
                        f"    speed: float = inspector_field(100.0, label={label!r}, tooltip={category!r})\n",
                        encoding="utf-8",
                    )
                    project.import_scripts()
                    self.assertEqual(_component_group(f"{module}.Behavior"), category)
                    self.assertEqual(_component_label(f"{module}.Behavior"), label)
                    from g.inspector import inspector_fields
                    fields = inspector_fields(scene_types.component_type_for_name(f"{module}.Behavior"))
                    self.assertEqual(fields["speed"].label, label)
                    self.assertEqual(fields["speed"].tooltip, category)
            finally:
                scene_types.unregister_module(module)
                sys.modules.pop(module, None)
