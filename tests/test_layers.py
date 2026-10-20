import unittest

from g import (
    Button,
    CanvasLayout,
    Game,
    GameObject,
    RectRenderer,
    UIView,
    create_rect,
)


class TrackingObject(GameObject):
    def __init__(self) -> None:
        super().__init__()
        self.updates = 0

    def update(self, delta_time: float) -> None:
        del delta_time
        self.updates += 1


class SceneLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(320, 180)
        self.scene = self.game.scenes.current

    def test_scene_and_layer_child_lookup_is_ordered_and_scoped(self) -> None:
        first = self.scene.add(GameObject())
        first.name = "Player"
        nested = GameObject()
        nested.name = "Visual"
        first.add_child(nested)
        foreground = self.scene.layers.add("Foreground")
        second = self.scene.add(GameObject(), layer=foreground)
        second.name = "PlayerFellArea"
        duplicate = GameObject()
        duplicate.name = "Player"
        second.add_child(duplicate)
        self.scene.layers.add_tile("Tiles")
        self.assertEqual(self.scene.get_children(), (first, second))
        self.assertEqual(self.scene.get_children(recursive=True), (first, nested, second, duplicate))
        self.assertIs(self.scene.require_child("PlayerFellArea"), second)
        self.assertIs(self.scene.require_child("Player", recursive=True), first)
        self.assertIsNone(self.scene.get_child("Visual"))
        self.assertIs(self.scene.require_child("Visual", recursive=True), nested)
        self.assertEqual(foreground.get_children(), (second,))
        self.assertIs(foreground.require_child("Player", recursive=True), duplicate)
        self.assertIsNone(foreground.get_child("Visual", recursive=True))
        with self.assertRaisesRegex(LookupError, "Foreground.*child 'Missing'"):
            foreground.require_child("Missing")
        with self.assertRaisesRegex(LookupError, "Scene.*descendant 'Missing'"):
            self.scene.require_child("Missing", recursive=True)
        self.scene.layers.move(foreground, 0)
        self.assertIs(self.scene.require_child("Player", recursive=True), duplicate)
        second.name = "FallArea"
        self.assertIsNone(self.scene.get_child("PlayerFellArea"))
        self.assertIs(self.scene.require_child("FallArea"), second)

    def test_add_uses_default_layer_without_extra_arguments(self) -> None:
        game_object = self.scene.add(GameObject())

        self.assertIs(game_object.layer, self.scene.default_layer)
        self.assertIs(game_object.parent, self.scene.root)
        self.assertIn(game_object, self.scene.objects)

    def test_named_layers_have_independent_roots_and_batches(self) -> None:
        foreground = self.scene.layers.add("Foreground")
        default_object = self.scene.add(
            create_rect(10, 10, (255, 255, 255), 5, 5)
        )
        foreground_object = self.scene.add(
            create_rect(20, 20, (255, 255, 255), 5, 5),
            layer="Foreground",
            z_index=7,
        )

        self.assertIs(default_object.parent, self.scene.default_layer.root)
        self.assertIs(foreground_object.parent, foreground.root)
        self.assertIs(foreground_object.layer, foreground)
        self.assertEqual(foreground_object.z_index, 7)
        self.assertIs(
            default_object.require_component(RectRenderer).drawable.batch,
            self.scene.default_layer._batch,
        )
        self.assertIs(
            foreground_object.require_component(RectRenderer).drawable.batch,
            foreground._batch,
        )
        self.assertIsNot(
            self.scene.default_layer._batch,
            foreground._batch,
        )

    def test_integer_factory_layer_remains_a_z_index_alias(self) -> None:
        game_object = self.scene.add_rect(
            10,
            10,
            (255, 255, 255),
            5,
            5,
            layer=4,
        )

        self.assertIs(game_object.layer, self.scene.default_layer)
        self.assertEqual(game_object.z_index, 4)

    def test_moving_parent_moves_its_entire_subtree(self) -> None:
        foreground = self.scene.layers.add("Foreground")
        parent = self.scene.add(GameObject())
        child = GameObject()
        parent.add_child(child)

        self.scene.move_to_layer(parent, foreground)

        self.assertIs(parent.layer, foreground)
        self.assertIs(child.layer, foreground)
        self.assertIs(parent.parent, foreground.root)
        self.assertIs(child.parent, parent)

    def test_cross_layer_parenting_is_rejected(self) -> None:
        foreground = self.scene.layers.add("Foreground")
        parent = self.scene.add(GameObject())
        child = self.scene.add(GameObject(), layer=foreground)

        with self.assertRaisesRegex(ValueError, "same scene layer"):
            parent.add_child(child)

    def test_disabled_layer_does_not_update_its_objects(self) -> None:
        background = self.scene.layers.add("Background", enabled=False)
        default_object = self.scene.add(TrackingObject())
        background_object = self.scene.add(
            TrackingObject(),
            layer=background,
        )

        self.game._update(0.1)

        self.assertEqual(default_object.updates, 1)
        self.assertEqual(background_object.updates, 0)

    def test_named_ui_view_uses_gui_rendering_and_input(self) -> None:
        clicks: list[str] = []
        view = self.scene.gui.register(
            "Pause",
            UIView(CanvasLayout(), name="Pause"),
        )
        assert isinstance(view.root, CanvasLayout)
        button = view.root.add_at(
            Button("Start", lambda: clicks.append("start")),
            20,
            30,
        )
        self.scene.gui.replace("Pause")

        self.scene.gui._draw()
        handled_press = self.scene.gui.on_mouse_press(30, 40, 1, 0)
        handled_release = self.scene.gui.on_mouse_release(30, 40, 1, 0)

        self.assertIn(view, self.scene.gui.views)
        self.assertEqual((button.x, button.y), (20, 30))
        self.assertTrue(handled_press)
        self.assertTrue(handled_release)
        self.assertEqual(clicks, ["start"])


if __name__ == "__main__":
    unittest.main()
