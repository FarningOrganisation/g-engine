# GUI

Every scene owns a `GUIManager` with an active default view.

```python
from g import Button, Label, VBox

# Assume scene and start_game exist.
scene.gui.add(
    VBox(
        Label("My Game", font_size=32),
        Button("Play", on_click=start_game),
        spacing=12,
    )
)
```

Widgets include labels, images, panels, buttons, toggles, checkboxes, sliders,
progress bars, dropdowns, text inputs, list views, and scroll views. Layouts
include vertical and horizontal boxes, grids, anchors, canvas placement, and
stacking.

## Status, selection, and image widgets

Progress bars are display-only and clamp assigned values to their range:

```python
from g import ProgressBar

health = ProgressBar(0, 100, 75, width=240, height=20)
health.value = 60
```

Use a stack when text should appear over a progress bar:

```python
from g import Label, StackLayout

health_display = StackLayout(
    health,
    Label("60 / 100"),
)
```

`g.Image` remains the loaded image resource. `UIImage` is the GUI widget so
existing resource code remains unambiguous:

```python
from g import UIImage

portrait_image = game.resources.load_image("portraits/player.png")
portrait = UIImage(
    portrait_image,
    fit="cover",
    width=128,
    height=128,
)
```

Image fitting supports:

- `contain`: preserve aspect ratio and show the complete image
- `cover`: preserve aspect ratio and crop to fill the widget
- `stretch`: fill both dimensions independently
- `native`: center the image at its resource size

Checkboxes, dropdowns, and list views report single-selection changes:

```python
from g import Checkbox, Dropdown, Label, ListView

music = Checkbox(
    "Music",
    value=True,
    on_change=lambda enabled: set_music_enabled(enabled),
)

quality = Dropdown(
    ["Low", "Medium", "High"],
    selected_index=1,
    on_change=lambda index, value: set_quality(value),
)

levels = ListView(
    Label("Forest", height=36),
    Label("Caverns", height=36),
    Label("Citadel", height=36),
    selected_index=0,
    on_change=lambda index, row: load_level(index),
    width=260,
    height=120,
)
```

List view rows are arbitrary widgets, so rows may use labels, images, panels,
or nested layouts. Selection belongs to the row; interactive controls should
normally be placed outside a selectable list row.

## Scrolling

`ScrollView` and `ListView` automatically show a vertical scrollbar when
content is taller than the viewport. The thumb can be dragged, the track can
be clicked, and wheel scrolling remains supported. The scrollbar disappears
when the content fits.

```python
from g import ScrollView, VBox

scrolling_options = ScrollView(
    VBox(*option_widgets, spacing=8),
    width=320,
    height=240,
)
```

## Views and modal overlays

```python
from g import UIFade, UIView

scene.gui.register(
    "pause",
    UIView(transparent=True, modal=True, pauses_scene=True),
)
scene.gui.push("pause", transition=UIFade(0.2))
scene.gui.pop(transition=UIFade(0.2))
```

`push` and `pop` manage overlays; `replace` changes pages. A modal view blocks
input below it. `pauses_scene` stops world updates, including scene timers.
Transparency, pausing lower views, and pausing the world are separate choices.

Modal is a property of `UIView`, not a separate widget. Any GUI tree can be
placed inside a modal view:

```python
from g import AnchorLayout, Button, Label, Panel, UIView, VBox

dialog = Panel(width=420, height=260)
dialog.add_at(
    VBox(
        Label("Settings"),
        quality,
        music,
        Button("Close", lambda: scene.gui.pop()),
        spacing=12,
        width=360,
        height=220,
    ),
    30,
    20,
)
scene.gui.register(
    "settings",
    UIView(
        AnchorLayout(dialog),
        transparent=True,
        modal=True,
        pauses_scene=True,
    ),
)
```

## Styles and input

Use `UITheme` and the widget style classes to customize appearance. Labels,
buttons, checkboxes, and dropdowns accept project `Font` resources.

Keyboard navigation includes Tab/Shift+Tab focus traversal, Enter/Space for
buttons, and arrow keys for sliders. Pointer capture keeps dragging controls
from also activating the world.

Presentation properties such as `opacity`, `offset_x`, and `scale_x` can be
animated without replacing layout geometry. Opacity alone does not disable
input: use visibility or enabled state for that.

Widget animations have independent named channels. The reserved interaction
channel handles state transitions without replacing entrance or attention
animations.
