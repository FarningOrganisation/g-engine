from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite

import pyglet
from pyglet import gl
from pyglet.graphics import Batch, Group
from pyglet.graphics.vertexdomain import IndexedVertexList
from pyglet.sprite import SpriteGroup

from .renderer import Renderer, set_vertex_attribute_region
from .renderer_utils import _transform_point
from ..inspector import inspector_field
from ..resources import Color, Image, clamp_texture_edges, source_of
from ..runtime import NullDrawable, presentation_enabled


@dataclass
class _TrailSegment:
    start: tuple[float, float]
    end: tuple[float, float]
    age: float = 0.0


class TrailRenderer(Renderer):
    """Render a bounded world-space trail sampled from object movement.

    Attributes:
        image: Optional texture used on each trail segment.
        emitting: Whether movement adds new segments.
        offset: Trail source position in owner-local coordinates.
        width: Segment width in world pixels.
        minimum_distance: Minimum movement before adding a segment.
        lifetime: Segment lifetime in seconds.
        color: Starting RGBA color.
        fade: Whether segments fade over their lifetime.
        max_segments: Maximum number of retained segments.
    """

    __inspector_fields__ = {
        "image": inspector_field(
            None,
            widget="image",
            tooltip=(
                "Optional texture for each trail segment. "
                "Empty uses a solid color strip."
            ),
        ),
        "emitting": inspector_field(
            True,
            tooltip="Add trail segments while the owner moves.",
        ),
        "offset": inspector_field(
            [0.0, 0.0],
            tooltip="Trail source position in owner-local coordinates.",
        ),
        "width": inspector_field(
            5.0,
            minimum=0.001,
            tooltip="Trail width in world pixels.",
        ),
        "minimum_distance": inspector_field(
            3.0,
            minimum=0.001,
            tooltip="Minimum movement before adding a segment.",
        ),
        "lifetime": inspector_field(
            12.0,
            minimum=0.001,
            tooltip="Seconds before a segment disappears.",
        ),
        "color": inspector_field(
            [40, 40, 40, 180],
            widget="color",
            tooltip="Trail color and starting opacity.",
        ),
        "fade": inspector_field(
            True,
            tooltip="Fade segments over their lifetime.",
        ),
        "max_segments": inspector_field(
            2_000,
            minimum=1,
            maximum=20_000,
            tooltip="Maximum retained segments.",
        ),
    }

    def __init__(
        self,
        *,
        image: Image | None = None,
        emitting: bool = True,
        offset: tuple[float, float] = (0.0, 0.0),
        width: float = 5.0,
        minimum_distance: float = 3.0,
        lifetime: float = 12.0,
        color: Color = (40, 40, 40, 180),
        fade: bool = True,
        max_segments: int = 2_000,
        visible: bool = True,
        enabled: bool = True,
        screen_space: bool = False,
    ) -> None:
        """Configure trail sampling and appearance.

        Args:
            image: Optional segment texture.
            emitting: Whether to add segments as the owner moves.
            offset: Local-space location sampled for movement.
            width: Positive segment width.
            minimum_distance: Positive sampling distance.
            lifetime: Positive segment lifetime in seconds.
            color: RGB or RGBA segment color.
            fade: Whether alpha decreases with segment age.
            max_segments: Segment limit from 1 through 20,000.
            visible: Whether the trail is shown.
            enabled: Whether renderer behavior is enabled.
            screen_space: Whether the trail uses screen coordinates.

        Raises:
            ValueError: A geometric, timing, color, or capacity setting is
                invalid.
            TypeError: ``image`` is not an Image or None.
        """
        super().__init__(
            visible=visible,
            enabled=enabled,
            screen_space=screen_space,
        )
        self.emitting = emitting
        self.offset = offset
        self.width = width
        self.minimum_distance = minimum_distance
        self.lifetime = lifetime
        self.color = color
        self.fade = fade
        self.max_segments = max_segments
        self._validate()
        self._segments: list[_TrailSegment] = []
        self._last_position: tuple[float, float] | None = None
        self._rendered_vertex_count = 0
        self._presentation_enabled = presentation_enabled()
        self._batch = None
        self._group = None
        self._vertex_list = None
        self._image = image
        if not self._presentation_enabled:
            self._placeholder = NullDrawable()
            self._fallback_texture = None
            self._texture = None
            return
        self._placeholder = pyglet.sprite.Sprite(
            pyglet.image.SolidColorImagePattern(
                (0, 0, 0, 0)
            ).create_image(1, 1)
        )
        self._fallback_texture = pyglet.image.SolidColorImagePattern(
            (255, 255, 255, 255)
        ).create_image(1, 1).get_texture()
        clamp_texture_edges(self._fallback_texture)
        self._texture = self._fallback_texture
        self._batch: Batch | None = None
        self._group: Group | None = None
        self._vertex_list: IndexedVertexList | None = None
        self._image: Image | None = None
        self.image = image

    @property
    def drawable(self) -> pyglet.sprite.Sprite | NullDrawable:
        """Placeholder drawable used by renderer integration."""
        return self._placeholder

    @property
    def segment_count(self) -> int:
        """Number of retained trail segments."""
        return len(self._segments)

    @property
    def image(self) -> Image | None:
        """Optional texture used by trail segments."""
        return self._image

    @image.setter
    def image(self, value: Image | None) -> None:
        """Set the segment texture or use the solid fallback."""
        if value is not None and not isinstance(value, Image):
            raise TypeError("trail image must be an Image or None")
        self._image = value
        if not self._presentation_enabled:
            return
        texture = (
            source_of(value).get_texture()
            if value is not None
            else self._fallback_texture
        )
        if texture is None:
            return
        clamp_texture_edges(texture)
        self._texture = texture
        if self._batch is not None and self._group is not None:
            batch = self._batch
            group = self._group
            self._delete_vertex_list()
            self.bind_batch(batch, group)

    @property
    def color(self) -> tuple[int, int, int, int]:
        """Trail color normalized to RGBA channels."""
        return self._color

    @color.setter
    def color(self, value: Color) -> None:
        """Set a three- or four-channel byte color."""
        channels = tuple(value)
        if len(channels) == 3:
            channels = (*channels, 255)
        if (
            len(channels) != 4
            or any(
                not isinstance(channel, int)
                or isinstance(channel, bool)
                or not 0 <= channel <= 255
                for channel in channels
            )
        ):
            raise ValueError(
                "trail color must contain three or four byte channels"
            )
        self._color = channels

    def on_detach(self) -> None:
        """Clear segment history and release the vertex list."""
        self.clear()
        self._delete_vertex_list()

    def clear(self) -> None:
        """Remove all segments and reset the movement sampling position."""
        self._segments.clear()
        self._last_position = None

    def update(self, delta_time: float) -> None:
        """Age and expire segments, then sample owner movement.

        Args:
            delta_time: Finite, non-negative elapsed time in seconds.
        """
        if not isfinite(delta_time) or delta_time < 0:
            raise ValueError(
                "trail delta_time must be finite and non-negative"
            )
        self._validate()
        if not self._presentation_enabled:
            return
        for segment in self._segments:
            segment.age += delta_time
        self._segments = [
            segment for segment in self._segments
            if segment.age < self.lifetime
        ]
        if not self.emitting or self.owner is None:
            self._last_position = None
            return
        current = _transform_point(self.owner, self.offset)
        if self._last_position is None:
            self._last_position = current
            return
        start = self._last_position
        distance = hypot(current[0] - start[0], current[1] - start[1])
        if distance < self.minimum_distance:
            return
        steps = max(1, int(distance / self.minimum_distance))
        for step in range(1, steps + 1):
            amount = step / steps
            end = (
                start[0] + (current[0] - start[0]) * amount,
                start[1] + (current[1] - start[1]) * amount,
            )
            self._segments.append(_TrailSegment(self._last_position, end))
            self._last_position = end
        if len(self._segments) > self.max_segments:
            del self._segments[:len(self._segments) - self.max_segments]

    def bind_batch(self, batch: Batch, group: Group) -> None:
        """Allocate bounded vertex storage in the supplied pyglet batch."""
        if not self._presentation_enabled:
            return
        self._placeholder.batch = batch
        self._placeholder.group = group
        if batch is self._batch and group is self._group:
            return
        self._delete_vertex_list()
        self._batch = batch
        self._group = group
        if self._texture is None:
            return
        program = pyglet.sprite.get_default_shader()
        render_group = SpriteGroup(
            self._texture,
            gl.GL_SRC_ALPHA,
            gl.GL_ONE_MINUS_SRC_ALPHA,
            program,
            group,
        )
        capacity = self.max_segments
        vertex_count = capacity * 4
        coordinates = self._texture.tex_coords
        texture_quad = tuple(
            coordinate
            for index in range(0, 12, 3)
            for coordinate in coordinates[index:index + 3]
        )
        self._vertex_list = program.vertex_list_indexed(
            vertex_count,
            gl.GL_TRIANGLES,
            tuple(
                value
                for index in range(capacity)
                for value in (
                    index * 4, index * 4 + 1, index * 4 + 2,
                    index * 4, index * 4 + 2, index * 4 + 3,
                )
            ),
            batch,
            render_group,
            position=("f", (0.0,) * (vertex_count * 3)),
            colors=("Bn", (0,) * (vertex_count * 4)),
            translate=("f", (0.0,) * (vertex_count * 3)),
            scale=("f", (1.0,) * (vertex_count * 2)),
            rotation=("f", (0.0,) * vertex_count),
            tex_coords=("f", texture_quad * capacity),
        )
        self._rendered_vertex_count = 0

    def sync(self) -> None:
        """Update segment vertices and colors for rendering."""
        if not self._presentation_enabled:
            return
        self._placeholder.visible = False
        vertex_list = self._vertex_list
        if vertex_list is None:
            return
        positions: list[float] = []
        colors: list[int] = []
        red, green, blue, alpha = self.color
        segments = self._segments if self.effective_visible else ()
        half_width = self.width / 2
        for segment in segments:
            delta_x = segment.end[0] - segment.start[0]
            delta_y = segment.end[1] - segment.start[1]
            length = hypot(delta_x, delta_y)
            if length == 0:
                continue
            normal_x = -delta_y / length * half_width
            normal_y = delta_x / length * half_width
            positions.extend((
                segment.start[0] + normal_x,
                segment.start[1] + normal_y, 0,
                segment.end[0] + normal_x,
                segment.end[1] + normal_y, 0,
                segment.end[0] - normal_x,
                segment.end[1] - normal_y, 0,
                segment.start[0] - normal_x,
                segment.start[1] - normal_y, 0,
            ))
            opacity = (
                round(alpha * max(0.0, 1.0 - segment.age / self.lifetime))
                if self.fade
                else alpha
            )
            colors.extend((red, green, blue, opacity) * 4)
        count = len(positions) // 12
        vertex_count = count * 4
        if vertex_count == 0 and self._rendered_vertex_count == 0:
            return
        set_vertex_attribute_region(
            vertex_list,
            "position",
            positions,
            vertex_count,
        )
        set_vertex_attribute_region(
            vertex_list,
            "colors",
            colors,
            vertex_count,
        )
        if vertex_count < self._rendered_vertex_count:
            cleared = self._rendered_vertex_count - vertex_count
            set_vertex_attribute_region(
                vertex_list,
                "colors",
                (0,) * (cleared * 4),
                cleared,
                start_vertex=vertex_count,
            )
        self._rendered_vertex_count = vertex_count

    def _delete_vertex_list(self) -> None:
        if self._vertex_list is not None:
            self._vertex_list.delete()
            self._vertex_list = None
        self._rendered_vertex_count = 0
        self._batch = None
        self._group = None

    def _validate(self) -> None:
        if (
            not isinstance(self.offset, (tuple, list))
            or len(self.offset) != 2
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not isfinite(value)
                for value in self.offset
            )
        ):
            raise ValueError("trail offset must contain two finite numbers")
        for name in ("width", "minimum_distance", "lifetime"):
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"trail {name} must be finite and positive")
        if (
            not isinstance(self.max_segments, int)
            or isinstance(self.max_segments, bool)
            or not 1 <= self.max_segments <= 20_000
        ):
            raise ValueError(
                "trail max_segments must be between 1 and 20000"
            )
