# Game, scenes, and timing

`Window`, `Game`, and `Scene` have different responsibilities:

| Type | Responsibility |
| --- | --- |
| `Window` | Platform window, frame presentation, and event-loop integration |
| `Game` | Shared input, resources, networking, and scene management |
| `Scene` | World objects, layers, camera, physics, navigation, GUI, and time |

`game.add(...)`, `game.camera`, `game.physics`, `game.grid`, and `game.time`
forward to the current scene. A `Game` is not a subclass of `Scene`.

## Switch scenes

```python
from g import FadeTransition, Game, Scene


class Menu(Scene):
    def on_load(self) -> None:
        self.add_text(400, 300, "Start", font_size=32, screen_space=True)


game = Game(800, 600)
game.scenes.add("menu", Menu())
game.scenes.add("play", Scene())
game.scenes.switch("menu")
# Later, in a callback:
game.scenes.switch("play", transition=FadeTransition(0.5))
```

Lifecycle hooks are `on_load`, `on_enter`, `on_shown`, `on_exit`, and
`on_unload`. Switching changes the complete active world. Use a
[GUI view](gui.md) for a pause menu or inventory over the existing world.

## Simulation-time scheduling

```python
once = game.time.after(1.5, lambda: print("Ready"))
repeating = game.time.every(0.25, lambda: print("Tick"), times=4)
deferred = game.time.next_update(lambda: print("Next update"))

repeating.pause()
repeating.resume()
repeating.cancel()
```

Callbacks receive no arguments. A zero-argument lambda is valid; a function
expecting `delta_time` is not a timer callback.

Timers follow scene simulation time. They do not advance while the scene is
not updating, and unloading resets its clock and cancels its timers.
Schedule a repeating timer once, not once per `update` call.

Use the returned `TimerHandle` to inspect `remaining`, `running`, `paused`,
`cancelled`, and `done`. Long updates may invoke a repeating callback multiple
times; the clock has a callback limit to prevent unbounded catch-up work.
