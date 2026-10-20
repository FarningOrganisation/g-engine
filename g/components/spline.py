"""A transform-aware, editable Catmull–Rom spline component."""

from __future__ import annotations

from math import isfinite
from typing import Sequence

from ..curves import CatmullRomSpline, Point
from ..inspector import inspector_field
from .base import Component
from .renderer_utils import _transform_point


class Spline(Component):
    """Own local Catmull–Rom control points and query the world-space curve.

    Attributes:
        points: Local-space control points.
        closed: Whether the curve connects its last point back to the first.
        tolerance: Maximum adaptive arc-table flatness error.
    """

    MIN_OPEN_POINTS = 2
    MIN_CLOSED_POINTS = 3
    DEFAULT_POINTS: tuple[Point, ...] = ((-64.0, 0.0), (0.0, 64.0), (64.0, 0.0))

    __inspector_fields__ = {
        "points": inspector_field(
            [list(point) for point in DEFAULT_POINTS],
            tooltip="Spline control points in local coordinates.",
            group="Movement",
        ),
        "closed": inspector_field(
            False,
            tooltip="Connect the final control point back to the first.",
            group="Movement",
        ),
        "tolerance": inspector_field(
            0.5,
            minimum=0.000001,
            tooltip="Maximum adaptive arc-table flatness error.",
            group="Movement",
        ),
    }

    def __init__(
        self,
        points: Sequence[Sequence[float]] = DEFAULT_POINTS,
        *,
        closed: bool = False,
        tolerance: float = 0.5,
        enabled: bool = True,
    ) -> None:
        """Create a spline from control points and curve options.

        Args:
            points: Ordered local-space control points.
            closed: Connect the final point to the first.
            tolerance: Positive curve arc-table tolerance.
            enabled: Whether the component is enabled.
        """
        super().__init__(enabled=enabled)
        self._closed = closed
        self._tolerance = tolerance
        self._curve: CatmullRomSpline | None = None
        self._world_curve_key: tuple[object, ...] | None = None
        self._world_curve_cache: CatmullRomSpline | None = None
        self._points: tuple[Point, ...] = ()
        self.points = points

    @property
    def points(self) -> tuple[Point, ...]:
        """Local-space curve control points."""
        return self._points

    @points.setter
    def points(self, value: Sequence[Sequence[float]]) -> None:
        """Replace the points and rebuild the curve caches."""
        curve = CatmullRomSpline(value, self._closed, self._tolerance)
        self._points = curve.points
        self._curve = curve
        self._world_curve_key = None
        self._world_curve_cache = None

    @property
    def closed(self) -> bool:
        """Whether the curve is closed."""
        return self._closed

    @closed.setter
    def closed(self, value: bool) -> None:
        """Set whether the curve joins its endpoints."""
        curve = CatmullRomSpline(self._points, value, self._tolerance)
        self._closed = value
        self._curve = curve
        self._world_curve_key = None
        self._world_curve_cache = None

    @property
    def tolerance(self) -> float:
        """Curve approximation tolerance."""
        return self._tolerance

    @tolerance.setter
    def tolerance(self, value: float) -> None:
        """Set the approximation tolerance and rebuild cached curves."""
        curve = CatmullRomSpline(self._points, self._closed, value)
        self._tolerance = curve.tolerance
        self._curve = curve
        self._world_curve_key = None
        self._world_curve_cache = None

    @property
    def curve(self) -> CatmullRomSpline:
        """Local-space Catmull–Rom curve."""
        if self._curve is None:
            self._curve = CatmullRomSpline(
                self._points, self._closed, self._tolerance
            )
        return self._curve

    @property
    def world_curve(self) -> CatmullRomSpline:
        """Curve transformed by the owning object's world transform."""
        if self.owner is None:
            return self.curve
        owner = self.owner
        position = owner.world_position
        scale = owner.world_scale
        key: tuple[object, ...] = (
            self._points,
            self._closed,
            self._tolerance,
            position.x,
            position.y,
            owner.world_rotation,
            scale,
        )
        if key != self._world_curve_key:
            origin = _transform_point(owner, (0.0, 0.0))
            x_axis_point = _transform_point(owner, (1.0, 0.0))
            y_axis_point = _transform_point(owner, (0.0, 1.0))
            x_axis = (
                x_axis_point[0] - origin[0],
                x_axis_point[1] - origin[1],
            )
            y_axis = (
                y_axis_point[0] - origin[0],
                y_axis_point[1] - origin[1],
            )

            def transform(point: Point) -> Point:
                return (
                    origin[0] + point[0] * x_axis[0] + point[1] * y_axis[0],
                    origin[1] + point[0] * x_axis[1] + point[1] * y_axis[1],
                )

            if not all(isfinite(value) for value in (*origin, *x_axis, *y_axis)):
                raise ValueError("spline world transform must be finite")
            self._world_curve_cache = CatmullRomSpline._transformed(
                self.curve, transform
            )
            self._world_curve_key = key
        assert self._world_curve_cache is not None
        return self._world_curve_cache

    @property
    def length(self) -> float:
        """World-space curve length."""
        return self.world_curve.length

    def point_at_distance(self, distance: float) -> Point:
        """Return the world-space point at a distance along the curve."""
        return self.world_curve.point_at_distance(distance)

    def tangent_at_distance(self, distance: float) -> Point:
        """Return the tangent at a world-space distance along the curve."""
        return self.world_curve.tangent_at_distance(distance)


__all__ = ["Spline"]
