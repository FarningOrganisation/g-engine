from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from g import (
    BodyType,
    Game,
    GameObject,
    Key,
    MouseButton,
    PhysicsBody,
    Window,
    create_circle,
    create_rect,
)


BOARD_WIDTH = 26
BOARD_HEIGHT = 18
CELL_SIZE = 30
BACKGROUND_COLOR = (20, 24, 32)
STATIC_COLOR = (79, 91, 111)
BALL_COLOR = (80, 195, 255)
BOX_COLOR = (255, 176, 76)


class PhysicsPlayground:
    def __init__(self) -> None:
        self.game = Game.from_grid(
            BOARD_WIDTH,
            BOARD_HEIGHT,
            CELL_SIZE,
            BACKGROUND_COLOR,
        )
        self.game.physics.gravity = (0, -900)
        self.game.physics.damping = 0.995
        self.dynamic_objects: list[tuple[GameObject, PhysicsBody]] = []

        self._create_level()
        self._spawn_initial_objects()

    def _create_level(self) -> None:
        width = self.game.pixel_width
        height = self.game.pixel_height
        self._add_static_box(width / 2, 18, width, 36)
        self._add_static_box(18, height / 2, 36, height)
        self._add_static_box(width - 18, height / 2, 36, height)
        self._add_static_box(width * 0.32, 170, 230, 24, angle=-14)
        self._add_static_box(width * 0.70, 285, 230, 24, angle=16)

    def _add_static_box(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        angle: float = 0,
    ) -> None:
        box = create_rect(
            x,
            y,
            STATIC_COLOR,
            width,
            height,
            angle=angle,
            z_index=1,
        )
        box.add_physics(BodyType.STATIC, friction=0.9)
        self.game.add(box)

    def _spawn_initial_objects(self) -> None:
        for index in range(5):
            self.spawn_ball(270 + index * 35, 440 + index * 8)
            self.spawn_box(480 + index * 32, 430 + index * 36)

    def spawn_ball(self, x: float, y: float) -> GameObject:
        ball = create_circle(x, y, BALL_COLOR, 13, z_index=2)
        body = ball.add_physics(
            density=0.01,
            friction=0.6,
            elasticity=0.65,
        )
        self.game.add(ball)
        self.dynamic_objects.append((ball, body))
        return ball

    def spawn_box(self, x: float, y: float) -> GameObject:
        box = create_rect(x, y, BOX_COLOR, 25, 25, z_index=2)
        body = box.add_physics(
            density=0.01,
            friction=0.8,
            elasticity=0.2,
        )
        self.game.add(box)
        self.dynamic_objects.append((box, body))
        return box

    def update(self, _delta_time: float) -> None:
        mouse_x, mouse_y = self.game.input.mouse_position
        if self.game.input.just_pressed(MouseButton.LEFT):
            self.spawn_ball(mouse_x, mouse_y)
        elif self.game.input.just_pressed(MouseButton.RIGHT):
            self.spawn_box(mouse_x, mouse_y)

        if self.game.input.just_pressed(Key.SPACE):
            for index, (_game_object, body) in enumerate(self.dynamic_objects):
                horizontal_impulse = -90 if index % 2 else 90
                body.apply_impulse((horizontal_impulse, 360))
        elif self.game.input.just_pressed(Key.R):
            self._reset_objects()

        for game_object, _body in tuple(self.dynamic_objects):
            if game_object.y < -100:
                self.game.remove(game_object)
                self.dynamic_objects.remove((game_object, _body))

    def _reset_objects(self) -> None:
        for game_object, _body in tuple(self.dynamic_objects):
            self.game.remove(game_object)
        self.dynamic_objects.clear()
        self._spawn_initial_objects()


def main() -> None:
    playground = PhysicsPlayground()
    window = Window("Physics Playground")
    window.show_game(playground.game)
    print("Left click: ball | Right click: box | Space: impulse | R: reset")
    window.run(playground.update)


if __name__ == "__main__":
    main()
