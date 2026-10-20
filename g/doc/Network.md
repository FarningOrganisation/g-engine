# Simple Networking for g

## Status and recommendation

The first server-authoritative vertical slice is implemented:

- `NetworkedGameObject`, `NetworkClient`, `game.network`, and `network_field`;
- project/editor configuration and a bounded WebSocket transport;
- scene-authored object identity, ownership, automatic transform/physics state,
  custom runtime fields, owner input, disconnect cleanup, and remote smoothing;
- separate client and server export targets;
- an eight-player racing showcase with a mode menu, seven-bot single-player
  races, best-lap ghost time trials, a timed multiplayer lobby, prefab grid
  spawning, authoritative laps, local prediction, and dead-reckoned remotes;
- a null-presentation server path that loads the same scenes/components while
  preserving tile collision/navigation and suppressing windows, image/audio
  decoding, batches, sprites, particles, trails, shapes, text, and shaders.

The sections below describe the full target architecture. Dynamic prefab
spawning, general command/event schemas, shared scene switching, account or
admission systems, reconnect, replay-based vehicle reconciliation, datagram
snapshots and the editor's multi-process Play button remain later milestones.
Linux server exports select pyglet's EGL headless platform for import
compatibility, but construct no presentation backend and require neither X11
nor `DISPLAY`. Removing the remaining pyglet/EGL import dependency entirely is
a packaging optimization rather than a simulation-authoring requirement.

Build a **server-authoritative, component-based networking system** with:

- one project-level session configuration;
- a `NetworkedGameObject` component marking an ordinary object for replication;
- a `NetworkClient` component for connection setup and local-client information;
- automatic spawning, state synchronization, ownership assignment, and cleanup;
- explicit, small APIs for gameplay input and one-shot events;
- an editor action that starts one server and several clients;
- automatic export of separate client and headless server applications when
  networking is enabled, and only the game when it is disabled.

The beginner-facing promise is:

> Mark the objects, choose what to synchronize, select a player prefab, and
> press Multiplayer Play. The engine handles the connection and replication.

It is not:

> Any existing single-player script automatically becomes safe multiplayer code.

The engine can automate transport and replication. The developer still decides
which gameplay is authoritative, what input means, and what is local presentation.

## 1. Scope

### First version

- Small cooperative or casual games: initially validate with 2-8 clients.
- One dedicated server process per session; no peer-to-peer mesh.
- One shared gameplay scene at a time, with local menus and overlays.
- Scene-placed replicated objects and runtime-spawned registered prefabs.
- Server-owned simulation and client input requests.
- automatic network-safe object state, lifecycle, and ownership.
- Late join, clean disconnect, explicit connection failure states.
- Local multiplayer testing and separate distributable client/server builds.
- Windows, macOS, and Linux desktop targets, tested individually.

### Not in the first version

Matchmaking, accounts, relay hosting, NAT traversal, public server discovery,
host migration, seamless reconnect, persistent worlds, deterministic lockstep,
rollback combat, general client prediction, or browser exports.

Automatic server export is not automatic server hosting. Someone must run the
server and provide a reachable address. Internet use also requires deployment,
firewall configuration, TLS, and an admission policy.

Fast competitive racing and shooters need a later latency-focused milestone.
Do not advertise the first version as suitable for every engine example.

## 2. What "automatic" means

| Engine handles | Game developer provides |
|---|---|
| Connection, handshake, heartbeat, timeout | Server endpoint or join UI |
| Client IDs and object IDs | Admission and gameplay rules |
| Replicated spawn/despawn and late-join baseline | Allowed spawn prefabs |
| Sending network-safe object changes | Custom runtime state the engine cannot infer |
| Routing owner input to the server | Input meaning and validation |
| Snapshot buffering and remote visual interpolation | Local camera, UI, audio, effects |
| Role filtering and built-in component adapters | Role declaration for custom scripts |
| Client/server packaging | Deployment and server credentials |

Non-networked objects remain local. A local client object must never
accidentally become authoritative because its Python properties changed.

## 3. Public model

### `game.network`: the session service

