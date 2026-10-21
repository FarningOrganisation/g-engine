import inspect
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from g import Script, scene_component
from g_editor.code_editor import _vscode_command, open_script_in_editor
from g_editor.panels.inspector import InspectorPanel
from g_editor.state import EditorState


@scene_component("tests.code_editor_script")
class CodeEditorScript(Script):
    pass


class CodeEditorTests(unittest.TestCase):
    @patch("g_editor.code_editor.shutil.which", return_value="/bin/code")
    @patch("g_editor.code_editor.subprocess.run")
    def test_opens_source_at_class_using_existing_window(self, run, which) -> None:
        path = open_script_in_editor(CodeEditorScript)

        self.assertEqual(path, Path(__file__).resolve())
        line = inspect.getsourcelines(CodeEditorScript)[1]
        run.assert_called_once_with(
            ["/bin/code", "--reuse-window", "--goto", f"{path}:{line}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        which.assert_called_once_with("code")

    @patch("g_editor.code_editor.shutil.which", return_value=None)
    @patch("g_editor.code_editor.platform.system", return_value="Darwin")
    def test_finds_macos_app_without_code_on_path(self, system, which) -> None:
        for directory in (Path("/Applications"), Path.home() / "Applications"):
            expected = directory / "Visual Studio Code.app/Contents/Resources/app/bin/code"
            with self.subTest(directory=directory):
                with patch(
                    "g_editor.code_editor.Path.is_file",
                    autospec=True,
                    side_effect=lambda path: path == expected,
                ):
                    self.assertEqual(_vscode_command(), str(expected))

    @patch("g_editor.code_editor.shutil.which", return_value=None)
    @patch("g_editor.code_editor.platform.system", return_value="Linux")
    @patch("g_editor.code_editor.subprocess.run")
    def test_missing_editor_reports_actionable_error(self, run, system, which) -> None:
        with self.assertRaisesRegex(ValueError, "Visual Studio Code was not found"):
            open_script_in_editor(CodeEditorScript)
        run.assert_not_called()

    @patch("g_editor.code_editor.inspect.getsourcefile", return_value=None)
    @patch("g_editor.code_editor.subprocess.run")
    def test_missing_source_does_not_launch(self, run, source) -> None:
        with self.assertRaisesRegex(ValueError, "No Python source"):
            open_script_in_editor(CodeEditorScript)
        run.assert_not_called()

    @patch("g_editor.code_editor.inspect.getsourcefile", return_value="/missing/script.py")
    @patch("g_editor.code_editor.Path.is_file", return_value=False)
    @patch("g_editor.code_editor.subprocess.run")
    def test_deleted_source_does_not_launch(self, run, is_file, source) -> None:
        with self.assertRaisesRegex(ValueError, "Script source does not exist"):
            open_script_in_editor(CodeEditorScript)
        run.assert_not_called()

    @patch("g_editor.code_editor._vscode_command", return_value="/bin/code")
    @patch("g_editor.code_editor.subprocess.run")
    def test_launch_errors_are_reported(self, run, command) -> None:
        for error, message in (
            (OSError("permission denied"), "permission denied"),
            (subprocess.TimeoutExpired("code", 10), "timed out"),
            (
                subprocess.CalledProcessError(1, "code", stderr="launch failed"),
                "launch failed",
            ),
        ):
            with self.subTest(error=error):
                run.side_effect = error
                with self.assertRaisesRegex(ValueError, message):
                    open_script_in_editor(CodeEditorScript)

    @patch("g_editor.panels.inspector.open_script_in_editor")
    def test_inspector_opens_registered_script_and_clears_error(self, open_script) -> None:
        panel = InspectorPanel()
        panel.state = EditorState()
        panel.state.error = "previous error"
        open_script.return_value = Path(__file__)

        panel._open_component_script("tests.code_editor_script")

        open_script.assert_called_once_with(CodeEditorScript)
        self.assertIsNone(panel.state.error)
        self.assertIn("VS Code", panel.state.status)

    @patch(
        "g_editor.panels.inspector.open_script_in_editor",
        side_effect=ValueError("editor unavailable"),
    )
    def test_inspector_surfaces_launch_failure(self, open_script) -> None:
        panel = InspectorPanel()
        panel.state = EditorState()

        panel._open_component_script("tests.code_editor_script")

        self.assertEqual(panel.state.error, "editor unavailable")

    def test_component_menu_only_offers_open_for_scripts(self) -> None:
        for component_type, expected in (
            ("tests.code_editor_script", True),
            ("g.rect_renderer", False),
        ):
            with self.subTest(component_type=component_type):
                panel = InspectorPanel()
                panel.state = EditorState()
                panel.state.add_object("empty")
                panel._component_clipboard = None
                with (
                    patch("g_editor.panels.inspector.imgui.begin_popup", return_value=True),
                    patch(
                        "g_editor.panels.inspector.imgui.menu_item",
                        side_effect=lambda label, *args: (
                            label == "Open Script in VS Code", False
                        ),
                    ) as menu_item,
                    patch("g_editor.panels.inspector.imgui.separator"),
                    patch("g_editor.panels.inspector.imgui.begin_disabled"),
                    patch("g_editor.panels.inspector.imgui.is_item_hovered", return_value=False),
                    patch("g_editor.panels.inspector.imgui.end_disabled"),
                    patch("g_editor.panels.inspector.imgui.end_popup"),
                    patch.object(panel.state, "component_removal_error", return_value=None),
                    patch.object(panel, "_open_component_script") as open_script,
                ):
                    removed = panel._draw_component_actions(
                        "actions", 0, component_type, "Component", {},
                    )
                self.assertFalse(removed)
                labels = [call.args[0] for call in menu_item.call_args_list]
                self.assertEqual("Open Script in VS Code" in labels, expected)
                if expected:
                    open_script.assert_called_once_with(component_type)
                else:
                    open_script.assert_not_called()


if __name__ == "__main__":
    unittest.main()
