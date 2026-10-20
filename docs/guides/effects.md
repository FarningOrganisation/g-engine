# Animation, particles, and trails

## Property animation

Game objects expose a fluent animation builder:

```python
# Assume player exists.
handle = player.animate().to(0.5, x=400, opacity=255).start()
handle.pause()
handle.resume()
```

Behaviours support waits, callbacks, easing, parallel work, and repetition.
Numbers, vectors, and RGB/RGBA colors can be interpolated. Starting another
object animation replaces the previous object animation; GUI widgets use
named animation channels instead.

See the [behaviours API](../api/behaviours.md) for sequencing and cancellation
semantics.

## Particles

`ParticleEffect` attaches reusable `.particle.json` definitions to an object.
Definitions can contain multiple emitters, spawn shapes, bursts, motion,
appearance, and deterministic seeds.

The editor's Particles panel edits assets with isolated play, pause, restart,
stop, and step controls. Built-in presets include fire, smoke, sparks,
explosion, rain, and snow. Presets become ordinary project assets, not hidden
engine state.

The [particle definitions reference](../api/particles.md) documents the data
model; component playback methods are in the
[components reference](../api/components.md).

## Trails

`TrailRenderer` records bounded world-space samples for tire marks and other
motion trails. Width, fade, texture, and lifetime determine their appearance.
Trails are distinct from ordinary sprite animation and do not replace
physics or collision geometry.