Every `Game` exposes a session service. Disabled projects use an inactive
service with no sockets, worker threads, or replication work; attempts to
connect or spawn through it raise a clear configuration error.

Project startup selects `offline`, `client`, or `server` before scene
construction and lifecycle callbacks. There is only one session per `Game`.
Do not use global connection state shared by games or editor previews.

Core properties:

| Property | Meaning |
|---|---|
| `role` | `offline`, `client`, or `server` |
| `state` | Current connection/listener state |
| `is_server` | This process owns authoritative simulation |
| `is_client` | This process presents a remote session |
| `local_client` | Local `ClientInfo`, or `None` before admission/on server |
| `clients` | Read-only admitted-client collection; server only |

`ClientInfo` contains `id`, `number`, and connection diagnostics.
`id` is an opaque session-scoped identity, not an account identity.
`number` is a server-assigned, one-based display number; it is not a list index,
credential, or ownership proof. Neither is reused during a session. A new
connection gets a new identity in v1.

Main operations:

```python
game.network.connect("ws://127.0.0.1:7777")
game.network.disconnect()

# Server-only operations:
player = game.network.spawn("player", owner=client.id, position=(100, 80))
game.network.despawn(player)
game.network.change_scene("arena")
```

`connect()` starts a nonblocking operation. Its return does not mean connected.
Callers observe state changes and structured failure events. Calling spawn or
scene-control operations on a client raises `NetworkAuthorityError`.

Normal editor-created projects need none of these calls for their standard
startup flow; the generated launchers and connection component drive it.

### `NetworkedGameObject`: replication component

Despite its name, this is a `Component`, not a subclass of `GameObject`.
It can be attached to existing objects and custom object subclasses without
changing inheritance.

```python
player.add_component(NetworkedGameObject())
```

Suggested inspector:

| Setting | Default | Purpose |
|---|---|---|
| Synchronization | Automatic | Use engine adapters plus declared custom state |
| Sync Scale | Off | Include scale in runtime state when needed |
| Remote Smoothing | Interpolate | Smooth received transforms visually |
| Owner Disconnect | Despawn | Despawn or keep as server-owned |

Automatic synchronization is the normal path. Adding this component means the
complete **network-safe** state of the object is replicated: identity,
ownership, lifecycle, transform, appropriate enabled/visible state, built-in
component state through engine adapters, authored spawn configuration, and
custom runtime fields declared by scripts. The user does not maintain a list
of transform or built-in physics fields.

This is semantic whole-object synchronization, not serialization of every
Python attribute. Private caches, callbacks, engine references, images,
renderer internals, particle simulations, local cameras, and client-only UI
must not silently become protocol state. Engine adapters classify built-in
components as authoritative simulation, replicated state, spawn configuration,
or local presentation. The resolved network schema is generated at export and
shown by the editor for diagnostics.

Advanced synchronization modes may include **Transform Only** and **Custom**.
Custom mode allows explicit adapter/field control for unusual objects, but is
not required for ordinary networked gameplay.

Identity and ownership are read-only runtime information:

- `network_id`: server-assigned live-object identity, unavailable before spawn;
- `owner_id`: controlling client's ID, or `None` for server-owned;
- `is_local_owner`: true only on the owning client replica.

Ownership grants permission to send declared input, not permission to author
position, health, inventory, or arbitrary fields. All state is server-written
in v1. Ownership transfer is a server-only operation and clears pending old-owner
input before the new owner can control the object.

Allow at most one marker per object. Disabling/removing it while spawned must
be rejected; use the lifecycle API instead. Rendering visibility and gameplay
enabled state are distinct from network existence.

### `NetworkClient`: connection component

Attach this to a local session/controller object, not to every player.
It delegates to `game.network`; it does not own another independent connection.

Inspector fields:

- Endpoint, defaulting to the project's local development address.
- Auto Connect, on by default in client launches.
- Connection Timeout, initially 10 seconds.

Helpers: `state`, `is_connected`, `client_id`, and `client_number`.
The last two return `None` until admitted. Never return a fake player number.
Expose `on_connected`, `on_disconnected`, and `on_connection_failed` through
the same event mechanism as the service, with machine-readable reasons.

