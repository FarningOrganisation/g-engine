# Component-Based Particle System

## Status

Design and implementation plan. The multi-emitter definitions, packed
pure-Python simulation, playback controller, `ParticleEffect` renderer,
versioned asset format, scene persistence, and initial Particles editor are
implemented. Later phases in this document remain planned.

## Goals

The particle system should:

- provide a `ParticleEffect` component that can be attached to a `GameObject`;
- support common 2D effects such as fire, smoke, sparks, explosions, rain,
  trails, ambient particles, and UI effects;
- use g-owned public APIs and persisted formats;
- simulate efficiently in pure Python without creating a Python object or
  pyglet sprite for every particle;
- render particles in batches through pyglet 2 and g's layer ordering;
- be deterministic when given a seed;
- support both continuous emitters and one-shot bursts;
- provide reusable particle-effect assets;
- support isolated Edit-mode preview in a dedicated editor panel;
- leave room for an optional native or GPU simulation backend without changing
  projects or public APIs.

## Non-goals for the first version

The first version will not include:

- particle-to-particle interaction;
- particle collision with physics shapes;
- sub-emitters triggered by particle events;
- ribbons, trails, mesh particles, or animated texture sheets;
- a visual node graph;
- arbitrary timeline scrubbing;
- GPU simulation;
- a mandatory C extension or NumPy dependency.

These features should remain possible without complicating the initial data
model.

## Terminology

- **Effect**: the component instance attached to a game object.
- **Definition**: persistent authoring data describing an effect.
- **Emitter**: emission settings and spawn shape within a definition.
- **Particle**: one short-lived simulated item in an effect.
- **Simulation space**: whether emitted particles continue in world
  coordinates or follow the owner.
- **Backend**: the implementation that stores and updates live particles.

## High-level architecture

```text
GameObject
  |
  +-- ParticleEffect(Renderer)
        |
        +-- ParticleEffectDefinition
        |     +-- ParticleEmitterDefinition
        |     +-- ParticleMotionDefinition
        |     +-- ParticleAppearanceDefinition
        |
        +-- ParticleSimulation
        |     +-- PythonParticleSimulation
        |     +-- optional future native/GPU implementation
        |
        +-- ParticleDrawable
              +-- pyglet vertex list
              +-- shader
              +-- texture and blend state
```

`ParticleEffect` is both the playback component and the renderer. Keeping these
responsibilities in one public component provides predictable layer ordering,
visibility, ownership, and cleanup. Simulation and rendering remain separate
internal objects so either can be optimized independently.

The component owns a private simulation. There is no module-global particle
system. Editor previews also receive independent simulations so previewing an
asset cannot alter a running scene.

## Public API

### Built-in presets

g includes typed source presets for `fire`, `smoke`, `sparks`, `explosion`,
`rain`, and `snow`:

```python
definition = g.particle_preset("fire")
```

Each call returns a fresh validated definition. In the editor, choosing a
preset while adding a Particle Effect component writes that definition to a
normal project-local `.particle.json` asset and assigns it to the component.
The generated asset is independent of the built-in preset and can be edited
freely.

### ParticleEffect

```python
effect = game_object.add_component(
    g.ParticleEffect(
        definition=g.ParticleEffectDefinition(
            emitters=(
                g.ParticleEmitterDefinition(
                    name="Sparks",
                    capacity=1_000,
                    duration=2.0,
                    loop=True,
                    rate=100.0,
                    lifetime=g.FloatRange(0.5, 1.2),
                    speed=g.FloatRange(50.0, 120.0),
                    direction=90.0,
                    spread=30.0,
                    motion=g.ParticleMotionDefinition(
                        gravity=(0.0, -80.0),
                    ),
                ),
            ),
        ),
        autoplay=True,
    )
)

effect.play()
effect.pause()
effect.resume()
effect.stop(clear=False)
effect.clear()
effect.emit(30)
```

Proposed component properties:

```python
class ParticleEffect(Renderer):
    definition: ParticleEffectDefinition
    image: Image | None
    autoplay: bool
    simulation_space: ParticleSimulationSpace
    blend_mode: ParticleBlendMode
    seed: int | None

    @property
    def playing(self) -> bool: ...

    @property
    def paused(self) -> bool: ...

    @property
    def particle_count(self) -> int: ...

    def play(self, *, restart: bool = False) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self, *, clear: bool = False) -> None: ...
    def clear(self) -> None: ...
    def emit(self, count: int) -> None: ...
```

