from __future__ import annotations

from math import atan2, degrees, isfinite
from collections.abc import Mapping

from ..components import (
    Collider,
    Script,
    requires_components,
)
from ..physics import BodyType, PhysicsBody, PhysicsTarget
from ..inspector import inspector_field


def _approach(current: float, target: float, amount: float) -> float:
    if current < target:
        return min(current + amount, target)
    return max(current - amount, target)


@requires_components(Collider)
@requires_components(PhysicsBody, auto_add=True)
class PlatformerMotor(Script):
    """Control a dynamic body with ground, slope, and buffered-jump handling.

    Requires a Collider and automatically adds a PhysicsBody when absent.
    """
    speed: float = inspector_field(240.0, minimum=0, tooltip="Movement speed along the ground, in pixels per second.")
    acceleration: float = inspector_field(1800.0, minimum=0.001, tooltip="Speed gained per second while grounded.")
    air_acceleration: float = inspector_field(900.0, minimum=0.001, tooltip="Horizontal speed change per second while airborne.")
    deceleration: float = inspector_field(2200.0, minimum=0.001, tooltip="Speed lost per second when grounded with no movement input.")
    jump_speed: float = inspector_field(520.0, minimum=0, tooltip="Upward velocity applied to each jump, in pixels per second.")
    max_fall_speed: float = inspector_field(900.0, minimum=0.001, tooltip="Maximum downward velocity while airborne.")
    gravity_scale: float = inspector_field(1.0, minimum=0, tooltip="Multiplier applied to the scene's gravity.")
    max_slope: float = inspector_field(50.0, minimum=0, maximum=89.9, tooltip="Maximum walkable slope angle in degrees.")
    ground_snap_distance: float = inspector_field(8.0, minimum=0, tooltip="Ground support recovery distance in pixels. Zero disables snapping.")
    coyote_time: float = inspector_field(0.1, minimum=0, tooltip="Seconds after leaving ground during which a ground jump is still allowed.")
    jump_buffer_time: float = inspector_field(0.1, minimum=0, tooltip="Seconds a jump request waits for landing.")
    allow_double_jump: bool = inspector_field(False, tooltip="Allow one extra airborne jump. Resets on landing.")

    def __init__(
        self,
        speed: float = 240,
        *,
        acceleration: float = 1800,
        air_acceleration: float = 900,
        deceleration: float = 2200,
        jump_speed: float = 520,
        max_fall_speed: float = 900,
        gravity_scale: float = 1.0,
        max_slope: float = 50,
        ground_snap_distance: float = 8,
        coyote_time: float = 0.1,
        jump_buffer_time: float = 0.1,
        allow_double_jump: bool = False,
        enabled: bool = True,
    ) -> None:
        """Configure ground movement, jumping, and support detection.

        Args:
            speed: Maximum horizontal movement speed.
            acceleration: Grounded velocity gain per second.
            air_acceleration: Airborne horizontal velocity gain per second.
            deceleration: Grounded speed loss when there is no input.
            jump_speed: Upward velocity applied on a jump.
            max_fall_speed: Maximum downward speed.
            gravity_scale: Multiplier for scene gravity.
            max_slope: Maximum walkable slope in degrees.
            ground_snap_distance: Support recovery distance; zero disables it.
            coyote_time: Post-grounding interval in which a ground jump is
                still permitted.
            jump_buffer_time: Time a jump request waits for a valid jump.
            allow_double_jump: Permit one extra airborne jump before landing.
            enabled: Whether the script is enabled.

        Raises:
            ValueError: A movement parameter is outside its valid range.
        """
        super().__init__(enabled=enabled)
        if speed < 0:
            raise ValueError("speed must not be negative")
        if acceleration <= 0 or air_acceleration <= 0:
            raise ValueError("acceleration must be positive")
        if deceleration <= 0:
            raise ValueError("deceleration must be positive")
        if jump_speed < 0:
            raise ValueError("jump_speed must not be negative")
        if max_fall_speed <= 0:
            raise ValueError("max_fall_speed must be positive")
        if gravity_scale < 0:
            raise ValueError("gravity_scale must not be negative")
        if not 0 <= max_slope < 90:
            raise ValueError("max_slope must be between 0 and 90")
        if not isfinite(ground_snap_distance) or ground_snap_distance < 0:
            raise ValueError("ground_snap_distance must be finite and non-negative")
        if coyote_time < 0 or jump_buffer_time < 0:
            raise ValueError("jump timing must not be negative")
        self.speed = speed
        self.acceleration = acceleration
        self.air_acceleration = air_acceleration
        self.deceleration = deceleration
        self.jump_speed = jump_speed
        self.max_fall_speed = max_fall_speed
        self.gravity_scale = gravity_scale
        self.max_slope = max_slope
        self.ground_snap_distance = ground_snap_distance
        self.coyote_time = coyote_time
        self.jump_buffer_time = jump_buffer_time
        self.allow_double_jump = allow_double_jump
        self._horizontal_intent = 0.0
        self._jump_buffer_remaining = 0.0
        self._coyote_remaining = 0.0
        self._body: PhysicsBody | None = None
        self._grounded = False
        self._ground_objects: tuple[PhysicsTarget, ...] = ()
        self._ground_normal = (0.0, 1.0)
        self._jumping = False
        self._air_jump_used = False
        self._ground_object: PhysicsTarget | None = None
        self._ground_velocity = (0.0, 0.0)
        self._air_ground_velocity = (0.0, 0.0)

    @property
    def ground_velocity(self) -> tuple[float, float]:
        """Velocity of the current supporting surface."""
        return self._ground_velocity

    @property
    def relative_velocity(self) -> tuple[float, float]:
        """Player velocity relative to the current supporting surface."""
        x, y = self.velocity
        gx, gy = self._ground_velocity
        return x - gx, y - gy

    @property
    def velocity(self) -> tuple[float, float]:
        """Current body velocity, or zero before the body is attached."""
        if self._body is None:
            return (0, 0)
        velocity = self._body.velocity
        return velocity.x, velocity.y

    @property
    def grounded(self) -> bool:
        """Whether ground support was found in the most recent update."""
        return self._grounded

    @property
    def falling(self) -> bool:
        """Whether the motor is airborne with downward velocity."""
        return not self._grounded and self.velocity[1] < 0

    @property
    def ground_objects(self) -> tuple[PhysicsTarget, ...]:
        """Objects or tile layers currently supporting the motor."""
        return self._ground_objects

    @property
    def ground_normal(self) -> tuple[float, float]:
        """Normal vector of the selected supporting surface."""
        return self._ground_normal

    @property
    def ground_angle(self) -> float:
        """Clockwise surface angle in degrees, matching game-object rotation."""
        return degrees(atan2(self._ground_normal[0], self._ground_normal[1]))

    def on_attach(self) -> None:
        """Obtain a dynamic body and configure its gravity and rotation."""
        self._body = self._require_dynamic_body()
        self._body.gravity_scale = self.gravity_scale
        self._body.fixed_rotation = True

    def move(self, horizontal: float) -> None:
        """Set horizontal intent, clamped to the interval from -1 to 1."""
        self._horizontal_intent = max(-1.0, min(1.0, horizontal))

    def jump(self) -> None:
        """Queue a jump request for the configured buffer interval."""
        self._jump_buffer_remaining = self.jump_buffer_time

    def stop(self) -> None:
        """Clear pending horizontal movement intent."""
        self._horizontal_intent = 0

    def update(self, delta_time: float) -> None:
        """Update support, horizontal motion, jump timing, and body velocity."""
        body = self._body
        owner = self.owner
        if (
            body is None
            or owner is None
            or owner.scene is None
            or not body.enabled
        ):
            return
        collider = owner.collider
        if collider is None:
            raise RuntimeError("platformer motor needs a collider")
        current_x, current_y = body.velocity
        previous_grounded = self._grounded
        previous_velocity = self._ground_velocity
        if current_y - self._air_ground_velocity[1] <= 0:
            self._jumping = False
        if self._jumping:
            ground_objects, normal = (), (0.0, 1.0)
        else:
            ground_objects, normal = owner.scene.physics.ground_contacts(
                collider,
                max_slope=self.max_slope,
            )
            if ground_objects or self._grounded:
                support_objects, support_normal = owner.scene.physics._snap_to_ground(
                    collider,
                    body,
                    distance=self.ground_snap_distance,
                    max_slope=self.max_slope,
                    snap=not ground_objects,
                    preferred_normal=self._ground_normal if self._grounded else None,
                )
                if support_objects:
                    ground_objects = tuple(dict.fromkeys((*ground_objects, *support_objects)))
                    normal = support_normal
        self._ground_objects = ground_objects
        self._ground_normal = normal
        self._grounded = bool(ground_objects)
        if self._grounded:
            if not any(target is self._ground_object for target in ground_objects):
                self._ground_object = min(
                    ground_objects,
                    key=lambda target: str(getattr(target, "scene_id", None) or getattr(target, "id", "")),
                )
            assert self._ground_object is not None
            self._ground_velocity = owner.scene.physics.ground_velocity(collider, self._ground_object)
            self._air_ground_velocity = self._ground_velocity
            self._coyote_remaining = self.coyote_time
            self._air_jump_used = False
        else:
            self._ground_object = None
            self._ground_velocity = (0.0, 0.0)
            self._coyote_remaining = max(
                0,
                self._coyote_remaining - delta_time,
            )

        target_speed = self._horizontal_intent * self.speed
        if self._horizontal_intent == 0 and self._grounded:
            rate = self.deceleration
        else:
            rate = (
                self.acceleration
                if self._grounded
                else self.air_acceleration
            )
        if self._grounded:
            gx, gy = self._ground_velocity
            old_gx, old_gy = previous_velocity if previous_grounded else self._ground_velocity
            tangent_x, tangent_y = normal[1], -normal[0]
            along_ground = _approach(
                (current_x - old_gx) * tangent_x + (current_y - old_gy) * tangent_y,
                target_speed,
                rate * delta_time,
            )
            current_x = gx + along_ground * tangent_x
            current_y = gy + along_ground * tangent_y
        else:
            current_x = _approach(
                current_x,
                target_speed + self._air_ground_velocity[0],
                rate * delta_time,
            )

        ground_jump = self._grounded or self._coyote_remaining > 0
        air_jump = self.allow_double_jump and not self._air_jump_used
        if self._jump_buffer_remaining > 0 and (ground_jump or air_jump):
            if not ground_jump:
                self._air_jump_used = True
            current_y = self.jump_speed + (self._air_ground_velocity[1] if ground_jump else 0)
            self._jump_buffer_remaining = 0
            self._coyote_remaining = 0
            self._grounded = False
            self._ground_objects = ()
            self._ground_object = None
            self._ground_velocity = (0.0, 0.0)
            self._ground_normal = (0.0, 1.0)
            self._jumping = True
        else:
            self._jump_buffer_remaining = max(
                0,
                self._jump_buffer_remaining - delta_time,
            )
        body.velocity = (
            current_x,
            current_y if self._grounded else max(current_y, -self.max_fall_speed),
        )
        self._horizontal_intent = 0

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

    def save_state(self) -> Mapping[str, object]:
        """Serialize movement and ground-support state for checkpoints."""
        from ..nodes import GameObject

        def identifier(target: PhysicsTarget) -> str:
            value = target.scene_id if isinstance(target, GameObject) else target.id
            if value is None:
                raise ValueError("Ground support has no persistent identifier")
            return value

        return {
            "horizontal_intent": self._horizontal_intent,
            "jump_buffer": self._jump_buffer_remaining,
            "coyote": self._coyote_remaining,
            "grounded": self._grounded, "jumping": self._jumping,
            "air_jump_used": self._air_jump_used,
            "ground_normal": list(self._ground_normal),
            "ground_velocity": list(self._ground_velocity),
            "air_ground_velocity": list(self._air_ground_velocity),
            "ground_objects": [identifier(target) for target in self._ground_objects],
            "ground_object": identifier(self._ground_object) if self._ground_object is not None else None,
        }

    def load_state(self, state: Mapping[str, object]) -> None:
        """Restore validated movement and ground-support checkpoint state."""
        def number(name: str) -> float:
            value = state.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
                raise ValueError(f"Invalid motor checkpoint {name}")
            return float(value)

        def boolean(name: str) -> bool:
            value = state.get(name)
            if not isinstance(value, bool):
                raise ValueError(f"Invalid motor checkpoint {name}")
            return value

        def pair(name: str) -> tuple[float, float]:
            value = state.get(name)
            if not isinstance(value, list) or len(value) != 2 or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in value
            ):
                raise ValueError(f"Invalid motor checkpoint {name}")
            return float(value[0]), float(value[1])

        targets: dict[str, PhysicsTarget] = {
            str(obj.scene_id): obj for obj in self.scene.get_children(recursive=True)
        }
        from ..tilemap import TileLayer
        targets.update({layer.id: layer for layer in self.scene.layers if isinstance(layer, TileLayer)})

        def resolve(identifier: object) -> PhysicsTarget:
            if not isinstance(identifier, str) or identifier not in targets:
                raise ValueError("Invalid motor checkpoint ground support")
            return targets[identifier]

        identifiers = state.get("ground_objects")
        if not isinstance(identifiers, list):
            raise ValueError("Invalid motor checkpoint ground_objects")
        self._ground_objects = tuple(resolve(identifier) for identifier in identifiers)
        support = state.get("ground_object")
        self._ground_object = resolve(support) if support is not None else None
        self._horizontal_intent = number("horizontal_intent")
        self._jump_buffer_remaining = number("jump_buffer")
        self._coyote_remaining = number("coyote")
        self._grounded = boolean("grounded")
        self._jumping = boolean("jumping")
        self._air_jump_used = boolean("air_jump_used")
        self._ground_normal = pair("ground_normal")
        self._ground_velocity = pair("ground_velocity")
        self._air_ground_velocity = pair("air_ground_velocity")
