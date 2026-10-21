from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from math import atan2, ceil, cos, degrees, hypot, isclose, isfinite, radians
from typing import TYPE_CHECKING, TypeAlias
from warnings import warn

import pymunk
from .inspector import inspector_field
from .math import Vec2

from .components import (
    Collider,
    ColliderMode,
    Component,
)

if TYPE_CHECKING:
    from .nodes import GameObject, Node
    from .tilemap import TileCollision, TileFlags, TileLayer
    from .tilemap import TileContact


ObjectProvider: TypeAlias = Callable[[], Iterable["Node"]]
TileProvider: TypeAlias = Callable[[], Iterable["TileLayer"]]
PhysicsTarget: TypeAlias = "GameObject | TileLayer"
CollisionPair: TypeAlias = "tuple[GameObject, PhysicsTarget]"
TileCollisionPair: TypeAlias = "tuple[GameObject, TileContact]"


@dataclass(frozen=True)
class CollisionContact:
    """World-space collision geometry oriented from this object toward the other.

    Attributes:
        other: Game object at the other side of the contact.
        collider: Collider belonging to the receiving object.
        other_collider: Collider belonging to ``other``.
        normal: Unit contact normal pointing toward ``other``.
        points: Contact points in world-space coordinates.
        penetration: Non-negative penetration depth in pixels.
    """

    other: GameObject
    collider: Collider
    other_collider: Collider
    normal: tuple[float, float]
    points: tuple[tuple[float, float], ...]
    penetration: float

    @property
    def angle(self) -> float:
        """Normal angle in degrees, clockwise from world +X."""
        return -degrees(atan2(self.normal[1], self.normal[0]))

    @classmethod
    def _from_point_set(
        cls,
        other: GameObject,
        collider: Collider,
        other_collider: Collider,
        contact_set: pymunk.ContactPointSet,
    ) -> CollisionContact:
        return cls(
            other,
            collider,
            other_collider,
            (contact_set.normal.x, contact_set.normal.y),
            tuple(
                ((p.point_a.x + p.point_b.x) / 2, (p.point_a.y + p.point_b.y) / 2)
                for p in contact_set.points
            ),
            # Pymunk's distance sign can depend on shape order; project instead.
            max((
                max(0.0, (p.point_a - p.point_b).dot(contact_set.normal))
                for p in contact_set.points
            ), default=0.0),
        )

    @classmethod
    def _between_colliders(
        cls, collider: Collider, other: Collider,
    ) -> CollisionContact | None:
        shapes: list[pymunk.Shape] = []
        for component in (collider, other):
            owner = component.owner
            if owner is None:
                raise RuntimeError("collider is not attached")
            body = pymunk.Body(body_type=pymunk.Body.STATIC)
            body.position = tuple(owner.world_position)
            body.angle = -radians(owner.world_rotation)
            shape = component._create_shape(body)
            shape.cache_bb()
            shapes.append(shape)
        contact_set = shapes[0].shapes_collide(shapes[1])
        owner = other.owner
        if owner is None:
            raise RuntimeError("collider is not attached")
        if not contact_set.points:
            first, second = shapes
            if isinstance(first, pymunk.Circle) and isinstance(second, pymunk.Circle):
                ax, ay = collider._world_center()
                bx, by = other._world_center()
                distance = hypot(bx - ax, by - ay)
                # Pymunk omits circle-circle contacts at exact tangency.
                if distance == first.radius + second.radius:
                    normal = ((bx - ax) / distance, (by - ay) / distance) if distance else (1.0, 0.0)
                    point = (ax + normal[0] * first.radius, ay + normal[1] * first.radius)
                    return cls(owner, collider, other, normal, (point,), 0.0)
            return None
        return cls._from_point_set(owner, collider, other, contact_set)


@dataclass(frozen=True)
class RaycastHit:
    """World-space segment hit.

    Attributes:
        target: Game object or tile layer hit.
        point: Hit point in world coordinates.
        normal: Surface normal at the hit.
        fraction: Hit position along the segment, from 0 to 1.
        distance: Distance from segment start to the hit.
    """

    target: PhysicsTarget
    point: tuple[float, float]
    normal: tuple[float, float]
    fraction: float
    distance: float


@dataclass(frozen=True)
class PointQueryHit:
    """Nearest surface information for a world-space point.

    Attributes:
        target: Game object or tile layer nearest the query point.
        point: Closest surface point in world coordinates.
        distance: Signed distance reported by the physics query.
        gradient: Surface gradient at the closest point.
    """

    target: PhysicsTarget
    point: tuple[float, float]
    distance: float
    gradient: tuple[float, float]


@dataclass(frozen=True)
class OverlapHit:
    """Contact geometry from a query shape toward an overlapping target.

    Attributes:
        target: Game object or tile layer overlapped.
        normal: Contact normal from the query shape toward the target.
        points: Contact points in world coordinates.
        penetration: Non-negative penetration depth in pixels.
    """

    target: PhysicsTarget
    normal: tuple[float, float]
    points: tuple[tuple[float, float], ...]
    penetration: float


class BodyType(Enum):
    """Physics integration mode for a body.

    Attributes:
        DYNAMIC: Responds to forces, impulses, and gravity.
        KINEMATIC: Moves by assigned velocity without force integration.
        STATIC: Fixed body that does not move through simulation.
    """

    DYNAMIC = pymunk.Body.DYNAMIC
    KINEMATIC = pymunk.Body.KINEMATIC
    STATIC = pymunk.Body.STATIC


class PhysicsTransformWarning(RuntimeWarning):
    """Warning emitted when a dynamic body's owner transform is changed directly."""


