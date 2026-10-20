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

`unload(filename)` evicts a filename from the resource caches; `clear()`
empties them. Cache eviction is not a request to delete game objects currently
using those resources.

## Text and project fonts

```python
title = game.add_text(
    400, 300, "My Game", font=font, font_size=32,
)
score = game.add_text(20, 20, "Score: 0", screen_space=True)
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
