# Editor workflow

Install the [editor extra](installation.md), then start in a new game folder:

```bash
g-editor --assets .
```

The Assets panel's root determines how resource paths resolve. Opening a scene
without an explicit root uses its containing folder by default:

```bash
g-editor level.scene.json
```

For scenes in a subfolder of a project, pass the intended project asset root:

```bash
g-editor scenes/level.scene.json --assets .
```

## Author a scene

1. Create objects in the hierarchy or drag image assets into the viewport.
2. Use the Inspector to change transforms and renderer/collider properties.
3. Use Layers for world content and UI Views for menus and overlays.
4. Add project `.ttf` or `.otf` fonts through the typed font fields.
5. Use Play to test and Stop to return to authored state.
6. Save the scene.

Editor Play uses temporary runtime state; do not treat gameplay changes as a
replacement for editing the saved document.

## Add reusable Python logic

Use **Assets > Create > Python Script** or **Add Component > Scripts >
New Script**. The editor registers the module in `g.project.json`.

```python
from g import Script, scene_component


@scene_component("my_game.Player")
class Player(Script):
    speed: float = 180.0

    def update(self, delta_time: float) -> None:
        self.game_object.x += (
            self.input.get_axis("x") * self.speed * delta_time
        )
```

Public annotated fields appear in the Inspector and participate in
serialization. The registration ID should remain stable after scenes use it.

!!! warning "Project trust"
    Loading project scripts imports Python modules and executes their code
    with your user permissions. Only trust projects you would otherwise run.
    The editor's trust prompt is consent, not a sandbox.

For loading and distributing the result, see
[persistence and exporting](../guides/persistence.md). The
[editor reference](../guides/editor.md) covers the complete workspace,
selection, assets, components, animation, Play mode, and export.
