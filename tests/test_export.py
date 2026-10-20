from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import pyglet

from g import Game, Image, ProjectConfig, ProjectExportConfig, SceneDocument
from g.export import (
    ExportError,
    export_project,
    prepare_export,
    project_python,
    validate_export_project,
)


def _project(root: Path) -> ProjectConfig:
    SceneDocument(
        {
            "background": [0, 0, 0, 255],
            "size": [320, 180],
            "grid_cell_size": 16,
        },
        [],
    ).write(root / "main.scene.json")
    (root / "player.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "texture.png").write_bytes(b"image")
    project = ProjectConfig.create(
        root,
        width=320,
        height=180,
        export=ProjectExportConfig(
            "Test Game",
            "1.2.3",
            "main.scene.json",
            hidden_imports=("plugin.runtime",),
            collect_packages=("package_data",),
        ),
    ).with_module("player")
    project.write()
    return project


class ExportTests(unittest.TestCase):
    def test_packaged_launcher_loads_resources_from_bundle_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = _project(root)
            Image.solid(4, 6, (255, 255, 0))._source.save(str(root / "texture.png"))
            prepared = prepare_export(project.path, python="/usr/bin/python3")
            loaded = []

            def run(game: Game, *, title: str) -> None:
                loaded.append(game.resources.load_image("texture.png"))
                self.assertEqual(game.resources.asset_root, prepared.staging_directory.resolve())
                self.assertEqual(title, project.export.name)

            previous_player = sys.modules.get("player")
            try:
                with (
                    patch.object(pyglet.options, "debug_gl", pyglet.options["debug_gl"]),
                    patch.object(sys, "_MEIPASS", str(prepared.staging_directory), create=True),
                    patch.object(Game, "run", autospec=True, side_effect=run),
                ):
                    source = prepared.bootstrap.read_text(encoding="utf-8")
                    exec(compile(source, str(prepared.bootstrap), "exec"), {"__file__": str(prepared.bootstrap)})
                self.assertEqual(len(loaded), 1)
                self.assertEqual((loaded[0].width, loaded[0].height), (4, 6))
                self.assertEqual(loaded[0]._filename, prepared.staging_directory / "texture.png")
            finally:
                if previous_player is None:
                    sys.modules.pop("player", None)
                else:
                    sys.modules["player"] = previous_player

    def test_export_disables_gl_checks_before_engine_import_in_fresh_process(self) -> None:
        with TemporaryDirectory() as directory:
            project = _project(Path(directory))
            prepared = prepare_export(project.path, python="/usr/bin/python3")
            source = prepared.bootstrap.read_text(encoding="utf-8")
            prefix = source.split("ROOT = ", 1)[0]
            self.assertLess(prefix.index("pyglet.options['debug_gl'] = False"), prefix.index("from g import Game"))
            result = subprocess.run(
                [
                    sys.executable, "-c",
                    prefix + "\nassert not pyglet.options['debug_gl']\n"
                    "assert getattr(pyglet.gl.glClear, 'errcheck', None) is None\n",
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_prepare_export_stages_project_and_generates_build_files(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = _project(root)
            (root / ".venv").mkdir()
            (root / ".venv" / "ignored.txt").write_text("ignored")

            prepared = prepare_export(project.path, python="/usr/bin/python3")

            self.assertTrue(
                (prepared.staging_directory / "main.scene.json").is_file()
            )
            self.assertTrue(
                (prepared.staging_directory / "texture.png").is_file()
            )
            self.assertFalse(
                (prepared.staging_directory / ".venv").exists()
            )
            bootstrap = prepared.bootstrap.read_text(encoding="utf-8")
            self.assertIn("main.scene.json", bootstrap)
            self.assertIn("game.scenes.switch('startup')", bootstrap)
            self.assertIn("game.run(title='Test Game')", bootstrap)
            spec = prepared.spec.read_text(encoding="utf-8")
            self.assertIn("'player'", spec)
            self.assertIn("'plugin.runtime'", spec)
            self.assertIn("collect_data_files('package_data')", spec)

    def test_startup_scene_must_stay_inside_project(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = ProjectConfig.create(
                root,
                export=ProjectExportConfig(
                    startup_scene="../outside.scene.json"
                ),
            )

            with self.assertRaisesRegex(
                ExportError,
                "inside the project",
            ):
                validate_export_project(project)

    def test_project_virtual_environment_is_preferred(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / ".venv" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text("", encoding="utf-8")

            self.assertEqual(
                project_python(root),
                interpreter.resolve(),
            )

    def test_export_invokes_pyinstaller_and_returns_executable(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = _project(root)
            prepared = prepare_export(
                project.path,
                python="/usr/bin/python3",
            )
            executable = (
                prepared.dist_directory
                / project.export.name
                / project.export.name
            )
            executable.parent.mkdir(parents=True)
            executable.write_text("", encoding="utf-8")
            process = Mock()
            process.stdout = iter(["building\n", "done\n"])
            process.wait.return_value = 0
            output: list[str] = []

            with (
                patch("g.export.prepare_export", return_value=prepared),
                patch("g.export._ensure_pyinstaller"),
                patch("g.export.subprocess.Popen", return_value=process),
            ):
                result = export_project(
                    project.path,
                    output=output.append,
                )

            self.assertEqual(result.executable, executable)
            self.assertIn("building", output)
            self.assertIn("done", output)


if __name__ == "__main__":
    unittest.main()
