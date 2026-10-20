from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, overload

import pyglet
from pyglet.gl import (
    GL_BLEND,
    GL_COLOR_BUFFER_BIT,
    GL_FRAMEBUFFER,
    GL_FRAMEBUFFER_BINDING,
    GL_LINEAR,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_SRC_ALPHA,
    GL_TRIANGLES,
    GLfloat,
    GLint,
    glBindFramebuffer,
    glBlendFunc,
    glClear,
    glClearColor,
    glDisable,
    glEnable,
    glGetFloatv,
    glGetIntegerv,
    glIsEnabled,
    glViewport,
)
from pyglet.graphics.shader import (
    Shader as PygletShader,
    ShaderProgram as PygletShaderProgram,
)
from pyglet.image.buffer import Framebuffer

from .resources import clamp_texture_edges


POST_PROCESS_VERTEX_SOURCE = """
#version 150 core
in vec2 position;
in vec2 tex_coords;
out vec2 texture_coords;

void main()
{
    gl_Position = vec4(position, 0.0, 1.0);
    texture_coords = tex_coords;
}
"""

PASSTHROUGH_FRAGMENT_SOURCE = """
#version 150 core
in vec2 texture_coords;
out vec4 final_color;
uniform sampler2D scene_texture;

void main()
{
    final_color = texture(scene_texture, texture_coords);
}
"""


class Shader:
    """A compiled pyglet shader program with convenient uniform access."""
    def __init__(self, vertex_source: str, fragment_source: str) -> None:
        """Compile vertex and fragment shader source.

        Args:
            vertex_source: GLSL vertex shader source.
            fragment_source: GLSL fragment shader source.

        Raises:
            ValueError: Either source is empty.
        """
        if not vertex_source.strip() or not fragment_source.strip():
            raise ValueError("shader sources must not be empty")
        vertex = PygletShader(vertex_source, "vertex")
        fragment = PygletShader(fragment_source, "fragment")
        self._program = PygletShaderProgram(vertex, fragment)

    @classmethod
    def for_shapes(cls, fragment_source: str) -> Shader:
        """Build a shape shader using pyglet's standard shape vertex shader.

        Args:
            fragment_source: GLSL fragment shader source.
        """
        return cls(pyglet.shapes.vertex_source, fragment_source)

    @classmethod
    def for_sprites(cls, fragment_source: str) -> Shader:
        """Build a sprite shader using pyglet's standard sprite vertex shader.

        Args:
            fragment_source: GLSL fragment shader source.
        """
        return cls(pyglet.sprite.vertex_source, fragment_source)

    @classmethod
    def for_text(cls, fragment_source: str) -> Shader:
        """Build a text shader using pyglet's standard text vertex shader.

        Args:
            fragment_source: GLSL fragment shader source.
        """
        return cls(pyglet.text.layout.layout_vertex_source, fragment_source)

    @property
    def program(self) -> PygletShaderProgram:
        """Compiled pyglet shader program."""
        return self._program

    @property
    def uniforms(self) -> tuple[str, ...]:
        """Names of uniforms exposed by the compiled program."""
        return tuple(self._program.uniforms)

    def __getitem__(self, name: str) -> Any:
        """Read a uniform value by name."""
        return self._program[name]

    def __setitem__(self, name: str, value: Any) -> None:
        """Set a uniform value by name."""
        self._program[name] = value


