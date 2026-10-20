import unittest
from unittest.mock import Mock, patch

from g import (
    Area,
    Collider,
    Component,
    Game,
    GameObject,
    Image,
    NetworkedGameObject,
    ParticleEffect,
    PhysicsBody,
    SpriteRenderer,
    TextRenderer,
    TileRenderer,
    TrailRenderer,
    create_area_box,
    create_sprite,
    create_text,
)
from g.nodes import Node
from g.particles import particle_preset


class _LifecycleComponent(Component):
    def __init__(self) -> None:
        super().__init__()
        self.ready_count = 0
        self.scene_detach_count = 0

    def on_ready(self) -> None:
        self.ready_count += 1

    def on_scene_detach(self) -> None:
        self.scene_detach_count += 1


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
        child = create_sprite(None, 0, 0)
        sprite.add_child(child)
        self.scene._prepare_subtree(parent, parent.require_layer())
        self.scene.physics.refresh()
        collider = sprite.collider
        assert collider is not None
        shape = collider._shape
        assert shape is not None
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
            self.assertTrue(node.is_deleted)
            self.assertIsNone(node.parent)
            self.assertIsNone(node.scene)
            self.assertIsNone(node.layer)
            self.assertEqual(node.children, ())
            self.assertEqual(node.components, ())
        self.assertIsNone(component.owner)
        self.assertIsNone(collider._shape)
        self.assertNotIn(shape, self.scene.physics._space.shapes)
        self.assertIsNone(shape.space)

        with self.assertRaisesRegex(RuntimeError, "deleted game object"):
            sprite.add_component(Component())
        with self.assertRaisesRegex(ValueError, "deleted game object"):
            self.scene.add(sprite)

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
        child = GameObject()
        parent.add_child(child)
        child_component = child.add_component(Component())
        self.scene._prepare_subtree(parent, parent.require_layer())
        first.update = lambda delta_time: parent.delete()
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
                child = GameObject()
                parent.add_child(child)
                self.scene._prepare_subtree(parent, parent.require_layer())
                if use_layer:
                    parent.require_layer().remove(child)
                else:
                    self.scene.remove(child)
                self.assertEqual(parent.children, ())
                self.assertIsNone(child.scene)
                self.assertTrue(child.is_deleted)

    def test_detach_preserves_subtree_for_reattachment(self) -> None:
        parent = self.scene.add(GameObject())
        sprite = create_sprite(
            Image.solid(8, 8, (255, 0, 0)),
            0,
            0,
            collider=True,
        )
        parent.add_child(sprite)
        self.scene._prepare_subtree(parent, parent.require_layer())
        self.scene.physics.refresh()
        renderer = sprite.require_component(SpriteRenderer)
        collider = sprite.require_component(Collider)
        drawable = renderer.drawable
        shape = collider._shape
        assert shape is not None
        self.assertIsNotNone(drawable.batch)
        self.assertIn(shape, self.scene.physics._space.shapes)

        detached = self.game.detach(parent)

        self.assertIs(detached, parent)
        self.assertIsNone(parent.scene)
        self.assertIsNone(parent.layer)
        self.assertIs(sprite.parent, parent)
        self.assertIsNone(sprite.scene)
        self.assertFalse(parent.is_deleted)
        self.assertFalse(sprite.is_deleted)
        self.assertIs(sprite.require_component(SpriteRenderer), renderer)
        self.assertIsNone(drawable.batch)
        self.assertNotIn(shape, self.scene.physics._space.shapes)

        self.game.add(parent)

        self.assertIs(parent.scene, self.scene)
        self.assertIs(sprite.scene, self.scene)
        self.assertIsNotNone(drawable.batch)
        self.assertIn(collider._shape, self.scene.physics._space.shapes)

    def test_detach_runs_scene_hook_and_makes_components_ready_again(self) -> None:
        owner = GameObject()
        component = owner.add_component(_LifecycleComponent())
        self.scene.add(owner)
        self.assertEqual(component.ready_count, 1)

        self.scene.detach(owner)

        self.assertEqual(component.scene_detach_count, 1)
        self.scene.add(owner)
        self.assertEqual(component.ready_count, 2)

    def test_network_component_unregisters_for_delete_and_detach(self) -> None:
        for delete in (False, True):
            with self.subTest(delete=delete):
                owner = GameObject()
                marker = owner.add_component(NetworkedGameObject())
                self.scene.add(owner)
                identifier = marker.network_id
                assert identifier is not None
                self.assertIs(self.game.network._objects[identifier], marker)

                if delete:
                    self.scene.delete(owner)
                    self.assertTrue(owner.is_deleted)
                else:
                    self.scene.detach(owner)
                    self.assertFalse(owner.is_deleted)
                self.assertNotIn(identifier, self.game.network._objects)
                self.assertIsNone(marker.network_id)

                if not delete:
                    self.scene.add(owner)
                    self.assertEqual(marker.network_id, identifier)
                    self.assertIs(self.game.network._objects[identifier], marker)
                    self.scene.delete(owner)

    def test_detach_releases_custom_renderer_batches_for_reattachment(self) -> None:
        owner = GameObject()
        trail = owner.add_component(TrailRenderer())
        particles = owner.add_component(
            ParticleEffect(particle_preset("sparks"))
        )
        tiles = owner.add_component(TileRenderer())
        self.scene.add(owner)
        layer_batch = owner.require_layer()._batch
        self.assertIsNotNone(trail._vertex_list)
        self.assertTrue(particles._vertex_lists)
        self.assertIs(tiles.drawable.batch, layer_batch)

        self.scene.detach(owner)

        self.assertIsNone(trail._vertex_list)
        self.assertEqual(particles._vertex_lists, [])
        self.assertIsNone(tiles.drawable.batch)
        self.scene.add(owner)
        self.assertIsNotNone(trail._vertex_list)
        self.assertTrue(particles._vertex_lists)
        self.assertIs(tiles.drawable.batch, layer_batch)

    def test_detach_removes_text_from_the_scene_batch(self) -> None:
        text = self.scene.add(create_text("Reusable", 0, 0))
        renderer = text.require_component(TextRenderer)
        layer_batch = text.require_layer()._batch
        self.assertIs(renderer.drawable.batch, layer_batch)

        self.scene.detach(text)

        self.assertIsNot(renderer.drawable.batch, layer_batch)
        self.scene.add(text)
        self.assertIs(renderer.drawable.batch, layer_batch)

    def test_unattached_deletion_releases_subtree_and_reports_cleanup_errors(self) -> None:
        parent = GameObject()
        child = create_sprite(None, 0, 0)
        parent.add_child(child)
        component = child.add_component(Component())
        component.on_detach = Mock(side_effect=RuntimeError("cleanup failed"))
        renderer = child.require_component(SpriteRenderer)
        with patch.object(renderer.drawable, "delete", wraps=renderer.drawable.delete) as deleted:
            with self.assertRaises(ExceptionGroup):
                parent.delete()
        deleted.assert_called_once()
        self.assertTrue(parent.is_deleted)
        self.assertTrue(child.is_deleted)
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
        second_collider = second.collider
        assert second_collider is not None
        second_collider.width = second_collider.height = 10
        first.on_collision(lambda other: first.delete())
        self.scene._update(0)
        self.assertNotIn(first, self.scene.objects)
        self.assertEqual(self.scene._collisions, set())
        self.assertEqual(self.scene.physics.collision_pairs, ())
