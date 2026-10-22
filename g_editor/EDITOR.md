# G Editor

An Editor that supports the game creation in the g engine (python and 2D only game engine).

## Panel architecture

The editor window and frame lifecycle remain in `application.py`. Docked panel
behavior is split across `g_editor/panels/`:

- `layers.py` owns layer management and object/GUI hierarchy interactions.
- `tools.py` owns the contextual viewport-tool row and the dedicated tile
  palette panel.
- `tilemap.py` owns tileset configuration for the active tile layer.
- `assets.py` owns the project asset tree, preview, picker, import, and drag
  interactions.
- `inspector.py` owns scene, layer, object, component, GUI, and tile property
  editing.

`ImGuiEditor` composes the panels through multiple inheritance so existing
event routing and private interaction points remain stable. `EditorPanel`
declares the typed state and cross-panel operations available to each panel.
Panels coordinate through the shared `EditorState` and these declared
operations rather than importing or instantiating one another. Persistent
scene changes belong in `EditorState`; transient ImGui interaction state
remains on the editor application.


What this editor should do:
- place gameobjects and change properties
    - create sprites from project images and keep their default collider sized
      to the selected image
- keep layer management and active-layer content in separate dockable panels
    - layers selects the active layer and controls render order, naming, visibility, and defaults
    - UI Views is a separate panel tabbed with Layers by default
    - object layers show their scene hierarchy below Layers
    - tile layers show tabbed tileset configuration below Layers
    - the second viewport row adapts its tools to the active layer type
    - a dedicated center-column Tile Palette panel shows tabbed palettes
- use native color pickers for scene and component color values
- expose Sprite Renderer Flip X/Flip Y as persisted visual-only mirroring controls
- expose Sprite Animator components through a dedicated dockable Animation panel:
    - share the default bottom tab group with Tile Palette and Console
    - one component for static image states, decoded animation assets, and timed frames
    - named animation states with a persisted default state and Autoplay
    - ordered image frames with per-frame durations
    - per-state Loop controls and add/remove/reorder actions
    - image selection and clearing through the normal Assets picker and drag/drop workflow
    - simulation-time playback only in Play, without changing collider dimensions
    - an isolated Edit-mode preview with play/pause/stop and scrubbing, never gameplay
    - mirror preview frames onto the selected Edit viewport sprite during rendering only; Stop restores the normal appearance
    - platform rename/delete shortcuts in the state list and inline state renaming
    - a labeled preview to the right of frame details, with play/pause/stop icons
    - keep submitted preview/timeline textures alive through rendering, even when editing replaces a clip
    - proportional frame timelines with drag resizing, frame reordering, and undo
    - multi-image Assets selection and drag/drop; GIF timing overrides without modifying source assets
    - sprite-sheet grid slicing from a selected image asset, with cell size,
      margin, spacing, visual cell selection, and selection-ordered frame import
    - an Inspector summary and shortcut to open the panel
- create and edit multi-emitter `.particle.json` assets in a dedicated dockable
  Particles panel with isolated play, pause, restart, stop, and step preview
- choose an empty effect or a built-in fire, smoke, sparks, explosion, rain, or
  snow preset when adding a Particle Effect component; presets become normal
  editable project assets
- assign particle assets through the Inspector and preview texture, size,
  color, emission, spawn, and motion changes without running gameplay
- combine Particle Effect and Trail Renderer components for effects such as
  the drift sparks, tire marks, and oil splashes in the racing example
- use a consistent custom dark ImGui theme with clear active states
- use bundled Inter typography throughout the editor
- use a persistent dockable workspace with a useful default layout
- keep action/error messages, asset-root path, and zoom in a fixed bottom status bar
- use a compact second viewport-toolbar row and ImGuizmo for transforms
    - expose Global and Local spaces for move and rotate
    - force multi-object movement to Global and keep scale Local
    - show matching world/local position and rotation fields in the Inspector
    - use highlighted Font Awesome Move, Rotate, Scale, Global, and Local
      buttons with shortcut and disabled-state tooltips
    - keep Save in the File menu and Cmd/Ctrl+S rather than the tool row
- keep Play/Stop and grid controls in a toolbar above the viewport
    - choose Transform, Edit Collider, or Edit Spline from the viewport tool
      dropdown; unavailable component tools explain their requirements
    - use the Inspector's Edit in Viewport / Done Editing buttons to activate
      the same tool, targeting that exact component
    - choose the target when several compatible components are present
    - component tools suppress transform gizmos but retain camera navigation
    - changing selection, removing the target, or entering Play exits editing
    - Escape cancels an active drag first; a second Escape returns to Transform
    - retain the magnet toggle for Grid Snap