class PhysicsBody(Component):
    """Rigid body attached to a game object.

    A dynamic body owns its object's runtime world position and rotation.
    Changing the object transform directly after physics initialization emits
    :class:`PhysicsTransformWarning` and the change is discarded. Use
    :attr:`velocity`, :meth:`apply_force`, :meth:`apply_impulse`, a movement
    motor, or :meth:`teleport` instead. Kinematic and static bodies continue to
    follow direct object-transform changes. A dynamic body with
    :attr:`fixed_rotation` also follows direct rotation changes.

    Attributes:
        body_type: Dynamic, kinematic, or static simulation mode.
        velocity: Linear velocity in pixels per second.
        angular_velocity: Clockwise angular velocity in degrees per second.
        gravity_scale: Multiplier applied to world gravity.
        fixed_rotation: Whether the body is prevented from rotating.
        categories: Default collision category bitmask for inheriting colliders.
        mask: Default collision mask bitmask for inheriting colliders.
        group: Nonzero shared group suppresses collision between matching shapes.
    """

    __inspector_fields__ = {
        "body_type": inspector_field("dynamic", choices=("dynamic", "kinematic", "static"), tooltip="Dynamic responds to forces; Kinematic moves by velocity; Static stays fixed."),
        "velocity": inspector_field(Vec2(), tooltip="Linear velocity in pixels per second."),
        "angular_velocity": inspector_field(0.0, tooltip="Clockwise angular velocity in degrees per second."),
        "gravity_scale": inspector_field(1.0, minimum=0, tooltip="Multiplier applied to the scene's gravity."),
        "fixed_rotation": inspector_field(False, tooltip="Prevent physics from rotating the body."),
        "categories": inspector_field(0xFFFFFFFF, widget="mask", group="Collision Filtering", group_expanded=False, tooltip="Shared collision categories for colliders without a filtering override."),
        "mask": inspector_field(0xFFFFFFFF, widget="mask", group="Collision Filtering", group_expanded=False, tooltip="Categories the body's inheriting colliders can interact with."),
        "group": inspector_field(0, group="Collision Filtering", group_expanded=False, minimum=0, tooltip="Shapes with the same nonzero group do not collide."),
    }
    def __init__(
        self,
        body_type: BodyType = BodyType.DYNAMIC,
        *,
        velocity: Vec2 | tuple[float, float] = (0, 0),
        angular_velocity: float = 0,
        gravity_scale: float = 1.0,
        fixed_rotation: bool = False,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        group: int = 0,
        enabled: bool = True,
    ) -> None:
        """Create a rigid body.

        Args:
            body_type: Simulation mode.
            velocity: Initial linear velocity as ``Vec2`` or an ``(x, y)`` pair.
            angular_velocity: Initial clockwise angular velocity in degrees per
                second.
            gravity_scale: Non-negative multiplier for world gravity.
            fixed_rotation: Whether physics rotation is disabled.
            categories: Default category bitmask.
            mask: Default collision mask bitmask.
            group: Default collision group.
            enabled: Whether this component participates in the world.

        Raises:
            ValueError: If ``gravity_scale`` is negative.
        """
        super().__init__(enabled=enabled)
        if gravity_scale < 0:
            raise ValueError("gravity_scale must not be negative")
        self.body_type = body_type
        self._body = pymunk.Body(body_type=body_type.value)
        self.gravity_scale = gravity_scale
        self.fixed_rotation = fixed_rotation
        self.categories = categories
        self.mask = mask
        self.group = group
        self._body.velocity_func = self._update_velocity
        self.velocity = velocity
        self.angular_velocity = angular_velocity
        self._initialized = False

    @property
    def velocity(self) -> Vec2:
        """Linear velocity in pixels per second."""
        return Vec2(self._body.velocity.x, self._body.velocity.y)

    @velocity.setter
    def velocity(self, value: Vec2 | tuple[float, float]) -> None:
        """Set linear velocity from a vector or ``(x, y)`` pair.

        Args:
            value: New linear velocity in pixels per second.
        """
        self._body.velocity = (value[0], value[1])

    @property
    def angular_velocity(self) -> float:
        """Clockwise angular velocity in degrees per second."""
        return -degrees(self._body.angular_velocity)

    @angular_velocity.setter
    def angular_velocity(self, value: float) -> None:
        """Set clockwise angular velocity in degrees per second.

        Args:
            value: New angular velocity in degrees per second.
        """
        self._body.angular_velocity = -radians(value)

    def apply_force(
        self,
        force: tuple[float, float],
        point: tuple[float, float] | None = None,
    ) -> None:
        """Apply a force at a world-space point.

        Args:
            force: Force vector.
            point: World-space application point; defaults to the body center.
        """
        self._body.apply_force_at_world_point(
            force, self._body.position if point is None else point
        )

    def apply_impulse(
        self,
        impulse: tuple[float, float],
        point: tuple[float, float] | None = None,
    ) -> None:
        """Apply an impulse at a world-space point.

        Args:
            impulse: Impulse vector.
            point: World-space application point; defaults to the body center.
        """
        self._body.apply_impulse_at_world_point(
            impulse, self._body.position if point is None else point
        )

    def teleport(self, x: float, y: float, angle: float | None = None) -> None:
        """Move the body immediately and reset owner interpolation.

        Args:
            x: New world-space horizontal coordinate.
            y: New world-space vertical coordinate.
            angle: Optional clockwise world rotation in degrees.
        """
        if self.owner is not None:
            self.owner.world_position = x, y
            if angle is not None:
                self.owner.world_rotation = angle
            self.owner.reset_interpolation()
        self._body.position = x, y
        if angle is not None:
            self._body.angle = -radians(angle)
        self._body.activate()

    def _update_velocity(
        self,
        body: pymunk.Body,
        gravity: tuple[float, float],
        damping: float,
        delta_time: float,
    ) -> None:
        scaled_gravity = (
            gravity[0] * self.gravity_scale,
            gravity[1] * self.gravity_scale,
        )
        pymunk.Body.update_velocity(
            body,
            scaled_gravity,
            damping,
            delta_time,
        )
        if self.fixed_rotation:
            body.angular_velocity = 0