Behavior:

- `play()` starts continuous emission and scheduled bursts.
- Calling `play()` while already playing is a no-op unless `restart=True`.
- `pause()` freezes simulation and emission.
- `resume()` continues from the current state.
- `stop(clear=False)` stops new emission but lets existing particles expire.
- `stop(clear=True)` also removes all live particles.
- `clear()` removes particles without changing the playback state.
- `emit(count)` emits an immediate burst and validates that `count` is
  non-negative.
- Disabling the component freezes simulation and hides its particles.
- Detaching the component releases its CPU and GPU resources.

### Definitions

Definitions are typed g values, not backend objects:

```python
@dataclass(frozen=True)
class ParticleEffectDefinition:
    emitters: tuple[ParticleEmitterDefinition, ...] = ...


@dataclass(frozen=True)
class ParticleEmitterDefinition:
    name: str = "Emitter"
    enabled: bool = True
    capacity: int = 1_000
    duration: float | None = None
    loop: bool = True
    warmup: float = 0.0
    rate: float = 10.0
    bursts: tuple[ParticleBurst, ...] = ()
    lifetime: FloatRange = FloatRange(1.0, 1.0)
    speed: FloatRange = FloatRange(0.0, 0.0)
    direction: float = 0.0
    spread: float = 360.0
    shape: ParticleSpawnShape = ParticlePoint()
    motion: ParticleMotionDefinition = ...
    appearance: ParticleAppearanceDefinition = ...
```

Exact class names may be adjusted to follow existing resource and inspector
patterns, but the separation between runtime state and definitions should be
preserved.

Definitions must validate at construction and load time:

- capacity is positive and has a documented safety maximum;
- duration, warmup, rate, lifetime, and burst times are finite and
  non-negative;
- lifetime is strictly positive;
- ranges are ordered;
- curve times are ordered and contained in `[0, 1]`;
- colors and scalar curve values are finite;
- spawn-shape dimensions are non-negative;
- spread is between 0 and 360 degrees;
- unsupported enum values produce an explicit format error.

Invalid definitions must not silently fall back to defaults.

## First-version features

### Emission

- Continuous rate measured in particles per second.
- Timed bursts with `time`, `count`, and optional repeat interval/count.
- Explicit runtime bursts through `emit(count)`.
- Finite or indefinite duration.
- Optional looping and warmup.
- Fixed capacity with deterministic overflow behavior.

When capacity is exhausted, new particles are dropped. Existing particles are
not removed to make room. The runtime exposes a dropped-particle counter for
editor diagnostics.

Fractional continuous emission is accumulated between updates:

```text
pending += rate * delta_time
emit floor(pending)
pending -= floor(pending)
```

### Spawn shapes

The first version supports:

- point;
- line;
- rectangle, on the edge or throughout the area;
- circle, on the edge or throughout the area.

Shapes are represented by g definitions. They must not expose classes from a
third-party simulation backend.

### Motion

The first version supports:

- initial speed range;
- direction and angular spread;
- constant gravity;
- linear drag;
- initial rotation and angular velocity.

### Appearance

The first version supports:

- one texture per effect;
- start-to-end size curve;
- color and opacity gradient over normalized lifetime;
- alpha and additive blend modes;
- untextured soft-circle fallback when no image is assigned.

Curves and gradients use a small ordered set of piecewise-linear keys. An
implementation may initially evaluate them on the CPU. The renderer may later
move evaluation into a shader without changing persisted definitions.

### Simulation space

`ParticleSimulationSpace` has two values:

- `WORLD`: a particle keeps its emitted world position when its owner moves;
- `LOCAL`: live particles remain relative to the owner and inherit its current
  transform.

World space is the default for smoke, sparks, rain, and explosions. Local space
is useful for auras and UI effects.

Owner scale affects the local spawn shape. It does not change particle lifetime
or velocity magnitudes. Mirrored owner scales must produce predictable mirrored
spawn positions and directions.

## Pure-Python simulation backend

The first backend uses preallocated packed storage. It must not allocate a
particle object or sprite per live particle.

