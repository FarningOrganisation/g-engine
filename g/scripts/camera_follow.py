from __future__ import annotations

from math import exp, isfinite

from ..components import Script
from ..inspector import inspector_field


class CameraFollow(Script):
    """Move the scene camera toward this object's world position each update.

    Attributes:
        offset_x: Horizontal world-space offset from the target.
        offset_y: Vertical world-space offset from the target.
        smoothing: Exponential response rate; zero moves directly to target.
    """
    offset_x: float = inspector_field(0.0, tooltip="Horizontal camera offset in world pixels.")
    offset_y: float = inspector_field(0.0, tooltip="Vertical camera offset in world pixels.")
    smoothing: float = inspector_field(0.0, minimum=0, tooltip="Exponential response rate. Higher follows faster; zero follows immediately.")

    def update(self, delta_time: float) -> None:
        """Advance the camera toward the target position.

        Args:
            delta_time: Elapsed simulation time in seconds.

        Raises:
            ValueError: Offsets or smoothing are invalid.
        """
        if not all(isfinite(value) for value in (
            self.offset_x, self.offset_y, self.smoothing,
        )) or self.smoothing < 0:
            raise ValueError("CameraFollow offsets must be finite and smoothing non-negative")
        camera = self.scene.camera
        position = self.game_object.world_position
        target_x = position.x + self.offset_x
        target_y = position.y + self.offset_y
        weight = 1 if self.smoothing == 0 else 1 - exp(-self.smoothing * delta_time)
        camera.position = (
            camera.x + (target_x - camera.x) * weight,
            camera.y + (target_y - camera.y) * weight,
        )
