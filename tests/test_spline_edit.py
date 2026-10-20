import unittest
from copy import deepcopy
from unittest.mock import patch

from pyglet.window import key

from g import Spline
from g.components.renderer_utils import _transform_point
from g_editor.state import EditorState


class SplineEditStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = EditorState()
        self.state.add_object("empty")
        self.state.add_component("g.spline")

    def test_geometry_change_is_atomic_undoable_and_independent_of_transform(self) -> None:
        state = self.state
        original = deepcopy(state.document)
        state.set_spline_geometry(0, {"points": [[0, 0], [20, 40], [60, 0]]})
        self.assertTrue(state.undo())
        self.assertEqual(state.document, original)
        self.assertTrue(state.redo())
        self.assertEqual(state._selected_component(0)[1]["properties"]["points"], [[0, 0], [20, 40], [60, 0]])

    def test_inspector_edits_reject_invalid_values_without_history_or_document_changes(self) -> None:
        state = self.state
        for name, value in (
            ("points", [[0, 0], [0, 0]]),
            ("points", [[0, 0]]),
            ("points", [[True, 0], [2, 0]]),
            ("points", [[0, 0], [float("nan"), 0]]),
            ("closed", "yes"),
            ("tolerance", 0),
            ("tolerance", float("inf")),
        ):
            with self.subTest(name=name, value=value):
                before = deepcopy(state.document)
                history = state._history_cursor
                with self.assertRaises(ValueError):
                    state.set_component_value(0, name, value)
                self.assertEqual(state.document, before)
                self.assertEqual(state._history_cursor, history)

    def test_closing_two_point_spline_requires_third_point(self) -> None:
        self.state.set_spline_geometry(0, {"points": [[0, 0], [50, 0]]})
        with self.assertRaises(ValueError):
            self.state.set_component_value(0, "closed", True)
        self.state.set_spline_geometry(0, {"points": [[0, 0], [50, 0], [20, 50]], "closed": True})


try:
    from imgui_bundle import imgui
    from g_editor.application import ImGuiEditor
except ImportError:
    imgui = None
    ImGuiEditor = None