The component is client-only and must not be on a replicated object.
An active client launch may have at most one auto-connecting component.
The service survives shared gameplay-scene changes; removing a scene-local
component unsubscribes its callbacks but does not implicitly disconnect.
An explicit Disconnect action or process shutdown closes the session.

No required `NetworkServer` component: the dedicated server launcher starts the
service from project configuration. A server component would duplicate startup
configuration and complicate loading the first scene.

## 4. Authoring workflow

1. Enable Networking in Project Settings.
2. Choose the shared startup scene and a Player Prefab.
3. Add `NetworkedGameObject` to the prefab root.
4. Choose **Network Player Motor** or write a network-aware controller.
5. Mark shared doors, pickups, enemies, or moving platforms for replication.
6. Add a `NetworkClient` to the client connection/menu scene if using a menu;
   otherwise use the generated direct-connect startup.
7. Press **Multiplayer Play: 2 Clients**.

For each admitted, scene-ready client, the server automatically spawns one
instance of the player prefab and assigns ownership. With no player prefab,
clients join as spectators; no player is invented.

The default spawn is the prefab's authored position. The editor warns that
multiple players may overlap and offers a spawn-point list. A server callback
may choose a spawn point or override automatic player spawning.

Connection readiness and scene readiness are separate: player spawning occurs
after the client's scene baseline has been acknowledged. A client that never
becomes ready times out without leaving an orphan player.

## 5. Gameplay API: state, input, and events

### Replicated state

Replication separates a complete spawn baseline from frequent runtime updates.
The spawn baseline contains the prefab and component identities, authored
inspector configuration, transform, ownership, initial built-in state, and
custom gameplay state. Runtime snapshots contain only changed dynamic state.
Static values such as collider dimensions or maximum speed are therefore
available on every client without being resent at the snapshot rate.

Built-in adapters automatically handle transforms and supported physics,
animation, and component state. Custom scripts declare only additional runtime
gameplay state whose meaning the engine cannot infer:

```python
from g import Script, inspector_field


class PlayerStats(Script):
    network_role = "server"

    health: int = inspector_field(
        100, minimum=0, maximum=100, networked=True,
    )
    score: int = inspector_field(0, minimum=0, networked=True)
```

Extend the existing inspector-field metadata rather than introduce a separate
persistence/editor schema. `network_field(...)` may exist as a concise alias
for `inspector_field(..., networked=True)` and may support `inspector=False`
for state that should not be author-editable. Authored defaults are persisted;
live replication state is not written into ordinary scene saves.

Ordinary inspector fields are automatically included as authored spawn
configuration beneath a marked object, but changes to them are not continuously
replicated unless an engine adapter or `networked=True` declares them dynamic.
This makes a newly spawned car arrive with its authored acceleration, collider,
and other configuration while reserving snapshot bandwidth for position,
velocity, lap, health, and other changing state.

The editor does not require routine field selection. Its advanced network
section shows the resolved schema grouped as runtime state, spawn
configuration, server-only internals, or client-local presentation. Export
validation rejects an unsupported or ambiguous value instead of silently
omitting it. Explicit inclusion/exclusion remains available only in Custom
synchronization mode. The resolved schema becomes part of the build manifest.

Support bounded booleans, integers, finite floats, strings, `Vec2`, enums,
and nullable network-object references initially. Collections require explicit
bounded schemas later. Reject unsupported types during validation.

Assignment on the server marks a field dirty when its value changes.
Assignment to replicated state on a client raises an authority error.
Initialization and received-state application use a separate internal path.
Mutable values such as `Vec2` must be copied/canonically compared at snapshot
capture so in-place edits cannot bypass change detection.

Change hooks run after a complete state batch is applied, never half-way
through reference resolution. They can update UI and visuals even if the
component's gameplay update is server-only.

### Input, not client-authored transforms

The easiest supported controller is a built-in **Network Player Motor**:

- local owner reads configured input axes;
- engine sends the latest bounded input state;
- server validates and applies it through a supported motor adapter;
- server simulation produces replicated movement.

Do not automatically wrap every existing movement script. Initially support
one top-down motor adapter, then add a platformer adapter with its own tests.
Client cameras and UI remain local and follow `is_local_owner`.