```text
capacity
alive_count
position_x[capacity]
position_y[capacity]
velocity_x[capacity]
velocity_y[capacity]
age[capacity]
lifetime[capacity]
start_size[capacity]
end_size[capacity]
rotation[capacity]
angular_velocity[capacity]
start_color[capacity]
end_color[capacity]
```

The implementation should use `array.array`, compatible pyglet buffers, or
another standard-library representation selected by profiling. Storage is
allocated when capacity changes and reused thereafter.

Dead particles are removed with swap-remove:

```text
copy last live slot over dead slot
decrement alive_count
continue checking the replacement slot
```

Particle order is not part of the public contract. This permits constant-time
removal. Effects requiring stable back-to-front ordering are deferred.

### Backend interface

The component depends on a private protocol rather than the concrete Python
implementation:

```python
class ParticleSimulation(Protocol):
    @property
    def count(self) -> int: ...

    def configure(
        self,
        definition: ParticleEffectDefinition,
        seed: int,
    ) -> None: ...

    def update(
        self,
        delta_time: float,
        emitter_transform: ParticleTransform,
    ) -> ParticleUpdateResult: ...

    def emit(
        self,
        count: int,
        emitter_transform: ParticleTransform,
    ) -> int: ...

    def clear(self) -> None: ...
```

This protocol is private in the first version. It prevents backend details from
becoming part of g's stable public API.

### Determinism

Each effect owns a dedicated `random.Random` instance or equivalent backend RNG.
It never uses the module-global random generator.

- An explicit seed produces repeatable emission for the same definition and
  update sequence.
- If `seed=None`, the component creates a runtime seed once when playback
  begins.
- Restarting with `restart=True` resets the RNG to the playback seed.
- Editor preview displays and can lock its seed.

Frame-rate-independent emission must be deterministic for an identical sequence
of simulation steps. Exact equality across different step sizes is not required
for the first version.

## Rendering

### ParticleDrawable

`ParticleDrawable` is an internal batch-backed drawable that owns:

- a pyglet vertex list sized for effect capacity or current high-water mark;
- one shader program;
- texture coordinates;
- particle position, size, rotation, and color attributes;
- a texture/blend render group;
- explicit GPU-resource cleanup.

It emits one indexed quad per live particle and submits all particles in one
batch. It must not issue one draw call per particle.

`ParticleEffect` integrates with the existing `Renderer` lifecycle:

- `bind_batch()` attaches the particle vertex list to the object layer batch
  and group;
- `sync()` updates visibility, owner transform, and dirty GPU data;
- `effective_visible` controls drawing;
- `screen_space` allows the same component to be used in UI rendering;
- layer and hierarchy ordering remain controlled by the existing scene.

The implementation may require extending the internal `Renderer` drawable type
or adding a protected hook for specialized batch-backed renderers. Existing
renderers must retain their current behavior.

### Upload policy

Simulation marks ranges dirty. Rendering uploads particle attributes at most
once per rendered frame, even if fixed-step simulation updates more than once.

The first implementation may rebuild all live-particle vertices each rendered
frame. Partial uploads should only be added if profiling shows a material
benefit.

### OpenGL state

Blend and texture state belong in pyglet groups. The particle renderer must not
leave modified blend, texture, shader, depth, or point-size state for later
drawables.

No legacy fixed-function OpenGL calls are permitted.

## Runtime lifecycle

### Attachment

When attached, `ParticleEffect`:

1. creates its private simulation backend;
2. validates and applies the definition;
3. creates the drawable lazily when a rendering context is available;
4. starts playback in `on_ready()` when `autoplay=True`.

### Update

`update(delta_time)`:

1. validates that `delta_time` is finite and non-negative;
2. exits when disabled, paused, stopped with no live particles, or detached;
3. captures the required owner transform;
4. advances duration, looping, bursts, and continuous emission;
5. updates live particles;
6. marks rendering data dirty;
7. stops when a finite non-looping effect has emitted everything and all
   particles have expired.

The simulation uses the engine's simulation delta, not wall-clock time.

### Definition changes

Changing definition properties in Edit mode recreates or reconfigures the
preview simulation.

At runtime:

- appearance-only changes preserve live particles where possible;
- increasing capacity preserves live particles;
- reducing capacity keeps the first `new_capacity` live slots and reports the
  number removed;
- emission, motion, shape, seed, or simulation-space changes apply to future
  particles;
