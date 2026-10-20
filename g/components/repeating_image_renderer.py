from __future__ import annotations

from math import isfinite

import pyglet
from pyglet.graphics.shader import Shader, ShaderProgram

from .renderer import Renderer
from ..inspector import inspector_field
from ..math import Vec2
from ..resources import Image, source_of
from ..runtime import NullDrawable, presentation_enabled


_VERTEX_SOURCE = """
#version 150 core
in vec3 translate;
in vec4 colors;
in vec3 tex_coords;
in vec2 scale;
in vec3 position;
in float rotation;
out vec4 vertex_colors;
out vec2 texture_coords;
uniform WindowBlock {
    mat4 projection;
    mat4 view;
} window;
void main() {
    float angle = -radians(rotation);
    mat2 turn = mat2(cos(angle), sin(angle), -sin(angle), cos(angle));
    vec2 point = turn * (position.xy * scale) + translate.xy;
    gl_Position = window.projection * vec4(point, translate.z, 1.0);
    texture_coords = tex_coords.xy;
    vertex_colors = colors;
}
"""

_FRAGMENT_SOURCE = """
#version 150 core
in vec4 vertex_colors;
in vec2 texture_coords;
out vec4 final_colors;
uniform sampler2D sprite_texture;
uniform vec2 tile_size;
uniform vec2 pattern_offset;
uniform vec2 repeat_axes;
uniform vec4 texture_bounds;
uniform vec2 viewport_size;
uniform vec2 half_texel;
void main() {
    vec2 screen_position = (texture_coords - texture_bounds.xy) /
        (texture_bounds.zw - texture_bounds.xy) * viewport_size;
    vec2 uv = (screen_position - pattern_offset) / tile_size;
    if ((repeat_axes.x == 0.0 && (uv.x < 0.0 || uv.x >= 1.0)) ||
        (repeat_axes.y == 0.0 && (uv.y < 0.0 || uv.y >= 1.0))) {
        discard;
    }
    vec2 wrapped = fract(uv);
    vec2 coordinates = mix(texture_bounds.xy, texture_bounds.zw, wrapped);
    coordinates = clamp(coordinates, texture_bounds.xy + half_texel, texture_bounds.zw - half_texel);
    final_colors = texture(sprite_texture, coordinates) * vertex_colors;
}
"""


def _vector(value: Vec2 | tuple[float, float], name: str, *, positive: bool = False) -> Vec2:
    result = Vec2(*value)
    if not all(isfinite(axis) and (not positive or axis > 0) for axis in result):
        raise ValueError(f"{name} must contain {'positive ' if positive else ''}finite coordinates")
    return result


