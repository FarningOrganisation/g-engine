# Shaders and post-processing

Renderers can use a shared custom `Shader`. Convenience constructors retain
pyglet's vertex format for shapes, sprites, and text:

```python
from g import CircleRenderer, Shader

shader = Shader.for_shapes("""
#version 150 core
in vec4 vertex_colors;
out vec4 final_color;
uniform float strength;

void main()
{
    final_color = vec4(vertex_colors.rgb * strength, vertex_colors.a);
}
""")
shader["strength"] = 0.7
# Assume ball exists.
ball.require_component(CircleRenderer).shader = shader
```

Set `renderer.shader = None` to restore the default program.
A shader shared by multiple renderers also shares its uniform values.
Custom fragment sources must match the corresponding pyglet inputs and
texture uniforms.

## Full-screen effects

```python
from g import PostProcess

effect = PostProcess("""
#version 150 core
in vec2 texture_coords;
out vec4 final_color;
uniform sampler2D scene_texture;

void main()
{
    vec4 color = texture(scene_texture, texture_coords);
    final_color = vec4(vec3(dot(color.rgb, vec3(0.299, 0.587, 0.114))), color.a);
}
""")
# Assume game exists.
game.scenes.current.post_process = effect
```

The engine supplies `scene_texture`, `time`, and `resolution` when declared.
Post-processing includes world, screen-space objects, and GUI. Scene
transition overlays draw afterward.

`PostProcessPipeline` chains effects in order with alternating framebuffer
textures. Every enabled effect adds a full-screen pass. Disable effects or
clear the scene's `post_process` when they are unnecessary.

## Native lighting interaction

Native [2D lighting](lighting.md) is a scene-level pipeline rather than a
replacement for each renderer's shader. Renderer shaders still produce scene
color; the engine then multiplies that captured world color by ambient, point,
and spot-light accumulation. Hard shadows clip light geometry through explicit
occluders.

Post-processing wraps the lit world plus screen-space objects and GUI. A
general post-process effect is therefore still separate from native lighting.
Normal maps and soft shadows are not currently supported.
