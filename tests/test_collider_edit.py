import unittest
from copy import deepcopy
from unittest.mock import patch

from pyglet.window import key

from g import Collider, GameObject
from g.components.renderer_utils import _transform_point
from g.persistence import JsonValue
from g_editor.collider_edit import drag_values, geometry_values, handles, local_point, number, validate_geometry
from g_editor.state import EditorState


class ColliderEditGeometryTests(unittest.TestCase):
    def test_local_conversion_handles_rotated_mirrored_parent(self) -> None:
        parent = GameObject(25, -9)
        parent.rotation = 35
        parent.scale_x, parent.scale_y = -2, 3
        owner = GameObject(8, 6)
        parent.add_child(owner)
        owner.rotation = -17
        point = (12.0, -30.0)
        actual = local_point(owner, _transform_point(owner, point))
        self.assertAlmostEqual(actual[0], point[0])
        self.assertAlmostEqual(actual[1], point[1])
        owner.scale_x = 0
        with self.assertRaisesRegex(ValueError, "zero"):
            local_point(owner, (0, 0))

    def test_box_edge_resize_keeps_opposite_edge_and_object_transform(self) -> None:
        owner = GameObject(50, 80)
        owner.rotation = 45
        owner.scale_x, owner.scale_y = 2, -3
        box = owner.add_component(Collider(width=20, height=10, offset_x=3, offset_y=4))
        start = handles(box)["size:1:0"]
        values = drag_values(box, "size:1:0", start, _transform_point(owner, (23, 4)), geometry_values(box))
        self.assertAlmostEqual(number(values, "width"), 30)
        self.assertAlmostEqual(number(values, "offset_x"), 8)
        self.assertEqual(values["height"], 10)
        self.assertEqual(tuple(owner.world_position), (50, 80))

    def test_circle_resizes_using_actual_world_radius_not_nonuniform_axes(self) -> None:
        owner = GameObject()
        owner.scale_x, owner.scale_y = 2, 4
        circle = owner.add_component(Collider(shape="circle", radius=10))
        self.assertEqual(handles(circle)["radius"], (40, 0))
        values = drag_values(circle, "radius", (40, 0), (80, 0), geometry_values(circle))
        self.assertEqual(values["radius"], 20)

    def test_ellipse_axis_resize_and_polygon_vertex_offset(self) -> None:
        owner = GameObject()
        ellipse = owner.add_component(Collider(shape="ellipse", radius_x=20, radius_y=10))
        values = drag_values(ellipse, "size:0:1", (0, 10), (0, 30), geometry_values(ellipse))
        self.assertEqual((values["radius_x"], values["radius_y"]), (20, 30))
        polygon = owner.add_component(Collider(
            shape="polygon",
            points=((-10, -10), (10, -10), (10, 10), (-10, 10)),
            offset_x=2,
        ))
        values = drag_values(polygon, "point:1", (12, -10), (22, -10), geometry_values(polygon))
        points = values["points"]
        assert isinstance(points, list)
        self.assertEqual(points[1], [20, -10])
        validate_geometry("polygon", values)

    def test_state_edits_are_atomic_undoable_and_do_not_resize_renderer(self) -> None:
        state = EditorState()
        state.add_object("rectangle")
        record, component, _ = state._selected_component(1)
        components = record["components"]
        assert isinstance(components, list)
        renderer = deepcopy(components[0])
        before = deepcopy(component["properties"])
        state.set_collider_geometry(1, {"width": 100.0, "offset_x": 10.0})
        self.assertEqual(components[0], renderer)
        self.assertTrue(state.undo())
        self.assertEqual(state._selected_component(1)[1]["properties"], before)
        self.assertTrue(state.redo())
        properties = state._selected_component(1)[1]["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["width"], 100)
        unchanged = deepcopy(state.document)
        with self.assertRaisesRegex(ValueError, "positive"):
            state.set_collider_geometry(1, {"width": -2})
        self.assertEqual(state.document, unchanged)


try:
    from imgui_bundle import imgui
    from g_editor.application import ImGuiEditor
except ImportError:
    imgui = None
    ImGuiEditor = None


@unittest.skipIf(ImGuiEditor is None, "ImGui editor dependency is not installed")
class ColliderEditViewportTests(unittest.TestCase):
    def setUp(self) -> None:
        assert ImGuiEditor is not None
        self.editor = ImGuiEditor(visible=False)
        self.editor.window.switch_to()
        self.editor.state.add_object("empty")
        self.editor.state.add_component("g.collider")
        self.editor.state.set_collider_shape(0, "polygon")
        self.editor.preview.synchronize()
        self.editor._activate_component_tool("collider", 0)
        self.editor._shortcut_context = "viewport"
        self.editor.state.set_snap_enabled(False)

    def tearDown(self) -> None:
        self.editor.state.dirty = False
        self.editor._close()

    def active(self) -> tuple[int, Collider]:
        result = self.editor._active_collider()
        assert result is not None
        return result

    def properties(self) -> dict[str, JsonValue]:
        value = self.editor.state._selected_component(0)[1]["properties"]
        assert isinstance(value, dict)
        return value

    def points(self) -> list[JsonValue]:
        value = self.properties()["points"]
        assert isinstance(value, list)
        return value

    def test_drag_previews_then_commits_one_history_step(self) -> None:
        editor = self.editor
        _, collider = self.active()
        original = deepcopy(editor.state.document)
        history = editor.state._history_cursor
        start = handles(collider)["center"]
        editor._begin_collider_drag("center", start)
        for x in (5, 10, 20):
            editor._update_collider_drag((start[0] + x, start[1] + 8), down=True)
        self.assertEqual(editor.state.document, original)
        editor._update_collider_drag((0, 0), down=False)
        self.assertEqual(editor.state._history_cursor, history + 1)
        self.assertEqual(self.properties()["offset_x"], 20)
        editor.state.undo()
        self.assertEqual(editor.state.document, original)
        editor.state.redo()
        self.assertEqual(self.properties()["offset_y"], 8)

    def test_larger_handles_and_hover_cursor_for_every_shape(self) -> None:
        assert imgui is not None
        editor = self.editor
        for shape, handle in (
            ("box", "size:1:1"), ("circle", "radius"),
            ("ellipse", "size:1:0"), ("polygon", "point:1"),
        ):
            with self.subTest(shape=shape):
                editor.state.set_collider_shape(0, shape)
                editor.preview.synchronize()
                _, collider = self.active()
                x, y = editor.preview.world_to_screen(*handles(collider)[handle])
                hit = editor._collider_hit(collider, x + 15, y)
                assert hit is not None
                self.assertEqual(hit[0], handle)
                self.assertIsNone(editor._collider_hit(collider, x + 17, y))
                width, height = editor.render_target.width, editor.render_target.height
                minimum, maximum = imgui.ImVec2(0, 0), imgui.ImVec2(width, height)
                editor._viewport_hovered = True
                with (
                    patch("g_editor.application.imgui.get_mouse_pos", return_value=imgui.ImVec2(x + 15, height - y)),
                    patch("g_editor.application.imgui.is_mouse_clicked", return_value=False),
                    patch("g_editor.application.imgui.is_mouse_down", return_value=False),
                    patch("g_editor.application.imgui.set_mouse_cursor") as cursor,
                ):
                    editor._handle_viewport_input(minimum, maximum)
                    cursor.assert_called_with(imgui.MouseCursor_.hand)
                with patch("g_editor.application.imgui.set_mouse_cursor"):
                    self.assertTrue(editor._handle_collider_click(x + 15, y))
                assert editor._collider_drag is not None
                self.assertEqual(editor._collider_drag.handle, handle)
                with (
                    patch("g_editor.application.imgui.get_mouse_pos", return_value=imgui.ImVec2(-100, -100)),
                    patch("g_editor.application.imgui.is_mouse_down", return_value=True),
                    patch("g_editor.application.imgui.set_mouse_cursor") as cursor,
                ):
                    editor._handle_viewport_input(minimum, maximum)
                    cursor.assert_called_with(imgui.MouseCursor_.hand)
                editor._cancel_collider_drag()

    def test_handles_draw_larger_dots_and_selected_vertex(self) -> None:
        assert imgui is not None
        editor = self.editor
        self.active()
        editor._collider_point = 0
        from unittest.mock import Mock
        draw_list = Mock()
        with (
            patch("g_editor.application.imgui.get_window_draw_list", return_value=draw_list),
            patch("g_editor.application._color", return_value=0),
        ):
            editor._draw_collider_overlay(imgui.ImVec2(0, 0), imgui.ImVec2(800, 600))
        radii = [call.args[1] for call in draw_list.add_circle_filled.call_args_list]
        self.assertEqual(radii.count(7), 1)
        self.assertEqual(radii.count(6), 4)

    def test_escape_cancels_without_saving_preview(self) -> None:
        editor = self.editor
        _, collider = self.active()
        original = deepcopy(editor.state.document)
        start = handles(collider)["center"]
        editor._begin_collider_drag("center", start)
        editor._update_collider_drag((start[0] + 25, start[1]), down=True)
        editor.on_key_press(key.ESCAPE, 0)
        self.assertIsNone(editor._collider_drag)
        self.assertEqual(editor.state.document, original)
        self.assertEqual(self.active()[1].offset_x, 0)

    def test_collider_drags_ignore_enabled_grid_snap_for_all_shapes(self) -> None:
        editor = self.editor
        editor.state.set_grid_size(64)
        editor.state.set_snap_enabled(True)
        for shape, handle in (
            ("box", "size:1:1"), ("circle", "radius"),
            ("ellipse", "size:1:0"), ("polygon", "point:1"),
        ):
            with self.subTest(shape=shape):
                editor.state.set_collider_shape(0, shape)
                editor.preview.synchronize()
                _, collider = self.active()
                original = geometry_values(collider)
                start = handles(collider)[handle]
                current = (start[0] + 3.25, start[1] + 1.75)
                expected = drag_values(collider, handle, start, current, original)
                editor._begin_collider_drag(handle, start)
                editor._update_collider_drag(current, down=True)
                self.assertEqual(geometry_values(collider), expected)
                editor._update_collider_drag(current, down=False)
                self.assertTrue(editor.state.snap_enabled)
                _, collider = self.active()
                start = handles(collider)["center"]
                ox, oy = collider.offset_x, collider.offset_y
                editor._begin_collider_drag("center", start)
                editor._update_collider_drag((start[0] + 2.5, start[1] + 4.75), down=True)
                self.assertAlmostEqual(collider.offset_x, ox + 2.5)
                self.assertAlmostEqual(collider.offset_y, oy + 4.75)
                editor._cancel_collider_drag()

    def test_shift_click_inserts_point_then_command_delete_removes_it(self) -> None:
        assert imgui is not None
        editor = self.editor
        _, collider = self.active()
        vertices = list(handles(collider).values())[1:]
        a, b = vertices[:2]
        x, y = editor.preview.world_to_screen((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        imgui.get_io().key_shift = True
        try:
            self.assertTrue(editor._handle_collider_click(x, y))
        finally:
            imgui.get_io().key_shift = False
        self.assertEqual(len(self.points()), 5)
        editor.preview.synchronize()
        editor.on_key_press(key.BACKSPACE, key.MOD_COMMAND)
        self.assertEqual(len(self.points()), 4)
        editor.state.undo()
        self.assertEqual(len(self.points()), 5)

    def test_invalid_vertex_move_keeps_last_valid_shape_and_reports_error(self) -> None:
        editor = self.editor
        _, collider = self.active()
        self.assertEqual(collider.shape, "polygon")
        start = handles(collider)["point:0"]
        original = collider.points
        editor._begin_collider_drag("point:0", start)
        editor._update_collider_drag(handles(collider)["point:2"], down=True)
        self.assertEqual(collider.points, original)
        self.assertIsNotNone(editor.state.error)
        editor._update_collider_drag((0, 0), down=False)
        self.assertIsNone(editor._collider_drag)

    def test_triangle_point_cannot_be_deleted_and_plain_delete_never_deletes_object(self) -> None:
        editor = self.editor
        editor.state.set_collider_geometry(0, {"points": [[0, 0], [10, 0], [0, 10]]})
        editor.preview.synchronize()
        editor._active_collider()
        editor._collider_point = 0
        editor.on_key_press(key.DELETE, key.MOD_COMMAND)
        assert editor.state.error is not None
        self.assertIn("three", editor.state.error)
        identifier = editor.state.selected_id
        editor.on_key_press(key.DELETE, 0)
        self.assertEqual(editor.state.selected_id, identifier)
        self.assertEqual(len(editor.state.records), 1)

    def test_selection_change_cancels_drag_and_multi_collider_selection_is_explicit(self) -> None:
        editor = self.editor
        editor.state.add_component("g.collider")
        editor.state.set_collider_shape(1, "ellipse")
        editor.preview.synchronize()
        self.assertEqual(len(editor._editable_colliders()), 2)
        editor._collider_component_index = 1
        _, collider = self.active()
        self.assertIsInstance(collider, Collider)
        start = handles(collider)["center"]
        editor._begin_collider_drag("center", start)
        editor._update_collider_drag((start[0] + 5, start[1]), down=True)
        editor.state.select(None)
        editor._update_collider_drag(start, down=False)
        self.assertIsNone(editor._collider_drag)

    def test_real_viewport_input_routes_click_drag_and_release_for_all_shapes(self) -> None:
        assert imgui is not None
        editor = self.editor
        for shape, handle in (
            ("box", "size:1:0"),
            ("circle", "radius"),
            ("ellipse", "size:1:0"),
            ("polygon", "point:1"),
        ):
            with self.subTest(shape=shape):
                editor.state.remove_component(0)
                editor.state.add_component("g.collider")
                editor.state.set_collider_shape(0, shape)
                editor.preview.synchronize()
                editor._activate_component_tool("collider", 0)
                editor._collider_point = None
                _, collider = self.active()
                initial = geometry_values(collider)
                world = handles(collider)[handle]
                sx, sy = editor.preview.world_to_screen(*world)
                width, height = editor.render_target.width, editor.render_target.height
                minimum = imgui.ImVec2(10, 20)
                maximum = imgui.ImVec2(10 + width, 20 + height)
                editor._viewport_hovered = True
                left_button = int(imgui.MouseButton_.left)
                with (
                    patch("g_editor.application.imgui.get_mouse_pos", return_value=imgui.ImVec2(10 + sx, 20 + height - sy)),
                    patch("g_editor.application.imgui.is_mouse_clicked", side_effect=lambda button: button == left_button),
                    patch("g_editor.application.imgui.is_mouse_down", return_value=True),
                ):
                    editor._handle_viewport_input(minimum, maximum)
                self.assertIsNotNone(editor._collider_drag)
                sx, sy = editor.preview.world_to_screen(world[0] + 10, world[1])
                history = editor.state._history_cursor
                with (
                    patch("g_editor.application.imgui.get_mouse_pos", return_value=imgui.ImVec2(10 + sx, 20 + height - sy)),
                    patch("g_editor.application.imgui.is_mouse_down", return_value=True),
                ):
                    editor._handle_viewport_input(minimum, maximum)
                with (
                    patch("g_editor.application.imgui.get_mouse_pos", return_value=imgui.ImVec2(10 + sx, 20 + height - sy)),
                    patch("g_editor.application.imgui.is_mouse_down", return_value=False),
                ):
                    editor._handle_viewport_input(minimum, maximum)
                self.assertIsNone(editor._collider_drag)
                self.assertEqual(editor.state._history_cursor, history + 1)
                editor.preview.synchronize()
                self.assertNotEqual(geometry_values(self.active()[1]), initial)

    def test_collider_overlay_draws_shape_outline_and_selected_vertex_in_gold(self) -> None:
        assert imgui is not None
        editor = self.editor
        self.active()
        editor._collider_point = 1
        with patch("g_editor.application.imgui.get_window_draw_list") as drawing:
            editor._draw_collider_overlay(imgui.ImVec2(0, 0), imgui.ImVec2(800, 600))
        self.assertEqual(drawing.return_value.add_line.call_count, 4)
        self.assertEqual(drawing.return_value.add_circle_filled.call_count, 5)

    def test_invalid_polygon_state_edit_is_atomic_and_editor_ellipse_supports_area(self) -> None:
        editor = self.editor
        before = deepcopy(editor.state.document)
        with self.assertRaisesRegex(ValueError, "convex"):
            editor.state.set_collider_geometry(
                0, {"points": [[-10, -10], [10, -10], [0, 0], [10, 10], [-10, 10]]},
            )
        self.assertEqual(editor.state.document, before)
        editor.state.remove_component(0)
        editor.state.add_component("g.collider")
        editor.state.set_collider_shape(0, "ellipse")
        editor.state.add_component("g.area")
        editor.preview.synchronize()
        _, collider = self.active()
        self.assertEqual(collider.shape, "ellipse")
        self.assertTrue(collider.sensor)

    def test_on_draw_with_edit_collider_mode_has_no_imgui_errors(self) -> None:
        self.active()
        self.editor.on_draw()
        self.assertIsNone(self.editor.state.error)

    def test_inserting_on_sloped_edges_keeps_convex_polygon(self) -> None:
        editor = self.editor
        editor.state.set_collider_geometry(
            0, {"points": [[-17.3, -20.7], [30.8, -2.3], [-5.7, 45.2]]},
        )
        editor.preview.synchronize()
        self.active()
        editor._insert_collider_point(0, 0.371)
        self.assertEqual(len(self.points()), 4)

    def test_polygon_vertex_at_origin_is_selectable_over_center_handle(self) -> None:
        editor = self.editor
        editor.state.set_collider_geometry(0, {"points": [[0, 0], [10, 0], [0, 10]]})
        editor.preview.synchronize()
        _, collider = self.active()
        x, y = editor.preview.world_to_screen(*handles(collider)["point:0"])
        self.assertTrue(editor._handle_collider_click(x, y))
        self.assertEqual(editor._collider_point, 0)
        assert editor._collider_drag is not None
        self.assertEqual(editor._collider_drag.handle, "point:0")
        editor._cancel_collider_drag()


if __name__ == "__main__":
    unittest.main()
