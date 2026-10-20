from copy import deepcopy
import unittest

from g_editor.state import EditorState, layer_objects, new_object_record, position
from g import TileCell, TileFlags


class MixedSelectionStateTests(unittest.TestCase):
    def setUp(self):
        self.state = EditorState()
        self.objects = self.state.selected_layer
        self.tile_layer = self.state.add_tile_layer("Ground")
        self.tile_layer["cell_size"] = [16, 16]
        self.state.add_color_tileset()
        self.state._set_tile_cell(self.tile_layer, 0, 0, TileCell(0, TileFlags.FLIP_HORIZONTAL))
        self.state._set_tile_cell(self.tile_layer, 1, 0, TileCell(0, TileFlags.FLIP_DIAGONAL))
        self.object = new_object_record("rectangle", x=8, y=8)
        assert self.objects is not None
        layer_objects(self.objects).append(self.object)
        self.state.mark_changed("Seed mixed scene")
        self.state.select_layer(str(self.objects["id"]), toggle=True)

    def select(self):
        self.state.select_tile_rectangle((0, 0), (1, 0))
        self.state.select_many([str(self.object["id"])])

    def layer(self, identifier):
        return next(layer for layer in self.state.document.layers if layer["id"] == identifier)

    def test_layer_toggle_and_object_selection_preserve_mixed_mode(self):
        self.assertTrue(self.state.mixed_layer_selection)
        self.assertEqual(len(self.state.selected_layers), 2)
        self.assertEqual(self.state.tile_tool, "select")
        self.select()
        self.assertTrue(self.state.mixed_layer_selection)
        self.assertEqual(self.state.selected_layer_id, self.tile_layer["id"])
        self.state.select_layer(str(self.objects["id"]), toggle=True)
        self.assertFalse(self.state.mixed_layer_selection)
        self.assertEqual(len(self.state.selected_layers), 1)
        self.assertFalse(self.state.selected_tile_cells)

    def test_mixed_move_is_one_undo_step_and_preserves_flags_and_overlap(self):
        self.select()
        before = deepcopy(self.state.document)
        history_count = len(self.state._history)
        self.assertTrue(self.state.move_world_selection(1, 0))
        self.assertEqual(len(self.state._history), history_count + 1)
        self.assertIsNone(self.state.tile_at(0, 0))
        self.assertEqual(self.state.tile_at(1, 0), TileCell(0, TileFlags.FLIP_HORIZONTAL))
        self.assertEqual(self.state.tile_at(2, 0), TileCell(0, TileFlags.FLIP_DIAGONAL))
        self.assertEqual(position(self.object), (24, 8))
        self.assertTrue(self.state.undo())
        self.assertEqual(self.state.document, before)
        self.assertTrue(self.state.mixed_layer_selection)
        self.assertEqual(set(self.state.selected_tile_cells), {(0, 0), (1, 0)})
        self.assertTrue(self.state.redo())
        self.assertEqual(position(self.state.selected_records[0]), (24, 8))

    def test_mixed_copy_paste_preserves_layers_and_creates_new_object_ids(self):
        self.select()
        clipboard = self.state.copy_world_selection()
        assert clipboard is not None
        self.state.paste_world_selection(clipboard, -2, 3)
        self.assertEqual(self.state.tile_at(-2, 3), TileCell(0, TileFlags.FLIP_HORIZONTAL))
        self.assertEqual(self.state.tile_at(-1, 3), TileCell(0, TileFlags.FLIP_DIAGONAL))
        clone = self.state.selected_records[0]
        self.assertNotEqual(clone["id"], self.object["id"])
        self.assertEqual(position(clone), (-24, 56))
        self.assertEqual(len(layer_objects(self.objects)), 2)
        self.assertEqual(len(self.tile_layer["tilesets"]), 1)
        self.state.paste_world_selection(clipboard, 5, 5)
        self.assertEqual(len(self.tile_layer["tilesets"]), 1)
        self.assertTrue(self.state.undo())
        self.assertTrue(self.state.undo())
        self.assertIsNone(self.state.tile_at(-2, 3))
        self.assertEqual(len(layer_objects(self.layer(self.objects["id"]))), 1)

    def test_failed_paste_is_atomic_when_object_layer_is_not_selected(self):
        self.select()
        clipboard = self.state.copy_world_selection()
        assert clipboard is not None
        self.state.select_layer(str(self.tile_layer["id"]))
        before = deepcopy(self.state.document)
        revision = self.state.revision
        with self.assertRaisesRegex(ValueError, "Select the copied"):
            self.state.paste_world_selection(clipboard, 2, 2)
        self.assertEqual(before, self.state.document)
        self.assertEqual(revision, self.state.revision)

    def test_tile_only_paste_into_another_layer_remaps_conflicting_tile_ids(self):
        self.state.select_layer(str(self.tile_layer["id"]))
        self.state.select_tile_rectangle((0, 0), (1, 0))
        clipboard = self.state.copy_world_selection()
        assert clipboard is not None
        destination = self.state.add_tile_layer("Destination")
        destination["cell_size"] = [16, 16]
        self.state.add_color_tileset(color=[255, 0, 0, 255])
        self.state.paste_world_selection(clipboard, 2, 1)
        copied = self.state.tile_at(2, 1)
        assert copied is not None
        self.assertNotEqual(copied.tile_id, 0)
        self.assertEqual(copied.flags, TileFlags.FLIP_HORIZONTAL)
        self.assertEqual(len(destination["tilesets"]), 2)
        self.state.paste_world_selection(clipboard, 3, 2)
        self.assertEqual(len(destination["tilesets"]), 2)

    def test_multiple_tilemaps_respect_offsets_and_share_movement(self):
        other = self.state.add_tile_layer("Details")
        other["cell_size"] = [16, 16]
        other["offset"] = [16, 0]
        self.state.add_color_tileset()
        self.state._set_tile_cell(other, -1, 0, TileCell(0))
        self.state.select_layer(str(self.tile_layer["id"]), toggle=True)
        self.state.select_layer(str(self.objects["id"]), toggle=True)
        self.state.select_tile_rectangle((0, 0), (0, 0))
        self.assertEqual(set(self.state.selected_tile_groups[str(other["id"])]), {(-1, 0)})
        self.state.move_world_selection(2, -1)
        self.assertEqual(self.state._tile_at_layer(other, 1, -1), TileCell(0))

    def test_incompatible_grid_move_fails_without_changes(self):
        self.select()
        other = self.state.add_tile_layer("Different grid")
        other["cell_size"] = [8, 8]
        self.state.select_layer(str(self.tile_layer["id"]), toggle=True)
        before = deepcopy(self.state.document)
        with self.assertRaisesRegex(ValueError, "matching cell sizes"):
            self.state.move_world_selection(1, 1)
        self.assertEqual(self.state.document, before)

    def test_parent_and_child_selection_moves_and_pastes_subtree_once(self):
        child = new_object_record("empty", x=4, y=5)
        self.object["children"] = [child]
        self.object["scale"] = [2, 3]
        self.object["rotation"] = 90
        self.select()
        self.state.select_many([str(self.object["id"]), str(child["id"])])
        child_before = self.state.object_world_transform(child)
        self.state.move_world_selection(2, -1)
        child_after = self.state.object_world_transform(child)
        self.assertAlmostEqual(child_after[0] - child_before[0], 32)
        self.assertAlmostEqual(child_after[1] - child_before[1], -16)
        self.assertEqual(position(child), (4, 5))
        clipboard = self.state.copy_world_selection()
        assert clipboard is not None
        self.assertEqual(len(clipboard.objects), 1)
        self.state.paste_world_selection(clipboard, 1, 1)
        clone = self.state.selected_records[0]
        self.assertNotEqual(clone["children"][0]["id"], child["id"])

    def test_selected_child_moves_and_pastes_in_parent_local_space(self):
        child = new_object_record("empty", x=4, y=5)
        self.object["children"] = [child]
        self.object["scale"] = [2, 3]
        self.object["rotation"] = 90
        self.select()
        self.state.select_many([str(child["id"])])
        before = self.state.object_world_transform(child)
        clipboard = self.state.copy_world_selection()
        assert clipboard is not None
        self.state.paste_world_selection(clipboard, -1, 2)
        clone = self.state.selected_records[0]
        after = self.state.object_world_transform(clone)
        self.assertAlmostEqual(after[0] - before[0], -16)
        self.assertAlmostEqual(after[1] - before[1], 32)

    def test_mixed_delete_and_undo_restore_both_kinds(self):
        self.select()
        before = deepcopy(self.state.document)
        self.assertTrue(self.state.delete_selected())
        self.assertFalse(self.state.records)
        self.assertIsNone(self.state.tile_at(0, 0))
        self.assertTrue(self.state.undo())
        self.assertEqual(self.state.document, before)
