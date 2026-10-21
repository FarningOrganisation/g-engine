from __future__ import annotations

from math import cos, radians, sin
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..nodes import GameObject


def _bounds_from_points(
    points: tuple[tuple[float, float], ...],
    *,
    padding: float = 0,
) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (
        min(xs) - padding,
        min(ys) - padding,
        max(xs) + padding,
        max(ys) + padding,
    )


def _transform_point(
    owner: GameObject,
    point: tuple[float, float],
) -> tuple[float, float]:
    scale_x, scale_y = owner.world_scale
    x = point[0] * scale_x
    y = point[1] * scale_y
    angle = -radians(owner.world_rotation)
    cosine = cos(angle)
    sine = sin(angle)
    world_x, world_y = owner.world_position
    return (
        world_x + x * cosine - y * sine,
        world_y + x * sine + y * cosine,
    )
