from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch

import pyglet

from g import Area, Collider, Camera, Component, Game, GameObject, Image, PhysicsBody, Scene, SpriteRenderer, create_area_box
from g.rendering import RenderInterpolation
from g.timing import FixedStepAccumulator


class RenderInterpolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(320, 180)
        self.scene = self.game.scenes.current
        self.interpolation = RenderInterpolation()

    def test_hierarchy_camera_and_drawables_blend_without_changing_simulation(self) -> None:
        parent = self.game.add(GameObject(10, 20))
        child = parent.add_child(GameObject(5, 0))
        renderer = child.add_component(SpriteRenderer(Image.solid(4, 4, (255, 0, 0))))
        self.scene._prepare_subtree(parent)
        self.interpolation.begin_step(self.game)
        parent.x = 30
        child.x = 15
        self.scene.camera.x += 40
        self.interpolation.end_step(self.game)
        position = parent.position
        camera_position = self.scene.camera.position
        with self.interpolation.apply(self.game, 0.5):
            self.scene._draw()
            self.assertEqual((parent.x, child.x), (20, 10))
            self.assertEqual(renderer.drawable.x, 30)
            self.assertEqual(self.scene.camera.x, 180)
        self.assertIs(parent.position, position)
        self.assertIs(self.scene.camera.position, camera_position)
        self.assertEqual((parent.x, child.x), (30, 15))
        self.assertEqual(self.scene.camera.x, 200)

    def test_interpolation_restores_transform_after_render_failure(self) -> None:
        obj = self.game.add(GameObject())
        self.interpolation.begin_step(self.game)
        obj.position = (20, 40)
        obj.rotation = 60
        obj.scale_x = 2
        self.scene.camera.zoom = 2
        self.interpolation.end_step(self.game)
        with self.assertRaisesRegex(RuntimeError, "render failed"):
            with self.interpolation.apply(self.game, 0.25):
                self.assertEqual(obj.position, (5, 10))
                self.assertEqual(obj.rotation, 15)
                self.assertEqual(self.scene.camera.zoom, 1.25)
                raise RuntimeError("render failed")
        self.assertEqual(obj.position, (20, 40))
        self.assertEqual(obj.rotation, 60)
        self.assertEqual(obj.scale_x, 2)
        self.assertEqual(self.scene.camera.zoom, 2)

    def test_teleports_new_nodes_and_reparenting_do_not_blend(self) -> None:
        obj = self.game.add(GameObject())
        body = obj.add_component(PhysicsBody())
        child = obj.add_child(GameObject(5, 0))
        other = self.game.add(GameObject(100, 0))
        self.interpolation.begin_step(self.game)
        body.teleport(200, 0)
        child.parent = other
        new = self.game.add(GameObject(80, 0))
        self.interpolation.end_step(self.game)
        with self.interpolation.apply(self.game, 0):
            self.assertEqual(obj.x, 200)
            self.assertEqual(child.world_position, (105, 0))
            self.assertEqual(new.x, 80)

    def test_out_of_band_edits_deletion_and_scene_switch_do_not_use_stale_history(self) -> None:
        obj = self.game.add(GameObject())
        deleted = self.game.add(GameObject())
        self.interpolation.begin_step(self.game)
        obj.x = deleted.x = 20
        self.interpolation.end_step(self.game)
        obj.x = 50
        deleted.delete()
        with self.interpolation.apply(self.game, 0.5):
            self.assertEqual(obj.x, 50)
            self.assertIsNone(deleted.scene)
        new_scene = self.game.scenes.add("next", Scene())
        self.game.scenes.switch("next")
        with self.interpolation.apply(self.game, 0):
            self.assertIs(self.game.scenes.current, new_scene)

    def test_catch_up_blends_only_the_last_two_simulation_steps(self) -> None:
        obj = self.game.add(GameObject())
        for _ in range(3):
            self.interpolation.begin_step(self.game)
            obj.x += 10
            self.interpolation.end_step(self.game)
        with self.interpolation.apply(self.game, 0.5):
            self.assertEqual(obj.x, 25)
        self.assertEqual(obj.x, 30)

    def test_visual_motion_is_even_at_60_120_and_144_hz(self) -> None:
        obj = self.game.add(GameObject())
        for rate in (60, 120, 144):
            with self.subTest(refresh_rate=rate):
                obj.x = 0
                interpolation = RenderInterpolation()
                accumulator = FixedStepAccumulator(1 / 60)

                def update(delta: float) -> None:
                    interpolation.begin_step(self.game)
                    obj.x += 600 * delta
                    interpolation.end_step(self.game)

                positions = []
                for _ in range(rate):
                    accumulator.advance(1 / rate, update)
                    with interpolation.apply(self.game, accumulator.alpha):
                        positions.append(obj.x)
                for previous, current in zip(positions[4:-1], positions[5:]):
                    self.assertAlmostEqual(current - previous, 600 / rate, places=6)

    def test_angle_wrap_and_camera_cuts(self) -> None:
        obj = self.game.add(GameObject(rotation=350))
        self.scene.camera.rotation = 350
        self.interpolation.begin_step(self.game)
        obj.rotation = self.scene.camera.rotation = 10
        self.scene.camera.x = 500
        self.scene.camera.reset_interpolation()
        self.interpolation.end_step(self.game)
        with self.interpolation.apply(self.game, 0.5):
            self.assertEqual(obj.rotation, 360)
            self.assertEqual(self.scene.camera.x, 500)
            self.assertEqual(self.scene.camera.rotation, 10)
        self.assertEqual(obj.rotation, 10)

    def test_replaced_camera_does_not_blend_from_old_camera(self) -> None:
        self.interpolation.begin_step(self.game)
        self.scene.camera = Camera(320, 180, x=500, y=400)
        self.interpolation.end_step(self.game)
        with self.interpolation.apply(self.game, 0):
            self.assertEqual(self.scene.camera.position, (500, 400))

    def test_readiness_callbacks_see_simulation_not_interpolated_positions(self) -> None:
        seen = []

        class Ready(Component):
            def on_ready(self) -> None:
                assert self.owner is not None and self.owner.parent is not None
                seen.append(self.owner.parent.x)

        parent = self.game.add(GameObject(10, 0))
        self.interpolation.begin_step(self.game)
        parent.x = 40
        child = parent.add_child(GameObject())
        child.add_component(Ready())
        self.interpolation.end_step(self.game)
        with self.interpolation.apply(self.game, 0.5):
            self.assertEqual(seen, [40])
            self.assertEqual(parent.x, 25)

