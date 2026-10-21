from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeAlias

import pyglet
from pyglet.graphics.shader import ShaderProgram as PygletShaderProgram

from .base import Component
from ..inspector import inspector_field
from ..runtime import NullDrawable

if TYPE_CHECKING:
    from ..shader import Shader

if TYPE_CHECKING:
    Drawable: TypeAlias = (
        pyglet.shapes.ShapeBase | pyglet.sprite.Sprite
        | pyglet.text.Label | NullDrawable
    )
else:
    Drawable: TypeAlias = Any


def set_vertex_attribute_region(
    vertex_list: Any,
    name: str,
    data: object,
    vertex_count: int,
    *,
    start_vertex: int = 0,
) -> None:
    """Write data into a vertex attribute buffer region.

    Args:
        vertex_list: Vertex list whose attribute buffer is updated.
        name: Attribute name.
        data: Buffer-compatible values for the region.
        vertex_count: Number of vertices to update; non-positive values do
            nothing.
        start_vertex: First vertex in the vertex list to update.
    """
    if vertex_count <= 0:
        return
    buffer = vertex_list.domain.attrib_name_buffers[name]
    buffer.set_region(
        vertex_list.start + start_vertex,
        vertex_count,
        data,
    )


class Renderer(Component, ABC):
    """Base class for components that draw one or more graphics objects."""
    __inspector_fields__ = {
        "visible": inspector_field(True, tooltip="Show this renderer."),
        "screen_space": inspector_field(False, tooltip="Render in screen coordinates instead of world coordinates."),
    }
    def __init__(
        self,
        *,
        visible: bool = True,
        enabled: bool = True,
        screen_space: bool = False,
    ) -> None:
        """Configure renderer visibility and coordinate space.

        Args:
            visible: Whether its drawables may be shown.
            enabled: Whether renderer behavior is enabled.
            screen_space: Whether geometry is expressed in screen coordinates.
        """
        super().__init__(enabled=enabled)
        self.visible = visible
        self.screen_space = screen_space
        self._shader: Shader | None = None
        self._default_program: PygletShaderProgram | None = None
        self._camera_visible = True
        self._desired_batch: pyglet.graphics.Batch | None = None
        self._desired_group: pyglet.graphics.Group | None = None

    @property
    @abstractmethod
    def drawable(self) -> Drawable:
        """Primary drawable managed by this renderer."""
        pass

    @property
    def drawables(self) -> tuple[Drawable, ...]:
        """All drawables managed by this renderer."""
        return (self.drawable,)

    def bind_batch(
        self,
        batch: pyglet.graphics.Batch | None,
        group: pyglet.graphics.Group | None,
    ) -> None:
        """Assign or clear the pyglet batch and group for every drawable."""
        self._desired_batch = batch
        self._desired_group = group
        self._apply_batch_binding(
            batch if self._camera_visible else None,
            group if self._camera_visible else None,
        )

    def _apply_batch_binding(
        self,
        batch: pyglet.graphics.Batch | None,
        group: pyglet.graphics.Group | None,
    ) -> None:
        for drawable in self.drawables:
            drawable.batch = batch
            drawable.group = group

    @property
    def layer_ordered(self) -> bool:
        """Whether screen-space rendering belongs in the ordered object-layer pass."""
        return False

    @property
    def shader(self) -> Shader | None:
        """Custom shader, or ``None`` to use the drawable's original program."""
        return self._shader

    @shader.setter
    def shader(self, value: Shader | None) -> None:
        """Set a shader program on every drawable managed by this renderer."""
        if self._default_program is None:
            self._default_program = self.drawable.program
        program = (
            self._default_program if value is None else value.program
        )
        self._shader = value
        for drawable in self.drawables:
            drawable.program = program

    @property
    def effective_visible(self) -> bool:
        """Whether the renderer and its owning object are enabled and visible."""
        return self._camera_visible and self.intrinsic_visible

    @property
    def intrinsic_visible(self) -> bool:
        """Whether renderer state permits drawing before camera culling."""
        return bool(
            self.owner
            and self.enabled
            and self.visible
            and self.owner.enabled_in_hierarchy
            and self.owner.visible_in_hierarchy
        )

    def world_bounds(self) -> tuple[float, float, float, float] | None:
        """Return conservative world-space bounds, or ``None`` to disable culling."""
        return None

    def _set_camera_visible(self, visible: bool) -> None:
        """Attach or detach render resources according to camera visibility."""
        if visible == self._camera_visible:
            return
        self._camera_visible = visible
        self._apply_batch_binding(
            self._desired_batch if visible else None,
            self._desired_group if visible else None,
        )

    @abstractmethod
    def sync(self) -> None:
        """Synchronize drawable transforms and appearance from the owner."""
        pass