- show gold collider outlines separately from blue object-selection bounds
    - Edit Collider mode edits one selected object's Collider in box, circle,
      ellipse, or polygon shape
    - select the active collider when an object contains multiple colliders
    - drag the center/outline for local offsets and shape handles for dimensions
    - viewport collider handles use larger dots and a 16-pixel hit radius;
      hover handles/outlines or drag to see the hand cursor
    - switch Shape in the Inspector without replacing the component; retain all
      geometry fields and keep Area/physics-body references attached
    - drag polygon vertices, Shift+click an edge to insert a vertex, and
      Cmd/Ctrl+Backspace/Delete to remove a selected vertex (minimum three)
    - enforce convex, non-self-intersecting polygons with explicit errors
    - commit each drag as one undoable edit; Escape cancels without saving
    - collider edits do not modify renderer dimensions or object transforms
    - collider editing ignores Grid Snap; normal object transform snapping is unchanged
- author Catmull-Rom paths using the Spline component, independently of renderers
    - local control points move with the object's transform
    - selected splines show purple editor-only curves; Edit Spline adds control
      polygons and draggable points
    - Shift+click the curve inserts a point in that segment; Delete/Backspace
      removes the selected point (minimum two open, three closed)
    - precise Points, Closed, and Tolerance values remain editable in the Inspector
    - adjacent coincident points and invalid settings report errors without
      changing the document; every drag is one undoable change
    - runtime world-space length and distance queries support enemy/camera
      movement at approximately constant speed, including nonuniform scaling
- support homogeneous multi-selection within the active layer
    - hierarchy Up/Down immediately replaces the selection and updates the
      Inspector; use Shift+Up/Down to extend a range
    - hierarchy rows use one editor selection highlight, not a separate keyboard
      navigation cursor; Enter on macOS (F2 elsewhere) renames the selected item
    - click replaces, Cmd/Ctrl-click toggles, and hierarchy Shift-click selects
      a visible top-to-bottom range
    - empty left-drag creates a marquee; Shift adds and Cmd/Ctrl toggles
    - move selected objects together while normalizing selected parent/child
      targets to avoid applying a world-space move twice
    - hold Space and left-drag, middle-drag, or right-drag to pan; a stationary
      right-click still opens viewport context actions
    - keep rotate and scale disabled for multiple objects until group-pivot
      transforms are implemented
    - Cmd/Ctrl+C, Cmd/Ctrl+V, and Cmd/Ctrl+X copy, paste, and cut recursive
      object or GUI selections
        - hierarchy paste uses the single selected container or the layer root
        - viewport paste offsets immediate duplicates or uses a viewport
          position clicked after copying
        - viewport object paste keeps each source parent, converting placement
          into parent-local coordinates even for rotated/scaled parents;
          an unavailable parent reports an error without inserting objects
        - pasted subtrees receive fresh stable IDs
- use explicit **New Scene**, **Load Scene**, **Save Scene**, and
  **Save Scene As** actions in the top File menu
- load `.scene.json` files by double-clicking them or choosing **Load Scene**
  from their Assets context menu, with the normal unsaved-changes prompt
- use native platform dialogs for scenes and atlas images
- keep shared controls and theme code in a reusable editor UI component library
- keep all persisted resources below the project asset root
    - display assets as a folder tree with a right-side image preview
    - use the configured start directory as the tree root without creating an
      extra `Assets` directory
    - import external files and folders by dropping copies into Assets
    - create uniquely named folders immediately from the Assets `+` menu
    - rename selected objects, GUI elements, layers, files, and folders inline
      with Return on macOS or F2 on Windows/Linux
    - update known references in the open scene when an asset is renamed
    - use typed, read-only asset-reference fields instead of filesystem browse
      controls
    - show an abbreviated project-relative path with a full-path tooltip and a
      circle-dot button embedded into the field and dedicated to entering
      picker mode
    - temporarily highlight Assets while a field is choosing a resource
    - disable incompatible file extensions during field selection
    - leave selection mode after assignment, Escape, a repeated picker-button
      click, or any unrelated editor interaction
    - drag assets directly onto compatible fields
    - drag an image into an object-layer viewport to create a root Sprite at
      its dropped world position