class PhysicsWorld:
    """Pymunk-backed simulation world for colliders, bodies, and tile layers.

    Attributes:
        gravity: World gravity vector in pixels per second squared.
        damping: Velocity damping factor used by the solver.
        max_step: Maximum internal simulation step in seconds.
        collision_contacts: Contact details for the selected collider pair for
            each colliding game-object pair.
    """

    def __init__(
        self,
        gravity: tuple[float, float] = (0, 0),
        *,
        damping: float = 1.0,
        max_step: float = 1 / 120,
    ) -> None:
        """Create a physics world.

        Args:
            gravity: World gravity vector.
            damping: Non-negative velocity damping factor.
            max_step: Positive maximum duration of each internal simulation step.

        Raises:
            ValueError: If damping is negative or ``max_step`` is not positive.
        """
        if damping < 0:
            raise ValueError("damping must not be negative")
        if max_step <= 0:
            raise ValueError("max_step must be positive")
        self._space = pymunk.Space()
        self.gravity = gravity
        self.damping = damping
        self.max_step = max_step
        self._object_provider: ObjectProvider | None = None
        self._tile_provider: TileProvider | None = None
        self._registered_bodies: set[PhysicsBody] = set()
        self._registered_colliders: set[Collider] = set()
        self._collider_bodies: dict[Collider, pymunk.Body] = {}
        self._implicit_bodies: dict[Collider, pymunk.Body] = {}
        self._shape_colliders: dict[pymunk.Shape, Collider] = {}
        self._shape_tile_layers: dict[pymunk.Shape, TileLayer] = {}
        self._shape_tile_cells: dict[
            pymunk.Shape, tuple[tuple[int, int], ...]
        ] = {}
        self._tile_physics: dict[
            TileLayer,
            tuple[tuple[object, ...], pymunk.Body, tuple[pymunk.Shape, ...]],
        ] = {}
        self._collision_pairs: set[CollisionPair] = set()
        self.collision_contacts: dict[
            tuple[GameObject, GameObject], tuple[CollisionContact, CollisionContact]
        ] = {}
        self._overlap_pairs: set[CollisionPair] = set()
        self._tile_collision_pairs: set[TileCollisionPair] = set()
        self._tile_overlap_pairs: set[TileCollisionPair] = set()
        self._contact_state: tuple[object, ...] | None = None
        self._sharing_contacts = False

    @property
    def gravity(self) -> tuple[float, float]:
        """World gravity vector in pixels per second squared."""
        return self._space.gravity.x, self._space.gravity.y

    @gravity.setter
    def gravity(self, value: tuple[float, float]) -> None:
        """Set world gravity.

        Args:
            value: Gravity vector in pixels per second squared.
        """
        self._space.gravity = value

    @property
    def damping(self) -> float:
        """Velocity damping factor."""
        return self._space.damping

    @damping.setter
    def damping(self, value: float) -> None:
        """Set non-negative velocity damping.

        Args:
            value: Damping factor.

        Raises:
            ValueError: If the damping factor is negative.
        """
        if value < 0:
            raise ValueError("damping must not be negative")
        self._space.damping = value

    def bind(
        self,
        object_provider: ObjectProvider,
        tile_provider: TileProvider | None = None,
    ) -> None:
        """Bind providers used to discover current objects and tile layers.

        Args:
            object_provider: Callable returning scene nodes.
            tile_provider: Optional callable returning tile layers.
        """
        self._object_provider = object_provider
        self._tile_provider = tile_provider

    def refresh(self) -> None:
        """Synchronize registered bodies, colliders, and tile collision shapes."""
        self._refresh_tile_layers()
        active_bodies, active_colliders = self._active_components()

        for collider in self._registered_colliders - active_colliders:
            self._remove_collider(collider)

        for body in self._registered_bodies - active_bodies:
            for collider, collider_body in tuple(
                self._collider_bodies.items()
            ):
                if collider_body is body._body:
                    self._remove_collider(collider)
            self._space.remove(body._body)
            body._initialized = False
        self._registered_bodies.intersection_update(active_bodies)

        for body in active_bodies - self._registered_bodies:
            self._sync_owner_to_body(body)
            self._space.add(body._body)
            if body.fixed_rotation:
                body._body.moment = float("inf")
            body._initialized = True
            self._registered_bodies.add(body)

        for collider in tuple(
            self._registered_colliders & active_colliders
        ):
            expected_body = self._body_for_collider(collider)
            if (
                self._collider_bodies.get(collider) is not expected_body
                or collider._shape_state != collider._state()
            ):
                self._remove_collider(collider)

        for collider in active_colliders - self._registered_colliders:
            body = self._body_for_collider(collider)
            if body.space is None:
                self._sync_collider_body(collider, body)
                self._space.add(body)
            shape = collider._create(body)
            self._space.add(shape)
            owner = collider.owner
            physics_body = (
                None
                if owner is None
                else owner.get_component(PhysicsBody)
            )
            if (
                physics_body is not None
                and physics_body.fixed_rotation
                and physics_body._body is body
            ):
                body.moment = float("inf")
            self._registered_colliders.add(collider)
            self._collider_bodies[collider] = body
            self._shape_colliders[shape] = collider

    def step(self, delta_time: float) -> None:
        """Advance simulation, subdividing time to respect ``max_step``.

        Args:
            delta_time: Non-negative elapsed seconds.

        Raises:
            ValueError: If ``delta_time`` is negative.
        """
        if delta_time < 0:
            raise ValueError("delta_time must not be negative")
        self.refresh()
        for body in self._registered_bodies:
            if body.body_type is BodyType.DYNAMIC:
                self._restore_dynamic_body_transform(body)
            else:
                self._sync_owner_to_body(body)
            if body.fixed_rotation:
                owner = body.owner
                if owner is not None:
                    body._body.angle = -radians(owner.world_rotation)
                body._body.angular_velocity = 0
        for collider, body in self._implicit_bodies.items():
            if collider in self._registered_colliders:
                self._sync_collider_body(collider, body)
                self._space.reindex_shapes_for_body(body)
        if delta_time:
            steps = max(1, ceil(delta_time / self.max_step))
            step_time = delta_time / steps
            for _ in range(steps):
                self._space.step(step_time)
                for body in self._registered_bodies:
                    if body.fixed_rotation:
                        body._body.angular_velocity = 0
        # Parents must have their final world pose before children convert to local space.
        for body in sorted(self._registered_bodies, key=self._body_hierarchy_depth):
            if body.body_type is not BodyType.STATIC:
                self._sync_body_to_owner(body)
        self._contact_state = None
        self.refresh_contacts()

    def refresh_contacts(self) -> None:
        """Refresh collision and overlap contact sets if scene state changed."""
        if self._sharing_contacts and self._contact_state is not None:
            return
        state = self._current_contact_state()
        if state == self._contact_state:
            return
        self.refresh()
        for body in self._registered_bodies:
            if body.body_type is BodyType.DYNAMIC:
                self._restore_dynamic_body_transform(body)
            else:
                self._sync_owner_to_body(body)
            self._space.reindex_shapes_for_body(body._body)
        for collider, body in self._implicit_bodies.items():
            if collider in self._registered_colliders:
                self._sync_collider_body(collider, body)
                self._space.reindex_shapes_for_body(body)
        collisions: set[CollisionPair] = set()
        collision_contacts: dict[
            tuple[GameObject, GameObject], tuple[CollisionContact, CollisionContact]
        ] = {}
        overlaps: set[CollisionPair] = set()
        tile_collisions: set[TileCollisionPair] = set()
        tile_overlaps: set[TileCollisionPair] = set()
        for collider in self._registered_colliders:
            shape = collider._shape
            owner = collider.owner
            if shape is None or owner is None:
                continue
            for match in self._space.shape_query(shape):
                matched_shape = match.shape
                if matched_shape is None:
                    continue
                other = self._shape_colliders.get(matched_shape)
                tile_layer = self._shape_tile_layers.get(matched_shape)
                if tile_layer is not None:
                    pair = (owner, tile_layer)
                    if collider.mode is ColliderMode.QUERY_ONLY:
                        continue
                    tile_contacts = self._tile_contacts_for_match(
                        tile_layer,
                        matched_shape,
                        match,
                    )
                    if collider.mode is ColliderMode.SENSOR:
                        overlaps.add(pair)
                        tile_overlaps.update(
                            (owner, contact) for contact in tile_contacts
                        )
                    else:
                        collisions.add(pair)
                        tile_collisions.update(
                            (owner, contact) for contact in tile_contacts
                        )
                    continue
                if (
                    other is None
                    or other is collider
                    or other.owner is None
                    or other.owner is owner
                ):
                    continue
                pair = self._ordered_pair(owner, other.owner)
                if (
                    collider.mode is ColliderMode.QUERY_ONLY
                    or other.mode is ColliderMode.QUERY_ONLY
                ):
                    continue
                if (
                    collider.mode is ColliderMode.SENSOR
                    or other.mode is ColliderMode.SENSOR
                ):
                    overlaps.add(pair)
                else:
                    collisions.add(pair)
                    first, second = pair
                    if first is not owner:
                        continue
                    contact = CollisionContact._from_point_set(
                        other.owner, collider, other, match.contact_point_set,
                    )
                    contacts = (
                        contact,
                        CollisionContact(
                            owner, other, collider,
                            (-contact.normal[0], -contact.normal[1]),
                            contact.points, contact.penetration,
                        ),
                    )
                    existing = collision_contacts.get((first, other.owner))
                    key = (owner.components.index(collider), other.owner.components.index(other))
                    if existing is None or key < (
                        owner.components.index(existing[0].collider),
                        other.owner.components.index(existing[0].other_collider),
                    ):
                        collision_contacts[(first, other.owner)] = contacts
        self._collision_pairs = collisions
        self.collision_contacts = collision_contacts
        self._overlap_pairs = overlaps
        self._tile_collision_pairs = tile_collisions
        self._tile_overlap_pairs = tile_overlaps
        self._contact_state = self._current_contact_state()

    @contextmanager
    def _share_contacts(self) -> Iterator[None]:
        self.refresh_contacts()
        previous = self._sharing_contacts
        self._sharing_contacts = True
        try:
            yield
        finally:
            self._sharing_contacts = previous

    @contextmanager
    def _independent_queries(self) -> Iterator[None]:
        previous = self._sharing_contacts
        self._sharing_contacts = False
        try:
            yield
        finally:
            self._sharing_contacts = previous
            self._contact_state = None

    def _current_contact_state(self) -> tuple[object, ...]:
        bodies, colliders = self._active_components()
        collider_states = frozenset(
            (
                collider, collider.owner,
                tuple(collider.owner.world_position),
                collider.owner.world_rotation,
                collider._state(),
                collider.owner.get_component(PhysicsBody),
            )
            for collider in colliders if collider.owner is not None
        )
        body_states = frozenset(
            (
                body, body.owner, body.body_type,
                tuple(body.owner.world_position),
                body.owner.world_rotation,
            )
            for body in bodies if body.owner is not None
        )
        tile_states = frozenset(
            (layer, layer._physics_collision_state())
            for layer in (() if self._tile_provider is None else self._tile_provider())
        )
        return collider_states, body_states, tile_states

    @property
    def collision_pairs(self) -> tuple[CollisionPair, ...]:
        """Object/target pairs currently touching through solid colliders.

        Targets may be game objects or tile layers.
        """
        return tuple(self._collision_pairs)

    @property
    def overlap_pairs(self) -> tuple[CollisionPair, ...]:
        """Object/target pairs overlapping through sensor colliders.

        Targets may be game objects or tile layers.
        """
        return tuple(self._overlap_pairs)

    @property
    def tile_collision_pairs(self) -> tuple[TileCollisionPair, ...]:
        """Game-object and tile-contact pairs touching through solid colliders."""
        return tuple(self._tile_collision_pairs)

    @property
    def tile_overlap_pairs(self) -> tuple[TileCollisionPair, ...]:
        """Game-object and tile-contact pairs overlapping through sensors."""
        return tuple(self._tile_overlap_pairs)

    def _tile_contacts_for_match(
        self,
        layer: TileLayer,
        shape: pymunk.Shape,
        match: pymunk.ShapeQueryInfo,
    ) -> tuple[TileContact, ...]:
        from .tilemap import TileContact

        cells = self._shape_tile_cells.get(shape, ())
        if not cells:
            return ()
        contact_set = match.contact_point_set
        points = contact_set.points
        normal = (contact_set.normal.x, contact_set.normal.y)
        epsilon = min(layer.cell_width, layer.cell_height) * 1e-6
        samples = [
            (point.point_b.x, point.point_b.y)
            for point in points
        ]
        for first, second in zip(points, points[1:]):
            distance = max(
                abs(second.point_b.x - first.point_b.x) / layer.cell_width,
                abs(second.point_b.y - first.point_b.y) / layer.cell_height,
            )
            steps = max(1, ceil(distance * 2))
            samples.extend(
                (
                    first.point_b.x
                    + (second.point_b.x - first.point_b.x) * index / steps,
                    first.point_b.y
                    + (second.point_b.y - first.point_b.y) * index / steps,
                )
                for index in range(1, steps)
            )
        if not samples:
            bounds = shape.bb
            samples.append(
                ((bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2)
            )

        cell_set = set(cells)
        contacts: dict[tuple[int, int], TileContact] = {}
        for point_x, point_y in samples:
            column, row = layer.world_to_cell(
                point_x + normal[0] * epsilon,
                point_y + normal[1] * epsilon,
            )
            if (column, row) not in cell_set:
                continue
            cell = layer.tile_at(column, row)
            if cell is not None:
                contacts.setdefault(
                    (column, row),
                    TileContact(
                        layer,
                        column,
                        row,
                        cell,
                        (point_x, point_y),
                        normal,
                    ),
                )
        if not contacts and len(cells) == 1:
            column, row = cells[0]
            cell = layer.tile_at(column, row)
            if cell is not None:
                point = samples[0]
                contacts[(column, row)] = TileContact(
                    layer, column, row, cell, point, normal
                )
        return tuple(contacts.values())

    def overlapping_objects(
        self,
        collider: Collider,
    ) -> tuple[PhysicsTarget, ...]:
        """Return targets overlapping an attached, query-enabled collider.

        Args:
            collider: Collider whose current shape is queried.

        Returns:
            Unique game objects or tile layers intersecting the collider.
            Unattached or query-only colliders return an empty tuple.
        """
        owner = collider.owner
        if owner is None:
            return ()
        self.refresh_contacts()
        if collider._shape is None or collider.mode is ColliderMode.QUERY_ONLY:
            return ()
        targets: set[PhysicsTarget] = set()
        for match in self._space.shape_query(collider._shape):
            if match.shape is None:
                continue
            other = self._shape_colliders.get(match.shape)
            tile_layer = self._shape_tile_layers.get(match.shape)
            if tile_layer is not None:
                targets.add(tile_layer)
            elif (
                other is not None and other.owner is not None
                and other.owner is not owner and other.mode is not ColliderMode.QUERY_ONLY
            ):
                targets.add(other.owner)
        return tuple(targets)

    def overlap_point(
        self,
        point: tuple[float, float],
        *,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[PhysicsTarget, ...]:
        """Return targets containing a world-space point.

        Args:
            point: Query point in world coordinates.
            categories: Query shape category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from results.

        Returns:
            Matching game objects and tile layers.
        """
        self.refresh_contacts()
        excluded = tuple(exclude)
        matches = self._space.point_query(
            point,
            0,
            pymunk.ShapeFilter(categories=categories, mask=mask),
        )
        objects: list[PhysicsTarget] = []
        for match in matches:
            matched_shape = match.shape
            if matched_shape is None:
                continue
            target = self._target_for_shape(matched_shape)
            if (
                target is not None
                and target not in excluded
                and target not in objects
            ):
                objects.append(target)
        return tuple(objects)

    def overlap_circle(
        self,
        center: tuple[float, float],
        radius: float,
        *,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[PhysicsTarget, ...]:
        """Return targets overlapping a circle query shape.

        Args:
            center: Circle center in world coordinates.
            radius: Positive circle radius.
            categories: Query shape category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from results.

        Returns:
            Unique overlapping targets.

        Raises:
            ValueError: If ``radius`` is not positive.
        """
        return tuple(
            hit.target
            for hit in self.overlap_circle_hits(
                center,
                radius,
                categories=categories,
                mask=mask,
                exclude=exclude,
            )
        )

    def overlap_circle_hits(
        self,
        center: tuple[float, float],
        radius: float,
        *,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[OverlapHit, ...]:
        """Return overlap targets and contact geometry for a circle query.

        Args:
            center: Circle center in world coordinates.
            radius: Positive circle radius.
            categories: Query shape category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from results.

        Returns:
            Unique overlap hits with contact normals, points, and penetration.

        Raises:
            ValueError: If ``radius`` is not positive.
        """
        if radius <= 0:
            raise ValueError("radius must be positive")
        body = pymunk.Body(body_type=pymunk.Body.STATIC)
        body.position = center
        shape = pymunk.Circle(body, radius)
        shape.filter = pymunk.ShapeFilter(
            categories=categories,
            mask=mask,
        )
        return self._overlap_shape_hits(shape, exclude=exclude)

    def overlap_box(
        self,
        center: tuple[float, float],
        size: tuple[float, float],
        *,
        rotation: float = 0,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[PhysicsTarget, ...]:
        """Return targets overlapping an axis-aligned or rotated box.

        Args:
            center: Box center in world coordinates.
            size: Positive ``(width, height)`` dimensions.
            rotation: Clockwise box rotation in degrees.
            categories: Query shape category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from results.

        Returns:
            Unique overlapping targets.

        Raises:
            ValueError: If either box dimension is not positive.
        """
        return tuple(
            hit.target
            for hit in self.overlap_box_hits(
                center,
                size,
                rotation=rotation,
                categories=categories,
                mask=mask,
                exclude=exclude,
            )
        )

    def overlap_box_hits(
        self,
        center: tuple[float, float],
        size: tuple[float, float],
        *,
        rotation: float = 0,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[OverlapHit, ...]:
        """Return box overlap hits with contact geometry.

        Args:
            center: Box center in world coordinates.
            size: Positive ``(width, height)`` dimensions.
            rotation: Clockwise box rotation in degrees.
            categories: Query shape category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from results.

        Returns:
            Unique overlap hits with contact normals, points, and penetration.

        Raises:
            ValueError: If either box dimension is not positive.
        """
        width, height = size
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        body = pymunk.Body(body_type=pymunk.Body.STATIC)
        body.position = center
        body.angle = -radians(rotation)
        shape = pymunk.Poly.create_box(body, size)
        shape.filter = pymunk.ShapeFilter(
            categories=categories,
            mask=mask,
        )
        return self._overlap_shape_hits(shape, exclude=exclude)

    def overlap_polygon(
        self,
        points: Iterable[tuple[float, float]],
        *,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[PhysicsTarget, ...]:
        """Return targets overlapping a polygon query shape.

        Args:
            points: Polygon vertices in world coordinates.
            categories: Query shape category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from results.

        Returns:
            Unique overlapping targets.

        Raises:
            ValueError: If the points do not define a valid collider polygon.
        """
        return tuple(
            hit.target
            for hit in self.overlap_polygon_hits(
                points,
                categories=categories,
                mask=mask,
                exclude=exclude,
            )
        )

    def overlap_polygon_hits(
        self,
        points: Iterable[tuple[float, float]],
        *,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[OverlapHit, ...]:
        """Return polygon overlap hits with contact geometry.

        Args:
            points: Polygon vertices in world coordinates.
            categories: Query shape category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from results.

        Returns:
            Unique overlap hits with contact normals, points, and penetration.

        Raises:
            ValueError: If the points do not define a valid collider polygon.
        """
        validated = Collider(
            shape="polygon",
            points=tuple(points),
        ).points
        body = pymunk.Body(body_type=pymunk.Body.STATIC)
        shape = pymunk.Poly(body, validated)
        shape.filter = pymunk.ShapeFilter(
            categories=categories,
            mask=mask,
        )
        return self._overlap_shape_hits(shape, exclude=exclude)

    def nearest(
        self,
        point: tuple[float, float],
        *,
        max_distance: float = float("inf"),
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> PointQueryHit | None:
        """Find the nearest filtered surface to a world-space point.

        Args:
            point: Query point.
            max_distance: Maximum search distance, defaulting to infinity.
            categories: Query category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from consideration.

        Returns:
            Nearest hit, or ``None`` when no target is within range.

        Raises:
            ValueError: If ``max_distance`` is negative or NaN.
        """
        if max_distance < 0 or max_distance != max_distance:
            raise ValueError("max_distance must be non-negative")
        self.refresh_contacts()
        shape_filter = pymunk.ShapeFilter(
            categories=categories,
            mask=mask,
        )
        excluded = tuple(exclude)
        if excluded:
            matches = self._space.point_query(
                point,
                max_distance,
                shape_filter,
            )
            match = min(
                (
                    candidate
                    for candidate in matches
                    if candidate.shape is not None
                    and (
                        target := self._target_for_shape(candidate.shape)
                    ) is not None
                    and target not in excluded
                ),
                key=lambda candidate: candidate.distance,
                default=None,
            )
        else:
            match = self._space.point_query_nearest(
                point,
                max_distance,
                shape_filter,
            )
        if match is None or match.shape is None:
            return None
        target = self._target_for_shape(match.shape)
        if target is None or target in excluded:
            return None
        return PointQueryHit(
            target,
            (match.point.x, match.point.y),
            match.distance,
            (match.gradient.x, match.gradient.y),
        )

    def raycast(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        radius: float = 0,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> PhysicsTarget | None:
        """Return the first target intersected by a segment query.

        Args:
            start: Segment start in world coordinates.
            end: Segment end in world coordinates.
            radius: Non-negative query radius (zero is a ray).
            categories: Query category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from consideration.

        Returns:
            First target hit, or ``None`` if the segment misses.

        Raises:
            ValueError: If ``radius`` is negative.
        """
        hit = self.raycast_hit(
            start,
            end,
            radius=radius,
            categories=categories,
            mask=mask,
            exclude=exclude,
        )
        return None if hit is None else hit.target

    def raycast_hit(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        radius: float = 0,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> RaycastHit | None:
        """Return the first segment hit with its world-space geometry.

        Args:
            start: Segment start in world coordinates.
            end: Segment end in world coordinates.
            radius: Non-negative query radius (zero is a ray).
            categories: Query category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from consideration.

        Returns:
            First hit, or ``None`` if the segment misses.

        Raises:
            ValueError: If ``radius`` is negative.
        """
        if radius < 0:
            raise ValueError("radius must not be negative")
        excluded = tuple(exclude)
        if not excluded:
            self.refresh_contacts()
            match = self._space.segment_query_first(
                start,
                end,
                radius,
                pymunk.ShapeFilter(
                    categories=categories,
                    mask=mask,
                ),
            )
            if match is None or match.shape is None:
                return None
            target = self._target_for_shape(match.shape)
            if target is None:
                return None
            return self._raycast_hit(
                target,
                match,
                hypot(end[0] - start[0], end[1] - start[1]),
            )
        hits = self.raycast_all_hits(
            start,
            end,
            radius=radius,
            categories=categories,
            mask=mask,
            exclude=excluded,
        )
        return hits[0] if hits else None

    def raycast_all_hits(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        radius: float = 0,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[RaycastHit, ...]:
        """Return unique segment hits ordered by fractional distance.

        Args:
            start: Segment start in world coordinates.
            end: Segment end in world coordinates.
            radius: Non-negative query radius (zero is a ray).
            categories: Query category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from consideration.

        Returns:
            Hits ordered from segment start to end.

        Raises:
            ValueError: If ``radius`` is negative.
        """
        if radius < 0:
            raise ValueError("radius must not be negative")
        self.refresh_contacts()
        matches = self._space.segment_query(
            start,
            end,
            radius,
            pymunk.ShapeFilter(categories=categories, mask=mask),
        )
        excluded = tuple(exclude)
        length = hypot(end[0] - start[0], end[1] - start[1])
        hits: list[RaycastHit] = []
        for match in sorted(matches, key=lambda candidate: candidate.alpha):
            if match.shape is None:
                continue
            target = self._target_for_shape(match.shape)
            if (
                target is None
                or target in excluded
                or any(hit.target is target for hit in hits)
            ):
                continue
            hits.append(self._raycast_hit(
                target,
                match,
                length,
            ))
        return tuple(hits)

    def raycast_all(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        radius: float = 0,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[PhysicsTarget, ...]:
        """Return unique targets hit by a segment, in hit order.

        Args:
            start: Segment start in world coordinates.
            end: Segment end in world coordinates.
            radius: Non-negative query radius (zero is a ray).
            categories: Query category bitmask.
            mask: Query collision mask.
            exclude: Targets omitted from consideration.

        Raises:
            ValueError: If ``radius`` is negative.

        Returns:
            Targets ordered from segment start to end.
        """
        return tuple(
            hit.target
            for hit in self.raycast_all_hits(
                start,
                end,
                radius=radius,
                categories=categories,
                mask=mask,
                exclude=exclude,
            )
        )

    def ground_contacts(
        self,
        collider: Collider,
        *,
        max_slope: float = 50,
    ) -> tuple[tuple[PhysicsTarget, ...], tuple[float, float]]:
        """Return solid supports whose normals are within the slope limit.

        Args:
            collider: Collider to inspect for ground support.
            max_slope: Maximum surface angle from horizontal, in degrees.

        Returns:
            A tuple of support targets and their averaged upward-facing normal.
            With no support, the normal is ``(0, 1)``.

        Raises:
            ValueError: If ``max_slope`` is outside ``[0, 90)``.
        """
        if not 0 <= max_slope < 90:
            raise ValueError("max_slope must be between 0 and 90")
        shape = collider._shape
        if shape is None:
            self.refresh_contacts()
            shape = collider._shape
        if shape is None:
            return (), (0, 1)
        minimum_y = cos(radians(max_slope))
        objects: list[PhysicsTarget] = []
        normal_x = 0.0
        normal_y = 0.0
        for match in self._space.shape_query(shape):
            matched_shape = match.shape
            if matched_shape is None:
                continue
            other = self._shape_colliders.get(matched_shape)
            tile_layer = self._shape_tile_layers.get(matched_shape)
            if tile_layer is not None:
                target: PhysicsTarget = tile_layer
            elif (
                other is not None
                and other.owner is not None
                and other.owner is not collider.owner
                and other.mode is ColliderMode.SOLID
            ):
                target = other.owner
            else:
                continue
            query_normal = match.contact_point_set.normal
            ground_normal = (-query_normal.x, -query_normal.y)
            if (
                not all(isfinite(value) for value in ground_normal)
                or ground_normal[1] < minimum_y
            ):
                continue
            if target not in objects:
                objects.append(target)
            normal_x += ground_normal[0]
            normal_y += ground_normal[1]
        length = hypot(normal_x, normal_y)
        normal = (
            (normal_x / length, normal_y / length)
            if length
            else (0, 1)
        )
        return tuple(objects), normal

    def ground_velocity(
        self, collider: Collider, target: PhysicsTarget,
    ) -> tuple[float, float]:
        """Return a moving target's velocity at the ground contact point.

        Args:
            collider: Collider of the supported object.
            target: Potential moving game-object support.

        Returns:
            Surface velocity in pixels per second; static/non-object supports
            return ``(0, 0)``.

        Raises:
            RuntimeError: If the collider has no owner when the target is a
                moving game object.
        """
        from .nodes import GameObject

        if not isinstance(target, GameObject):
            return (0.0, 0.0)
        body = target.get_component(PhysicsBody)
        if body is None or not body.enabled or body.body_type is BodyType.STATIC:
            return (0.0, 0.0)
        owner = collider.owner
        if owner is None:
            raise RuntimeError("ground velocity needs an attached collider")
        point = (owner.world_position.x, owner.bottom)
        shape = collider._shape
        if shape is not None:
            points = [
                contact.point_b
                for match in self._space.shape_query(shape)
                if match.shape is not None and self._target_for_shape(match.shape) is target
                and match.contact_point_set.normal.y < 0
                for contact in match.contact_point_set.points
            ]
            if points:
                point = (
                    sum(p.x for p in points) / len(points),
                    sum(p.y for p in points) / len(points),
                )
        velocity = body._body.velocity_at_world_point(point)
        return velocity.x, velocity.y

    def _snap_to_ground(
        self,
        collider: Collider,
        body: PhysicsBody,
        *,
        distance: float,
        max_slope: float,
        snap: bool = True,
        preferred_normal: tuple[float, float] | None = None,
    ) -> tuple[tuple[PhysicsTarget, ...], tuple[float, float]]:
        shape = collider._shape
        if shape is None or distance == 0:
            return (), (0, 1)
        support_tolerance = self._space.collision_slop * 2
        if isinstance(shape, pymunk.Circle):
            center = shape.body.local_to_world(shape.offset)
            starts = ((center.x, center.y),)
            radius = shape.radius
        elif isinstance(shape, pymunk.Poly):
            vertices = [
                shape.body.local_to_world(vertex)
                for vertex in shape.get_vertices()
            ]
            bottom = min(vertex.y for vertex in vertices)
            starts = tuple(
                (vertex.x, vertex.y)
                for vertex in vertices
                if abs(vertex.y - bottom) < 1e-6
            )
            if len(starts) > 1:
                starts += ((sum(x for x, _ in starts) / len(starts), bottom),)
                left = min(x for x, _ in starts)
                right = max(x for x, _ in starts)
                support_tolerance = min(distance, (right - left) / 64)
                starts += (
                    (left + support_tolerance, bottom),
                    (right - support_tolerance, bottom),
                )
            radius = shape.radius
        else:
            return (), (0, 1)
        closest: pymunk.SegmentQueryInfo | None = None
        hits: list[pymunk.SegmentQueryInfo] = []
        # Start above solver penetration and exact-edge undefined normals.
        probe_lift = max(distance, self._space.collision_slop * 2)
        probe_length = distance + probe_lift
        for x, y in starts:
            for hit in self._space.segment_query(
                (x, y + probe_lift), (x, y - distance), radius, shape.filter,
            ):
                hit_shape = hit.shape
                if hit_shape is None:
                    continue
                other = self._shape_colliders.get(hit_shape)
                if (
                    hit_shape is shape
                    or hit_shape.sensor
                    or (other is not None and other.mode is not ColliderMode.SOLID)
                    or self._target_for_shape(hit_shape) is None
                    or not all(isfinite(value) for value in (
                        hit.alpha, hit.normal.x, hit.normal.y,
                    ))
                    or hit.normal.y < cos(radians(max_slope))
                ):
                    continue
                hits.append(hit)
                if closest is None or hit.alpha < closest.alpha:
                    closest = hit
        if (
            closest is None
            or closest.shape is None
            or closest.normal.y < cos(radians(max_slope))
        ):
            return (), (0, 1)
        if preferred_normal is not None:
            compatible = [
                hit for hit in hits
                if (hit.alpha - closest.alpha) * probe_length <= support_tolerance
                and (
                    hit.normal.x * preferred_normal[0]
                    + hit.normal.y * preferred_normal[1]
                ) > 0.999
            ]
            if compatible:
                closest = min(compatible, key=lambda hit: hit.alpha)
        support_shape = closest.shape
        assert support_shape is not None
        target = self._target_for_shape(support_shape)
        assert target is not None
        correction = closest.alpha * probe_length - probe_lift
        if correction < -self._space.collision_slop:
            probe_body = pymunk.Body(body_type=pymunk.Body.STATIC)
            probe_body.position = body._body.position - (0, correction)
            probe_body.angle = body._body.angle
            if isinstance(shape, pymunk.Circle):
                probe_shape = pymunk.Circle(probe_body, shape.radius, shape.offset)
            else:
                assert isinstance(shape, pymunk.Poly)
                probe_shape = pymunk.Poly(
                    probe_body, shape.get_vertices(), radius=shape.radius,
                )
            probe_shape.filter = shape.filter
            for overlap in self._space.shape_query(probe_shape):
                other_shape = overlap.shape
                if other_shape is None or other_shape is shape or other_shape.sensor:
                    continue
                if any(
                    point.distance < -self._space.collision_slop
                    for point in overlap.contact_point_set.points
                ):
                    return (), (0, 1)
        if snap or correction < -self._space.collision_slop:
            body._body.position -= (
                0, correction,
            )
            self._sync_body_to_owner(body)
            self._space.reindex_shapes_for_body(body._body)
        return (target,), (closest.normal.x, closest.normal.y)

    def _overlap_shape(
        self,
        shape: pymunk.Shape,
        *,
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[PhysicsTarget, ...]:
        return tuple(
            hit.target
            for hit in self._overlap_shape_hits(shape, exclude=exclude)
        )

    def _overlap_shape_hits(
        self,
        shape: pymunk.Shape,
        *,
        exclude: Iterable[PhysicsTarget] = (),
    ) -> tuple[OverlapHit, ...]:
        self.refresh_contacts()
        excluded = tuple(exclude)
        hits: list[OverlapHit] = []
        for match in self._space.shape_query(shape):
            matched_shape = match.shape
            if matched_shape is None:
                continue
            target = self._target_for_shape(matched_shape)
            if (
                target is None
                or target in excluded
                or any(hit.target is target for hit in hits)
            ):
                continue
            contact_set = match.contact_point_set
            hits.append(OverlapHit(
                target,
                (contact_set.normal.x, contact_set.normal.y),
                tuple(
                    (
                        (point.point_a.x + point.point_b.x) / 2,
                        (point.point_a.y + point.point_b.y) / 2,
                    )
                    for point in contact_set.points
                ),
                max(
                    (
                        max(
                            0.0,
                            (point.point_a - point.point_b).dot(
                                contact_set.normal
                            ),
                        )
                        for point in contact_set.points
                    ),
                    default=0.0,
                ),
            ))
        return tuple(hits)

    @staticmethod
    def _raycast_hit(
        target: PhysicsTarget,
        match: pymunk.SegmentQueryInfo,
        length: float,
    ) -> RaycastHit:
        return RaycastHit(
            target,
            (match.point.x, match.point.y),
            (match.normal.x, match.normal.y),
            match.alpha,
            match.alpha * length,
        )

    def _target_for_shape(
        self,
        shape: pymunk.Shape,
    ) -> PhysicsTarget | None:
        tile_layer = self._shape_tile_layers.get(shape)
        if tile_layer is not None:
            return tile_layer
        collider = self._shape_colliders.get(shape)
        return None if collider is None else collider.owner

    def _refresh_tile_layers(self) -> None:
        active = (
            set()
            if self._tile_provider is None
            else set(self._tile_provider())
        )
        for layer in set(self._tile_physics) - active:
            self._remove_tile_layer(layer)
        for layer in active:
            state = layer._physics_collision_state()
            current = self._tile_physics.get(layer)
            if current is not None and current[0] == state:
                continue
            if current is not None:
                self._remove_tile_layer(layer)
            entries = layer._collision_entries()
            if not entries:
                self._tile_physics[layer] = (
                    state,
                    pymunk.Body(body_type=pymunk.Body.STATIC),
                    (),
                )
                continue
            body = pymunk.Body(body_type=pymunk.Body.STATIC)
            shapes: list[pymunk.Shape] = []
            full_cells = {
                (column, row)
                for column, row, cell, collision in entries
                if (
                    collision.is_full_rectangle
                    and int(cell.flags) == 0
                )
            }
            for column, row, width, height in self._merged_cells(
                full_cells
            ):
                left, bottom = layer.cell_origin(column, row)
                right = left + width * layer.cell_width
                top = bottom + height * layer.cell_height
                shape = pymunk.Poly(
                    body,
                    (
                        (left, bottom),
                        (right, bottom),
                        (right, top),
                        (left, top),
                    ),
                )
                shape.friction = 0.7
                shapes.append(shape)
                self._shape_tile_layers[shape] = layer
                self._shape_tile_cells[shape] = tuple(
                    (cell_column, cell_row)
                    for cell_row in range(row, row + height)
                    for cell_column in range(column, column + width)
                )
            for column, row, cell, collision in entries:
                if (
                    collision.is_full_rectangle
                    and int(cell.flags) == 0
                ):
                    continue
                left, bottom = layer.cell_origin(column, row)
                polygons = self._tile_collision_polygons(
                    layer,
                    collision,
                )
                for polygon in polygons:
                    points = tuple(
                        (
                            left + transformed_x * layer.cell_width,
                            bottom + transformed_y * layer.cell_height,
                        )
                        for point in polygon
                        for transformed_x, transformed_y in (
                            self._transform_tile_collision_point(
                                point,
                                cell.flags,
                            ),
                        )
                    )
                    if self._polygon_area(points) < 0:
                        points = tuple(reversed(points))
                    shape = pymunk.Poly(body, points)
                    shape.friction = 0.7
                    shapes.append(shape)
                    self._shape_tile_layers[shape] = layer
                    self._shape_tile_cells[shape] = ((column, row),)
            self._space.add(body, *shapes)
            self._tile_physics[layer] = (state, body, tuple(shapes))

    @staticmethod
    def _tile_collision_polygons(
        layer: TileLayer,
        collision: TileCollision,
    ) -> tuple[tuple[tuple[float, float], ...], ...]:
        if collision.shape == "polygon":
            return collision.polygons
        width = (
            layer.cell_width
            if collision.width is None
            else collision.width
        )
        height = (
            layer.cell_height
            if collision.height is None
            else collision.height
        )
        left = collision.x / layer.cell_width
        bottom = collision.y / layer.cell_height
        right = left + width / layer.cell_width
        top = bottom + height / layer.cell_height
        return (((left, bottom), (right, bottom), (right, top), (left, top)),)

    @staticmethod
    def _transform_tile_collision_point(
        point: tuple[float, float],
        flags: TileFlags,
    ) -> tuple[float, float]:
        x, y = point
        if int(flags) & 2:
            y = 1 - y
        if int(flags) & 1:
            x = 1 - x
        if int(flags) & 4:
            x, y = y, x
        return x, y

    @staticmethod
    def _polygon_area(
        points: tuple[tuple[float, float], ...],
    ) -> float:
        return sum(
            x * next_y - next_x * y
            for (x, y), (next_x, next_y) in zip(
                points,
                (*points[1:], points[0]),
                strict=True,
            )
        ) / 2

    def _remove_tile_layer(self, layer: TileLayer) -> None:
        current = self._tile_physics.pop(layer, None)
        if current is None:
            return
        _, body, shapes = current
        for shape in shapes:
            self._shape_tile_layers.pop(shape, None)
            self._shape_tile_cells.pop(shape, None)
        if body.space is self._space:
            self._space.remove(*shapes, body)

    @staticmethod
    def _merged_cells(
        source: set[tuple[int, int]],
    ) -> tuple[tuple[int, int, int, int], ...]:
        remaining = set(source)
        rectangles: list[tuple[int, int, int, int]] = []
        while remaining:
            column, row = min(remaining, key=lambda cell: (cell[1], cell[0]))
            width = 1
            while (column + width, row) in remaining:
                width += 1
            height = 1
            while all(
                (candidate, row + height) in remaining
                for candidate in range(column, column + width)
            ):
                height += 1
            for candidate_row in range(row, row + height):
                for candidate_column in range(column, column + width):
                    remaining.remove((candidate_column, candidate_row))
            rectangles.append((column, row, width, height))
        return tuple(rectangles)

    def _active_components(
        self,
    ) -> tuple[set[PhysicsBody], set[Collider]]:
        bodies: set[PhysicsBody] = set()
        colliders: set[Collider] = set()
        if self._object_provider is None:
            return bodies, colliders
        for node in self._object_provider():
            from .nodes import GameObject

            if (
                not isinstance(node, GameObject)
                or not node.enabled_in_hierarchy
                or node.layer is None
                or not node.layer.enabled
            ):
                continue
            node_colliders = {
                component
                for component in node.components
                if isinstance(component, Collider) and component.enabled
            }
            colliders.update(node_colliders)
            body = node.get_component(PhysicsBody)
            if body is None or not body.enabled:
                continue
            if (
                body.body_type is not BodyType.DYNAMIC
                or node_colliders
            ):
                bodies.add(body)
        return bodies, colliders

    def _body_for_collider(self, collider: Collider) -> pymunk.Body:
        owner = collider.owner
        if owner is None:
            raise RuntimeError("collider is not attached")
        physics_body = owner.get_component(PhysicsBody)
        if (
            physics_body is not None
            and physics_body in self._registered_bodies
        ):
            return physics_body._body
        body = self._implicit_bodies.get(collider)
        if body is None:
            body = pymunk.Body(body_type=pymunk.Body.STATIC)
            self._implicit_bodies[collider] = body
        return body

    def _remove_collider(self, collider: Collider) -> None:
        shape = collider._shape
        if shape is not None:
            self._shape_colliders.pop(shape, None)
            if shape.space is self._space:
                self._space.remove(shape)
        collider._shape = None
        collider._shape_state = None
        self._registered_colliders.discard(collider)
        body = self._collider_bodies.pop(collider, None)
        implicit = self._implicit_bodies.pop(collider, None)
        if (
            implicit is not None
            and implicit is body
            and implicit.space is self._space
        ):
            self._space.remove(implicit)

    @staticmethod
    def _sync_collider_body(
        collider: Collider,
        body: pymunk.Body,
    ) -> None:
        owner = collider.owner
        if owner is None:
            return
        position = owner.world_position
        body.position = position.x, position.y
        body.angle = -radians(owner.world_rotation)

    @staticmethod
    def _ordered_pair(
        first: GameObject,
        second: GameObject,
    ) -> tuple[GameObject, GameObject]:
        return (
            (first, second)
            if id(first) < id(second)
            else (second, first)
        )

    @staticmethod
    def _body_hierarchy_depth(component: PhysicsBody) -> int:
        depth = 0
        owner = component.owner
        while owner is not None and owner.parent is not None:
            depth += 1
            owner = owner.parent
        return depth

    @staticmethod
    def _sync_owner_to_body(component: PhysicsBody) -> None:
        owner = component.owner
        if owner is None:
            return
        position = owner.world_position
        component._body.position = position.x, position.y
        component._body.angle = -radians(owner.world_rotation)

    @staticmethod
    def _restore_dynamic_body_transform(component: PhysicsBody) -> None:
        owner = component.owner
        if owner is None:
            return
        body = component._body
        position = owner.world_position
        position_changed = not (
            isclose(position.x, body.position.x, abs_tol=1e-6)
            and isclose(position.y, body.position.y, abs_tol=1e-6)
        )
        body_rotation = -degrees(body.angle)
        rotation_difference = (
            owner.world_rotation - body_rotation + 180
        ) % 360 - 180
        rotation_changed = (
            not component.fixed_rotation
            and not isclose(rotation_difference, 0, abs_tol=1e-6)
        )
        if position_changed or rotation_changed:
            label = owner.name or owner.scene_id or "unnamed game object"
            controlled_transform = (
                "position"
                if component.fixed_rotation
                else "position and rotation"
            )
            warn(
                (
                    f"Dynamic PhysicsBody on {label!r} controls its runtime "
                    f"{controlled_transform}; direct transform changes are "
                    "ignored. Use velocity, apply_force(), apply_impulse(), "
                    "a movement motor, or teleport()."
                ),
                PhysicsTransformWarning,
                stacklevel=4,
            )
        if position_changed:
            owner.world_position = body.position.x, body.position.y
        if component.fixed_rotation:
            body.angle = -radians(owner.world_rotation)
        elif rotation_changed:
            owner.world_rotation = body_rotation

    @staticmethod
    def _sync_body_to_owner(component: PhysicsBody) -> None:
        owner = component.owner
        if owner is None:
            return
        owner.world_position = component._body.position.x, component._body.position.y
        owner.world_rotation = -degrees(component._body.angle)
