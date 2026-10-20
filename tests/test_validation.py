import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from g import (
    ProjectConfig,
    ProjectExportConfig,
    SceneDocument,
    SceneFormatError,
    scene_types,
    validate_project,
)
from g.validation import main


def _scene() -> SceneDocument:
    return SceneDocument(
        {
            "background": [0, 0, 0, 255],
            "size": [320, 180],
            "grid_cell_size": 16,
        },
        [],
    )


class ProjectValidationTests(unittest.TestCase):
    def test_valid_export_project_has_no_issues(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _scene().write(root / "main.scene.json")
            (root / "player.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            project = ProjectConfig.create(
                root,
                export=ProjectExportConfig(
                    "Test Game",
                    "1.2.3",
                    "main.scene.json",
                ),
            ).with_module("player")
            project.write()

            report = validate_project(
                project,
                purpose="export",
                import_scripts=False,
            )

            self.assertTrue(report.is_valid, report.format())
            self.assertEqual(report.issues, ())

    def test_collects_independent_missing_asset_errors(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = _scene()
            document.settings.update({
                "image": "missing.png",
                "font": "missing.ttf",
                "sound": "missing.ogg",
                "shader": "missing.frag",
                "source": "missing.prefab.json",
            })
            document.write(root / "main.scene.json")
            project = ProjectConfig.create(
                root,
                export=ProjectExportConfig(
                    startup_scene="missing.scene.json",
                ),
            )

            report = validate_project(
                project,
                purpose="export",
                import_scripts=False,
            )

            self.assertEqual(
                {issue.code for issue in report.issues},
                {
                    "asset.missing.audio",
                    "asset.missing.font",
                    "asset.missing.image",
                    "asset.missing.prefab",
                    "asset.missing.scene",
                    "asset.missing.shader",
                },
            )
            self.assertIn("6 errors", report.format())

    def test_reports_missing_and_invalid_script_modules(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.py").write_text(
                "def invalid(\n",
                encoding="utf-8",
            )
            project = (
                ProjectConfig.create(root)
                .with_module("missing")
                .with_module("broken")
            )

            report = validate_project(
                project,
                import_scripts=False,
            )

            self.assertEqual(
                {issue.code for issue in report.issues},
                {"script.invalid", "script.missing"},
            )

    def test_failed_script_import_does_not_leave_registered_types(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken_runtime.py").write_text(
                "from g import Script, scene_component\n"
                "@scene_component('validation.Leaked')\n"
                "class Leaked(Script):\n"
                "    pass\n"
                "raise RuntimeError('broken startup')\n",
                encoding="utf-8",
            )
            project = ProjectConfig.create(root).with_module(
                "broken_runtime"
            )

            report = validate_project(project)

            self.assertIn(
                "script.import",
                {issue.code for issue in report.issues},
            )
            with self.assertRaises(SceneFormatError):
                scene_types.component_codec_for_name(
                    "validation.Leaked"
                )

    def test_reports_unsupported_types_and_broken_object_references(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = _scene()
            objects = document.layers[0]["objects"]
            assert isinstance(objects, list)
            objects.append({
                "id": "player",
                "name": "Player",
                "type": "missing.Player",
                "target": {"$object": "missing-id"},
                "components": [
                    {
                        "type": "missing.Component",
                        "properties": {},
                    }
                ],
                "children": [],
            })
            document.write(root / "main.scene.json")
            project = ProjectConfig.create(root)

            report = validate_project(
                project,
                import_scripts=False,
            )

            self.assertEqual(
                {issue.code for issue in report.issues},
                {
                    "reference.object",
                    "type.component",
                    "type.object",
                },
            )

    def test_reports_assets_outside_project(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = _scene()
            document.settings["image"] = "../outside.png"
            document.write(root / "main.scene.json")

            report = validate_project(
                ProjectConfig.create(root),
                import_scripts=False,
            )

            self.assertEqual(
                [issue.code for issue in report.issues],
                ["asset.outside"],
            )
            self.assertIn("project root", report.issues[0].message)

    def test_reports_unsupported_prefab_version(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "old.prefab.json").write_text(
                '{"format":"g.prefab","version":1}',
                encoding="utf-8",
            )

            report = validate_project(
                ProjectConfig.create(root),
                import_scripts=False,
            )

            self.assertEqual(
                [issue.code for issue in report.issues],
                ["prefab.invalid"],
            )

    def test_reports_mismatched_prefab_instance_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "player.prefab.json").write_text(
                '{"format":"g.prefab","version":2,'
                '"prefab_id":"expected","revision":1,'
                '"kind":"game_object","template":{}}',
                encoding="utf-8",
            )
            document = _scene()
            objects = document.layers[0]["objects"]
            assert isinstance(objects, list)
            objects.append({
                "id": "player",
                "name": "Player",
                "type": "g.game_object",
                "components": [],
                "children": [],
                "prefab": {
                    "source": "player.prefab.json",
                    "prefab_id": "stale",
                    "revision": 1,
                    "kind": "game_object",
                    "base": {},
                },
            })
            document.write(root / "main.scene.json")

            report = validate_project(
                ProjectConfig.create(root),
                import_scripts=False,
            )

            self.assertIn(
                "reference.prefab",
                {issue.code for issue in report.issues},
            )

    def test_validates_windows_export_name(self) -> None:
        with TemporaryDirectory() as directory:
            project = ProjectConfig.create(
                directory,
                export=ProjectExportConfig(name="CON"),
            )

            report = validate_project(
                project,
                target="windows",
                import_scripts=False,
            )

            self.assertEqual(
                [issue.code for issue in report.issues],
                ["export.platform"],
            )

    def test_document_override_validates_unsaved_scene_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "main.scene.json"
            _scene().write(path)
            changed = _scene()
            changed.settings["image"] = "unsaved-missing.png"

            report = validate_project(
                ProjectConfig.create(root),
                document_overrides={path: changed},
                import_scripts=False,
            )

            self.assertEqual(
                [issue.code for issue in report.issues],
                ["asset.missing.image"],
            )

    def test_cli_returns_failure_and_json_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = ProjectConfig.create(
                root,
                export=ProjectExportConfig(
                    startup_scene="missing.scene.json",
                ),
            )
            project.write()
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                result = main([
                    str(project.path),
                    "--purpose",
                    "export",
                    "--no-import-scripts",
                    "--json",
                ])

            self.assertEqual(result, 1)
            self.assertIn('"valid": false', output.getvalue())
            self.assertIn('"asset.missing.scene"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