class PostProcess:
    """Capture a frame and draw it through a fragment shader.

    Attributes:
        shader: Shader used to process the captured texture.
        enabled: Whether a pipeline should apply this effect.
        time: Accumulated simulation time exposed as a ``time`` uniform when
            that uniform exists.
    """
    def __init__(self, fragment_source: str) -> None:
        """Create a post-process effect from fragment shader source.

        Args:
            fragment_source: GLSL fragment shader source. The standard
                post-process vertex shader is supplied automatically.
        """
        self.shader = Shader(
            POST_PROCESS_VERTEX_SOURCE,
            fragment_source,
        )
        self.enabled = True
        self.time = 0.0
        self._texture: pyglet.image.Texture | None = None
        self._framebuffer: Framebuffer | None = None
        self._vertices = self.shader.program.vertex_list_indexed(
            4,
            GL_TRIANGLES,
            (0, 1, 2, 0, 2, 3),
            position=(
                "f",
                (-1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0, 1.0),
            ),
            tex_coords=(
                "f",
                (0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0),
            ),
        )
        self._clear_color = (GLfloat * 4)()
        self._previous_framebuffer = (GLint * 1)()

    def __getitem__(self, name: str) -> Any:
        """Read a shader uniform by name."""
        return self.shader[name]

    def __setitem__(self, name: str, value: Any) -> None:
        """Set a shader uniform by name."""
        self.shader[name] = value

    def update(self, delta_time: float) -> None:
        """Advance the effect clock and update a ``time`` uniform if present.

        Args:
            delta_time: Elapsed simulation time in seconds.
        """
        self.time += delta_time
        if "time" in self.shader.uniforms:
            self.shader["time"] = self.time

    def begin(self, width: int, height: int) -> None:
        """Begin rendering into this effect's framebuffer."""
        glGetIntegerv(
            GL_FRAMEBUFFER_BINDING,
            self._previous_framebuffer,
        )
        self._ensure_target(width, height)
        if self._framebuffer is None:
            raise RuntimeError("post-process framebuffer was not created")
        glGetFloatv(pyglet.gl.GL_COLOR_CLEAR_VALUE, self._clear_color)
        self._framebuffer.bind()
        glViewport(0, 0, width, height)
        glClearColor(0, 0, 0, 0)
        glClear(GL_COLOR_BUFFER_BIT)

    def end(self) -> None:
        """Restore the framebuffer and clear color saved by :meth:`begin`."""
        if self._framebuffer is not None:
            glBindFramebuffer(
                GL_FRAMEBUFFER,
                self._previous_framebuffer[0],
            )
        glClearColor(
            self._clear_color[0],
            self._clear_color[1],
            self._clear_color[2],
            self._clear_color[3],
        )

    def draw(self, width: int, height: int) -> None:
        """Draw the captured texture to the current framebuffer."""
        if self._texture is None:
            return
        self._draw_texture(self._texture, width, height)

    def _draw_texture(
        self,
        texture: pyglet.image.Texture,
        width: int,
        height: int,
        *,
        blend: bool = True,
    ) -> None:
        glViewport(0, 0, width, height)
        if "scene_texture" in self.shader.uniforms:
            self.shader["scene_texture"] = 0
        if "resolution" in self.shader.uniforms:
            self.shader["resolution"] = (float(width), float(height))
        blend_was_enabled = bool(glIsEnabled(GL_BLEND))
        if blend:
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        else:
            glDisable(GL_BLEND)
        texture.bind(0)
        self.shader.program.use()
        self._vertices.draw(GL_TRIANGLES)
        self.shader.program.stop()
        if blend_was_enabled:
            glEnable(GL_BLEND)
        else:
            glDisable(GL_BLEND)

    def delete(self) -> None:
        """Release vertex, framebuffer, and texture resources."""
        self._vertices.delete()
        if self._framebuffer is not None:
            self._framebuffer.delete()
            self._framebuffer = None
        if self._texture is not None:
            self._texture.delete()
            self._texture = None

    def _ensure_target(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("post-process dimensions must be positive")
        if (
            self._texture is not None
            and self._texture.width == width
            and self._texture.height == height
        ):
            return
        if self._framebuffer is not None:
            self._framebuffer.delete()
        if self._texture is not None:
            self._texture.delete()
        self._texture = pyglet.image.Texture.create(
            width,
            height,
            min_filter=GL_LINEAR,
            mag_filter=GL_LINEAR,
        )
        clamp_texture_edges(self._texture)
        self._framebuffer = Framebuffer()
        self._framebuffer.attach_texture(self._texture)
        self._framebuffer.bind()
        complete = self._framebuffer.is_complete
        status = self._framebuffer.get_status()
        self._framebuffer.unbind()
        if not complete:
            raise RuntimeError(f"post-process framebuffer is incomplete: {status}")


class PostProcessPipeline:
    """Ordered collection of post-process effects and frame capture buffers."""
    def __init__(self, *effects: PostProcess) -> None:
        """Create a pipeline and append the supplied effects.

        Args:
            effects: Effects to apply in order.
        """
        self.enabled = True
        self._effects: list[PostProcess] = []
        self._capture = PostProcess(PASSTHROUGH_FRAGMENT_SOURCE)
        self._buffer = PostProcess(PASSTHROUGH_FRAGMENT_SOURCE)
        self.extend(effects)

    def __iter__(self) -> Iterator[PostProcess]:
        """Iterate through effects in processing order."""
        return iter(self._effects)

    def __len__(self) -> int:
        """Return the number of effects in the pipeline."""
        return len(self._effects)

    @overload
    def __getitem__(self, index: int) -> PostProcess: ...

    @overload
    def __getitem__(self, index: slice) -> list[PostProcess]: ...

    def __getitem__(
        self, index: int | slice
    ) -> PostProcess | list[PostProcess]:
        """Return an effect or effect slice by index."""
        return self._effects[index]

    def add(self, effect: PostProcess) -> PostProcess:
        """Append an effect and return it.

        Raises:
            TypeError: ``effect`` is not a PostProcess.
            ValueError: ``effect`` is already present.
        """
        self._validate_new_effect(effect)
        self._effects.append(effect)
        return effect

    def extend(self, effects: Iterable[PostProcess]) -> None:
        """Append each effect in iteration order."""
        for effect in effects:
            self.add(effect)

    def insert(self, index: int, effect: PostProcess) -> PostProcess:
        """Insert an effect at an index and return it."""
        self._validate_new_effect(effect)
        self._effects.insert(index, effect)
        return effect

    def remove(self, effect: PostProcess) -> PostProcess:
        """Remove and return an effect.

        Raises:
            ValueError: The effect is not in the pipeline.
        """
        self._effects.remove(effect)
        return effect

    def move(self, effect: PostProcess, index: int) -> None:
        """Move an existing effect to an index in the processing order."""
        self._effects.remove(effect)
        self._effects.insert(index, effect)

    def clear(self) -> None:
        """Remove all effects without deleting their resources."""
        self._effects.clear()

    def update(self, delta_time: float) -> None:
        """Update enabled effects."""
        for effect in self._effects:
            if effect.enabled:
                effect.update(delta_time)

    def begin(self, width: int, height: int) -> None:
        """Start capturing the scene frame for post-processing."""
        self._capture.begin(width, height)

    def end(self) -> None:
        """Finish scene-frame capture."""
        self._capture.end()

    def draw(self, width: int, height: int) -> None:
        """Apply enabled effects in order and draw the final output."""
        source = self._capture._texture
        if source is None:
            return
        effects = [effect for effect in self._effects if effect.enabled]
        if not effects:
            self._capture._draw_texture(source, width, height)
            return

        output = self._buffer
        for index, effect in enumerate(effects):
            if index == len(effects) - 1:
                effect._draw_texture(source, width, height)
                continue
            output.begin(width, height)
            effect._draw_texture(source, width, height, blend=False)
            output.end()
            if output._texture is None:
                raise RuntimeError("post-process output texture was not created")
            source = output._texture
            output = (
                self._capture if output is self._buffer else self._buffer
            )

    def delete(self) -> None:
        """Release the pipeline's internal frame-capture buffers."""
        self._capture.delete()
        self._buffer.delete()

    def _validate_new_effect(self, effect: PostProcess) -> None:
        if not isinstance(effect, PostProcess):
            raise TypeError("effect must be a PostProcess")
        if effect in self._effects:
            raise ValueError("effect is already in this pipeline")
