"""Pure-Python runtime curve primitives."""

from __future__ import annotations

from bisect import bisect_left
from math import hypot, isfinite
from typing import Callable, TypeAlias

Point: TypeAlias = tuple[float, float]


def _snapshot_points(points: object, *, closed: bool) -> tuple[Point, ...]:
    if not isinstance(points, (list, tuple)):
        raise TypeError("points must be a sequence of 2D points")
    snapshot: list[Point] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("each point must contain exactly two coordinates")
        coordinates: list[float] = []
        for coordinate in point:
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise TypeError("point coordinates must be real numbers")
            value = float(coordinate)
            if not isfinite(value):
                raise ValueError("point coordinates must be finite")
            coordinates.append(value)
        snapshot.append((coordinates[0], coordinates[1]))

    minimum = 3 if closed else 2
    if len(snapshot) < minimum:
        label = "closed" if closed else "open"
        raise ValueError(f"{label} splines require at least {minimum} points")
    pairs = list(zip(snapshot, snapshot[1:]))
    if closed:
        pairs.append((snapshot[-1], snapshot[0]))
    if any(first == second for first, second in pairs):
        raise ValueError("adjacent spline points must not coincide")
    return tuple(snapshot)


def _distance(first: Point, second: Point) -> float:
    return hypot(second[0] - first[0], second[1] - first[1])


def _mix(first: Point, second: Point, amount: float) -> Point:
    return (
        first[0] + (second[0] - first[0]) * amount,
        first[1] + (second[1] - first[1]) * amount,
    )


