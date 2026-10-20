# Editor reference

The optional `g-editor` application authors the same scenes, objects, and
components used by the runtime. It renders the active scene with the real
engine renderer, then overlays selection, transform, grid, and authoring tools.

For installation and a first scripted scene, begin with the
[editor workflow](../getting-started/editor.md).

## Start the editor

Open a project folder as the asset root:

```bash
g-editor --assets .
```

Open an existing scene:

```bash
g-editor scenes/level.scene.json --assets .
```

When no asset root is supplied, opening a scene uses its containing directory.
Pass `--assets` when scenes live below a larger project root. Resource paths in
scenes are stored relative to that root.

The compatibility entry point `python -m g.editor` is also available, but new
workflows should use `g-editor` or `python -m g_editor`.

## Main workspace

The default workspace contains:

- **Scene**: the rendered game viewport, grid, selection, and transform tools.
- **Hierarchy**: object trees for the selected object layer.
- **Layers**: ordered world, tile, and UI content.
- **Inspector**: transform, component, and script properties.
- **Assets**: project files below the configured asset root.
- **Animation**: sprite states and frame timing.
- **Tile Palette**: tileset selection and painting.
- **Console**: editor, play, and export output.

The docking layout is saved between sessions. Panels can be moved, resized,
closed, and reopened from the editor menus.

## Appearance and help

The editor opens with a branded g-engine and Farning splash screen, then uses
the operating system's light or dark application appearance by default.

Choose **Window > Theme** to switch between:

- **System**: detect the macOS, Windows, or supported Linux desktop appearance
  the next time the editor starts.
- **Light**: use the white, navy, and teal editor palette.
- **Dark**: use the navy, black, teal, and white editor palette.

The choice is stored in `~/.g-editor/settings.json`. Choosing **System**
restores operating-system detection; explicit Light or Dark choices remain in
effect across restarts.

Use **Help > Documentation** to open the published g-engine documentation or
**Help > Farning Website** to visit the company website.

## Objects, selection, and transforms

Create objects from the Hierarchy `+` menu, the viewport context menu, or by
dragging compatible assets into the viewport.

- Click to replace the selection.
- Shift-click to add an object.
- Cmd/Ctrl-click to toggle an object.
- Drag empty viewport space for box selection.
- Drag with Space, the middle mouse button, or the right mouse button to pan.
- Use the wheel to zoom.

Move, rotate, and scale tools edit the selected objects. Grid snapping uses the
configured scene cell size even when the visible grid becomes coarser at low
zoom levels.

Objects may be nested in the Hierarchy. Child transforms remain relative to
their parent, while viewport tools convert edits through the parent transform.

## Sprites and colliders

Choose **Sprite** to add an image-less sprite, then assign its image from the
Inspector. You can also:

- Double-click an image in Assets.
- Drag an image into the viewport.
- Drag an image onto a Sprite Renderer's image field.

Sprite picking and selection bounds use the image's real dimensions and world
scale. A collider is not needed for editor selection.

Sprites do not receive colliders automatically. Add a **Collider** component
when a sprite should participate in physics or spatial queries. Changing the
sprite image does not resize explicitly authored collision geometry.

The same separation applies in game code: renderers define appearance,
colliders define physical geometry, and physics bodies define simulated
movement.

## Inspector and components

The Inspector exposes the selected object's transform, visibility, enabled
state, custom properties, and serialized component fields.

Use **Add Component** to add compatible:

- Renderers
- Colliders and areas
- Physics bodies
- Top-down and platformer motors
- Sprite animators
- Point lights, spotlights, and shadow occluders
- Navigation and networking components
- Registered project scripts

Component menus support reset, copy, paste values, and removal. Required
dependencies are added as one undoable operation. Removal is disabled when
another component still depends on the selected component.

Collider fields select box, circle, ellipse, or polygon geometry. Interaction
mode selects **Solid**, **Sensor**, or **Query Only** behavior. Use the viewport
collider tool for direct geometry editing.

