# Your first game

Save this as `main.py` in your game folder:

```python
from g import Game, Key, colors

game = Game(800, 600, bg_color=(20, 24, 32))
player = game.add_circle(400, 300, colors.CORNFLOWER_BLUE, radius=20)


def update(delta_time: float) -> None:
    player.x += game.input.get_axis("x") * 180 * delta_time
    player.y += game.input.get_axis("y") * 180 * delta_time
    if game.input.key_just_pressed(Key.SPACE):
        player.position = (400, 300)


game.run(update=update, title="My First Game")
```

Run it with:

```bash
python main.py
```

WASD and the arrow keys move the circle. Space returns it to the center.
Positions use world pixels; increasing X moves right and increasing Y moves
up. `delta_time` is measured in seconds, so the speed above is 180 pixels per
second rather than 180 pixels per rendered frame.

Use `key_pressed(Key.SPACE)` for a key held down, rather than only the frame
it is first pressed. Mouse helpers use the same naming and default to the
left button: `mouse_just_pressed()` detects a new left click.
See [Input and coordinates](../guides/input.md) for press and release helpers,
and [Moving forward](../guides/objects.md#moving-forward) for aiming and
moving objects with `look_at()` and `move()`.

## Add a timer

Before `game.run(...)`, schedule a no-argument callback:

```python
game.time.after(2.0, lambda: print("Two simulation seconds elapsed"))
```

The timer belongs to the current scene. It follows that scene's simulation
time, not an independent wall clock.

## Next steps

- [Scenes and timing](../guides/scenes.md) for organizing game flow
- [Objects and scripts](../guides/objects.md) for reusable logic
- [Physics](../guides/physics.md) for physical bodies and overlap queries
- [Editor](editor.md) for authoring scenes visually
