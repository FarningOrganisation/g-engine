from __future__ import annotations

from collections.abc import Mapping
from math import cos, hypot, radians, sin

import pyglet
from pyglet import gl
from pyglet.graphics import Batch, Group
from pyglet.graphics.vertexdomain import IndexedVertexList
from pyglet.sprite import SpriteGroup

from .renderer import Renderer, set_vertex_attribute_region
from ..inspector import inspector_field
from ..particles import (
    ParticleEffectDefinition,
    ParticlePlayer,
    ParticleTransform,
)
from ..resources import Image, source_of
from ..runtime import NullDrawable, presentation_enabled


def _soft_circle_image(size: int = 32) -> pyglet.image.ImageData:
    if size < 2:
        raise ValueError("soft circle texture size must be at least 2")
    center = size / 2
    radius = size / 2
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            distance = hypot(
                (x + 0.5 - center) / radius,
                (y + 0.5 - center) / radius,
            )
            edge = max(0.0, min(1.0, (1.0 - distance) / 0.25))
            alpha = round(edge * edge * (3.0 - 2.0 * edge) * 255)
            pixels.extend((255, 255, 255, alpha))
    return pyglet.image.ImageData(
        size,
        size,
        "RGBA",
        bytes(pixels),
        pitch=size * 4,
    )