class RenderWorkTests(unittest.TestCase):
    def test_unchanged_sprites_do_not_write_vertex_attributes(self) -> None:
        game = Game(100, 100)
        obj = game.add(GameObject(10, 20))
        renderer = obj.add_component(SpriteRenderer(Image.solid(4, 4, (255, 0, 0))))
        renderer.sync()
        with ExitStack() as stack:
            setters = {}
            for name in ("position", "rotation", "scale_x", "scale_y", "visible", "paused"):
                descriptor = getattr(pyglet.sprite.Sprite, name)
                spy = Mock()
                stack.enter_context(patch.object(
                    pyglet.sprite.Sprite, name, new=property(descriptor.fget, spy),
                ))
                setters[name] = spy
            renderer.sync()
            for setter in setters.values():
                setter.assert_not_called()
            obj.x += 5
            renderer.sync()
            setters["position"].assert_called_once()

    def test_render_preparation_only_runs_when_structure_changes(self) -> None:
        game = Game(100, 100)
        scene = game.scenes.current
        obj = game.add(GameObject())
        obj.add_component(SpriteRenderer())
        layer = obj.require_layer()
        with patch.object(scene, "_prepare_subtree", wraps=scene._prepare_subtree) as prepare:
            layer._prepare_render_structure()
            layer._prepare_render_structure()
            self.assertEqual(prepare.call_count, 1)
            obj.x = 50
            layer._prepare_render_structure()
            self.assertEqual(prepare.call_count, 1)
            obj.z_index = 4
            layer._prepare_render_structure()
            self.assertEqual(prepare.call_count, 2)
            child = obj.add_child(GameObject())
            renderer = child.add_component(SpriteRenderer())
            layer._prepare_render_structure()
            self.assertIs(child.scene, scene)
            self.assertIs(renderer.drawable.batch, layer._batch)
            renderer.screen_space = True
            layer._prepare_render_structure()
            self.assertIs(renderer.drawable.batch, layer._ui_batch)


class PhysicsContactReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(320, 180)
        self.physics = self.game.physics

    def test_nine_areas_share_one_world_contact_scan_per_tick(self) -> None:
        for index in range(9):
            self.game.add(create_area_box(index * 30, 0, 10, 10))
        with patch.object(self.physics._space, "shape_query", wraps=self.physics._space.shape_query) as query:
            self.game._update(0)
            # Nine queries for the world contacts, plus one local query per Area.
            self.assertEqual(query.call_count, 18)

    def test_queries_detect_mutable_transforms_geometry_and_enablement(self) -> None:
        obj = self.game.add(GameObject())
        collider = obj.add_component(Collider(width=10, height=10))
        self.assertIn(obj, self.physics.overlap_point((0, 0)))
        obj.position.x = 30
        self.assertNotIn(obj, self.physics.overlap_point((0, 0)))
        self.assertIn(obj, self.physics.overlap_point((30, 0)))
        collider.width = 60
        self.assertIn(obj, self.physics.overlap_point((5, 0)))
        collider.enabled = False
        self.assertNotIn(obj, self.physics.overlap_point((30, 0)))
        collider.enabled = True
        self.assertIn(obj, self.physics.overlap_point((30, 0)))
        collider.mask = 0
        self.assertNotIn(obj, self.physics.overlap_point((30, 0)))

    def test_area_callbacks_can_query_move_and_query_again_without_stale_results(self) -> None:
        trigger = self.game.add(create_area_box(0, 0, 20, 20))
        target = self.game.add(GameObject())
        target.add_component(Collider(width=5, height=5))
        later = self.game.add(create_area_box(100, 0, 20, 20))
        seen = []

        def move(other: GameObject) -> None:
            if other is target:
                self.assertIn(target, self.physics.overlap_point((0, 0)))
                target.x = 100
                self.assertNotIn(target, self.physics.overlap_point((0, 0)))
                self.assertIn(target, self.physics.overlap_point((100, 0)))

        trigger.require_component(Area).on_enter(move)
        later.require_component(Area).on_enter(seen.append)
        self.game._update(0)
        self.assertIn(target, seen)

    def test_custom_area_updates_do_not_reuse_contacts_after_mutation(self) -> None:
        class MovingArea(Area):
            def update(self, delta_time: float) -> None:
                assert self.owner is not None
                self.owner.x = 100
                super().update(delta_time)

        obj = self.game.add(GameObject())
        obj.add_component(Collider(width=10, height=10, sensor=True))
        area = obj.add_component(MovingArea())
        target = self.game.add(GameObject(100, 0))
        target.add_component(Collider(width=5, height=5))
        seen = []
        area.on_enter(seen.append)
        self.game._update(0)
        self.assertIn(target, seen)

    def test_parent_motion_and_component_removal_invalidate_queries(self) -> None:
        parent = self.game.add(GameObject())
        child = parent.add_child(GameObject())
        collider = child.add_component(Collider(width=5, height=5))
        self.game.scenes.current._prepare_subtree(parent)
        self.assertIn(child, self.physics.overlap_point((0, 0)))
        parent.x = 100
        self.assertNotIn(child, self.physics.overlap_point((0, 0)))
        self.assertIn(child, self.physics.overlap_point((100, 0)))
        child.remove_component(collider)
        self.assertNotIn(child, self.physics.overlap_point((100, 0)))
