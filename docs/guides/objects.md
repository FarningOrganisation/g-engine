# Objects, components, and scripts

Standalone factories create unattached objects; scene and game factories also
add them to the scene:

```python
from g import Game, RectRenderer, colors, create_rect

game = Game(800, 600)
wall = game.add(create_rect(200, 150, 120, 24, color=colors.SLATE_GRAY))
ball = game.add_circle(400, 300, 12, color=colors.CORNFLOWER_BLUE)
physical_wall = game.add_rect(
    200, 150, 120, 24, color=colors.SLATE_GRAY, collider=True
)
wall.require_component(RectRenderer).color = colors.LIGHT_SLATE_GRAY
```

A `GameObject` composes renderers, colliders, scripts, and other components.
Visual factories are collider-free by default. Pass `collider=True`, add a
custom `Collider`, or call `add_physics()` when the object should participate
in physics. The editor follows the same rule: sprite picking and selection
bounds come from the image dimensions, while collision geometry is added and
edited explicitly.
Use `get_component` for optional lookup and `require_component` when absence
should be an error. Ordinary hierarchy and transforms belong to `Node`;
gameplay-specific data belongs to `GameObject`.

## Rendering and camera culling

World-space renderers are conservatively culled against the active camera.
Sprites, shapes, text, tile-renderer patches, trails, and whole particle
effects leave their render batch while their bounds are outside the viewport.
Screen-space renderers are never camera-culled. Tile layers additionally cull
their chunk meshes, and lights are skipped when their range does not intersect
the viewport.

`SceneRenderer.stats` reports the most recent frame's submitted and culled
renderer counts, submitted vertices, batch draws, and approximate draw calls:

```python
stats = renderer.stats
print(stats.submitted_renderers, stats.culled_renderers)
print(stats.submitted_vertices, stats.approximate_draw_calls)
```

The bounds are deliberately conservative: content may occasionally be
submitted just outside a rotated viewport, but visible content is not removed.
Renderers without known bounds remain submitted.

## Direct per-frame drawing

Use a draw callback for changing overlays or short-lived visuals that do not
need a `GameObject`, component, collider, or scene lifetime. It runs once per
rendered frame in virtual screen coordinates, after scene objects and GUI but
before scene transitions:

```python
life_icon = game.resources.load_image("life.png")
lives = 3


def draw() -> None:
    for index in range(lives):
        life_icon.draw(16 + index * 24, game.height - 32)

    game.draw_circle(400, 300, 12, color=(255, 220, 80))
    game.draw_rect(400, 40, 160, 12, color=(80, 200, 120))
    game.draw_polygon(((700, 24), (760, 24), (730, 56)))


game.run(update=update, draw=draw)
```

Subclass-based games can override `Game.on_draw()` instead. Direct primitive
helpers may only be called from that hook or the `draw` callback. The engine
reuses their underlying drawables between frames and removes commands that
disappear. `Image.draw()` can also be called there and uses a cached sprite;
it does not perform pyglet's costly immediate texture blit each frame.

Use `draw_line()` with `dash_pattern=(dash_length, gap_length)` for dashed or
dotted guides:

```python
game.draw_line(
    (100, 100),
    (400, 100),
    thickness=4,
    dash_pattern=(4, 8),  # Short dashes produce a dotted appearance.
)
```

Persistent lines support the same option through `game.add_line(...)`,
`scene.add_line(...)`, `create_line(...)`, and `LineRenderer`. Set a
renderer's `dash_pattern` to `None` to make it solid again:

```python
from g import LineRenderer

guide = game.add_line(
    (100, 80),
    (400, 80),
    thickness=3,
    dash_pattern=(12, 6),
)
guide.require_component(LineRenderer).dash_pattern = None
```

Prefer a renderer when a visual needs world transforms, camera culling,
scene persistence, editor inspection, shaders, or a stable layer order.
`TrailRenderer` remains a continuous history of object movement; use dashed
lines for fixed or explicitly updated guides.

## Aiming sprites

Use `look_at()` when any node should rotate toward another node or a
world-space point. For sprite game objects, `face_towards()` can also keep the
artwork upright when aiming left:

```python
sniper.face_towards(game.input.mouse_position)
```

`face_towards()` changes `SpriteRenderer.flip_y`; it does not negate the
object's scale or affect its collider and child transforms. This keeps a
child muzzle marker on the barrel:

```python
muzzle = create_circle(22, 0, 1, color=colors.RED)
sniper.add_child(muzzle)
sniper.face_towards(game.input.mouse_position, offset=0)
```

Pass `keep_upright=False` when only rotation is wanted. A sprite whose artwork
does not initially face along positive X can use the `offset` argument.

## Moving forward

`Node.move(distance)`, also available on `GameObject`, moves along the current
world rotation. Zero degrees faces right (+X), and positive rotation is
clockwise. Pass a distance in world units, or multiply a speed by delta time:

