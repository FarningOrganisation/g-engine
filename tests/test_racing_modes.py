from pathlib import Path
from unittest.mock import PropertyMock, patch
import unittest

from pyglet.window import key

from examples.racing.race import (
    COUNTDOWN_SECONDS,
    LOBBY_SECONDS,
    BotDriver,
    GhostPlayback,
    RaceDirector,
    RacePauseMenu,
    configure_race,
)
from examples.racing.car import Car, CarController
from g import (
    Button,
    CameraFollow,
    ClientInfo,
    Container,
    Game,
    NetworkedGameObject,
    PhysicsBody,
    SceneSerializer,
    Spline,
    TextInput,
    Widget,
)


ROOT = Path(__file__).parent.parent / "examples" / "racing"


class RacingModeTests(unittest.TestCase):
    def test_menu_scene_restores_authored_main_menu_view(self) -> None:
        configure_race("bots", "ws://127.0.0.1:7777")
        game = Game(1920, 1080, asset_root=ROOT)
        scene = SceneSerializer().load(
            game,
            ROOT / "menu.scene.json",
            asset_root=ROOT,
        )

        view = scene.gui.get("Main Menu")
        widgets: list[Widget] = []

        def collect(widget: Widget) -> None:
            widgets.append(widget)
            if isinstance(widget, Container):
                for child in widget.children:
                    collect(child)

        collect(view.root)
        view._layout()
        menu_center = next(
            widget
            for widget in widgets
            if widget.name == "Menu Center"
        )
        menu_layout = next(
            widget
            for widget in widgets
            if widget.name == "Menu Layout"
        )
        buttons = [
            widget for widget in widgets
            if isinstance(widget, Button)
        ]
        inputs = [
            widget for widget in widgets
            if isinstance(widget, TextInput)
        ]

        self.assertIs(scene.gui.current, view)
        self.assertEqual(menu_center.bounds, (0, 0, 1920, 1080))
        self.assertEqual(
            menu_layout.bounds,
            (730, 315, 1190, 765),
        )
        self.assertEqual(len(buttons), 3)
        self.assertEqual(len(inputs), 1)
        self.assertEqual(
            len({button.bounds for button in buttons}),
            3,
        )
        for upper, lower in zip(buttons, buttons[1:]):
            self.assertGreaterEqual(
                upper.bounds[1],
                lower.bounds[3],
            )
        self.assertTrue(all(button.on_click is not None for button in buttons))
        self.assertEqual(inputs[0].text, "127.0.0.1:7777")

    def load_race(self, mode: str, *, role: str = "offline"):
        configure_race(mode)
        game = Game(
            1920,
            1080,
            asset_root=ROOT,
            presentation_enabled=role != "server",
        )
        game.network.configure(role, snapshot_hz=30, max_clients=8)
        scene = SceneSerializer().load(
            game,
            ROOT / "racing.scene.json",
            asset_root=ROOT,
        )
        director = scene.require_child(
            "Race Director"
        ).require_component(RaceDirector)
        return game, scene, director

    def test_bot_mode_spawns_full_prefab_grid_at_eight_starts(self) -> None:
        _, scene, director = self.load_race("bots")
        starts = scene.require_child("starts")

        self.assertEqual(len(director.cars), 8)
        self.assertEqual(
            sum(
                car.get_component(BotDriver) is not None
                for car in director.cars.values()
            ),
            7,
        )
        director.update(0)
        self.assertFalse(
            director.cars[2].require_component(
                CarController
            ).accept_input
        )
        self.assertFalse(
            director.cars[2].require_component(
                CarController
            ).enabled
        )
        for slot, car in director.cars.items():
            self.assertEqual(car.scene_id, f"race-car-{slot}")
            self.assertEqual(
                car.world_position,
                starts.require_child(f"start{slot}").world_position,
            )

    def test_bots_accelerate_and_only_player_controls_camera(self) -> None:
        _, scene, director = self.load_race("bots")

        for _ in range(300):
            scene._update(1 / 60)

        player = director.cars[1]
        bot = director.cars[2]
        self.assertGreater(
            bot.require_component(Car).forward_speed,
            250,
        )
        self.assertEqual(scene.camera.position, player.world_position)
        self.assertEqual(
            [
                slot
                for slot, car in director.cars.items()
                if car.require_component(CameraFollow).enabled
            ],
            [1],
        )

    def test_offline_race_uses_countdown_before_enabling_input(
        self,
    ) -> None:
        _, _, director = self.load_race("bots")
        player = director.cars[1].require_component(CarController)

        director.update(COUNTDOWN_SECONDS - 0.01)
        self.assertFalse(director.race_started)
        self.assertFalse(player.accept_input)
        assert director._countdown_label is not None
        self.assertEqual(director._countdown_label.text, "1")
        self.assertTrue(director._countdown_label.visible)

        director.update(0.01)

        self.assertTrue(director.race_started)
        self.assertTrue(player.accept_input)
        self.assertEqual(director._countdown_label.text, "GO!")
        self.assertTrue(director._countdown_label.visible)

    def test_bots_use_authored_closed_spline_with_separate_lanes(
        self,
    ) -> None:
        _, scene, director = self.load_race("bots")
        path = scene.require_child(
            "Bot Racing Line"
        ).require_component(Spline)
        bots = [
            director.cars[slot].require_component(BotDriver)
            for slot in (2, 3, 4)
        ]
        targets = [
            bot._path_target(slot, 300)
            for slot, bot in zip((2, 3, 4), bots)
        ]

        self.assertTrue(path.closed)
        self.assertTrue(any(y < 0 for _, y in path.points))
        self.assertTrue(all(bot.path is path for bot in bots))
        self.assertEqual(len(set(targets)), 3)

    def test_time_trial_camera_follows_only_player(self) -> None:
        _, _, director = self.load_race("time_trial")

        self.assertEqual(
            [
                slot
                for slot, car in director.cars.items()
                if car.require_component(CameraFollow).enabled
            ],
            [1],
        )

    def test_escape_menu_pauses_resumes_and_returns_to_menu(
        self,
    ) -> None:
        game, scene, director = self.load_race("bots")
        pause = director.game_object.require_component(RacePauseMenu)
        view = scene.gui.get("Pause Menu")
        buttons: list[Button] = []

        def collect(widget: Widget) -> None:
            if isinstance(widget, Button):
                buttons.append(widget)
            if isinstance(widget, Container):
                for child in widget.children:
                    collect(child)

        collect(view.root)
        self.assertTrue(view.modal)
        self.assertTrue(view.pauses_scene)
        self.assertEqual(
            {button.name for button in buttons},
            {"Resume Race", "Return to Main Menu"},
        )
        self.assertTrue(all(button.on_click is not None for button in buttons))

        with patch.object(
            pause.input,
            "pressed",
            return_value=True,
        ):
            pause.update(0)

        self.assertIs(scene.gui.current, view)
        self.assertTrue(scene.gui.pauses_scene)
        elapsed = scene.time.elapsed
        scene._update(0.5)
        self.assertEqual(scene.time.elapsed, elapsed)

        self.assertTrue(scene.gui.on_key_press(key.ESCAPE, 0))
        self.assertIs(scene.gui.current, scene.gui.default)
        self.assertFalse(scene.gui.pauses_scene)

        game.network.state = "connected"
        with (
            patch.object(game.network, "disconnect") as disconnect,
            patch.object(game.scenes, "get", return_value=scene),
            patch.object(game.scenes, "switch") as switch,
        ):
            pause._return_to_menu()

        disconnect.assert_called_once_with()
        switch.assert_called_once_with("menu")

    def test_multiplayer_lobby_starts_120_seconds_after_first_join(
        self,
    ) -> None:
        game, _, director = self.load_race(
            "multiplayer",
            role="server",
        )
        clients = (ClientInfo("player-one", 1),)
        with patch.object(
            type(game.network),
            "clients",
            new_callable=PropertyMock,
            return_value=clients,
        ):
            director.update(LOBBY_SECONDS - 0.01)
            self.assertFalse(director.race_started)
            self.assertAlmostEqual(director.lobby_remaining, 0.01)
            director.update(0.01)

            self.assertFalse(director.race_started)
            self.assertEqual(
                director.countdown_remaining,
                COUNTDOWN_SECONDS,
            )
            director.update(COUNTDOWN_SECONDS)

        self.assertTrue(director.race_started)
        self.assertFalse(game.network.accepting_clients)
        self.assertTrue(director.cars[1].enabled)
        self.assertFalse(director.cars[2].enabled)

    def test_backward_finish_crossing_is_rejected_and_five_laps_finish(
        self,
    ) -> None:
        _, _, director = self.load_race("bots")
        director.race_started = True
        car = director.cars[1]
        participant = director.participants[1]
        body = car.require_component(PhysicsBody)

        participant.distance = 5000
        participant.far_from_finish = True
        participant.lap_elapsed = 30
        body.velocity = (-100, 0)
        director.cross_finish(car)
        self.assertEqual(participant.laps_completed, 0)

        for lap in range(5):
            participant.distance = 5000
            participant.far_from_finish = True
            participant.lap_elapsed = 30 - lap
            body.velocity = (100, 0)
            director.cross_finish(car)

        self.assertEqual(participant.laps_completed, 5)
        self.assertTrue(participant.finished)
        self.assertEqual(participant.finish_position, 1)
        self.assertEqual(participant.best_lap, 26)

    def test_time_trial_records_best_lap_for_ghost_playback(self) -> None:
        _, _, director = self.load_race("time_trial")
        director.race_started = True
        participant = director.participants[1]
        car = director.cars[1]
        body = car.require_component(PhysicsBody)
        ghost = director.cars[2].require_component(GhostPlayback)
        marker = director.cars[2].require_component(NetworkedGameObject)

        self.assertFalse(marker.enabled)
        participant.distance = 5000
        participant.far_from_finish = True
        participant.lap_elapsed = 20
        participant.samples = [
            (0, 100, 100, 90),
            (20, 1344, 448, 90),
        ]
        body.velocity = (100, 0)
        director.cross_finish(car)

        self.assertEqual(participant.best_lap, 20)
        self.assertEqual(ghost.samples, (
            (0, 100, 100, 90),
            (20, 1344, 448, 90),
        ))


if __name__ == "__main__":
    unittest.main()
