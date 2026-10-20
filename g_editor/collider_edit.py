"""Collider-edit geometry shared by the viewport and editor state."""

from __future__ import annotations

from collections.abc import Mapping
from math import cos, hypot, isfinite, radians, sin, tau

from g import Collider, GameObject
from g.components.renderer_utils import _transform_point
from g.geometry import CircleGeometry
from g.persistence import JsonValue


COLLIDER_SHAPES: tuple[str, ...] = tuple(Collider.SHAPES)
GEOMETRY_FIELDS: dict[str, tuple[str, ...]] = {
    shape: (*Collider.SHAPE_FIELDS[shape], "offset_x", "offset_y") for shape in COLLIDER_SHAPES
}
GEOMETRY_DEFAULTS: dict[str, float] = {
    "width": 64, "height": 64, "radius": 32, "radius_x": 32, "radius_y": 16,
}


def collider_shape(values: Mapping[str, object]) -> str:
    shape = values.get("shape", "box")
    if shape not in COLLIDER_SHAPES:
        raise ValueError(f"Unknown collider shape: {shape!r}")
    return str(shape)


def number(values: Mapping[str, object], name: str, default: float = 0) -> float:
    value = values.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"collider {name} must be finite")
    return float(value)


def polygon_points(value: object) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Polygon collider points must be a list")
    result = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Polygon collider points must contain coordinate pairs")
        result.append((number({"x": point[0]}, "x"), number({"y": point[1]}, "y")))
    return tuple(result)


def validate_geometry(shape: str, values: Mapping[str, object]) -> None:
    """Validate geometry for a shape name; ``"g.collider"`` uses the values' own shape."""
    if shape == "g.collider":
        shape = collider_shape(values)
    if shape not in GEOMETRY_FIELDS:
        raise ValueError(f"Unknown collider shape: {shape!r}")
    number(values, "offset_x")
    number(values, "offset_y")
    if shape == "polygon":
        if "points" in values:
            Collider(shape="polygon", points=polygon_points(values["points"]))
    else:
        for name in GEOMETRY_FIELDS[shape][:-2]:
            if number(values, name, GEOMETRY_DEFAULTS[name]) <= 0:
                raise ValueError(f"collider {name} must be positive")


def geometry_values(collider: Collider) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {
        "offset_x": collider.offset_x, "offset_y": collider.offset_y,
    }
    shape = collider.shape
    if shape == "box":
        values.update(width=collider.width, height=collider.height)
    elif shape == "circle":
        values["radius"] = collider.radius
    elif shape == "ellipse":
        values.update(radius_x=collider.radius_x, radius_y=collider.radius_y)
    elif shape == "polygon":
        values["points"] = [list(p) for p in collider.points]
    return values


def apply_geometry(collider: Collider, values: Mapping[str, object]) -> None:
    shape = collider.shape
    if shape == "box":
        collider.width = number(values, "width")
        collider.height = number(values, "height")
    elif shape == "circle":
        collider.radius = number(values, "radius")
    elif shape == "ellipse":
        collider.radius_x = number(values, "radius_x")
        collider.radius_y = number(values, "radius_y")
    elif shape == "polygon":
        collider.points = polygon_points(values["points"])
    collider.offset_x = number(values, "offset_x")
    collider.offset_y = number(values, "offset_y")


def local_point(owner: GameObject, world: tuple[float, float]) -> tuple[float, float]:
    sx, sy = owner.world_scale
    if sx == 0 or sy == 0:
        raise ValueError("Cannot edit component geometry on an object with zero world scale")
    x, y = world[0] - owner.world_position.x, world[1] - owner.world_position.y
    angle = radians(owner.world_rotation)
    return ((x * cos(angle) - y * sin(angle)) / sx, (x * sin(angle) + y * cos(angle)) / sy)


def outline(collider: Collider) -> tuple[tuple[float, float], ...]:
    geometry = collider.geometry
    if isinstance(geometry, CircleGeometry):
        return tuple(
            (geometry.x + geometry.radius * cos(tau * i / 48),
             geometry.y + geometry.radius * sin(tau * i / 48))
            for i in range(48)
        )
    return geometry.points


def handles(collider: Collider) -> dict[str, tuple[float, float]]:
    owner = collider.owner
    if owner is None:
        return {}
    ox, oy = collider.offset_x, collider.offset_y
    result = {"center": _transform_point(owner, (ox, oy))}
    shape = collider.shape
    geometry = collider.geometry
    if isinstance(geometry, CircleGeometry):
        result.update({
            "radius": (geometry.x + geometry.radius, geometry.y),
            "radius_top": (geometry.x, geometry.y + geometry.radius),
            "radius_left": (geometry.x - geometry.radius, geometry.y),
            "radius_bottom": (geometry.x, geometry.y - geometry.radius),
        })
    elif shape == "polygon":
        result.update({
            f"point:{i}": _transform_point(owner, (x + ox, y + oy))
            for i, (x, y) in enumerate(collider.points)
        })
    else:
        if shape == "box":
            rx, ry = collider.width / 2, collider.height / 2
            directions = ((-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0))
        elif shape == "ellipse":
            rx, ry = collider.radius_x, collider.radius_y
            directions = ((-1, 0), (1, 0), (0, -1), (0, 1))
        else:
            return result
        result.update({
            f"size:{x}:{y}": _transform_point(owner, (ox + x * rx, oy + y * ry))
            for x, y in directions
        })
    return result


def drag_values(
    collider: Collider, handle: str, start: tuple[float, float],
    current: tuple[float, float], original: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    if collider.owner is None:
        raise ValueError("Collider must be attached")
    values = dict(original)
    x, y = local_point(collider.owner, current)
    ox, oy = number(original, "offset_x"), number(original, "offset_y")
    if handle == "center":
        start_x, start_y = local_point(collider.owner, start)
        values.update(offset_x=ox + x - start_x, offset_y=oy + y - start_y)
    elif collider.shape == "circle":
        center = _transform_point(collider.owner, (ox, oy))
        scale = max(abs(s) for s in collider.owner.world_scale)
        values["radius"] = max(0.001, hypot(current[0] - center[0], current[1] - center[1]) / scale)
    elif handle.startswith("point:") and collider.shape == "polygon":
        points: list[JsonValue] = [list(p) for p in polygon_points(original["points"])]
        points[int(handle.split(":")[1])] = [x - ox, y - oy]
        values["points"] = points
    elif handle.startswith("size:"):
        _, dx, dy = handle.split(":")
        if collider.shape == "box":
            values.update(box_resize_values(original, (x, y), int(dx), int(dy)))
        elif collider.shape == "ellipse":
            if int(dx):
                values["radius_x"] = max(0.001, int(dx) * (x - ox))
            if int(dy):
                values["radius_y"] = max(0.001, int(dy) * (y - oy))
    return values


def box_resize_values(
    original: Mapping[str, JsonValue], current: tuple[float, float], dx: int, dy: int,
) -> dict[str, JsonValue]:
    """Resize against the opposite edge in local coordinates."""
    values: dict[str, JsonValue] = {}
    for direction, coordinate, size, offset in (
        (dx, current[0], "width", "offset_x"),
        (dy, current[1], "height", "offset_y"),
    ):
        if direction:
            opposite = number(original, offset) - direction * number(original, size) / 2
            extent = max(0.001, direction * (coordinate - opposite))
            values.update({size: extent, offset: opposite + direction * extent / 2})
    return values


def edge_projection(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float],
) -> tuple[float, float]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    amount = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared)) if length_squared else 0.0
    return hypot(point[0] - start[0] - amount * dx, point[1] - start[1] - amount * dy), amount
