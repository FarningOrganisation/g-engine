# Racing showcase

The example starts in `menu.scene.json` and demonstrates three complete modes:

- **Race against bots** fills all eight prefab start slots with one player and
  seven spline-guided opponents with forward raycast avoidance and stuck
  recovery.
- **Time trial** records the fastest completed lap and replays it as a
  translucent ghost on later laps.
- **Race multiplayer** accepts a server IP or `ws://` endpoint, then joins the
  dedicated authoritative server.

Every mode uses the reusable `car.prefab.json`. Cars are instantiated at
`start1` through `start8` under the scene's `starts` object. The finish line is
an area trigger. A lap counts only after the car has travelled a full-lap
minimum distance, moved far away from the line, and crossed it in the forward
direction. The race ends after five valid laps.

Every mode uses a large animated `3`, `2`, `1`, `GO!` start countdown. The
bots follow the closed `Bot Racing Line` spline authored in
`racing.scene.json`. Select that object and use the editor's spline tool to
adjust the racing line visually; raycasts handle nearby walls, cars, and
temporary obstructions rather than replacing the authored route.

Open the menu directly with:

```bash
python3 examples/racing/run_network.py
```

From the repository root, start the authoritative server:

```bash
python3 examples/racing/run_network.py server
```

Then start up to eight clients in separate terminals:

```bash
python3 examples/racing/run_network.py client
```

Choose **Race multiplayer** in each client and enter the server address. The
first connection opens a 120-second lobby; additional players can join during
that window. When the lobby closes, new connections are refused and the
three-second race countdown begins. Use the arrow keys or WASD to steer and Space to brake. The
server owns physics, lap validation, finishing order, and all replicated cars;
each client predicts only its locally owned car.

Press Escape during a race to open the modal pause menu. Escape or **Resume**
continues the race; **Return to Main Menu** disconnects an active multiplayer
client and switches back to the mode-selection scene.

The server opens no game window and loads the same scene and scripts through
the engine's null-presentation backend. Sprite images remain metadata-only,
tile collision/navigation data remains available, and particle/trail APIs are
safe no-ops instead of allocating graphics resources. Linux selects pyglet's
EGL headless platform only when neither X11 nor Wayland is available. A Linux
desktop uses its normal display backend; a display-free Linux server needs an
EGL runtime library. macOS does not enable EGL because it is not normally
available there.

The project disables automatic client connection because the menu collects the
endpoint. It also selects separate startup scenes: `menu.scene.json` for the
game and `racing.scene.json` for the dedicated server. Exporting produces
`NetworkRacer` and `NetworkRacerServer` applications.
