# 2D lighting and hard shadows

Native lighting is opt-in per scene. With no enabled light components, the
engine keeps its direct rendering path and allocates no lighting buffers.
Adding or enabling a point light or spotlight activates the lighting pipeline
automatically.

## Point lights

Use the game helper for an omnidirectional light:

```python
from g import Game, colors

game = Game(800, 600, bg_color=(25, 28, 36))
torch = game.add_point_light(
    400,
    300,
    radius=260,
    color=colors.ORANGE,
    intensity=1.2,
    falloff=1.4,
)
```

The returned value is an ordinary `GameObject`. Move it, parent it, disable
it, or retrieve its `PointLight2D` component like any other object.

For explicit component composition:

```python
from g import GameObject, PointLight2D

torch = game.add(GameObject(400, 300))
torch.add_component(PointLight2D(260, color=colors.ORANGE))
```

## Spotlights

A spotlight points along its owner's positive local x-axis. Its world rotation
therefore controls its direction:

```python
flashlight = game.add_spot_light(
    300,
    240,
    range=420,
    angle=50,
    inner_angle=35,
    rotation=0,
)
player.add_child(flashlight)
```

`angle` is the outer cone. `inner_angle` is fully illuminated; brightness
fades between the inner and outer angles. When omitted, `inner_angle` defaults
to 80 percent of `angle`.

## Ambient light

Ambient light is the minimum world illumination while native lighting is
active:

```python
game.scenes.current.ambient_light = (45, 50, 65, 255)
```

It is serialized as a scene setting. Ambient light does not change scenes
without enabled lights because those scenes use the original direct renderer.
Screen-space objects and GUI are drawn after lighting and remain unlit.

## Hard shadows

Set `casts_shadows=True` on a light and add explicit occluders to objects that
should block it:

```python
from g import ShadowOccluder2D

torch = game.add_point_light(
    200,
    200,
    radius=350,
    casts_shadows=True,
)

wall = game.add_rect(
    400,
    200,
    colors.SLATE_GRAY,
    128,
    32,
)
wall.add_component(ShadowOccluder2D.rectangle(128, 32))
```

Occluders are separate from colliders. Physics geometry often differs from a
visual silhouette, and adding an occluder never changes collision behavior.
When both shapes should match, author them explicitly with the same dimensions.

Available constructors are:

```python
ShadowOccluder2D.rectangle(width, height)
ShadowOccluder2D.circle(radius, segments=16)
ShadowOccluder2D(points=[(-20, -10), (30, 0), (-20, 10)])
```

Occluder points use the owner's local coordinates and follow parent position,
rotation, and scale. `shadow_mask` on a light and `occlusion_mask` on an
occluder can restrict which geometry blocks that light:

```python
light.shadow_mask = 0b0010
occluder.occlusion_mask = 0b0010
```

Only shadow-casting lights perform visibility calculations. Ordinary lights
remain cheaper.

## Editor workflow

In **Add Component > Lighting**, choose:

- **Point Light 2D**
- **Spot Light 2D**
- **Shadow Occluder 2D**

Select the scene itself to edit **Ambient Light** in the Inspector. The editor
uses the runtime renderer, so enabled lights and shadows appear in the scene
viewport. Occluder polygon points are currently edited in the Inspector.

## Rendering order and post-processing

Lighting captures the background and world, accumulates ambient and native
lights, and composites the two textures. Screen-space renderers and GUI draw
after that composite. Existing scene post-processing wraps the complete result,
including UI, as before.

Scenes with enabled lights add off-screen color and light targets plus a
composite pass. Disable unused lights and avoid unnecessary shadow casting on
large numbers of dynamic lights.

## Current limitations

The first implementation intentionally provides hard polygonal shadows.
It does not yet provide:

- Soft or translucent shadows
- Sprite-alpha silhouettes
- Per-renderer light masks
- Normal maps or specular highlights
- Area and directional lights
- Reduced-resolution light buffers

These can be added without changing the point-light and spotlight component
model.