```python
# Aim once when creating the bullet.
bullet.look_at(game.input.mouse_position)

# Move each update along its current facing direction.
bullet.move(bullet_speed * delta_time)
```

Negative distances move backward. Movement uses world units even under a
rotated or scaled parent; parent scale does not change the distance traveled.
The rotation is read on each call, so changing it steers subsequent movement.
The distance is not a speed: `move(100)` moves 100 units every time it is
called, whereas `move(100 * delta_time)` moves at 100 units per second.
This directly changes the transform, like `translate()`; it does not perform
a collision sweep or set physics velocity.
A parent with a zero world-scale component prevents world-position updates
and causes `move()` to raise `ValueError`.

For a click-to-shoot update callback, combine the
[mouse helpers](input.md#keyboard-and-mouse-buttons) with `look_at()` and
`move()`. This snippet assumes an existing `game` and `player`:

```python
from g import GameObject, colors

bullets: list[GameObject] = []
bullet_speed = 1000


def update(delta_time: float) -> None:
    if game.input.mouse_just_pressed():
        bullet = game.add_circle(
            *player.world_position, radius=3, color=colors.YELLOW
        )
        bullet.look_at(game.input.mouse_position)
        bullets.append(bullet)

    for bullet in bullets:
        bullet.move(bullet_speed * delta_time)
```

Each bullet keeps its initial aim unless its rotation is changed later.
This minimal example omits bullet lifetime and collision handling.

### Vectors from rotations

To construct a vector using the same clockwise-degree convention, use
`Vec2.from_rotation(rotation, length=1.0)`:

```python
from g import Vec2

direction = Vec2.from_rotation(bullet.world_rotation)
displacement = Vec2.from_rotation(bullet.world_rotation, length=10)
```

The default length is 1 (a unit direction); zero produces a zero vector, and
a negative length reverses the direction.

| Rotation in degrees | Direction |
| --- | --- |
| `0` | Right (+X) |
| `90` | Down (-Y) |
| `180` | Left (-X) |
| `270` or `-90` | Up (+Y) |

Use `world_rotation` for a world-space direction and `rotation` for a
direction relative to the parent. For an unparented object they are equal.
`move(distance)` is equivalent to adding
`Vec2.from_rotation(obj.world_rotation, distance)` to `obj.world_position`.

`Vec2.from_polar()` and `Vec2.from_heading()` retain their mathematical
convention of counterclockwise radians. Do not pass an object's
degree-based rotation directly to either method; use `from_rotation()` instead.

## Reusable behavior

```python
from g import Script, inspector_field, scene_component


@scene_component("game.Patrol")
class Patrol(Script):
    speed: float = inspector_field(
        120.0,
        minimum=0,
        tooltip="Horizontal movement speed in pixels per second.",
    )

    def update(self, delta_time: float) -> None:
        self.game_object.x += self.speed * delta_time
```

Attach an instance with `obj.add_component(Patrol())`.
Public annotated fields are authored/serialized fields. Names beginning with
`_` are runtime details unless explicitly registered. Inspector bounds guide
editor edits; they do not enforce every Python assignment.

Use lifecycle hooks such as `on_ready` for scene-dependent setup,
`on_scene_detach` when a reusable object leaves a scene, and `on_detach` when
the component is removed or its object is permanently deleted. Reattaching a
detached object runs `on_ready` again.
See the generated reference for exact hook ordering and component requirements.

## Arbitrary scripting data

```python
ball.properties["team"] = "blue"
ball.properties["damage"] = 25
```

`properties` is a `dict[str, Any]` intended for game-specific scripting data.
For persistence, its values must be supported by the scene encoder. This
data is saved separately as `script_properties`, not mixed with object codec
configuration.

## Deletion

`delete()` permanently removes an object subtree and releases its owned
resources:

```python
message.delete()

if message.is_deleted:
    print("The message cannot be reused")
```

Deletion is terminal and idempotent. Deleted objects cannot receive new
components or children and cannot be added to a scene again. Deleting a parent
also deletes its descendants. It is safe to call `delete()` during updates or
collision callbacks. Shared asset resources are not deleted with individual
objects.

Use `remove_child()` only to remove a parent-child relationship:

```python
player.remove_child(weapon)
```

If both objects belong to a scene, `weapon` becomes a top-level object in the
same layer without moving, rotating, or changing its visible scale. It remains
alive, rendered, updated, and physical. Outside a scene, it becomes an
unattached object with `parent is None`.

Use `detach()` to remove a live subtree from a scene without destroying it:

```python
saved_enemy = game.detach(enemy)

assert saved_enemy.scene is None
assert not saved_enemy.is_deleted

other_scene.add(saved_enemy)
```

Detach preserves components, renderer resources, children, properties, and
callbacks while removing render-batch, physics, navigation, and scene
membership. `Game.detach()`, `Scene.detach()`, and `ObjectLayer.detach()` return
the detached object. Existing `remove()` methods remain compatibility aliases
for permanent deletion; prefer `delete()` when writing new code.
