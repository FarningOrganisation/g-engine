from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from g import (
    CircleRenderer,
    GameObject,
    Game,
    MouseButton,
    NavigationAgent,
    NavigationObstacle,
    RectRenderer,
    Scene,
    TimerHandle,
    create_circle,
    create_rect,
)


BOARD_WIDTH = 30
BOARD_HEIGHT = 20
CELL_SIZE = 28
PLAYER_SPEED = 190.0
ENEMY_SPEED = 75.0
BULLET_SPEED = 470.0
FIRE_INTERVAL = 0.16
REPATH_INTERVAL = 0.35
WAVE_INTERVAL = 1.5

BACKGROUND_COLOR = (18, 21, 27)
WALL_COLOR = (69, 78, 91)
PLAYER_COLOR = (75, 196, 255)
GUN_COLOR = (220, 235, 242)
BULLET_COLOR = (255, 216, 92)
ENEMY_COLORS = ((238, 83, 83), (222, 130, 67), (142, 151, 68))


@dataclass
class Bullet:
    body: GameObject
    direction_x: float
    direction_y: float
    lifetime: float = 1.8


@dataclass
class Enemy:
    body: GameObject
    agent: NavigationAgent
    lives: int = 3


class ShooterScene(Scene):
    def __init__(self) -> None:
        super().__init__(
            bg_color=BACKGROUND_COLOR,
            grid_cell_size=CELL_SIZE,
        )
        self.walls: list[GameObject] = []
        self.enemies: list[Enemy] = []
        self.bullets: list[Bullet] = []
        self.wave = 0
        self._mouse_position = (0.0, 0.0)
        self._can_fire = True
        self._can_take_damage = True
        self._repath_timer: TimerHandle | None = None
        self._wave_timer: TimerHandle | None = None
        self.player_lives = 5
        self.game_over = False
        self.player: GameObject
        self.gun: GameObject

    def on_load(self) -> None:
        self.navigation.allow_diagonal = True
        self._create_walls()
        self.player = self.grid.add_circle(
            BOARD_WIDTH // 2,
            BOARD_HEIGHT // 2,
            PLAYER_COLOR,
            radius=10,
            z_index=3,
            collider=True,
        )
        self.gun = create_rect(15, 0, GUN_COLOR, 25, 7, z_index=4)
        self.player.add_child(self.gun)
        self._mouse_position = (self.player.x + CELL_SIZE, self.player.y)
        self._aim_at(*self._mouse_position)
        self._start_next_wave()
        self._repath_timer = self.time.every(
            REPATH_INTERVAL,
            self._repath_enemies,
        )

    def update(self, delta_time: float) -> None:
        if self.game_over:
            return
        self._move_player(delta_time)
        self._mouse_position = self.input.mouse_position
        self._aim_at(*self._mouse_position)
        if self.input.pressed(MouseButton.LEFT):
            self._fire()

        self._update_bullets(delta_time)
        self._check_enemy_contacts()
        self._update_waves(delta_time)

    def _create_walls(self) -> None:
        wall_cells = {
            *((5, y) for y in range(3, 16) if y not in (8, 9)),
            *((24, y) for y in range(4, 17) if y not in (11, 12)),
            *((x, 5) for x in range(10, 20) if x not in (14, 15)),
            *((x, 14) for x in range(10, 21) if x not in (16, 17)),
        }
        for grid_x, grid_y in wall_cells:
            wall = self.grid.add_rect(
                grid_x,
                grid_y,
                WALL_COLOR,
                CELL_SIZE - 2,
                CELL_SIZE - 2,
                z_index=1,
                collider=True,
            )
            wall.add_component(NavigationObstacle())
            self.walls.append(wall)

    def _create_enemies(self, count: int) -> None:
        for grid_x, grid_y in self._spawn_positions(count):
            x, y = self.grid.cell_center(grid_x, grid_y)
            body = create_rect(
                x,
                y,
                ENEMY_COLORS[0],
                21,
                21,
                z_index=2,
                collider=True,
            )
            agent = body.add_component(NavigationAgent(ENEMY_SPEED))
            self.add(body)
            self.enemies.append(Enemy(body, agent))

    def _spawn_positions(self, count: int) -> tuple[tuple[int, int], ...]:
        candidates = [
            *((1, y) for y in range(1, BOARD_HEIGHT - 1)),
            *((BOARD_WIDTH - 2, y) for y in range(1, BOARD_HEIGHT - 1)),
            *((x, 1) for x in range(2, BOARD_WIDTH - 2)),
            *((x, BOARD_HEIGHT - 2) for x in range(2, BOARD_WIDTH - 2)),
        ]
        if count > len(candidates):
            raise RuntimeError("not enough free cells to spawn this wave")
        offset = self.wave % len(candidates)
        return tuple(
            candidates[(offset + index * len(candidates) // count) % len(candidates)]
            for index in range(count)
        )

    def _start_next_wave(self) -> None:
        self.wave += 1
        enemy_count = self.wave * 2 - 1
        self._create_enemies(enemy_count)
        self._repath_enemies()
        print(f"Wave {self.wave}: {enemy_count} enemies")

    def _update_waves(self, delta_time: float) -> None:
        del delta_time
        if self.enemies:
            if self._wave_timer is not None:
                self._wave_timer.cancel()
                self._wave_timer = None
            return
        if self._wave_timer is None:
            print(f"Wave {self.wave} cleared")
            self._wave_timer = self.time.after(
                WAVE_INTERVAL,
                self._finish_wave_delay,
            )

    def _finish_wave_delay(self) -> None:
        self._wave_timer = None
        if not self.game_over:
            self._start_next_wave()

    def _move_player(self, delta_time: float) -> None:
        horizontal = self.input.get_axis("x")
        vertical = self.input.get_axis("y")
        length = hypot(horizontal, vertical)
        if length == 0:
            return

        movement_x = horizontal / length * PLAYER_SPEED * delta_time
        movement_y = vertical / length * PLAYER_SPEED * delta_time
        self._move_player_axis(movement_x, 0)
        self._move_player_axis(0, movement_y)

    def _move_player_axis(self, movement_x: float, movement_y: float) -> None:
        previous_position = self.player.position.copy()
        self.player.x += movement_x
        self.player.y += movement_y
        if self._player_outside_board() or self.player.collides_with_any(self.walls):
            self.player.position = previous_position

    def _player_outside_board(self) -> bool:
        radius = self.player.require_component(CircleRenderer).radius
        return (
            self.player.x - radius < 0
            or self.player.x + radius > self.pixel_width
            or self.player.y - radius < 0
            or self.player.y + radius > self.pixel_height
        )

    def _aim_at(self, x: float, y: float) -> None:
        direction_x = x - self.player.x
        direction_y = y - self.player.y
        if direction_x or direction_y:
            self.player.rotation = -degrees(atan2(direction_y, direction_x))

    def _fire(self) -> None:
        if not self._can_fire or self.game_over:
            return
        direction_x = self._mouse_position[0] - self.player.x
        direction_y = self._mouse_position[1] - self.player.y
        length = hypot(direction_x, direction_y)
        if length == 0:
            return
        direction_x /= length
        direction_y /= length
        bullet = create_circle(
            self.player.x + direction_x * 24,
            self.player.y + direction_y * 24,
            BULLET_COLOR,
            4,
            z_index=5,
            collider=True,
        )
        self.add(bullet)
        self.bullets.append(Bullet(bullet, direction_x, direction_y))
        self._can_fire = False
        self.time.after(
            FIRE_INTERVAL,
            self._enable_firing,
        )

    def _enable_firing(self) -> None:
        self._can_fire = True

    def _update_bullets(self, delta_time: float) -> None:
        for bullet in tuple(self.bullets):
            bullet.body.x += bullet.direction_x * BULLET_SPEED * delta_time
            bullet.body.y += bullet.direction_y * BULLET_SPEED * delta_time
            bullet.lifetime -= delta_time

            enemy = next(
                (
                    candidate
                    for candidate in self.enemies
                    if bullet.body.collides_with(candidate.body)
                ),
                None,
            )
            if enemy is not None:
                self._damage_enemy(enemy)
                self._remove_bullet(bullet)
            elif (
                bullet.lifetime <= 0
                or self._outside_board(bullet.body.x, bullet.body.y)
                or bullet.body.collides_with_any(self.walls)
            ):
                self._remove_bullet(bullet)

    def _damage_enemy(self, enemy: Enemy) -> None:
        enemy.lives -= 1
        if enemy.lives <= 0:
            enemy.agent.stop()
            self.remove(enemy.body)
            self.enemies.remove(enemy)
            return
        enemy.body.require_component(RectRenderer).color = ENEMY_COLORS[
            3 - enemy.lives
        ]

    def _remove_bullet(self, bullet: Bullet) -> None:
        self.remove(bullet.body)
        self.bullets.remove(bullet)

    def _repath_enemies(self) -> None:
        target = self.grid.world_to_cell(*self.player.position)
        for enemy in self.enemies:
            enemy.agent.move_to(target)

    def _check_enemy_contacts(self) -> None:
        if not self._can_take_damage:
            return
        if not any(enemy.body.collides_with(self.player) for enemy in self.enemies):
            return
        self.player_lives -= 1
        self._can_take_damage = False
        self.time.after(
            0.8,
            self._enable_contact_damage,
        )
        print(f"Player lives: {self.player_lives}")
        if self.player_lives <= 0:
            self.game_over = True
            if self._repath_timer is not None:
                self._repath_timer.cancel()
            if self._wave_timer is not None:
                self._wave_timer.cancel()
                self._wave_timer = None
            for enemy in self.enemies:
                enemy.agent.enabled = False
            print("Game over!")

    def _enable_contact_damage(self) -> None:
        self._can_take_damage = True

    def _outside_board(self, x: float, y: float) -> bool:
        return not (
            0 <= x <= self.pixel_width
            and 0 <= y <= self.pixel_height
        )


def main() -> None:
    game = Game.from_grid(
        BOARD_WIDTH,
        BOARD_HEIGHT,
        CELL_SIZE,
        BACKGROUND_COLOR,
    )
    game.scenes.add("shooter", ShooterScene())
    game.scenes.switch("shooter")
    print("Move with WASD or arrow keys. Aim and shoot with the mouse.")
    game.run(title="Pathfinding Shooter")


if __name__ == "__main__":
    main()