- an explicit `restart=True` is required to regenerate existing particles.

The initial implementation may conservatively restart after any definition
replacement, provided this behavior is documented and tested. Fine-grained live
reconfiguration is not required for the first release.

### Detachment and scene changes

Detachment:

- removes the vertex list from its batch;
- clears the simulation;
- drops texture references;
- deletes GPU resources exactly once.

Scene reload, object deletion, undo, and preview shutdown must exercise the same
cleanup path.

## Persistence and assets

### Effect asset

Reusable effects use a project asset with the suffix:

```text
*.particle.json
```

Proposed format:

```json
{
  "format": "g-particle-effect",
  "version": 1,
  "capacity": 1000,
  "duration": 2.0,
  "loop": true,
  "warmup": 0.0,
  "emitter": {
    "rate": 100.0,
    "bursts": [],
    "lifetime": [0.5, 1.2],
    "speed": [50.0, 120.0],
    "direction": 90.0,
    "spread": 30.0,
    "shape": {
      "type": "point"
    }
  },
  "motion": {
    "gravity": [0.0, -80.0],
    "linear_drag": 0.0
  },
  "appearance": {
    "image": "particles/spark.png",
    "blend_mode": "additive",
    "size": [
      [0.0, 1.0],
      [1.0, 0.0]
    ],
    "color": [
      [0.0, [255, 255, 255, 255]],
      [1.0, [255, 64, 0, 0]]
    ],
    "initial_rotation": [0.0, 360.0],
    "angular_velocity": [-90.0, 90.0]
  }
}
```

All referenced files are relative to the project asset root. Loading rejects
paths that escape that root and reports missing or invalid images explicitly.

Version 1 stores a non-empty array of uniquely named emitters. All emitters
belong to the same effect asset and share effect-level playback controls.

### Scene component

The scene stores the effect asset reference and instance-specific behavior:

```json
{
  "type": "g.particle_effect",
  "properties": {
    "effect": "effects/fire.particle.json",
    "autoplay": true,
    "simulation_space": "world",
    "seed": null,
    "visible": true,
    "screen_space": false,
    "enabled": true
  }
}
```

Normal scene saves never include live particles, playback time, or RNG state.

The existing asset-reference system must recognize image references inside
particle assets and particle-asset references inside scenes. Rename, move,
delete diagnostics, project export, and dependency collection must cover both
levels.

### Runtime checkpoints

Exact particle checkpoint restoration can require a large amount of data. It is
therefore a separate implementation step.

The initial release should either:

- serialize playback time, RNG state, emission accumulator, and packed live
  particle arrays for exact restoration; or
- explicitly mark `ParticleEffect` checkpoint state as unsupported and fail
  through the existing checkpoint error path.

It must not silently restore a different visual state.

## Editor UX

### Panel organization

Particles is a dedicated dockable panel named **Particles**. Its default
location is the existing bottom tab group alongside:

- Tile Palette;
- Animation;
- Console.

It is not a single automatically changing "content" panel. Each tool retains
its identity and can be undocked or displayed beside another tool.

The Particles panel opens or receives focus when:

- the user clicks **Edit Effect** in the Inspector;
- the user double-clicks a `.particle.json` asset;
- the user chooses **Edit** from that asset's context menu;
- a new particle asset is created.

Merely selecting another object updates the available target but does not steal
focus from the currently active bottom tab.

### Inspector

The `ParticleEffect` inspector shows:

- effect asset reference;
- Edit Effect button;
- autoplay;
- simulation space;
- optional deterministic seed;
- enabled, visible, and screen-space inherited properties;
- read-only runtime state in Play mode: status, live count, capacity, and
  dropped count;
- playback controls in Play mode where consistent with other components.

### Particle editor layout

```text
+---------------------------------------------------------------+
| Effect: fire.particle.json       [Pin] [Save] [Save As]       |
+-------------------------------+-------------------------------+
| Emission                      | Preview                       |
|   Rate                        |                               |
|   Duration / Loop / Warmup    |      isolated particles       |
|   Bursts                      |                               |
| Spawn                         |                               |
|   Shape / dimensions          +-------------------------------+
|   Direction / spread          | [Play] [Pause] [Stop] [Step] |
|   Lifetime / speed            | Seed  Alive  Dropped  FPS     |
| Motion                        | Preview loop / background      |
| Appearance                    | Grid / zoom / bounds           |
|   Texture / blend             |                               |
|   Size curve / color gradient |                               |
+-------------------------------+-------------------------------+
```

