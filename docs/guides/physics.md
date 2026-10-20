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
