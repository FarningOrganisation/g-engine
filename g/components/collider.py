from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from math import cos, isfinite, radians, sin, tau
from typing import TYPE_CHECKING

import pymunk
import pymunk.autogeometry
import pymunk.util

from .base import Component
from .renderer_utils import _transform_point
from ..inspector import inspector_field
from ..geometry import BoxGeometry, CircleGeometry, CollisionGeometry, contains_point, intersects, rotated_box

if TYPE_CHECKING:
    from ..nodes import GameObject
    from ..tilemap import TileLayer

class ColliderMode(Enum):
    """Interaction mode controlling physical contacts and overlap queries."""
    SOLID = "solid"
    SENSOR = "sensor"
    QUERY_ONLY = "query_only"

class Collider(Component):
    """Collision component with a selectable box, circle, ellipse, or polygon.

    Geometry and offsets are defined in local pixels and transformed with the
    owning game object. Filtering can use local masks or PhysicsBody defaults.
    """

    SHAPES = ("box", "circle", "ellipse", "polygon")
    DEFAULT_POINTS = ((-32.0, -32.0), (32.0, -32.0), (32.0, 32.0), (-32.0, 32.0))
    SHAPE_FIELDS = {
        "box": ("width", "height"),
        "circle": ("radius",),
        "ellipse": ("radius_x", "radius_y"),
        "polygon": ("points",),
    }

    __inspector_fields__ = {
        "shape": inspector_field("box", group="Shape", choices=SHAPES, tooltip="Collision geometry. Changing shape retains this component and its settings."),
        "width": inspector_field(64.0, group="Shape", minimum=0, tooltip="Box width in local pixels."),
        "height": inspector_field(64.0, group="Shape", minimum=0, tooltip="Box height in local pixels."),
        "radius": inspector_field(32.0, group="Shape", minimum=0, tooltip="Circle radius in local pixels."),
        "radius_x": inspector_field(32.0, group="Shape", minimum=0.001, tooltip="Ellipse horizontal radius in local pixels."),
        "radius_y": inspector_field(16.0, group="Shape", minimum=0.001, tooltip="Ellipse vertical radius in local pixels."),
        "points": inspector_field([list(point) for point in DEFAULT_POINTS], group="Shape", tooltip="Convex polygon vertices in local pixels."),
        "offset_x": inspector_field(0.0, group="Shape", tooltip="Local horizontal offset from the game object's origin."),
        "offset_y": inspector_field(0.0, group="Shape", tooltip="Local vertical offset from the game object's origin."),
        "density": inspector_field(1.0, group="Material", minimum=0.001, tooltip="Mass per unit area; must be positive."),
        "friction": inspector_field(0.7, group="Material", minimum=0, tooltip="Surface friction coefficient."),
        "elasticity": inspector_field(0.0, group="Material", minimum=0, tooltip="Collision bounce coefficient."),
        "mode": inspector_field("solid", group="Shape", choices=("solid", "sensor", "query_only"), tooltip="Solid blocks motion; Sensor reports overlaps; Query Only participates in queries."),
        "collision_type": inspector_field(0, group="Advanced", group_expanded=False, minimum=0, tooltip="Collision handler type identifier."),
        "override_collision_filtering": inspector_field(False, group="Collision Filtering", group_expanded=False, tooltip="Use this collider's filtering instead of the PhysicsBody defaults. Without a body, local filtering always applies."),
        "categories": inspector_field(0xFFFFFFFF, widget="mask", group="Collision Filtering", group_expanded=False, tooltip="Collision category bit mask."),
        "mask": inspector_field(0xFFFFFFFF, widget="mask", group="Collision Filtering", group_expanded=False, tooltip="Categories this collider can interact with."),
        "group": inspector_field(0, group="Collision Filtering", group_expanded=False, minimum=0, tooltip="Colliders with the same nonzero group do not collide."),
    }
    def __init__(
        self,
        *,
        shape: str = "box",
        width: float = 64.0,
        height: float = 64.0,
        radius: float = 32.0,
        radius_x: float = 32.0,
        radius_y: float = 16.0,
        points: Sequence[tuple[float, float]] = DEFAULT_POINTS,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
        density: float = 1.0,
        friction: float = 0.7,
        elasticity: float = 0.0,
        mode: ColliderMode = ColliderMode.SOLID,
        sensor: bool | None = None,
        collision_type: int = 0,
        categories: int = pymunk.ShapeFilter.ALL_CATEGORIES(),
        mask: int = pymunk.ShapeFilter.ALL_MASKS(),
        group: int = 0,
        override_collision_filtering: bool | None = None,
        enabled: bool = True,
    ) -> None:
        """Create a collider and validate its shape and material settings.

        Args:
            shape: Geometry kind.
            width: Box width in local pixels.
            height: Box height in local pixels.
            radius: Circle radius in local pixels.
            radius_x: Ellipse horizontal radius.
            radius_y: Ellipse vertical radius.
            points: Convex polygon vertices in local coordinates.
            offset_x: Local horizontal offset.
            offset_y: Local vertical offset.
            density: Positive mass per unit area.
            friction: Non-negative friction coefficient.
            elasticity: Non-negative bounce coefficient.
            mode: Collider interaction mode.
            sensor: Compatibility override for sensor/solid mode, if supplied.
            collision_type: Non-negative collision handler identifier.
            categories: Collision category bit mask.
            mask: Mask of categories this collider can interact with.
            group: Collision group identifier.
            override_collision_filtering: Whether to use this collider's own
                filtering instead of attached PhysicsBody defaults.
            enabled: Whether this collider participates in behavior.

        Raises:
            ValueError: A geometry, offset, or material setting is invalid.
        """
        super().__init__(enabled=enabled)
        self.width = width
        self.height = height
        self.radius = radius
        self.radius_x = radius_x
        self.radius_y = radius_y
        self.points = points
        self.shape = shape
        if not isfinite(offset_x) or not isfinite(offset_y):
            raise ValueError("collider offsets must be finite")
        self.offset_x = offset_x
        self.offset_y = offset_y
        if density <= 0:
            raise ValueError("density must be positive")
        if friction < 0:
            raise ValueError("friction must not be negative")
        if elasticity < 0:
            raise ValueError("elasticity must not be negative")
        if collision_type < 0:
            raise ValueError("collision_type must not be negative")
        self.density = density
        self.friction = friction
        self.elasticity = elasticity
        self.mode = (
            ColliderMode.SENSOR if sensor is True
            else ColliderMode.SOLID if sensor is False
            else mode
        )
        self.collision_type = collision_type
        self.categories = categories
        self.mask = mask
        self.group = group
        self.override_collision_filtering = (
            (categories, mask, group) != (0xFFFFFFFF, 0xFFFFFFFF, 0)
            if override_collision_filtering is None else override_collision_filtering
        )
        self._shape: pymunk.Shape | None = None
        self._shape_state: tuple[object, ...] | None = None

    @property
    def shape(self) -> str:
        """Geometry kind: box, circle, ellipse, or polygon."""
        return self._shape_kind

    @shape.setter
    def shape(self, value: str) -> None:
        """Set the geometry kind."""
        if not isinstance(value, str) or value not in self.SHAPES:
            raise ValueError(f"collider shape must be one of {', '.join(self.SHAPES)}")
        self._shape_kind = value

    @staticmethod
    def _dimension(value: float, name: str, *, positive: bool = False) -> float:
        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or value < 0 or (positive and value == 0)
        ):
            requirement = "positive" if positive else "non-negative"
            raise ValueError(f"collider {name} must be finite and {requirement}")
        return float(value)

    @property
    def width(self) -> float:
        """Box width in local pixels."""
        return self._width

    @width.setter
    def width(self, value: float) -> None:
        """Set a finite, non-negative box width."""
        self._width = self._dimension(value, "width")

    @property
    def height(self) -> float:
        """Box height in local pixels."""
        return self._height

    @height.setter
    def height(self, value: float) -> None:
        """Set a finite, non-negative box height."""
        self._height = self._dimension(value, "height")

    @property
    def radius(self) -> float:
        """Circle radius in local pixels."""
        return self._radius

    @radius.setter
    def radius(self, value: float) -> None:
        """Set a finite, non-negative circle radius."""
        self._radius = self._dimension(value, "radius")

    @property
    def radius_x(self) -> float:
        """Ellipse horizontal radius in local pixels."""
        return self._radius_x

    @radius_x.setter
    def radius_x(self, value: float) -> None:
        """Set a finite, positive ellipse horizontal radius."""
        self._radius_x = self._dimension(value, "radius_x", positive=True)

    @property
    def radius_y(self) -> float:
        """Ellipse vertical radius in local pixels."""
        return self._radius_y

    @radius_y.setter
    def radius_y(self, value: float) -> None:
        """Set a finite, positive ellipse vertical radius."""
        self._radius_y = self._dimension(value, "radius_y", positive=True)

    @property
    def points(self) -> tuple[tuple[float, float], ...]:
        """Convex polygon vertices in local coordinates."""
        return self._points

    @points.setter
    def points(self, value: Sequence[tuple[float, float]]) -> None:
        """Set at least three distinct finite convex polygon vertices."""
        points = tuple((float(x), float(y)) for x, y in value)
        if len(points) < 3 or len(set(points)) != len(points):
            raise ValueError("Polygon collider needs at least three distinct vertices")
        if not all(isfinite(v) for point in points for v in point):
            raise ValueError("Polygon collider vertices must be finite")
        crosses = [
            (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            for a, b, c in zip(points, (*points[1:], points[0]), (*points[2:], *points[:2]))
        ]
        # Inserted collinear vertices may have a tiny floating-point turn.
        tolerance = max(abs(v) for v in crosses) * 1e-12
        if not (
            all(v >= -tolerance for v in crosses)
            or all(v <= tolerance for v in crosses)
        ) or not any(abs(v) > tolerance for v in crosses):
            raise ValueError("Polygon collider must be convex and have nonzero area")
        hull = pymunk.util.convex_hull(list(points))
        if abs(abs(pymunk.util.calc_area(list(points))) - abs(pymunk.util.calc_area(hull))) > 1e-7:
            raise ValueError("Polygon collider must not self-intersect")
        self._points = points

    @property
    def sensor(self) -> bool:
        """Whether the collider is non-solid (sensor or query-only)."""
        return self.mode is not ColliderMode.SOLID

    @sensor.setter
    def sensor(self, value: bool) -> None:
        """Set sensor mode, mapping false to solid mode."""
        self.mode = (
            ColliderMode.SENSOR if value else ColliderMode.SOLID
        )

    @property
    def mode(self) -> ColliderMode:
        """Collider interaction mode."""
        return self._mode

    @mode.setter
    def mode(self, value: ColliderMode) -> None:
        """Set mode; an Area-selected collider must remain a sensor."""
        if not isinstance(value, ColliderMode):
            raise TypeError("mode must be a ColliderMode")
        if value is not ColliderMode.SENSOR and self.owner is not None:
            from .area import Area

            if any(isinstance(component, Area) and component.uses_collider(self) for component in self.owner.components):
                raise ValueError("Area's selected collider must remain a sensor")
        self._mode = value

    @property
    def geometry(self) -> CollisionGeometry:
        """World-space collision geometry.

        Raises:
            RuntimeError: The collider is not attached to a game object.
        """
        if self.owner is None:
            raise RuntimeError("collider is not attached")
        x, y = self._world_center()
        sx, sy = self.owner.world_scale
        if self.shape == "circle":
            return CircleGeometry(x, y, self.radius * max(abs(sx), abs(sy)))
        if self.shape == "box":
            return rotated_box(x, y, self.width * abs(sx), self.height * abs(sy), self.owner.world_rotation)
        return BoxGeometry(tuple(
            _transform_point(self.owner, (px + self.offset_x, py + self.offset_y))
            for px, py in self._outline_points()
        ))

    def _outline_points(self) -> tuple[tuple[float, float], ...]:
        if self.shape == "ellipse":
            return tuple(
                (self.radius_x * cos(tau * i / 32), self.radius_y * sin(tau * i / 32))
                for i in range(32)
            )
        return self.points

    def _scaled_offset(self) -> tuple[float, float]:
        if self.owner is None:
            raise RuntimeError("collider is not attached")
        scale_x, scale_y = self.owner.world_scale
        return self.offset_x * scale_x, self.offset_y * scale_y

    def _world_center(self) -> tuple[float, float]:
        if self.owner is None:
            raise RuntimeError("collider is not attached")
        x, y = self.owner.world_position
        offset_x, offset_y = self._scaled_offset()
        angle = -radians(self.owner.world_rotation)
        return (
            x + offset_x * cos(angle) - offset_y * sin(angle),
            y + offset_x * sin(angle) + offset_y * cos(angle),
        )

    def intersects(self, other: Collider) -> bool:
        """Return whether this collider's geometry intersects another.

        Same-owner colliders are considered non-intersecting.
        """
        if not isinstance(other, Collider):
            raise TypeError("other must be a Collider")
        if self.owner is not None and self.owner is other.owner:
            return False
        if (
            self._shape is not None and other._shape is not None
            and self._shape_state == self._state()
            and other._shape_state == other._state()
        ):
            return bool(self._shape.shapes_collide(other._shape).points)
        return intersects(self.geometry, other.geometry)

    def overlaps(self, other: Collider) -> bool:
        """Return whether both enabled colliders geometrically overlap."""
        return (
            self.enabled
            and other.enabled
            and self.intersects(other)
        )

    def is_colliding_with(self, other: Collider) -> bool:
        """Return whether both colliders are solid and overlap."""
        return (
            self.mode is ColliderMode.SOLID
            and other.mode is ColliderMode.SOLID
            and self.overlaps(other)
        )

    def contains_point(self, x: float, y: float) -> bool:
        """Test whether a world-space point is inside this collider."""
        return contains_point(self.geometry, x, y)

    @property
    def overlapping_objects(self) -> tuple[GameObject | TileLayer, ...]:
        """Objects and tile layers currently overlapping this collider."""
        if self.owner is None or self.owner.scene is None:
            return ()
        return self.owner.scene.physics.overlapping_objects(self)

    def _create(self, body: pymunk.Body) -> pymunk.Shape:
        shape = self._create_shape(body)
        shape.density = self.density
        shape.friction = self.friction
        shape.elasticity = self.elasticity
        shape.sensor = self.sensor
        shape.collision_type = self.collision_type
        shape.filter = self.effective_filter
        self._shape = shape
        self._shape_state = self._state()
        return shape

    def _create_shape(self, body: pymunk.Body) -> pymunk.Shape:
        if self.owner is None:
            raise RuntimeError("collider is not attached")
        sx, sy = self.owner.world_scale
        ox, oy = self._scaled_offset()
        if self.shape == "circle":
            return pymunk.Circle(body, self.radius * max(abs(sx), abs(sy)), (ox, oy))
        if self.shape == "box":
            rx, ry = self.width * abs(sx) / 2, self.height * abs(sy) / 2
            return pymunk.Poly(body, [
                (ox - rx, oy - ry), (ox + rx, oy - ry),
                (ox + rx, oy + ry), (ox - rx, oy + ry),
            ])
        return pymunk.Poly(body, [
            ((x + self.offset_x) * sx, (y + self.offset_y) * sy)
            for x, y in self._outline_points()
        ])

    def _state(self) -> tuple[object, ...]:
        collision_filter = self.effective_filter
        return (
            self.offset_x,
            self.offset_y,
            self.density,
            self.friction,
            self.elasticity,
            self.mode,
            self.collision_type,
            collision_filter.categories,
            collision_filter.mask,
            collision_filter.group,
            *self._geometry_state(),
        )

    @property
    def effective_filter(self) -> pymunk.ShapeFilter:
        """Resolved collision filter, including PhysicsBody defaults."""
        from ..physics import PhysicsBody

        body = self.owner.get_component(PhysicsBody) if self.owner is not None else None
        if body is not None and not self.override_collision_filtering:
            return pymunk.ShapeFilter(group=body.group, categories=body.categories, mask=body.mask)
        return pymunk.ShapeFilter(group=self.group, categories=self.categories, mask=self.mask)

    def _geometry_state(self) -> tuple[object, ...]:
        return (
            self.shape,
            *(getattr(self, name) for name in self.SHAPE_FIELDS[self.shape]),
            *(self.owner.world_scale if self.owner is not None else (1, 1)),
        )

    @staticmethod
    def decompose(points: tuple[tuple[float, float], ...]) -> tuple[tuple[tuple[float, float], ...], ...]:
        """Split a simple concave outline without filling its missing regions."""
        points = tuple((float(x), float(y)) for x, y in points)
        if len(points) > 1 and points[0] == points[-1]:
            points = points[:-1]
        if len(points) < 3 or len(set(points)) != len(points) or not all(isfinite(v) for p in points for v in p):
            raise ValueError("Tile collision polygon needs distinct finite vertices")
        edges = list(zip(points, (*points[1:], points[0])))

        def turn(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

        for i, (a, b) in enumerate(edges):
            for j, (c, d) in enumerate(edges[i + 1:], i + 1):
                if j == i + 1 or (i == 0 and j == len(edges) - 1):
                    continue
                if (max(a[0], b[0]) >= min(c[0], d[0]) and max(c[0], d[0]) >= min(a[0], b[0])
                        and max(a[1], b[1]) >= min(c[1], d[1]) and max(c[1], d[1]) >= min(a[1], b[1])
                        and turn(a, b, c) * turn(a, b, d) <= 0 and turn(c, d, a) * turn(c, d, b) <= 0):
                    raise ValueError("Tile collision polygon must not self-intersect")
        area = pymunk.util.calc_area(list(points))
        if abs(area) <= 1e-9:
            raise ValueError("Tile collision polygon must have nonzero area")
        if area < 0:
            points = tuple(reversed(points))
        pieces = pymunk.autogeometry.convex_decomposition([*points, points[0]], 0)
        result = tuple(tuple((v.x, v.y) for v in piece[:-1]) for piece in pieces)
        for piece in result:
            Collider(shape="polygon", points=piece)
        if not result or abs(sum(abs(pymunk.util.calc_area(list(p))) for p in result) - abs(area)) > 1e-7:
            raise ValueError("Tile collision polygon could not be decomposed faithfully")
        return result
