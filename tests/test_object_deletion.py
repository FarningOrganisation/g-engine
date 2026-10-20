import unittest
from unittest.mock import Mock, patch

from g import Area, Component, Game, GameObject, Image, PhysicsBody, SpriteRenderer, create_area_box, create_sprite
from g.nodes import Node


class ObjectDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(320, 180)
        self.scene = self.game.scenes.current

    def test_delete_detaches_nested_subtree_and_releases_physics_and_drawables(self) -> None:
        parent = self.scene.add(GameObject())
        group = parent.add_child(Node())
        sprite = create_sprite(
            Image.solid(8, 8, (255, 0, 0)), 0, 0, collider=True
        )
        group.add_child(sprite)
        child = sprite.add_child(create_sprite(None, 0, 0))
        self.scene._prepare_subtree(parent, parent.require_layer())
        self.scene.physics.refresh()
        collider = sprite.collider
        assert collider is not None
        shape = collider._shape
        drawable = sprite.require_component(SpriteRenderer).drawable
        detach = Mock()
        component = sprite.add_component(Component())
        component.on_detach = detach
        with patch.object(drawable, "delete", wraps=drawable.delete) as deleted:
            sprite.delete()
            sprite.delete()
        deleted.assert_called_once()
        detach.assert_called_once()
        self.assertIn(parent, self.scene.objects)
        self.assertEqual(group.children, ())
        for node in (sprite, child):
            self.assertIsNone(node.parent)
            self.assertIsNone(node.scene)
            self.assertIsNone(node.layer)
            self.assertEqual(node.children, ())
            self.assertEqual(node.components, ())
        self.assertIsNone(component.owner)
        self.assertIsNone(collider._shape)
        self.assertNotIn(shape, self.scene.physics._space.shapes)
        self.assertIsNone(shape.space)

    def test_root_deletion_releases_dynamic_body(self) -> None:
        sprite = self.scene.add(
            create_sprite(
                Image.solid(8, 8, (255, 0, 0)),
                0,
                0,
                collider=True,
            )
        )
        body = sprite.add_component(PhysicsBody())
        self.scene.physics.refresh()
        self.assertIn(body._body, self.scene.physics._space.bodies)
        sprite.delete()
        self.assertNotIn(body._body, self.scene.physics._space.bodies)
        self.assertIsNone(body.owner)

    def test_delete_from_area_callback_stops_remaining_handlers_and_preserves_other_objects(self) -> None:
        coin = self.scene.add(create_area_box(0, 0, 20, 20))
        player = self.scene.add(create_sprite(None, 0, 0, collider=True))
        area = coin.require_component(Area)
        second = Mock()
        area.on_enter(lambda other: coin.delete())
        area.on_enter(second)
        self.scene._update(0)
        self.assertNotIn(coin, self.scene.objects)
        self.assertIn(player, self.scene.objects)
        second.assert_not_called()
        self.assertEqual(area._enter_handlers, [])
        self.assertEqual(area._overlapping, set())
        self.assertEqual(self.scene._overlaps, set())
        self.assertEqual(self.scene.physics.overlap_pairs, ())

    def test_delete_during_component_update_skips_remaining_components_and_children(self) -> None:
        parent = self.scene.add(GameObject())
        first = parent.add_component(Component())
        second = parent.add_component(Component())
        child = parent.add_child(GameObject())
        child_component = child.add_component(Component())
        self.scene._prepare_subtree(parent, parent.require_layer())
        first.update = lambda delta: parent.delete()
        second.update = Mock()
        child_component.update = Mock()
        self.scene._update(0)
        second.update.assert_not_called()
        child_component.update.assert_not_called()
        self.assertEqual(self.scene.objects, ())

    def test_scene_and_layer_remove_accept_nested_objects(self) -> None:
        for use_layer in (False, True):
            with self.subTest(layer=use_layer):
                parent = self.scene.add(GameObject())
                child = parent.add_child(GameObject())
                self.scene._prepare_subtree(parent, parent.require_layer())
                if use_layer:
                    parent.require_layer().remove(child)
                else:
                    self.scene.remove(child)
                self.assertEqual(parent.children, ())
                self.assertIsNone(child.scene)

    def test_unattached_deletion_releases_subtree_and_reports_cleanup_errors(self) -> None:
        parent = GameObject()
        child = parent.add_child(create_sprite(None, 0, 0))
        component = child.add_component(Component())
        component.on_detach = Mock(side_effect=RuntimeError("cleanup failed"))
        renderer = child.require_component(SpriteRenderer)
        with patch.object(renderer.drawable, "delete", wraps=renderer.drawable.delete) as deleted:
            with self.assertRaises(ExceptionGroup):
                parent.delete()
        deleted.assert_called_once()
        self.assertEqual(parent.children, ())
        self.assertEqual(child.components, ())
        self.assertIsNone(component.owner)

    def test_collision_callback_deletion_does_not_retain_stale_pairs(self) -> None:
        first = self.scene.add(
            create_sprite(
                Image.solid(10, 10, (255, 0, 0)),
                0,
                0,
                collider=True,
            )
        )
        second = self.scene.add(create_sprite(None, 0, 0, collider=True))
        second.collider.width = second.collider.height = 10
        first.on_collision(lambda other: first.delete())
        self.scene._update(0)
        self.assertNotIn(first, self.scene.objects)
        self.assertEqual(self.scene._collisions, set())
        self.assertEqual(self.scene.physics.collision_pairs, ())
