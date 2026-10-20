import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest

from g import (
    Component,
    ClientInfo,
    Game,
    GameObject,
    NetworkAuthorityError,
    NetworkedGameObject,
    PhysicsBody,
    ProjectConfig,
    ProjectExportConfig,
    ProjectNetworkConfig,
    SceneSerializer,
    inspector_field,
    network_field,
)
from g.export import prepare_export


def _wait_until(condition, *sessions, timeout: float = 3) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        for session in sessions:
            session._before_update()
        if condition():
            return
        sleep(0.005)
    raise AssertionError("network condition timed out")


class SharedState(Component):
    health: int = network_field(100)
    local_cache: int = inspector_field(7)


class NetworkTests(unittest.TestCase):
    def test_project_network_configuration_round_trips(self) -> None:
        with TemporaryDirectory() as directory:
            project = ProjectConfig.create(
                directory,
                network=ProjectNetworkConfig(
                    True,
                    "ws://game.test:9000",
                    "0.0.0.0",
                    9000,
                    4,
                    30,
                ),
            )
            project.write()
            loaded = ProjectConfig.read(project.path)
            self.assertEqual(loaded.network, project.network)

    def test_network_component_round_trips_through_scene(self) -> None:
        game = Game(100, 100)
        owner = game.add(GameObject())
        owner.add_component(NetworkedGameObject(
            sync_scale=True,
            owner_client_number=2,
            owner_disconnect="despawn",
        ))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "network.scene.json"
            SceneSerializer().save(game.scenes.current, path)
            restored = SceneSerializer().load(game, path)
        marker = restored.objects[0].require_component(NetworkedGameObject)
        self.assertTrue(marker.sync_scale)
        self.assertEqual(marker.owner_client_number, 2)
        self.assertEqual(marker.owner_disconnect, "despawn")

    def test_network_export_prepares_client_and_server_targets(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            game = Game(100, 100)
            scene = root / "race.scene.json"
            menu = root / "menu.scene.json"
            SceneSerializer().save(game.scenes.current, scene)
            SceneSerializer().save(game.scenes.current, menu)
            project = ProjectConfig.create(
                root,
                export=ProjectExportConfig(
                    name="Race",
                    startup_scene=menu.name,
                    server_startup_scene=scene.name,
                ),
                network=ProjectNetworkConfig(
                    enabled=True,
                    auto_connect=False,
                ),
            )
            project.write()
            prepared = prepare_export(
                project.path,
                python="/usr/bin/python3",
            )
            self.assertIsNotNone(prepared.server_bootstrap)
            self.assertIsNotNone(prepared.server_spec)
            assert prepared.server_bootstrap is not None
            assert prepared.server_spec is not None
            compile(
                prepared.bootstrap.read_text(encoding="utf-8"),
                str(prepared.bootstrap),
                "exec",
            )
            compile(
                prepared.server_bootstrap.read_text(encoding="utf-8"),
                str(prepared.server_bootstrap),
                "exec",
            )
            self.assertIn("RaceServer", prepared.server_spec.read_text())
            self.assertNotIn(
                "game.network.connect",
                prepared.bootstrap.read_text(encoding="utf-8"),
            )
            bootstrap = prepared.server_bootstrap.read_text(encoding="utf-8")
            self.assertIn(repr(scene.name), bootstrap)
            self.assertIn("G_HEADLESS_SERVER", bootstrap)
            self.assertIn("platform.system() == 'Linux'", bootstrap)
            self.assertIn("not os.environ.get('DISPLAY')", bootstrap)
            self.assertIn(
                "not os.environ.get('WAYLAND_DISPLAY')",
                bootstrap,
            )

    def test_racing_server_loads_visual_components_without_backends(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = """
from pathlib import Path
from g import (
    CircleRenderer,
    Game,
    LineRenderer,
    ParticleEffect,
    PolygonRenderer,
    RectRenderer,
    RepeatingImageRenderer,
    SpriteRenderer,
    TextRenderer,
    TileRenderer,
    TrailRenderer,
    TriangleRenderer,
)
from g.runtime import NullDrawable

root = Path.cwd() / "examples" / "racing"
game = Game.from_project(root / "g.project.json", network_role="server")
game.scenes.load("race", root / "racing.scene.json", asset_root=root)
scene = game.scenes.switch("race")
components = [
    component
    for root_object in scene.objects
    for node in root_object.walk()
    for component in getattr(node, "components", ())
]
assert game.presentation_enabled is False
assert sum(isinstance(c, SpriteRenderer) for c in components) == 14
assert sum(isinstance(c, ParticleEffect) for c in components) == 32
assert sum(isinstance(c, TrailRenderer) for c in components) == 16
for component in components:
    if isinstance(component, SpriteRenderer):
        assert component.image is None or component.image._source is None
    elif isinstance(component, ParticleEffect):
        component.play()
        assert component.emit(5) == 0
        component.stop(clear=True)
    elif isinstance(component, TrailRenderer):
        component.update(1 / 60)
        component.clear()
renderers = (
    CircleRenderer(1),
    RectRenderer(1, 1),
    LineRenderer((0, 0), (1, 1)),
    PolygonRenderer(((0, 0), (1, 0), (0, 1))),
    TriangleRenderer(((0, 0), (1, 0), (0, 1))),
    TextRenderer("server"),
    RepeatingImageRenderer(),
    TileRenderer(),
)
assert all(isinstance(renderer.drawable, NullDrawable) for renderer in renderers)
"""
        environment = os.environ.copy()
        environment["G_HEADLESS_SERVER"] = "1"
        environment["PYGLET_AUDIO"] = "silent"
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"{result.stdout}\n{result.stderr}",
        )

    def test_server_replication_ownership_custom_state_and_input(self) -> None:
        server = Game(100, 100)
        server.network.configure("server", snapshot_hz=20)
        server_object = GameObject()
        server_object.scene_id = "player-one"
        server_state = server_object.add_component(SharedState())
        server_marker = server_object.add_component(
            NetworkedGameObject(owner_client_number=1)
        )
        server.add(server_object)

        client = Game(100, 100)
        client.network.configure("client", snapshot_hz=20)
        client_object = GameObject()
        client_object.scene_id = "player-one"
        client_state = client_object.add_component(SharedState())
        client_marker = client_object.add_component(
            NetworkedGameObject(owner_client_number=1)
        )
        client.add(client_object)

        port = server.network.listen("127.0.0.1", 0)
        try:
            client.network.connect(f"ws://127.0.0.1:{port}")
            _wait_until(
                lambda: (
                    client.network.state == "connected"
                    and server_marker.owner_id is not None
                    and client_marker.is_local_owner
                ),
                server.network,
                client.network,
            )
            self.assertTrue(client_marker.is_local_owner)

            server_object.position = 80, 40
            server_state.health = 55
            server_state.local_cache = 999
            server.network._after_update(0.1)
            _wait_until(
                lambda: client_object.x > 0,
                server.network,
                client.network,
            )
            self.assertGreater(client_object.x, 0)
            self.assertEqual(client_state.health, 55)
            self.assertEqual(client_state.local_cache, 7)

            client.network.set_input(
                client_marker,
                throttle=1.0,
                steering=-0.5,
                brake=False,
            )
            _wait_until(
                lambda: bool(server.network.input_for(server_marker)),
                server.network,
                client.network,
            )
            self.assertEqual(
                server.network.input_for(server_marker)["throttle"],
                1.0,
            )

            server_object.delete()
            _wait_until(
                lambda: client_object.scene is None,
                server.network,
                client.network,
            )
        finally:
            client.network.disconnect()
            server.network.disconnect()

    def test_client_applies_each_snapshot_only_once(self) -> None:
        client = Game(100, 100)
        client.network.configure("client")
        owner = GameObject()
        owner.scene_id = "remote"
        marker = owner.add_component(NetworkedGameObject())
        client.add(owner)
        client.network._targets["remote"] = {
            "id": "remote",
            "position": [100.0, 0.0],
            "rotation": 0.0,
        }

        client.network._before_update()
        self.assertEqual(owner.x, 50)
        client.network._before_update()
        self.assertEqual(owner.x, 50)

    def test_local_prediction_ignores_small_snapshot_position_error(
        self,
    ) -> None:
        client = Game(100, 100)
        client.network.configure("client")
        client.network.local_client = ClientInfo("local", 1)
        owner = GameObject()
        owner.scene_id = "local-car"
        marker = owner.add_component(
            NetworkedGameObject(owner_client_number=1)
        )
        marker.owner_id = "local"
        client.add(owner)
        client.network._targets["local-car"] = {
            "id": "local-car",
            "owner": "local",
            "position": [5.0, 0.0],
            "rotation": 2.0,
        }

        client.network._before_update()

        self.assertEqual(owner.position, (0, 0))
        self.assertEqual(owner.rotation, 0)

    def test_only_server_can_close_new_client_admissions(self) -> None:
        client = Game(100, 100)
        client.network.configure("client")
        with self.assertRaises(NetworkAuthorityError):
            client.network.close_admissions()

        server = Game(100, 100)
        server.network.configure("server")
        server.network.close_admissions()
        self.assertFalse(server.network.accepting_clients)
        port = server.network.listen("127.0.0.1", 0)
        rejected = Game(100, 100)
        rejected.network.configure("client")
        try:
            rejected.network.connect(f"ws://127.0.0.1:{port}")
            _wait_until(
                lambda: rejected.network.state != "connecting",
                server.network,
                rejected.network,
            )
            self.assertIsNone(rejected.network.local_client)
            self.assertNotEqual(rejected.network.state, "connected")
        finally:
            rejected.network.disconnect()
            server.network.disconnect()

    def test_local_prediction_tolerates_expected_high_speed_lead(self) -> None:
        client = Game(100, 100)
        client.network.configure("client", snapshot_hz=30)
        client.network.local_client = ClientInfo("local", 1)
        owner = GameObject(100, 0)
        owner.scene_id = "local-car"
        body = owner.add_component(PhysicsBody(velocity=(600, 0)))
        marker = owner.add_component(
            NetworkedGameObject(owner_client_number=1)
        )
        marker.owner_id = "local"
        client.add(owner)
        revision = owner._interpolation_revision
        client.network._targets["local-car"] = {
            "id": "local-car",
            "owner": "local",
            "position": [60.0, 0.0],
            "rotation": 0.0,
            "velocity": [600.0, 0.0],
            "angular_velocity": 0.0,
        }

        client.network._before_update()

        self.assertEqual(owner.position, (100, 0))
        self.assertEqual(body.velocity, (600, 0))
        self.assertEqual(owner._interpolation_revision, revision)

    def test_remote_physics_correction_preserves_render_interpolation(
        self,
    ) -> None:
        client = Game(100, 100)
        client.network.configure("client")
        owner = GameObject()
        owner.scene_id = "remote-car"
        body = owner.add_component(PhysicsBody())
        owner.add_component(NetworkedGameObject())
        client.add(owner)
        revision = owner._interpolation_revision
        client.network._targets["remote-car"] = {
            "id": "remote-car",
            "position": [20.0, 0.0],
            "rotation": 5.0,
            "velocity": [100.0, 0.0],
            "angular_velocity": 10.0,
        }

        client.network._before_update()

        self.assertEqual(owner.position, (0, 0))
        self.assertEqual(body.velocity, (220, 0))
        self.assertEqual(body.angular_velocity, 40)
        self.assertEqual(owner._interpolation_revision, revision)


if __name__ == "__main__":
    unittest.main()
