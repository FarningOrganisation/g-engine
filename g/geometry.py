from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import cos, radians, sin
from typing import TypeAlias


@dataclass(frozen=True)
class CircleGeometry:
    x: float
    y: float
    radius: float


@dataclass(frozen=True)
class BoxGeometry:
    points: tuple[tuple[float, float], ...]


CollisionGeometry: TypeAlias = CircleGeometry | BoxGeometry


def rotated_box(
    x: float,
    y: float,
    width: float,
    height: float,
    angle: float,
) -> BoxGeometry:
    half_width = width / 2
    half_height = height / 2
    rotation = -radians(angle)
    cosine = cos(rotation)
    sine = sin(rotation)
    points = tuple(
        (
            x + local_x * cosine - local_y * sine,
            y + local_x * sine + local_y * cosine,
        )
        for local_x, local_y in (
            (-half_width, -half_height),
            (half_width, -half_height),
            (half_width, half_height),
            (-half_width, half_height),
        )
    )
    return BoxGeometry(points)


def _axes(box: BoxGeometry) -> Iterable[tuple[float, float]]:
    for index, point in enumerate(box.points):
        next_point = box.points[(index + 1) % len(box.points)]
        edge_x = next_point[0] - point[0]
        edge_y = next_point[1] - point[1]
        length = (edge_x * edge_x + edge_y * edge_y) ** 0.5
        if length:
            yield -edge_y / length, edge_x / length


def _project(
    box: BoxGeometry, axis: tuple[float, float]
) -> tuple[float, float]:
    projections = tuple(
        point_x * axis[0] + point_y * axis[1]
        for point_x, point_y in box.points
    )
    return min(projections), max(projections)


def _overlaps(
    first: tuple[float, float], second: tuple[float, float]
) -> bool:
    return first[1] >= second[0] and second[1] >= first[0]


def _boxes_intersect(first: BoxGeometry, second: BoxGeometry) -> bool:
    return all(
        _overlaps(_project(first, axis), _project(second, axis))
        for axis in (*_axes(first), *_axes(second))
    )


def _circle_box_intersect(
    circle: CircleGeometry, box: BoxGeometry
) -> bool:
    closest_point = min(
        box.points,
        key=lambda point: (point[0] - circle.x) ** 2
        + (point[1] - circle.y) ** 2,
    )
    center_axis_x = closest_point[0] - circle.x
    center_axis_y = closest_point[1] - circle.y
    center_axis_length = (
        center_axis_x * center_axis_x + center_axis_y * center_axis_y
    ) ** 0.5
    axes = list(_axes(box))
    if center_axis_length:
        axes.append(
            (
                center_axis_x / center_axis_length,
                center_axis_y / center_axis_length,
            )
        )

    for axis in axes:
        center_projection = circle.x * axis[0] + circle.y * axis[1]
        circle_projection = (
            center_projection - circle.radius,
            center_projection + circle.radius,
        )
        if not _overlaps(circle_projection, _project(box, axis)):
            return False
    return True


def intersects(first: CollisionGeometry, second: CollisionGeometry) -> bool:
    if isinstance(first, CircleGeometry) and isinstance(second, CircleGeometry):
        distance_x = first.x - second.x
        distance_y = first.y - second.y
        radius = first.radius + second.radius
        return distance_x * distance_x + distance_y * distance_y <= radius * radius
    if isinstance(first, CircleGeometry):
        return _circle_box_intersect(first, second)
    if isinstance(second, CircleGeometry):
        return _circle_box_intersect(second, first)
    return _boxes_intersect(first, second)


def contains_point(
    geometry: CollisionGeometry, x: float, y: float
) -> bool:
    if isinstance(geometry, CircleGeometry):
        distance_x = x - geometry.x
        distance_y = y - geometry.y
        return (
            distance_x * distance_x + distance_y * distance_y
            <= geometry.radius * geometry.radius
        )
    for axis in _axes(geometry):
        projection = x * axis[0] + y * axis[1]
        minimum, maximum = _project(geometry, axis)
        if projection < minimum or projection > maximum:
            return False
    return True
