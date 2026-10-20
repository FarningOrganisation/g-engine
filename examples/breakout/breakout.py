from dataclasses import dataclass
from pathlib import Path
import sys


EXAMPLE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SPEED = 10


import g

W = 800
H = 500

breakout = g.Game(800, 500)
breakout.input.bind_axis("x", g.Key.A, g.Key.D)
breakout.input.bind_axis("x2", g.Key.LEFT, g.Key.RIGHT)

paddle = breakout.add_rect(
    100, 20, (255, 255, 255), 100, 20, collider=True
)

ball = breakout.add_circle(
    200, 30, (255, 255, 255), 10, collider=True
)
ball_released = False
ball_offset_x = 0
ball.speed = g.Vec2(0, 0)

# 600 -> 50 * 12
# 100-300 -> 0-20, 30-50, 70-90

LEVELS = [
    """
BBBBBBBBBBBB
BBBBBBBBBBBB
BBBBBBBBBBBB
BBBBBBBBBBBB
BBBBBBBBBBBB
BBBBBBBBBBBB
""",

"""
BBBBBBBBBBBB
BBBBBBBBBBBB
BBBBBEEBBBBB
BBBBBEEBBBBB
BBBBBBBBBBBB
BBBBBBBBBBBB
""",

"""
TTTTTTTTTTTT
BBBBBBBBBBBB
BBBBBEEBBBBB
BBBBBEEBBBBB
BBBBBBBBBBBB
TTTTTTTTTTTT
"""
]

blocks: list[g.GameObject] = []

def load_level(level: str):
    # clear all blocks
    for b in blocks:
        b.delete()
    blocks.clear()

    level_rows = level.strip().splitlines(keepends=False)[::-1]
    for y, row in enumerate(level_rows):
        for x, type in enumerate(row):
            block_color = (255, 255, 255)
            if type == "E":
                block_color = (255, 0, 0)
            block = breakout.add_rect(
                100 + 50 * x + 5 * x,
                300 + y * 20 + 5 * y,
                block_color,
                50,
                20,
                collider=True,
            )
            block.properties["type"] = type
            blocks.append(block)


load_level(LEVELS[1])       


def set_ball_paddle_speed():
    ball_speed_y = 1
    ball_speed_x = (ball.x - paddle.x) / (paddle.width / 2)
    ball.speed = g.Vec2(ball_speed_x, ball_speed_y).normalize() * 400

def update(delta_time):
    global ball_released, ball_offset_x

    x_change = breakout.input.get_axis("x")
    paddle.position.x += x_change * SPEED

    if paddle.left < 0:
        paddle.left = 0
    if paddle.right >= W:
        paddle.right = W

    if ball_released:
        for b in blocks:
            type = b.properties["type"]
            cc = ball.collides_with(b)
            if cc:
                if type == "B":
                    b.delete()
                    blocks.remove(b)
                ball.speed = ball.speed.reflect(cc.normal)

                if type == "E":
                    explosion_center = (b.x, b.y)
                    nearby = breakout.physics.overlap_circle(
                        explosion_center,
                        100,
                    )
                    for target in nearby:
                        if (
                            isinstance(target, g.GameObject)
                            and target in blocks
                        ):
                            target.delete()
                            blocks.remove(target)

                break

        if ball.collides_with(paddle):
            set_ball_paddle_speed()

        if ball.left < 0:
            ball.speed = ball.speed.reflect(g.Vec2(1, 0))
            ball.left = 0
        elif ball.right > W:
            ball.speed = ball.speed.reflect(g.Vec2(-1, 0))
            ball.right = W
        elif ball.top > H:
            ball.speed = ball.speed.reflect(g.Vec2(0, -1))
            ball.top = H
        elif ball.bottom < 0:
            def reset_ball():
                global ball_released
                ball_released = False
            breakout.time.after(1, reset_ball)


        

        
    else:
        if breakout.input.just_pressed(g.Key.SPACE):
            ball_released = True
            set_ball_paddle_speed()
            """
            0 -> 1
            0.5 -> 0.6
            1 -> 0.3
            1 + abs
            """
        x_ball_change = breakout.input.get_axis("x2") * 2
        ball_offset_x += x_ball_change
        ball.x = paddle.x + ball_offset_x
        if not ball.collides_with(paddle):
            ball_offset_x -= x_ball_change
        ball.bottom = paddle.top
        ball.x = paddle.x + ball_offset_x


breakout.run("Breakout", update=update, fullscreen=True)