class CatmullRomSpline:
    """Centripetal Catmull–Rom curve with an adaptive arc-length table.

    ``t`` is divided evenly among curve segments; ``tolerance`` controls the
    maximum geometric flatness error used to construct distance lookups.

    Args:
        points (object): Ordered 2D control points. Open splines need two points;
            closed splines need three.
        closed (bool): Whether the final point connects back to the first.
        tolerance (float): Positive geometric tolerance for adaptive sampling.

    Raises:
        TypeError: If points, coordinates, or options have invalid types.
        ValueError: If points are invalid, non-finite, adjacent duplicates,
            or tolerance is not positive and finite.
    """

    def __init__(
        self,
        points: object,
        closed: bool = False,
        tolerance: float = 0.5,
    ) -> None:
        """Capture control points and build the adaptive arc-length table."""
        if not isinstance(closed, bool):
            raise TypeError("closed must be a boolean")
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
            raise TypeError("tolerance must be a real number")
        tolerance = float(tolerance)
        if not isfinite(tolerance) or tolerance <= 0:
            raise ValueError("tolerance must be finite and greater than zero")
        self._points = _snapshot_points(points, closed=closed)
        self._closed = closed
        self._tolerance = tolerance
        self._segment_count = len(self._points) if closed else len(self._points) - 1
        self._source_curve: CatmullRomSpline | None = None
        self._point_transform: Callable[[Point], Point] | None = None
        self._sample_parameters: tuple[float, ...]
        self._sampled_points: tuple[Point, ...]
        self._cumulative_lengths: tuple[float, ...]
        self._length: float
        self._build_arc_table()

    @classmethod
    def _transformed(
        cls,
        source: CatmullRomSpline,
        point_transform: Callable[[Point], Point],
    ) -> CatmullRomSpline:
        """Create a transformed view without changing the source parameterization."""
        result = cls.__new__(cls)
        result._points = _snapshot_points(
            tuple(point_transform(point) for point in source.points),
            closed=source.closed,
        )
        result._closed = source.closed
        result._tolerance = source.tolerance
        result._segment_count = source._segment_count
        result._source_curve = source
        result._point_transform = point_transform
        result._build_arc_table()
        return result

    @property
    def points(self) -> tuple[Point, ...]:
        """Control points captured when this spline was created."""
        return self._points

    @property
    def closed(self) -> bool:
        """Whether the spline wraps from its final point to its first."""
        return self._closed

    @property
    def tolerance(self) -> float:
        """Geometric tolerance used to build adaptive samples."""
        return self._tolerance

    @property
    def length(self) -> float:
        """Approximate total arc length based on the adaptive sample table."""
        return self._length

    @property
    def sampled_points(self) -> tuple[Point, ...]:
        """Points in the adaptive arc-length sample table."""
        return self._sampled_points

    @property
    def sample_parameters(self) -> tuple[float, ...]:
        """Normalized curve parameters corresponding to ``sampled_points``."""
        return self._sample_parameters

    def sample(self) -> tuple[tuple[float, Point], ...]:
        """Return adaptive ``(global_t, point)`` samples for hit testing.

        Returns:
            tuple[tuple[float, Point], ...]: Ordered normalized-parameter and point pairs.
        """
        return tuple(zip(self._sample_parameters, self._sampled_points))

    def _segment_points(self, segment: int) -> tuple[Point, Point, Point, Point]:
        count = len(self._points)
        current = segment
        following = (segment + 1) % count
        if self._closed:
            previous = (segment - 1) % count
            after = (segment + 2) % count
            return (
                self._points[previous],
                self._points[current],
                self._points[following],
                self._points[after],
            )
        previous = (
            self._points[0]
            if current == 0
            else self._points[current - 1]
        )
        if current == 0:
            previous = (
                2 * self._points[0][0] - self._points[1][0],
                2 * self._points[0][1] - self._points[1][1],
            )
        after = (
            self._points[-1]
            if following == count - 1
            else self._points[following + 1]
        )
        if following == count - 1:
            after = (
                2 * self._points[-1][0] - self._points[-2][0],
                2 * self._points[-1][1] - self._points[-2][1],
            )
        return previous, self._points[current], self._points[following], after

    @staticmethod
    def _segment_value(
        points: tuple[Point, Point, Point, Point],
        amount: float,
    ) -> tuple[Point, Point]:
        p0, p1, p2, p3 = points
        d01 = _distance(p0, p1) ** 0.5
        d12 = _distance(p1, p2) ** 0.5
        d23 = _distance(p2, p3) ** 0.5
        chord = (p2[0] - p1[0], p2[1] - p1[1])

        # The interval-scaled tangents are the derivatives of the
        # non-uniform Catmull–Rom interpolant on [p1, p2].
        m1 = (
            chord[0] + d12 * (
                (p1[0] - p0[0]) / d01
                - (p2[0] - p0[0]) / (d01 + d12)
            ),
            chord[1] + d12 * (
                (p1[1] - p0[1]) / d01
                - (p2[1] - p0[1]) / (d01 + d12)
            ),
        )
        m2 = (
            chord[0] + d12 * (
                (p3[0] - p2[0]) / d23
                - (p3[0] - p1[0]) / (d12 + d23)
            ),
            chord[1] + d12 * (
                (p3[1] - p2[1]) / d23
                - (p3[1] - p1[1]) / (d12 + d23)
            ),
        )
        u = amount
        u2 = u * u
        u3 = u2 * u
        point = (
            (2 * u3 - 3 * u2 + 1) * p1[0]
            + (u3 - 2 * u2 + u) * m1[0]
            + (-2 * u3 + 3 * u2) * p2[0]
            + (u3 - u2) * m2[0],
            (2 * u3 - 3 * u2 + 1) * p1[1]
            + (u3 - 2 * u2 + u) * m1[1]
            + (-2 * u3 + 3 * u2) * p2[1]
            + (u3 - u2) * m2[1],
        )
        tangent = (
            (6 * u2 - 6 * u) * p1[0]
            + (3 * u2 - 4 * u + 1) * m1[0]
            + (-6 * u2 + 6 * u) * p2[0]
            + (3 * u2 - 2 * u) * m2[0],
            (6 * u2 - 6 * u) * p1[1]
            + (3 * u2 - 4 * u + 1) * m1[1]
            + (-6 * u2 + 6 * u) * p2[1]
            + (3 * u2 - 2 * u) * m2[1],
        )
        if not all(isfinite(value) for value in (*point, *tangent)):
            raise ValueError("spline evaluation must remain finite")
        return point, tangent

    def _at_segment(self, segment: int, amount: float) -> tuple[Point, Point]:
        if self._source_curve is not None:
            assert self._point_transform is not None
            point, tangent = self._source_curve._at_segment(segment, amount)
            transformed_point = self._point_transform(point)
            origin = self._point_transform((0.0, 0.0))
            transformed_tangent = self._point_transform(tangent)
            result_tangent = (
                transformed_tangent[0] - origin[0],
                transformed_tangent[1] - origin[1],
            )
            if not all(isfinite(value) for value in (*transformed_point, *result_tangent)):
                raise ValueError("transformed spline evaluation must remain finite")
            return transformed_point, result_tangent
        return self._segment_value(self._segment_points(segment), amount)

    def _normalized_parameter(self, t: float) -> float:
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise TypeError("t must be a real number")
        value = float(t)
        if not isfinite(value):
            raise ValueError("t must be finite")
        if self._closed:
            return value % 1.0
        return min(1.0, max(0.0, value))

    def point_at(self, t: float) -> Point:
        """Evaluate the curve at a normalized parameter.

        Args:
            t (float): Curve parameter. Open curves clamp to ``[0, 1]``; closed
                curves wrap periodically.

        Returns:
            Point: The interpolated 2D point.

        Raises:
            TypeError: If ``t`` is not a real number.
            ValueError: If ``t`` is not finite or evaluation is non-finite.
        """
        parameter = self._normalized_parameter(t)
        scaled = parameter * self._segment_count
        segment = min(int(scaled), self._segment_count - 1)
        amount = 1.0 if parameter == 1.0 else scaled - segment
        return self._at_segment(segment, amount)[0]

    def tangent_at(self, t: float) -> Point:
        """Return the normalized tangent at a curve parameter.

        Args:
            t (float): Normalized curve parameter.

        Returns:
            Point: Unit tangent vector.

        Raises:
            TypeError: If ``t`` is not a real number.
            ValueError: If ``t`` is non-finite or the tangent is undefined.
        """
        parameter = self._normalized_parameter(t)
        scaled = parameter * self._segment_count
        segment = min(int(scaled), self._segment_count - 1)
        amount = 1.0 if parameter == 1.0 else scaled - segment
        tangent = self._at_segment(segment, amount)[1]
        magnitude = hypot(*tangent)
        if magnitude == 0.0:
            raise ValueError("spline tangent is undefined at this parameter")
        return tangent[0] / magnitude, tangent[1] / magnitude

    def _build_arc_table(self) -> None:
        parameters = [0.0]
        points = [self.point_at(0.0)]
        tolerance = self._tolerance

        def flatness(start: Point, middle: Point, end: Point) -> float:
            dx, dy = end[0] - start[0], end[1] - start[1]
            chord_length = hypot(dx, dy)
            if chord_length == 0:
                return _distance(start, middle)
            return abs(dx * (start[1] - middle[1]) - (start[0] - middle[0]) * dy) / chord_length

        def subdivide(
            segment: int,
            u0: float,
            p0: Point,
            u1: float,
            p1: Point,
            depth: int,
        ) -> None:
            span = u1 - u0
            quarter = u0 + span * 0.25
            middle = u0 + span * 0.5
            three_quarter = u0 + span * 0.75
            q1 = self._at_segment(segment, quarter)[0]
            pm = self._at_segment(segment, middle)[0]
            q3 = self._at_segment(segment, three_quarter)[0]
            deviation = max(
                flatness(p0, q1, p1),
                flatness(p0, pm, p1),
                flatness(p0, q3, p1),
            )
            # Flatness alone misses nonlinear speed along collinear segments.
            parameter_error = max(
                _distance(q1, _mix(p0, p1, 0.25)),
                _distance(pm, _mix(p0, p1, 0.5)),
                _distance(q3, _mix(p0, p1, 0.75)),
            )
            polygon_length = (
                _distance(p0, q1) + _distance(q1, pm)
                + _distance(pm, q3) + _distance(q3, p1)
            )
            if (
                depth < 20
                and (
                    deviation > tolerance
                    or parameter_error > tolerance
                    or polygon_length - _distance(p0, p1) > tolerance
                )
            ):
                subdivide(segment, u0, p0, middle, pm, depth + 1)
                subdivide(segment, middle, pm, u1, p1, depth + 1)
                return
            global_t = (segment + u1) / self._segment_count
            if global_t > parameters[-1]:
                parameters.append(global_t)
                points.append(p1)

        for segment in range(self._segment_count):
            start = self._at_segment(segment, 0.0)[0]
            end = self._at_segment(segment, 1.0)[0]
            subdivide(segment, 0.0, start, 1.0, end, 0)

        cumulative = [0.0]
        for first, second in zip(points, points[1:]):
            next_length = cumulative[-1] + _distance(first, second)
            if not isfinite(next_length):
                raise ValueError("spline length must remain finite")
            cumulative.append(next_length)
        self._sample_parameters = tuple(parameters)
        self._sampled_points = tuple(points)
        self._cumulative_lengths = tuple(cumulative)
        self._length = cumulative[-1]

    def _parameter_at_distance(self, distance: float) -> float:
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise TypeError("distance must be a real number")
        value = float(distance)
        if not isfinite(value):
            raise ValueError("distance must be finite")
        if self._closed:
            value %= self._length
        else:
            value = min(self._length, max(0.0, value))
        index = bisect_left(self._cumulative_lengths, value)
        if index <= 0:
            return self._sample_parameters[0]
        if index >= len(self._cumulative_lengths):
            return self._sample_parameters[-1]
        before = self._cumulative_lengths[index - 1]
        after = self._cumulative_lengths[index]
        fraction = 0.0 if after == before else (value - before) / (after - before)
        return (
            self._sample_parameters[index - 1]
            + (self._sample_parameters[index] - self._sample_parameters[index - 1]) * fraction
        )

    def point_at_distance(self, distance: float) -> Point:
        """Evaluate the point at an approximate distance along the curve.

        Args:
            distance (float): Arc distance from the start. Closed curves wrap; open
                curves clamp to the curve extent.

        Returns:
            Point: The corresponding 2D point.

        Raises:
            TypeError: If ``distance`` is not a real number.
            ValueError: If ``distance`` is non-finite.
        """
        return self.point_at(self._parameter_at_distance(distance))

    def tangent_at_distance(self, distance: float) -> Point:
        """Return the unit tangent at an approximate arc distance.

        Args:
            distance (float): Arc distance from the start.

        Returns:
            Point: Unit tangent vector at that distance.

        Raises:
            TypeError: If ``distance`` is not a real number.
            ValueError: If ``distance`` is non-finite or the tangent is undefined.
        """
        return self.tangent_at(self._parameter_at_distance(distance))


__all__ = ["CatmullRomSpline", "Point"]
