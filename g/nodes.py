from __future__ import annotations

from collections.abc import Callable, Iterable
from math import atan2, cos, degrees, isfinite, radians, sin
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar

from .behaviours import AnimationBuilder, Behaviour
from .geometry import BoxGeometry, CircleGeometry
from .math import Vec2

if TYPE_CHECKING:
    from .components import Collider, Component, Renderer
    from .physics import (
        BodyType,
        PhysicsBody,
        CollisionContact,
    )
    from .scripts import PlatformerMotor, TopDownMotor
    from .scene import ObjectLayer, Scene
    from .shader import Shader
    from .tilemap import TileContact, TileLayer


ComponentType = TypeVar("ComponentType", bound="Component")
CollisionTarget: TypeAlias = "GameObject | TileLayer"
CollisionHandler = Callable[["CollisionContact"], None]
OverlapHandler = Callable[["GameObject"], None]
ContactEvent = TypeVar("ContactEvent", "CollisionContact", "GameObject")
TileCollisionHandler = Callable[["TileContact"], None]


class Node:
    """Hierarchical transform node with parent-relative visibility and enablement.

    Args:
        x (float): Local horizontal position.
        y (float): Local vertical position.
        rotation (float): Local rotation in degrees.
        scale_x (float): Local horizontal scale.
        scale_y (float): Local vertical scale.
        scale (float | Vec2 | tuple[float, float] | None): Optional combined
            uniform or non-uniform scale.
        visible (bool): Whether this node is visible.
        enabled (bool): Whether this node is enabled.
        z_index (int): Drawing order within its layer.
        layer (int | None): Deprecated alias for ``z_index``.

    Raises:
        ValueError: If both ``layer`` and a nonzero ``z_index`` are given.
    """

    def __init__(
        self,
        x: float = 0,
        y: float = 0,
        *,
        rotation: float = 0,
        scale_x: float = 1,
        scale_y: float = 1,
        scale: float | Vec2 | tuple[float, float] | None = None,
        visible: bool = True,
        enabled: bool = True,
        z_index: int = 0,
        layer: int | None = None,
    ) -> None:
        """Initialize a node transform and hierarchy state."""
        if layer is not None:
            if z_index != 0:
                raise ValueError("use either z_index or the legacy layer argument")
            z_index = layer
        if scale is not None and (scale_x != 1 or scale_y != 1):
            raise ValueError("use either scale or scale_x and scale_y")
        if scale is not None:
            if isinstance(scale, (int, float)):
                scale_x = scale_y = scale
            else:
                scale_x, scale_y = scale
        self._position = Vec2(x, y)
        self.rotation = rotation
        self.scale_x = scale_x
        self.scale_y = scale_y
        self.visible = visible
        self.enabled = enabled
        self.z_index = z_index
        self._parent: Node | None = None
        self._children: list[Node] = []
        self._interpolation_revision = 0

    def reset_interpolation(self) -> None:
        """Show a teleport immediately, without blending from the previous tick."""
        for node in self.walk():
            node._interpolation_revision += 1

    @property
    def position(self) -> Vec2:
        """Local position as a mutable vector."""
        return self._position

    @position.setter
    def position(self, value: Vec2 | tuple[float, float]) -> None:
        """Set the local position from a vector or coordinate pair."""
        self._position = Vec2(*value)

    @property
    def x(self) -> float:
        """Local horizontal position."""
        return self._position.x

    @x.setter
    def x(self, value: float) -> None:
        """Set the local horizontal position."""
        self._position.x = value

    @property
    def y(self) -> float:
        """Local vertical position."""
        return self._position.y

    @y.setter
    def y(self, value: float) -> None:
        """Set the local vertical position."""
        self._position.y = value

    @property
    def angle(self) -> float:
        """Alias for local rotation in degrees."""
        return self.rotation

    @angle.setter
    def angle(self, value: float) -> None:
        """Set local rotation in degrees."""
        self.rotation = value

    @property
    def scale(self) -> Vec2:
        """Local horizontal and vertical scale as a vector snapshot."""
        return Vec2(self.scale_x, self.scale_y)

    @scale.setter
    def scale(self, value: float | Vec2 | tuple[float, float]) -> None:
        """Set uniform scale from a number or non-uniform scale from a pair."""
        if isinstance(value, (int, float)):
            self.scale_x = self.scale_y = value
        else:
            self.scale_x, self.scale_y = value

    @property
    def parent(self) -> Node | None:
        """Parent node, if any."""
        return self._parent

    @parent.setter
    def parent(self, value: Node | None) -> None:
        """Reparent this node while preserving its local transform.

        Raises:
            ValueError: If reparenting would create a cycle or cross scene layers.
        """
        if value is self._parent:
            return
        if value is self or (value is not None and self.is_ancestor_of(value)):
            raise ValueError("a node cannot be parented to itself or a descendant")
        own_layer = getattr(self, "_layer", None)
        parent_layer = getattr(value, "_layer", None)
        if (
            own_layer is not None
            and parent_layer is not None
            and own_layer is not parent_layer
        ):
            raise ValueError("parent and child must belong to the same scene layer")
        if self._parent is not None:
            self._parent._children.remove(self)
        self._parent = value
        if value is not None:
            value._children.append(self)

    @property
    def children(self) -> tuple[Node, ...]:
        """Direct children as an immutable snapshot."""
        return tuple(self._children)

    def add_child(self, child: Node) -> Node:
        """Parent ``child`` to this node and return it."""
        child.parent = self
        return child

    def remove_child(self, child: Node) -> Node:
        """Detach and return a direct child.

        Raises:
            ValueError: If ``child`` is not a direct child of this node.
        """
        if child.parent is not self:
            raise ValueError("node is not a child of this parent")
        child.parent = None
        return child

    def is_ancestor_of(self, node: Node) -> bool:
        """Return whether this node is an ancestor of ``node``."""
        current = node.parent
        while current is not None:
            if current is self:
                return True
            current = current.parent
        return False

    @property
    def world_rotation(self) -> float:
        """Combined rotation in world space, in degrees."""
        if self.parent is None:
            return self.rotation
        return self.parent.world_rotation + self.rotation

    @world_rotation.setter
    def world_rotation(self, value: float) -> None:
        """Set world rotation by adjusting local rotation."""
        self.rotation = (
            value
            if self.parent is None
            else value - self.parent.world_rotation
        )

    @property
    def world_scale(self) -> tuple[float, float]:
        """Combined horizontal and vertical world scales."""
        if self.parent is None:
            return self.scale_x, self.scale_y
        parent_scale_x, parent_scale_y = self.parent.world_scale
        return parent_scale_x * self.scale_x, parent_scale_y * self.scale_y

    @property
    def world_position(self) -> Vec2:
        """Position in world coordinates."""
        if self.parent is None:
            return self.position.copy()
        parent_x, parent_y = self.parent.world_position
        parent_scale_x, parent_scale_y = self.parent.world_scale
        local_x = self.x * parent_scale_x
        local_y = self.y * parent_scale_y
        rotation = -radians(self.parent.world_rotation)
        cosine = cos(rotation)
        sine = sin(rotation)
        return Vec2(
            parent_x + local_x * cosine - local_y * sine,
            parent_y + local_x * sine + local_y * cosine,
        )

    @world_position.setter
    def world_position(
        self,
        value: Vec2 | tuple[float, float],
    ) -> None:
        """Set world position, converting through the parent transform.

        Raises:
            ValueError: If a parent has a zero world-scale component.
        """
        target = Vec2(*value)
        if self.parent is None:
            self.position = target
            return
        parent_x, parent_y = self.parent.world_position
        parent_scale_x, parent_scale_y = self.parent.world_scale
        if parent_scale_x == 0 or parent_scale_y == 0:
            raise ValueError(
                "cannot set world position through a parent "
                "with zero scale"
            )
        delta_x = target.x - parent_x
        delta_y = target.y - parent_y
        angle = radians(self.parent.world_rotation)
        cosine = cos(angle)
        sine = sin(angle)
        self.position = (
            (delta_x * cosine - delta_y * sine) / parent_scale_x,
            (delta_x * sine + delta_y * cosine) / parent_scale_y,
        )

    def translate(
        self,
        x: float,
        y: float,
        *,
        space: str = "local",
    ) -> None:
        """Move by a delta in local or world space.

        Args:
            x (float): Horizontal translation.
            y (float): Vertical translation.
            space (str): Either ``"local"`` or ``"world"``.

        Raises:
            ValueError: If ``space`` is not supported.
        """
        delta = Vec2(x, y)
        if space == "local":
            self.position += delta.rotate(-radians(self.rotation))
        elif space == "world":
            self.world_position = self.world_position + delta
        else:
            raise ValueError(
                "transform space must be 'local' or 'world'"
            )

    def set_world_transform(
        self,
        *,
        position: Vec2 | tuple[float, float] | None = None,
        rotation: float | None = None,
    ) -> None:
        """Set either or both world-space position and rotation."""
        if position is not None:
            self.world_position = position
        if rotation is not None:
            self.world_rotation = rotation

    @staticmethod
    def _target_position(
        target: Node | Vec2 | tuple[float, float],
    ) -> Vec2:
        return target.world_position if isinstance(target, Node) else Vec2(*target)

    def distance_to(
        self,
        target: Node | Vec2 | tuple[float, float],
    ) -> float:
        """Return the world-space distance to a node or point."""
        return self.world_position.distance(self._target_position(target))

    def direction_to(
        self,
        target: Node | Vec2 | tuple[float, float],
    ) -> Vec2:
        """Return the normalized world-space direction to a node or point."""
        offset = self._target_position(target) - self.world_position
        return offset.normalize() if offset else Vec2.zero()

    def angle_to(
        self,
        target: Node | Vec2 | tuple[float, float],
    ) -> float:
        """Return the clockwise world-space angle to a node or point in degrees."""
        offset = self._target_position(target) - self.world_position
        return degrees(atan2(-offset.y, offset.x))

    def look_at(
        self,
        target: Node | Vec2 | tuple[float, float],
        *,
        offset: float = 0,
    ) -> None:
        """Rotate in world space to face a node or point."""
        self.world_rotation = self.angle_to(target) + offset

    def move(self, distance: float) -> None:
        """Move forward along the current world rotation by a world-space distance.

        Zero rotation faces +X. Negative distances move backward. Multiply a
        speed by delta time to get frame-rate-independent movement. Parent
        scale does not change the distance traveled.

        Raises:
            ValueError: If a parent has a zero world-scale component.
        """
        self.world_position += Vec2.from_rotation(self.world_rotation, distance)

    def move_towards(
        self,
        target: Node | Vec2 | tuple[float, float],
        max_distance: float,
    ) -> None:
        """Move in world space toward a node or point without overshooting."""
        self.world_position = self.world_position.move_towards(
            self._target_position(target),
            max_distance,
        )

    def rotate_towards(
        self,
        target: Node | Vec2 | tuple[float, float],
        max_degrees: float,
        *,
        offset: float = 0,
    ) -> None:
        """Rotate in world space toward a target along the shortest arc."""
        if not isfinite(max_degrees) or max_degrees < 0:
            raise ValueError("max_degrees must be finite and non-negative")
        target_rotation = self.angle_to(target) + offset
        difference = (target_rotation - self.world_rotation + 180) % 360 - 180
        if abs(difference) <= max_degrees:
            self.world_rotation = target_rotation
        else:
            self.world_rotation += max_degrees if difference > 0 else -max_degrees

    @property
    def enabled_in_hierarchy(self) -> bool:
        """Whether this node and all of its ancestors are enabled."""
        return self.enabled and (
            self.parent is None or self.parent.enabled_in_hierarchy
        )

    @property
    def visible_in_hierarchy(self) -> bool:
        """Whether this node and all of its ancestors are visible."""
        return self.visible and (
            self.parent is None or self.parent.visible_in_hierarchy
        )

    def walk(self) -> tuple[Node, ...]:
        """Return this node and all descendants in depth-first order."""
        descendants: list[Node] = [self]
        for child in self.children:
            descendants.extend(child.walk())
        return tuple(descendants)

    def get_children(self, *, recursive: bool = False) -> tuple[GameObject, ...]:
        """Return child game objects, optionally including all descendants."""
        nodes = self.walk()[1:] if recursive else self.children
        return tuple(node for node in nodes if isinstance(node, GameObject))

    def get_child(self, name: str, *, recursive: bool = False) -> GameObject | None:
        """Find a named child game object, returning ``None`` if absent."""
        return next(
            (child for child in self.get_children(recursive=recursive) if child.name == name),
            None,
        )

    def require_child(self, name: str, *, recursive: bool = False) -> GameObject:
        """Find a named child or raise ``LookupError`` if it is absent."""
        child = self.get_child(name, recursive=recursive)
        if child is None:
            object_name = f" {self.name!r}" if isinstance(self, GameObject) and self.name is not None else ""
            scope = "descendant" if recursive else "child"
            raise LookupError(
                f"{type(self).__name__}{object_name} does not have {scope} {name!r}"
            )
        return child


