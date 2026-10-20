# Resources, colors, and text

Each game owns a `ResourceManager`. Equivalent resolved paths share cached
resources:

```python
from g import Game

game = Game(800, 600, asset_root="assets")
image = game.resources.load_image("images/player.png")
sound = game.resources.load_sound("audio/jump.wav")
font = game.resources.load_font("fonts/MyFont.ttf")
```

Relative paths resolve against the asset root, not a repeatedly consulted
working directory. Do not repeat the root prefix in resource paths.
Projects use the project folder as their resource root. Scene loading with an
explicit asset root and exports preserve the same relative-path convention.

## Loading assets together

Declare named assets in one place with a resource bundle:

```python
assets = game.resources.bundle(
    images={
        "player": "images/player.png",
        "ball": "images/ball.png",
    },
    sounds={
        "beep": "audio/beep.wav",
    },
    fonts={
        "main": "fonts/PressStart2P-Regular.ttf",
    },
)
```

The game can load the bundle automatically, one asset before each rendered
frame. Drawing continues while normal scene and user updates pause, allowing a
loading bar to remain responsive:

```python
loading_bar = game.add_rect(400, 300, (255, 255, 255), 300, 20)
loading_bar.scale_x = 0

def show_progress(progress):
    loading_bar.scale_x = progress

def start_game():
    player_image = assets.images["player"]
    beep = assets.sounds["beep"]
    font = assets.fonts["main"]
    # Create the rest of the game here.

game.load_assets(
    assets,
    on_progress=show_progress,
    on_complete=start_game,
)
```

`game.is_loading` reports whether a bundle is active. The bundle also exposes
`progress`, `loaded_count`, `total_count`, `current_name`, `loaded`, `failed`,
and `error`. Loading uses the normal resource cache, so later calls such as
`load_image("images/player.png")` return the already loaded asset.

`unload(filename)` evicts a filename from the resource caches; `clear()`
empties them. Cache eviction is not a request to delete game objects currently
using those resources.

## Text and project fonts

```python
title = game.add_text(
    400, 300, "My Game", font=font, font_size=32, alignment="center",
)
score = game.add_text(
    20, 20, "Score: 0", screen_space=True, alignment="left"
)
```

`alignment` accepts `"left"`, `"center"`, or `"right"` and controls which
part of the text is anchored at the object's position. It defaults to
`"center"` for compatibility.

Newline characters create multiple lines without additional configuration:

```python
instructions = game.add_text(
    400,
    300,
    "Use A and D to move\nPress Space to launch",
    alignment="center",
)
```

`load_font` returns a `Font` with filename, family, and style metadata.
Scenes store font asset paths and register fonts automatically when loaded.
The editor supports `.ttf` and `.otf` font selection for text renderers, labels,
buttons, and toggles. There is no system-font picker.

Keep font licenses with your project and confirm redistribution rights before
exporting.

## Named colors

```python
from g import colors

red = colors.RED
blue = colors.CORNFLOWER_BLUE
translucent_blue = (*colors.BLUE, 128)
```

The 148 browser/CSS named colors are RGB integer tuples. Multiword names use
underscores, and both gray/grey spellings are available. `Color` accepts RGB
or RGBA tuples; the named constants are always RGB. `transparent` is not
included.

## Sound

```python
player = sound.play(volume=0.5, pitch=1.0, loop=False)
player.pause()
```

Playback returns pyglet's media player. Non-streaming sound sources can be
reused for multiple plays. Audio decoder availability depends on the target
platform and installed media dependencies.
