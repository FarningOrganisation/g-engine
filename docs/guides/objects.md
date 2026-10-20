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

Use lifecycle hooks such as `on_ready` for scene-dependent setup, and
`on_detach` to release subscriptions or other external resources.
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

Call `obj.delete()` to remove an object safely during updates or collision
callbacks. Shared asset resources are not deleted with individual objects.
