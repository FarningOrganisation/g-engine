<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/g-engine-dark.svg">
    <img src="docs/assets/brand/g-engine.svg" alt="g-engine" width="620">
  </picture>
</p>

`g` is a beginner-friendly, component-based 2D game engine for Python, built
on [pyglet](https://pyglet.org/) and
[Pymunk](https://www.pymunk.org/). Small games can use the direct `Game` API;
larger projects can grow into scenes, reusable scripts, serialized assets, and
the optional visual editor.

> **Alpha:** APIs and scene formats are still evolving. Pin a release or commit
> for projects and keep scenes and assets under version control.

## Highlights

- Sprites, shapes, text, animation, particles, trails, and custom shaders
- Opt-in point lights, spotlights, ambient light, and hard 2D shadows
- Hierarchical game objects with components and reusable scripts
- Pymunk physics, collisions, areas, raycasts, and overlap queries
- Scenes, layers, cameras, timers, transitions, and persistence
- Keyboard, mouse, grid, tile-map, navigation, and pathfinding support
- GUI layouts, themes, focus, views, and transitions
- Optional visual scene editor and PyInstaller desktop export
- Experimental server-authoritative networking

## Installation

Python 3.11 or newer is required.

From a checkout:

```bash
python3 -m pip install .
```

Install optional tools as needed:

```bash
python3 -m pip install ".[editor]"
python3 -m pip install ".[export]"
python3 -m pip install ".[all]"
```

For engine development, use an editable installation:

```bash
python3 -m pip install -e ".[all,docs]"
```

See the [installation guide](https://farningorganisation.github.io/g-engine/getting-started/installation/)
for virtual environments and Git-based project installs.

## First game

Save as `main.py`:

```python
from g import Game, Key, colors

game = Game(800, 600, bg_color=(20, 24, 32))
player = game.add_circle(
    400,
    300,
    20,
    color=colors.CORNFLOWER_BLUE,
)


def update(delta_time: float) -> None:
    player.x += game.input.get_axis("x") * 180 * delta_time
    player.y += game.input.get_axis("y") * 180 * delta_time
    if game.input.key_just_pressed(Key.SPACE):
        player.position = (400, 300)


game.run(update=update, title="My First Game")
```

Run it with:

```bash
python3 main.py
```

WASD and the arrow keys move the player. Space returns it to the center.

## Visual editor

Start the optional editor in a game folder:

```bash
g-editor --assets .
```

Open a scene with:

```bash
g-editor scenes/level.scene.json --assets .
```

The editor provides a rendered scene viewport, hierarchy, layers, Inspector,
asset browser, animation timeline, tile palette, GUI authoring, Play mode, and
desktop export. Project scripts can expose typed fields in the Inspector.

Read the [first editor workflow](https://farningorganisation.github.io/g-engine/getting-started/editor/)
and the full [editor reference](https://farningorganisation.github.io/g-engine/guides/editor/).

## Documentation

The complete documentation is at
**<https://farningorganisation.github.io/g-engine/>**:

- [First game](https://farningorganisation.github.io/g-engine/getting-started/first-game/)
- [Editor workflow](https://farningorganisation.github.io/g-engine/getting-started/editor/)
- [Guides](https://farningorganisation.github.io/g-engine/guides/scenes/)
- [Public API reference](https://farningorganisation.github.io/g-engine/api/)

The guides cover objects and scripts, input, resources, scenes, physics, 2D
lighting, tile maps, GUI, animation, shaders, persistence, exporting, and
networking.

Build the documentation locally with:

```bash
python3 -m pip install -e ".[docs]"
python3 -m mkdocs serve
```

## Examples

Runnable projects are available in [`examples/`](examples/), including:

- [`snake`](examples/snake/)
- [`breakout`](examples/breakout/)
- [`pingpong`](examples/pingpong/)
- [`platformer`](examples/platformer/)
- [`shooter`](examples/shooter/)
- [`racing`](examples/racing/)
- [`physics`](examples/physics/)

## Development

Run the core suite:

```bash
python3 run_tests.py
```

Run focused tests:

```bash
python3 run_tests.py tests/test_resources.py tests/test_game_factories.py
```

Run the complete suite and strict documentation build:

```bash
python3 run_tests.py --full
python3 -m mkdocs build --strict
```

See [testing and compatibility](docs/development/testing.md) and
[documentation maintenance](docs/development/documentation.md).

## License

Licensed under the [MIT License](LICENSE).

<p align="center">
  <small>Developed by</small><br>
  <a href="https://www.farning.de">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/farning-logo-dark.svg">
      <img src="docs/assets/brand/farning-logo.svg" alt="Farning" width="260">
    </picture>
  </a>
</p>
