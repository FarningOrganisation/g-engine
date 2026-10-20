from dataclasses import dataclass
from pathlib import Path
import sys


EXAMPLE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import g
from g import Collider, RectRenderer, TextRenderer


GAME_WIDTH = 1000
GAME_HEIGHT = 600
PADDLE_SPEED = 400
INITIAL_BALL_SPEED = 400
BALL_SPEED_INCREASE = 1.05
WINNING_SCORE = 11


@dataclass
class MatchState:
    player1_score: int = 0
    player2_score: int = 0
    last_winner: str | None = None
    started: bool = False
    game_over: bool = False


game = g.Game(GAME_WIDTH, GAME_HEIGHT, grid_cell_size=20)
window = g.Window("Ping Pong")
state = MatchState()

beep_sound = game.resources.load_sound(EXAMPLE_DIR / "beep.ogg")
peep_sound = game.resources.load_sound(EXAMPLE_DIR / "peep.ogg")
retro_font = game.resources.load_font(EXAMPLE_DIR / "retro_pong.ttf")



class Paddle(g.GameObject):
    def __init__(self, x: float, up_key: g.Key, down_key: g.Key) -> None:
        super().__init__(
            x,
            GAME_HEIGHT / 2,
            z_index=1,
        )
        self.add_component(
            RectRenderer(20, 100, (255, 255, 255))
        )
        self.add_component(Collider(width=20, height=100))
        self.up_key = up_key
        self.down_key = down_key

    def update(self, delta_time: float) -> None:
        if state.game_over:
            return
        direction = 0
        if game.input.pressed(self.up_key):
            direction = 1
        elif game.input.pressed(self.down_key):
            direction = -1

        self.y += direction * PADDLE_SPEED * delta_time
        height = self.require_component(RectRenderer).height
        self.y = min(game.pixel_height - height / 2, self.y)
        self.y = max(height / 2, self.y)


player1 = game.add(Paddle(40, g.Key.W, g.Key.S))
player2 = game.add(
    Paddle(game.pixel_width - 40, g.Key.UP, g.Key.DOWN)
)
ball = game.add_circle(
    GAME_WIDTH / 2,
    GAME_HEIGHT / 2,
    (255, 255, 255),
    10,
    z_index=1,
    collider=True,
)
center_line = game.add_line(
    (game.pixel_width / 2, 0),
    (game.pixel_width / 2, game.pixel_height),
    (150, 150, 150),
    thickness=8,
)

player1_score_text = game.add_text(
    200,
    game.pixel_height - 40,
    "0",
    retro_font,
    50,
)
player2_score_text = game.add_text(
    game.pixel_width - 200,
    game.pixel_height - 40,
    "0",
    retro_font,
    50,
)
game_over_text = game.add_text(
    game.pixel_width / 2,
    game.pixel_height / 2 + 200,
    "",
    retro_font,
    75,
)
restart_text = game.add_text(
    game.pixel_width / 2,
    game.pixel_height / 2 - 100,
    "Press R to restart...",
    retro_font,
    40,
)
game_over_renderer = game_over_text.require_component(TextRenderer)
restart_renderer = restart_text.require_component(TextRenderer)
player1_score_renderer = player1_score_text.require_component(TextRenderer)
player2_score_renderer = player2_score_text.require_component(TextRenderer)
game_over_renderer.opacity = 0
restart_renderer.opacity = 0


@ball.on_collision
def bounce_from_paddle(contact: g.CollisionContact) -> None:
    other = contact.other
    if other is player1:
        ball.speed.x = abs(ball.speed.x) * BALL_SPEED_INCREASE
    elif other is player2:
        ball.speed.x = -abs(ball.speed.x) * BALL_SPEED_INCREASE
    else:
        return
    beep_sound.play()


def show_game_over() -> None:
    state.game_over = True
    player1_score_text.animate().to(
        0.5,
        x=game.pixel_width / 2 - 100,
        y=game.pixel_height / 2,
        font_size=75,
        easing=g.Ease.in_quad,
    ).start()
    player2_score_text.animate().to(
        0.5,
        x=game.pixel_width / 2 + 100,
        y=game.pixel_height / 2,
        font_size=75,
        easing=g.Ease.in_quad,
    ).start()
    center_line.animate().to(0.5, opacity=0).start()

    game_over_renderer.text = f"{state.last_winner} has won!"
    game_over_text.animate().to(0.5, opacity=255).start()
    restart_text.animate().to(0.5, opacity=255).start()


def finish_restart(line: g.GameObject) -> None:
    del line
    state.game_over = False


def restart_game() -> None:
    state.player1_score = 0
    state.player2_score = 0
    state.last_winner = None
    state.started = False
    player1_score_renderer.text = "0"
    player2_score_renderer.text = "0"

    player1_score_text.animate().to(
        0.5,
        x=200,
        y=game.pixel_height - 40,
        font_size=50,
        easing=g.Ease.in_quad,
    ).start()
    player2_score_text.animate().to(
        0.5,
        x=game.pixel_width - 200,
        y=game.pixel_height - 40,
        font_size=50,
        easing=g.Ease.in_quad,
    ).start()
    center_line.animate().to(
        0.5,
        opacity=255,
    ).start(on_complete=finish_restart)
    game_over_text.animate().to(0.5, opacity=0).start()
    restart_text.animate().to(0.5, opacity=0).start()
    reset_ball(play_sound=False)


def finish_ball_reset(ball_object: g.GameObject) -> None:
    del ball_object
    state.started = False


def reset_ball(*, play_sound: bool = True) -> None:
    if play_sound:
        peep_sound.play()
    ball.position = (game.pixel_width / 2, game.pixel_height / 2)
    ball.speed = g.Vec2()
    ball.animate() \
        .to(0.1, opacity=0) \
        .to(0.1, opacity=255) \
        .repeat(3) \
        .call(finish_ball_reset) \
        .start()

    if (
        state.player1_score >= WINNING_SCORE
        or state.player2_score >= WINNING_SCORE
    ):
        show_game_over()


def launch_ball() -> None:
    direction = (
        1
        if state.last_winner in (None, "P2")
        else -1
    )
    ball.speed = g.Vec2(direction, direction) * INITIAL_BALL_SPEED
    state.started = True


def update(delta_time: float) -> None:
    del delta_time
    if (
        not state.started
        and not state.game_over
        and game.input.just_pressed(g.Key.SPACE)
    ):
        launch_ball()

    if state.game_over and game.input.just_pressed(g.Key.R):
        restart_game()

    radius = ball.require_component(g.CircleRenderer).radius
    if ball.y + radius > game.pixel_height:
        ball.y = game.pixel_height - radius
        ball.speed.y = -abs(ball.speed.y)
    elif ball.y - radius < 0:
        ball.y = radius
        ball.speed.y = abs(ball.speed.y)

    if ball.x + radius < 0:
        state.player2_score += 1
        player2_score_renderer.text = str(state.player2_score)
        state.last_winner = "P2"
        reset_ball()
    elif ball.x - radius > game.pixel_width:
        state.player1_score += 1
        player1_score_renderer.text = str(state.player1_score)
        state.last_winner = "P1"
        reset_ball()


window.show_game(game)
window.run(update)
