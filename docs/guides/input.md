# Input and coordinates

The default `x` and `y` input axes combine WASD and arrow keys.

## Keyboard and mouse buttons

```python
from g import Key, MouseButton

# Inside an update callback:
horizontal = game.input.get_axis("x")
held = game.input.key_pressed(Key.SPACE)
started = game.input.mouse_just_pressed()
released = game.input.key_just_released(Key.SPACE)
```

These snippets assume an existing `game`. The device-specific helpers are:

| State | Keyboard | Mouse |
| --- | --- | --- |
| Held now | `key_pressed(key)` | `mouse_pressed(button=MouseButton.LEFT)` |
| Not held now | `key_released(key)` | `mouse_released(button=MouseButton.LEFT)` |
| Pressed this frame | `key_just_pressed(key)` | `mouse_just_pressed(button=MouseButton.LEFT)` |
| Released this frame | `key_just_released(key)` | `mouse_just_released(button=MouseButton.LEFT)` |

Pass a `Key` to keyboard helpers and a `MouseButton` to mouse helpers.
All mouse helpers default to the left button; for example,
`game.input.mouse_pressed(MouseButton.RIGHT)` checks the right button.
`released` means not held, including before the first press;
`just_released` is true only during the frame of a release.
Repeated keyboard press events while held do not generate another press edge.
Edges are available for one simulation frame and are not consumed by a query,
so multiple checks in the same frame return the same result. A press and
release between simulation frames can produce both edges in the next frame,
even though the button is no longer held.

For example, distinguish continuous firing from a single shot per click:

```python
continuous_fire = game.input.mouse_pressed()
single_shot = game.input.mouse_just_pressed()
right_click_finished = game.input.mouse_just_released(MouseButton.RIGHT)
space_is_up = game.input.key_released(Key.SPACE)
```

The existing `pressed(button)`, `just_pressed(button)`, and
`just_released(button)` helpers still accept either a `Key` or a `MouseButton`.
Gamepad input is not currently supported.

`mouse_delta` and `scroll_delta` accumulate frame movement.

See [Moving forward](objects.md#moving-forward) for a click-to-shoot example
using `look_at()` and `move()`.

## Named axes

Create a custom axis with:

```python
game.input.bind_axis("zoom", Key.Q, (Key.E, Key.PAGE_UP))
```

Both directions held simultaneously produce zero. An unknown axis raises
`KeyError`.

## World and screen

Game objects use world pixels with positive X right and positive Y up.
The camera maps world coordinates into the logical game viewport:

```python
screen = game.camera.world_to_screen((100, 200))
world = game.camera.screen_to_world(screen)
```

World-space rendering follows the camera. `screen_space=True` text and other
screen-space renderers do not.
Presentation scaling maps the logical game viewport into the physical window;
`fit`, `integer_fit`, `fill`, and `stretch` select the scaling policy.

## Optional grid

```python
from g import Game, colors

game = Game.from_grid(columns=40, rows=30, cell_size=20)
player = game.grid.add_rect(4, 7)
game.grid.move(player, 5, 7)
cell = game.grid.world_to_cell(*player.position)
```

Grid factories position objects at cell centers. Dimensions and physics still
use world pixels. `world_to_cell` floors coordinates, including negative
coordinates.
