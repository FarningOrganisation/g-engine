# Input and coordinates

The default `x` and `y` input axes combine WASD and arrow keys.

```python
from g import Key, MouseButton

# Inside an update callback:
horizontal = game.input.get_axis("x")
held = game.input.pressed(Key.SPACE)
started = game.input.just_pressed(MouseButton.LEFT)
released = game.input.just_released(Key.SPACE)
```

These snippets assume an existing `game`. `pressed` means held now;
`just_pressed` and `just_released` are edges for the current simulation frame.
`mouse_delta` and `scroll_delta` accumulate frame movement.

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
player = game.grid.add_rect(4, 7, colors.WHITE)
game.grid.move(player, 5, 7)
cell = game.grid.world_to_cell(*player.position)
```

Grid factories position objects at cell centers. Dimensions and physics still
use world pixels. `world_to_cell` floors coordinates, including negative
coordinates.