For custom gameplay, declare an input schema in source. Proposed usage:

```python
# On the local owner, in a client-role input script:
game.network.set_input(player, move=Vec2(horizontal, vertical))

# On the server, in an authoritative update:
controls = game.network.input_for(player)
player.speed = controls.move * movement_speed
```

`set_input` only accepts declared fields; calls from non-owners fail.
The server derives sender identity from the connection, validates finite values
and ranges, and clamps permitted movement axes. It never trusts an owner ID
supplied by a packet. Speed and cooldown rules remain server-owned.

Continuous input uses latest-value semantics and a sequence number. Stale or
duplicate sequences are ignored. If no fresh input arrives for 250 ms, reset
movement to neutral. Clear it immediately on disconnect, scene change, or
ownership transfer.

One-shot actions such as jump, interact, or fire use declared commands, not a
boolean that could disappear between snapshots:

```python
game.network.command(player, "interact")
```

Commands have an allowlisted typed schema, owner-only permission by default,
request IDs, rate limits, and authoritative handlers. Handlers enforce gameplay
preconditions. Arbitrary method invocation or name-based Python dispatch from
network packets is forbidden. Do not offer a generic unrestricted RPC decorator.

### Presentation events

Server gameplay can emit declared events such as `hit_effect` or `play_emote`.
Events have a bounded payload and target: everyone, owner, or selected clients.
They are not persistent state, are not replayed to late joiners, and cannot be
used as the only representation of health or whether a door is open.

v1 delivers events reliably within a live connection, with deduplication IDs.
There is no exactly-once promise across disconnects or process crashes.
Cosmetic events can be dropped under a documented overload policy; commands
must receive an explicit rejection rather than silently disappear.

## 6. Simulation and execution roles

Add an explicit role declaration for custom scripts: `server`, `client`,
`owner`, or `both`. `owner` runs only on the local owner client.
Unknown/unclassified gameplay scripts on replicated objects are an export
validation error until a role is selected. Existing offline projects are
unchanged.

Role filtering happens before readiness callbacks, timers, collision hooks,
and gameplay updates, not just before `update()`. Authoritative scripts still
have passive replicated-state containers on clients so references and field
change notifications work.

| Component/work | Server | Client |
|---|---|---|
| Physics and gameplay collisions | Authoritative | No authoritative simulation |
| Server-role scripts and gameplay timers | Run | State container only |
| Owner-role input scripts | Do not run | Local owner only |
| Renderers, camera, sound, UI | Do not instantiate GPU/audio work | Run |
| Network replica transforms/fields | Produce | Receive |
| Local-only scene decoration | Omit when presentation-only | Run |

Non-networked static collision geometry still exists on the server. Do not
equate "not replicated" with "client-only." Role-aware scene validation must
catch server scripts referencing stripped presentation components.

Proposed fixed-step order:

1. Drain bounded inbound messages on the game thread.
2. Validate commands, update input state, apply queued lifecycle operations.
3. Run authoritative scripts, timers, physics, and collision callbacks.
4. Capture a consistent post-simulation state.
5. Publish due snapshots and queued events without blocking the tick.

Networking I/O may use a dedicated event-loop thread, but scene/component
mutation and user callbacks always run on the game thread. Queues and per-tick
work are bounded.

Use a 60 Hz simulation and 20 Hz snapshots initially. Rates are project
settings; they need not match render rate. An overloaded server caps catch-up,
logs tick overruns, and never accepts unlimited queued work.

## 7. Identity, spawning, hierarchy, and scenes

Keep separate identities for:

- authored scene object IDs, already used by scene persistence;
- prefab asset IDs and stable prefab-relative component/object keys;
- server-generated runtime network IDs.

Use a session epoch, scene generation, and monotonic object counter for live
identity. Never send Python object IDs or use runtime component list indexes
as stable wire identifiers.

For scene objects, clients load the same scene manifest and bind the
server's network IDs to authored IDs. They must not spawn duplicate copies.
For dynamic objects, the server sends an allowlisted prefab ID, runtime ID,
owner, parent/layer identity, and initial state.

