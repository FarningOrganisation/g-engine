# Physics and spatial queries

`game.physics` forwards to the active scene's Pymunk-backed physics world.
Colliders define shapes and filtering. A `PhysicsBody` provides body motion
and shared filtering defaults; objects without bodies can still have static
colliders.

Visual factories are collider-free by default. Pass `collider=True` for an
automatically sized solid collider, attach a custom `Collider`, or call
`add_physics()` to infer a collider and body from a supported renderer.
Movement-motor helpers also infer their required collider. A child visual
therefore does not become a separate static obstacle merely because it uses a
sprite or shape renderer.

## Moving physics bodies

A dynamic `PhysicsBody` owns its object's runtime world position and rotation.
After the body has entered the physics world, direct changes such as
`player.position = ...`, `player.move(...)`, or
`player.move_towards(...)` are discarded with a
`PhysicsTransformWarning`. Choose the movement operation that matches the
intended behavior:

```python
body = player.require_component(PhysicsBody)

# Direct continuous control, in pixels per second.
body.velocity = (200, 0)

# Physical acceleration or an instantaneous impact.
body.apply_force((2000, 0))
body.apply_impulse((0, 300))

# Explicit repositioning without render interpolation.
player.teleport(400, 300)
```

`GameObject.teleport()` forwards to an attached body and otherwise updates the
normal object transform. `PhysicsBody.teleport()` provides the same explicit
body-level operation. Kinematic and static bodies follow direct object
transform changes; a dynamic body with fixed rotation also follows direct
rotation changes.

For player movement, prefer a motor:

```python
motor = player.add_top_down_motor(speed=250)
motor.move(input_direction)
motor.move_towards(target)
```

`TopDownMotor.move_towards()` converts a world-space object or point into
directional intent and retains the motor's configured speed, acceleration, and
deceleration. Platformer movement uses `PlatformerMotor.move()` and `jump()`.

## Query without creating a scene object

```python
# Assume game exists.
nearby = game.physics.overlap_circle((200, 150), 80)
```

This uses a physics query shape, not a temporary object inserted into the
scene. Do not create and delete an `Area` just to collect nearby objects.

Available query families include:

- `overlap_circle`, `overlap_box`, and `overlap_polygon`
- Rich overlap variants returning contact information
- `raycast` and richer `raycast_hit` / `raycast_all_hits` results
- `nearest` for closest-point results

Use the [physics API reference](../api/physics.md) for exact signatures,
filtering options, exclusions, result types, and no-hit behavior.
Query methods with a radius on a ray can perform a thick segment query.

## Persistent sensor regions

Use `Area` when a region should remain in the world and track overlaps over
time. `Area.overlapping_objects` provides its current collection. Sensor
callbacks suit triggers, pickups, and detection zones.

## Collision details

`collides_with(other)` returns a `CollisionContact` or `None`.
Normal collision callbacks and area callbacks are distinct: a sensor is not
intended to provide the same physical response as a solid collider.

Categories and masks filter collision participation. Collider groups exclude
matching nonzero groups. Generated tile geometry participates in physics
without creating a hidden object for every tile: queries can identify its
owning `TileLayer`, and tile callbacks use `TileContact`.

For common movement, start with `TopDownMotor` or `PlatformerMotor` rather than
reimplementing grounding and acceleration from scratch.
