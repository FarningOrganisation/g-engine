# g-engine

`g` is a beginner-friendly, component-based 2D game engine written in Python
and built on top of pyglet. Simple games can use the procedural `Game` API,
while larger projects can use scene classes, components, serialized scenes,
custom shaders, and the optional visual editor.

## Documentation

Read the **[online documentation](https://farningorganisation.github.io/g-engine/)**:

- [First-game tutorial](https://farningorganisation.github.io/g-engine/getting-started/first-game/)
- [Editor guide](https://farningorganisation.github.io/g-engine/getting-started/editor/)
- [Public API reference](https://farningorganisation.github.io/g-engine/api/), generated from Google-style docstrings

To preview or validate it from this checkout:

```bash
python3 -m pip install -e ".[docs]"
python3 -m mkdocs serve
python3 -m mkdocs build --strict
```

See [documentation development](docs/development/documentation.md) for
GitHub Pages publishing instructions. The existing detailed README sections
remain available while the website grows.

## Supported features

- Cached images, animations, sounds, and custom fonts
- Sound playback with volume, pitch, and looping
- Sprites, text, circles, rectangles, lines, triangles, and polygons
- Multi-emitter particle effects with deterministic playback and batched
  rendering, plus built-in fire, smoke, sparks, explosion, rain, and snow
  authoring presets
- Bounded, fading `TrailRenderer` components for persistent world-space marks
  such as tire tracks, with optional image textures for richer skid patterns
- The racing example includes orange-to-red drift sparks and translucent,
  round oil tire marks behind the car. Oil removes lateral grip while on a
  surface and for six seconds after leaving the last overlapping surface.
  Re-entering oil refreshes the effect; throttle and steering remain usable.
  **Oil Grip** and **Oil Duration** are editable on the Car Effects component.
- Filled and outlined shape styles
- Hierarchical game objects and reusable components
- Code-first game objects and serializable `Script` components
- Human-readable JSON scene saving and loading
- Stable object IDs and references between serialized objects
- Ordered scene layers with independent hierarchies and per-layer z-indices
- Multiple scenes, lifecycle hooks, and fade transitions
- 2D cameras and separate world-space and screen-space rendering
- Keyboard and mouse input with configurable axes
- Pixel-based and grid-based object placement
- Grid navigation, pathfinding, and navigation agents
- Pymunk-backed physics and collision callbacks
- Behaviours and property animations with easing, sequences, parallel steps,
  repetition, callbacks, and animation handles
- Numeric, vector, RGB, and RGBA property interpolation
- GUI widgets, layouts, focus, modal views, and UI transitions
- Per-object shaders and multi-pass scene post-processing
- Optional custom-styled ImGui scene editor running in the engine's pyglet
  window, with a GPU-texture viewport, hierarchy, collapsible inspector,
  resource browser, runtime preview modes, and registered script-component
  support

## Installation

Install the runtime package from a local checkout:

```bash
python3 -m pip install .
```

For engine development, use an editable installation:

```bash
python3 -m pip install -e .
```

Projects can install a branch, tag, or commit directly from GitHub:

```bash
python3 -m pip install \
  "g-engine @ git+https://github.com/OWNER/REPOSITORY.git@REVISION"
```

The distribution is named `g-engine`; Python code imports it as `g`.
Python 3.11 or newer is required.

## Running tests

Use the lightweight `unittest` runner from the project root; no extra test
dependency is needed:

```bash
# Default: small core smoke suite
python3 run_tests.py

# Feature work: run only the relevant files
python3 run_tests.py tests/test_trail_renderer.py tests/test_persistence.py

# Add the core smoke suite to feature tests
python3 run_tests.py --core tests/test_trail_renderer.py

# Narrow further to one class or method
python3 run_tests.py test_trail_renderer.TrailRendererTests.test_segments_expire_and_clear

# Explicit full regression run, including longer-running tests
python3 run_tests.py --full
```

The core covers vector math, component lookup, object updates, timing, scene
lifecycle, and collision callbacks. It is a quick smoke check, not a substitute
for feature coverage or the full suite. Explicit selectors run only those tests
unless `--core` is supplied; overlapping selections are deduplicated.

After a feature change, run its tests and tests for directly affected callers
and integrations (for example, persistence when changing serialized component
fields). Selection is explicit, not automatic dependency detection. Run the
full suite before a release or merge, and for broad changes to shared engine
infrastructure.

Every run reports its five slowest tests; use `--durations 20` to see more,
`--durations 0` to disable the report, `-v` for test names, or `-f` to stop
after the first failure. Timings include each test's setup and teardown but
exclude module imports and shared class/module fixtures. No tests are
automatically skipped or interrupted based on elapsed time. The default saves
time by selecting a small suite rather than declaring unmeasured tests slow.
Existing `python3 -m unittest discover -s tests` full-suite runs are unchanged.

## Networking

Networking is opt-in and server-authoritative. Add a `NetworkedGameObject` to
scene objects that should synchronize. Automatic mode replicates network-safe
transform, physics, lifecycle, ownership, and declared custom state:

```python
from g import NetworkedGameObject, Script, network_field


class RaceProgress(Script):
    lap: int = network_field(1)
    checkpoint: int = network_field(0)


player.add_component(NetworkedGameObject(owner_client_number=1))
```

Clients send input through `game.network.set_input(...)`; server scripts read
it through `game.network.input_for(...)`. Projects configure endpoint, bind
address, client limit, and snapshot rate under `network` in
`g.project.json`. The editor's Export dialog exposes the same settings and
lists both network components under the Network component category.

When networking is enabled, export creates separate `<Name>` client and
`<Name>Server` applications. Offline projects retain the original single-game
export.

The complete two-player example is in
[`examples/racing/`](examples/racing/). Start its server and up to two clients:

```bash
python3 examples/racing/run_network.py server
python3 examples/racing/run_network.py client
```

See [`g/doc/Network.md`](g/doc/Network.md) for the model, security boundaries,
current implementation status, and later milestones.

## Minimal game

```python
from g import Game

game = Game(800, 600, bg_color=(20, 24, 32))
game.add_circle(400, 300, (80, 190, 255), radius=30)
game.run(title="My Game")
```

Save this as a Python file in the project root and run it with `python3`.

## Visual scene editor

The editor is a separate desktop application in [`g_editor/`](g_editor/).
Install the editor extra:

```bash
python3 -m pip install ".[editor]"
```

Start a new scene:

```bash
python3 -m g_editor
```

An installed package also provides:

```bash
g-editor
```

The editor refreshes its framebuffer scale every frame so moving between
Retina and non-Retina displays preserves the UI's logical size and clipping.
Viewport grid lines use subdued transparency, with slightly stronger origin axes.
When zooming out makes
grid spacing smaller than 12 screen pixels, the overlay shows every second,
fourth, etc. line to stay readable; snapping continues to use the configured
grid cell size, not the coarser visible spacing.

Open an existing scene:

```bash
python3 -m g_editor scenes/level-one.scene.json
```

The editor changes its working directory to the scene's containing folder and
uses that folder as the default asset root. Pass `--assets PATH` only when the
project deliberately stores assets under a different root.

Projects with registered scripts load them automatically after you trust the
project. The explicit `--module` option remains available for advanced or
one-off use:

```bash
python3 -m g_editor \
    scenes/level-one.scene.json \
    --assets . \
    --module my_game.scripts \
    --module my_game.objects
```

The compatibility command `python3 -m g.editor` is also supported.

### Complete beginner: make an editor scene with a script

A Python **module** is simply a `.py` file that Python can import. The project
manifest tells both the editor and your game which modules define custom
components. Here is a complete workflow:

1. Make an empty folder for the game, open a terminal in it, and start the
   editor with that folder as the asset root:

   ```bash
   python3 -m g_editor --assets .
   ```

2. In the **Assets** panel, use **Create > Python Script**, enter `Player`,
   and choose **Create**. The editor creates `player.py`, registers its module
   in `g.project.json`, and fills it with an editable starter component:

   ```python
   from g import Script, scene_component

   @scene_component("player.Player")
   class Player(Script):
       speed: float = 120.0

       def update(self, delta_time: float) -> None:
           pass
   ```

   Open the file in your code editor and replace `pass` with the behavior you
   want. The decorator gives the component a stable name for saved scenes.

3. The editor asks whether to trust the project. Choose **Trust and Load** only
   for a project whose Python code you trust. Importing a Python module runs
   its top-level code with your user permissions; it can access files, the
   network, and other programs. Trust is consent to run that code, not a
   sandbox. The decision is stored in `~/.g-editor/trusted-projects.json`,
   outside the project, so a downloaded project cannot declare itself trusted.

4. Add a game object in the Hierarchy. Drag `player.py` onto that object, or
   select the object and drag the script onto **Add Component** in the
   Inspector. You can also choose `player.Player` from the Add Component menu.
   Its `speed` field is now editable in the Inspector.
   To create and attach a new script in one flow, select an object and choose
   **Add Component > Scripts > New Script**. The script is created in the
   selected Assets folder and attached to that object once the project is
   trusted. The **Scripts** submenu is available even before any scripts exist.

5. Save the scene as `level.scene.json`. Create `run_game.py` beside
   `g.project.json`:

   ```python
   from g import Game

   game = Game.from_project("g.project.json")
   project = game.project
   assert project is not None
   game.scenes.load(
       "level",
       "level.scene.json",
       asset_root=project.asset_root,
   )
   game.scenes.switch("level")
   game.run(title="My Game")
   ```

6. Run the game from the project folder:

   ```bash
   python3 run_game.py
   ```

`Game.from_project()` imports the registered modules before loading a scene, so
the serializer knows how to rebuild `player.Player`. Scene and prefab JSON
files remain data-only: they can refer to registered type names, but they
cannot request imports or execute code themselves. Choose **Open Without
Scripts** in the trust prompt to inspect an untrusted project's files without
importing its modules; custom scripted objects may not preview until their
types are loaded.

### Exporting a standalone game with PyInstaller

Install the optional export backend into the Python environment used by the
project:

```bash
python3 -m pip install ".[export]"
```

Save the startup scene, then choose **Project > Export Game...**. Enter the
game name, version, startup scene path, and an optional icon path. **Build**
creates a one-folder standalone application; **Build and Run** launches it
after a successful build. Export output is streamed into the editor
**Console**, and completed builds are written below `dist/<Game Name>/`.
Exported launchers disable pyglet's per-call OpenGL debug checks before
importing the engine. Development/editor runs retain their usual checks.
Re-export after engine changes to update an existing standalone executable.

The settings are persisted in `g.project.json`:

```json
{
  "export": {
    "name": "My Game",
    "version": "1.0.0",
    "startup_scene": "scenes/main.scene.json",
    "server_startup_scene": "scenes/server.scene.json",
    "icon": "branding/icon.png",
    "hidden_imports": [],
    "collect_packages": []
  }
}
```

`server_startup_scene` is optional and only used by network server exports.
When omitted, the server uses `startup_scene`. Network projects can also set
`network.auto_connect` to `false` when a menu should collect the endpoint and
call `game.network.connect()` itself.

The same backend is available without the editor:

```bash
python3 -m g.export g.project.json
```

The exporter validates the startup scene, stages the project without editor
trash, caches, virtual environments, or previous builds, generates a runtime
bootstrap and PyInstaller specification, and creates a one-folder build.
Registered project scripts are supplied to PyInstaller as hidden imports.

If the project contains `.venv/bin/python` on macOS/Linux or
`.venv/Scripts/python.exe` on Windows, that interpreter is used automatically.
Install the game's pip dependencies and PyInstaller into that environment so
the build analyzes the same packages used during development:

```bash
.venv/bin/python -m pip install ".[export]"
```

Ordinary imports are discovered automatically. Add dynamically imported
modules to `hidden_imports`. Add packages that ship runtime data files to
`collect_packages`; the generated specification uses PyInstaller's
`collect_data_files()` for them.

PyInstaller produces applications for the operating system and CPU
architecture on which it runs; it is not a general cross-compiler. Build
Windows releases on Windows, macOS releases on macOS, and Linux releases on
Linux. Public macOS and Windows releases still need the platform's normal
code-signing and distribution steps.

The editor and real `g` renderer share one pyglet OpenGL context. The scene is
rendered directly into a framebuffer texture and displayed by ImGui without
reading each frame back through CPU memory. This accurately displays engine
shapes, sprites, text, cameras, GUI, shaders, and post-processing while keeping
editor interaction responsive. ImGui draws the grid and selection overlays on
top of the texture. In Edit mode, click an object to replace the selection,
Cmd/Ctrl-click to toggle it, or Shift-click to add it. Drag empty viewport
space to make a rectangular selection; hold Shift to add its contents or
Cmd/Ctrl to toggle them. Hold Space while left-dragging, middle-drag, or
right-drag to pan; a stationary right-click still opens the viewport context
menu. Use the mouse wheel to zoom. In an object
or GUI hierarchy, Shift-click selects the top-to-bottom range from the selection
anchor, while Cmd/Ctrl+Shift-click adds that range. In the focused hierarchy,
**Up/Down** selects the previous/next visible row; **Shift+Up/Down** extends or
shrinks the range from the selection anchor. Collapsed descendants are skipped,
and keyboard selection scrolls into view. Arrow navigation is inactive while
editing fields, renaming, showing a popup, or playing.
Multi-object move applies
in world space and avoids moving a selected child twice when its parent is also
selected. Multi-object rotate and scale remain disabled until the editor has a
group-pivot workflow. Use the toolbar or **W**, **E**, and **R** to activate the
move, rotate, and scale gizmos respectively. Hold Shift while rotating to snap
to 15-degree steps. The second viewport toolbar row can orient move and rotate handles in
**Global** world axes or the selected object's **Local** axes. Multiple-object
movement is always global, while scale remains local-only. The Inspector's
position and rotation fields follow the selected transform space.
**Cmd/Ctrl+Z** and **Cmd/Ctrl+Shift+Z** undo and redo authored scene, GUI,
tilemap, hierarchy, and asset operations. **Ctrl+Y** is also accepted for redo.
**Cmd/Ctrl+C**, **Cmd/Ctrl+V**, and **Cmd/Ctrl+X** copy,
paste, and cut complete selected object or GUI subtrees. In the Hierarchy or
GUI panel, paste adds the copies beneath the single selected container or at
the active layer root when no container is selected. In the viewport, an
immediate paste offsets copies by 16 pixels; clicking a new viewport position
after copying makes the next paste use that position instead. Every pasted
record receives a new stable ID. Clicking empty space in the Hierarchy or GUI
panel clears the object or element selection while keeping the layer active.
Selecting a game object in the Hierarchy recenters the editor camera when that
object is completely outside the scene viewport; selecting an already visible
object leaves the camera unchanged. Drag an object or GUI row near another
row's top or bottom edge to reorder it, or onto the row center to make it a
child. Drop it on empty hierarchy space to move it to the active layer root.
Game-object world position, rotation, and scale are preserved across parent
changes. Free-positioned GUI elements preserve their screen position;
automatic layouts take ownership of child placement. Invalid cycles,
non-container GUI parents, and extra Anchor Layout children are rejected.
Selected items move together in their existing hierarchy order. Right-clicking
a hierarchy row or the viewport opens the same add, rename, cut, copy, paste,
duplicate, and delete actions. A viewport paste or **Add Here** uses the
clicked scene position.

In the editor, hierarchy order is the source of truth for render order.
Children render in front of their parent by default, while rows higher among
siblings and root subtrees render in front. Enable **Render behind parent** in
the Game Object Inspector to place a child's subtree behind its parent, useful
for tire tracks, shadows, and other ground effects. Children on each side of
the parent retain their hierarchy ordering; the option has no effect on root
objects. The editor derives the persisted `z_index` values automatically and
does not expose manual z-index fields in the Inspector. The preference is
saved with the object, and generated indices control rendering in both the
preview and exported game. Runtime code can still use the engine's `z_index`
API directly.
The docked **Layers** panel selects the active layer and controls render order,
visibility, and default placement. The panel beneath it adapts to that
selection: object layers show their **Hierarchy**, while tile layers show
**Tilemap** tileset tabs and layer settings. Painting tools appear in the
second toolbar row above the viewport, matching palettes appear as tabs in the
dedicated **Tile Palette** panel, and the selected tile's metadata appears in
the **Inspector**. Object and GUI
creation lives in the `+` menu at the top of the corresponding hierarchy.
New game objects become children of the selected object. New GUI elements
become children of the selected Panel, or siblings when a non-container GUI
element is selected. Select the layer again to add at the root.
Each layer-order row contains a visibility icon beside its type icon and name.
Double-click the name to rename it inline. Viewport editing always targets only
the selected layer, so separate layer locking is unnecessary.
The editor uses the bundled Inter font and a persisted Dear ImGui docking
layout. Panels can be moved, resized, tabbed, and rearranged; the initial
layout tabs Layers and UI Views above the active Hierarchy, GUI, or Tilemap
panel on the left, with assets below them, the engine viewport in the center,
the Tile Palette tabbed with Animation and Console at center-bottom, and the
inspector on the right. A thin status bar along the bottom shows the latest
action or a capped error summary, asset-root path, and viewport zoom. Full errors
are retained in Console; hover an action message to read its full text.
A second row directly above the viewport
provides the primary transform or tile-painting actions. Highlighted Move,
Rotate, and Scale icon buttons are anchored left, while Global and Local
controls are anchored right. Explanatory tooltips replace persistent toolbar
labels, while ImGuizmo supplies the viewport handles. Saving remains available from the File menu and
**Cmd/Ctrl+S** rather than the transform toolbar. Scene and atlas selection use
native platform file dialogs.

Use the toolbar above the viewport to start and stop Play mode. Its icon changes
from Play to Stop while the game is running, and the measured editor render FPS
appears beside it. Play starts the game lifecycle and forwards keyboard input
while the editor is not editing text and mouse input from the viewport.
Stopping rebuilds the authored scene, discarding temporary runtime changes.
Grid size and snapping controls live beside the playback button and are
disabled during Play mode. Object move snapping uses world-unit grid steps,
independent of viewport zoom. The editor grid and selection overlays are hidden
during Play and return when stopping. Python standard output, standard error, and
editor-frame tracebacks appear in the **Console** tab while
also remaining visible in the launching terminal.
Console stdout is white; stderr, errors, and tracebacks are red. Script reload
errors appear in Console and the status bar, not over the viewport; the last working
preview remains visible. Saving a corrected script clears its active reload
error automatically, while Console retains the diagnostic history.
Scene-preview load errors are logged there with a traceback and displayed in
the viewport instead of leaving an unexplained blank area. The selected
object's Inspector lists missing component dependencies under **Component
warnings**. Components with missing dependencies are omitted from the Edit
preview only, with a Console warning; the objects, grid, and authored component
data remain available for editing. Play validates the full scene and refuses
to start until the dependencies are fixed, keeping the Edit preview visible.
Dependency errors include the affected game object's name and ID.
Add the required components or remove the dependent component; the preview
reloads automatically when the document changes.

Stopping simulation or play reconstructs the runtime scene from the authored
document, so temporary runtime changes do not overwrite the scene. The
inspector separates the game object and each component into collapsible
sections.

## Examples

### Snake

Run from the project root:

```bash
python3 examples/snake/snake.py
```

The example can also be started with VS Code's **Run Python File** action.

Move with the arrow keys or WASD. Press `R` to restart after a collision.
Snake reads directional `just_pressed(...)` states directly from `game.input`.

### Ping Pong

Run from the project root:

```bash
python3 examples/pingpong/pingpong.py
```

Player one uses `W` and `S`; player two uses the up and down arrows. Press
`Space` to launch the ball and `R` to restart after a win. The example
demonstrates collision callbacks, property animations, custom font and sound
resources, a CRT scanline post-process, and shader-based screen shake on paddle
hits.

## Input

Every game exposes a frame-based input state through `game.input`. The default
`x` and `y` axes combine WASD with the arrow keys:

```python
from g import Game, Key, MouseButton

game = Game(800, 600)

def update(delta_time: float) -> None:
	player.x += game.input.get_axis("x") * 180 * delta_time
	player.y += game.input.get_axis("y") * 180 * delta_time

	if game.input.just_pressed(Key.SPACE):
		jump()
	if game.input.pressed(MouseButton.LEFT):
		shoot(game.input.mouse_position)
```

Use `pressed`, `just_pressed`, and `just_released` with keys or mouse buttons.
`mouse_delta` and `scroll_delta` contain the movement accumulated for the
current frame. Custom axes can combine one or more keys per direction:

```python
game.input.bind_axis("zoom", Key.Q, (Key.E, Key.PAGE_UP))
```

The same input state is available from its window as `window.input` after
`window.show_game(game)`, so window-level update callbacks do not need to create
their own `Input` instance.

## Grid

`Game` dimensions and all game-object positions are world pixels:

```python
game = Game(800, 600)
```

Every game also has an explicit `grid` subsystem. For grid-oriented prototypes,
create matching world and grid dimensions in one call:

```python
game = Game.from_grid(columns=40, rows=30, cell_size=20)
player = game.grid.add_rect(4, 7, (255, 255, 255))
game.grid.move(player, 5, 7)
```

Grid factories include `add_circle`, `add_rect`, `add_sprite`, `add_text`, and
`add_stateful_sprite`. Existing objects can be placed with `grid.add(...)`.
Conversions and snapping are explicit:

```python
world_position = game.grid.cell_center(4, 7)
cell = game.grid.world_to_cell(*player.position)
player.position = game.grid.snap_to_center(player.position)
```

Display and remove a grid overlay with:

```python
game.grid.show(color=(255, 255, 255, 80), thickness=1)
game.grid.hide()
```

The overlay consists of camera-transformed `GameObject` instances with
`LineRenderer` components and does not add colliders.

## Game object factories

`Game` and `Scene` provide pixel-based factories for every built-in concrete
visual family. Each factory returns a plain `GameObject` with the appropriate
renderer component (and collider where applicable):

```python
ball = game.add_circle(400, 300, (255, 255, 255), radius=12)
wall = game.add_rect(200, 150, (80, 90, 110), 120, 24)
player = game.add_sprite(320, 240, player_image)
score = game.add_text(
    20,
    570,
    "Score: 0",
    font_size=20,
    screen_space=True,
)

line = game.add_line((20, 20), (200, 80), thickness=3)
triangle = game.add_triangle((0, 0), (30, 0), (15, 25))
polygon = game.add_polygon([(0, 0), (30, 0), (35, 20), (10, 30)])
```

Stateful factories are `add_stateful_game_object` and `add_stateful_sprite`.
The standalone root API also exports `create_rect`, `create_circle`,
`create_sprite`, `create_text`, `create_line`, `create_triangle`,
`create_polygon`, and `create_stateful_sprite`; these create an unattached
`GameObject` with the corresponding renderer/animator components. Renderer
properties are read and changed through `require_component`, for example:

```python
from g import RectRenderer, create_rect

wall = game.add(create_rect(200, 150, (80, 90, 110), 120, 24))
wall_renderer = wall.require_component(RectRenderer)
wall_renderer.color = (100, 110, 120)
```

The stateful-sprite factory accepts `(states, initial_state, x, y, ...)` and
attaches the regular `SpriteAnimator` component. `add_stateful_sprite` and
`create_stateful_sprite` return ordinary `GameObject` instances; the separate
`StatefulGameObject` type remains a custom behavior object. Stateful factories
on `Game` and `Scene` return the created object after adding it to the current
scene. The distinction is explicit:

- `game.add_circle(400, 300, ...)` uses world pixels.
- `game.grid.add_circle(20, 15, ...)` uses grid cells.

Inside a `Scene` subclass, the same helpers are available through
`self.add_circle`, `self.add_rect`, and the other `add_*` methods.

## Sound

Load reusable sound effects with `Sound` and play them whenever needed:

```python
from g import Sound

laser = Sound("assets/laser.wav")
player = laser.play(volume=0.5, pitch=1.0, loop=False)
```

`play()` returns pyglet's media player, which also provides playback controls
such as `pause()`, `seek()`, `pitch`, and `position`. Sound files are loaded as
non-streaming sources so the same `Sound` can be played more than once. Set
`loop=True` for continuous playback.

## Resource management

Every game provides a resource manager that loads each image, animation, or
sound once and reuses it for subsequent requests:

```python
game = Game(800, 600)
player_image = game.resources.load_image("assets/player.png")
walk_animation = game.resources.load_animation("assets/player-walk.gif")
jump_sound = game.resources.load_sound("assets/jump.wav")
```

Equivalent relative, absolute, and `Path` filenames share the same cached
resource. Use `game.resources.unload(filename)` to evict that filename from all
resource caches, or `game.resources.clear()` to empty every cache. A standalone
`ResourceManager` can be created when resources need to be shared by multiple
game instances.

Relative resource paths (including fonts) resolve against the game's asset
root, not the process working directory. Editor Play uses the root shown in
the Assets panel; `Game.from_project()` and `game.load_project()` use the
project folder. Loading/restoring a scene sets the resource root before script
initialization, so `self.resources.load_sound("audio/coin.wav")` in a script
uses the same root as the scene's image references. Absolute paths still work.
Exported games use the packaged asset root inside the bundle, so these same
relative paths also work when the executable is launched from another folder.

For a code-only game, pass `Game(800, 600, asset_root="path/to/assets")`.
A standalone manager accepts `ResourceManager(asset_root="path/to/assets")`.
Without an explicit root or project, the initial working directory is used;
loading a scene without a project or explicit asset root uses its containing
folder. Resource paths should be relative to that root (do not repeat the
asset-folder prefix).

## Game-object child lookup

Use named-child helpers from scripts instead of relying on child indices:

```python
def on_ready(self):
    self.visual = self.require_child("Visual")

def update(self, delta_time):
    self.visual.rotation = self.platformer_motor.ground_angle
```

`get_child(name)` returns the first matching direct child, or `None`.
`require_child(name)` raises `LookupError` with the owning object's name if
missing. `get_children()` returns a tuple of direct child game objects.
Components (including scripts) expose `get_component(Type)`,
`require_component(Type)`, `get_child`, `require_child`, and `get_children` as
typed shortcuts to their owning GameObject. Component lookup searches only
that object's components; child lookup searches its children, with the same
`recursive=True` option. Missing required lookups retain the GameObject's
`LookupError`; unattached or detached components raise `RuntimeError`.
Use `self.game_object` for explicit object operations. These lookups do not
replace `@requires_components(...)`, which declares component dependencies.

```python
def on_ready(self) -> None:
    self.sprite_renderer = self.require_component(SpriteRenderer)
```

The child-lookup methods are also available on plain `Node` roots, `ObjectLayer`, and `Scene`.
Scene lookup searches root GameObjects across all object layers in layer order;
`scene.root` still refers only to the default layer's root. Tile layers are skipped.
Each child lookup accepts `recursive=True` to search descendants in depth-first
hierarchy order, excluding the lookup owner itself. Names are case-sensitive;
duplicate names resolve to the first match.

```python
fall_area = self.scene.require_child("PlayerFellArea").require_component(Area)
sibling = self.game_object.require_parent().require_child("Sibling")
scene_object = self.game_object.require_scene().require_child("Player")
layer_object = self.game_object.require_layer().require_child("PlayerFellArea")
```

`game_object.parent`, `game_object.scene`, and `game_object.layer` remain optional properties.
`require_parent()` returns a GameObject parent, raising `LookupError` for an
unparented object or a top-level object whose parent is the plain layer-root Node.
`require_scene()` raises `LookupError` if the object is not attached to a scene.
`require_layer()` returns its owning `ObjectLayer`, raising `LookupError` if
unattached. Child lookup on that layer searches only that layer, not the whole scene.
Scenes remain separate from GameObjects; these helpers do not add transforms or
components to scenes.
These helpers return `GameObject` instances. Built-in visuals are ordinary
game objects composed with renderer components; for example, retrieve a
`SpriteRenderer` with `require_component(SpriteRenderer)`. The lower-level
`children` property still returns `Node` instances: plain transform/grouping
nodes are allowed, and `GameObject` derives from `Node`, not the other way
around. Recursive lookup traverses those grouping nodes too.

Every `GameObject` has a `properties` dictionary for script-defined data:

```python
enemy.properties["team"] = "red"
enemy.properties["damage"] = 3
```

Properties are editable in the Inspector and persist with scenes, prefabs, and
checkpoints. Values may contain scene-serializable primitives, lists,
dictionaries, `Vec2` values, and references to saved game objects. Saving
raises `SceneFormatError` for unsupported runtime-only values. Plain `Node`
instances do not have properties because they are transform/hierarchy helpers,
not persisted gameplay objects.

Sprite Renderer exposes **Flip X** and **Flip Y** in the Inspector and as
`renderer.flip_x` / `renderer.flip_y` in scripts. Both default to false and
are saved with the scene. They mirror the visual around its centered origin,
including animated frames, without changing the object's transform, children,
or collider. Flips compose with parent and object scaling.
Sprites retain linear filtering for smooth scaling; texture coordinates stay
half a texel inside the image or atlas frame to avoid opposite-edge and
neighboring-frame color bleeding.

### Deleting game objects

Call `game_object.delete()` to remove an object and its entire descendant
hierarchy from its parent, layer, and scene. This also unregisters physics
bodies and colliders, cancels behaviours, calls component `on_detach()` hooks,
deletes drawables, and clears owned collision/Area callbacks. It works for root
objects, nested objects, and unattached objects; repeated calls are safe.
`scene.remove(game_object)` and `layer.remove(game_object)` use the same cleanup
and also support nested objects.

For example, a coin can delete itself from an Area callback:

```python
def on_enter_area(self, other: GameObject) -> None:
    if other.name == "Player":
        self.game_object.delete()
```

Deletion is safe during script updates and engine collision/Area callbacks;
deleted objects do not continue updating that frame. Shared image assets are
not deleted, since other objects may still use them. Custom scripts should
release their own subscriptions, timers, and other external resources in
`on_detach()`. Cleanup failures are reported after attempting the remaining
resource cleanup.

## Colors

Use `g.colors` for the complete set of 148 browser/CSS named colors instead
of repeating RGB values. Multiword browser names use underscores:

```python
import g

player = g.create_rect(100, 80, g.colors.CORNFLOWER_BLUE, 32, 32)
danger = g.create_circle(200, 80, g.colors.RED, 16)
```

Every named color is a three-item `tuple[int, int, int]` with channels from
0 through 255, so it works anywhere an RGB or RGBA `Color` is accepted. Add an
alpha channel explicitly when needed, for example `(*g.colors.BLUE, 128)`.
Both `GRAY` and `GREY` browser spellings are available. CSS `transparent` is
not included because it has an alpha channel and is therefore not an RGB color.

## Text

Use `create_text` or `game.add_text` with pixel coordinates, or
`game.grid.add_text` with cell coordinates. Fonts are project assets so scenes
use the same typeface on every machine:

```python
from g import TextRenderer

font = game.resources.load_font("assets/fonts/MyFont.ttf")
title = game.grid.add_text(
    10,
    12,
    "Game Over",
    font=font,
    font_size=28,
    color=(255, 220, 80),
    z_index=10,
)

text_renderer = title.require_component(TextRenderer)
text_renderer.text = "You Win!"
text_renderer.font = game.resources.load_font(
    "assets/fonts/MyOtherFont.otf"
)
text_renderer.opacity = 180
```

For HUD text that should remain fixed while the camera moves, enable screen
space and use pixel coordinates:

```python
score = game.add_text(20, 20, "Score: 0", screen_space=True)
```

`load_font` supports `.ttf` and `.otf` files, registers the file with Pyglet,
and returns a cached `Font` resource containing its internal family and style.
The editor exposes the same project fonts in font picker fields for text
renderers, labels, buttons, and toggles. Drag or copy font files into the
project assets, then select them in the inspector.

Scene files store project-relative font paths and load those resources
automatically, so exported games include portable font references rather than
machine-specific family names. System-font selection is intentionally not
supported. Text participates in normal game-object transforms, visibility,
hierarchy, scene layers, and batching.

## Scene layers and drawing order

Every scene has a default layer. Existing beginner-friendly calls place objects
there automatically:

```python
player = game.add_sprite(100, 100, player_image)
wall = game.add_rect(300, 200, (255, 255, 255), 64, 64)
```

Add named layers only when the game needs independent hierarchies and render
batches:

```python
scene = game.scenes.current
scene.layers.add("Background")
scene.layers.add("Foreground")

sky = scene.add_sprite(400, 300, sky_image, layer="Background")
player = scene.add_sprite(100, 100, player_image)
fog = scene.add(
    create_sprite(fog_image, 400, 300),
    layer="Foreground",
    z_index=20,
)
```

Layers render in collection order. Each layer owns an independent root,
world-space batch, and screen-space batch. An object's `z_index` controls its
drawing order inside its layer. Objects with equal z-indices keep deterministic
batch insertion order.

Objects can also be added through a layer:

```python
foreground = scene.layers["Foreground"]
foreground.add(create_sprite(tree_image, 500, 200), z_index=10)
```

`scene.add(...)`, `game.add(...)`, and all shape helpers continue to use the
default layer when `layer` is omitted. Change it explicitly when needed:

```python
scene.default_layer = scene.layers["Foreground"]
```

A parent and its descendants always belong to the same layer. Moving a parent
moves its complete subtree:

```python
scene.move_to_layer(player, "Foreground")
```

Layer visibility controls rendering, layer enabled state controls updates, and
layer locking prevents editor viewport picking. Physics, navigation, input,
resources, and the scene camera remain shared by all layers.

## UI views

UI views are separate from ordered world layers. Each named `UIView` contains
screen-space widgets in a free-positioned `CanvasLayout`. Panel, Label, Button,
Toggle, Slider, TextInput, and layout containers are persisted with the scene,
render above world content, and participate in the normal GUI input system:

```python
from g import Button, CanvasLayout, Label, Panel, UIView

hud = scene.gui.default
assert isinstance(hud.root, CanvasLayout)
panel = hud.root.add_at(
    Panel((20, 24, 34, 220), width=260, height=210),
    20,
    440,
)
panel.add_at(Label("Score: 0", font_size=22), x=20, y=70)
panel.add_at(Button("Pause"), x=40, y=12)

pause = scene.gui.register(
    "Pause",
    UIView(
        CanvasLayout(),
        transparent=True,
        modal=True,
        pauses_scene=True,
    ),
)
scene.gui.push("Pause")
```

The editor provides separate **Layers** and **UI Views** panels, tabbed together
by default above the shared content panel. New scenes start with Tiles,
Objects, and a startup HUD view. Selecting a UI view opens its nested GUI
hierarchy and exposes its name, startup status, transparency, modality,
lower-view pausing, and scene-pausing behavior in the Inspector. The UI
hierarchy `+` menu creates Panel, Label, Button, Toggle, Slider, Text Input,
Stack, Vertical, Horizontal, Grid, Anchor, and Scroll View elements.

Panels retain free-positioned children. Automatic layouts control child
positions, so those children expose size, margin, minimum-size, and growth
settings instead of editable X/Y coordinates. Layout-specific spacing,
padding, alignment, grid-column, and anchor settings live in the Inspector.
Initial toggle, slider, and text values are persisted. Runtime callbacks are
code-owned rather than serialized; game code can attach `on_click`,
`on_change`, and `on_commit` behavior after loading.

## Tile maps

World layers can mix object and tile layers. Object layers contain normal
game-object hierarchies. Tile layers instead keep cells as compact, chunked
integer data, so an individual tile is not a `GameObject`:

```python
from g import Image, TileFlags, TileSet

atlas = Image("assets/terrain.png")
terrain = TileSet(atlas, 16)
ground = scene.layers.add_tile(
    "Ground",
    terrain,
    chunk_size=16,
)
details = TileSet(Image("assets/details.png"), 16)
ground.add_tileset(
    details,
    identifier="details",
    name="Details",
    first_gid=terrain.tile_count,
)

ground.set_tile(2, 4, 0)
ground.set_tile(
    3,
    4,
    terrain.tile_count,
    flags=TileFlags.FLIP_HORIZONTAL,
)
ground.set_tile(2, 4, None)  # erase

cell = ground.tile_at(3, 4)
```

Tile coordinates may be negative. Cells store layer-global tile IDs. Each
tileset owns a stable, non-overlapping ID range, so removing one does not
renumber cells that use another. A non-empty chunk creates one indexed mesh per
tileset texture represented in that chunk and is culled against the camera.
Changing a cell rebuilds only its chunk. `scene.add(...)` continues to target
the default object layer; game objects cannot be parented into a tile layer.
Each chunk has an independent visibility group so camera culling cannot hide
other chunks sharing its tileset texture while panning or zooming.
Visibility is assigned only when a chunk enters or leaves the camera bounds;
unchanged culling results reuse the batch draw list instead of rebuilding it
every frame.
Tile texture coordinates stay half a texel inside each atlas cell to prevent
neighboring transparent pixels or colors bleeding into seams during camera
movement and fractional zoom.

Use the **Generate Collision** button on a tile layer to create static physics
geometry for every tile in the active tileset. **Collision Source** can derive
rectangles or rough convex polygons from opaque pixels, invert the alpha mask
to use transparent pixels, use full tiles, or retain existing metadata only.
The alpha threshold is configurable. New tile types painted afterward are
generated lazily.
Select one or more palette tiles to edit colors, collision, navigation, tags,
and custom properties together; a single tile also exposes rectangle geometry
and generated polygon details. Adjacent full solid cells are still merged into
rectangles. The generated shapes participate in physical response,
character-controller grounding, collision callbacks, overlaps, and raycasts
without creating hidden game objects. Physics queries identify generated
geometry by returning the owning `TileLayer`; tile collision callbacks identify
the contacted cell with a `TileContact`.

In the editor, add and remove tilesets from the tabbed **Tilemap** panel.
Removing an in-use tileset reports its usage count and requires **Delete
Anyway**, which also erases its cells. Select one tile or drag a rectangle in
the corresponding **Tile Palette** tab. **Cmd/Ctrl-click** tiles to toggle an
arbitrary sparse selection; unselected cells inside its bounds become
transparent stamp holes. Pencil paints that selection as a stamp;
Rectangle Fill repeats the stamp across the dragged scene area. The dice-style
Random modifier makes Pencil or Rectangle Fill choose independently from the
selected tile pool for each output cell. Eraser ignores Random. Rotate turns a
palette stamp or placed-tile selection 90 degrees clockwise; horizontal and
vertical flip buttons mirror the whole selection and show their active state.
Use **S** for Pencil/Stamp, **D** for Eraser, **F** for Rectangle Fill, **R**
to rotate, **H** to flip horizontally, and **V** to flip vertically.
While Pencil is active, hold **Shift** and click a placed tile to sample it
from the Scene viewport, or Shift-drag a rectangle to capture a multi-tile
stamp. Sampled stamps preserve each tile's rotation and flips; empty cells are
transparent holes and leave destination cells unchanged when painted. The
palette reveals the tileset containing the first sampled tile. Clicking the
Pencil toolbar button again toggles a persistent Tile Picker mode; click it
once more or press **S** to return to Pencil.
Choosing a different palette or placed-tile selection resets these transform
controls. Tile collision, navigation, tags, names, and custom properties for
palette or selected placed tiles are edited in **Inspector**.
Atlas selection follows the displayed palette rows. Stamps anchor at the
cursor's upper-left cell and extend rightward and downward. Rectangle Fill
repeats the selected stamp across the dragged area while keeping its top row at
the top of the filled region.

To rearrange tiles already placed in a tile layer, choose **Rectangle Select**
(`Q`) in the Scene toolbar and drag across the cells. Occupied cells are
highlighted; empty cells are not part of the selection. **Cmd/Ctrl-click** an
occupied cell to toggle it without replacing the rest of the selection. Switch
to **Move Tiles**
(`W`) and drag any selected tile to move the group, snapped to the layer's grid.
Dragging an unselected tile moves just that tile. While dragging, the actual
tiles are shown at full opacity at their destination and removed from their
source positions; only selection outlines are overlaid. The scene document is
committed on release, so cancelling restores the source and any covered tiles.
Moves replace occupied destination cells,
preserve each tile's flip flags and relative arrangement, and create one
undo/redo step per drag. Press `Escape` to cancel a drag and clear the selection.
Delete selected placed tiles with `Cmd+Delete` (`Ctrl+Delete` on other platforms),
the viewport's right-click **Delete** action, or **Edit > Delete**. Deletion is
one undo/redo step; undo also restores the tile selection.
Placed-tile selection temporarily supplies the single visible Tile Palette
highlight; selecting palette tiles returns that highlight to the Pencil stamp.

Use the viewport context menu's **Copy / Paste Here** actions or `Cmd+C` /
`Cmd+V` (`Ctrl+C` / `Ctrl+V` on other platforms) to copy placed tiles,
including their flip flags.
Without another viewport click, each paste offsets the copied group by another
grid cell. After a viewport click, paste anchors the group near that world
position, snapped to the grid. Tileset definitions travel with copied tiles;
pasting into another compatible tilemap remaps IDs rather than changing its
existing tile definitions.

**Cmd/Ctrl-click** world layers in the Layers panel to select multiple layers.
Selecting both tile and object layers unlocks a shared **Select / Move**
toolbar. Copy/paste uses shortcuts or the context menu. Rectangle Select includes occupied tiles and intersecting
GameObjects from the selected layers. Move translates both together on the tile
grid; copy/paste keeps objects in their original layers and parent hierarchy,
gives copied objects and descendants new IDs, and preserves relative world
transforms. Parent/child selections move or copy each subtree only once.
Mixed paste requires the original layers to be selected; clipboard resources
must use the same asset root. Multiple tilemaps must share cell sizes for
combined movement and paste, but may have different offsets. A combined move,
paste, or deletion is one undo/redo step, restoring the mixed selection as well.

To turn a tiled platform or other tile selection into a scriptable object,
right-click the viewport and choose **Convert Tiles to GameObject...**. The dialog
lets you name the object, choose its destination object layer, or create a new
object layer. It defaults to the single selected object layer, otherwise the
scene's default object layer. The object's world position stays unchanged.
Only selected tiles are removed; any selected GameObjects remain untouched.
Conversion, including new-layer creation, is a single undo/redo step.

The new **TileRenderer** keeps the original tileset assets, tile flags, spacing,
opacity, and animations; no PNG is generated. Its tiles move, rotate, and scale
together with the GameObject. Attach scripts and components normally.
**Include existing tile collisions** is off by default. When enabled, adjacent
full rectangular tile collisions merge into box-shaped Collider components;
partial rectangles and slopes retain their transformed polygon outlines, and
concave outlines split into convex polygon-shaped Collider components. Holes and disconnected pieces stay open,
and tiles without collision definitions do not become solid. The shapes share
the object's PhysicsBody when one is added; conversion does not add a body.
For a moving platform, add a kinematic PhysicsBody and movement script. Carrying
a standing character requires appropriate platform/motor logic separately.

Tilesets separate engine-understood metadata from game-specific properties:

```python
from g import (
    TileAnimation,
    TileAnimationFrame,
    TileCollision,
    TileNavigation,
)

terrain.define(
    1,
    name="grass",
    collision=TileCollision(),
    navigation=TileNavigation(walkable=True, cost=1.0),
    animation=TileAnimation(
        (
            TileAnimationFrame(1, 0.2),
            TileAnimationFrame(2, 0.2),
        )
    ),
    tags={"ground"},
    properties={"footstep_sound": "grass"},
)
```

Built-in fields cover rendering, full-cell collision, navigation metadata,
animation, and tags. `properties` is JSON-compatible custom game data. Cells
store only a tile ID and transform flags; they do not duplicate the tile
definition or allocate a custom-property dictionary.

For asset-free prototyping, use a `ColorTileSet`. It generates its rendering
texture in memory while retaining the same painting, metadata, collision, and
persistence behavior as an atlas:

```python
from g import ColorTileSet, TileCollision

prototype = ColorTileSet(
    [
        (76, 145, 65),
        (116, 82, 52),
        (65, 105, 190, 220),
    ]
)
prototype.define(0, name="Ground", collision=TileCollision())
prototype.define(1, name="Wall", collision=TileCollision())

world = scene.layers.add_tile(
    "Prototype",
    prototype,
    collision_enabled=True,
)
world.set_tile(2, 3, 0)
```

In the editor, choose **Add Color Palette** in Tilemap. The Tile Palette panel then
provides **Add Color** and a color field for the selected swatch, keeping color
authoring next to tile selection and painting. Color palettes use the normal
Pencil, Rectangle Fill, Eraser, stamp, and Random workflows. Their generated
textures are never written into the asset directory.

Individual images can be grouped without preparing an atlas:

```python
from g import Image, ImageCollectionTileSet

objects = ImageCollectionTileSet(
    [
        Image("assets/crate.png"),
        Image("assets/key.png"),
        Image("assets/door.png"),
    ]
)
decorations = scene.layers.add_tile("Objects", objects)
decorations.set_tile(4, 2, 0)
```

The largest source image determines the generated atlas cell size; smaller
images are centered without scaling. The tile layer's cell size still controls
their rendered world size. In the editor, choose **Add Image Collection** and
pick its first image from Assets. The collection appears as a normal palette,
where **Add Image Tile** opens the picker for additional project images.
Generated atlases remain in memory; scenes persist the original asset paths.

The editor can add object and tile world layers, configure an atlas, choose a
tile ID, paint or erase cells, and edit built-in and custom tile metadata.
Collision generation updates live as cells, definitions, or layer settings
change. Navigation generation remains metadata-only.

## Camera

Every game has a 2D camera centered on the game world. Move it, rotate it, or
change its zoom without modifying game objects:

```python
game.camera.position = player.position
game.camera.zoom = 1.5
game.camera.rotation = 10
```

The camera position is the world point shown at the center of the viewport.
Positive rotation is clockwise, matching game objects. Zoom must be finite and
greater than zero. `game.camera.reset()` restores the initial full-game view.

Convert coordinates for mouse picking and screen-space effects with:

```python
world_mouse = game.camera.screen_to_world(game.input.mouse_position)
screen_position = game.camera.world_to_screen(player.world_position)
```

The scene's solid background and screen-space renderers remain fixed to the viewport while
the camera transforms the world. Screen-space renderers are drawn afterward in
a dedicated UI batch, so they always appear above world content. Camera view
state is restored before this UI pass and does not affect other pyglet
rendering.

### Repeating image backgrounds

**Add Background** in the editor creates a plain GameObject with a
`RepeatingImageRenderer`. It creates or reuses an ordinary **Background** object
layer; a newly created layer is placed before gameplay layers. Assign an image
with the Inspector's image picker or drag-and-drop. Background objects still use
normal layer visibility, enablement, ordering, and undo/redo. They have no collider.
You can also attach Repeating Image Renderer directly through **Add Component**.

```python
background = game.add_background(
    sky_image,
    parallax=Vec2(0.2, 0.2),
    scroll_speed=Vec2(-8, 0),
)
renderer = background.require_component(RepeatingImageRenderer)
renderer.tile_size = Vec2(256, 128)
```

`game.add_background()` and `scene.add_background()` use the dedicated layer by
default. Pass `layer="Distant scenery"` to use a different existing object layer.
`create_background()` creates the same preset without attaching it to a scene.

- **Tile Size** is the displayed width/height of one image tile in screen pixels;
  runtime creation defaults to the image's native size. An empty editor background
  starts at 64 x 64 and adopts the first assigned image's size.
- **Repeat X/Y** control tiling per axis. A non-repeated axis draws only one tile,
  leaving the rest transparent.
- **Offset** and the object's world position shift the pattern in screen pixels.
  Object rotation/scale and camera zoom/rotation do not affect the viewport quad.
- **Parallax** is per-axis camera translation relative to the scene's initial
  centered view: `(0, 0)` is fixed, `(0.2, 0.2)` moves slowly, and `(1, 1)` matches
  world translation at unit zoom. Positive camera movement moves the pattern in
  the opposite direction. Parallax is evaluated when drawing, including when
  panning a paused editor preview.
- **Scroll Speed** advances only with simulation updates, respecting disabled
  objects/components/layers and scene pauses. **Opacity** ranges from 0 to 255.

Unlike other screen-space renderers, repeated backgrounds draw in their ordinary
object-layer pass, not the late UI pass. Keep their layer before gameplay to draw
behind it; rearranging layers intentionally changes their stacking.

## Drawing primitives

Lines, triangles, and simple polygons are game objects backed by pyglet shapes:

```python
line = game.add_line((40, 40), (240, 120), thickness=5)
triangle = game.add_triangle(
    (300, 100),
    (360, 220),
    (240, 220),
    color=(255, 100, 40),
)
polygon = game.add_polygon(
    [(400, 100), (500, 80), (550, 160), (460, 220)],
    color=(80, 180, 255),
)

outline = ShapeStyle(
    fill=None,
    outline=(255, 255, 255),
    outline_thickness=4,
)
outlined_circle = game.add_circle(
    650, 150, (255, 255, 255), radius=50, style=outline
)

filled_and_outlined = ShapeStyle(
    fill=(40, 120, 220),
    outline=(220, 240, 255),
    outline_thickness=3,
)
panel = game.add_rect(
    650, 300, (255, 255, 255), 140, 80, style=filled_and_outlined
)
```

Primitive coordinates are pixels. Their points are stored relative to the
primitive's center, so position, rotation, scaling, parenting, camera movement,
layers, visibility, and `screen_space=True` work like other game objects.
Circles, rectangles, triangles, and polygons accept a `ShapeStyle` for
fill-only, outline-only, or combined rendering. Their `fill_color`,
`outline_color`, `outline_thickness`, `fill_visible`, and `outline_visible`
properties are mutable on their renderer components. The existing `color`
factory argument and the renderer's `color` property control the fill. Lines
remain outline-only and their renderer exposes mutable color, opacity, and
thickness. Drawing primitives do not add colliders automatically.

## Behaviours and animations

`GameObject.animate()` provides a beginner-friendly sequence builder for
transform properties, `Vec2` positions, and visual properties exposed by
exactly one renderer component:

```python
handle = (
    player.animate()
    .to(0.5, x=500, y=300, easing=Ease.out_quad)
    .wait(0.2)
    .to(0.3, opacity=0)
    .start(on_complete=lambda player: print("Finished"))
)
```

Colors interpolate each channel and RGB targets preserve the current alpha:

```python
player.animate().to(
    0.5,
    position=Vec2(500, 300),
    color=(255, 80, 40),
).start()
```

`fill_color` and `outline_color` can be animated in the same way when they
already contain a color. Set an absent color to a transparent starting value
before animating it, for example `(255, 0, 0, 0)`. For an explicit renderer
target, or when an object has multiple renderers that expose the same property,
animate the component directly:

```python
renderer = player.require_component(SpriteRenderer)
renderer.animate().to(0.3, opacity=0).start()
```

`Component.animate()` targets only that component. An ambiguous object-level
visual property raises an explicit error rather than choosing a renderer.

The returned handle can control playback:

```python
handle.pause()
handle.resume()
handle.cancel()
handle.restart()
```

Pass `loop=True` to `start` for continuous playback. Build independent
sub-animations and run them together with `parallel`:

```python
move = player.animate().to(1, x=700)
fade = player.animate().to(0.5, opacity=80)
handle = player.animate().parallel(move, fade).start()
```

Use `repeat(times)` to repeat the animation built so far before continuing:

```python
player.animate() \
    .to(0.5, x=700) \
    .to(0.5, x=300) \
    .repeat(3) \
    .call(lambda player: print("Repeated three times")) \
    .start()
```

Calling `repeat()` without a count repeats indefinitely. Unlike
`start(loop=True)`, `repeat` can apply to an earlier section of the builder.

Starting another animation on the same object cancels the previous handle.
Animations continue to use the behaviour system internally. Advanced code can
assign `Sequence`, `Parallel`, `Repeat`, `Wait`, `Tween`, `Call`, and `SetState`
directly to `object.behaviour`. Use `cancel_behaviour`, `pause_behaviour`,
`resume_behaviour`, and `restart_behaviour` to control it.

## Shaders

Every rendered game object can replace its pyglet shader program. Convenience
constructors retain pyglet's vertex format for shapes, sprites, or text while
accepting a custom fragment shader:

```python
shape_shader = Shader.for_shapes("""
#version 150 core
in vec4 vertex_colors;
out vec4 final_color;
uniform float strength;

void main()
{
    final_color = vec4(
        vertex_colors.rgb * strength,
        vertex_colors.a
    );
}
""")
shape_shader["strength"] = 0.7
ball.require_component(CircleRenderer).shader = shape_shader
```

Set the renderer's `shader` to `None` to restore pyglet's default shader. A `Shader` can be
shared by several objects; its uniforms are then shared as well. Use separate
instances when objects need different uniform values. `Shader.for_sprites` and
`Shader.for_text` provide the corresponding pyglet vertex shaders. The custom
fragment source must use the matching pyglet inputs and texture uniforms.

Scenes can apply a full-screen post-processing shader:

```python
effect = PostProcess("""
#version 150 core
in vec2 texture_coords;
out vec4 final_color;
uniform sampler2D scene_texture;
uniform float time;
uniform vec2 resolution;

void main()
{
    vec4 color = texture(scene_texture, texture_coords);
    float pulse = 0.85 + sin(time * 4.0) * 0.15;
    final_color = vec4(color.rgb * pulse, color.a);
}
""")

game.scenes.current.post_process = effect
```

`scene_texture`, `time`, and `resolution` are populated automatically when the
shader declares them. Post-processing includes the scene background, world,
screen-space renderers, and GUI. Scene transition overlays render afterward.
Set `effect.enabled = False` to bypass it or assign `None` to remove it.

Chain effects in order with a post-processing pipeline:

```python
pipeline = PostProcessPipeline(
    grayscale,
    vignette,
    crt_effect,
)
game.scenes.current.post_process = pipeline
```

The pipeline uses two alternating framebuffer textures regardless of its
length. Disabled effects are skipped. Pipelines are mutable:

```python
pipeline.add(blur)
pipeline.insert(1, color_grade)
pipeline.move(crt_effect, 0)
pipeline.remove(vignette)
pipeline.clear()
```

A single `PostProcess` remains valid when no pipeline is needed. Every enabled
effect adds one full-screen rendering pass, so expensive effects should be used
selectively.

## Collision callbacks

`collides_with(other)` returns a `CollisionContact` when touching, or `None`
otherwise. Existing `if object.collides_with(other):` checks still work:

```python
contact = ball.collides_with(paddle)
if contact is not None:
    print(contact.normal, contact.angle, contact.points, contact.penetration)
```

For event-style code, register a callback on either object:

```python
@ball.on_collision
def bounce(contact):
    if contact.other is paddle:
        ball.speed.x = -ball.speed.x
```

`on_collision` runs once when contact begins. `on_collision_stay` runs on later
updates while contact continues, and `on_collision_end` runs when it ends. A
handler receives a `CollisionContact` and can be removed with
`remove_collision_handler(handler)`. Tile contacts use a separate set of
callbacks: `on_tile_collision`, `on_tile_collision_stay`, and
`on_tile_collision_end`, with corresponding `on_tile_overlap` callbacks for
sensors. Each receives a `TileContact` with the layer, cell coordinates, tile
cell, world-space contact point, and a normal pointing from the object toward
the tile. Tile handlers can be removed with
`remove_tile_collision_handler(handler)` or
`remove_tile_overlap_handler(handler)`.

Script components can instead override the same callback names directly,
without registration in `on_ready()`:

```python
from g import CollisionContact, Script

class PlatformMovement(Script):
    def on_collision(self, contact: CollisionContact) -> None:
        print("Platform touched by", contact.other.name, contact.normal, contact.angle)
```

Automatic Script hooks include collision, overlap, tile collision, and tile
overlap events, each with begin (the base name), `_stay`, and `_end` variants.
Object collision events receive `CollisionContact`; overlap events still receive
the other GameObject, and tile events receive `TileContact`.
Collision contacts expose `other`, `collider` (on the receiving object),
`other_collider`, world-space `points` (midpoints between touching surfaces),
`normal` (from the receiver toward the other object), `angle` (degrees clockwise
from world +X), and nonnegative `penetration` in world pixels. The other object's
callback receives the reversed normal and swapped colliders. End events carry
the last observed contact, not fabricated geometry after separation.
Events remain once per object pair; for multiple touching collider pairs the
first pair in component order on the physics pair's first object is used.
Contact data is geometric, not solver impulse or impact-force data.
Existing registered GameObject callbacks run first, followed by attached
Scripts in component order. Only enabled, ready Scripts on enabled objects
receive automatic events. Removing a Script stops its callbacks; deleting an
object during dispatch prevents subsequent callbacks to that object.
Do not also register an overridden hook manually unless you intend to call it
twice. Collision hooks include side/underside contacts; use PlatformerMotor's
ground state to determine whether a player is standing on a platform.

## Scenes

`Game` owns shared input and resources while each `Scene` owns its objects,
camera, grid, physics, navigation, and render batches. The existing `game.add`,
`game.camera`, `game.grid`, `game.time`, and similar APIs delegate to the
current scene.

```python
from g import FadeTransition, Game, Scene

class MenuScene(Scene):
    def on_load(self) -> None:
        self.add_text(400, 300, "Start", font_size=32, screen_space=True)

    def on_enter(self, previous: Scene | None) -> None:
        print("Menu opened")

class PlayScene(Scene):
    def on_load(self) -> None:
        self.player = self.grid.add_circle(10, 8, (80, 190, 255))

game = Game(800, 600)
game.scenes.add("menu", MenuScene())
game.scenes.add("play", PlayScene())
game.scenes.switch("menu")
game.scenes.switch("play", transition=FadeTransition(0.5))
```

Scene switching always replaces the complete active world. Use GUI views for
pause screens, inventories, settings, and other overlays:

```python
from g import UIFade, UIView

pause = UIView(
    transparent=True,
    modal=True,
    pauses_scene=True,
)
play = game.scenes.get("play")
play.gui.register("pause", pause)
play.gui.push("pause", transition=UIFade(0.2))
play.gui.pop(transition=UIFade(0.2))
```

Scene lifecycle hooks are `on_load`, `on_enter`, `on_shown`, `on_exit`, and
`on_unload`. A fade blocks input by default and switches scenes at its fully
opaque midpoint. Set `block_input=False` only when both scenes can safely
receive input during a transition.

### Saving and loading scenes

Scenes use a readable JSON format. During the current prototyping phase the
schema remains version 1 and may change without migration support. The
removed visual/Area object type IDs are not converted automatically; authored
presets use `g.game_object` with renderer, collider, and Area components. The
serializer stores ordered layers, their independent hierarchies, object
z-indices, compact tile chunks and tileset metadata, registered components,
scene settings, portable resource paths, and stable IDs for references between
objects:

```python
scene = game.scenes.current
scene.save("scenes/level-one.scene.json", asset_root=".")

loaded = game.scenes.load(
    "level-one",
    "scenes/level-one.scene.json",
    asset_root=".",
)
game.scenes.switch("level-one")
```

Only registered object and component types can be loaded. Scene files cannot
name arbitrary modules or execute arbitrary Python code.

Use `Script` for reusable, editor-attachable game logic. Annotated fields are
serialized and exposed by the editor:

```python
from g import Script, scene_component

@scene_component("game.patrol")
class Patrol(Script):
    speed: float = 120
    distance: float = 300

    def on_ready(self) -> None:
        self.start_x = self.game_object.x

    def update(self, delta_time: float) -> None:
        self.game_object.x += self.speed * delta_time
```

Python field names remain stable serialization keys, while the Inspector shows
friendly title labels: `max_fall_speed` appears as **Max Fall Speed** and
`world_x` as **World X**. Annotated names beginning with `_` are treated as
private runtime state and are not serialized or shown in the Inspector unless
the component is explicitly registered with a `fields` list containing them.

Project scripts default to **Add Component → Scripts**. Declare optional editor
metadata to change their menu category and label:

```python
@scene_component("player.Player", display_name="Player", category="Characters")
class Player(Script):
    ...
```

Public annotated `Vec2` fields are supported directly, for example
`offset: Vec2 = Vec2(10, 20)` (import `Vec2` from `g`). The Inspector displays
a paired numeric X/Y field rather than raw JSON. Vector defaults and edits
retain the Vec2 serialization tag, restore as `Vec2` instances, and support
component reset and undo/redo.

The Inspector's component field definitions live beside their Python source.
Use optional `inspector_field` metadata for labels, tooltips, numeric limits,
choices, or color controls:

```python
from g import Script, Vec2, inspector_field, scene_component

@scene_component("player.Player")
class Player(Script):
    speed: float = inspector_field(
        240.0, label="Run Speed", minimum=0, maximum=1000,
        tooltip="Movement speed in pixels per second.",
    )
    team: str = inspector_field("player", choices=("player", "enemy"))
    offset: Vec2 = inspector_field(Vec2(0, 10))
    tint: list[int] = inspector_field([255, 255, 255, 255], widget="color")
    health: int = 100  # Plain public annotations still appear automatically.
```

Private fields, `ClassVar` constants, methods, and unlisted properties are not
authored fields. Unannotated runtime attributes are not automatically exposed.
Metadata-backed mutable defaults are copied per instance. Bounds and choices
are checked during Inspector edits; they are not runtime assignment constraints.
Creation defaults, reset defaults, and missing-field display values use the
same declarations. Built-ins with custom serialized names or property setters
declare `__inspector_fields__ = {"name": inspector_field(...)}` rather than
replacing their runtime properties. Explicitly listed properties use their
getter docstrings as tooltips when no tooltip is supplied.
Sprite assets and animation configurations retain specialized editor widgets.

Use `group="Movement"` on fields to place them in a collapsible Inspector
section. `group_expanded=False` starts that section collapsed (the first field
in a group determines its initial state). Ungrouped fields remain at the top.
Colliders use Shape, Material, Advanced, and Collision Filtering sections;
Advanced and Collision Filtering start collapsed.
Inspector components sit in padded, bordered cards, with compact indented
section headers to distinguish groups from separate components.

`widget="mask"` selects a 32-bit checkbox picker with **All** and **None**
actions. Values remain unsigned integers in scene files. Optional `bit_names`
labels bits in order, starting with bit 1 (integer value `1`):

```python
layers: int = inspector_field(
    0xFFFFFFFF, widget="mask", bit_names=("Player", "Terrain", "Enemy"),
    group="Collision Filtering", group_expanded=False,
)
```

Unnamed bits use numbered labels. Collider Categories and Mask both use this
picker; collider Group remains an integer exclusion identifier, not a bit mask.
PhysicsBody velocity is a `Vec2`, edited with paired numeric controls in the
Inspector and saved using the vector encoding. Assign either a `Vec2` or an
`(x, y)` tuple; reads return a vector snapshot, so assign it back after editing.

PhysicsBody provides shared Categories, Mask, and Group defaults. Colliders
inherit these when **Override Collision Filtering** is off; enable it for
individual shapes such as sensors. Without a PhysicsBody, a collider always
uses its own filtering. Collision Type stays collider-specific.

Categories are flat menu groups, created as needed. They only affect editor
placement, not ownership, persistence keys, dependencies, or behavior.
Explicit `register_component(...)` calls accept the same `category` and
`display_name` metadata; built-in registrations declare their categories there.
Without an explicit display name, component headers and menus show the class
name unchanged (for example, `PlatformMovement`). When distinct registered
component classes share that name, the editor adds their registered type IDs
to distinguish them. Explicit display names always take precedence.
Built-in behaviors do not require project-script trust and remain outside the
default Scripts group.

Their implementations live together in `g/scripts/`: `platformer_motor.py`,
`top_down_motor.py`, and `camera_follow.py`. These `Script` subclasses compose
existing engine capabilities into gameplay behavior. Core capability components
such as renderers, colliders, and the `Script` base stay in `g/components/`;
`PhysicsBody` and the physics world stay in `g/physics.py`.
Public imports remain `from g import PlatformerMotor, TopDownMotor, CameraFollow`;
they are also available from `g.scripts`. Source-folder organization does not
change their editor categories or serialized scene type IDs.

**Scripts → New Script…** opens a creation dialog with **Empty Script**,
**Platformer Player**, and **Top-Down Player** templates. Templates create
editable project files deriving directly from `Script`, not motor subclasses.
The player templates read input and access a required motor. Attaching them
adds the motor, physics body, and a default box-shaped Collider if none exists.
Creating from Add Component attaches the new script to the
selected object after registration/trust; creating from Assets only creates
and registers the file. There is no inheritance-based "Create based on" option.

`on_attach()` runs when the component is structurally attached, including in the
Edit preview; keep gameplay initialization in `on_ready()`. `on_ready()` runs
once in the runtime/Play preview after its owner belongs to a scene. During
loading it waits until every object across all layers, descendant, reference,
and camera setting has been restored, so sibling and scene lookups are safe.
Edit preview loading, rendering, and component attachment do not invoke
`on_ready()`. Entering Play creates a fresh runtime scene and invokes readiness;
returning to Edit creates a preview without gameplay readiness. No separate
editor lifecycle is provided yet.
If Play initialization or a Play-mode reload fails, the editor reports the
original exception and traceback in the Console, returns to Edit, and rebuilds
the non-gameplay preview. An old or partially initialized runtime is never
published for simulation. Correct the reported cause and enter Play again.

`SceneSerializer.restore(..., initialize_components=False)` builds the same
non-gameplay preview for tooling; ordinary restore/load calls initialize
components by default. Dynamically attaching components or objects to runtime
scenes still invokes their readiness once. An active script can use
`game_object`, `scene`, `game`, `input`, and `resources`. The inherited `owner`
property remains optional because detached components do not have an owner.

### Scene restart and runtime checkpoint saves

```python
game.scenes.restart()                         # original designed scene
path = game.scenes.save_checkpoint("slot1")   # current scene progress
game.scenes.load_checkpoint("slot1")          # replace the current scene
```

Restart requires a scene loaded/restored by `SceneSerializer`. It reconstructs
the original design, including objects deleted during gameplay. A checkpoint
instead captures the current hierarchy, object/component configuration,
transforms, physics velocities, edited tiles, camera and scene elapsed time.
Removed objects stay removed. The original design is embedded in the checkpoint,
so restart still works after continuing a saved game.

Saves default to pyglet's writable per-user settings directory under the project
export name, in `saves/<slot>.scene.json`; assets and exported bundles are not
overwritten. Without a project, pass `application_name="MyGame"` or an explicit
`directory`. These keyword arguments are supported by `checkpoint_path`,
`save_checkpoint`, and `load_checkpoint`. Slots accept only ASCII letters,
numbers, hyphens and underscores. Writes use a temporary file and atomic
replacement; serialization failure leaves an existing save intact. Invalid,
missing or unsupported save files raise errors rather than silently starting
a new game. Register the project's script types before loading a saved scene.

Operations requested during scene updates (including collision callbacks) are
queued until the update finishes. `save_checkpoint` returns its destination
immediately, but a queued write happens at that frame's end. Only one operation
can be queued at a time; restart/load/save during a scene transition is rejected.
Replacing a scene releases its object layers and resets input. Old GameObject
references are no longer valid; reacquire them from the restored scene.

Private script state is opt-in, independent of editor configuration fields:

```python
class CheckpointProgress(Script):
    def on_ready(self):
        self._collected = 0
        self._remaining = 3.0

    def save_state(self):
        return {"collected": self._collected, "remaining": self._remaining}

    def load_state(self, state):
        self._collected = int(state["collected"])
        self._remaining = float(state["remaining"])
```

`save_state` must return a mapping with serializable values (including `Vec2`
and references to saved GameObjects). `load_state` runs after **all**
`on_ready` callbacks and after physics velocities/transforms are restored.
Object references resolve to the new objects. Components inherit the same hooks;
the engine implements them for platformer/top-down motor input and jump/support
state, sprite-animation playback and tile-renderer animation timing. The
platformer examples also preserve the moving platform's trigger, origin and
travel direction and the player's spawn position.

Normal `capture`/`save` calls remain configuration-only. Use
`include_runtime_state=True` to explicitly capture/save a runtime checkpoint.
Do not use `initialize_components=False` when restoring a checkpoint, or
change its saved component layout from `on_ready`.

This is a scene-data snapshot, **not** a Python-process or physics-solver snapshot.
It does not automatically preserve private attributes, arbitrary callbacks,
coroutines, external resources, GUI focus/transition stacks, input state or
game-wide inventory. Active callback timers are rejected on checkpoint capture;
store remaining time as explicit script state and update it in gameplay instead.
Unregistered transient components (such as behaviour/tween runners) must be
removed/reconstructed explicitly rather than silently discarded. Collision
contacts are recomputed, so begin callbacks can run again after loading. Keep
irreversible progression idempotent or guard it with saved script state.
Cross-scene/global progress belongs in a separate game-wide save.

Code-first game object classes are supported as well:

```python
from g import GameObject, scene_object

@scene_object("game.checkpoint")
class Checkpoint(GameObject):
    destination: str = "start"
```

The default loader calls a registered class without arguments and then restores
its annotated fields. Register a custom factory for classes that need constructor
arguments. Constructors should not create components that are also stored in
the scene, because the loader would otherwise create them twice.

Resource references must remain below `asset_root`. Generated in-memory images
cannot be persisted as paths and produce an explicit `SceneFormatError`.

### Visual scene editor

Install the optional desktop editor dependencies:

```bash
python -m pip install ".[editor]"
```

Then start the editor:

```bash
python -m g_editor scenes/level-one.scene.json --assets .
```

The custom-styled ImGui editor provides separate hierarchy and layer tabs,
Inter typography, a saved docking layout, a compact tool panel, ImGuizmo
transforms, native file dialogs, and a direct GPU-texture
engine viewport, synchronized engine picking and property inspection,
collapsible component sections, configurable grid size, enabled-by-default
grid snapping, draggable objects, built-in object templates, pan and zoom,
move/rotate/scale gizmos, edit/play modes, a resource browser, and
atomic JSON saves. Tile atlases appear as clickable palettes after loading and
automatically set the scene grid to their tile width. Color properties use
Dear ImGui's native color editor and popup picker rather than raw JSON.
Project resources must first be included below the configured asset root.
The top **File** menu provides explicit **New Scene**, **Load Scene**,
**Save Scene**, and **Save Scene As** actions. The **Assets** panel uses the
configured start directory directly as its root;
it does not add a synthetic `Assets` directory. It presents that directory as
a folder tree with an image preview, dimensions, and project-relative path.
The browser has no file-count cutoff. Hidden files and directories (including
`.git`) and `__pycache__` directories are excluded from scanning.
Use its `+` menu or a right-click menu to create folders, Python scripts, and
scene files. Double-click a `.scene.json` asset or right-click it and choose
**Load Scene** to make it the active editor scene; unsaved changes are
confirmed first. File and folder menus also provide rename, copy, paste, duplicate,
delete, reveal, and refresh actions. The panel also rescans the asset folder
about once per second, so files and folders added, deleted, or renamed outside
the editor (Finder, terminal, other tools) appear automatically; a selection
whose file disappeared is cleared. Asset deletion moves the item into an
editor-session trash area so undo can restore it. Deleting an asset (or a
folder containing assets) that is still referenced by the open scene, another
JSON scene/prefab file, or a registered project script opens a
**Delete Referenced Asset** dialog listing the references, with **Cancel** and
**Delete Anyway**. After deleting anyway, the editor keeps running: sprites
whose image is missing render nothing, and their Inspector image field shows
the path in red with “(missing)”. Use the field's **×** button to reset a
sprite image to none, choose **None** in the asset picker, or pick a replacement.
Missing animation-frame or tileset
images are reported as a preview error until they are replaced. Asset create, import, rename,
copy, duplicate, and delete operations participate in the editor's undo/redo
history. Drag files and folders from Finder or Explorer into the panel to copy
them into the selected asset folder. Existing names are preserved with a
numbered “keep both” suffix rather than being overwritten.

Resource fields use a shared picker component rather than editable filesystem
paths. Its read-only input displays the project-relative path, abbreviating
long paths while exposing the complete value in a tooltip. Its circle-dot
button is embedded at the right edge of the input and enters a temporary
picker mode: Assets is highlighted,
compatible extensions remain selectable, and incompatible files are disabled.
Choosing an asset assigns it and leaves picker mode. Escape, clicking the
circle-dot button again, or interacting elsewhere in the editor also leaves
the mode naturally. Assets can alternatively be dragged directly onto the
read-only input.

Inspector property controls share a common component layout: the value control
is on the left with a capped width, and its field name is aligned in the
right-hand label column. Specialized text, integer, float, slider, boolean,
choice, color, JSON, and asset controls inherit that layout while retaining
their type-specific editing behavior. Optional renderer colors such as shape
fill and outline use an enable checkbox beside the color swatch; disabling one
stores `None` while preserving its last color for later re-enabling. The final
active fill or outline cannot be disabled because every shape needs one.
Game objects expose their transform, enabled and visible states, custom
properties, and every serialized component property. **Add Component** opens
a categorized menu of compatible built-in renderers, colliders, physics
bodies, motors, and registered project scripts that are not already
attached. Choosing an entry adds that specific component. Each collapsible
component header shows its collapse control, name, and component-kind icon
inside the collapsible button, with an **Enabled** checkbox and a separate
three-dot actions button on the right. All GameObjects use the same cube icon
in the hierarchy.
The same menu opens by right-clicking the header and provides **Reset to
Defaults**, **Copy Component**, compatible **Paste Component Values**, and
**Remove Component**. Removal is disabled with an explanatory tooltip while
another component depends on it. Component edits and actions participate in
undo/redo.
Custom Properties and dictionary-valued script fields use a key/type/value
table. **Add Property** creates a unique key; edit its key and press Enter to
rename it. Keys cannot be blank or duplicate an existing key. Choose **string**,
**int**, **float**, or **bool** for a matching value control; changing type
resets that value to its default (`""`, `0`, `0.0`, or `False`). The trash
button removes a pair. Existing nested dictionaries remain editable, while
lists and null values retain JSON input; Vec2 values retain their vector field.
Adding, editing, renaming, and removing pairs use the existing undo/redo
and scene-persistence paths, including object and tile custom properties.
Sprite Renderer can be added with no image; assign its image in the Inspector
afterward. Entries already attached and conflicting movement motors are hidden,
but missing dependencies do not hide entries. Editor addition recursively adds
all required components, regardless of the runtime `auto_add` flag, and reuses
existing compatible components. Movement motors require the single Collider
component; its Shape field controls the selected geometry. Sprite Animator adds a Sprite
Renderer when needed and starts with an empty `default` state. Base component
classes are not menu entries. **Animation → Sprite Animator** is the single
component for named image states, animation assets, and frame sequences.
Use project scripts to choose states; there is no automatic transition graph.
To attach a copied component to another game object, select that object and
choose **Add Component → Paste Component as New**. It copies the component's
values, including Enabled, and adds missing dependencies in a single undo
step. Pasting is disabled for duplicate or incompatible components or
dependencies that cannot be created; **Paste Component Values** still updates an existing
component without adding another. Adding or pasting components and their
dependencies is atomic: failures leave the object unchanged. Code-first runtime
attachment still follows the declared dependency rules and `auto_add` flag.
Right-click empty space in the game-object Inspector for **Add Component**
and **Paste Component as New**. Component headers retain their own context
menus for copying, resetting, pasting values, and removal.
Collider shape, mode, and geometry use constrained Inspector fields. Box,
circle, ellipse, and polygon shapes expose **Solid**, **Sensor**, and **Query
Only** modes, including colliders loaded from scenes that did not persist an
explicit mode.

Two frequently used movement components have friendly predefined Inspector
entries:

- **Movement → Top-Down Motor** exposes speed, acceleration, and deceleration.
- **Movement → Platformer Motor** exposes ground and air acceleration, jump
  speed, fall-speed limits, gravity scale, slope limits, coyote time, and jump
  buffering.

Both require a Collider and automatically create or reuse a dynamic physics
body at runtime. Only one player controller can be attached to
an object. These built-in scripts implement movement physics; a project script
supplies player intent by calling `move()` and, for the platformer motor,
`jump()`.

**Camera → Camera Follow** attaches a premade script to the object the camera
should follow. **Offset X/Y** are world-space offsets. **Smoothing** is an
exponential response rate (larger values respond faster); zero follows
immediately. It follows the owning object's world position, including parent
transforms, without requiring a separate camera object.
Components update in attachment order; place Camera Follow after behaviors
that change the followed object's transform during their update.
PlatformerMotor is the exception: it runs after ordinary scripts so it can use
their current input and moving-platform velocities.

The platformer example uses `PlatformerMotor`, its project-owned Player script
for input and Visual tilt, and a separate Camera Follow script. The prototype
API and scene type IDs have been renamed from `PlatformerController` /
`TopDownController` to `PlatformerMotor` / `TopDownMotor`; old names are not
retained.

The example's `PlatformMovement` script requires a **kinematic PhysicsBody**.
Its **Distance** parameter is the horizontal travel in world pixels from the
starting position to the right-hand endpoint. **Velocity** is the non-negative
speed in pixels per second; zero pauses movement. The platform repeatedly moves
right and returns left, using body velocity and shortening the final fixed
physics step to avoid overshooting an endpoint. It does not animate positions.
PlatformerMotor automatically rides solid moving supports using their body velocity.

The Layers panel is intentionally limited to world-layer ordering and compact
row controls. The adjacent UI Views tab owns named view selection and ordering;
neither panel repeats detailed properties below its list. Selecting one tile
in a tile palette displays that tile's
name, color, collision shape and size, navigation data, tags, animation frames,
and custom properties in the Inspector. Multi-tile palette selections remain
paint stamps and do not expose a single shared property editor. Zoom the Tile
Palette with its `+` and `-` controls or Ctrl/Cmd+wheel over the palette.
When placing tiles, a translucent stamp preview follows the pointer in the
viewport; Rectangle Fill previews the full repeated stamp area while dragging.

New editor scenes use world gravity `(0, -980)` so dynamic physics bodies and
the Platformer Motor fall immediately in Play mode. Clear the object
selection to open the grouped **Appearance**, **Starting Camera**,
**Presentation**, and **Physics** sections in the scene Inspector. Starting
camera position, zoom, and rotation are applied when the scene loads. Physics
exposes **Gravity X**, **Gravity Y**, and **Physics Damping**. These settings
are stored in the scene document. Code-first `PhysicsWorld` instances retain
their neutral `(0, 0)` default until the game sets gravity explicitly.

In Play mode, the Scene viewport renders at the authored virtual game
resolution rather than changing the runtime camera to match the editor dock
size. The resulting game image is centered horizontally and vertically inside
the presentation matte. Larger displays therefore show a larger or sharper
image, not more of the game world. Mouse coordinates are converted from the
physical output rectangle back into virtual game pixels; clicks in matte bars
are ignored.

Clear the object selection to configure presentation in the scene Inspector:

- **Resolution Width** and **Resolution Height** set the virtual game
  resolution. New scenes default to 1280 x 720. The preset menu provides
  640 x 360, 960 x 540, 1280 x 720, 1600 x 900, and 1920 x 1080 while still
  allowing custom dimensions.
- **Fit** preserves aspect ratio and shows the complete game with centered
  letterbox or pillarbox matte bars. This is the default.
- **Integer Fit** uses the largest whole-number scale that fits, when possible,
  for crisp pixel art.
- **Fill** preserves aspect ratio and covers the output by cropping equal
  amounts from opposite edges. It never reveals additional world content.
- **Stretch** covers the output without cropping but may distort the image.
- **Presentation Matte** controls the color outside the game image.
- **Resizable Window** and **Start Fullscreen** configure exported runtime
  windows.

`Game.width`, `Game.height`, and the scene camera remain the virtual
resolution. `Game.run()` creates a physical presentation window which can be
resized or made fullscreen without changing the camera view:

```python
game = Game(
    1280,
    720,
    scaling="fit",
    matte_color=(12, 14, 20, 255),
    resizable=True,
)
game.run(title="My Game")
```

Runtime and editor Play mode use a fixed-step simulation accumulator.
`Game.run()` defaults to a `1 / 60` second simulation step; faster render
frames accumulate until one complete step is available, while slower render
frames can run multiple simulation steps before drawing. Catch-up is limited
to eight steps per frame so a long pause cannot create an unbounded spiral of
work. The existing `interval` argument selects the fixed simulation step, not
a rendering FPS cap. Physics retains its smaller internal substeps, and game
objects and scripts continue to use the single `update(delta_time)` API.

On macOS, standalone game windows use a CoreVideo display link instead of
independent simulation/drawing timers. Its callback only posts a coalesced
frame request; the main thread advances simulation, draws, and presents.
Blocking OpenGL vsync is disabled while this driver runs, avoiding a second
presentation wait. Display changes, fullscreen/resizing, and reactivation reset
accumulated timing rather than triggering catch-up bursts. Display-link failures
are reported explicitly. Other platforms retain continuous, vsync-enabled drawing.

Standalone macOS games use borderless fullscreen on the current monitor rather
than a native fullscreen Space. The green window button, F11, and
Control-Command-F toggle fullscreen; Escape restores the previous window size
and position. Command-M exits fullscreen and minimizes the window. Keyboard and
text input focus is preserved when entering and leaving fullscreen.
`game.run(fullscreen=True)` and the exported project's fullscreen
setting use the same borderless path without changing the display resolution.
`Window.set_fullscreen()` and `Window.toggle_fullscreen()` also use this path.
Other platforms retain their existing fullscreen behavior.

Rendering blends object position, rotation, and
scale, plus camera position, rotation, and zoom, between the last two simulation
steps. Physics and scripts still see the authoritative simulation transforms;
the blended values are used only during drawing and are restored even if
drawing fails. This adds up to one simulation step of visual latency in
exchange for smooth movement on higher-refresh displays. Editor rendering is
unchanged.

For an instant respawn or camera cut, call `game_object.reset_interpolation()`
(also resets descendants) or `scene.camera.reset_interpolation()` after changing
the transform. `PhysicsBody.teleport()` and `Camera.reset()` do this automatically.
New objects, reparented objects, scene changes, and transform edits made
outside simulation updates do not blend from stale history.

Physics contact refreshes reuse unchanged results. Standard Area updates share
one contact snapshot until a callback runs; callbacks and custom Area updates
use independently refreshed queries so moving, deleting, or changing colliders
still takes effect immediately. Object-layer render preparation runs only when
hierarchy/components/render ordering change. Sprites skip unchanged transform
and visibility writes, and repeating backgrounds skip unchanged uniform uploads.

Each scene owns a simulation-time scheduler at `scene.time`, also available as
`game.time` for the current scene. Use `after` for a one-shot event and `every`
for repetition:

```python
scene.time.after(2.0, spawn_boss)

repath_timer = scene.time.every(
    0.35,
    repath_enemies,
)

wave_timer = scene.time.every(
    1.5,
    spawn_wave,
    times=5,
)
```

Scheduling returns a `TimerHandle`:

```python
repath_timer.pause()
repath_timer.resume()
repath_timer.cancel()

print(repath_timer.running)
print(repath_timer.remaining)
```

`scene.time.next_update(callback)` defers work to the next simulation update.
`scene.time.elapsed` is the scene's accumulated active simulation time and
`scene.time.delta` is its latest step. Timers advance from the same fixed-step
loop as physics and game objects, pause when a UI view pauses the scene, suspend
while another scene is active, and are cancelled when their scene is unloaded.
Timers created by a timer callback begin on the next update, so callback-driven
scheduling cannot mutate the active timer traversal. The pathfinding shooter
example uses scene timers for firing, contact damage, repathing, and wave
delays.

Use `game.run(fullscreen=True)` for an explicit runtime override. Project
manifests support the corresponding `scaling`, `matte_color`, `resizable`, and
`fullscreen` keys under `game`. Scene presentation settings are restored when
the scene is loaded and are used by both editor Play mode and exported games.

Select an object, GUI element, layer, file, or folder and press **Return** on
macOS or **F2** on Windows/Linux to rename it inline. Renaming an asset updates
known Sprite and tileset references in the currently open scene.

Choosing **Sprite** from the Hierarchy `+` menu (or the right-click **Add** /
viewport **Add Here** menus) adds an empty sprite; assign its image later via
the Inspector's **Image** field. Image-less sprites draw nothing and are saved
with `"image": null`. Sprites with an image can also be created by
double-clicking an image while an object layer is active, or by dragging an
image from Assets into the viewport. A viewport drop creates a root sprite at
the dropped world position. Setting or replacing a Sprite image through its
Inspector asset field also creates or updates its default box collider to the
new source dimensions.

Add a **Sprite Animator** component to a sprite to author named states such
as `idle`, `walk`, and `attack`. Selecting that GameObject populates the
dockable **Animation** panel, tabbed with **Tile Palette** and
**Console** below Scene by default. The Inspector shows a summary and
an **Open Animation Panel** shortcut.

- Add or select states in the left list; `*` marks the startup state.
  Use the **Name** field above the timeline to rename a state (Enter to confirm).
  With the state list focused, press Enter on macOS or F2 on Windows/Linux
  to rename inline (Escape cancels). Delete/Backspace, including Cmd+Delete
  on macOS, removes the selected state; the last state cannot be removed.
  Right-click a state for **Set default** and **Delete**. These actions target
  the clicked state; deleting the last state and editing during Play are disabled.
  Expand **State Settings** to choose
  **Image**, **Animation Asset**, or **Frames**, and set Loop, Default, and Autoplay.
- Cmd/Ctrl-click or Shift-click image assets to select several. Drag them
  onto the timeline to append frames in asset-browser order.
  Empty animations show a drop hint inside the timeline.
  An initial empty placeholder is replaced. You can also drop files from
  Finder onto the panel; external files are first copied into the selected
  Assets folder. Drop one GIF on its own to use its decoded frames and timing.
- Frame widths represent their durations. Zoom or scroll the timeline,
  click a frame to select it, drag frames to reorder them, and drag a frame's
  right edge to shorten or lengthen it. The selected frame also has a numeric
  Duration field, image picker, and add/remove controls. A resize gesture is
  one undo action. GIF frame order belongs to the asset; GIF duration overrides
  are saved in the scene without rewriting or extracting the source file.
- **Play/Pause**, **Stop**, and the time slider preview the selected state
  independently under **Animation Preview**, to the right of the frame details
  below the timeline. Clicking the timeline ruler scrubs the preview.
  In Edit mode, playback and scrubbing also temporarily display that frame on
  the selected sprite in the viewport. Selecting another state during playback
  restarts preview with that state. Pause holds the frame; Stop restores the
  normal appearance. No scripts or physics run, scene data and the runtime
  animator's state are unchanged, and gameplay Play is never overridden.
  State selection alone does not change Default.

Image states show one static image (or no image); animation assets are
decoded into timed frames. Gameplay playback still uses scene update time,
so it freezes in Edit and when its component, object, or scene is inactive
or paused. Stop gameplay Play before editing animations.
Asset-field click targets are refreshed every frame; changing source modes
or removing a field cancels its picker rather than assigning to an old target.
Sprite Renderer only displays static images; it does not run a pyglet animation
clock. Passing an `Animation` to `create_sprite` / `add_sprite` automatically
adds Sprite Animator. A non-looping asset's untimed final frame uses the last
timed frame's duration (0.1 seconds if none exist), then holds the final image.

The same workflow is available from code:

```python
from g import (
    Image,
    SpriteAnimation,
    SpriteAnimationFrame,
    SpriteAnimator,
)

animator = sprite.add_component(
    SpriteAnimator(
        {
            "idle": SpriteAnimation(
                (
                    SpriteAnimationFrame(
                        Image("player/idle_1.png"),
                        0.2,
                    ),
                    SpriteAnimationFrame(
                        Image("player/idle_2.png"),
                        0.2,
                    ),
                )
            ),
            "attack": SpriteAnimation(
                (
                    SpriteAnimationFrame(
                        Image("player/attack_1.png"),
                        0.08,
                    ),
                    SpriteAnimationFrame(
                        Image("player/attack_2.png"),
                        0.12,
                    ),
                ),
                loop=False,
            ),
        },
        "idle",
    )
)

animator.play("attack")
animator.play("idle")
```

Calling `play()` for the state that is already running does not restart it
unless `restart=True` is passed. Animators can also be paused, resumed, and
stopped. Sprite colliders remain stable while visual frames change.
For simpler states, use `SpriteAnimator({"idle": Image("idle.png"),
"run": Animation("run.gif"), "hidden": None}, "idle")`.
Scripts can switch with `animator.play("run")`, `animator.state = "run"`,
or `game_object.set_state("run")`. This replaces the former Image States
component; old `g.image_states` scene records are not migrated.

Game-object hierarchies and complete UI views can be reused as linked prefabs.
Drag one selected game object from Hierarchy or a view from UI Views onto an
Assets folder to create `Name.prefab.json`. Object prefabs include their components and child
hierarchy; UI-view prefabs include view behavior and their complete widget
hierarchy. Drag an object prefab into an object hierarchy or viewport. Drag a
UI-view prefab onto an existing row in the **UI Views** panel to add it to the
scene. Each instance receives independent scene IDs. Object instances preserve
their root placement, while each scene keeps its own startup-view choice.
Editing an instance records value overrides relative to the prefab. Its
Inspector **Prefab** section shows the source, revision, and override count:
**Apply** writes the instance back to the prefab and updates all linked
instances, **Revert** removes its overrides, **Select Source** locates the
asset, and **Unpack** turns it into an ordinary independent hierarchy.

Dragging a hierarchy object or UI view directly onto an existing compatible
prefab overwrites that prefab while retaining its stable asset identity and
increasing its revision. Object and UI-view prefab kinds cannot overwrite or be
instantiated into each other's destinations.
Compatible instances keep their overrides; replacing it with unrelated
structure retains instance root placement and discards incompatible overrides.
Prefab writes and all affected open-scene instances form one undoable action.
Scenes refresh linked instances from the latest prefab revision when opened.
Missing sources retain the scene's cached instance data and report an editor
error. Object prefabs may reference project resources and objects within their
own hierarchy, but external scene-object references are rejected.

Object prefabs can also be instantiated at runtime. The returned hierarchy is
detached until it is added to a scene, and every instance receives fresh IDs:

```python
from g import instantiate_prefab

car = instantiate_prefab(
    game,
    "cars/racing-car.prefab.json",
    scene_id="player-car",
)
car.position = start.position
game.add(car)
```

Registered project scripts and custom object types are loaded from
`g.project.json` after the project is trusted. The manual option remains
available when no manifest is desired:

```bash
python -m g_editor \
    scenes/level-one.scene.json \
    --assets . \
    --module my_game.scripts \
    --module my_game.objects
```

Script components registered by those modules appear in the editor's
**Add Component** dialog. Other registered types are preserved and validated by
the runtime serializer. The editor reads and writes scene documents without
running scripts in Edit mode. Play rebuilds the authored document before
advancing the real game update loop in the editor's pyglet window.

Saving a registered project script while the editor is open reloads it
automatically (checked in Edit mode, when opening Add Component, and when
pressing Play), so there is no
need to restart the editor. Script components also pick up the edited fields:
properties that were removed from the class are dropped from the scene, and
new fields are added with their default values. Scripts without any fields
can still be attached. When a scene is loaded at runtime, stored script
properties that no longer exist on the class are ignored.
Newly created registered modules are loaded without restarting; if a script
fails its initial import, saving a corrected version triggers another attempt.
Import errors are shown in the editor, and untrusted scripts are not executed.

For a simple single-window game, `game.run(title="My Game")` creates and runs a
`Window` internally. Explicit `Window` construction remains available for
advanced window configuration.

## GUI

Every scene has one `GUIManager` with an automatically active default
`UIView`. For simple interfaces, add widgets directly:

```python
from g import Button, Label, VBox

menu = VBox(
    Label("My Game", font_size=32),
    Button("Play", on_click=start_game),
    Button("Quit", on_click=quit_game),
    spacing=12,
)
scene.gui.add(menu)
```

The default view centers fixed-size widgets. Use layout containers for
structured interfaces:

- `VBox` and `HBox` arrange controls linearly.
- `GridLayout` arranges inventory and selection grids.
- `AnchorLayout` pins one child to an edge, corner, or the center.
- `StackLayout` overlays children for panels and composed controls.

Layouts support spacing, padding, margins, minimum sizes, and growing children.
They measure desired sizes before arranging final widget bounds.

Available controls are `Label`, `Panel`, `Button`, `Toggle`, `Slider`, and
`TextInput`. `ScrollView` clips one oversized child and handles mouse-wheel
scrolling. Buttons use `ButtonStyle` for their normal, hover, pressed,
disabled, text, and font styling. Labels support left, center, and right
alignment plus multiline wrapping.

### Themes, animation, and interaction states

`UITheme` provides shared label, panel, button, slider, and text-input styles.
Set a manager-wide theme before creating widgets, or override it for one view:

```python
from g import ButtonStyle, UITheme, UIView

scene.gui.theme = UITheme(
    button=ButtonStyle(
        background=(28, 32, 48),
        hover=(45, 52, 78),
        pressed=(20, 23, 35),
    )
)
scene.gui.register("pause", UIView(theme=pause_theme))
```

Explicit widget styles still take precedence. The default theme retains the
original GUI colors and sizes, so existing constructors remain valid.

Widgets have layout-independent presentation properties: `offset_x`,
`offset_y`, `scale_x`, `scale_y`, and `opacity`. Rendering and hit testing use
the same composed parent/child transforms, while the layout rectangle remains
stable. Animate these properties with the familiar sequencing API:

```python
play_button.animate("entrance").to(
    0.25,
    offset_y=0,
    opacity=255,
).start()
```

Animation channels are independent. Starting another animation on the same
channel interrupts that channel from its current presented value without
cancelling entrance, attention, or other named channels. Handles support
pause, resume, cancel, and restart.

Buttons and toggles use theme-provided normal, hovered, pressed, focused,
selected, and disabled presentation states. State transitions run on the
reserved `interaction` channel, so they do not replace application animations.
Opacity alone does not disable input; use `visible` or `enabled` for that.

### Scrolling and desktop input

Wrap one widget in a `ScrollView` to constrain long menus or inventory lists:

```python
from g import ScrollView, VBox

list_view = ScrollView(
    VBox(*inventory_rows, spacing=6),
    width=320,
    height=420,
)
```

The scroll viewport clips its child, clamps offsets to the content bounds, and
bubbles wheel input from nested controls. Set `horizontal=True` for horizontal
content.

Focusable controls participate in Tab and Shift+Tab traversal. Enter and Space
activate focused buttons and toggles, arrow keys adjust focused sliders, and
Escape pops the top GUI view. Set `scene.gui.scale` to a positive value to
scale layout, rendering, and pointer hit testing independently of the game
camera and presentation scaling.

### Multiple GUI views

Register named views for inventory, settings, maps, dialogs, and other UI
pages:

```python
from g import AnchorLayout, UIFade, UISlide, UIView

settings = UIView(
    AnchorLayout(settings_form),
    transparent=True,
    modal=True,
    pauses_scene=True,
)
scene.gui.register("settings", settings)

scene.gui.push("settings", transition=UISlide("left", 0.25))
scene.gui.pop(transition=UIFade(0.15))
```

`replace` switches pages, while `push` and `pop` manage overlays. Views can be
transparent, modal, pause lower GUI views, or pause the scene's world update.
Modal views prevent pointer input from leaking into lower views or gameplay.

GUI views have `on_load`, `on_enter`, `on_shown`, `on_pause`, `on_resume`,
`on_exit`, `on_unload`, and `update` lifecycle hooks. `UIFade` switches views
at its opaque midpoint. `UISlide` moves the outgoing view away, switches at the
midpoint, and moves the incoming view into place. Both block GUI input by
default.

The manager performs top-down hit testing, hover tracking, keyboard focus, and
pointer capture. This keeps button clicks, slider drags, and text entry from
also activating world input. Text input focus is cleared when the window loses
focus.

Game objects with colliders expose `left`, `right`, `top`, and `bottom` as
world-pixel edge coordinates. These bounds account for parent transforms,
scaling, and rotation. Assigning an edge repositions the object to that
coordinate without changing its dimensions.

## Custom game objects

Subclass `GameObject` and add renderer/collider components when needed, then
override `update(delta_time)` for automatic per-frame behavior:

```python
from g import Collider, Game, GameObject, RectRenderer, Vec2

class Paddle(GameObject):
    def __init__(self):
        super().__init__(0, 0)
        self.add_component(RectRenderer(20, 100, (255, 255, 255)))
        self.add_component(Collider(width=20, height=100))
        self.speed = Vec2(0, 100)

    def update(self, delta_time: float) -> None:
        super().update(delta_time)

game = Game.from_grid(50, 30, 20)
paddle = game.grid.add(Paddle(), 2, 15)
```

Each enabled object is updated after input frame preparation and physics,
before its enabled components. Children are updated too; disabling an object
or an ancestor skips its updates. Visibility does not affect updates. Component
updates run independently. Every object starts with its own zero `Vec2` speed.
The base `update` moves by `speed * delta_time` in local pixels. Subclasses must
call `super().update(delta_time)` to retain this movement; component updates
still run even without that call. Set speed before calling `super`, then clamp
or check collisions afterward. Use `PhysicsBody.velocity` instead of `speed`
for physics-driven movement.

### Component dependencies

Custom components can declare structural dependencies that are enforced by
both the code API and editor:

```python
from g import (
    Collider,
    Component,
    PhysicsBody,
    requires_components,
)

@requires_components(PhysicsBody, auto_add=True)
@requires_components(Collider)
class CharacterMovement(Component):
    pass
```

`requires_components()` requires every listed type.
Subclasses satisfy a base-type requirement. `auto_add=True` constructs missing
requirements with no arguments, so use it only for components with safe
defaults. A collider's geometry is selected through its `shape` property, not
by choosing a shape-specific component class.

Adding a component through `GameObject.add_component()` first validates hard
requirements, recursively adds safe automatic requirements, detects cycles,
and rolls the operation back if it fails. Removing a component is blocked when
another attached component still depends on it. The editor uses the same
metadata to hide incompatible choices, add automatic requirements in the same
undoable operation, and prevent invalid removal.

Use `get_component(ComponentType)` when a component is optional; it returns
either the matching component or `None`. Use
`require_component(ComponentType)` for a mandatory dependency; it returns the
typed component directly and raises a descriptive `LookupError` if the object
is invalid. This keeps optional capability checks concise while avoiding
redundant `None` checks after `@requires_components(...)`.

Scene loading attaches all serialized components structurally before checking
dependencies, so their JSON order does not matter. Missing automatic
requirements are created for compatibility with older scenes; missing hard
requirements produce an explicit `SceneFormatError`. Scene and prefab files
still cannot declare new dependency rules or execute code—the decorators come
only from already trusted, imported Python modules.

## Vector positions

`g.Vec2` is a mutable wrapper around pyglet's vector math, including arithmetic,
`length()`, and `normalize()`. Every `GameObject` (and `Node`) has a writable
`position` property in local pixels, relative to its parent:

```python
from g import GameObject, Vec2

player = GameObject()
player.position = Vec2(100, 200)
player.position.x = 150
player.position.y += 10
player.position += Vec2(1, 0) * 180 * delta_time
```

`position`, `x`, and `y` all use the same coordinates. Assigning a tuple such as
`player.position = (100, 200)` is also supported, but reading `position` always
returns a `Vec2`. `position` is the object's parent-relative live vector:
modifying its `x` or `y` moves the object immediately. Assigning `position`
copies the supplied coordinates so objects do not accidentally share position
state. Use `saved = player.position.copy()` to save a position before moving.

`world_position` and `world_rotation` include all parent transforms and can
also be assigned explicitly. The engine converts assigned world values back to
the object's local transform:

```python
weapon.world_position = player.world_position
weapon.world_rotation = 90
weapon.translate(20, 0, space="world")
weapon.translate(20, 0, space="local")  # Along the weapon's rotated X axis
weapon.set_world_transform(position=(500, 300), rotation=45)
```

World-position reads return separate snapshots; modifying a returned vector
does not move the object unless it is assigned back. A world-position write
through a zero-scale parent raises `ValueError`. Scale remains local because a
general world scale under rotated, non-uniform parents may require shear.
Arithmetic and math methods return new vectors without mutating their inputs.
Mutable vectors cannot be used as dictionary keys or set members.

### Vector helpers

```python
velocity = Vec2(3, -4)
bounced = velocity.reflect(Vec2(0, 1))  # Vec2(3, 4)
horizontal = velocity.project(Vec2(1, 0))  # Vec2(3, 0)
closest = Vec2(3, 4).project_on_line(Vec2(0, 1), Vec2(10, 1))
normal = velocity.perpendicular()  # Vec2(4, 3)
turn = Vec2(1, 0).cross(Vec2(0, 1))  # 1
player.position = player.position.move_towards(Vec2(200, 200), 180 * delta_time)
```

`reflect` uses a surface normal and accepts non-unit normals. `project` projects
a vector onto a direction through the origin; `project_on_line` finds the
closest point on the infinite line through two points (not a clamped segment).
Zero normals/directions and coincident line points raise `ValueError`.
`perpendicular` rotates counter-clockwise without changing length; `cross`
returns a scalar, positive for a counter-clockwise turn. `move_towards` never
overshoots and requires a finite, non-negative maximum distance.

Pyglet's `dot`, `distance`, `length_squared`, `lerp`, `rotate` (radians), `heading`,
`from_heading`, `from_polar`, `clamp`, and `step` remain available. Arithmetic and
vector-returning math methods preserve `g.Vec2`, so the helpers can be chained.

## Pathfinding Shooter

```bash
/usr/local/bin/python3 examples/shooter/shooter.py
```

Move with WASD or the arrow keys. Aim with the mouse and hold the left mouse
button to shoot. Enemies use `NavigationAgent` to find their way around walls.
The waves contain 1, 3, 5, 7, ... enemies with a short break between waves.
Movement uses `get_axis("x")` and `get_axis("y")`; aiming and shooting use the
polled mouse position and button state.

## Physics Playground

Install the project dependencies and start the example:

```bash
/usr/local/bin/python3 -m pip install .
/usr/local/bin/python3 examples/physics/physics.py
```

Left click spawns a ball, right click spawns a box, `Space` applies an impulse,
and `R` resets all dynamic objects. `game_object.add_physics(...)` automatically
creates a body and uses the object's existing colliders as its Pymunk shapes.
Renderable shapes and sprites already have colliders; when
no explicit body is present the physics world gives each collider an automatic
static body. This makes objects physics-ready without causing ordinary level
geometry to fall. A `Collider` selects one authoritative geometry through its
validated `shape` property (`"box"`, `"circle"`, `"ellipse"`, or `"polygon"`);
add a `PhysicsBody` when an object needs explicit body behavior. Multiple
colliders on an object share its explicit body. The keyword-only constructor
accepts every geometry field regardless of the selected shape:

```python
from g import Collider

box = Collider(shape="box", width=64, height=32)
circle = Collider(shape="circle", radius=20)
ellipse = Collider(shape="ellipse", radius_x=32, radius_y=16)
polygon = Collider(
    shape="polygon",
    points=((-32, -32), (32, -32), (0, 32)),
)
```

Polygon points are convex local-pixel vertices; concave or self-intersecting
outlines are rejected rather than replaced with a hull. Ellipses use a
32-vertex convex approximation shared by physics and overlap queries, and
follow object and parent scaling and rotation. Circles keep their circular
shape under non-uniform scaling. All geometry fields are retained and
serialized even when inactive, so switching `collider.shape` keeps its
dimensions, offsets, material, filters, mode, enabled state, and component
identity (including references from `Area` and `PhysicsBody`). Physics uses a
`pymunk.Circle` for circles and `pymunk.Poly` for boxes, ellipses, and polygons.

Physics bodies may also belong to parented game objects. Physics motion,
velocity, and `PhysicsBody.teleport(x, y, angle=...)` use world coordinates;
the object's local position and rotation are updated relative to its parent,
including rotated, scaled, or mirrored parents. Nested bodies simulate
independently: parenting does not create a physics joint. A parent with zero
world scale cannot support world-to-local physics synchronization.

Colliders are the authoritative collision shape and support three modes:

Every Collider shape supports local-space `offset_x` and `offset_y` (defaulting
to zero), editable as **Offset X** and **Offset Y** in the Inspector.
Offsets move the collision shape without moving the object or its image, and
follow the object's world scale and rotation, including parent transforms.
For example, `Collider(shape="box", width=32, height=48, offset_y=-8)` shifts the box
downward relative to the object. Offset edits update physics queries and
collision shapes and are saved with the scene.

In the editor, blue outlines indicate object selection; gold outlines show the
selected objects' collider shapes. Select one object and enable **Edit Collider**
above the scene viewport to edit its collider independently of the renderer.
For objects with multiple colliders, use the adjacent collider selector. Use
the Inspector's **Shape** field to switch the selected collider in place. The
Inspector shows only that shape's geometry controls, while inactive geometry
values remain stored and reappear if switched back; existing Areas and physics
bodies continue referring to the same Collider.
Drag the gold center handle (or outline) to change the local offset. Box
edge/corner handles resize against the opposite edge, circles have radius
handles, and ellipses have independent horizontal/vertical radius handles.
Polygon vertex handles select and move points; **Shift+click an edge** inserts
a point on that edge, and **Cmd+Backspace/Delete** (**Ctrl** on other platforms)
removes the selected point. Polygons must remain convex, non-self-intersecting,
and contain at least three distinct vertices; invalid edits show an error and
retain the last valid shape. Each completed drag is one undo step. **Escape**
cancels the current drag and exits collider editing. Collider handles ignore
Grid Snap for precise geometry edits. Editing a collider does not move/resize its image
or object transform; existing renderer-to-collider size linking still applies
when editing renderer dimensions in the Inspector.

```python
from g import ColliderMode

wall.collider.mode = ColliderMode.SOLID
pickup.collider.mode = ColliderMode.SENSOR
preview.collider.mode = ColliderMode.QUERY_ONLY
```

Solid colliders participate in physical response and collision callbacks.
Sensors report overlaps without blocking objects. Query-only colliders can be
checked explicitly but do not generate automatic events. Collider material and
filter values are persisted:

```python
crate.collider.friction = 0.8
crate.collider.elasticity = 0.1
crate.collider.override_collision_filtering = True
crate.collider.categories = 0b0010
crate.collider.mask = 0b0101
```

For shared filtering, set `categories`, `mask`, and `group` on `PhysicsBody`
(or pass them to `add_physics`). Body edits update all inheriting shapes on
the next physics refresh. Local settings are retained when inheritance is
enabled, and apply again if the body is removed. Filtering defaults still
apply while the body is disabled.

Passing nondefault filter values to a collider constructor automatically
enables its override unless `override_collision_filtering=False` is supplied.
Older scenes with nondefault collider filters also retain them as overrides;
new colliders inherit by default.

Create non-blocking regions with area factories. They return plain game objects
with a sensor collider and an `Area` component:

```python
from g import Area, create_area_box

exit_area = game.add(create_area_box(700, 120, 80, 160))
area = exit_area.require_component(Area)

@area.on_enter
def finish_level(other):
    if other is player:
        load_next_level()
```

Use `create_area_box` / `create_area_circle` for unattached objects, or
`game.add_area_box` / `game.add_area_circle` to create them in the current
scene. `Area` components provide `on_enter`, `on_stay`, `on_exit`,
`contains(object)`, and `overlapping_objects`. General game objects provide `overlaps(other)`,
`collides_with(other)`, and `contains_point(x, y)`. `overlaps` includes sensors,
while `collides_with` returns a `CollisionContact` for solid-to-solid contact,
or `None` when not touching. Direct checks use each object's first collider,
read the current world transforms without requiring a physics update or scene,
and ignore collision masks/groups, as before. Disabled colliders do not collide.
Use truthiness or `is not None`, rather than comparing the result with `True`.
`collides_with_any` still returns a boolean and `collisions_with` still returns
the touching objects.

An `Area` selects a collider by its position among the owning object's
`Collider` components (`collider_index`, default `0`); non-collider components
do not affect that index. Its event callbacks and overlap queries use only the
selected sensor shape and respect collider collision filters. `on_enter`,
`on_stay`, and `on_exit` callbacks receive only `GameObject` overlaps; use the
object's `on_tile_overlap` callbacks for tile layer contact.

Every `Game` provides its `PhysicsWorld` through `game.physics`, including
engine-target queries. Results are `GameObject` instances for ordinary
colliders and `TileLayer` instances for generated tile collision:

```python
objects = game.physics.overlap_circle((200, 150), 100)
objects = game.physics.overlap_box((300, 200), (120, 80))
objects = game.physics.overlap_point(game.input.mouse_position)
objects = game.physics.overlap_polygon(
    ((100, 100), (220, 100), (160, 240)),
)
first = game.physics.raycast(player.position, game.input.mouse_position)
all_hits = game.physics.raycast_all(
    player.position,
    game.input.mouse_position,
)
```

All overlap and raycast methods accept `exclude=...` to omit known game objects
or tile layers. This is particularly useful when a ray begins inside its
shooter:

```python
target = game.physics.raycast(
    muzzle.position,
    aim_position,
    exclude=(player, weapon),
)
```

Use `raycast_hit` and `raycast_all_hits` when impact geometry is needed.
`RaycastHit` provides the target, world-space point and normal, the fractional
position along the ray, and world-space distance. Results are ordered nearest
first. The object-only `raycast` and `raycast_all` methods are convenience
versions of the same query.

```python
hit = game.physics.raycast_hit(muzzle.position, aim_position)
if hit is not None:
    spawn_impact(hit.point, hit.normal)
```

`nearest(point, max_distance=...)` returns a `PointQueryHit` containing the
nearest target, closest surface point, signed distance, and outward gradient.
It returns `None` when no target is within the optional maximum distance.

For contact geometry, `overlap_circle_hits`, `overlap_box_hits`, and
`overlap_polygon_hits` return `OverlapHit` values with the target,
query-to-target normal, world-space contact points, and penetration depth.
Their object-only counterparts return each target once. Polygon query vertices
must form a finite, non-self-intersecting convex polygon.

Mouse and keyboard actions remain separate from physics and are queried with
`game.input.just_pressed(...)`, `get_axis(...)`, and related input methods.

### Top-down motor

`TopDownMotor` converts movement intent into accelerated, normalized
velocity. It automatically adds or reuses a dynamic body, disables gravity for
that body, and locks rotation:

```python
player = game.add_sprite(100, 100, "player.png")
movement = player.add_top_down_motor(
    speed=220,
    acceleration=1200,
    deceleration=1600,
)

def update(delta_time):
    del delta_time
    movement.move(
        (
            game.input.get_axis("x"),
            game.input.get_axis("y"),
        )
    )
```

Call `move()` every update that should provide movement intent. Diagonal input
is normalized. If no intent is provided for a frame, the controller decelerates
the body toward rest. `movement.velocity` and `movement.moving` expose its
current state.

### Platformer motor

`PlatformerMotor` uses world gravity and Pymunk contact normals for
grounding. It provides ground and air acceleration, constant-speed movement
along walkable slopes, slope filtering, coyote time, jump buffering, and a
maximum falling speed:

Adding or pasting Platformer Motor in the editor sets its Collider
friction to zero, including when a script adds the motor as a dependency.
The motor controls braking instead of contact friction slowing the player on
landing. This one-time setup is undoable; friction remains editable afterward.
Existing motors are not changed automatically. For code-created players, set
`player.collider.friction = 0` explicitly.

```python
from g import Key

player = game.add_sprite(100, 150, "player.png")
movement = player.add_platformer_motor(
    speed=240,
    jump_speed=520,
    max_slope=50,
    ground_snap_distance=8,
    coyote_time=0.1,
    jump_buffer_time=0.1,
    allow_double_jump=False,
)

def update(delta_time):
    del delta_time
    movement.move(game.input.get_axis("x"))
    if game.input.just_pressed(Key.SPACE):
        movement.jump()
```

Useful state includes:

```python
movement.grounded
movement.falling
movement.velocity
movement.ground_normal
movement.ground_angle
movement.ground_objects
movement.ground_velocity
movement.relative_velocity
```

PlatformerMotor handles solid moving GameObject supports automatically. Grounded
walking, acceleration, and braking are relative to the supporting surface:
an idle player follows its velocity, while walking adds the player's surface
speed. `ground_velocity` is the support velocity at the contact (including
angular motion); `relative_velocity` subtracts that support velocity from the
player's world velocity. Use relative velocity for idle/run animations.
The motor retains its chosen support while it remains underfoot. Static objects
and tilemaps contribute zero support velocity.

Upward and downward platforms participate in ground contact and snapping.
Jumping adds the support's vertical velocity to the jump speed and keeps its
horizontal momentum; walking off also retains horizontal support momentum.
After detaching, later platform velocity changes are not applied to the player.
No parenting or extra position translation is needed. Keep player collider
friction at zero so the motor, rather than friction, controls relative motion.

PlatformerMotor updates in a dedicated pass after ordinary object/component
scripts, before Area updates. Input and platform scripts therefore prepare
intent and support velocities before the motor runs, regardless of attachment
or layer order. Physics continues to integrate these velocities on the next
simulation step.

Enable **Allow Double Jump** in the Platformer Motor Inspector, or set
`allow_double_jump=True`, to allow one extra airborne jump at `jump_speed`.
It works while rising or falling and resets on landing. A coyote-time jump
counts as the ground jump and does not consume the extra jump. Walking off a
ledge also leaves one airborne jump available. The option defaults to off;
the existing Player script's `jump()` calls need no changes.

`speed` measures distance along the surface while grounded, not horizontal
distance. `ground_snap_distance` is the maximum downward correction in world
units when an already-grounded character loses contact, for example when
walking downhill or crossing a tile join. Set it to zero to disable snapping.
When ground snapping is enabled, foot probes also determine the supporting
surface angle during contact, avoiding misleading overlap normals at triangle
joins. Nearby foot samples preserve a matching slope normal across tiny caps
and insets in generated polygons, but require real nearby support.
The same distance bounds recovery upward over small slope joins; upward
corrections are rejected if they would penetrate blocking geometry.
Existing solver contacts are left in place unless such recovery is needed;
only lost contact triggers a downward correction.
It does not snap airborne characters onto nearby platforms, cross larger
drops, or pull a jumping character back down.

The physics body remains upright. To tilt the character, put its renderer on
a child object and assign `visual.rotation = movement.ground_angle` from your
player script. The angle uses the engine's clockwise rotation convention and
returns to zero when airborne; optionally smooth the visual rotation.
Slope tiles need polygon collision matching their diagonal, not full-cell
rectangle collision.

Motors consume intent rather than reading keys directly, so the same
movement component works with keyboard, gamepad, AI, replay, or network input.
Motor and physics-body settings are preserved by scene persistence.

## Window Shake Physics

```bash
/usr/local/bin/python3 examples/physics_shake/physics_shake.py
```

Drag the window by its title bar and shake it. The example uses
`window.on_move(...)` to translate native window movement into velocity and
rotation changes for the dynamic physics bodies. Faster movement and stronger
acceleration produce a larger response; event spikes are clamped for stability.

## Navigation

`Game` creates a `NavigationWorld` automatically. Obstacles are discovered
from the current scene whenever a path is requested:

```python
from g import Game, GameObject, NavigationAgent, NavigationObstacle

game = Game.from_grid(20, 20, cell_size=32)

wall = GameObject()
wall.add_component(NavigationObstacle())
game.grid.add(wall, 8, 8)

player = GameObject()
agent = player.add_component(NavigationAgent(speed=120))
game.grid.add(player, 2, 2)

agent.move_to((15, 10))
```

Moving, disabling, or removing `wall` is reflected automatically by the next
path request. Use `game.navigation.set_cost((x, y), cost)` for terrain costs;
passing `None` blocks a cell.
