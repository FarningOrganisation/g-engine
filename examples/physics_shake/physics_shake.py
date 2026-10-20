from __future__ import annotations

from math import exp
from pathlib import Path
import sys
from time import monotonic

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from g import (
    BodyType,
    Game,
    PhysicsBody,
    Window,
    create_circle,
    create_rect,
)


BOARD_WIDTH = 22
BOARD_HEIGHT = 15
CELL_SIZE = 32
MAX_WINDOW_DELTA = 45
MAX_EVENT_INTERVAL = 0.1
MAX_WINDOW_SPEED = 2400.0
MAX_WINDOW_ACCELERATION = 30000.0
VELOCITY_RESPONSE = 18.0
VELOCITY_TRANSFER = 2.25
ACCELERATION_TRANSFER = 0.45
ANGULAR_ACCELERATION_TRANSFER = 0.06
BODY_COUPLING = 9.0

BACKGROUND_COLOR = (17, 21, 29)
WALL_COLOR = (76, 88, 108)
BALL_COLORS = (
    (77, 201, 255),
    (255, 183, 77),
    (239, 94, 121),
    (132, 220, 126),
)
BOX_COLORS = (
    (255, 218, 105),
    (157, 126, 255),
    (87, 218, 190),
    (255, 132, 86),
)


class WindowShakeDemo:
    def __init__(self) -> None:
        self.game = Game.from_grid(
            BOARD_WIDTH,
            BOARD_HEIGHT,
            CELL_SIZE,
            BACKGROUND_COLOR,
        )
        self.game.physics.gravity = (0, -650)
        self.game.physics.damping = 0.997
        self.bodies: list[PhysicsBody] = []
        self._last_window_move_time: float | None = None
        self._window_velocity = (0.0, 0.0)
        self._raw_window_velocity = (0.0, 0.0)
        self._create_container()
        self._create_objects()

    def _create_container(self) -> None:
        width = self.game.pixel_width
        height = self.game.pixel_height
        thickness = 28
        self._add_wall(width / 2, thickness / 2, width, thickness)
        self._add_wall(width / 2, height - thickness / 2, width, thickness)
        self._add_wall(thickness / 2, height / 2, thickness, height)
        self._add_wall(width - thickness / 2, height / 2, thickness, height)

    def _add_wall(
        self, x: float, y: float, width: float, height: float
    ) -> None:
        wall = create_rect(x, y, WALL_COLOR, width, height, z_index=1)
        wall.add_physics(BodyType.STATIC, friction=0.9, elasticity=0.25)
        self.game.add(wall)

    def _create_objects(self) -> None:
        for index in range(16):
            column = index % 8
            row = index // 8
            x = 115 + column * 66
            y = 190 + row * 70
            if index % 2:
                game_object = create_rect(
                    x,
                    y,
                    BOX_COLORS[index % len(BOX_COLORS)],
                    34,
                    34,
                    angle=index * 7,
                    z_index=2,
                )
                body = game_object.add_physics(
                    density=0.008,
                    friction=0.7,
                    elasticity=0.45,
                )
            else:
                game_object = create_circle(
                    x,
                    y,
                    BALL_COLORS[index % len(BALL_COLORS)],
                    18,
                    z_index=2,
                )
                body = game_object.add_physics(
                    density=0.008,
                    friction=0.55,
                    elasticity=0.72,
                )
            self.game.add(game_object)
            self.bodies.append(body)

    def on_window_move(
        self,
        _x: int,
        _y: int,
        delta_x: int,
        delta_y: int,
    ) -> None:
        current_time = monotonic()
        if self._last_window_move_time is None:
            delta_time = 1 / 60
        else:
            elapsed = current_time - self._last_window_move_time
            if elapsed > MAX_EVENT_INTERVAL:
                self._window_velocity = (0.0, 0.0)
                self._raw_window_velocity = (0.0, 0.0)
            delta_time = max(1 / 240, min(MAX_EVENT_INTERVAL, elapsed))
        self._last_window_move_time = current_time
        self._apply_window_motion(delta_x, delta_y, delta_time)

    def _apply_window_motion(
        self,
        delta_x: int,
        delta_y: int,
        delta_time: float,
    ) -> None:
        delta_x = max(-MAX_WINDOW_DELTA, min(MAX_WINDOW_DELTA, delta_x))
        delta_y = max(-MAX_WINDOW_DELTA, min(MAX_WINDOW_DELTA, delta_y))
        raw_velocity_x = max(
            -MAX_WINDOW_SPEED,
            min(MAX_WINDOW_SPEED, delta_x / delta_time),
        )
        raw_velocity_y = max(
            -MAX_WINDOW_SPEED,
            min(MAX_WINDOW_SPEED, -delta_y / delta_time),
        )
        previous_raw_x, previous_raw_y = self._raw_window_velocity
        acceleration_x = max(
            -MAX_WINDOW_ACCELERATION,
            min(
                MAX_WINDOW_ACCELERATION,
                (raw_velocity_x - previous_raw_x) / delta_time,
            ),
        )
        acceleration_y = max(
            -MAX_WINDOW_ACCELERATION,
            min(
                MAX_WINDOW_ACCELERATION,
                (raw_velocity_y - previous_raw_y) / delta_time,
            ),
        )
        self._raw_window_velocity = raw_velocity_x, raw_velocity_y

        previous_x, previous_y = self._window_velocity
        response = 1 - exp(-VELOCITY_RESPONSE * delta_time)
        velocity_x = previous_x + (raw_velocity_x - previous_x) * response
        velocity_y = previous_y + (raw_velocity_y - previous_y) * response
        self._window_velocity = velocity_x, velocity_y

        coupling = 1 - exp(-BODY_COUPLING * delta_time)
        target_x = (velocity_x * 0.6 + raw_velocity_x * 0.4) * VELOCITY_TRANSFER
        target_y = (velocity_y * 0.6 + raw_velocity_y * 0.4) * VELOCITY_TRANSFER
        for index, body in enumerate(self.bodies):
            current_x, current_y = body.velocity
            body.velocity = (
                current_x
                + (target_x - current_x) * coupling
                + acceleration_x * delta_time * ACCELERATION_TRANSFER,
                current_y
                + (target_y - current_y) * coupling
                + acceleration_y * delta_time * ACCELERATION_TRANSFER,
            )
            body.angular_velocity += (
                acceleration_x
                * delta_time
                * ANGULAR_ACCELERATION_TRANSFER
                * (1 if index % 2 else -1)
            )


def main() -> None:
    demo = WindowShakeDemo()
    window = Window("Shake the Window")
    window.show_game(demo.game)
    window.on_move(demo.on_window_move)
    print("Drag the window by its title bar and shake it.")
    window.run()


if __name__ == "__main__":
    main()