Spawn reception is transactional: instantiate, bind IDs, resolve references,
apply the baseline, then invoke `on_network_spawn`. Ordinary attachment can
happen during construction, but network-dependent code belongs in this new
hook, not `on_attach` or an early `on_ready`.
`on_network_despawn` runs before removal. Partial spawn failures disconnect
with a clear content/schema error rather than leave half-created objects.

v1 replicated prefabs have one marked root. Unmarked children are part of its
local prefab hierarchy; their transforms are not individually replicated.
Reject nested marked roots and runtime network reparenting initially.
Use world-space root transforms and require a stable, nonmoving authored
parent/layer. Independently moving attachments need a later explicit model.

Only registered prefab IDs may spawn. Server calls to ordinary
`Game.add()` with marked objects require a registered template and funnel
through the spawn lifecycle; otherwise raise a useful error.
Deletion of a server-side marked object funnels through despawn. Client
deletion of a replica is rejected. Ordinary local objects keep existing APIs.

Late join uses a versioned baseline followed by queued changes. Choose a
snapshot tick, capture live objects and fields consistently, then deliver
subsequent changes in order. Buffer under strict limits; restart or fail the
join if those limits are exceeded. A despawn during loading cannot resurrect
an object from an older baseline.

`change_scene()` is server-controlled. It increments scene generation,
stops old gameplay replication, clears old input/events, and sends the new
scene identifier. Clients load and acknowledge a bounded baseline. Other
ready clients can continue while a slow client waits; the slow client receives
a fresh baseline and is disconnected if it cannot catch up. No packet from an
old generation may mutate the new scene.

There are no cross-scene persistent network objects in v1; recreate players
from server-owned session data. Client menu changes do not switch the shared
authoritative scene.

## 8. Replication and transport

### Transport decision

Use a maintained WebSocket implementation for v1, with TLS (`wss://`) for
non-loopback deployment. Select and pin the library during implementation;
do not implement custom TCP framing, cryptography, or a reliable UDP stack.
Keep this an optional networking dependency, absent from offline installs.

WebSockets give a practical first implementation with reliable ordered
delivery and familiar deployment. Their TCP head-of-line blocking means packet
loss can delay newer movement behind older data. This is an explicit tradeoff,
not a competitive-action networking solution.

Keep a small internal transport boundary so a later established datagram
transport can carry snapshots without changing gameplay components. Do not
build a public transport-plugin framework in v1.

### Protocol

- Versioned, bounded, typed messages with an explicit message kind.
- Handshake checks protocol, game/build compatibility, scene/prefab manifests,
  selected field schemas, and registered command schemas.
- Begin with bounded JSON envelopes and explicit numeric/enum validation;
  optimize encoding only after measuring. Never pickle, evaluate source,
  import packet-selected modules, or fetch executable assets from peers.
- Lifecycle and baselines are reliable and ordered.
- Each state update carries scene generation and authoritative tick.
- Snapshots contain changed fields; new replicas receive complete state.
- Coalesce pending unsent state per object/field. Once bytes enter TCP they
  cannot be withdrawn; bounded send buffers are therefore essential.
- Disconnect a persistently slow peer with an explicit reason rather than
  grow memory indefinitely. One slow client must not stall the server.
- Use baseline acknowledgements before assuming a client has the referenced
  objects. References unresolved within a baseline are deferred until its
  completion; missing required references then fail explicitly.

Initial guardrails: 8 clients, 1,000 live replicated objects, 64 KiB messages,
64 MiB total baseline, and 2 MiB queued data per peer. Chunk baselines below
the message limit and enforce an aggregate limit before allocation.
These are starting defaults to benchmark, not performance claims.
Set command/input rate limits from their schemas and tick rate; cap strings,
field counts, nesting depth, and decoded collection sizes.

### Smoothness

Clients render remote objects roughly two snapshot intervals behind server
time and interpolate between received poses. Start with a 100 ms buffer at
20 Hz; estimate clock offset using heartbeat round trips, not wall-clock time.
Do not extrapolate indefinitely: hold the last pose after a short bounded
gap and expose degraded connection status.