- use inherited typed property components for text, integer, float, slider,
  boolean, choice, color, JSON, and asset values
    - keep the value control on the left with a shared maximum width
    - align the field name in a consistent column to the right
- support custom gameobjects that the user has programmed
- play testing
- support ordered object and tile world layers plus separate named UI views
    - every scene has an automatically created default layer
    - the default is always an object layer
    - adding an object without choosing a layer uses the default object layer
    - layers render in an explicit, editor-reorderable order
    - layers can be named, hidden, and disabled
    - object layers have independent scene graphs and render batches
        - each game object belongs to exactly one object layer
        - parent-child relationships cannot cross layer boundaries
        - moving a parent to another object layer moves its complete subtree
        - hierarchy order controls drawing order inside a layer; the editor
          derives persisted z-index values for runtime compatibility
    - tile layers store compact, chunked tile IDs and transform flags
        - individual tiles are data, not game objects
        - cells store layer-global IDs from stable, non-overlapping tileset ranges
        - each non-empty chunk renders one atlas mesh per tileset it uses
        - chunks outside the camera are culled
        - cells may use negative coordinates
    - UI views store real UIView widgets in a free-positioned screen-space canvas
        - new scenes include a startup HUD view
        - view navigation properties are edited in the Inspector
        - support Panel, Label, Button, Toggle, Slider, and Text Input elements
        - panels can contain nested elements with panel-relative coordinates
        - support Stack, Vertical, Horizontal, Grid, and Anchor layout containers
        - automatic layouts own child positioning while exposing margins,
          growth, spacing, padding, alignment, columns, and anchors
        - create elements from a hierarchy-local `+` type menu
        - select and drag elements in the viewport
        - edit position, size, text, font size, and colors
        - drag rows to reorder siblings, reparent into containers, or move
          elements back to the layer root
        - preserve game-object world transforms and free GUI screen positions
          across parent changes
    - game objects can be nested from the Hierarchy `+` type menu
    - layers share scene physics, navigation, input, resources, and camera
- script component support
- adding and removing components
- activate and deactivate components
- grid support
- brush support
    - create a tile layer and configure multiple tabbed atlas grids
    - add and remove tilesets without renumbering the remaining global IDs
    - confirm removal with a usage count when painted cells still use a tileset
    - render matching tabs in the dedicated Tile Palette panel
    - automatically match the scene grid to the loaded tile width
    - select one tile or a rectangular stamp in a palette
    - paint stamps, repeat them with Rectangle Fill, or erase cells
    - rotate palette stamps and placed tile selections clockwise in 90-degree steps
    - flip palette stamps and placed tile selections horizontally or vertically
    - use Random as a Pencil/Rectangle Fill modifier over the selected tile pool
    - support rectangular and polygon tile collision metadata
    - generate collision from opaque or transparent tile pixels, full tiles, or existing metadata
    - regenerate collision definitions for the entire active tileset
    - edit tile collision with the same gold handles as viewport colliders:
      drag rectangle edges/corners or centers, and drag polygon vertices or outlines
    - Shift+click an edge to insert a polygon point; select a point and use
      Cmd/Ctrl+Backspace/Delete to remove it (at least three vertices remain)
    - tile polygon editing is mouse-driven, without coordinate lists or point add/remove modes;
      valid concave outlines and multiple polygons remain supported
    - use Add Polygon to drag out another rectangle on the tile; existing
      rectangles are retained as four-point polygons when adding a second shape
    - tile handles have a 16-pixel hit radius and larger dots; hovering handles
      or outlines and dragging shows a hand cursor
    - complete each tile collision drag as one undo step, or cancel with Escape
    - support tile navigation metadata, animation, tags, and custom JSON properties
    - batch-edit colors, collision, navigation, tags, and custom properties for selected tiles
    - keep built-in engine fields separate from custom game fields
    - generate physics and navigation data from enabled tile layers in a later step
- export game
- game objects and complete UI views can be saved as typed, linked
  `.prefab.json` assets, instantiated by drag and drop into matching editor
  destinations, overwritten from Hierarchy/Layers, overridden, reverted,
  applied, or unpacked


How should creating a new game for the user work?
- user installs g python package + editor
- user starts the editor and creates a new project (first view) when no project is passed as arg selected
- a new folder is created with a structure
- a simple example scene is created and loaded in the editor
- then there is a code folder where there is a code template that loads the scene and runs the game
- the user can expand the code to add e.g. custom game objects that he can place in the editor