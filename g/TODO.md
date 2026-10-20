# Roadmap

The goal is a beginner-friendly 2D game library that can grow into a capable
game engine without requiring advanced systems for simple games.

## Completed foundations

- [x] Sound loading and playback
- [x] Cached images, animations, sounds, and custom fonts
- [x] Text rendering and custom font support
- [x] Pixel-based games with an optional grid subsystem
- [x] Circles, rectangles, lines, triangles, polygons, fills, and outlines
- [x] Single-active-scene management, lifecycle hooks, and fade transitions
- [x] 2D cameras and world/screen-space rendering
- [x] GUI controls, views, focus, transitions, and layout managers
- [x] Physics bodies and basic collision queries/callbacks
- [x] Grid navigation and pathfinding
- [x] Behaviours and property animations with easing, repetition, and parallel
      steps
- [x] Per-object shaders and scene post-processing pipelines
- [x] Serializable script components with scene-aware lifecycle hooks

## Scene persistence and reusable content

- [x] Define a human-readable prototype scene format
- [x] Serialize scene hierarchy, transforms, layers, components, and settings
- [x] Store resource references as portable paths rather than embedded objects
- [x] Add stable object IDs so references between serialized objects survive
      saving and loading
- [x] Add a type registry for serializing custom game objects and components
      without executing arbitrary code from scene files
- [ ] Version the scene schema and add migrations after the prototype format
      is declared stable
- [x] Add linked JSON prefabs with stable template IDs, instance overrides,
      apply/revert/unpack, drag-and-drop creation, and revision refresh
- [ ] Keep scene serialization separate from save-game state serialization
- [ ] Add save slots and a small API for game progress and player settings

## Visual scene editor

- [x] Create a standalone ImGui scene-editor application that reads and writes
      the engine's scene format
- [x] Add move, rotate, and scale gizmos plus pan and zoom controls to the
      viewport
- [x] Add grid display, snapping, and configurable cell sizes
- [x] Add a scene hierarchy panel and component/property inspector
- [x] Add image-backed Sprite creation, asset-browser placement, resource
      reassignment, and automatic collider sizing
- [x] Add folder-based Assets navigation, image previews, external file/folder
      imports, typed asset fields, and viewport Sprite drops
- [x] Add nested game-object and GUI hierarchies with hierarchy-local creation
      menus and container-relative GUI positioning
- [x] Add a project resource browser
- [x] Render the viewport to an ImGui texture with the real pyglet engine
      renderer and no CPU framebuffer readback
- [x] Synchronize editor changes and stable-ID picking in the shared pyglet
      render context
- [x] Add collapsible game-object and component inspector sections
- [x] Add Inter typography, dockable persistent layouts, reusable UI
      components, native file dialogs, ImGuizmo, and a compact Tools panel
- [x] Add drag-and-drop object placement and reusable prefab items
- [x] Add ordered object and GUI multi-selection, hierarchy ranges, viewport
      marquee selection, group movement/deletion, and Space/middle-button pan
- [x] Add hierarchy and GUI drag-and-drop reordering/reparenting with
      transform preservation and hierarchy-derived render order
- [x] Add explicit writable world position/rotation APIs and Local/Global
      editor transform spaces while keeping scale local
- [x] Add focus-aware copy, paste, and cut for recursive game-object and GUI
      selections in hierarchies and the viewport
- [x] Add undo/redo, duplicate, and context menus for scene and asset actions
- [x] Add independent scene layers with visibility, activation, editor locking,
      default placement, and per-layer z-index ordering
- [x] Add persistent named UI views separate from world layers, backed by
      UIView widgets with
      free-positioned Panel, Label, Button, Toggle, Slider, and Text Input
      editor tools
- [x] Add persistent Stack, Vertical, Horizontal, Grid, and Anchor GUI layout
      containers with layout-aware Inspector controls and viewport picking
- [ ] Add camera/UI preview modes
- [x] Add play/stop-in-editor controls with temporary runtime state
- [ ] Add validation for missing resources and unsupported serialized types

## Tile maps and world building

- [x] Use the native scene editor as the primary tile-map
      workflow
- [x] Add an efficient tile-map renderer with layers, chunks, and large-map
      culling
