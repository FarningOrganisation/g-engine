from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any


EXAMPLE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXAMPLE_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import g


W = 800
H = 500
PADDLE_SPEED = 600
BALL_SPEED = 400
MIN_REFLECTION_ANGLE = 20
STARTING_LIVES = 3
MENU_BALL_RADIUS = 8
MENU_BLOCK_WIDTH = 48
MENU_BLOCK_HEIGHT = 18
SAVE_PATH = EXAMPLE_DIR / "breakout_highscores.json"

MENU = "menu"
CHOOSER = "chooser"
INTRO = "intro"
PLAYING = "playing"
PAUSED = "paused"
CLEARED = "cleared"
GAME_OVER = "game_over"
FINISHED = "finished"

STAGES = [
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
""",
    """
B.B.B.B.B.B.
.B.B.B.B.B.B
BBTTBBBBTTBB
BBTTBBBBTTBB
.B.B.B.B.B.B
B.B.B.B.B.B.
""",
    """
BBBBE.EBBBBB
BBBEE.EEBBBB
BBBE.T.EBBBB
BBBE.T.EBBBB
BBBEE.EEBBBB
BBBBE.EBBBBB
""",
    """
TTTTBBBBTTTT
TBBBEETBBB.T
T.BBBBBBBB.T
T.BBBBBBBB.T
T.BBEEEEBB.T
TTTTBBBBTTTT
""",
    """
BTTBBBBBBTTB
BBTBBEEBBTBB
BBBTBBBBTBBB
BBBBTBBTBBBB
BBBBBTTEBBBB
BBBBBEEBBBBB
""",
    """
TETETETETETE
BBBBBBBBBBBB
B.T.B..B.T.B
BBBBEEEEBBBB
BBTTBBBBTTBB
BBBBBBBBBBBB
""",
]


def default_progress() -> dict[str, Any]:
    return {
        "version": 2,
        "campaign_started": False,
        "current_stage": 0,
        "unlocked_stage": 0,
        "lives": STARTING_LIVES,
        "best_times": {},
    }


def load_progress() -> dict[str, Any]:
    progress = default_progress()
    if not SAVE_PATH.exists():
        return progress
    try:
        stored = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
        save_version = int(stored.get("version", 1))
        current = int(stored.get("current_stage", 1))
        unlocked = int(stored.get("unlocked_stage", 1))
        lives_value = int(stored.get("lives", STARTING_LIVES))
        raw_times = stored.get("best_times", {})
        if not isinstance(raw_times, dict):
            raise ValueError("best_times must be an object")
        campaign_started = stored.get("campaign_started", True)
        if not isinstance(campaign_started, bool):
            raise ValueError("campaign_started must be a boolean")
        if save_version < 2:
            current -= 1
            unlocked -= 1
        progress["campaign_started"] = campaign_started
        progress["current_stage"] = max(
            0,
            min(len(STAGES) - 1, current),
        )
        progress["unlocked_stage"] = max(
            0,
            min(len(STAGES) - 1, unlocked),
        )
        progress["lives"] = max(1, lives_value)
        progress["best_times"] = {
            str(int(stage) - 1 if save_version < 2 else int(stage)): float(
                value
            )
            for stage, value in raw_times.items()
            if (
                0
                <= (int(stage) - 1 if save_version < 2 else int(stage))
                < len(STAGES)
                and float(value) > 0
            )
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"Could not load {SAVE_PATH.name}: {error}", file=sys.stderr)
    return progress


progress = load_progress()


def save_progress() -> None:
    temporary = SAVE_PATH.with_suffix(".tmp")
    try:
        temporary.write_text(
            json.dumps(progress, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(SAVE_PATH)
    except OSError as error:
        print(f"Could not save {SAVE_PATH.name}: {error}", file=sys.stderr)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def format_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    centiseconds = int((seconds % 1) * 100)
    return f"{minutes:02d}:{secs:02d}:{centiseconds:02d}"


breakout = g.Game(W, H)
breakout.resources.asset_root = EXAMPLE_DIR / "assets"
breakout.input.bind_axis("paddle", g.Key.A, g.Key.D)
breakout.input.bind_axis("aim", g.Key.LEFT, g.Key.RIGHT)

font = breakout.resources.load_font("./fonts/PressStart2P-Regular.ttf")
beep_sound = breakout.resources.load_sound("./sounds/beep.wav")
fail_sound = breakout.resources.load_sound("./sounds/fail.wav")
glitch_sound = breakout.resources.load_sound("./sounds/glitch.wav")
explosion_sound = breakout.resources.load_sound("./sounds/explosion.wav")
life_image = breakout.resources.load_image("./sprites/ball_live.png")

theme = g.UITheme(
    label=g.LabelStyle(
        font=font,
        font_size=12,
        color=(235, 240, 255),
    ),
    panel=g.PanelStyle(color=(10, 14, 24, 235)),
    button=g.ButtonStyle(
        background=(32, 43, 67),
        hover=(62, 84, 126),
        pressed=(22, 29, 46),
        disabled=(38, 38, 44),
        text_color=(255, 255, 255),
        font=font,
        font_size=11,
    ),
)
scene_gui = breakout.scenes.current.gui
if not isinstance(scene_gui, g.GUIManager):
    raise RuntimeError("Breakout menus require graphics presentation")
gui: g.GUIManager = scene_gui
gui.theme = theme

paddle = breakout.add_rect(
    W / 2,
    40,
    100,
    20,
    (255, 255, 255),
    collider=True,
)
ball = breakout.add_circle(
    W / 2,
    70,
    10,
    (255, 255, 255),
    collider=True,
)
ball.speed = g.Vec2(0, 0)

menu_ball = breakout.add_circle(
    175,
    155,
    MENU_BALL_RADIUS,
    (75, 90, 125, 115),
)
menu_ball_speed = g.Vec2(135, 105)
menu_blocks: list[g.GameObject] = []
for menu_row in range(3):
    for menu_column in range(9):
        menu_blocks.append(
            breakout.add_rect(
                176 + menu_column * 56,
                315 + menu_row * 27,
                MENU_BLOCK_WIDTH,
                MENU_BLOCK_HEIGHT,
                (48, 60, 86, 105),
            )
        )
menu_reset_time = 0.0

hud = gui.default.root
if not isinstance(hud, g.CanvasLayout):
    raise RuntimeError("Breakout HUD requires a canvas layout")
time_label = hud.add_at(
    g.Label("00:00:00", font=font, font_size=11, width=140, height=24),
    W - 155,
    H - 34,
)
stage_label = hud.add_at(
    g.Label("", font=font, font_size=22, width=560, height=70),
    120,
    235,
)
instruction_label = hud.add_at(
    g.Label("", font=font, font_size=10, width=680, height=36),
    60,
    115,
)
best_label = hud.add_at(
    g.Label("", font=font, font_size=11, width=500, height=30),
    150,
    190,
)
pause_hint = hud.add_at(
    g.Label(
        "ESC / P: Pause",
        font=font,
        font_size=8,
        color=(150, 160, 180),
        width=150,
        height=20,
    ),
    W - 165,
    8,
)

state = MENU
current_stage = int(progress["current_stage"])
lives = int(progress["lives"])
stage_time = 0.0
intro_time = 0.0
intro_awards_life = False
blocks_animated = False
single_stage_mode = False
ball_released = False
ball_reset_pending = False
ball_offset_x = 0.0
new_best = False
blocks: list[g.GameObject] = []


def set_gameplay_visible(visible: bool) -> None:
    paddle.visible = visible
    ball.visible = visible
    for block in blocks:
        block.visible = visible


def set_menu_background_visible(visible: bool) -> None:
    menu_ball.visible = visible
    for menu_block in menu_blocks:
        menu_block.visible = visible


def clear_blocks() -> None:
    for block in tuple(blocks):
        block.delete()
    blocks.clear()


def show_hud() -> None:
    if gui.current.name != "HUD":
        gui.replace("HUD", transition=g.UIFade(0.2))


def show_main_menu() -> None:
    global state, ball_reset_pending
    state = MENU
    ball_reset_pending = False
    ball.speed = g.Vec2(0, 0)
    set_gameplay_visible(False)
    set_menu_background_visible(True)
    refresh_main_menu()
    if gui.current.name == "Pause":
        gui.pop()
    if gui.current.name != "Main Menu":
        gui.replace("Main Menu", transition=g.UIFade(0.25))


def start_campaign(*, reset: bool) -> None:
    global lives, single_stage_mode
    if not reset and not bool(progress["campaign_started"]):
        return
    single_stage_mode = False
    if reset:
        progress["campaign_started"] = True
        progress["current_stage"] = 0
        progress["unlocked_stage"] = 0
        progress["lives"] = STARTING_LIVES
    lives = max(1, int(progress["lives"]))
    set_menu_background_visible(False)
    save_progress()
    start_stage(int(progress["current_stage"]))


def show_stage_chooser() -> None:
    global state
    state = CHOOSER
    set_gameplay_visible(False)
    set_menu_background_visible(False)
    refresh_stage_buttons()
    if gui.current.name != "Stage Chooser":
        gui.replace("Stage Chooser", transition=g.UIFade(0.25))


def play_selected_stage(stage_number: int) -> None:
    global single_stage_mode, lives
    if stage_number > int(progress["unlocked_stage"]):
        return
    single_stage_mode = True
    lives = STARTING_LIVES
    start_stage(stage_number)


continue_button = g.Button(
    "Continue",
    lambda: start_campaign(reset=False),
    width=260,
)
main_menu = g.VBox(
    g.Label("BREAKOUT", font=font, font_size=32),
    continue_button,
    g.Button("New Game", lambda: start_campaign(reset=True), width=260),
    g.Button("Choose Stage", show_stage_chooser, width=260),
    spacing=14,
    padding=24,
)
gui.register(
    "Main Menu",
    g.UIView(
        g.AnchorLayout(main_menu),
        transparent=False,
        modal=True,
        pauses_scene=True,
        theme=theme,
    ),
)


def refresh_main_menu() -> None:
    continue_button.enabled = bool(progress["campaign_started"])


preview_button_style = g.ButtonStyle(
    background=(0, 0, 0, 0),
    hover=(100, 140, 210, 55),
    pressed=(70, 100, 160, 90),
    disabled=(8, 8, 12, 175),
    text_color=(255, 255, 255),
    font=font,
    font_size=9,
)
stage_cards: list[g.Panel] = []
stage_buttons: list[g.Button] = []
stage_title_labels: list[g.Label] = []
stage_best_labels: list[g.Label] = []
for stage_number, level in enumerate(STAGES):
    card = g.Panel(
        color=(22, 29, 46, 245),
        width=340,
        height=76,
    )
    title = card.add_at(
        g.Label(
            f"STAGE {stage_number}",
            font=font,
            font_size=11,
            align="left",
            width=135,
            height=22,
        ),
        12,
        45,
    )
    best = card.add_at(
        g.Label(
            "Best --:--:--",
            font=font,
            font_size=8,
            align="left",
            width=140,
            height=18,
        ),
        12,
        17,
    )
    for row_index, row in enumerate(level.strip().splitlines()[::-1]):
        for column, block_type in enumerate(row):
            if block_type == ".":
                continue
            preview_color = (230, 235, 245, 230)
            if block_type == "E":
                preview_color = (255, 70, 70, 230)
            elif block_type == "T":
                preview_color = (105, 115, 145, 230)
            card.add_at(
                g.Panel(
                    color=preview_color,
                    width=11,
                    height=6,
                ),
                166 + column * 13,
                16 + row_index * 8,
            )
    button = card.add_at(
        g.Button(
            "",
            lambda selected=stage_number: play_selected_stage(selected),
            style=preview_button_style,
            width=340,
            height=76,
        ),
        0,
        0,
    )
    button.name = f"Stage {stage_number}"
    stage_cards.append(card)
    stage_buttons.append(button)
    stage_title_labels.append(title)
    stage_best_labels.append(best)

stage_grid = g.GridLayout(
    *stage_cards,
    columns=2,
    row_spacing=9,
    column_spacing=12,
)
chooser_layout = g.VBox(
    g.Label("CHOOSE STAGE", font=font, font_size=22),
    stage_grid,
    g.Button("Back", show_main_menu, width=220, height=38),
    spacing=10,
    padding=12,
)
gui.register(
    "Stage Chooser",
    g.UIView(
        g.AnchorLayout(chooser_layout),
        transparent=False,
        modal=True,
        pauses_scene=True,
        theme=theme,
    ),
)


def resume_game() -> None:
    global state
    if gui.current.name == "Pause":
        gui.pop()
    state = PLAYING


def restart_after_game_over() -> None:
    global lives
    lives = STARTING_LIVES
    if single_stage_mode:
        start_stage(current_stage)
        return
    progress["campaign_started"] = True
    progress["current_stage"] = 0
    progress["lives"] = lives
    save_progress()
    start_stage(0)


pause_menu = g.VBox(
    g.Label("PAUSED", font=font, font_size=24),
    g.Button("Continue", resume_game, width=260),
    g.Button("Back to Menu", show_main_menu, width=260),
    spacing=16,
    padding=24,
)
gui.register(
    "Pause",
    g.UIView(
        g.AnchorLayout(
            g.Panel(
                color=(8, 12, 22, 235),
                width=360,
                height=250,
            )
        ),
        transparent=True,
        modal=True,
        pauses_scene=True,
        theme=theme,
    ),
)
pause_panel = gui.get("Pause").root.children[0]
if not isinstance(pause_panel, g.Panel):
    raise RuntimeError("Breakout pause menu requires a panel")
pause_panel.add_at(pause_menu, 50, 25)

game_over_menu = g.VBox(
    g.Label(
        "GAME OVER",
        font=font,
        font_size=28,
        color=(255, 80, 80),
        width=352,
        height=50,
    ),
    g.Button("Restart", restart_after_game_over, width=300),
    g.Button("Back to Menu", show_main_menu, width=300),
    spacing=16,
    padding=24,
    width=400,
    height=240,
)
gui.register(
    "Game Over",
    g.UIView(
        g.AnchorLayout(
            g.Panel(
                color=(8, 12, 22, 240),
                width=480,
                height=300,
            )
        ),
        transparent=True,
        modal=True,
        pauses_scene=True,
        theme=theme,
    ),
)
game_over_panel = gui.get("Game Over").root.children[0]
if not isinstance(game_over_panel, g.Panel):
    raise RuntimeError("Breakout game-over menu requires a panel")
game_over_panel.add_at(game_over_menu, 40, 30)


def refresh_stage_buttons() -> None:
    unlocked = int(progress["unlocked_stage"])
    best_times = progress["best_times"]
    for stage_number, button in enumerate(stage_buttons):
        best = best_times.get(str(stage_number))
        best_text = "--:--:--" if best is None else format_time(float(best))
        title = stage_title_labels[stage_number]
        title.text = (
            f"STAGE {stage_number}"
            if stage_number <= unlocked
            else f"STAGE {stage_number}  LOCKED"
        )
        title.color = (
            (255, 255, 255)
            if stage_number <= unlocked
            else (125, 130, 145)
        )
        stage_best_labels[stage_number].text = f"Best {best_text}"
        button.enabled = stage_number <= unlocked


def reset_ball_on_paddle() -> None:
    global ball_released, ball_offset_x
    ball_released = False
    ball_offset_x = 0
    ball.speed = g.Vec2(0, 0)
    ball.x = paddle.x
    ball.bottom = paddle.top


def create_stage_blocks(stage_number: int) -> None:
    clear_blocks()
    rows = STAGES[stage_number].strip().splitlines()[::-1]
    for row_index, row in enumerate(rows):
        for column, block_type in enumerate(row):
            if block_type == ".":
                continue
            color = (255, 255, 255)
            if block_type == "E":
                color = (255, 70, 70)
            elif block_type == "T":
                color = (105, 115, 145)
            block = breakout.add_rect(
                97.5 + 55 * column,
                300 + 25 * row_index,
                50,
                20,
                color,
                collider=True,
            )
            block.properties["type"] = block_type
            block.properties["count"] = 3 if block_type == "T" else 1
            block.scale_x = 0
            block.scale_y = 0
            blocks.append(block)


def animate_stage_blocks() -> None:
    global blocks_animated
    if blocks_animated:
        return
    blocks_animated = True
    for index, block in enumerate(blocks):
        block.animate().wait(index * 0.012).to(
            0.28,
            g.Ease.out_quad,
            scale_x=1,
            scale_y=1,
        ).start()


def start_stage(stage_number: int, *, award_life: bool = False) -> None:
    global state, current_stage, stage_time, intro_time
    global intro_awards_life, blocks_animated, ball_reset_pending
    global lives, new_best
    current_stage = max(0, min(len(STAGES) - 1, stage_number))
    if award_life:
        lives += 1
    if not single_stage_mode:
        progress["current_stage"] = current_stage
        progress["lives"] = lives
        save_progress()
    create_stage_blocks(current_stage)
    paddle.position = (W / 2, 40)
    reset_ball_on_paddle()
    ball_reset_pending = False
    stage_time = 0
    intro_time = 0
    intro_awards_life = award_life
    blocks_animated = False
    new_best = False
    state = INTRO
    set_menu_background_visible(False)
    set_gameplay_visible(True)
    show_hud()


def record_best_time() -> None:
    global new_best
    best_times = progress["best_times"]
    previous = best_times.get(str(current_stage))
    new_best = previous is None or stage_time < float(previous)
    if new_best:
        best_times[str(current_stage)] = round(stage_time, 3)
    if not single_stage_mode:
        progress["unlocked_stage"] = max(
            int(progress["unlocked_stage"]),
            min(len(STAGES) - 1, current_stage + 1),
        )
        progress["current_stage"] = min(
            len(STAGES) - 1,
            current_stage + 1,
        )
        progress["lives"] = lives
    save_progress()


def complete_stage() -> None:
    global state, ball_released
    if state != PLAYING:
        return
    ball_released = False
    ball.speed = g.Vec2(0, 0)
    record_best_time()
    campaign_finished = (
        not single_stage_mode and current_stage == len(STAGES) - 1
    )
    if campaign_finished:
        progress["campaign_started"] = False
        save_progress()
    state = FINISHED if campaign_finished else CLEARED


def pause_game() -> None:
    global state
    if state != PLAYING:
        return
    state = PAUSED
    gui.push("Pause", transition=g.UIFade(0.15))


def set_ball_paddle_speed() -> None:
    ball_speed_x = (ball.x - paddle.x) / (paddle.width / 2)
    ball.speed = g.Vec2(ball_speed_x, 1).normalize() * BALL_SPEED


def reflect_ball(normal: g.Vec2 | tuple[float, float]) -> None:
    normal_vector = (
        normal if isinstance(normal, g.Vec2) else g.Vec2(*normal)
    )
    reflected = ball.speed.reflect(normal_vector)
    speed = reflected.length()
    minimum_y_speed = speed * math.sin(
        math.radians(MIN_REFLECTION_ANGLE)
    )
    if speed == 0 or abs(reflected.y) >= minimum_y_speed:
        ball.speed = reflected
        return
    y_direction = reflected.y or normal_vector.y or 1
    x_direction = reflected.x or 1
    ball.speed = g.Vec2(
        math.copysign(
            math.sqrt(speed**2 - minimum_y_speed**2),
            x_direction,
        ),
        math.copysign(minimum_y_speed, y_direction),
    )


def handle_block_collisions() -> None:
    for block in tuple(blocks):
        block_type = str(block.properties["type"])
        contact = ball.collides_with(block)
        if contact is None:
            continue
        beep_sound.play()
        reflect_ball(contact.normal)
        if block_type == "B":
            block.delete()
            blocks.remove(block)
        elif block_type == "E":
            explosion_sound.play()
            nearby = breakout.physics.overlap_circle((block.x, block.y), 100)
            for target in nearby:
                if isinstance(target, g.GameObject) and target in blocks:
                    target.delete()
                    blocks.remove(target)
        elif block_type == "T":
            count = int(block.properties["count"]) - 1
            block.properties["count"] = count
            if count == 0:
                blocks.remove(block)
                block.delete()
            else:
                blue = 250 - 45 * count
                block.animate().to(
                    0.2,
                    color=(180, 190, blue),
                ).start()
        break


def lose_life() -> None:
    global ball_reset_pending
    if ball_reset_pending:
        return
    ball_reset_pending = True
    glitch_sound.play()

    def reset() -> None:
        global ball_reset_pending, lives, state
        if state not in {PLAYING, PAUSED}:
            ball_reset_pending = False
            return
        ball_reset_pending = False
        lives = max(0, lives - 1)
        if not single_stage_mode:
            progress["lives"] = max(1, lives)
            save_progress()
        fail_sound.play()
        if lives == 0:
            ball.visible = False
            state = GAME_OVER
            gui.replace("Game Over", transition=g.UIFade(0.25))
        else:
            reset_ball_on_paddle()

    breakout.time.after(0.8, reset)


def update_hud() -> None:
    time_label.text = format_time(stage_time)
    pause_hint.visible = state in {INTRO, PLAYING}
    stage_label.color = (255, 255, 255)
    best_label.color = (255, 255, 255)
    best_label.text = ""
    if state == INTRO:
        stage_label.text = f"STAGE {current_stage}"
        instruction_label.text = (
            "+1 LIFE" if intro_awards_life else "GET READY"
        )
    elif state == PLAYING:
        stage_label.text = ""
        instruction_label.text = (
            "" if ball_released
            else "A / D MOVE   ARROWS AIM   SPACE LAUNCH"
        )
    elif state == CLEARED:
        stage_label.text = "STAGE CLEARED"
        instruction_label.text = (
            "PRESS SPACE TO REPLAY"
            if single_stage_mode
            else "PRESS SPACE FOR THE NEXT STAGE"
        )
        best_label.text = (
            f"NEW BEST  {format_time(stage_time)}"
            if new_best
            else f"BEST  {format_time(float(progress['best_times'][str(current_stage)]))}"
        )
        if new_best:
            best_label.color = (255, 220, 40)
    elif state == GAME_OVER:
        stage_label.text = "GAME OVER"
        instruction_label.text = ""
    elif state == FINISHED:
        stage_label.text = "GAME FINISHED!"
        instruction_label.text = "PRESS SPACE TO RETURN TO THE MENU"


def update_playing(delta_time: float) -> None:
    global stage_time, ball_offset_x, ball_released
    stage_time += delta_time
    paddle.x += (
        breakout.input.get_axis("paddle")
        * PADDLE_SPEED
        * delta_time
    )
    paddle.left = max(0, paddle.left)
    paddle.right = min(W, paddle.right)

    if not ball_released:
        if breakout.input.key_just_pressed(g.Key.SPACE):
            ball_released = True
            set_ball_paddle_speed()
        change = breakout.input.get_axis("aim") * 180 * delta_time
        ball_offset_x += change
        ball.x = paddle.x + ball_offset_x
        if not ball.collides_with(paddle):
            ball_offset_x -= change
        ball.bottom = paddle.top
        ball.x = paddle.x + ball_offset_x
        return

    handle_block_collisions()
    if ball.collides_with(paddle):
        set_ball_paddle_speed()
        beep_sound.play()
    if ball.left < 0:
        reflect_ball(g.Vec2(1, 0))
        ball.left = 0
    elif ball.right > W:
        reflect_ball(g.Vec2(-1, 0))
        ball.right = W
    elif ball.top > H:
        reflect_ball(g.Vec2(0, -1))
        ball.top = H
    elif ball.bottom < 0:
        lose_life()
    if not blocks:
        complete_stage()


def update_menu_background(delta_time: float) -> None:
    global menu_ball_speed, menu_reset_time
    menu_ball.x += menu_ball_speed.x * delta_time
    menu_ball.y += menu_ball_speed.y * delta_time

    radius = MENU_BALL_RADIUS
    if menu_ball.x - radius <= 0:
        menu_ball.x = radius
        menu_ball_speed = g.Vec2(abs(menu_ball_speed.x), menu_ball_speed.y)
    elif menu_ball.x + radius >= W:
        menu_ball.x = W - radius
        menu_ball_speed = g.Vec2(-abs(menu_ball_speed.x), menu_ball_speed.y)
    if menu_ball.y - radius <= 0:
        menu_ball.y = radius
        menu_ball_speed = g.Vec2(menu_ball_speed.x, abs(menu_ball_speed.y))
    elif menu_ball.y + radius >= H:
        menu_ball.y = H - radius
        menu_ball_speed = g.Vec2(menu_ball_speed.x, -abs(menu_ball_speed.y))

    for menu_block in menu_blocks:
        if not menu_block.visible:
            continue
        half_width = MENU_BLOCK_WIDTH / 2
        half_height = MENU_BLOCK_HEIGHT / 2
        nearest_x = max(
            menu_block.x - half_width,
            min(menu_ball.x, menu_block.x + half_width),
        )
        nearest_y = max(
            menu_block.y - half_height,
            min(menu_ball.y, menu_block.y + half_height),
        )
        delta_x = menu_ball.x - nearest_x
        delta_y = menu_ball.y - nearest_y
        if delta_x**2 + delta_y**2 > radius**2:
            continue
        menu_block.visible = False
        overlap_x = half_width + radius - abs(
            menu_ball.x - menu_block.x
        )
        overlap_y = half_height + radius - abs(
            menu_ball.y - menu_block.y
        )
        if overlap_x < overlap_y:
            direction = 1 if menu_ball.x >= menu_block.x else -1
            menu_ball.x = menu_block.x + direction * (
                half_width + radius
            )
            menu_ball_speed = g.Vec2(
                direction * abs(menu_ball_speed.x),
                menu_ball_speed.y,
            )
        else:
            direction = 1 if menu_ball.y >= menu_block.y else -1
            menu_ball.y = menu_block.y + direction * (
                half_height + radius
            )
            menu_ball_speed = g.Vec2(
                menu_ball_speed.x,
                direction * abs(menu_ball_speed.y),
            )
        break

    if any(menu_block.visible for menu_block in menu_blocks):
        menu_reset_time = 0
    else:
        menu_reset_time += delta_time
        if menu_reset_time >= 0.8:
            for menu_block in menu_blocks:
                menu_block.visible = True
            menu_reset_time = 0


def update(delta_time: float) -> None:
    global intro_time, state, lives
    if state == MENU:
        update_menu_background(delta_time)
    elif state == CHOOSER and gui.current.name == "Main Menu":
        state = MENU
        set_menu_background_visible(True)
    elif state == INTRO:
        intro_time += delta_time
        if intro_time >= 0.6:
            animate_stage_blocks()
        if intro_time >= 1.65:
            state = PLAYING
    elif state == PLAYING:
        if (
            breakout.input.key_just_pressed(g.Key.ESCAPE)
            or breakout.input.key_just_pressed(g.Key.P)
        ):
            pause_game()
        else:
            update_playing(delta_time)
    elif state == PAUSED:
        if gui.current.name != "Pause":
            state = PLAYING
        elif breakout.input.key_just_pressed(g.Key.P):
            resume_game()
    elif state == CLEARED and breakout.input.key_just_pressed(g.Key.SPACE):
        if single_stage_mode:
            start_stage(current_stage)
        else:
            start_stage(current_stage + 1, award_life=True)
    elif state == GAME_OVER and breakout.input.key_just_pressed(g.Key.SPACE):
        restart_after_game_over()
    elif state == FINISHED and breakout.input.key_just_pressed(g.Key.SPACE):
        show_main_menu()
    update_hud()


def draw() -> None:
    if state in {INTRO, PLAYING, PAUSED, CLEARED, GAME_OVER, FINISHED}:
        for index in range(lives):
            life_image.draw(
                20 + index * 28,
                H - 36,
                width=18,
                height=18,
            )
    if state == INTRO and intro_awards_life and intro_time < 1.15:
        pulse = 1 + 0.12 * math.sin(intro_time * 12)
        size = 54 * pulse
        life_image.draw(
            W / 2 - size / 2,
            H / 2 + 36,
            width=size,
            height=size,
        )


refresh_stage_buttons()
refresh_main_menu()
set_gameplay_visible(False)
set_menu_background_visible(True)
gui.replace("Main Menu")
breakout.run("Breakout", update=update, draw=draw, fullscreen=True)