Teleport messages reset history. Scale interpolation is opt-in. Physics,
serialization, and gameplay always see authoritative simulation transforms.

The engine already has fixed-step render interpolation. Network interpolation
must compose through one presentation-pose path rather than applying two
independent smoothing passes to the same object.

v1 owner movement also waits for server state. The resulting input latency is
real and must be shown in editor diagnostics. Prediction/reconciliation is a
later opt-in feature, initially limited to a tested built-in motor; general
Pymunk scenes must not be assumed deterministic or safely replayable.

## 9. Connection lifecycle and failures

Client states:

```text
disconnected -> connecting -> handshaking -> loading -> connected
                         \-> failed
connected -> disconnecting -> disconnected
connected -> disconnected (timeout, server shutdown, protocol failure)
```

The server separately reports `starting`, `listening`, `stopping`, and `failed`.
Bind failure must not look like a running server.

Handshake, scene loading, inactivity, and graceful shutdown each have bounded
deadlines. Heartbeats distinguish a quiet game from a dead peer.

On disconnect, stop local input, remove session replicas, emit the reason,
and return control to the connection UI. Do not silently continue gameplay as
if still connected. The server applies each object's disconnect policy once.
Default player cleanup is despawn; kept objects become server-owned.

v1 reconnect means a new admission and a new baseline, not session resumption.
Do not automatically retry indefinitely or retain previous authority.

Configuration/schema errors fail validation. Malformed packets, permission
violations, and overload produce structured diagnostics and bounded,
rate-limited logs. Sensitive tokens and raw private payloads are not logged.

## 10. Editor integration

### Project Settings > Networking

Show Enabled, client entry scene (optional), shared startup scene, player
prefab, registered spawn prefabs, endpoint, server bind address/port, maximum
clients, simulation rate, and snapshot rate. Keep advanced limits collapsed.

The session is project-level; a scene marker alone does not silently enable
network export. If Networking is disabled but exported content contains
network-only components, validation explains how to enable or remove them.

### Inspector and hierarchy

- Add Component > Network > Networked Game Object / Network Client.
- Default to Automatic synchronization with no per-field setup.
- Show the resolved network-safe schema in a collapsed advanced section,
  grouped as runtime state, spawn configuration, server-only internals, and
  client-local presentation.
- Custom runtime fields use existing inspector labels, grouping, and validation.
- Show field direction as "Server -> Clients"; no unsafe owner-write toggle.
- Show role badges and network-object badges in the hierarchy.
- During Play, show object ID, owner, authority, last state tick, and traffic.
- Prevent editing client replica state, but allow inspecting it.
- Show errors at the exact scene/object/component/field.
- Preserve existing undo/redo, prefab override, and scene dirty-state behavior.

### Multiplayer Play

Toolbar: **Play Offline**, **Multiplayer Play**, **Stop Multiplayer**.
Multiplayer Play starts a headless child server, waits for actual readiness,
then launches the chosen number of client processes. Distinct processes are
required; several windows sharing one `Game` cannot test isolation properly.

Use loopback and an available development port. Track only child processes
owned by this play session. Stop and editor shutdown close clients/server,
with a bounded graceful shutdown before terminating those specific children.
Failure to start one process is visible; do not leave invisible children.

Edit mode never opens sockets or starts servers. Plain Play Offline with
network content runs a private local server plus one client over loopback,
so authority behavior stays the same; it means "no external connection," not
"pretend authority is local." Disabled-network projects retain existing Play.

The editor viewport attaches to one chosen client. Pause authoritative
simulation on the server while continuing heartbeats; show all clients paused.
Editing is read-only during multiplayer play in v1; no network hot reload.

### Network panel

Show state, clients, ping, bandwidth, object counts, snapshot age, queue size,
tick overruns, and structured disconnect reasons. Add simulated latency,
jitter, and bandwidth limits in a development-only transport wrapper.
For WebSockets, use delayed/stalled delivery and connection resets; do not
claim application-level message drops faithfully simulate TCP packet loss.

## 11. Project data and exports

Proposed project fragment:

