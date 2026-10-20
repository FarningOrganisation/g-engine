from __future__ import annotations

from math import isfinite, radians

from pyglet.math import Mat4, Vec3, Vec4

from .math import Vec2


class Camera:
    """A 2D camera mapping world pixels into a virtual-pixel viewport.

    Position is the world point at the viewport center. Rotation is in degrees;
    positive zoom magnifies the view.
    """

    def __init__(
        self,
        viewport_width: float,
        viewport_height: float,
        *,
        x: float | None = None,
        y: float | None = None,
        zoom: float = 1,
        rotation: float = 0,
    ) -> None:
        """Create a camera with the viewport centered on the world origin.

        Args:
            viewport_width (float): Viewport width in pixels.
            viewport_height (float): Viewport height in pixels.
            x (float | None): Optional world-pixel center coordinate; defaults to half width.
            y (float | None): Optional world-pixel center coordinate; defaults to half height.
            zoom (float): Finite positive scale factor.
            rotation (float): Camera rotation in degrees.

        Raises:
            ValueError: If viewport dimensions are not positive or zoom is
                non-finite or non-positive.
        """
        if viewport_width <= 0 or viewport_height <= 0:
            raise ValueError("viewport dimensions must be positive")
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self._default_position = Vec2(
            viewport_width / 2,
            viewport_height / 2,
        )
        self._position = Vec2(
            self._default_position.x if x is None else x,
            self._default_position.y if y is None else y,
        )
        self._zoom = 1.0
        self.zoom = zoom
        self.rotation = rotation
        self._interpolation_revision = 0

    def reset_interpolation(self) -> None:
        """Mark the next interpolated render as a camera cut."""
        self._interpolation_revision += 1

    @property
    def position(self) -> Vec2:
        """World-pixel coordinates at the center of the viewport."""
        return self._position

    @position.setter
    def position(self, value: Vec2 | tuple[float, float]) -> None:
        """Set the camera center from a vector or ``(x, y)`` pair."""
        self._position = Vec2(*value)

    @property
    def x(self) -> float:
        """World-pixel x-coordinate at the viewport center."""
        return self._position.x

    @x.setter
    def x(self, value: float) -> None:
        """Set the camera center's world-pixel x-coordinate."""
        self._position.x = value

    @property
    def y(self) -> float:
        """World-pixel y-coordinate at the viewport center."""
        return self._position.y

    @y.setter
    def y(self, value: float) -> None:
        """Set the camera center's world-pixel y-coordinate."""
        self._position.y = value

    @property
    def zoom(self) -> float:
        """Camera scale; values above one zoom in."""
        return self._zoom

    @zoom.setter
    def zoom(self, value: float) -> None:
        """Set a finite positive camera scale.

        Raises:
            ValueError: If ``value`` is non-finite or not positive.
        """
        if not isfinite(value) or value <= 0:
            raise ValueError("zoom must be finite and positive")
        self._zoom = value

    @property
    def view_matrix(self) -> Mat4:
        """Return the world-to-screen matrix for the current camera state."""
        center = Vec3(self.viewport_width / 2, self.viewport_height / 2, 0)
        position = Vec3(-self.x, -self.y, 0)
        scale = Vec3(self.zoom, self.zoom, 1)
        return (
            Mat4.from_translation(center)
            @ Mat4.from_rotation(radians(self.rotation), Vec3(0, 0, 1))
            @ Mat4.from_scale(scale)
            @ Mat4.from_translation(position)
        )

    def world_to_screen(
        self, position: Vec2 | tuple[float, float]
    ) -> Vec2:
        """Transform a world-pixel point to viewport coordinates."""
        x, y = position
        transformed = self.view_matrix @ Vec4(x, y, 0, 1)
        return Vec2(transformed.x, transformed.y)

    def screen_to_world(
        self, position: Vec2 | tuple[float, float]
    ) -> Vec2:
        """Transform a viewport point back to world-pixel coordinates."""
        x, y = position
        transformed = ~self.view_matrix @ Vec4(x, y, 0, 1)
        return Vec2(transformed.x, transformed.y)

    def reset(self) -> None:
        """Restore the default center, unit zoom, and zero rotation."""
        self.position = self._default_position
        self.zoom = 1
        self.rotation = 0
        self.reset_interpolation()