class GameObject(Node):
    """Scene object that combines a transform hierarchy with components.

    Args:
        x (float): Local horizontal position.
        y (float): Local vertical position.
        rotation (float): Local rotation in degrees.
        scale_x (float): Local horizontal scale.
        scale_y (float): Local vertical scale.
        scale (float | Vec2 | tuple[float, float] | None): Optional combined
            uniform or non-uniform scale.
        visible (bool): Whether the object is visible.
        enabled (bool): Whether the object is enabled.
        z_index (int): Drawing order within its layer.
        layer (int | None): Deprecated alias for ``z_index``.

    Attributes:
        name: Optional object name used by hierarchy lookups.
        properties: User-defined metadata.
        speed: Local movement speed in pixels per second.
        scene_id: Optional stable scene identifier.
        render_behind_parent: Whether renderers draw before their parent.
    """

    def __init__(
        self,
        x: float = 0,
        y: float = 0,
        *,
        rotation: float = 0,
        scale_x: float = 1,
        scale_y: float = 1,
        scale: float | Vec2 | tuple[float, float] | None = None,
        visible: bool = True,
        enabled: bool = True,
        z_index: int = 0,
        layer: int | None = None,
    ) -> None:
        """Initialize a game object with an empty component set."""
        super().__init__(
            x,
            y,
            rotation=rotation,
            scale_x=scale_x,
            scale_y=scale_y,
            scale=scale,
            visible=visible,
            enabled=enabled,
            z_index=z_index,
            layer=layer,
        )
        self._components: list[Component] = []
        self._scene: Scene | None = None
        self._layer: ObjectLayer | None = None
        self._deleting = False
        self._deleted = False
        self.scene_id: str | None = None
        self.name: str | None = None
        self.properties: dict[str, Any] = {}
        self.render_behind_parent = False
        self.speed = Vec2()
        self._collision_handlers: list[CollisionHandler] = []
        self._collision_stay_handlers: list[CollisionHandler] = []
        self._collision_end_handlers: list[CollisionHandler] = []
        self._tile_collision_handlers: list[TileCollisionHandler] = []
        self._tile_collision_stay_handlers: list[TileCollisionHandler] = []
        self._tile_collision_end_handlers: list[TileCollisionHandler] = []
        self._overlap_handlers: list[OverlapHandler] = []
        self._overlap_stay_handlers: list[OverlapHandler] = []
        self._overlap_end_handlers: list[OverlapHandler] = []
        self._tile_overlap_handlers: list[TileCollisionHandler] = []
        self._tile_overlap_stay_handlers: list[TileCollisionHandler] = []
        self._tile_overlap_end_handlers: list[TileCollisionHandler] = []

    def update(self, delta_time: float) -> None:
        """Move by speed in local pixels per second before component updates."""
        self.position += self.speed * delta_time

    def face_towards(
        self,
        target: Node | Vec2 | tuple[float, float],
        *,
        offset: float = 0,
        keep_upright: bool = True,
    ) -> None:
        """Aim at a world-space target and optionally mirror the sprite upright.

        The sprite is mirrored visually without changing this object's scale,
        collider, or child transforms.

        Args:
            target: Node or world-space point to face.
            offset: Rotation offset in degrees.
            keep_upright: Mirror the sprite vertically while aiming left.

        Raises:
            LookupError: If this object has no ``SpriteRenderer``.
        """
        from .components import SpriteRenderer

        direction = self.direction_to(target)
        self.look_at(target, offset=offset)
        renderer = self.require_component(SpriteRenderer)
        renderer.flip_y = keep_upright and direction.x < 0

    @property
    def components(self) -> tuple[Component, ...]:
        """Attached components as an immutable snapshot."""
        return tuple(self._components)

    @property
    def width(self) -> float:
        """Width of the collider, or zero if no collider is attached."""
        if self.collider:
            return self.collider.width
        else:
            return 0

    @width.setter
    def width(self, value):
        """Set collider width; raises if no collider is attached."""
        if self.collider:
            self.collider.width = value
        else:
            raise Exception("The GameObject has no collider component.")

    @property
    def height(self) -> float:
        """Height of the collider, or zero if no collider is attached."""
        if self.collider:
            return self.collider.height
        else:
            return 0

    @height.setter
    def height(self, value):
        """Set collider height; raises if no collider is attached."""
        if self.collider:
            self.collider.height = value
        else:
            raise Exception("The GameObject has not collider component.")

    def add_component(self, component: ComponentType) -> ComponentType:
        """Attach a component and any eligible automatic dependencies.

        Args:
            component (ComponentType): Component instance to attach.

        Returns:
            ComponentType: The attached component.

        Raises:
            ValueError: If ownership or component requirements are invalid.
        """
        if self.is_deleted:
            raise RuntimeError("cannot add a component to a deleted game object")
        added: list[Component] = []
        try:
            self._add_component_with_requirements(
                component,
                added,
                set(),
            )
        except Exception:
            self._rollback_components(added)
            raise
        return component

    def _add_component_with_requirements(
        self,
        component: Component,
        added: list[Component],
        resolving: set[type[Component]],
    ) -> None:
        from .components import (
            describe_component_requirement,
            missing_component_requirements,
        )

        if component.owner is not None:
            raise ValueError("component already belongs to a game object")
        component_type = type(component)
        if component_type in resolving:
            raise ValueError(
                f"component dependency cycle involving "
                f"{component_type.__name__}"
            )
        resolving.add(component_type)
        present_types = tuple(type(item) for item in self._components)
        missing = missing_component_requirements(
            component_type,
            present_types,
        )
        required = next(
            (
                requirement
                for requirement in missing
                if not requirement.auto_add
            ),
            None,
        )
        if required is not None:
            resolving.remove(component_type)
            raise ValueError(
                f"{component_type.__name__} requires "
                f"{describe_component_requirement(required)}"
            )
        for requirement in missing:
            for required_type in requirement.component_types:
                if self.get_component(required_type) is not None:
                    continue
                try:
                    dependency = required_type()
                except TypeError as error:
                    resolving.remove(component_type)
                    raise ValueError(
                        f"{component_type.__name__} cannot auto-add "
                        f"{required_type.__name__}; it needs constructor "
                        "arguments"
                    ) from error
                self._add_component_with_requirements(
                    dependency,
                    added,
                    resolving,
                )
        component._owner = self
        self._components.append(component)
        added.append(component)
        component.on_attach()
        if self._scene is not None and self._scene._components_ready_enabled:
            component._mark_ready()
        resolving.remove(component_type)

    def remove_component(self, component: Component) -> Component:
        """Detach a component that is not required by another component.

        Args:
            component (Component): Component instance to remove.

        Returns:
            Component: The detached component.

        Raises:
            ValueError: If it is not attached or its removal breaks dependencies.
        """
        from .components import (
            Area,
            describe_component_requirement,
            missing_component_requirements,
        )

        if component.owner is not self:
            raise ValueError("component does not belong to this game object")
        remaining = tuple(
            item for item in self._components if item is not component
        )
        remaining_types = tuple(type(item) for item in remaining)
        for dependent in remaining:
            if isinstance(dependent, Area) and dependent.uses_collider(component):
                raise ValueError("cannot remove collider selected by Area; remove or retarget Area first")
            missing = missing_component_requirements(
                type(dependent),
                remaining_types,
            )
            if missing:
                raise ValueError(
                    f"cannot remove {type(component).__name__}; "
                    f"{type(dependent).__name__} requires "
                    f"{describe_component_requirement(missing[0])}"
                )
        component.on_detach()
        self._components.remove(component)
        component._owner = None
        component._ready = False
        return component

    def validate_component_dependencies(self) -> None:
        """Raise ``ValueError`` if any attached component requirements are missing."""
        from .components import (
            describe_component_requirement,
            missing_component_requirements,
        )

        present_types = tuple(
            type(component) for component in self._components
        )
        for component in self._components:
            missing = missing_component_requirements(
                type(component),
                present_types,
            )
            if missing:
                raise ValueError(
                    f"{type(component).__name__} requires "
                    f"{describe_component_requirement(missing[0])}"
                )

    def _add_restored_components(
        self,
        components: Iterable[Component],
    ) -> None:
        from .components import missing_component_requirements

        restored = tuple(components)
        if any(component.owner is not None for component in restored):
            raise ValueError("restored component already has an owner")
        for component in restored:
            component._owner = self
            self._components.append(component)
        auto_added: list[Component] = []
        notified: list[Component] = []
        try:
            for component in restored:
                present_types = tuple(
                    type(item) for item in self._components
                )
                for requirement in missing_component_requirements(
                    type(component),
                    present_types,
                ):
                    if not requirement.auto_add:
                        continue
                    for required_type in requirement.component_types:
                        if self.get_component(required_type) is not None:
                            continue
                        try:
                            dependency = required_type()
                        except TypeError as error:
                            raise ValueError(
                                f"{type(component).__name__} cannot "
                                f"auto-add {required_type.__name__}; it "
                                "needs constructor arguments"
                            ) from error
                        self._add_component_with_requirements(
                            dependency,
                            auto_added,
                            {type(component)},
                        )
            self.validate_component_dependencies()
            for component in restored:
                notified.append(component)
                component.on_attach()
                if self._scene is not None and self._scene._components_ready_enabled:
                    component._mark_ready()
        except Exception:
            self._rollback_components(
                [*auto_added, *notified]
            )
            for component in restored:
                if component.owner is self:
                    self._components.remove(component)
                    component._owner = None
                    component._ready = False
            raise

    def _rollback_components(
        self,
        components: list[Component],
    ) -> None:
        for component in reversed(components):
            if component.owner is not self:
                continue
            try:
                component.on_detach()
            finally:
                self._components.remove(component)
                component._owner = None
                component._ready = False

    @property
    def scene(self) -> Scene | None:
        """Scene containing this object, if it is currently attached."""
        return self._scene

    def require_scene(self) -> Scene:
        """Return the containing scene or raise ``LookupError``."""
        if self.scene is None:
            raise LookupError(f"GameObject {self.name!r} does not belong to a scene")
        return self.scene

    def require_parent(self) -> GameObject:
        """Return the game-object parent or raise ``LookupError``."""
        if not isinstance(self.parent, GameObject):
            raise LookupError(f"GameObject {self.name!r} does not have a GameObject parent")
        return self.parent

    @property
    def layer(self) -> ObjectLayer | None:
        """Object layer containing this object, if attached."""
        return self._layer

    def require_layer(self) -> ObjectLayer:
        """Return the containing object layer or raise ``LookupError``."""
        if self.layer is None:
            raise LookupError(f"GameObject {self.name!r} does not belong to an object layer")
        return self.layer

    def add_child(self, child: Node) -> Node:
        """Add a child and prepare its subtree if this object is in a scene."""
        if self.is_deleted:
            raise RuntimeError("cannot add a child to a deleted game object")
        if isinstance(child, GameObject) and child.is_deleted:
            raise RuntimeError("cannot add a deleted game object as a child")
        if (
            isinstance(child, GameObject)
            and self.layer is not None
            and child.layer is not None
            and child.layer is not self.layer
        ):
            raise ValueError("parent and child must belong to the same scene layer")
        result = super().add_child(child)
        if self._scene is not None:
            self._scene._prepare_subtree(child, self.layer)
            self._scene.physics.refresh()
        return result

    def remove_child(self, child: Node) -> Node:
        """Remove a direct child while keeping scene objects alive.

        A child in a scene becomes a top-level object in the same layer.
        Outside a scene, the child is detached from the hierarchy.

        Raises:
            ValueError: If ``child`` is not a direct child.
        """
        if child.parent is not self:
            raise ValueError("node is not a child of this parent")
        scene = self.scene
        layer = self.layer
        if scene is None or layer is None:
            return super().remove_child(child)
        world_position = child.world_position
        world_rotation = child.world_rotation
        world_scale = child.world_scale
        layer.root.add_child(child)
        child.world_position = world_position
        child.world_rotation = world_rotation
        parent_scale_x, parent_scale_y = layer.root.world_scale
        if parent_scale_x == 0 or parent_scale_y == 0:
            raise ValueError(
                "cannot promote a child through a layer root with zero scale"
            )
        child.scale = (
            world_scale[0] / parent_scale_x,
            world_scale[1] / parent_scale_y,
        )
        scene._prepare_subtree(child, layer)
        scene.physics.refresh()
        scene.physics.refresh_contacts()
        return child

    def get_component(
        self,
        component_type: type[ComponentType],
    ) -> ComponentType | None:
        """Return the first attached component matching ``component_type``."""
        return next(
            (
                component
                for component in self._components
                if isinstance(component, component_type)
            ),
            None,
        )

    def require_component(
        self,
        component_type: type[ComponentType],
    ) -> ComponentType:
        """Return a matching component or raise ``LookupError``."""
        component = self.get_component(component_type)
        if component is None:
            object_name = (
                f" {self.name!r}"
                if self.name is not None
                else ""
            )
            raise LookupError(
                f"{type(self).__name__}{object_name} does not have "
                f"component {component_type.__name__}"
            )
        return component

    @property
    def renderers(self) -> tuple[Renderer, ...]:
        """Attached renderer components as an immutable snapshot."""
        from .components import Renderer

        return tuple(
            component
            for component in self._components
            if isinstance(component, Renderer)
        )

    @property
    def shader(self) -> Shader | None:
        """Shader of the first renderer, or ``None`` if there are no renderers."""
        renderers = self.renderers
        if not renderers:
            return None
        return renderers[0].shader

    @shader.setter
    def shader(self, value: Shader | None) -> None:
        """Set the shader on every renderer.

        Raises:
            RuntimeError: If no renderer is attached.
        """
        renderers = self.renderers
        if not renderers:
            raise RuntimeError("game object has no renderer")
        for renderer in renderers:
            renderer.shader = value

    @property
    def collider(self) -> Collider | None:
        """Attached collider, if present."""
        from .components import Collider

        return self.get_component(Collider)

    @property
    def body(self) -> PhysicsBody | None:
        """Attached physics body, if present."""
        from .physics import PhysicsBody

        return self.get_component(PhysicsBody)

    def teleport(
        self,
        x: float,
        y: float,
        angle: float | None = None,
    ) -> None:
        """Move immediately in world space without render interpolation.

        An attached physics body is moved through its teleport API so its
        simulation pose remains synchronized. Without a body, the object world
        transform is updated directly.

        Args:
            x: New world-space horizontal coordinate.
            y: New world-space vertical coordinate.
            angle: Optional clockwise world rotation in degrees.
        """
        body = self.body
        if body is not None:
            body.teleport(x, y, angle)
            return
        self.world_position = x, y
        if angle is not None:
            self.world_rotation = angle
        self.reset_interpolation()

    def _world_bounds(self) -> tuple[float, float, float, float]:
        from .components import Collider, TileRenderer

        geometries = [component.geometry for component in self.components if isinstance(component, Collider)]
        tile_points = [
            point for renderer in self.renderers if isinstance(renderer, TileRenderer)
            for quad in renderer.world_quads() for point in quad
        ]
        if tile_points:
            xs, ys = zip(*tile_points)
            return min(xs), min(ys), max(xs), max(ys)
        if not geometries:
            raise RuntimeError("game object needs a collider to determine its edges")
        bounds = []
        for geometry in geometries:
            if isinstance(geometry, CircleGeometry):
                bounds.append((geometry.x - geometry.radius, geometry.y - geometry.radius,
                               geometry.x + geometry.radius, geometry.y + geometry.radius))
            elif isinstance(geometry, BoxGeometry):
                xs, ys = zip(*geometry.points)
                bounds.append((min(xs), min(ys), max(xs), max(ys)))
            else:
                raise TypeError(f"unsupported collider geometry: {type(geometry).__name__}")
        return min(b[0] for b in bounds), min(b[1] for b in bounds), max(b[2] for b in bounds), max(b[3] for b in bounds)

    def _move_world(self, delta_x: float, delta_y: float) -> None:
        if self.parent is None:
            self.x += delta_x
            self.y += delta_y
            return

        scale_x, scale_y = self.parent.world_scale
        if scale_x == 0 or scale_y == 0:
            raise ValueError("cannot move through a parent with zero world scale")
        rotation = radians(self.parent.world_rotation)
        cosine = cos(rotation)
        sine = sin(rotation)
        self.x += (delta_x * cosine - delta_y * sine) / scale_x
        self.y += (delta_x * sine + delta_y * cosine) / scale_y

    @property
    def left(self) -> float:
        """Left world-space edge of collider or tile-renderer bounds."""
        return self._world_bounds()[0]

    @left.setter
    def left(self, value: float) -> None:
        """Move the object so its left world-space edge equals ``value``."""
        self._move_world(value - self.left, 0)

    @property
    def bottom(self) -> float:
        """Bottom world-space edge of collider or tile-renderer bounds."""
        return self._world_bounds()[1]

    @bottom.setter
    def bottom(self, value: float) -> None:
        """Move the object so its bottom world-space edge equals ``value``."""
        self._move_world(0, value - self.bottom)

    @property
    def right(self) -> float:
        """Right world-space edge of collider or tile-renderer bounds."""
        return self._world_bounds()[2]

    @right.setter
    def right(self, value: float) -> None:
        """Move the object so its right world-space edge equals ``value``."""
        self._move_world(value - self.right, 0)

    @property
    def top(self) -> float:
        """Top world-space edge of collider or tile-renderer bounds."""
        return self._world_bounds()[3]

    @top.setter
    def top(self, value: float) -> None:
        """Move the object so its top world-space edge equals ``value``."""
        self._move_world(0, value - self.top)

    def contains_point(self, x: float, y: float) -> bool:
        """Return whether the object's collider contains a world-space point."""
        collider = self.collider
        return collider is not None and collider.contains_point(x, y)

    def overlaps(self, other: GameObject) -> bool:
        """Return whether this object's first collider overlaps another's."""
        if not isinstance(other, GameObject):
            raise TypeError("other must be a GameObject")
        first = self.collider
        second = other.collider
        return (
            self is not other
            and first is not None
            and second is not None
            and first.overlaps(second)
        )

    def collides_with(self, other: GameObject) -> CollisionContact | None:
        """Return current solid contact geometry, or None, using the first colliders."""
        from .components import ColliderMode
        from .physics import CollisionContact

        if not isinstance(other, GameObject):
            raise TypeError("other must be a GameObject")
        first = self.collider
        second = other.collider
        if (
            self is other
            or first is None
            or second is None
            or not first.enabled
            or not second.enabled
            or first.mode is not ColliderMode.SOLID
            or second.mode is not ColliderMode.SOLID
        ):
            return None
        return CollisionContact._between_colliders(first, second)

    def collides_with_any(self, others: Iterable[GameObject]) -> bool:
        """Return whether any supplied object has solid contact with this one."""
        return any(self.collides_with(other) for other in others)

    def collisions_with(
        self, others: Iterable[GameObject]
    ) -> tuple[GameObject, ...]:
        """Return supplied objects currently in solid contact with this one."""
        return tuple(other for other in others if self.collides_with(other))

    def on_collision(self, handler: CollisionHandler) -> CollisionHandler:
        """Register a callback for the start of solid object contact."""
        self._collision_handlers.append(handler)
        return handler

    def on_collision_stay(
        self, handler: CollisionHandler
    ) -> CollisionHandler:
        """Register a callback for each continuing solid object contact."""
        self._collision_stay_handlers.append(handler)
        return handler

    def on_collision_end(
        self, handler: CollisionHandler
    ) -> CollisionHandler:
        """Register a callback when solid object contact ends."""
        self._collision_end_handlers.append(handler)
        return handler

    def on_tile_collision(
        self, handler: TileCollisionHandler
    ) -> TileCollisionHandler:
        """Register a callback for the start of solid tile contact."""
        self._tile_collision_handlers.append(handler)
        return handler

    def on_tile_collision_stay(
        self, handler: TileCollisionHandler
    ) -> TileCollisionHandler:
        """Register a callback for each continuing solid tile contact."""
        self._tile_collision_stay_handlers.append(handler)
        return handler

    def on_tile_collision_end(
        self, handler: TileCollisionHandler
    ) -> TileCollisionHandler:
        """Register a callback when solid tile contact ends."""
        self._tile_collision_end_handlers.append(handler)
        return handler

    def remove_collision_handler(self, handler: CollisionHandler) -> None:
        """Remove a registered collision, collision-stay, and collision-end callback."""
        for handlers in (
            self._collision_handlers,
            self._collision_stay_handlers,
            self._collision_end_handlers,
        ):
            if handler in handlers:
                handlers.remove(handler)

    def remove_tile_collision_handler(
        self, handler: TileCollisionHandler
    ) -> None:
        """Remove a registered tile-collision callback."""
        for handlers in (
            self._tile_collision_handlers,
            self._tile_collision_stay_handlers,
            self._tile_collision_end_handlers,
        ):
            if handler in handlers:
                handlers.remove(handler)

    def on_overlap(self, handler: OverlapHandler) -> OverlapHandler:
        """Register a callback for the start of object overlap."""
        self._overlap_handlers.append(handler)
        return handler

    def on_overlap_stay(
        self,
        handler: OverlapHandler,
    ) -> OverlapHandler:
        """Register a callback for each continuing object overlap."""
        self._overlap_stay_handlers.append(handler)
        return handler

    def on_overlap_end(
        self,
        handler: OverlapHandler,
    ) -> OverlapHandler:
        """Register a callback when object overlap ends."""
        self._overlap_end_handlers.append(handler)
        return handler

    def on_tile_overlap(
        self, handler: TileCollisionHandler
    ) -> TileCollisionHandler:
        """Register a callback for the start of tile overlap."""
        self._tile_overlap_handlers.append(handler)
        return handler

    def on_tile_overlap_stay(
        self, handler: TileCollisionHandler
    ) -> TileCollisionHandler:
        """Register a callback for each continuing tile overlap."""
        self._tile_overlap_stay_handlers.append(handler)
        return handler

    def on_tile_overlap_end(
        self, handler: TileCollisionHandler
    ) -> TileCollisionHandler:
        """Register a callback when tile overlap ends."""
        self._tile_overlap_end_handlers.append(handler)
        return handler

    def remove_overlap_handler(self, handler: OverlapHandler) -> None:
        """Remove a registered overlap, overlap-stay, and overlap-end callback."""
        for handlers in (
            self._overlap_handlers,
            self._overlap_stay_handlers,
            self._overlap_end_handlers,
        ):
            if handler in handlers:
                handlers.remove(handler)

    def remove_tile_overlap_handler(
        self, handler: TileCollisionHandler
    ) -> None:
        """Remove a registered tile-overlap callback."""
        for handlers in (
            self._tile_overlap_handlers,
            self._tile_overlap_stay_handlers,
            self._tile_overlap_end_handlers,
        ):
            if handler in handlers:
                handlers.remove(handler)

    def _dispatch_collision(
        self,
        handlers: Iterable[Callable[[ContactEvent], None]],
        contact: ContactEvent,
    ) -> None:
        from .components import Script
        from .physics import CollisionContact

        other = contact.other if isinstance(contact, CollisionContact) else contact
        scene = self.scene
        scripts = tuple(component for component in self.components if isinstance(component, Script))
        for handler in tuple(handlers):
            if self._deleting or self.scene is None or other.scene is not self.scene:
                break
            handler(contact)
        event = next((
            name for registered, name in (
                (self._collision_handlers, "on_collision"),
                (self._collision_stay_handlers, "on_collision_stay"),
                (self._collision_end_handlers, "on_collision_end"),
                (self._overlap_handlers, "on_overlap"),
                (self._overlap_stay_handlers, "on_overlap_stay"),
                (self._overlap_end_handlers, "on_overlap_end"),
            ) if handlers is registered
        ), None)
        if event is not None:
            for script in scripts:
                if self._deleting or self.scene is not scene or scene is None or other.scene is not scene:
                    break
                if self.enabled_in_hierarchy and script.owner is self and script.enabled and script._ready:
                    getattr(script, event)(contact)

    def _dispatch_tile_collision(
        self,
        handlers: Iterable[TileCollisionHandler],
        contact: TileContact,
    ) -> None:
        from .components import Script

        scene = self.scene
        scripts = tuple(component for component in self.components if isinstance(component, Script))
        for handler in tuple(handlers):
            if self._deleting or self.scene is None:
                break
            handler(contact)
        event = next((
            name for registered, name in (
                (self._tile_collision_handlers, "on_tile_collision"),
                (self._tile_collision_stay_handlers, "on_tile_collision_stay"),
                (self._tile_collision_end_handlers, "on_tile_collision_end"),
                (self._tile_overlap_handlers, "on_tile_overlap"),
                (self._tile_overlap_stay_handlers, "on_tile_overlap_stay"),
                (self._tile_overlap_end_handlers, "on_tile_overlap_end"),
            ) if handlers is registered
        ), None)
        if event is not None:
            for script in scripts:
                if self._deleting or self.scene is not scene or scene is None:
                    break
                if self.enabled_in_hierarchy and script.owner is self and script.enabled and script._ready:
                    getattr(script, event)(contact)

    def delete(self) -> None:
        """Permanently delete this subtree and release owned resources."""
        if self._deleting or self._deleted:
            return
        if self._scene is not None:
            self._scene.delete(self)
            return
        nodes = self.walk()
        self.parent = None
        errors: list[Exception] = []
        for node in reversed(nodes):
            if isinstance(node, GameObject):
                try:
                    node._delete_resources()
                except Exception as error:
                    errors.append(error)
            node.parent = None
        if errors:
            raise ExceptionGroup("GameObject subtree cleanup failed", errors)

    def _delete_resources(self) -> None:
        from .components import BehaviourRunner

        if self._deleting or self._deleted:
            return
        self._deleting = True
        self._deleted = True
        errors: list[Exception] = []
        runner = self.get_component(BehaviourRunner)
        if runner is not None:
            try:
                runner.cancel()
            except Exception as error:
                errors.append(error)
        drawables = tuple(drawable for renderer in self.renderers for drawable in renderer.drawables)
        for component in reversed(self.components):
            try:
                component.on_detach()
            except Exception as error:
                errors.append(error)
            finally:
                component._owner = None
                component._ready = False
        for drawable in drawables:
            try:
                drawable.delete()
            except Exception as error:
                errors.append(error)
        self._components.clear()
        self._scene = None
        self._layer = None
        self._collision_handlers.clear()
        self._collision_stay_handlers.clear()
        self._collision_end_handlers.clear()
        self._overlap_handlers.clear()
        self._overlap_stay_handlers.clear()
        self._overlap_end_handlers.clear()
        self._tile_collision_handlers.clear()
        self._tile_collision_stay_handlers.clear()
        self._tile_collision_end_handlers.clear()
        self._tile_overlap_handlers.clear()
        self._tile_overlap_stay_handlers.clear()
        self._tile_overlap_end_handlers.clear()
        self._deleting = False
        if errors:
            raise ExceptionGroup("GameObject cleanup failed", errors)

    @property
    def is_deleted(self) -> bool:
        """Whether this object has been permanently deleted."""
        return self._deleted

    @property
    def behaviour(self) -> Behaviour | None:
        """Current behaviour assigned to the attached behaviour runner."""
        from .components import BehaviourRunner

        runner = self.get_component(BehaviourRunner)
        return runner.behaviour if runner is not None else None

    @behaviour.setter
    def behaviour(self, value: Behaviour | None) -> None:
        """Set the behaviour, creating a runner if one is not attached."""
        from .components import BehaviourRunner

        runner = self.get_component(BehaviourRunner)
        if runner is None:
            runner = self.add_component(BehaviourRunner())
        runner.behaviour = value

    def animate(self) -> AnimationBuilder:
        """Create a fluent animation builder targeting this game object."""
        return AnimationBuilder(self)

    @property
    def is_animating(self) -> bool:
        """Whether an object animation is active, including while paused."""
        from .components import BehaviourRunner

        runner = self.get_component(BehaviourRunner)
        return runner is not None and runner.is_animating

    def cancel_behaviour(self) -> None:
        """Cancel the object's active behaviour, if any."""
        from .components import BehaviourRunner

        runner = self.get_component(BehaviourRunner)
        if runner is not None:
            runner.cancel()

    def pause_behaviour(self) -> None:
        """Pause the object's active behaviour, if any."""
        from .components import BehaviourRunner

        runner = self.get_component(BehaviourRunner)
        if runner is not None:
            runner.pause()

    def resume_behaviour(self) -> None:
        """Resume the object's paused behaviour, if any."""
        from .components import BehaviourRunner

        runner = self.get_component(BehaviourRunner)
        if runner is not None:
            runner.resume()

    def restart_behaviour(self) -> None:
        """Restart the object's active behaviour, if any."""
        from .components import BehaviourRunner

        runner = self.get_component(BehaviourRunner)
        if runner is not None:
            runner.restart()

    def add_physics(
        self,
        body_type: BodyType | None = None,
        *,
        density: float = 1.0,
        friction: float = 0.7,
        elasticity: float = 0.0,
        sensor: bool = False,
        collision_type: int = 0,
        velocity: Vec2 | tuple[float, float] = (0, 0),
        angular_velocity: float = 0,
        categories: int = 0xFFFFFFFF,
        mask: int = 0xFFFFFFFF,
        group: int = 0,
    ) -> PhysicsBody:
        """Create a physics body and infer a collider from an attached renderer.

        Args:
            body_type (BodyType | None): Body type; defaults to a dynamic body.
            density (float): Shape density.
            friction (float): Shape friction.
            elasticity (float): Shape restitution.
            sensor (bool): Whether the collider is a sensor.
            collision_type (int): Collision-handler type identifier.
            velocity (Vec2 | tuple[float, float]): Initial linear velocity.
            angular_velocity (float): Initial angular velocity.
            categories (int): Collision category bit mask.
            mask (int): Collision mask bit field.
            group (int): Collision group identifier.

        Returns:
            PhysicsBody: The newly attached physics body.

        Raises:
            ValueError: If a body already exists or no suitable shape is available.
        """
        from .components import (
            Collider,
            CircleRenderer,
            RectRenderer,
            SpriteRenderer,
        )
        from .physics import (
            BodyType,
            PhysicsBody,
        )

        if self.get_component(PhysicsBody) is not None:
            raise ValueError("game object already has a physics body")

        shape_options = {
            "density": density,
            "friction": friction,
            "elasticity": elasticity,
            "sensor": sensor,
            "collision_type": collision_type,
        }
        collider = self.get_component(Collider)
        if collider is None:
            circle = self.get_component(CircleRenderer)
            rectangle = self.get_component(RectRenderer)
            sprite = self.get_component(SpriteRenderer)
            if circle is not None:
                collider = self.add_component(
                    Collider(shape="circle", radius=circle.radius, **shape_options)
                )
            elif rectangle is not None:
                collider = self.add_component(
                    Collider(
                        shape="box",
                        width=rectangle.width,
                        height=rectangle.height,
                        **shape_options,
                    )
                )
            elif sprite is not None:
                collider = self.add_component(
                    Collider(
                        shape="box",
                        width=sprite.drawable.width,
                        height=sprite.drawable.height,
                        **shape_options,
                    )
                )
            else:
                raise ValueError(
                    "automatic physics needs a circle or box renderer/collider"
                )
        else:
            collider.density = density
            collider.friction = friction
            collider.elasticity = elasticity
            collider.sensor = sensor
            collider.collision_type = collision_type

        body = self.add_component(
            PhysicsBody(
                BodyType.DYNAMIC if body_type is None else body_type,
                velocity=velocity,
                angular_velocity=angular_velocity,
                categories=categories,
                mask=mask,
                group=group,
            )
        )
        if self.scene is not None:
            self.scene.physics.refresh()
        return body

    def add_top_down_motor(
        self,
        speed: float = 200,
        *,
        acceleration: float = 1200,
        deceleration: float = 1600,
    ) -> TopDownMotor:
        """Attach a top-down movement motor.

        Args:
            speed (float): Maximum movement speed.
            acceleration (float): Ground movement acceleration.
            deceleration (float): Movement deceleration.

        Returns:
            TopDownMotor: The attached motor.

        Raises:
            ValueError: If a movement motor is already attached.
        """
        from .scripts import PlatformerMotor, TopDownMotor

        if (
            self.get_component(TopDownMotor) is not None
            or self.get_component(PlatformerMotor) is not None
        ):
            raise ValueError("game object already has a movement motor")
        self._ensure_renderer_collider()
        return self.add_component(
            TopDownMotor(
                speed,
                acceleration=acceleration,
                deceleration=deceleration,
            )
        )

    def add_platformer_motor(
        self,
        speed: float = 240,
        *,
        acceleration: float = 1800,
        air_acceleration: float = 900,
        deceleration: float = 2200,
        jump_speed: float = 520,
        max_fall_speed: float = 900,
        gravity_scale: float = 1.0,
        max_slope: float = 50,
        ground_snap_distance: float = 8,
        coyote_time: float = 0.1,
        jump_buffer_time: float = 0.1,
        allow_double_jump: bool = False,
    ) -> PlatformerMotor:
        """Attach a platformer movement motor with the requested tuning.

        Args:
            speed (float): Maximum horizontal speed.
            acceleration (float): Ground acceleration.
            air_acceleration (float): Acceleration while airborne.
            deceleration (float): Horizontal deceleration.
            jump_speed (float): Initial jump speed.
            max_fall_speed (float): Terminal downward speed.
            gravity_scale (float): Multiplier applied to gravity.
            max_slope (float): Maximum ground slope in degrees.
            ground_snap_distance (float): Maximum distance used to snap to ground.
            coyote_time (float): Seconds after leaving ground in which jumping is allowed.
            jump_buffer_time (float): Seconds a jump request is retained before landing.
            allow_double_jump (bool): Whether one additional airborne jump is allowed.

        Returns:
            PlatformerMotor: The attached motor.

        Raises:
            ValueError: If a movement motor is already attached.
        """
        from .scripts import PlatformerMotor, TopDownMotor

        if (
            self.get_component(TopDownMotor) is not None
            or self.get_component(PlatformerMotor) is not None
        ):
            raise ValueError("game object already has a movement motor")
        self._ensure_renderer_collider()
        return self.add_component(
            PlatformerMotor(
                speed,
                acceleration=acceleration,
                air_acceleration=air_acceleration,
                deceleration=deceleration,
                jump_speed=jump_speed,
                max_fall_speed=max_fall_speed,
                gravity_scale=gravity_scale,
                max_slope=max_slope,
                ground_snap_distance=ground_snap_distance,
                coyote_time=coyote_time,
                jump_buffer_time=jump_buffer_time,
                allow_double_jump=allow_double_jump,
            )
        )

    def _ensure_renderer_collider(self) -> None:
        """Add a collider inferred from this object's renderer when absent."""
        from .components import Collider, CircleRenderer, RectRenderer, SpriteRenderer

        if self.get_component(Collider) is not None:
            return
        circle = self.get_component(CircleRenderer)
        rectangle = self.get_component(RectRenderer)
        sprite = self.get_component(SpriteRenderer)
        if circle is not None:
            self.add_component(Collider(shape="circle", radius=circle.radius))
        elif rectangle is not None:
            self.add_component(
                Collider(
                    shape="box",
                    width=rectangle.width,
                    height=rectangle.height,
                )
            )
        elif sprite is not None:
            self.add_component(
                Collider(
                    shape="box",
                    width=sprite.drawable.width,
                    height=sprite.drawable.height,
                )
            )
        else:
            raise ValueError(
                "automatic movement needs a circle or box renderer/collider"
            )

    def set_state(self, state: str) -> None:
        """Set the sprite animator's state.

        Args:
            state (str): Animation state name.

        Raises:
            AttributeError: If this object has no sprite animator.
        """
        from .components import SpriteAnimator

        animator = self.get_component(SpriteAnimator)
        if animator is None:
            raise AttributeError("game object has no animator")
        animator.state = state