```json
{
  "network": {
    "enabled": true,
    "shared_scene": "scenes/arena.scene.json",
    "player_prefab": "player",
    "prefabs": {
      "player": "prefabs/player.prefab.json",
      "projectile": "prefabs/projectile.prefab.json"
    },
    "client": {
      "endpoint": "ws://127.0.0.1:7777"
    },
    "server": {
      "bind": "127.0.0.1",
      "port": 7777,
      "max_clients": 8
    },
    "simulation_hz": 60,
    "snapshot_hz": 20
  }
}
```

Paths are project-relative and validated. Keep existing export name/version,
startup-scene, and registered-script conventions. The existing export startup
scene is the client entry scene when provided; otherwise launch directly into
the shared scene after admission.

Missing `network` means disabled, preserving old projects. Introduce an
explicit project-format migration/version policy before writing this section:
old readers must reject unsupported enabled networking rather than silently
export an offline game.

### Automatic targets

| Project configuration | Default export result |
|---|---|
| Networking disabled | Existing game application only |
| Networking enabled | Game client and dedicated server |
| Invalid network configuration | Export fails with actionable diagnostics |

```text
dist/
  MyGame/           # client application
  MyGameServer/     # separate headless executable and server data
```

Keep the existing offline output layout. Network exports generate distinct
staging directories, bootstrap files, manifests, and PyInstaller specs.
Do not let one target overwrite the other. An enabled-network default export
is successful only when both artifacts exist and pass startup checks.

The client packages presentation assets and required shared content.
The server packages simulation scripts, collision/tile data, scenes, prefabs,
and required nonvisual resources, without the editor or unnecessary visual
assets. Arbitrary Python import dependencies cannot be safely inferred:
require server-safe imports and explicit asset declarations where necessary.
It is acceptable initially to include extra inert data for correctness;
opening graphics/audio devices on the server is not acceptable.

Proposed server CLI:

```bash
./MyGameServer --bind 0.0.0.0 --port 7777
./MyGame --server wss://game.example.com
```

Use environment variables or a deployment-local config for tokens and TLS
credentials. Never bake server secrets into client bundles or project assets.
Loopback defaults are safe for local development; externally bound servers
require TLS directly or behind a documented TLS-terminating proxy.

No automatic public listener, firewall changes, port forwarding, downloads,
or account creation. Server binaries are built per target platform; exporting
on macOS does not automatically create a Linux server executable.

## 12. Implementation fit and required foundations

Existing extension points to reuse:

- [Component lifecycle](../components/base.py) and [Script](../components/script.py)
  for ownership and role-aware callbacks.
- [Inspector metadata](../inspector.py) for authoring and validation.
- [Scene persistence/type registry](../persistence.py) for known types and IDs.
- [Game](../game.py), [scenes](../scene.py), and [fixed timing](../timing.py)
  for session ownership and simulation ordering.
- [Render interpolation](../rendering.py) for a shared presentation-pose path.
- [Project configuration](../project.py) and [export pipeline](../export.py)
  for opt-in configuration and target generation.

Two prerequisites are architectural work, not small networking wrappers:

1. **Headless runtime (implemented).** `Game`, scenes, resources, tile layers,
   GUI, and built-in renderers select a null-presentation backend for the
   server role. Visual components retain their concrete types and public APIs,
   while resources retain only paths and PNG dimensions. The racing project is
   verified in a minimal Linux container with no `DISPLAY`, including a real
   WebSocket handshake. The current package still imports pyglet and therefore
   needs an EGL runtime library on Linux; it does not create a window, renderer,
   decoded image/audio resource, batch, sprite, particle buffer, or shader.
2. **Runtime prefab support.** Prefab documents and instantiation currently
   live in [the editor](../../editor/prefab.py). Extract the reusable format,
   validation, and runtime instantiation into engine-owned code, preserving
   existing editor IDs/overrides. Server startup must not import ImGui/editor
   code to spawn a player.

Suggested internal ownership:

```text
Game.network
  -> session state machine
  -> transport worker and bounded queues
  -> validated protocol/schema manifest
  -> object registry and replication
  -> input/command routing
  -> snapshot presentation adapter
```

Components are thin adapters over this service. No singleton networking
manager, duplicate editor networking implementation, or second scene format.
Do not send existing scene/checkpoint deserialization records directly across
the trust boundary; network schemas are narrower and independently validated.

