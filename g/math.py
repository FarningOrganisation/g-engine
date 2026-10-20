"""Vector math backed by pyglet."""

from __future__ import annotations

from collections.abc import Iterator
from math import isfinite
from typing import TypeAlias, overload

from pyglet.math import Vec2 as PygletVec2, Vec3, Vec4


Vector: TypeAlias = "Vec2 | PygletVec2 | tuple[float, float]"
Operand: TypeAlias = "Vector | float"


class Vec2:
    """Mutable two-dimensional vector with pyglet-compatible operations.

    Args:
        x (float): Initial horizontal component.
        y (float): Initial vertical component.
    """

    __slots__ = ("_value",)

    def __init__(self, x: float = 0.0, y: float = 0.0) -> None:
        """Initialize the vector components."""
        self._value = PygletVec2(x, y)

    @property
    def x(self) -> float:
        """Horizontal component."""
        return self._value.x

    @x.setter
    def x(self, value: float) -> None:
        """Set the horizontal component."""
        self._value = PygletVec2(value, self.y)

    @property
    def y(self) -> float:
        """Vertical component."""
        return self._value.y

    @y.setter
    def y(self, value: float) -> None:
        """Set the vertical component."""
        self._value = PygletVec2(self.x, value)

    def copy(self) -> Vec2:
        """Return an independent vector with the same components."""
        return Vec2(self.x, self.y)

    def __deepcopy__(self, memo: dict[int, object]) -> Vec2:
        result = self.copy()
        memo[id(self)] = result
        return result

    def __iter__(self) -> Iterator[float]:
        """Iterate over the horizontal and vertical components."""
        return iter(self._value)

    def __len__(self) -> int:
        """Return the number of components, which is always two."""
        return 2

    @overload
    def __getitem__(self, index: int) -> float: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[float, ...]: ...

    def __getitem__(self, index: int | slice) -> float | tuple[float, ...]:
        """Return a component or slice using sequence indexing."""
        return self._value[index]

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return f"Vec2(x={self.x!r}, y={self.y!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Vec2):
            return self._value == other._value
        if isinstance(other, (PygletVec2, tuple)):
            return self._value == other
        return NotImplemented

    def __lt__(self, other: Vector) -> bool:
        return self._value < (other[0], other[1])

    def __getattr__(self, attrs: str) -> Vec2 | Vec3 | Vec4:
        value = self._value.__getattr__(attrs)
        if isinstance(value, PygletVec2):
            return Vec2(*value)
        return value

    @staticmethod
    def zero():
        """Return a vector whose components are both zero."""
        return Vec2(0, 0)

    @staticmethod
    def _operand(value: Operand) -> PygletVec2 | tuple[float, float] | float:
        return value._value if isinstance(value, Vec2) else value

    def __add__(self, other: Operand) -> Vec2:
        return Vec2(*self._value.__add__(self._operand(other)))

    def __radd__(self, other: Operand) -> Vec2:
        return Vec2(*self._value.__radd__(self._operand(other)))

    def __sub__(self, other: Operand) -> Vec2:
        return Vec2(*self._value.__sub__(self._operand(other)))

    def __rsub__(self, other: Operand) -> Vec2:
        return Vec2(*self._value.__rsub__(self._operand(other)))

    def __mul__(self, scalar: Operand) -> Vec2:
        return Vec2(*self._value.__mul__(self._operand(scalar)))

    def __rmul__(self, scalar: Operand) -> Vec2:
        return Vec2(*self._value.__rmul__(self._operand(scalar)))

    def __truediv__(self, scalar: Operand) -> Vec2:
        return Vec2(*self._value.__truediv__(self._operand(scalar)))

    def __rtruediv__(self, scalar: Operand) -> Vec2:
        return Vec2(*self._value.__rtruediv__(self._operand(scalar)))

    def __floordiv__(self, scalar: Operand) -> Vec2:
        return Vec2(*self._value.__floordiv__(self._operand(scalar)))

    def __rfloordiv__(self, scalar: Operand) -> Vec2:
        return Vec2(*self._value.__rfloordiv__(self._operand(scalar)))

    def __mod__(self, other: Operand) -> Vec2:
        return Vec2(*self._value.__mod__(self._operand(other)))

    def __pow__(self, other: Operand) -> Vec2:
        return Vec2(*self._value.__pow__(self._operand(other)))

    def __abs__(self) -> Vec2:
        return Vec2(*self._value.__abs__())

    def __neg__(self) -> Vec2:
        return Vec2(*self._value.__neg__())

    def __round__(self, n_digits: int | None = None) -> Vec2:
        return Vec2(*self._value.__round__(n_digits))

    def __ceil__(self) -> Vec2:
        return Vec2(*self._value.__ceil__())

    def __floor__(self) -> Vec2:
        return Vec2(*self._value.__floor__())

    def __trunc__(self) -> Vec2:
        return Vec2(*self._value.__trunc__())

    @staticmethod
    def from_heading(heading: float, length: float = 1.0) -> Vec2:
        """Construct a vector from a heading in radians and a magnitude.

        Args:
            heading (float): Direction in radians.
            length (float): Magnitude of the resulting vector.

        Returns:
            Vec2: The vector pointing in the requested direction.
        """
        return Vec2(*PygletVec2.from_heading(heading, length))

    @staticmethod
    def from_polar(angle: float, length: float = 1.0) -> Vec2:
        """Construct a vector from a polar angle in radians and a magnitude.

        Args:
            angle (float): Polar angle in radians.
            length (float): Magnitude of the resulting vector.

        Returns:
            Vec2: The resulting vector.
        """
        return Vec2(*PygletVec2.from_polar(angle, length))

    def normalize(self) -> Vec2:
        """Return a unit vector in the same direction."""
        return Vec2(*self._value.normalize())

    def length(self) -> float:
        """Return the vector magnitude."""
        return self._value.length()

    def length_squared(self) -> float:
        """Return the squared vector magnitude."""
        return self._value.length_squared()

    def heading(self) -> float:
        """Return the direction angle in radians."""
        return self._value.heading()

    def dot(self, other: Vector) -> float:
        """Return the dot product with another two-dimensional vector."""
        return self._value.dot((other[0], other[1]))

    def distance(self, other: Vector) -> float:
        """Return the Euclidean distance to another vector."""
        return (self - other).length()

    def rotate(self, angle: float) -> Vec2:
        """Rotate counter-clockwise by an angle in radians."""
        return Vec2(*self._value.rotate(angle))

    def lerp(self, other: Vector, amount: float) -> Vec2:
        """Interpolate toward another vector by ``amount``."""
        return Vec2(*self._value.lerp((other[0], other[1]), amount))

    def step(self, edge: Vector) -> Vec2:
        """Return component-wise step results against the supplied edge."""
        return Vec2(*self._value.step((edge[0], edge[1])))

    def clamp(self, min_val: Operand, max_val: Operand) -> Vec2:
        """Clamp each component between corresponding minimum and maximum values."""
        return Vec2(*self._value.clamp(self._operand(min_val), self._operand(max_val)))

    def reflect(self, vector: Vector) -> Vec2:
        """Reflect across a surface normal, which need not be unit length.

        Args:
            vector (Vector): Surface normal.

        Returns:
            Vec2: The reflected vector.

        Raises:
            ValueError: If the normal is zero.
        """
        normal = Vec2(*vector)
        if not normal:
            raise ValueError("reflection normal must not be zero")
        return Vec2(*self._value.reflect(normal._value.normalize()))

    def project(self, direction: Vector) -> Vec2:
        """Project onto an infinite line through the origin.

        Args:
            direction (Vector): Direction of the line.

        Returns:
            Vec2: The vector projection.

        Raises:
            ValueError: If ``direction`` is zero.
        """
        axis = Vec2(*direction)
        if not axis:
            raise ValueError("projection direction must not be zero")
        axis = axis.normalize()
        return axis * self.dot(axis)

    def project_on_line(self, start: Vector, end: Vector) -> Vec2:
        """Return the closest point on the infinite line through two points.

        Args:
            start (Vector): A point on the line.
            end (Vector): Another point on the line.

        Returns:
            Vec2: The point on the line nearest to this vector.
        """
        origin = Vec2(*start)
        return origin + (self - origin).project(Vec2(*end) - origin)

    def perpendicular(self) -> Vec2:
        """Return a vector rotated 90 degrees counter-clockwise."""
        return Vec2(-self.y, self.x)

    def cross(self, other: Vector) -> float:
        """Return the signed scalar two-dimensional cross product."""
        return self.x * other[1] - self.y * other[0]

    def move_towards(self, target: Vector, max_distance: float) -> Vec2:
        """Return a point moved toward a target without overshooting.

        Args:
            target (Vector): Destination vector.
            max_distance (float): Maximum distance to move; must be finite and non-negative.

        Returns:
            Vec2: The destination if it is within range, otherwise the stepped vector.

        Raises:
            ValueError: If ``max_distance`` is negative or non-finite.
        """
        if not isfinite(max_distance) or max_distance < 0:
            raise ValueError("max_distance must be finite and non-negative")
        destination = Vec2(*target)
        offset = destination - self
        distance = offset.length()
        if distance <= max_distance:
            return destination
        return self + offset * (max_distance / distance)

__all__ = ["Vec2"]