The panel supports:

- play, pause, stop, restart, and one fixed-step advance;
- optional preview looping even for one-shot effects;
- fixed-seed preview;
- transparent, light, dark, and checkerboard backgrounds;
- origin, grid, spawn-shape, and bounds overlays;
- zoom and pan;
- drag/drop and normal asset picking for the particle texture;
- undo/redo for every persisted edit;
- unsaved-change handling consistent with scenes and other assets;
- pinning an asset while selecting or positioning another object;
- explicit error display when the asset or image cannot be loaded.

Arbitrary scrubbing is deferred because it requires replaying from the start or
storing snapshots. Restart and fixed-step controls provide predictable initial
authoring behavior.

### Scene preview

The panel preview is always available and isolated. A separate **Preview in
Scene** toggle may temporarily render the edited definition on the selected
object in Edit mode.

Scene preview:

- never starts scripts, physics, or gameplay;
- never changes persisted component values unless the user applies them;
- uses a separate preview simulation;
- restores the normal edit viewport state when disabled or when selection
  changes;
- is unavailable when no compatible object is selected.

## Performance requirements

Correctness comes first, but the first release is not complete without a
repeatable stress benchmark.

Initial target on a supported development machine:

- 5,000 simultaneously visible particles;
- 10 active effects;
- 60 rendered frames per second in a representative scene;
- total particle simulation below 2 ms per 60 Hz simulation step;
- render-data preparation below 2 ms per rendered frame;
- one draw submission per effect in version 1;
- no per-frame particle-object allocation after warmup;
- no unbounded growth in CPU or GPU buffers.

These are acceptance targets, not claims about all hardware. The benchmark must
report simulation and render preparation separately so a renderer bottleneck is
not mistaken for a need for native simulation.

Native code should only be considered after profiling demonstrates that the
packed Python simulation is the limiting stage. A future native backend must
pass the same behavior tests as the Python backend.

## Error handling

The system reports errors explicitly for:

- malformed particle files;
- unsupported format versions;
- invalid numeric values or ranges;
- missing or invalid texture assets;
- failure to allocate simulation or GPU buffers;
- unsupported checkpoint state;
- rendering without a valid graphics context;
- backend configuration failures.

Loading must not silently replace invalid data with defaults. The editor may
retain the last valid preview while displaying an error, but saving remains
disabled until the definition is valid.

## Testing strategy

### Definition tests

- defaults and explicit values;
- validation of every range and finite-number constraint;
- immutable definition behavior;
- JSON round trip;
- unsupported version and unknown enum rejection;
- project-relative asset validation.

### Simulation tests

- continuous emission accumulation;
- burst timing, repeats, duration, and looping;
- finite non-looping completion;
- stop, clear, pause, resume, and restart;
- capacity overflow and dropped count;
- lifetime expiration and swap removal;
- gravity, drag, rotation, and curves;
- every spawn shape;
- local and world simulation spaces;
- deterministic results for a fixed seed;
- invalid delta times;
- definition replacement and capacity changes.

Tests use a fake or recording backend where component lifecycle is the subject,
and the real Python backend where particle behavior is the subject.

### Rendering tests

- one batched drawable rather than per-particle sprites;
- correct generated vertices, colors, texture coordinates, and indices;
- owner transforms in local and world space;
- layer ordering and visibility;
- alpha and additive blend groups;
- dirty upload coalescing across multiple simulation steps;
- context-safe resource creation and cleanup;
- no leaked OpenGL state;
- empty and full-capacity rendering.

### Persistence and export tests

- scene round trip;
- particle asset round trip;
- nested image-reference discovery;
- asset rename/move updates;
- missing reference diagnostics;
- export includes particle and image assets;
- load and export reject paths outside the project root.

### Editor tests

- panel appears in the default bottom dock group;
- Inspector shortcut focuses the panel;
- preview controls and fixed-step behavior;
- selection changes do not unexpectedly switch tabs;
- pinning preserves the edited target;
- undo/redo and dirty state;
- drag/drop image assignment;
- Edit-mode scene preview isolation;
- Play-mode runtime statistics;
- invalid definitions remain visible and cannot be saved.