## 13. Security and operational boundaries

All remote input is untrusted, including traffic from the object's owner.
Authority checks are mandatory even for local games.

- Validate admission before spawning. Supply an admission callback for game
  policy; development anonymous access is loopback-only by default.
- TLS protects transport; it does not implement player accounts or prevent
  dishonest gameplay input.
- Bound connections, handshakes, messages, joins, queues, commands, and logs.
- Never accept arbitrary filesystem paths, scene assets, Python modules, or
  serialized executable objects from peers.
- Do not ship authority-critical secrets in shared scripts; role filtering
  is execution control, not a source-code confidentiality boundary.
- Provide graceful shutdown, startup readiness, nonzero failure exits, and
  useful server logs without requiring the editor.

## 14. Delivery plan and acceptance criteria

### Phase 1: runtime foundations

Headless simulation entry point, role-filtered loading, runtime prefabs, and
project configuration/migration. Offline behavior and exports remain unchanged.

Acceptance: a shared scene can load, simulate physics, spawn a prefab, and
exit without a display server, OpenGL context, audio device, or editor import.

### Phase 2: vertical slice

One server, two clients, handshake, client IDs, scene binding, player spawning,
top-down input, authoritative movement, transform replication, and disconnect.

Acceptance: each client controls only its own player; both see both players;
the server rejects forged ownership/input and removes disconnected players.

### Phase 3: useful replication

Custom fields, allowlisted commands/events, late join, bounded queues,
interpolation, scene switching, error UI, and manifest compatibility checks.

Acceptance: a joining client sees current door/health/player state, not initial
defaults; stale scene traffic cannot recreate deleted objects or change worlds.

### Phase 4: editor and shipping workflow

Inspector validation, multi-process Play, diagnostics, server CLI, separate
export targets, and packaged smoke tests. Complete this phase before calling
the beginner-facing feature ready.

Acceptance: a network project exports two working applications; an offline
project exports one. Exported clients connect to the exported server outside
the editor. Stopping Multiplayer Play leaves no owned child processes.

### Phase 5: measure before expanding

Benchmark the 8-client/1,000-object starting limits, join time, queue memory,
tick cost, bandwidth, and input latency. Publish measured supported envelopes.
Then decide whether datagram transport, interest management, prediction, or
additional motor adapters are necessary. Do not treat starting limits as
verified capacity.

### Test strategy

Follow the repository's targeted/core/full workflow:

- Feature tests for state transitions, schemas, IDs, ownership, field changes,
  bounds, command deduplication, and cleanup use an in-memory transport and
  fake clock. Do not use sleeps for timeout unit tests.
- A small networking smoke subset can join the core only after its runtime is
  measured. Never add multi-process exports or soak tests to the default core.
- Run relevant physics, persistence, scene, inspector, and export tests when
  those integrations change.
- Explicit integration runs cover real loopback sockets, 2+ client processes,
  late join during spawn/despawn, server loss, loading timeout, slow peers,
  scene changes, and platform-specific headless startup.
- Packaged artifact tests verify both binaries, role-safe content, protocol
  compatibility, clean shutdown, and the unchanged offline export.
- Longer load/latency/soak tests run explicitly, reporting durations and
  resource thresholds instead of imposing fragile wall-clock assertions on
  ordinary unit tests.

## 15. Decisions to keep visible

The recommended v1 deliberately chooses dedicated server authority, automatic
network-safe object synchronization, reliable WebSockets, no generic
prediction, and one shared scene. These keep the API small and behavior
explainable. Built-in adapters cover common components; custom scripts declare
only additional runtime gameplay state the engine cannot infer.

Before implementation, confirm whether the first showcase is a casual
top-down/cooperative game or latency-sensitive racing/shooting. If the latter,
promote established datagram transport and motor-specific prediction into the
first delivery scope rather than hide input latency behind "automatic sync."

The API should stay simple even as internals grow:

> An object is marked for networking and its network-safe state is synchronized
> automatically. The server owns game rules. Clients provide input. The editor
> configures the session. Export produces the applications needed to run it.