@unittest.skipIf(ImGuiEditor is None, "ImGui editor dependency is not installed")
class ComponentToolViewportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.editor = ImGuiEditor(visible=False)
        self.editor.window.switch_to()
        self.editor.state.add_object("empty")
        self.editor.state.add_component("g.collider")
        self.editor.state.add_component("g.spline")
        self.editor.preview.synchronize()
        self.editor._activate_component_tool("spline", 1)

    def tearDown(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()

    def spline(self) -> Spline:
        spline = self.editor._active_spline()
        assert spline is not None
        return spline

    def test_activation_targets_component_and_escape_returns_to_transform(self) -> None:
        editor = self.editor
        self.assertTrue(editor._component_tool_is_active("spline", 1))
        self.assertFalse(editor._component_tool_is_active("collider", 0))
        editor._activate_component_tool("collider", 0)
        self.assertEqual(editor._viewport_tool, "collider")
        self.assertEqual(editor._active_collider()[0], 0)
        editor.on_key_press(key.ESCAPE, 0)
        self.assertEqual(editor._viewport_tool, "transform")
        self.assertIsNone(editor._tool_target)

    def test_drag_previews_and_commits_one_undo_step(self) -> None:
        editor = self.editor
        original = deepcopy(editor.state.document)
        history = editor.state._history_cursor
        spline = self.spline()
        owner = spline.owner
        owner.rotation = 30
        owner.scale_x, owner.scale_y = -2, 3
        editor._begin_spline_drag(1)
        for point in ((12, 20), (22, 35), (30, 40)):
            editor._update_spline_drag(_transform_point(owner, point), down=True)
        self.assertEqual(editor.state.document, original)
        self.assertAlmostEqual(spline.points[1][0], 30)
        self.assertAlmostEqual(spline.points[1][1], 40)
        editor._update_spline_drag((0, 0), down=False)
        self.assertEqual(editor.state._history_cursor, history + 1)
        self.assertTrue(editor.state.undo())
        self.assertEqual(editor.state.document, original)
        self.assertTrue(editor.state.redo())

    def test_escape_cancels_drag_then_exits_without_discarding_committed_edits(self) -> None:
        editor = self.editor
        original = deepcopy(editor.state.document)
        points = self.spline().points
        editor._begin_spline_drag(1)
        editor._update_spline_drag((30, 40), down=True)
        editor.on_key_press(key.ESCAPE, 0)
        self.assertEqual(editor._viewport_tool, "spline")
        self.assertIsNone(editor._spline_drag)
        self.assertEqual(self.spline().points, points)
        self.assertEqual(editor.state.document, original)
        editor._begin_spline_drag(1)
        editor._update_spline_drag((30, 40), down=True)
        editor._update_spline_drag((0, 0), down=False)
        committed = deepcopy(editor.state.document)
        editor.on_key_press(key.ESCAPE, 0)
        self.assertEqual(editor._viewport_tool, "transform")
        self.assertEqual(editor.state.document, committed)

    def test_selection_change_component_removal_and_play_exit_safely(self) -> None:
        editor = self.editor
        original = deepcopy(editor.state.document)
        editor._begin_spline_drag(1)
        editor._update_spline_drag((30, 40), down=True)
        identifier = editor.state.selected_id
        editor.state.select(None)
        editor._validate_component_tool()
        self.assertEqual(editor._viewport_tool, "transform")
        self.assertIsNone(editor._spline_drag)
        self.assertEqual(editor.state.document, original)
        editor.state.select(identifier)
        editor._activate_component_tool("spline", 1)
        editor.preview.set_mode("play")
        editor._validate_component_tool()
        self.assertEqual(editor._viewport_tool, "transform")
        editor.preview.set_mode("edit")
        editor._activate_component_tool("spline", 1)
        editor.state.remove_component(1)
        editor.preview.synchronize()
        editor._validate_component_tool()
        self.assertEqual(editor._viewport_tool, "transform")

    def test_switching_tool_cancels_preview_and_targets_exact_collider(self) -> None:
        editor = self.editor
        editor.state.add_component("g.collider")
        editor.preview.synchronize()
        original = deepcopy(editor.state.document)
        editor._begin_spline_drag(1)
        editor._update_spline_drag((30, 40), down=True)
        editor._activate_component_tool("collider", 2)
        self.assertEqual(editor.state.document, original)
        self.assertIsNone(editor._spline_drag)
        self.assertTrue(editor._component_tool_is_active("collider", 2))
        self.assertEqual(editor._active_collider()[0], 2)

    def test_unavailable_tool_reports_error_and_preserves_active_tool(self) -> None:
        editor = self.editor
        with self.assertRaises(ValueError):
            editor._activate_component_tool("collider", 1)
        self.assertEqual(editor._viewport_tool, "spline")
        editor._activate_component_tool("transform")
        editor.state.select(None)
        with self.assertRaises(ValueError):
            editor._activate_component_tool("spline")

    def test_invalid_drag_reports_error_and_retains_last_valid_points(self) -> None:
        editor = self.editor
        spline = self.spline()
        original = spline.points
        editor._begin_spline_drag(1)
        editor._update_spline_drag(_transform_point(spline.owner, original[0]), down=True)
        self.assertEqual(spline.points, original)
        self.assertIsNotNone(editor.state.error)
        editor._component_tool_escape()

    def test_shift_click_inserts_then_delete_removes_point_and_is_undoable(self) -> None:
        editor = self.editor
        spline = self.spline()
        point = spline.world_curve.point_at(0.25)
        x, y = editor.preview.world_to_screen(*point)
        imgui.get_io().key_shift = True
        try:
            self.assertTrue(editor._handle_spline_click(x, y))
        finally:
            imgui.get_io().key_shift = False
        editor.preview.synchronize()
        self.assertEqual(len(self.spline().points), 4)
        editor.on_key_press(key.DELETE, 0)
        editor.preview.synchronize()
        self.assertEqual(len(self.spline().points), 3)
        self.assertTrue(editor.state.undo())
        editor.preview.synchronize()
        self.assertEqual(len(editor.preview.selected_runtime_object().require_component(Spline).points), 4)

    def test_minimum_point_delete_reports_error_and_never_deletes_object(self) -> None:
        editor = self.editor
        editor.state.set_spline_geometry(1, {"points": [[0, 0], [100, 0]]})
        editor.preview.synchronize()
        editor._spline_point = 0
        editor.on_key_press(key.DELETE, 0)
        self.assertIn("at least 2", editor.state.error)
        self.assertEqual(len(editor.state.records), 1)

    def test_spline_overlay_is_editor_only_and_normal_mode_has_no_handles(self) -> None:
        editor = self.editor
        with patch("g_editor.component_tools.imgui.get_window_draw_list") as drawing:
            editor._draw_spline_overlay(imgui.ImVec2(0, 0), imgui.ImVec2(800, 600))
            self.assertGreater(drawing.return_value.add_line.call_count, 2)
            self.assertEqual(drawing.return_value.add_circle_filled.call_count, 3)
        editor._activate_component_tool("transform")
        with patch("g_editor.component_tools.imgui.get_window_draw_list") as drawing:
            editor._draw_spline_overlay(imgui.ImVec2(0, 0), imgui.ImVec2(800, 600))
            self.assertEqual(drawing.return_value.add_circle_filled.call_count, 0)
        editor.preview.set_mode("play")
        with patch("g_editor.component_tools.imgui.get_window_draw_list") as drawing:
            editor._draw_spline_overlay(imgui.ImVec2(0, 0), imgui.ImVec2(800, 600))
            drawing.assert_not_called()

    def test_real_viewport_input_routes_drag_and_release(self) -> None:
        editor = self.editor
        spline = self.spline()
        point = _transform_point(spline.owner, spline.points[1])
        x, y = editor.preview.world_to_screen(*point)
        width, height = editor.render_target.width, editor.render_target.height
        minimum, maximum = imgui.ImVec2(0, 0), imgui.ImVec2(width, height)
        editor._viewport_hovered = True
        left = int(imgui.MouseButton_.left)
        with (
            patch("g_editor.application.imgui.get_mouse_pos", return_value=imgui.ImVec2(x, height - y)),
            patch("g_editor.application.imgui.is_mouse_clicked", side_effect=lambda button: button == left),
            patch("g_editor.application.imgui.is_mouse_down", return_value=True),
        ):
            editor._handle_viewport_input(minimum, maximum)
        self.assertIsNotNone(editor._spline_drag)
        x, y = editor.preview.world_to_screen(point[0] + 20, point[1] + 10)
        with (
            patch("g_editor.application.imgui.get_mouse_pos", return_value=imgui.ImVec2(x, height - y)),
            patch("g_editor.application.imgui.is_mouse_down", return_value=True),
        ):
            editor._handle_viewport_input(minimum, maximum)
        history = editor.state._history_cursor
        with (
            patch("g_editor.application.imgui.get_mouse_pos", return_value=imgui.ImVec2(x, height - y)),
            patch("g_editor.application.imgui.is_mouse_down", return_value=False),
        ):
            editor._handle_viewport_input(minimum, maximum)
        self.assertIsNone(editor._spline_drag)
        self.assertEqual(editor.state._history_cursor, history + 1)

    def test_full_editor_frame_has_no_imgui_errors(self) -> None:
        self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)

    def test_editor_undo_redo_preserves_tool_when_target_still_exists(self) -> None:
        editor = self.editor
        editor._begin_spline_drag(1)
        editor._update_spline_drag((30, 40), down=True)
        editor._update_spline_drag((0, 0), down=False)
        editor._undo()
        self.assertTrue(editor._component_tool_is_active("spline", 1))
        editor._redo()
        self.assertTrue(editor._component_tool_is_active("spline", 1))

    def test_collider_escape_cancels_drag_before_exiting(self) -> None:
        editor = self.editor
        editor._activate_component_tool("collider", 0)
        original = deepcopy(editor.state.document)
        editor._begin_collider_drag("center", (0, 0))
        editor._update_collider_drag((10, 20), down=True)
        editor.on_key_press(key.ESCAPE, 0)
        self.assertEqual(editor._viewport_tool, "collider")
        self.assertIsNone(editor._collider_drag)
        self.assertEqual(editor.state.document, original)
        editor.on_key_press(key.ESCAPE, 0)
        self.assertEqual(editor._viewport_tool, "transform")

    def test_transform_shortcut_leaves_component_tool_and_cancels_drag(self) -> None:
        editor = self.editor
        original = deepcopy(editor.state.document)
        editor._begin_spline_drag(1)
        editor._update_spline_drag((30, 40), down=True)
        editor.on_key_press(key.R, 0)
        self.assertEqual(editor._viewport_tool, "transform")
        self.assertEqual(editor._transform_tool, "scale")
        self.assertEqual(editor.state.document, original)

    def test_inspector_button_activates_exact_component_and_done_returns_to_transform(self) -> None:
        editor = self.editor
        editor._activate_component_tool("transform")
        editor._component_open[f"{editor.state.selected_id}:g.collider"] = False
        for _ in range(3):
            editor.on_draw()
        button = imgui.button
        clicked: list[str] = []

        def click_tool(label, *args, **kwargs):
            result = button(label, *args, **kwargs)
            if label.endswith("##component-tool-1"):
                clicked.append(label)
                return True
            return result

        with patch("g_editor.panels.inspector.imgui.button", side_effect=click_tool):
            editor.on_draw()
        self.assertIn("Edit in Viewport##component-tool-1", clicked)
        self.assertTrue(editor._component_tool_is_active("spline", 1))
        clicked.clear()
        with patch("g_editor.panels.inspector.imgui.button", side_effect=click_tool):
            editor.on_draw()
        self.assertIn("Done Editing##component-tool-1", clicked)
        self.assertEqual(editor._viewport_tool, "transform")
        self.assertIsNone(editor.state.error)


if __name__ == "__main__":
    unittest.main()