### Performance tests

Add a benchmark scene and non-flaky structural tests. Timing thresholds should
live in a dedicated benchmark rather than ordinary unit tests. Unit tests
should assert preallocation, bounded capacity, batching, and absence of
per-particle sprite creation.

## Delivery plan

### Phase 0: executable performance spike

1. Build a packed Python particle buffer with point emission, lifetime,
   velocity, gravity, and swap removal.
2. Build a pyglet 2 batched quad renderer using one texture.
3. Exercise 1,000, 5,000, and 10,000 particles.
4. Measure simulation, vertex preparation, upload, and draw cost separately.
5. Record the results and adjust the data layout before stabilizing APIs.

Exit criteria:

- no particle objects or sprites are allocated per particle;
- 5,000-particle target is measured;
- no need for a native backend is assumed without profiling evidence.

### Phase 1: definitions and simulation core

1. Add ranges, curves, gradients, bursts, spawn shapes, and typed definitions.
2. Add strict validation and JSON conversion.
3. Implement the backend protocol and `PythonParticleSimulation`.
4. Implement deterministic RNG, capacity handling, and playback state.
5. Add focused simulation and definition tests.

Exit criteria:

- all first-version simulation features work without a graphics context;
- deterministic tests pass;
- malformed data fails explicitly.

### Phase 2: ParticleEffect renderer-component

1. Add `ParticleEffect` and export it from `g.components` and `g`.
2. Integrate component attachment, readiness, updates, visibility, and cleanup.
3. Add the particle drawable, shader, texture/blend groups, and batch binding.
4. Implement local/world space and screen-space behavior.
5. Add lifecycle and rendering tests.

Exit criteria:

- effects participate correctly in scenes and layer ordering;
- component removal and scene reload release resources;
- the performance benchmark still meets its target.

### Phase 3: persistence, resources, and export

1. Add versioned `.particle.json` loading and saving.
2. Register `g.particle_effect` scene persistence.
3. Add particle assets and nested image references to asset-reference tracking.
4. Wire rename, move, delete diagnostics, project loading, and export.
5. Decide and implement explicit checkpoint behavior.

Exit criteria:

- scenes and projects round-trip;
- all referenced assets export;
- broken references are visible errors.

### Phase 4: particle editor

1. Add the Particles panel and default docking tab.
2. Add preview rendering and playback controls.
3. Add emission, shape, motion, appearance, curve, and gradient editors.
4. Add Inspector and Assets entry points.
5. Add pinning, undo/redo, dirty state, save handling, and diagnostics.
6. Add optional isolated scene preview.

Exit criteria:

- a user can create, preview, save, assign, and edit a complete effect without
  writing code;
- ordinary selection does not cause disruptive panel switching;
- preview never mutates gameplay or saved runtime state.

### Phase 5: examples and hardening

1. Add representative fire, smoke, explosion, rain, and aura assets.
2. Add one concise runtime API example.
3. Run targeted tests, full tests, type checking, and export validation.
4. Profile benchmark effects and fix measured bottlenecks.
5. Document limits and unsupported features.

Exit criteria:

- examples cover continuous, burst, world-space, and local-space effects;
- public API and asset schema are documented;
- performance and cleanup are verified.

### Future phases

Only after the initial system is complete and measured:

- multiple emitters per effect;
- animated particle textures;
- collision modules;
- per-particle trails and ribbons (persistent object trails are available
  separately through `TrailRenderer`);
- sub-emitters;
- sorting modes;
- shader-defined modules;
- native simulation backend;
- GPU simulation.

## Decisions

The initial implementation makes these deliberate decisions:

1. The public component is named `ParticleEffect`.
2. `ParticleEffect` is a `Renderer` so layer and visibility behavior remain
   consistent with existing visual components.
3. g owns the API, definitions, file format, and editor model.
4. The first simulation backend is packed, preallocated, and pure Python.
5. Rendering is batched through modern pyglet 2 APIs.
6. Version 1 supports multiple uniquely named emitters per effect.
7. Particle assets use `.particle.json`.
8. The editor uses a dedicated dockable Particles panel in the shared bottom
   tab group.
9. Runtime and editor preview simulations are isolated.
10. Native code is introduced only if profiling proves it necessary.