Select the scene itself to edit its ambient-light color. Adding an enabled
**Point Light 2D** or **Spot Light 2D** automatically activates native lighting
in the viewport. **Shadow Occluder 2D** is explicit and independent from
physics colliders. See [2D lighting and hard shadows](lighting.md).

## Assets

Assets displays the configured project root directly. Hidden files,
`.git`, and `__pycache__` are excluded.

The panel supports:

- Creating folders, scenes, and Python scripts
- Rename, copy, paste, duplicate, delete, reveal, and refresh
- Dragging files and folders from the operating system
- Image previews and dimensions
- Opening scenes and scripts

Resource fields use an asset picker rather than arbitrary external paths.
Referenced resources therefore remain portable below the project asset root.
Missing resources are shown explicitly and can be cleared or replaced.

Asset operations participate in undo and redo. Deleting a referenced asset
requires confirmation and lists known references before proceeding.

## Project scripts and trust

Create a script from **Assets > Create > Python Script**, or select an object
and use **Add Component > Scripts > New Script**. The editor registers the
module in `g.project.json`.

Public annotated script fields appear in the Inspector:

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

Registration identifiers must remain stable after scenes use them.

!!! warning "Project trust"
    Importing project scripts executes Python with your user permissions.
    Trust only projects you would otherwise run. The trust prompt is consent,
    not a sandbox. **Open Without Scripts** allows data inspection without
    importing registered modules.

## Sprite animation

Add **Sprite Animator** to author named states such as `idle`, `walk`, and
`attack`. A state can use:

- One static image
- An animation asset such as a GIF
- An authored sequence of image frames

The Animation panel controls the default state, looping, frame order, and
duration. Timeline playback and scrubbing preview animation without starting
scripts or physics. Stop gameplay Play mode before editing animation data.

## Tiles, GUI, and specialized editors

Tile layers use the Tile Palette for painting and collision authoring. See
[tile maps and navigation](tilemaps.md).

UI layers and views contain GUI widgets rather than world objects. See the
[GUI guide](gui.md).

Particle, prefab, collider, and other specialized editors become available
when their corresponding object, component, or asset is selected.

## Edit and Play modes

**Play** starts scripts, physics, timers, animation, input, and scene behavior
using temporary runtime state. **Stop** restores the authored scene document.

Gameplay mutations are not a substitute for editing and saving the scene.
Save authored changes before Play when they should persist.

## Project validation

The editor validates the project before entering Play mode and before export.
Use **Project > Validate Project** to run the same checks manually. Validation
collects independent errors where possible and reports them in the editor
status and Console, including:

- Missing image, font, audio, scene, prefab, particle, and shader files
- Invalid project scripts and unsupported serialized object, component, or GUI
  types
- Broken object references and invalid prefab or scene formats
- Invalid startup scenes, display settings, export metadata, and
  platform-specific application names
- Absolute asset paths or paths that leave the project root

Validation uses the unsaved in-memory version of the scene currently open in
the editor. Other project scenes and prefabs are read from disk.

For automation and continuous integration, run:

```bash
g-validate g.project.json
g-validate g.project.json --purpose export --target windows
g-validate g.project.json --json
```

The command exits with status `1` when errors are found. Project scripts are
imported so their registered object and component types can be checked. Use
`--no-import-scripts` when only static script syntax and file checks are
appropriate for an untrusted project.

## Saving and undo

Use **New Scene**, **Load Scene**, **Save Scene**, and **Save Scene As** from
the File menu. Scene writes are atomic.

Object, component, transform, asset, tile, animation, and GUI edits participate
in undo and redo where supported. Unsaved changes are confirmed before loading
another scene or closing the editor.

## Export

Install the export extra:

```bash
python -m pip install ".[export]"
```

Use **Project > Export Game** to configure the application name, version,
startup scene, and optional icon. Builds are written below `dist/`, and output
appears in Console. Export stops before invoking PyInstaller when project
validation fails.

The same project can be exported from the command line:

```bash
python -m g.export g.project.json
```

See [persistence and exporting](persistence.md) for runtime loading, project
configuration, platform builds, and deployment limitations.