class RepeatingImageRenderer(Renderer):
    """Draw a repeating image pattern across the viewport.

    Camera zoom and rotation are ignored. Pattern placement can follow the
    owning object's position, camera translation, and automatic scroll.
    """

    __inspector_fields__ = {
        "image": inspector_field(None, widget="image", tooltip="Image repeated across the viewport. May be empty."),
        "tile_size": inspector_field(Vec2(64, 64), minimum=0.001, tooltip="Displayed width and height of each image tile, in screen pixels."),
        "repeat_x": inspector_field(True, tooltip="Repeat the image horizontally."),
        "repeat_y": inspector_field(True, tooltip="Repeat the image vertically."),
        "offset": inspector_field(Vec2(), tooltip="Screen-pixel offset of the image pattern."),
        "parallax": inspector_field(Vec2(), tooltip="Per-axis camera translation factor: 0 is fixed, 1 matches world translation. Zoom and rotation are ignored."),
        "scroll_speed": inspector_field(Vec2(), tooltip="Automatic pattern movement in pixels per simulation second."),
        "opacity": inspector_field(255, minimum=0, maximum=255, tooltip="Opacity from transparent to opaque."),
    }

    def __init__(
        self, image: Image | None = None, *,
        tile_size: Vec2 | tuple[float, float] | None = None,
        repeat_x: bool = True, repeat_y: bool = True,
        offset: Vec2 | tuple[float, float] = (0, 0),
        parallax: Vec2 | tuple[float, float] = (0, 0),
        scroll_speed: Vec2 | tuple[float, float] = (0, 0),
        opacity: int = 255, visible: bool = True, enabled: bool = True,
    ) -> None:
        """Create a viewport image-pattern renderer.

        Args:
            image: Repeated image, or ``None`` for an empty pattern.
            tile_size: Positive on-screen tile dimensions; defaults to the
                image dimensions, or 64 by 64 without an image.
            repeat_x: Repeat along the horizontal axis.
            repeat_y: Repeat along the vertical axis.
            offset: Initial screen-pixel pattern offset.
            parallax: Per-axis camera translation factor.
            scroll_speed: Pattern movement in pixels per simulation second.
            opacity: Integer opacity from 0 through 255.
            visible: Whether the renderer is shown.
            enabled: Whether renderer behavior is enabled.

        Raises:
            TypeError: ``image`` is not an Image or None.
            ValueError: A vector is non-finite, tile size is not positive, or
                opacity is outside its byte range.
        """
        super().__init__(visible=visible, enabled=enabled, screen_space=True)
        if image is not None and not isinstance(image, Image):
            raise TypeError("repeating image must be an Image or None")
        self._image = image
        self.tile_size = _vector(
            tile_size if tile_size is not None else (image.width, image.height) if image else (64, 64),
            "tile_size", positive=True,
        )
        self.repeat_x = repeat_x
        self.repeat_y = repeat_y
        self.offset = _vector(offset, "offset")
        self.parallax = _vector(parallax, "parallax")
        self.scroll_speed = _vector(scroll_speed, "scroll_speed")
        self._scroll_offset = Vec2()
        self._pattern_uniforms: dict[str, tuple[float, ...]] = {}
        if not presentation_enabled():
            self._pattern_program = None
            self._drawable = NullDrawable()
            self._drawable.image = None
            self.opacity = opacity
            return
        self._pattern_program = ShaderProgram(
            Shader(_VERTEX_SOURCE, "vertex"), Shader(_FRAGMENT_SOURCE, "fragment"),
        )
        self._drawable = pyglet.sprite.Sprite(self._source(), 0, 0, program=self._pattern_program)
        self.opacity = opacity

    @property
    def screen_space(self) -> bool:
        """Always true; this renderer uses screen coordinates."""
        return True

    @screen_space.setter
    def screen_space(self, value: bool) -> None:
        """Reject attempts to disable screen-space rendering."""
        if value is not True:
            raise ValueError("RepeatingImageRenderer always uses screen coordinates")

    @property
    def layer_ordered(self) -> bool:
        """Whether this renderer participates in ordered object-layer drawing."""
        return True

    def _source(self) -> pyglet.image.AbstractImage:
        if self._image is not None:
            return source_of(self._image)
        return pyglet.image.SolidColorImagePattern((0, 0, 0, 0)).create_image(1, 1)

    @property
    def drawable(self) -> pyglet.sprite.Sprite:
        """Underlying sprite used to cover the viewport."""
        return self._drawable

    @property
    def image(self) -> Image | None:
        """Image currently used for the pattern, if any."""
        return self._image

    @image.setter
    def image(self, value: Image | None) -> None:
        """Replace the repeated image or clear it with ``None``."""
        if value is not None and not isinstance(value, Image):
            raise TypeError("repeating image must be an Image or None")
        self._image = value
        if isinstance(self._drawable, NullDrawable):
            return
        self._drawable.image = self._source()

    @property
    def opacity(self) -> int:
        """Pattern opacity from 0 through 255."""
        return self._drawable.opacity

    @opacity.setter
    def opacity(self, value: int) -> None:
        """Set opacity to an integer in the range 0 through 255."""
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError("opacity must be an integer between 0 and 255")
        self._drawable.opacity = value

    def update(self, delta_time: float) -> None:
        """Advance automatic scroll offset.

        Args:
            delta_time: Finite, non-negative simulation time in seconds.
        """
        if not isfinite(delta_time) or delta_time < 0:
            raise ValueError("background delta time must be finite and non-negative")
        self._scroll_offset += _vector(self.scroll_speed, "scroll_speed") * delta_time

    def on_detach(self) -> None:
        """Detach the drawable from its existing batch and hide it."""
        if self._drawable.image is not None:
            self._drawable.batch = pyglet.graphics.Batch()
            self._drawable.visible = False

    @property
    def pattern_offset(self) -> Vec2:
        """Current pattern offset including scroll, owner, and camera parallax."""
        offset = _vector(self.offset, "offset") + self._scroll_offset
        if self.owner is None or self.owner.scene is None:
            return offset
        scene = self.owner.scene
        camera_delta = scene.camera.position - Vec2(scene.width / 2, scene.height / 2)
        return offset + self.owner.world_position - camera_delta * _vector(self.parallax, "parallax")

    def sync(self) -> None:
        """Resize the drawable to the camera viewport and update uniforms."""
        if self.owner is None or self.owner.scene is None:
            self._drawable.visible = False
            return
        camera = self.owner.scene.camera
        source = self._drawable.image
        if not isinstance(source, pyglet.image.AbstractImage):
            raise RuntimeError("repeating image drawable must use a static image")
        texture = source.get_texture()
        scale_x = camera.viewport_width / texture.width
        scale_y = camera.viewport_height / texture.height
        if self._drawable.scale_x != scale_x:
            self._drawable.scale_x = scale_x
        if self._drawable.scale_y != scale_y:
            self._drawable.scale_y = scale_y
        position = texture.anchor_x * scale_x, texture.anchor_y * scale_y, self._drawable.z
        if self._drawable.position != position:
            self._drawable.position = position
        visible = self.effective_visible and self._image is not None
        if self._drawable.visible != visible:
            self._drawable.visible = visible
        coordinates = texture.tex_coords
        uniforms = {
            "texture_bounds": (coordinates[0], coordinates[1], coordinates[6], coordinates[7]),
            "tile_size": tuple(_vector(self.tile_size, "tile_size", positive=True)),
            "viewport_size": (camera.viewport_width, camera.viewport_height),
            "half_texel": (
                (coordinates[6] - coordinates[0]) / (2 * texture.width),
                (coordinates[7] - coordinates[1]) / (2 * texture.height),
            ),
            "pattern_offset": tuple(self.pattern_offset),
            "repeat_axes": (float(self.repeat_x), float(self.repeat_y)),
        }
        for name, value in uniforms.items():
            if self._pattern_uniforms.get(name) != value:
                self._pattern_program[name] = value
                self._pattern_uniforms[name] = value
