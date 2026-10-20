"""PlatformMovement game behavior."""

from math import isfinite
from collections.abc import Mapping

from g import BodyType, CollisionContact, PhysicsBody, Script, inspector_field, requires_components, scene_component, PlatformerMotor


@requires_components(PhysicsBody)
@scene_component("platform_movement.PlatformMovement")
class PlatformMovement(Script):
    """Move right from the starting position, then return, at a fixed speed."""
    trigger_on_player_touch: bool = False

    distance: float = inspector_field(
        100.0, minimum=0,
        tooltip="Horizontal travel from the starting position, in world pixels.",
    )
    velocity: float = inspector_field(
        60.0, minimum=0,
        tooltip="Travel speed in pixels per second. Zero pauses movement.",
    )

    def on_ready(self) -> None:
        self._validate_parameters()
        self._triggered = False
        self.physics_body = self.game_object.require_component(PhysicsBody)
        if self.physics_body.body_type is not BodyType.KINEMATIC:
            raise ValueError("PlatformMovement requires a kinematic PhysicsBody")
        self._start_x = self.game_object.world_position.x
        self._direction = 1
        # Physics runs before scripts; the first update prepares the next step.
        self.physics_body.velocity = (0, 0)
        self.physics_body.angular_velocity = 0

    def update(self, delta_time: float) -> None:
        self._validate_parameters()
        if self.trigger_on_player_touch and not self._triggered:
            return
        if not isfinite(delta_time) or delta_time < 0:
            raise ValueError("PlatformMovement delta_time must be finite and non-negative")
        if self.distance == 0 or self.velocity == 0:
            self.physics_body.velocity = (0, 0)
            return
        if delta_time == 0:
            return
        x = self.game_object.world_position.x
        target = self._start_x + self.distance if self._direction > 0 else self._start_x
        remaining = (target - x) * self._direction
        if remaining <= 1e-7:
            self._direction *= -1
            target = self._start_x + self.distance if self._direction > 0 else self._start_x
            remaining = (target - x) * self._direction
        # Limit the final fixed step so the body reaches, rather than overshoots, the endpoint.
        speed = min(self.velocity, max(0.0, remaining) / delta_time)
        self.physics_body.velocity = (self._direction * speed, 0)

    def _validate_parameters(self) -> None:
        if not isfinite(self.distance) or self.distance < 0:
            raise ValueError("PlatformMovement distance must be finite and non-negative")
        if not isfinite(self.velocity) or self.velocity < 0:
            raise ValueError("PlatformMovement velocity must be finite and non-negative")

    def on_collision(self, contact: CollisionContact) -> None:
        other = contact.other
        print(other)
        if other.name != "Player":
            return

        pm = other.require_component(PlatformerMotor)
    
        self._triggered = True

    def save_state(self) -> Mapping[str, object]:
        return {
            "triggered": self._triggered,
            "direction": self._direction,
            "start_x": self._start_x,
        }

    def load_state(self, state: Mapping[str, object]) -> None:
        triggered, direction, start_x = state.get("triggered"), state.get("direction"), state.get("start_x")
        if (not isinstance(triggered, bool) or not isinstance(direction, (int, float)) or isinstance(direction, bool)
                or direction not in (-1, 1) or not isinstance(start_x, (int, float))
                or isinstance(start_x, bool) or not isfinite(start_x)):
            raise ValueError("Invalid PlatformMovement checkpoint state")
        self._triggered = triggered
        self._direction = int(direction)
        self._start_x = float(start_x)
