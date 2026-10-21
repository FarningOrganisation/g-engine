from __future__ import annotations

from collections.abc import Mapping
from math import hypot, isfinite
from typing import TYPE_CHECKING

from ..components import (
    Collider,
    Script,
    requires_components,
)
from ..physics import BodyType, PhysicsBody
from ..inspector import inspector_field

if TYPE_CHECKING:
    from ..math import Vec2
    from ..nodes import Node


@requires_components(Collider)
@requires_components(PhysicsBody, auto_add=True)
class TopDownMotor(Script):
    """Accelerate a dynamic body toward a normalized top-down move intent.

    A PhysicsBody is added automatically when needed; a Collider is required.
    """
    speed: float = inspector_field(200.0, minimum=0, tooltip="Maximum movement speed in pixels per second.")
    acceleration: float = inspector_field(1200.0, minimum=0.001, tooltip="Movement speed gained per second.")
    deceleration: float = inspector_field(1600.0, minimum=0.001, tooltip="Movement speed lost per second with no input.")

    def __init__(
        self,
        speed: float = 200,
        *,
        acceleration: float = 1200,
        deceleration: float = 1600,
        enabled: bool = True,
    ) -> None:
        """Configure movement speed, acceleration, and deceleration.

        Args:
            speed: Maximum speed in pixels per second.
            acceleration: Positive velocity gain per second with input.
            deceleration: Positive velocity loss per second without input.
            enabled: Whether the script is enabled.

        Raises:
            ValueError: A speed or rate is outside its allowed range.
        """
        super().__init__(enabled=enabled)
        if speed < 0:
            raise ValueError("speed must not be negative")
        if acceleration <= 0:
            raise ValueError("acceleration must be positive")
        if deceleration <= 0:
            raise ValueError("deceleration must be positive")
        self.speed = speed
        self.acceleration = acceleration
        self.deceleration = deceleration
        self._intent = (0.0, 0.0)
        self._body: PhysicsBody | None = None

    @property
    def velocity(self) -> tuple[float, float]:
        """Current body velocity, or zero before the body is attached."""
        if self._body is None:
            return (0, 0)
        velocity = self._body.velocity
        return velocity.x, velocity.y

    @property
    def moving(self) -> bool:
        """Whether the current body velocity is nonzero."""
        return self.velocity != (0, 0)

    def on_attach(self) -> None:
        """Obtain a dynamic body and disable gravity and rotation."""
        self._body = self._require_dynamic_body()
        self._body.gravity_scale = 0
        self._body.fixed_rotation = True

    def move(self, direction: tuple[float, float]) -> None:
        """Set normalized directional intent for the next update."""
        x, y = direction
        length = hypot(x, y)
        if length > 1:
            x /= length
            y /= length
        self._intent = (x, y)

    def move_towards(
        self,
        target: Node | Vec2 | tuple[float, float],
    ) -> None:
        """Set movement intent toward a world-space node or point."""
        if self.owner is None:
            raise RuntimeError("motor is not attached")
        direction = self.owner.direction_to(target)
        self.move((direction.x, direction.y))

    def stop(self) -> None:
        """Clear pending directional intent."""
        self._intent = (0, 0)

    def save_state(self) -> Mapping[str, object]:
        """Return pending movement intent for a runtime checkpoint."""
        return {"intent": list(self._intent)}

    def load_state(self, state: Mapping[str, object]) -> None:
        """Restore validated pending movement intent."""
        intent = state.get("intent")
        if (not isinstance(intent, list) or len(intent) != 2 or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and isfinite(value) for value in intent)):
            raise ValueError("Invalid TopDownMotor checkpoint intent")
        self.move((float(intent[0]), float(intent[1])))

    def update(self, delta_time: float) -> None:
        """Approach the target velocity and consume the pending intent."""
        body = self._body
        if body is None or not body.enabled:
            return
        target_x = self._intent[0] * self.speed
        target_y = self._intent[1] * self.speed
        current_x, current_y = body.velocity
        rate = (
            self.acceleration
            if self._intent != (0, 0)
            else self.deceleration
        )
        amount = rate * delta_time
        delta_x = target_x - current_x
        delta_y = target_y - current_y
        distance = hypot(delta_x, delta_y)
        if distance <= amount or distance == 0:
            body.velocity = (target_x, target_y)
        else:
            body.velocity = (
                current_x + delta_x / distance * amount,
                current_y + delta_y / distance * amount,
            )
        self._intent = (0, 0)

    def _require_dynamic_body(self) -> PhysicsBody:
        """Return the attached dynamic body, adding one if absent."""
        owner = self.owner
        if owner is None:
            raise RuntimeError("motor is not attached")
        body = owner.get_component(PhysicsBody)
        if body is None:
            body = owner.add_physics(BodyType.DYNAMIC)
        if body.body_type is not BodyType.DYNAMIC:
            raise ValueError("movement motors need a dynamic body")
        return body
