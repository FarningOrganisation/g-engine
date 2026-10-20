import unittest

from g import Collider, ColorTileSet, Game, GameObject, Script, TileCollision


class ContactScript(Script):
    def __init__(self):
        super().__init__()
        self.events = []

    def on_collision(self, contact):
        self.events.append(("begin", contact.other))

    def on_collision_stay(self, contact):
        self.events.append(("stay", contact.other))

    def on_collision_end(self, contact):
        self.events.append(("end", contact.other))

    def on_overlap(self, other):
        self.events.append(("overlap", other))

    def on_overlap_stay(self, other):
        self.events.append(("overlap_stay", other))

    def on_overlap_end(self, other):
        self.events.append(("overlap_end", other))

    def on_tile_collision(self, contact):
        self.events.append(("tile", contact))

    def on_tile_collision_stay(self, contact):
        self.events.append(("tile_stay", contact))

    def on_tile_collision_end(self, contact):
        self.events.append(("tile_end", contact))

    def on_tile_overlap(self, contact):
        self.events.append(("tile_overlap", contact))

    def on_tile_overlap_stay(self, contact):
        self.events.append(("tile_overlap_stay", contact))

    def on_tile_overlap_end(self, contact):
        self.events.append(("tile_overlap_end", contact))


class ScriptCollisionHookTests(unittest.TestCase):
    def create_pair(self, sensor=False):
        game = Game(100, 100)
        first = game.add(GameObject(10, 10))
        second = game.add(GameObject(15, 10))
        for obj in (first, second):
            self.addCleanup(obj.delete)
            obj.add_component(Collider(width=20, height=20, sensor=sensor))
        script = first.add_component(ContactScript())
        return game, first, second, script

    def test_collision_begin_stay_end_and_existing_registrations(self):
        game, first, second, script = self.create_pair()
        other_script = second.add_component(ContactScript())
        registered = []
        first.on_collision(registered.append)
        game._update(0)
        game._update(0)
        second.x = 80
        game._update(0)
        self.assertEqual(script.events, [("begin", second), ("stay", second), ("end", second)])
        self.assertEqual(other_script.events, [("begin", first), ("stay", first), ("end", first)])
        self.assertEqual([contact.other for contact in registered], [second])

    def test_sensor_overlap_begin_stay_end(self):
        game, first, second, script = self.create_pair(sensor=True)
        game._update(0)
        game._update(0)
        second.x = 80
        game._update(0)
        self.assertEqual([name for name, _ in script.events], ["overlap", "overlap_stay", "overlap_end"])

    def test_tile_collision_and_sensor_hooks(self):
        for sensor in (False, True):
            with self.subTest(sensor=sensor):
                game = Game(100, 100)
                tileset = ColorTileSet([(255, 255, 255)])
                tileset.define(0, collision=TileCollision())
                layer = game.scenes.current.layers.add_tile(
                    "Ground", tileset, cell_width=32, cell_height=32, collision_enabled=True,
                )
                layer.set_tile(0, 0, 0)
                obj = game.add(GameObject(16, 16))
                self.addCleanup(obj.delete)
                obj.add_component(Collider(width=10, height=10, sensor=sensor))
                script = obj.add_component(ContactScript())
                game._update(0)
                game._update(0)
                obj.x = 80
                game._update(0)
                prefix = "tile_overlap" if sensor else "tile"
                self.assertEqual([name for name, _ in script.events], [prefix, prefix + "_stay", prefix + "_end"])

    def test_disabled_unready_and_removed_scripts_do_not_receive_callbacks(self):
        game, first, second, script = self.create_pair()
        script.enabled = False
        game._update(0)
        self.assertFalse(script.events)
        script.enabled = True
        script._ready = False
        game._update(0)
        self.assertFalse(script.events)
        script._ready = True
        game._update(0)
        self.assertEqual(script.events, [("stay", second)])
        first.remove_component(script)
        game._update(0)
        self.assertEqual(len(script.events), 1)

    def test_handlers_can_delete_owner_or_remove_script_before_automatic_callback(self):
        for delete in (False, True):
            game, first, second, script = self.create_pair()
            first.on_collision(lambda other: first.delete() if delete else first.remove_component(script))
            game._update(0)
            self.assertFalse(script.events)

    def test_script_can_delete_owner_before_later_script_callback(self):
        game, first, second, script = self.create_pair()
        script.on_collision = lambda other: first.delete()
        later = first.add_component(ContactScript())
        game._update(0)
        self.assertFalse(later.events)
