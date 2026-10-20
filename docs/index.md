# g: Python games, from a few lines to an editor project

`g` is a component-based 2D game engine built on pyglet and Pymunk.
Start with the procedural `Game` API, then grow into scenes, reusable scripts,
prefabs, tile maps, and the optional desktop editor.

```python
from g import Game, colors

game = Game(800, 600, bg_color=(20, 24, 32))
game.add_circle(400, 300, colors.CORNFLOWER_BLUE, radius=30)
game.run(title="My Game")
```

## Start here

1. [Install the engine](getting-started/installation.md).
2. [Write your first game](getting-started/first-game.md).
3. [Create an editor project](getting-started/editor.md).
4. Browse the [public API reference](api/index.md).

## What is included?

- Scenes, objects, components, reusable scripts, and simulation-time timers
- Sprites, shapes, text, project fonts, cameras, particles, and trails
- Opt-in point lights, spotlights, ambient light, and hard 2D shadows
- Physics, collisions, spatial queries, tile maps, and grid navigation
- GUI widgets, layouts, themes, focus, and view transitions
- Behaviours, property animation, custom shaders, and post-processing
- JSON scenes, prefabs, project configuration, and desktop exports
- A visual editor and experimental server-authoritative networking

!!! warning "Alpha software"
    APIs and serialized formats are still evolving. Pin a commit or release
    when starting a project and keep authored assets under version control.
    Native lighting currently supports point lights, spotlights, and hard
    polygonal shadows. Soft shadows and normal maps are not yet supported.

These guides describe implemented workflows. Internal design documents may
also discuss future work; they are not a promise that a feature is available.