class ParticleEffect(Renderer):
    """Simulate and render a configured multi-emitter particle effect.

    The component delegates emission and playback state to ParticlePlayer.

    Attributes:
        effect: Optional asset identifier for the effect.
        autoplay: Whether playback starts when the component becomes ready.
        simulation_space: Particle coordinates mode, ``"world"`` or ``"local"``.
        seed: Random seed used for repeatable particle sampling.
        definition: Effect configuration used by the player.
        player: Runtime particle playback controller.
    """
    __inspector_fields__ = {
        "effect": inspector_field(
            "",
            widget="particle",
            tooltip="Particle effect asset edited in the Particles panel.",
        ),
        "autoplay": inspector_field(
            True,
            tooltip="Start the effect when its component becomes ready.",
        ),
        "simulation_space": inspector_field(
            "world",
            choices=("world", "local"),
            tooltip="Keep emitted particles in world space or attached to the owner.",
        ),
        "seed": inspector_field(
            0,
            tooltip="Deterministic random seed used for playback.",
        ),
    }

    def __init__(
        self,
        definition: ParticleEffectDefinition | None = None,
        *,
        effect: str = "",
        images: Mapping[str, Image] | None = None,
        autoplay: bool = True,
        simulation_space: str = "world",
        seed: int = 0,
        visible: bool = True,
        enabled: bool = True,
        screen_space: bool = False,
    ) -> None:
        """Create an effect player and its presentation resources.

        Args:
            definition: Effect definition, or a default single-emitter effect.
            effect: Optional asset identifier associated with the component.
            images: Image assets keyed by emitter name.
            autoplay: Start playback when ready.
            simulation_space: ``"world"`` or ``"local"`` particle coordinates.
            seed: Integer seed for deterministic random sampling.
            visible: Whether particles are drawn.
            enabled: Whether the renderer component is enabled.
            screen_space: Whether output uses screen coordinates.

        Raises:
            ValueError: The simulation space is not recognized.
            TypeError: The seed is not an integer.
        """
        super().__init__(
            visible=visible,
            enabled=enabled,
            screen_space=screen_space,
        )
        if simulation_space not in {"world", "local"}:
            raise ValueError("particle simulation space must be world or local")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError("particle seed must be an integer")
        self.effect = effect
        self.autoplay = autoplay
        self.simulation_space = simulation_space
        self.seed = seed
        self.definition = definition or ParticleEffectDefinition()
        self.player = ParticlePlayer(self.definition, seed=seed)
        self._images = dict(images or {})
        self._rendered_vertex_counts: list[int] = []
        self._presentation_enabled = presentation_enabled()
        if not self._presentation_enabled:
            self._placeholder = NullDrawable()
            self._fallback = None
            self._vertex_lists = []
            self._batch = None
            self._group = None
            return
        self._placeholder = pyglet.sprite.Sprite(
            pyglet.image.SolidColorImagePattern(
                (0, 0, 0, 0)
            ).create_image(1, 1)
        )
        self._fallback = _soft_circle_image().get_texture()
        self._vertex_lists: list[IndexedVertexList] = []
        self._batch: Batch | None = None
        self._group: Group | None = None

    @property
    def drawable(self) -> pyglet.sprite.Sprite | NullDrawable:
        """Placeholder drawable used by renderer integration."""
        return self._placeholder

    @property
    def playing(self) -> bool:
        """Whether the particle player is emitting or simulating."""
        return self.player.playing

    @property
    def paused(self) -> bool:
        """Whether the particle player is paused."""
        return self.player.paused

    @property
    def particle_count(self) -> int:
        """Number of live particles across all emitters."""
        return self.player.particle_count

    @property
    def dropped_count(self) -> int:
        """Number of emissions omitted because emitter capacity was full."""
        return self.player.dropped_count

    def on_ready(self) -> None:
        """Start playback when autoplay and presentation are enabled."""
        if self.autoplay and self._presentation_enabled:
            self.play()

    def on_detach(self) -> None:
        """Release the particle vertex lists."""
        self._delete_vertex_lists()

    def play(self, *, restart: bool = False) -> None:
        """Start playback; do nothing in headless presentation mode."""
        if not self._presentation_enabled:
            return
        self.player.play(restart=restart)

    def pause(self) -> None:
        """Pause the particle player."""
        self.player.pause()

    def resume(self) -> None:
        """Resume playback unless running headlessly."""
        if not self._presentation_enabled:
            return
        self.player.resume()

    def stop(self, *, clear: bool = False) -> None:
        """Stop playback and optionally clear existing particles."""
        self.player.stop(clear=clear)

    def clear(self) -> None:
        """Remove all live particles without changing playback configuration."""
        self.player.clear()

    def emit(self, count: int, *, emitter: str | None = None) -> int:
        """Emit particles immediately from one or all emitters.

        Args:
            count: Number of particles requested.
            emitter: Optional emitter name; ``None`` targets all emitters.

        Returns:
            Number of particles actually emitted, or zero headlessly.
        """
        if not self._presentation_enabled:
            return 0
        return self.player.emit(
            count,
            emitter=emitter,
            transform=self._emitter_transform(),
        )

    def update(self, delta_time: float) -> None:
        """Advance emission and simulation using the owner's current transform."""
        if not self._presentation_enabled:
            return
        self.player.update(delta_time, self._emitter_transform())

    def bind_batch(
        self,
        batch: Batch | None,
        group: Group | None,
    ) -> None:
        """Allocate particle buffers in a batch or release them when detached."""
        if not self._presentation_enabled:
            return
        self._placeholder.batch = batch
        self._placeholder.group = group
        if batch is self._batch and group is self._group:
            return
        self._delete_vertex_lists()
        self._batch = batch
        self._group = group
        if batch is None:
            return
        program = pyglet.sprite.get_default_shader()
        for order, emitter in enumerate(self.definition.emitters):
            texture = self._texture_for(emitter.name)
            blend_destination = (
                gl.GL_ONE
                if emitter.appearance.blend_mode == "additive"
                else gl.GL_ONE_MINUS_SRC_ALPHA
            )
            render_group = SpriteGroup(
                texture,
                gl.GL_SRC_ALPHA,
                blend_destination,
                program,
                Group(order=order, parent=group),
            )
            capacity = emitter.capacity
            vertex_count = capacity * 4
            coordinates = texture.tex_coords
            texture_quad = tuple(
                coordinate
                for index in range(0, 12, 3)
                for coordinate in coordinates[index:index + 3]
            )
            vertex_list = program.vertex_list_indexed(
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
                position=("f", (
                    -0.5, -0.5, 0, 0.5, -0.5, 0,
                    0.5, 0.5, 0, -0.5, 0.5, 0,
                ) * capacity),
                colors=("Bn", (0,) * (vertex_count * 4)),
                translate=("f", (0.0,) * (vertex_count * 3)),
                scale=("f", (1.0,) * (vertex_count * 2)),
                rotation=("f", (0.0,) * vertex_count),
                tex_coords=("f", texture_quad * capacity),
            )
            self._vertex_lists.append(vertex_list)
            self._rendered_vertex_counts.append(0)

    def sync(self) -> None:
        """Upload current particle transforms and colors into vertex buffers."""
        if not self._presentation_enabled:
            return
        self._placeholder.visible = False
        if len(self._vertex_lists) != len(self.definition.emitters):
            return
        visible = self.effective_visible
        snapshots = self.player.snapshots() if visible else {}
        for index, (emitter, vertex_list) in enumerate(zip(
            self.definition.emitters, self._vertex_lists
        )
        ):
            translations: list[float] = []
            scales: list[float] = []
            rotations: list[float] = []
            colors: list[int] = []
            particles = snapshots.get(emitter.name, ())
            for particle in particles:
                x, y = particle.position
                size = particle.size
                rotation = particle.rotation
                if self.simulation_space == "local" and self.owner is not None:
                    scale_x, scale_y = self.owner.world_scale
                    x *= scale_x
                    y *= scale_y
                    angle = -radians(self.owner.world_rotation)
                    x, y = (
                        x * cos(angle) - y * sin(angle),
                        x * sin(angle) + y * cos(angle),
                    )
                    world = self.owner.world_position
                    x += world.x
                    y += world.y
                    size *= (abs(scale_x) + abs(scale_y)) / 2
                    rotation += self.owner.world_rotation
                translations.extend((x, y, 0) * 4)
                scales.extend((size, size) * 4)
                rotations.extend((rotation,) * 4)
                colors.extend(particle.color * 4)
            vertex_count = len(particles) * 4
            previous_count = self._rendered_vertex_counts[index]
            if vertex_count == 0 and previous_count == 0:
                continue
            set_vertex_attribute_region(
                vertex_list, "translate", translations, vertex_count
            )
            set_vertex_attribute_region(
                vertex_list, "scale", scales, vertex_count
            )
            set_vertex_attribute_region(
                vertex_list, "rotation", rotations, vertex_count
            )
            set_vertex_attribute_region(
                vertex_list, "colors", colors, vertex_count
            )
            if vertex_count < previous_count:
                cleared = previous_count - vertex_count
                set_vertex_attribute_region(
                    vertex_list,
                    "colors",
                    (0,) * (cleared * 4),
                    cleared,
                    start_vertex=vertex_count,
                )
            self._rendered_vertex_counts[index] = vertex_count

    def _texture_for(self, emitter_name: str) -> pyglet.image.Texture:
        image = self._images.get(emitter_name)
        return source_of(image).get_texture() if image is not None else self._fallback

    def _emitter_transform(self) -> ParticleTransform:
        if self.owner is None:
            return ParticleTransform()
        if self.simulation_space == "local":
            return ParticleTransform()
        position = self.owner.world_position
        return ParticleTransform(
            (position.x, position.y),
            self.owner.world_rotation,
            self.owner.world_scale,
        )

    def _delete_vertex_lists(self) -> None:
        for vertex_list in self._vertex_lists:
            vertex_list.delete()
        self._vertex_lists.clear()
        self._rendered_vertex_counts.clear()
        self._batch = None
        self._group = None
