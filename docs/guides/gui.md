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

Widgets include labels, panels, buttons, toggles, sliders, text inputs, and
scroll views. Layouts include vertical and horizontal boxes, grids, anchors,
canvas placement, and stacking.

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

## Styles and input

Use `UITheme`, `LabelStyle`, and `ButtonStyle` to customize appearance.
Label and button styles accept project `Font` resources.

Keyboard navigation includes Tab/Shift+Tab focus traversal, Enter/Space for
buttons, and arrow keys for sliders. Pointer capture keeps dragging controls
from also activating the world.

Presentation properties such as `opacity`, `offset_x`, and `scale_x` can be
animated without replacing layout geometry. Opacity alone does not disable
input: use visibility or enabled state for that.

Widget animations have independent named channels. The reserved interaction
channel handles state transitions without replacing entrance or attention
animations.
