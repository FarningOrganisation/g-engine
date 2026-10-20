import json
from importlib.util import find_spec
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from g import (
    Game,
    ProjectConfig,
    ProjectExportConfig,
    ProjectFormatError,
    SceneSerializer,
)
from g.persistence import scene_types
from g.project import ProjectImportError
from g_editor.trust import ProjectTrustStore


class ProjectConfigTests(unittest.TestCase):
    def test_new_script_import_invalidates_cached_directory_listing(self) -> None:
        module = "fresh_editor_component"
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = ProjectConfig.create(root).with_module(module)
            sys.path.insert(0, str(root))
            try:
                self.assertIsNone(find_spec(module))
                timestamp = root.stat()
                (root / f"{module}.py").write_text(
                    "from g import Script, scene_component\n"
                    f"@scene_component('{module}.Behavior')\n"
                    "class Behavior(Script):\n"
                    "    pass\n",
                    encoding="utf-8",
                )
                os.utime(root, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
                self.assertEqual(project.changed_scripts(), (module,))
                project.import_scripts()
                self.assertIn(
                    f"{module}.Behavior", scene_types.editable_component_types
                )
                self.assertEqual(project.changed_scripts(), ())
            finally:
                sys.path.remove(str(root))
                scene_types.unregister_module(module)
                sys.modules.pop(module, None)

    def test_failed_new_script_import_retries_after_source_is_fixed(self) -> None:
        module = "broken_new_editor_script"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / f"{module}.py"
            project = ProjectConfig.create(root).with_module(module)
            try:
                path.write_text("class Broken(\n", encoding="utf-8")
                with self.assertRaises(ProjectImportError):
                    project.import_scripts()
                self.assertEqual(project.changed_scripts(), ())
                path.write_text("FIXED = True\n", encoding="utf-8")
                self.assertEqual(project.changed_scripts(), (module,))
                imported = project.import_scripts()
                self.assertTrue(imported[0].FIXED)
                self.assertEqual(project.changed_scripts(), ())
            finally:
                sys.modules.pop(module, None)

    def test_project_round_trip_and_module_import(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "player_script.py").write_text(
                "VALUE = 42\n",
                encoding="utf-8",
            )
            project = ProjectConfig.create(
                root,
                width=640,
                height=360,
                grid_cell_size=16,
                scaling="integer_fit",
                matte_color=(12, 18, 24, 255),
                resizable=False,
                fullscreen=True,
                export=ProjectExportConfig(
                    "My Game",
                    "2.0.0",
                    "main.scene.json",
                    "icon.png",
                    ("plugin.runtime",),
                    ("plugin_assets",),
                ),
            ).with_module("player_script")
            project.write()

            loaded = ProjectConfig.read(project.path)
            imported = loaded.import_scripts()

            self.assertEqual(loaded.script_modules, ("player_script",))
            self.assertEqual(
                (loaded.width, loaded.height, loaded.grid_cell_size),
                (640, 360, 16),
            )
            self.assertEqual(loaded.scaling, "integer_fit")
            self.assertEqual(
                loaded.matte_color,
                (12, 18, 24, 255),
            )
            self.assertFalse(loaded.resizable)
            self.assertTrue(loaded.fullscreen)
            self.assertEqual(loaded.export, project.export)
            self.assertEqual(getattr(imported[0], "VALUE"), 42)
            self.assertNotIn(str(root), sys.path)
            sys.modules.pop("player_script", None)

    def test_invalid_or_escaping_module_name_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "g.project.json"
            path.write_text(
                json.dumps(
                    {
                        "format": "g.project",
                        "version": 1,
                        "script_modules": ["../outside"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ProjectFormatError):
                ProjectConfig.read(path)

    def test_module_path_must_be_inside_project(self) -> None:
        with TemporaryDirectory() as project_directory, TemporaryDirectory() as other:
            project = ProjectConfig.create(project_directory)
            script = Path(other) / "script.py"
            script.write_text("", encoding="utf-8")

            with self.assertRaises(ProjectFormatError):
                project.module_for_path(script)

    def test_game_from_project_loads_scripts_first(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "startup.py").write_text(
                "LOADED = True\n",
                encoding="utf-8",
            )
            project = ProjectConfig.create(
                root,
                width=320,
                height=180,
                scaling="fill",
                matte_color=(4, 5, 6, 255),
            ).with_module("startup")
            project.write()

            game = Game.from_project(project.path)

            self.assertEqual((game.width, game.height), (320, 180))
            self.assertEqual(game.project, project)
            self.assertEqual(game.resources.asset_root, root.resolve())
            self.assertEqual(game.presentation.scaling, "fill")
            self.assertEqual(
                game.presentation.matte_color,
                (4, 5, 6, 255),
            )
            self.assertTrue(sys.modules["startup"].LOADED)
            sys.modules.pop("startup", None)

    def test_load_project_sets_resource_root(self) -> None:
        with TemporaryDirectory() as directory:
            project = ProjectConfig.create(directory)
            project.write()
            game = Game(10, 10)
            game.load_project(project.path)
            self.assertEqual(game.resources.asset_root, project.asset_root)

    def test_nested_scene_load_keeps_project_resource_root(self) -> None:
        with TemporaryDirectory() as directory:
            project = ProjectConfig.create(directory)
            project.write()
            root = project.asset_root
            (root / "scenes").mkdir()
            game = Game.from_project(project.path)
            filename = root / "scenes" / "level.scene.json"
            SceneSerializer().save(game.scenes.current, filename, asset_root=root)
            game.scenes.load("level", str(filename))
            self.assertEqual(game.resources.asset_root, root)

    def test_import_scripts_reloads_changed_component_fields(self) -> None:
        module_name = "reload_player_script"

        def write(source: str, stamp: int) -> None:
            path = root / f"{module_name}.py"
            path.write_text(source, encoding="utf-8")
            os.utime(path, ns=(stamp, stamp))

        header = (
            "from g import Script, scene_component\n\n"
            "@scene_component('reload.Player')\n"
            "class Player(Script):\n"
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            try:
                write(header + "    speed: float = 120.0\n", 1_000_000_000)
                project = ProjectConfig.create(root).with_module(module_name)
                project.import_scripts()
                self.assertEqual(
                    scene_types.component_codec_for_name(
                        "reload.Player"
                    ).fields,
                    ("speed",),
                )
                self.assertEqual(project.changed_scripts(), ())

                write(header + "    pass\n", 2_000_000_000)
                self.assertEqual(project.changed_scripts(), (module_name,))
                project.import_scripts()

                codec = scene_types.component_codec_for_name("reload.Player")
                self.assertEqual(codec.fields, ())
                self.assertIn(
                    "reload.Player",
                    scene_types.editable_component_types,
                )
                self.assertEqual(project.changed_scripts(), ())

                write(header + "    broken(\n", 3_000_000_000)
                with self.assertRaises(ProjectImportError):
                    project.import_scripts()
                self.assertIs(
                    scene_types.component_type_for_name("reload.Player"),
                    codec.value_type,
                )
            finally:
                scene_types.unregister_module(module_name)
                sys.modules.pop(module_name, None)


class ProjectTrustStoreTests(unittest.TestCase):
    def test_trust_is_stored_outside_project(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectTrustStore(root.parent / "trust.json")

            self.assertFalse(store.is_trusted(root))
            store.trust(root)
            self.assertTrue(store.is_trusted(root))
            self.assertFalse((root / "trusted-projects.json").exists())
            store.revoke(root)
            self.assertFalse(store.is_trusted(root))


if __name__ == "__main__":
    unittest.main()
