import unittest

from g import Game, GameObject, Node, create_rect


class GameObjectChildrenTests(unittest.TestCase):
    def test_required_layer_returns_current_object_layer_or_raises(self) -> None:
        obj = GameObject()
        obj.name = "Player"
        with self.assertRaisesRegex(LookupError, "Player.*object layer"):
            obj.require_layer()
        child = GameObject()
        obj.add_child(child)
        game = Game(320, 180)
        game.add(obj)
        self.assertIs(obj.require_layer(), game.scenes.current.default_layer)
        self.assertIs(child.require_layer(), obj.require_layer())
        foreground = game.scenes.current.layers.add("Foreground")
        game.scenes.current.move_to_layer(obj, foreground)
        self.assertIs(obj.require_layer(), foreground)
        self.assertIs(child.require_layer(), foreground)

    def test_required_parent_and_scene_and_layer_root_lookup(self) -> None:
        child = GameObject()
        child.name = "Child"
        with self.assertRaisesRegex(LookupError, "Child.*scene"):
            child.require_scene()
        with self.assertRaisesRegex(LookupError, "Child.*parent"):
            child.require_parent()
        parent = GameObject()
        parent.add_child(child)
        self.assertIs(child.require_parent(), parent)
        game = Game(320, 180)
        game.add(parent)
        scene = game.scenes.current
        self.assertIs(child.require_scene(), scene)
        self.assertIs(parent.require_scene(), scene)
        with self.assertRaisesRegex(LookupError, "GameObject parent"):
            parent.require_parent()
        self.assertIs(scene.root.require_child("Child", recursive=True), child)
        parent.remove_child(child)
        with self.assertRaises(LookupError):
            child.require_parent()
        self.assertIs(child.parent, scene.root)
        self.assertIn(child, scene.objects)
        self.assertIs(child.scene, scene)
        self.assertFalse(child.is_deleted)

    def test_remove_child_outside_scene_only_detaches_hierarchy(self) -> None:
        parent = GameObject()
        child = GameObject()
        parent.add_child(child)

        result = parent.remove_child(child)

        self.assertIs(result, child)
        self.assertIsNone(child.parent)
        self.assertIsNone(child.scene)
        self.assertFalse(child.is_deleted)

    def test_remove_child_in_scene_preserves_world_transform(self) -> None:
        game = Game(320, 180)
        parent = game.add(GameObject())
        parent.position = (30, 40)
        parent.rotation = 25
        parent.scale = (2, 3)
        child = GameObject()
        parent.add_child(child)
        child.position = (5, -7)
        child.rotation = 15
        child.scale = (4, 0.5)
        game.scenes.current._prepare_subtree(child, parent.require_layer())
        expected_position = child.world_position
        expected_rotation = child.world_rotation
        expected_scale = child.world_scale

        parent.remove_child(child)

        self.assertAlmostEqual(child.world_position.x, expected_position.x)
        self.assertAlmostEqual(child.world_position.y, expected_position.y)
        self.assertAlmostEqual(child.world_rotation, expected_rotation)
        self.assertAlmostEqual(child.world_scale[0], expected_scale[0])
        self.assertAlmostEqual(child.world_scale[1], expected_scale[1])
        self.assertIs(child.parent, game.scenes.current.root)

    def test_direct_lookup_returns_game_objects_only(self) -> None:
        player = GameObject()
        player.name = "Player"
        group = Node()
        visual = create_rect(0, 0, 16, 16, (255, 255, 255))
        self.assertIs(type(visual), GameObject)
        visual.name = "Visual"
        player.add_child(group)
        player.add_child(visual)
        self.assertEqual(player.children, (group, visual))
        self.assertEqual(player.get_children(), (visual,))
        self.assertIs(player.get_child("Visual"), visual)
        self.assertIs(player.require_child("Visual"), visual)
        player.require_child("Visual").rotation = 45
        self.assertEqual(visual.rotation, 45)
        self.assertEqual(player.rotation, 0)

    def test_recursive_lookup_traverses_plain_nodes_and_excludes_self(self) -> None:
        player = GameObject()
        player.name = "Player"
        group = Node()
        visual = GameObject()
        visual.name = "Visual"
        nested = GameObject()
        nested.name = "Nested"
        sibling = GameObject()
        sibling.name = "Visual"
        player.add_child(group)
        group.add_child(visual)
        visual.add_child(nested)
        player.add_child(sibling)
        self.assertEqual(player.get_children(), (sibling,))
        self.assertEqual(player.get_children(recursive=True), (visual, nested, sibling))
        self.assertIs(player.get_child("Visual"), sibling)
        self.assertIs(player.require_child("Visual", recursive=True), visual)
        self.assertIsNone(player.get_child("Player", recursive=True))
        self.assertIsNone(player.get_child("Nested"))
        self.assertIs(player.get_child("Nested", recursive=True), nested)

    def test_missing_required_child_has_object_context(self) -> None:
        player = GameObject()
        player.name = "Player"
        self.assertIsNone(player.get_child("Missing"))
        with self.assertRaisesRegex(LookupError, "Player.*child 'Missing'"):
            player.require_child("Missing")
        with self.assertRaisesRegex(LookupError, "Player.*descendant 'Missing'"):
            player.require_child("Missing", recursive=True)

    def test_lookup_reflects_reparenting_and_renaming(self) -> None:
        first, second, visual = GameObject(), GameObject(), GameObject()
        visual.name = "Visual"
        first.add_child(visual)
        snapshot = first.get_children()
        second.add_child(visual)
        self.assertEqual(first.get_children(), ())
        self.assertEqual(snapshot, (visual,))
        self.assertIs(second.get_child("Visual"), visual)
        visual.name = "Sprite"
        self.assertIsNone(second.get_child("Visual"))
        self.assertIs(second.get_child("Sprite"), visual)
