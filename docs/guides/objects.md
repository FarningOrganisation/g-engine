# Objects, components, and scripts

Standalone factories create unattached objects; scene and game factories also
add them to the scene:

```python
from g import Game, RectRenderer, colors, create_rect

game = Game(800, 600)
wall = game.add(create_rect(200, 150, colors.SLATE_GRAY, 120, 24))
ball = game.add_circle(400, 300, colors.CORNFLOWER_BLUE, radius=12)
physical_wall = game.add_rect(
    200, 150, colors.SLATE_GRAY, 120, 24, collider=True
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
muzzle = create_circle(22, 0, colors.RED, 1)
sniper.add_child(muzzle)
sniper.face_towards(game.input.mouse_position, offset=0)
```

Pass `keep_upright=False` when only rotation is wanted. A sprite whose artwork
does not initially face along positive X can use the `offset` argument.

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
