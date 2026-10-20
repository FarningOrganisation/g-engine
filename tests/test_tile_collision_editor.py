import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from imgui_bundle import imgui

from g_editor.panels.inspector import InspectorPanel
from g_editor.state import EditorState
from g.persistence import JsonValue


class TileCollisionEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = EditorState()
        self.state.add_tile_layer("Ground")
        self.state.add_color_tileset()
        self.panel = InspectorPanel()
        self.panel.state = self.state
        self.panel.preview = Mock()
        self.panel._tile_collision_drag = None
        self.panel._tile_collision_target = None
        self.panel._tile_collision_point = None
        self.panel._tile_collision_add = False
        self.panel._shortcut_context = None
        self.panel._draw_tile_collision_image = Mock()
        self.io = SimpleNamespace(
            key_shift=False, key_ctrl=False, key_super=False, want_text_input=False,
        )
        self.gui = Mock()
        self.gui.ImVec2 = imgui.ImVec2
        self.gui.MouseButton_ = imgui.MouseButton_
        self.gui.Key = imgui.Key
        self.gui.MouseCursor_ = imgui.MouseCursor_
        self.gui.get_io.return_value = self.io
        self.gui.get_content_region_avail.return_value = imgui.ImVec2(240, 240)
        self.gui.get_item_rect_min.return_value = imgui.ImVec2(0, 0)
        self.gui.is_item_hovered.return_value = True
        self.gui.is_window_focused.return_value = True
        self.gui.is_any_item_active.return_value = False
        self.gui.is_key_pressed.return_value = False
        self.patcher = patch("g_editor.panels.inspector.imgui", self.gui)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.color = patch("g_editor.panels.inspector._color", return_value=0)
        self.color_mock = self.color.start()
        self.addCleanup(self.color.stop)
        self.icon = patch("g_editor.panels.inspector.icon_button", return_value=False)
        self.icon_mock = self.icon.start()
        self.addCleanup(self.icon.stop)
        self.set_collision({
            "type": "polygon",
            "polygons": [[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]],
            "generated_from_alpha": True,
            "alpha_threshold": 128,
        })

    def set_collision(self, value: dict[str, JsonValue]) -> None:
        self.state.set_tile_definition_value(0, "collision", value, coalesce=False)

    def collision(self) -> dict[str, JsonValue]:
        definition = self.state.tile_definition(0, create=False)
        assert definition is not None
        value = definition["collision"]
        assert isinstance(value, dict)
        return value

    def frame(
        self, pointer: tuple[float, float], *, clicked: bool = False,
        down: bool = False, escape: bool = False, delete: bool = False,
    ) -> None:
        layer = self.state.selected_layer
        assert layer is not None
        cell_size = layer["cell_size"]
        assert isinstance(cell_size, list)
        width, height = cell_size
        assert isinstance(width, (int, float)) and isinstance(height, (int, float))
        canvas_height = max(80.0, min(240.0, 240 * height / width))
        self.gui.get_item_rect_max.return_value = imgui.ImVec2(240, canvas_height)
        self.gui.get_mouse_pos.return_value = imgui.ImVec2(
            pointer[0] * 240, (1 - pointer[1]) * canvas_height,
        )
        self.gui.is_item_clicked.return_value = clicked
        self.gui.is_mouse_down.return_value = down
        self.gui.is_key_pressed.side_effect = lambda key: (
            escape and key == imgui.Key.escape or delete and key == imgui.Key.delete
        )
        self.panel._draw_tile_collision_editor(0, self.collision())

    def test_vertex_drag_previews_then_commits_one_undo_step(self) -> None:
        before = deepcopy(self.collision())
        history = self.state._history_cursor
        self.frame((0.2, 0.2), clicked=True, down=True)
        self.frame((0.1, 0.2), down=True)
        self.frame((0.1, 0.1), down=True)
        self.assertEqual(self.collision(), before)
        drag = self.panel._tile_collision_drag
        assert drag is not None
        self.assertNotEqual(drag.latest, before)
        self.panel.preview.reload.assert_not_called()
        self.frame((0.1, 0.1))
        after = deepcopy(self.collision())
        self.assertEqual(self.state._history_cursor, history + 1)
        self.assertFalse(after["generated_from_alpha"])
        self.assertEqual(after["alpha_threshold"], 128)
        self.panel.preview.reload.assert_called_once()
        self.assertTrue(self.state.undo())
        self.assertEqual(self.collision(), before)
        self.assertTrue(self.state.redo())
        self.assertEqual(self.collision(), after)

    def test_shift_click_inserts_selected_vertex_without_tool_buttons(self) -> None:
        self.io.key_shift = True
        self.frame((0.5, 0.2), clicked=True, down=True)
        polygons = InspectorPanel._collision_polygons(self.collision())
        self.assertEqual(polygons[0][1], [0.5, 0.2])
        self.assertEqual(self.panel._tile_collision_point, (0, 1))
        self.assertIsNone(self.panel._tile_collision_drag)
        self.gui.button.assert_not_called()
        self.assertTrue(self.state.undo())
        self.assertEqual(len(InspectorPanel._collision_polygons(self.collision())[0]), 4)

    def test_select_then_command_delete_preserves_minimum_and_reports_error(self) -> None:
        self.frame((0.2, 0.2), clicked=True, down=True)
        self.frame((0.2, 0.2))
        self.io.key_super = True
        self.frame((0.2, 0.2), delete=True)
        self.assertEqual(len(InspectorPanel._collision_polygons(self.collision())[0]), 3)
        self.frame((0.8, 0.2), clicked=True, down=True)
        self.frame((0.8, 0.2))
        before = deepcopy(self.collision())
        self.io.key_super = False
        self.io.key_ctrl = True
        self.frame((0.8, 0.2), delete=True)
        self.assertEqual(self.collision(), before)
        self.assertIn("three vertices", self.state.error or "")

    def test_escape_discards_preview_without_history_changes(self) -> None:
        before = deepcopy(self.collision())
        history = self.state._history_cursor
        self.frame((0.2, 0.2), clicked=True, down=True)
        self.frame((0.1, 0.1), down=True)
        self.frame((0.1, 0.1), down=True, escape=True)
        self.frame((0.1, 0.1))
        self.assertIsNone(self.panel._tile_collision_drag)
        self.assertEqual(self.collision(), before)
        self.assertEqual(self.state._history_cursor, history)

    def test_invalid_polygon_drag_keeps_last_valid_preview(self) -> None:
        self.frame((0.8, 0.2), clicked=True, down=True)
        self.frame((0.9, 0.2), down=True)
        drag = self.panel._tile_collision_drag
        assert drag is not None
        valid = deepcopy(drag.latest)
        self.frame((0.1, 0.7), down=True)
        self.assertEqual(drag.latest, valid)
        self.assertIn("self-intersect", self.state.error or "")
        self.frame((0.1, 0.7))
        self.assertEqual(self.collision(), valid)

    def test_concave_multiple_polygons_remain_supported_and_move_independently(self) -> None:
        self.set_collision({
            "type": "polygon",
            "polygons": [
                [[0.1, 0.1], [0.5, 0.1], [0.5, 0.2], [0.2, 0.2],
                 [0.2, 0.5], [0.1, 0.5]],
                [[0.7, 0.7], [0.9, 0.7], [0.9, 0.9], [0.7, 0.9]],
            ],
        })
        before = InspectorPanel._collision_polygons(self.collision())
        self.frame((0.3, 0.1), clicked=True, down=True)
        self.frame((0.4, 0.2), down=True)
        self.frame((0.4, 0.2))
        after = InspectorPanel._collision_polygons(self.collision())
        self.assertEqual(after[1], before[1])
        for (ax, ay), (bx, by) in zip(after[0], before[0]):
            self.assertAlmostEqual(ax, bx + 0.1)
            self.assertAlmostEqual(ay, by + 0.1)

    def test_non_square_canvas_hit_testing_uses_screen_pixels(self) -> None:
        layer = self.state.selected_layer
        assert layer is not None
        layer["cell_size"] = [32, 8]
        self.frame((0.2, 0.3), clicked=True, down=True)
        self.assertEqual(self.panel._tile_collision_point, (0, 0))

    def test_rectangle_edge_resize_matches_viewport_and_keeps_opposite_edge(self) -> None:
        self.set_collision({"type": "rectangle", "x": 8, "y": 8, "width": 16, "height": 16})
        before = deepcopy(self.collision())
        self.frame((0.75, 0.5), clicked=True, down=True)
        drag = self.panel._tile_collision_drag
        assert drag is not None
        self.assertEqual(drag.handle, "size:1:0")
        self.frame((0.9, 0.5), down=True)
        self.assertEqual(self.collision(), before)
        self.frame((0.9, 0.5))
        after = self.collision()
        self.assertAlmostEqual(after["x"], 8)
        self.assertEqual(after["y"], 8)
        self.assertEqual(after["height"], 16)
        self.assertAlmostEqual(after["width"], 20.8)
        self.color_mock.assert_any_call(245, 196, 85, 255)

    def test_rectangle_center_moves_and_clamps_to_tile(self) -> None:
        self.set_collision({"type": "rectangle", "x": 8, "y": 8, "width": 16, "height": 16})
        self.frame((0.5, 0.5), clicked=True, down=True)
        self.frame((1.5, -0.5), down=True)
        self.frame((1.5, -0.5))
        self.assertEqual(self.collision()["x"], 16)
        self.assertEqual(self.collision()["y"], 0)
        self.assertEqual(self.collision()["width"], 16)

    def test_click_without_drag_preserves_generated_metadata_and_history(self) -> None:
        before = deepcopy(self.collision())
        history = self.state._history_cursor
        self.frame((0.2, 0.2), clicked=True, down=True)
        self.frame((0.2, 0.2))
        self.assertEqual(self.collision(), before)
        self.assertEqual(self.state._history_cursor, history)

    def test_full_rectangle_cannot_move_and_does_not_create_history(self) -> None:
        self.set_collision({
            "type": "rectangle", "width": None, "height": None, "generated_from_alpha": True,
        })
        before = deepcopy(self.collision())
        history = self.state._history_cursor
        self.frame((0.5, 0.5), clicked=True, down=True)
        self.frame((0.8, 0.8), down=True)
        self.frame((0.8, 0.8))
        self.assertEqual(self.collision(), before)
        self.assertEqual(self.state._history_cursor, history)

    def test_shift_click_sloped_edge_uses_projection(self) -> None:
        self.set_collision({
            "type": "polygon",
            "polygons": [[[0.1, 0.1], [0.9, 0.3], [0.5, 0.9]]],
        })
        self.io.key_shift = True
        self.frame((0.5, 0.2), clicked=True, down=True)
        polygon = InspectorPanel._collision_polygons(self.collision())[0]
        self.assertEqual(len(polygon), 4)
        self.assertAlmostEqual(polygon[1][0], 0.5)
        self.assertAlmostEqual(polygon[1][1], 0.2)

    def test_delete_does_not_interfere_with_text_fields_or_other_panels(self) -> None:
        self.frame((0.2, 0.2), clicked=True, down=True)
        self.frame((0.2, 0.2))
        before = deepcopy(self.collision())
        self.io.key_ctrl = True
        self.io.want_text_input = True
        self.frame((0.2, 0.2), delete=True)
        self.io.want_text_input = False
        self.gui.is_window_focused.return_value = False
        self.frame((0.2, 0.2), delete=True)
        self.assertEqual(self.collision(), before)

    def test_external_edit_cancels_stale_drag(self) -> None:
        self.frame((0.2, 0.2), clicked=True, down=True)
        self.frame((0.1, 0.1), down=True)
        self.set_collision({"type": "rectangle", "width": None, "height": None})
        self.frame((0.1, 0.1))
        self.assertIsNone(self.panel._tile_collision_drag)
        self.assertEqual(self.collision()["type"], "rectangle")

    def test_consecutive_drags_are_separate_undo_steps(self) -> None:
        history = self.state._history_cursor
        before = deepcopy(self.collision())
        self.frame((0.2, 0.2), clicked=True, down=True)
        self.frame((0.1, 0.2), down=True)
        self.frame((0.1, 0.2))
        first = deepcopy(self.collision())
        self.frame((0.1, 0.2), clicked=True, down=True)
        self.frame((0.1, 0.1), down=True)
        self.frame((0.1, 0.1))
        self.assertEqual(self.state._history_cursor, history + 2)
        self.state.undo()
        self.assertEqual(self.collision(), first)
        self.state.undo()
        self.assertEqual(self.collision(), before)

    def test_add_separate_polygon_preserves_shapes_and_is_one_undo_step(self) -> None:
        self.frame((0, 0))
        before = deepcopy(self.collision())
        history = self.state._history_cursor
        self.panel._tile_collision_add = True
        self.frame((0.05, 0.05), clicked=True, down=True)
        self.frame((0.15, 0.15), down=True)
        self.assertEqual(self.collision(), before)
        self.frame((0.15, 0.15))
        polygons = InspectorPanel._collision_polygons(self.collision())
        self.assertEqual(len(polygons), 2)
        self.assertEqual(polygons[0], InspectorPanel._collision_polygons(before)[0])
        for point, expected in zip(polygons[1], (
            (0.05, 0.05), (0.15, 0.05), (0.15, 0.15), (0.05, 0.15),
        )):
            self.assertAlmostEqual(point[0], expected[0])
            self.assertAlmostEqual(point[1], expected[1])
        self.assertFalse(self.panel._tile_collision_add)
        self.assertEqual(self.state._history_cursor, history + 1)
        self.state.undo()
        self.assertEqual(self.collision(), before)
        self.state.redo()
        self.assertEqual(len(InspectorPanel._collision_polygons(self.collision())), 2)

    def test_add_polygon_to_rectangle_preserves_original_rectangle(self) -> None:
        self.set_collision({"type": "rectangle", "x": 8, "y": 8, "width": 8, "height": 8})
        self.frame((0, 0))
        self.panel._tile_collision_add = True
        self.frame((0.7, 0.7), clicked=True, down=True)
        self.frame((0.9, 0.9), down=True)
        self.frame((0.9, 0.9))
        polygons = InspectorPanel._collision_polygons(self.collision())
        self.assertEqual(polygons[0], [[0.25, 0.25], [0.5, 0.25], [0.5, 0.5], [0.25, 0.5]])
        self.assertEqual(len(polygons), 2)
        self.assertNotIn("width", self.collision())
        self.assertEqual(self.collision()["type"], "polygon")

    def test_add_polygon_escape_and_tiny_drag_do_not_change_collision(self) -> None:
        self.frame((0, 0))
        before = deepcopy(self.collision())
        self.panel._tile_collision_add = True
        self.frame((0.1, 0.1), clicked=True, down=True)
        self.frame((0.6, 0.6), down=True)
        self.frame((0.6, 0.6), escape=True)
        self.assertEqual(self.collision(), before)
        self.assertFalse(self.panel._tile_collision_add)
        self.panel._tile_collision_add = True
        self.frame((0.1, 0.1), clicked=True, down=True)
        self.frame((0.105, 0.105))
        self.assertEqual(self.collision(), before)

    def test_larger_vertex_hit_radius_and_hand_cursor(self) -> None:
        self.frame((0.2 - 15 / 240, 0.2))
        self.gui.set_mouse_cursor.assert_called_with(imgui.MouseCursor_.hand)
        self.frame((0.2 - 15 / 240, 0.2), clicked=True, down=True)
        self.assertEqual(self.panel._tile_collision_point, (0, 0))
        self.frame((-1, -1), down=True)
        self.gui.set_mouse_cursor.assert_called_with(imgui.MouseCursor_.hand)

    def test_hand_cursor_only_over_interactive_shape(self) -> None:
        self.frame((0.05, 0.95))
        self.gui.set_mouse_cursor.assert_not_called()
        self.frame((0.5, 0.2))
        self.gui.set_mouse_cursor.assert_called_with(imgui.MouseCursor_.hand)
