from __future__ import annotations

from pathlib import Path
from random import choice
import sys
from typing import TypeAlias

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from g import Game, GameObject, Key, RectRenderer, Window


GridPosition: TypeAlias = tuple[int, int]
Direction: TypeAlias = tuple[int, int]

BOARD_WIDTH = 20
BOARD_HEIGHT = 20
CELL_SIZE = 28
MOVE_INTERVAL = 0.12

BACKGROUND_COLOR = (18, 24, 20)
HEAD_COLOR = (158, 226, 76)
BODY_COLOR = (77, 166, 88)
FOOD_COLOR = (239, 91, 74)


class SnakeGame:
    def __init__(self) -> None:
        self.game = Game.from_grid(
            BOARD_WIDTH,
            BOARD_HEIGHT,
            CELL_SIZE,
            BACKGROUND_COLOR,
        )
        self._body: list[GridPosition] = []
        self._segments: list[GameObject] = []
        self._food: GameObject | None = None
        self._food_position: GridPosition | None = None
        self._direction: Direction = (1, 0)
        self._next_direction: Direction = self._direction
        self.game_over = False
        self.score = 0
        self.restart()

    def restart(self) -> None:
        for segment in self._segments:
            self.game.remove(segment)
        if self._food is not None:
            self.game.remove(self._food)

        center_x = BOARD_WIDTH // 2
        center_y = BOARD_HEIGHT // 2
        self._body = [
            (center_x, center_y),
            (center_x - 1, center_y),
            (center_x - 2, center_y),
        ]
        self._segments = [
            self.game.grid.add_rect(
                grid_x,
                grid_y,
                CELL_SIZE - 3,
                CELL_SIZE - 3,
                HEAD_COLOR if index == 0 else BODY_COLOR,
                z_index=1,
            )
            for index, (grid_x, grid_y) in enumerate(self._body)
        ]
        self._direction = (1, 0)
        self._next_direction = self._direction
        self.game_over = False
        self.score = 0
        self._food = None
        self._food_position = None
        self._spawn_food()

    def update(self, _delta_time: float) -> None:
        if self.game.input.just_pressed(Key.R):
            self.restart()
            return

        self._update_direction()
        if self.game_over:
            return

        self._direction = self._next_direction
        head_x, head_y = self._body[0]
        direction_x, direction_y = self._direction
        new_head = head_x + direction_x, head_y + direction_y
        eats_food = new_head == self._food_position
        occupied_positions = self._body if eats_food else self._body[:-1]

        if not self._inside_board(new_head) or new_head in occupied_positions:
            self.game_over = True
            print(f"Game over. Score: {self.score}. Press R to restart.")
            return

        if eats_food:
            self._body.insert(0, new_head)
            segment = self.game.grid.add_rect(
                *new_head,
                CELL_SIZE - 3,
                CELL_SIZE - 3,
                z_index=1,
                color=BODY_COLOR,
            )
            self._segments.insert(0, segment)
            self.score += 1
            if self._food is not None:
                self.game.remove(self._food)
                self._food = None
            self._spawn_food()
            print(f"Score: {self.score}")
        else:
            self._body.insert(0, new_head)
            self._body.pop()
            segment = self._segments.pop()
            self.game.grid.move(segment, *new_head)
            self._segments.insert(0, segment)

        self._segments[0].require_component(RectRenderer).color = HEAD_COLOR
        if len(self._segments) > 1:
            self._segments[1].require_component(RectRenderer).color = BODY_COLOR

    def _update_direction(self) -> None:
        directions: dict[Key, Direction] = {
            Key.LEFT: (-1, 0),
            Key.A: (-1, 0),
            Key.RIGHT: (1, 0),
            Key.D: (1, 0),
            Key.UP: (0, 1),
            Key.W: (0, 1),
            Key.DOWN: (0, -1),
            Key.S: (0, -1),
        }
        for key, direction in directions.items():
            if (
                self.game.input.just_pressed(key)
                and direction != (-self._direction[0], -self._direction[1])
            ):
                self._next_direction = direction
                return

    def _spawn_food(self) -> None:
        free_positions = [
            (grid_x, grid_y)
            for grid_y in range(BOARD_HEIGHT)
            for grid_x in range(BOARD_WIDTH)
            if (grid_x, grid_y) not in self._body
        ]
        if not free_positions:
            self.game_over = True
            self._food_position = None
            print(f"You won! Score: {self.score}")
            return

        self._food_position = choice(free_positions)
        self._food = self.game.grid.add_rect(
            *self._food_position,
            CELL_SIZE - 7,
            CELL_SIZE - 7,
            z_index=2,
            color=FOOD_COLOR,
        )

    @staticmethod
    def _inside_board(position: GridPosition) -> bool:
        grid_x, grid_y = position
        return 0 <= grid_x < BOARD_WIDTH and 0 <= grid_y < BOARD_HEIGHT


def main() -> None:
    snake = SnakeGame()
    window = Window("Snake")
    window.show_game(snake.game)
    print("Move with arrow keys or WASD. Press R to restart.")
    window.run(snake.update, MOVE_INTERVAL)


if __name__ == "__main__":
    main()
