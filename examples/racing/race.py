"""Race modes, lobby flow, lap validation, bots, and best-lap ghosts."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, ceil, cos, degrees, hypot, radians, sin
from pathlib import Path
from typing import TypeVar

from g import (
    AnchorLayout,
    Area,
    Button,
    CameraFollow,
    Collider,
    Component,
    Container,
    GameObject,
    GUIManager,
    Key,
    Label,
    NetworkedGameObject,
    PhysicsBody,
    Script,
    SpriteRenderer,
    Spline,
    TextInput,
    UIView,
    Widget,
    instantiate_prefab,
    network_field,
    scene_component,
)

if __package__:
    from .car import Car, CarController, CarEffects
else:
    from car import Car, CarController, CarEffects


WidgetType = TypeVar("WidgetType", bound=Widget)
ROOT = Path(__file__).resolve().parent
CAR_PREFAB = ROOT / "car.prefab.json"
TOTAL_LAPS = 5
LOBBY_SECONDS = 120.0
COUNTDOWN_SECONDS = 3.0
MINIMUM_LAP_DISTANCE = 3500.0
MINIMUM_FINISH_SPEED = 30.0
@dataclass
class RaceConfiguration:
    mode: str = "bots"
    endpoint: str = "ws://127.0.0.1:7777"


configuration = RaceConfiguration()


def configure_race(mode: str, endpoint: str | None = None) -> None:
    if mode not in {"bots", "time_trial", "multiplayer"}:
        raise ValueError(f"unknown race mode: {mode!r}")
    configuration.mode = mode
    if endpoint is not None:
        configuration.endpoint = endpoint


@scene_component("race.RaceMenu", display_name="Race Menu")
class RaceMenu(Script):
    def on_ready(self) -> None:
        gui = self.scene.gui
        if not isinstance(gui, GUIManager):
            return
        view = gui.get("Main Menu")
        self.endpoint = self._require_widget(
            view,
            "Server Address",
            TextInput,
        )
        self.endpoint.text = configuration.endpoint.removeprefix("ws://")
        self._require_widget(
            view,
            "Bots Mode",
            Button,
        ).on_click = self._start_bots
        self._require_widget(
            view,
            "Time Trial Mode",
            Button,
        ).on_click = self._start_time_trial
        self._require_widget(
            view,
            "Multiplayer Mode",
            Button,
        ).on_click = self._start_multiplayer

    @staticmethod
    def _require_widget(
        view: UIView,
        name: str,
        expected_type: type[WidgetType],
    ) -> WidgetType:
        def descendants(widget: Widget):
            yield widget
            if isinstance(widget, Container):
                for child in widget.children:
                    yield from descendants(child)

        widget = next(
            (
                candidate
                for candidate in descendants(view.root)
                if candidate.name == name
            ),
            None,
        )
        if not isinstance(widget, expected_type):
            raise LookupError(
                f"{view.name} requires {expected_type.__name__} {name!r}"
            )
        return widget

    def _start_bots(self) -> None:
        self._start("bots")

    def _start_time_trial(self) -> None:
        self._start("time_trial")

    def _start_multiplayer(self) -> None:
        address = self.endpoint.text.strip()
        if not address:
            address = "127.0.0.1:7777"
        endpoint = address if "://" in address else f"ws://{address}"
        self._start("multiplayer", endpoint)

    def _start(self, mode: str, endpoint: str | None = None) -> None:
        network = self.game.network
        if network.state not in {"disconnected", "failed"}:
            network.disconnect()
        network.configure(
            "client" if mode == "multiplayer" else "offline",
            snapshot_hz=network.snapshot_hz,
            max_clients=network.max_clients,
        )
        configure_race(mode, endpoint)
        if mode == "multiplayer":
            network.connect(configuration.endpoint)
        self.game.scenes.load(
            "race",
            str(ROOT / "racing.scene.json"),
            asset_root=str(ROOT),
        )
        self.game.scenes.switch("race")


@scene_component("race.RacePauseMenu", display_name="Race Pause Menu")
class RacePauseMenu(Script):
    def on_ready(self) -> None:
        gui = self.scene.gui
        if not isinstance(gui, GUIManager):
            self._gui = None
            return
        self._gui = gui
        view = gui.get("Pause Menu")
        RaceMenu._require_widget(
            view,
            "Resume Race",
            Button,
        ).on_click = self._resume
        RaceMenu._require_widget(
            view,
            "Return to Main Menu",
            Button,
        ).on_click = self._return_to_menu

    def update(self, delta_time: float) -> None:
        del delta_time
        gui = self._gui
        if (
            gui is not None
            and gui.current.name != "Pause Menu"
            and self.input.pressed(Key.ESCAPE)
        ):
            gui.push("Pause Menu")

    def _resume(self) -> None:
        gui = self._gui
        if gui is not None and gui.current.name == "Pause Menu":
            gui.pop()

    def _return_to_menu(self) -> None:
        network = self.game.network
        if network.state not in {"disconnected", "failed"}:
            network.disconnect()
        try:
            self.game.scenes.get("menu")
        except ValueError:
            self.game.scenes.load(
                "menu",
                str(ROOT / "menu.scene.json"),
                asset_root=str(ROOT),
            )
        self.game.scenes.switch("menu")


class RaceParticipant(Component):
    laps_completed: int = network_field(0)
    finished: bool = network_field(False)
    finish_position: int = network_field(0)
    best_lap: float = network_field(0.0)

    def __init__(self, slot: int, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self.slot = slot
        self.laps_completed = 0
        self.finished = False
        self.finish_position = 0
        self.best_lap = 0.0
        self.distance = 0.0
        self.far_from_finish = False
        self.lap_elapsed = 0.0
        self.previous_position: tuple[float, float] | None = None
        self.samples: list[tuple[float, float, float, float]] = []

    def observe(self, delta_time: float, finish: GameObject) -> None:
        position = self.game_object.world_position
        current = position.x, position.y
        if self.previous_position is not None:
            self.distance += hypot(
                current[0] - self.previous_position[0],
                current[1] - self.previous_position[1],
            )
        self.previous_position = current
        self.lap_elapsed += delta_time
        if hypot(position.x - finish.x, position.y - finish.y) >= 1200:
            self.far_from_finish = True
        self.samples.append((
            self.lap_elapsed,
            position.x,
            position.y,
            self.game_object.world_rotation,
        ))

    def reset_lap(self) -> None:
        self.distance = 0.0
        self.far_from_finish = False
        self.lap_elapsed = 0.0
        self.samples = []


@scene_component("race.BotDriver", display_name="Bot Driver")
class BotDriver(Script):
    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self._stuck_time = 0.0
        self._recovery_time = 0.0
        self._recovery_steering = 1.0
        self._path_segments: tuple[
            tuple[float, float, float, float, float, float],
            ...,
        ] = ()

    def on_ready(self) -> None:
        self.car = self.game_object.require_component(Car)
        self.path = self.scene.require_child(
            "Bot Racing Line"
        ).require_component(Spline)
        samples = self.path.world_curve.sampled_points
        cumulative = 0.0
        segments = []
        for first, second in zip(samples, samples[1:]):
            delta_x = second[0] - first[0]
            delta_y = second[1] - first[1]
            length = hypot(delta_x, delta_y)
            if length <= 1e-6:
                continue
            segments.append((
                first[0],
                first[1],
                delta_x,
                delta_y,
                length,
                cumulative,
            ))
            cumulative += length
        if not segments:
            raise ValueError("Bot Racing Line has no usable segments")
        self._path_segments = tuple(segments)

    def update(self, delta_time: float) -> None:
        director = self.scene.require_child(
            "Race Director"
        ).require_component(RaceDirector)
        participant = self.game_object.require_component(RaceParticipant)
        if not director.race_started or participant.finished:
            self._stuck_time = 0.0
            self._recovery_time = 0.0
            self.car.drive(0, 0, brake=True)
            return
        if self._recovery_time > 0:
            self._recovery_time = max(
                0.0,
                self._recovery_time - delta_time,
            )
            self.car.drive(-0.7, self._recovery_steering)
            return
        lookahead = 220 + min(
            abs(self.car.forward_speed),
            self.car.max_forward_speed,
        ) * 0.45
        target_x, target_y = self._path_target(
            participant.slot,
            lookahead,
        )
        position = self.game_object.world_position
        offset_x = target_x - position.x
        offset_y = target_y - position.y
        target_angle = degrees(atan2(offset_x, offset_y))
        angle_error = (
            target_angle - self.game_object.world_rotation + 180
        ) % 360 - 180
        route_steering = max(
            -1.0,
            min(1.0, angle_error / 40.0),
        )
        center_blocked = self._ray_blocked(0, lookahead)
        left_blocked = self._ray_blocked(32, lookahead * 0.78)
        right_blocked = self._ray_blocked(-32, lookahead * 0.78)
        avoidance = 0.0
        if center_blocked:
            if left_blocked and not right_blocked:
                avoidance = -1.0
            elif right_blocked and not left_blocked:
                avoidance = 1.0
            else:
                avoidance = -1.0 if route_steering >= 0 else 1.0
        elif left_blocked != right_blocked:
            avoidance = -0.55 if left_blocked else 0.55
        steering = max(
            -1.0,
            min(1.0, route_steering * 0.7 + avoidance),
        )
        throttle = (
            0.35
            if center_blocked
            else 0.6
            if abs(angle_error) > 65
            else 1.0
        )
        self.car.drive(throttle, steering)
        if abs(self.car.forward_speed) < 18:
            self._stuck_time += delta_time
        else:
            self._stuck_time = 0.0
        if self._stuck_time >= 1.0:
            self._stuck_time = 0.0
            self._recovery_time = 0.75
            self._recovery_steering = (
                -1.0
                if participant.slot % 2
                else 1.0
            )

    def _path_target(
        self,
        slot: int,
        lookahead: float,
    ) -> tuple[float, float]:
        position = self.game_object.world_position
        nearest_distance = 0.0
        nearest_squared = float("inf")
        for (
            start_x,
            start_y,
            delta_x,
            delta_y,
            length,
            cumulative,
        ) in self._path_segments:
            amount = max(
                0.0,
                min(
                    1.0,
                    (
                        (position.x - start_x) * delta_x
                        + (position.y - start_y) * delta_y
                    ) / (length * length),
                ),
            )
            projected_x = start_x + delta_x * amount
            projected_y = start_y + delta_y * amount
            squared = (
                (position.x - projected_x) ** 2
                + (position.y - projected_y) ** 2
            )
            if squared < nearest_squared:
                nearest_squared = squared
                nearest_distance = cumulative + length * amount
        distance = nearest_distance + lookahead
        target_x, target_y = self.path.point_at_distance(distance)
        tangent_x, tangent_y = self.path.tangent_at_distance(distance)
        lane = ((slot + 1) % 3 - 1) * 70.0
        return (
            target_x - tangent_y * lane,
            target_y + tangent_x * lane,
        )

    def _ray_blocked(
        self,
        angle_offset: float,
        distance: float,
    ) -> bool:
        angle = radians(
            self.game_object.world_rotation + angle_offset
        )
        forward_x, forward_y = sin(angle), cos(angle)
        position = self.game_object.world_position
        start = (
            position.x + forward_x * 48,
            position.y + forward_y * 48,
        )
        end = (
            position.x + forward_x * distance,
            position.y + forward_y * distance,
        )
        for hit in self.scene.physics.raycast_all(
            start,
            end,
            radius=10,
        ):
            if hit is self.game_object:
                continue
            if (
                isinstance(hit, GameObject)
                and hit.get_component(Area) is not None
            ):
                continue
            return True
        return False


class GhostPlayback(Script):
    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self.samples: tuple[tuple[float, float, float, float], ...] = ()

    def update(self, delta_time: float) -> None:
        del delta_time
        if not self.samples:
            self.game_object.visible = False
            return
        director = self.scene.require_child(
            "Race Director"
        ).require_component(RaceDirector)
        player = director.participants.get(1)
        if player is None:
            return
        elapsed = player.lap_elapsed
        before = self.samples[0]
        after = self.samples[-1]
        for sample in self.samples[1:]:
            if sample[0] >= elapsed:
                after = sample
                break
            before = sample
        duration = after[0] - before[0]
        weight = 0.0 if duration <= 0 else (elapsed - before[0]) / duration
        self.game_object.position = (
            before[1] + (after[1] - before[1]) * weight,
            before[2] + (after[2] - before[2]) * weight,
        )
        delta = (after[3] - before[3] + 180) % 360 - 180
        self.game_object.rotation = before[3] + delta * weight
        self.game_object.visible = True


@scene_component("race.FinishLine", display_name="Finish Line")
class FinishLine(Script):
    def on_ready(self) -> None:
        self.game_object.require_component(Area).on_enter(self._on_enter)

    def _on_enter(self, owner: GameObject) -> None:
        director = self.scene.require_child(
            "Race Director"
        ).require_component(RaceDirector)
        director.cross_finish(owner)


@scene_component("race.RaceDirector", display_name="Race Director")
class RaceDirector(Script):
    lobby_remaining: float = network_field(LOBBY_SECONDS)
    countdown_remaining: float = network_field(COUNTDOWN_SECONDS)
    race_started: bool = network_field(False)

    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__(enabled=enabled)
        self.lobby_remaining = LOBBY_SECONDS
        self.countdown_remaining = COUNTDOWN_SECONDS
        self.race_started = False
        self.participants: dict[int, RaceParticipant] = {}
        self.cars: dict[int, GameObject] = {}
        self._finish_count = 0
        self._lobby_open = False
        self._ghost: GhostPlayback | None = None
        self._status: Label | None = None
        self._countdown_label: Label | None = None
        self._go_remaining = 0.0
        self._was_race_started = False

    def on_ready(self) -> None:
        if self.game.network.is_server:
            configure_race("multiplayer")
        self.finish = self.scene.require_child("finish line")
        self._spawn_grid()
        if self.game.presentation_enabled:
            self._status = Label("", font_size=24, width=600, height=80)
            self._countdown_label = Label(
                "",
                font_size=110,
                color=(255, 220, 70, 255),
                width=420,
                height=160,
                visible=False,
            )
            gui = self.scene.gui
            if isinstance(gui, GUIManager):
                gui.add(
                    AnchorLayout(
                        self._status,
                        horizontal="left",
                        vertical="top",
                        margin=24,
                    )
                )
                gui.add(
                    AnchorLayout(
                        self._countdown_label,
                        horizontal="center",
                        vertical="center",
                    )
                )

    def update(self, delta_time: float) -> None:
        countdown_ready = (
            configuration.mode != "multiplayer"
            or self.lobby_remaining <= 0
        )
        if configuration.mode == "multiplayer":
            self._update_multiplayer_lobby(delta_time)
        if (
            not self.game.network.is_client
            and countdown_ready
        ):
            self._update_countdown(delta_time)
        for slot, participant in self.participants.items():
            car = self.cars[slot]
            controller = car.require_component(CarController)
            controller.accept_input = (
                self.race_started
                and not participant.finished
                and car.get_component(BotDriver) is None
                and (
                    configuration.mode != "multiplayer"
                    or car.require_component(
                        NetworkedGameObject
                    ).is_local_owner
                    or self.game.network.is_server
                )
            )
            if (
                self.race_started
                and car.enabled
                and not participant.finished
                and (
                    not self.game.network.is_client
                    or car.require_component(
                        NetworkedGameObject
                    ).is_local_owner
                )
            ):
                participant.observe(delta_time, self.finish)
        self._update_countdown_display(delta_time)
        self._update_status()
        self._was_race_started = self.race_started

    def _update_countdown(self, delta_time: float) -> None:
        if self.race_started:
            return
        self.countdown_remaining = max(
            0.0,
            self.countdown_remaining - delta_time,
        )
        if self.countdown_remaining <= 1e-6:
            self.countdown_remaining = 0.0
            self.race_started = True

    def _update_countdown_display(self, delta_time: float) -> None:
        label = self._countdown_label
        if label is None:
            return
        countdown_active = (
            configuration.mode != "multiplayer"
            or self.lobby_remaining <= 0
        )
        if not self.race_started and countdown_active:
            remaining = max(0.0, self.countdown_remaining)
            label.text = str(max(1, ceil(remaining)))
            pulse = remaining % 1.0
            scale = 1.0 + pulse * 0.45
            label.scale_x = scale
            label.scale_y = scale
            label.opacity = max(
                120,
                min(255, int(120 + pulse * 135)),
            )
            label.color = (255, 220, 70, 255)
            label.visible = True
            return
        if self.race_started and not self._was_race_started:
            self._go_remaining = 0.85
        if self._go_remaining > 0:
            self._go_remaining = max(
                0.0,
                self._go_remaining - delta_time,
            )
            progress = self._go_remaining / 0.85
            label.text = "GO!"
            label.scale_x = 1.0 + (1.0 - progress) * 0.6
            label.scale_y = label.scale_x
            label.opacity = max(0, min(255, int(progress * 255)))
            label.color = (80, 255, 120, 255)
            label.visible = True
            return
        label.visible = False

    def cross_finish(self, owner: GameObject) -> None:
        if not self.race_started:
            return
        if self.game.network.is_client:
            return
        participant = owner.get_component(RaceParticipant)
        body = owner.get_component(PhysicsBody)
        if participant is None or body is None or participant.finished:
            return
        if (
            body.velocity.x < MINIMUM_FINISH_SPEED
            or not participant.far_from_finish
            or participant.distance < MINIMUM_LAP_DISTANCE
        ):
            return
        lap_time = participant.lap_elapsed
        participant.laps_completed += 1
        if participant.best_lap == 0 or lap_time < participant.best_lap:
            participant.best_lap = lap_time
            if (
                configuration.mode == "time_trial"
                and participant.slot == 1
                and self._ghost is not None
            ):
                self._ghost.samples = tuple(participant.samples)
        participant.reset_lap()
        if participant.laps_completed >= TOTAL_LAPS:
            self._finish_count += 1
            participant.finished = True
            participant.finish_position = self._finish_count
            owner.require_component(CarController).accept_input = False

    def _spawn_grid(self) -> None:
        starts = self.scene.require_child("starts")
        for slot in range(1, 9):
            start = starts.require_child(f"start{slot}")
            car = instantiate_prefab(
                self.game,
                CAR_PREFAB,
                asset_root=ROOT,
                scene_id=f"race-car-{slot}",
            )
            car.name = f"Race Car {slot}"
            car.world_position = start.world_position
            car.world_rotation = 90
            marker = car.require_component(NetworkedGameObject)
            marker.owner_client_number = (
                slot if configuration.mode == "multiplayer" else 0
            )
            controller = car.require_component(CarController)
            controller.accept_input = slot == 1
            car.require_component(CameraFollow).enabled = slot == 1
            participant = car.add_component(RaceParticipant(slot))
            self.participants[slot] = participant
            self.cars[slot] = car

            if configuration.mode == "bots" and slot > 1:
                controller.accept_input = False
                controller.enabled = False
                car.add_component(BotDriver())
            elif configuration.mode == "time_trial" and slot == 2:
                self._prepare_ghost(car)
            elif configuration.mode == "time_trial" and slot > 2:
                car.enabled = False
                car.visible = False
            elif configuration.mode == "multiplayer":
                car.enabled = False
                car.visible = False
            self.scene.add(car)

    def _prepare_ghost(self, car: GameObject) -> None:
        car.require_component(SpriteRenderer).opacity = 120
        car.visible = False
        car.require_component(Collider).enabled = False
        car.require_component(PhysicsBody).enabled = False
        car.require_component(Car).enabled = False
        car.require_component(CarController).enabled = False
        car.require_component(CarEffects).enabled = False
        car.require_component(CameraFollow).enabled = False
        car.require_component(NetworkedGameObject).enabled = False
        self.participants.pop(2)
        self._ghost = car.add_component(GhostPlayback())

    def _update_multiplayer_lobby(self, delta_time: float) -> None:
        network = self.game.network
        if network.is_server:
            connected = {client.number for client in network.clients}
            for slot, car in self.cars.items():
                active = slot in connected
                car.enabled = active
                car.visible = active
            if connected and not self._lobby_open and not self.race_started:
                self._lobby_open = True
                self.lobby_remaining = LOBBY_SECONDS
            if self._lobby_open and not self.race_started:
                self.lobby_remaining = max(
                    0.0,
                    self.lobby_remaining - delta_time,
                )
                if self.lobby_remaining <= 1e-6:
                    self.lobby_remaining = 0.0
                    network.close_admissions()

    def _update_status(self) -> None:
        if self._status is None:
            return
        if configuration.mode == "multiplayer" and not self.race_started:
            if self.lobby_remaining <= 0:
                self._status.text = "Get ready"
            elif self.game.network.state != "connected":
                self._status.text = (
                    f"Connecting to {configuration.endpoint}..."
                )
            else:
                self._status.text = (
                    "Lobby open - race starts in "
                    f"{self.lobby_remaining:0.0f}s"
                )
            return
        if not self.race_started:
            self._status.text = "Get ready"
            return
        player = self.participants.get(1)
        if player is None:
            self._status.text = "Waiting for a race slot"
            return
        if player.finished:
            self._status.text = (
                f"Finished in position {player.finish_position}"
            )
            return
        best = (
            "--"
            if player.best_lap <= 0
            else f"{player.best_lap:0.2f}s"
        )
        self._status.text = (
            f"Lap {min(player.laps_completed + 1, TOTAL_LAPS)}/"
            f"{TOTAL_LAPS}   Best {best}"
        )