- [x] Generate static collision data from selected tile layers
- [ ] Generate navigation data from selected tile layers
- [x] Support animated tiles, object layers, built-in tile metadata, and custom
      tile properties
- [x] Add multiple tilesets with stable global IDs and per-texture chunk meshes
- [x] Add asset-free fixed-color tile palettes for rapid prototyping
- [x] Add tilesets assembled from individual image assets
- [x] Add tabbed palette configuration, rectangular stamps, rectangle fill,
      random painting, pencil, and eraser tools
- [x] Edit tile metadata in Inspector and confirm deletion of in-use tilesets
- [ ] Revisit optional TMX/Tiled import after the native scene format and tile
      model are stable

TMX should be an import adapter rather than the engine's primary scene format.
This keeps Tiled available for users who prefer it without forcing the engine
or its editor to adopt all of Tiled's data model.

## Building and exporting games

- [ ] Add a project manifest for title, version, entry point, assets, icon, and
      display settings
- [ ] Add a command that validates a project and creates a distributable build
- [ ] Bundle Python, engine dependencies, shaders, fonts, audio, and other
      declared resources
- [ ] Start with PyInstaller-based desktop builds while keeping the packaging
      layer replaceable
- [ ] Support Windows, macOS, and Linux build targets
- [ ] Add fullscreen, window scaling, high-DPI, and resolution configuration
- [ ] Produce clear diagnostics for missing assets and unavailable media
      decoders
- [ ] Add release-build smoke tests and a minimal starter project/template

## Rendering and visual effects

- [ ] Add sprite-sheet and texture-atlas loading
- [ ] Add particles with reusable emitters
- [ ] Add 2D lights, optional shadows, and normal-map support
- [ ] Add render-to-texture surfaces and multiple camera viewports
- [ ] Add reusable built-in post-process effects such as CRT, grayscale,
      vignette, blur, bloom, and pixelation
- [ ] Add optional reduced-resolution passes for expensive effects
- [ ] Add asset and shader hot reloading for development

## Animation

- [ ] Add reusable animation clips and named animation libraries
- [ ] Add sprite-sheet frame animations and animation events
- [ ] Add simple animation state machines for idle/run/jump/attack workflows
- [ ] Add animation queues and explicit property-conflict handling
- [ ] Preserve `object.animate()` as the simple beginner API

## Audio

- [ ] Add music playback with crossfading
- [ ] Add master, music, effect, and UI volume buses
- [ ] Add mute and persisted volume settings
- [ ] Add optional positional audio for world objects

## Input and platform support

- [ ] Add gamepad/controller support
- [ ] Add configurable and persisted input bindings
- [ ] Add input actions so games are not tied directly to physical keys
- [ ] Add touch input if a supported deployment target requires it
- [ ] Add accessibility helpers for keyboard-only UI navigation, text scaling,
      and reduced-motion settings

## Physics and navigation

- [x] Add intent-driven top-down and platformer character controllers
- [x] Add collision layers and masks
- [x] Add ray casts, shape queries, and point queries
- [ ] Expose richer collision information such as contact points and normals
- [ ] Add joints and constraints through beginner-friendly helpers
- [ ] Add navigation-map rebuilding and dynamic obstacle updates

## Timing and game flow

- [ ] Expose engine-managed clocks based on `pyglet.clock.Clock`
- [ ] Add game-scoped and scene-scoped delayed/interval callbacks
- [ ] Pause scene timers automatically when their scene is paused
- [ ] Return cancellable handles and clean timers up when scenes unload
- [ ] Add optional fixed-timestep simulation for deterministic gameplay

## Developer experience

- [ ] Add a debug overlay for FPS, frame time, object counts, physics, and
      navigation
- [ ] Add configurable logging and clear runtime error messages
- [ ] Add visual collider, bounds, anchor, camera, and navigation debugging
- [ ] Add headless testing helpers and deterministic test clocks
- [ ] Add API reference documentation and more complete example games
- [ ] Add project templates for procedural and scene-class styles
- [ ] Define API stability and deprecation rules before a stable release

## Possible future scope

- [ ] Localization and translated resource bundles
- [ ] Mod/plugin discovery with an explicit safe loading model
- [ ] Networking only after the local update, serialization, and deterministic
      simulation contracts are stable
