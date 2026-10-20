"""Framebuffer-based native 2D lighting and hard-shadow geometry."""

from __future__ import annotations

from math import atan2, cos, isfinite, pi, radians, sin, tau
from typing import TYPE_CHECKING

import pyglet
from pyglet.gl import (
    GL_BLEND,
    GL_BLEND_DST_RGB,
    GL_BLEND_SRC_RGB,
    GL_COLOR_BUFFER_BIT,
    GL_FRAMEBUFFER,
    GL_FRAMEBUFFER_BINDING,
    GL_LINEAR,
    GL_ONE,
    GL_TRIANGLE_FAN,
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
from pyglet.image.buffer import Framebuffer

from .components import Light2D, ShadowOccluder2D, SpotLight2D
from .resources import clamp_texture_edges
from .shader import PASSTHROUGH_FRAGMENT_SOURCE, POST_PROCESS_VERTEX_SOURCE, PostProcess, Shader

if TYPE_CHECKING:
    from .camera import Camera
    from .scene import Scene


LIGHT_VERTEX_SOURCE = """
#version 150 core
in vec2 position;
uniform vec2 resolution;

void main()
{
    vec2 clip = position / resolution * 2.0 - 1.0;
    gl_Position = vec4(clip, 0.0, 1.0);
}
"""

LIGHT_FRAGMENT_SOURCE = """
#version 150 core
out vec4 final_color;
uniform vec2 light_position;
uniform float radius;
uniform vec4 light_color;
uniform float intensity;
uniform float falloff;
uniform int is_spot;
uniform vec2 spot_direction;
uniform float outer_cos;
uniform float inner_cos;

void main()
{
    vec2 offset = gl_FragCoord.xy - light_position;
    float distance_to_light = length(offset);
    float radial = pow(max(0.0, 1.0 - distance_to_light / radius), falloff);
    float angular = 1.0;
    if (is_spot != 0 && distance_to_light > 0.0001) {
        float alignment = dot(normalize(offset), spot_direction);
        angular = smoothstep(outer_cos, inner_cos, alignment);
    }
    float strength = intensity * light_color.a * radial * angular;
    final_color = vec4(light_color.rgb * strength, 0.0);
}
"""

LIGHT_COMPOSITE_FRAGMENT_SOURCE = """
#version 150 core
in vec2 texture_coords;
out vec4 final_color;
uniform sampler2D scene_texture;
uniform sampler2D light_texture;

void main()
{
    vec4 scene = texture(scene_texture, texture_coords);
    vec3 lighting = texture(light_texture, texture_coords).rgb;
    final_color = vec4(scene.rgb * lighting, scene.a);
}
"""


Point = tuple[float, float]
Segment = tuple[Point, Point]


def _ray_segment_distance(
    origin: Point,
    angle: float,
    segment: Segment,
    maximum: float,
) -> float | None:
    direction = (cos(angle), sin(angle))
    start, end = segment
    edge = (end[0] - start[0], end[1] - start[1])
    denominator = direction[0] * edge[1] - direction[1] * edge[0]
    if abs(denominator) < 1e-9:
        return None
    offset = (start[0] - origin[0], start[1] - origin[1])
    ray_distance = (offset[0] * edge[1] - offset[1] * edge[0]) / denominator
    edge_amount = (offset[0] * direction[1] - offset[1] * direction[0]) / denominator
    if 0 <= ray_distance <= maximum and 0 <= edge_amount <= 1:
        return ray_distance
    return None


def _angular_difference(first: float, second: float) -> float:
    return (first - second + pi) % tau - pi


def _light_angles(
    light: Light2D,
    segments: tuple[Segment, ...],
) -> tuple[float, ...]:
    if isinstance(light, SpotLight2D):
        assert light.owner is not None
        direction = -radians(light.owner.world_rotation)
        half_angle = radians(light.angle) / 2
        start = direction - half_angle
        span = half_angle * 2
        angles = [start + span * index / 48 for index in range(49)]
        for segment in segments:
            for point in segment:
                angle = atan2(
                    point[1] - light.owner.world_position.y,
                    point[0] - light.owner.world_position.x,
                )
                if abs(_angular_difference(angle, direction)) <= half_angle:
                    angles.extend((angle - 1e-5, angle, angle + 1e-5))
        return tuple(sorted(angles))
    angles = [tau * index / 96 for index in range(97)]
    if light.owner is not None:
        origin = light.owner.world_position
        for segment in segments:
            for point in segment:
                angle = atan2(point[1] - origin.y, point[0] - origin.x)
                angles.extend(
                    (
                        (angle - 1e-5) % tau,
                        angle % tau,
                        (angle + 1e-5) % tau,
                    )
                )
    return tuple(sorted(angles))


def _visibility_polygon(
    light: Light2D,
    occluders: tuple[ShadowOccluder2D, ...],
) -> tuple[Point, ...]:
    """Return world points bounding the visible area of one attached light."""
    if light.owner is None:
        raise RuntimeError("light must be attached before rendering")
    origin = tuple(light.owner.world_position)
    maximum = light.maximum_range
    segments = tuple(
        segment
        for occluder in occluders
        if light.shadow_mask & occluder.occlusion_mask
        for segment in occluder.world_segments
    )
    if not light.casts_shadows:
        segments = ()
    points: list[Point] = []
    for angle in _light_angles(light, segments):
        distance = maximum
        for segment in segments:
            intersection = _ray_segment_distance(
                origin, angle, segment, maximum
            )
            if intersection is not None:
                distance = min(distance, intersection)
        points.append(
            (
                origin[0] + cos(angle) * distance,
                origin[1] + sin(angle) * distance,
            )
        )
    return tuple(points)


class LightingPipeline:
    """Lazily allocated scene-color, light-accumulation, and composite passes."""

    def __init__(self) -> None:
        """Create an unallocated lighting pipeline."""
        self._scene_capture = PostProcess(PASSTHROUGH_FRAGMENT_SOURCE)
        self._light_shader = Shader(LIGHT_VERTEX_SOURCE, LIGHT_FRAGMENT_SOURCE)
        self._composite_shader = Shader(
            POST_PROCESS_VERTEX_SOURCE,
            LIGHT_COMPOSITE_FRAGMENT_SOURCE,
        )
        self._quad = self._composite_shader.program.vertex_list_indexed(
            4,
            GL_TRIANGLES,
            (0, 1, 2, 0, 2, 3),
            position=("f", (-1, -1, 1, -1, 1, 1, -1, 1)),
            tex_coords=("f", (0, 0, 1, 0, 1, 1, 0, 1)),
        )
        self._light_texture: pyglet.image.Texture | None = None
        self._light_framebuffer: Framebuffer | None = None
        self._previous_framebuffer = (GLint * 1)()
        self._clear_color = (GLfloat * 4)()

    def begin_world(self, width: int, height: int) -> None:
        """Begin capturing background and world color."""
        self._scene_capture.begin(width, height)

    def end_world(self) -> None:
        """Finish world-color capture and restore the enclosing framebuffer."""
        self._scene_capture.end()

    def draw(self, scene: Scene, width: int, height: int) -> None:
        """Accumulate scene lights and composite them with captured world color."""
        source = self._scene_capture._texture
        if source is None:
            raise RuntimeError("lighting scene color was not captured")
        glGetIntegerv(GL_FRAMEBUFFER_BINDING, self._previous_framebuffer)
        glGetFloatv(pyglet.gl.GL_COLOR_CLEAR_VALUE, self._clear_color)
        self._ensure_light_target(width, height)
        if self._light_framebuffer is None or self._light_texture is None:
            raise RuntimeError("lighting accumulation target was not created")
        blend_was_enabled = bool(glIsEnabled(GL_BLEND))
        blend_source = (GLint * 1)()
        blend_destination = (GLint * 1)()
        glGetIntegerv(GL_BLEND_SRC_RGB, blend_source)
        glGetIntegerv(GL_BLEND_DST_RGB, blend_destination)
        try:
            self._light_framebuffer.bind()
            glViewport(0, 0, width, height)
            ambient = scene.ambient_light
            alpha = ambient[3] / 255 if len(ambient) == 4 else 1
            glClearColor(
                ambient[0] / 255 * alpha,
                ambient[1] / 255 * alpha,
                ambient[2] / 255 * alpha,
                1,
            )
            glClear(GL_COLOR_BUFFER_BIT)
            glEnable(GL_BLEND)
            glBlendFunc(GL_ONE, GL_ONE)
            for light in scene.lights:
                self._draw_light(
                    light,
                    scene.shadow_occluders,
                    scene.camera,
                    width,
                    height,
                )
        finally:
            glBindFramebuffer(GL_FRAMEBUFFER, self._previous_framebuffer[0])
            glClearColor(*self._clear_color)
            glBlendFunc(blend_source[0], blend_destination[0])
            if blend_was_enabled:
                glEnable(GL_BLEND)
            else:
                glDisable(GL_BLEND)
        glViewport(0, 0, width, height)
        self._composite_shader["scene_texture"] = 0
        self._composite_shader["light_texture"] = 1
        source.bind(0)
        self._light_texture.bind(1)
        self._composite_shader.program.use()
        self._quad.draw(GL_TRIANGLES)
        self._composite_shader.program.stop()

    def _draw_light(
        self,
        light: Light2D,
        occluders: tuple[ShadowOccluder2D, ...],
        camera: Camera,
        width: int,
        height: int,
    ) -> None:
        if light.owner is None or light.intensity <= 0:
            return
        center = camera.world_to_screen(light.owner.world_position)
        world_range = light.maximum_range
        radius = world_range * camera.zoom
        if (
            center.x + radius < 0
            or center.y + radius < 0
            or center.x - radius > width
            or center.y - radius > height
        ):
            return
        boundary = _visibility_polygon(light, occluders)
        screen_boundary = tuple(camera.world_to_screen(point) for point in boundary)
        vertices = (center, *screen_boundary)
        if len(vertices) < 4:
            return
        vertex_list = self._light_shader.program.vertex_list(
            len(vertices),
            GL_TRIANGLE_FAN,
            position=("f", tuple(value for point in vertices for value in point)),
        )
        color = light.color
        alpha = color[3] / 255 if len(color) == 4 else 1
        self._light_shader["resolution"] = (float(width), float(height))
        self._light_shader["light_position"] = (center.x, center.y)
        self._light_shader["radius"] = radius
        self._light_shader["light_color"] = (
            color[0] / 255,
            color[1] / 255,
            color[2] / 255,
            alpha,
        )
        self._light_shader["intensity"] = light.intensity
        self._light_shader["falloff"] = light.falloff
        if isinstance(light, SpotLight2D):
            direction_world = (
                light.owner.world_position.x
                + cos(-radians(light.owner.world_rotation)),
                light.owner.world_position.y
                + sin(-radians(light.owner.world_rotation)),
            )
            direction_point = camera.world_to_screen(direction_world)
            direction = (
                direction_point.x - center.x,
                direction_point.y - center.y,
            )
            length = (direction[0] ** 2 + direction[1] ** 2) ** 0.5
            if not isfinite(length) or length <= 0:
                vertex_list.delete()
                return
            self._light_shader["is_spot"] = 1
            self._light_shader["spot_direction"] = (
                direction[0] / length,
                direction[1] / length,
            )
            self._light_shader["outer_cos"] = cos(radians(light.angle / 2))
            self._light_shader["inner_cos"] = cos(
                radians(light.inner_angle / 2)
            )
        else:
            self._light_shader["is_spot"] = 0
            self._light_shader["spot_direction"] = (1.0, 0.0)
            self._light_shader["outer_cos"] = -1.0
            self._light_shader["inner_cos"] = 1.0
        self._light_shader.program.use()
        vertex_list.draw(GL_TRIANGLE_FAN)
        self._light_shader.program.stop()
        vertex_list.delete()

    def _ensure_light_target(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("lighting dimensions must be positive")
        if (
            self._light_texture is not None
            and self._light_texture.width == width
            and self._light_texture.height == height
        ):
            return
        if self._light_framebuffer is not None:
            self._light_framebuffer.delete()
        if self._light_texture is not None:
            self._light_texture.delete()
        self._light_texture = pyglet.image.Texture.create(
            width,
            height,
            min_filter=GL_LINEAR,
            mag_filter=GL_LINEAR,
        )
        clamp_texture_edges(self._light_texture)
        self._light_framebuffer = Framebuffer()
        self._light_framebuffer.attach_texture(self._light_texture)
        previous = (GLint * 1)()
        glGetIntegerv(GL_FRAMEBUFFER_BINDING, previous)
        try:
            self._light_framebuffer.bind()
            complete = self._light_framebuffer.is_complete
            status = self._light_framebuffer.get_status()
        finally:
            glBindFramebuffer(GL_FRAMEBUFFER, previous[0])
        if not complete:
            raise RuntimeError(
                f"lighting framebuffer is incomplete: {status}"
            )

    def delete(self) -> None:
        """Release framebuffer, texture, vertex, and shader-owned resources."""
        self._scene_capture.delete()
        self._quad.delete()
        if self._light_framebuffer is not None:
            self._light_framebuffer.delete()
            self._light_framebuffer = None
        if self._light_texture is not None:
            self._light_texture.delete()
            self._light_texture = None


__all__ = ["LightingPipeline"]
