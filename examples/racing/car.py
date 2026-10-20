"""Arcade car movement and player controls."""

from math import cos, hypot, isfinite, radians, sin

from g import (
    Area,
    BodyType,
    CameraFollow,
    Collider,
    GameObject,
    Key,
    NetworkedGameObject,
    ParticleEffect,
    PhysicsBody,
    Script,
    TrailRenderer,
    inspector_field,
    requires_components,
    scene_component,
)


def _move_toward(current: float, target: float, amount: float) -> float:
    if current < target:
        return min(current + amount, target)
    return max(current - amount, target)


@requires_components(PhysicsBody, Collider)
@scene_component("car.Car", display_name="Car")
class Car(Script):
    """Arcade top-down car motor driven through :meth:`drive`."""

    max_forward_speed: float = inspector_field(
        420.0,
        minimum=0,
        tooltip="Maximum forward speed in pixels per second.",
    )
    max_reverse_speed: float = inspector_field(
        160.0,
        minimum=0,
        tooltip="Maximum reverse speed in pixels per second.",
    )
    acceleration: float = inspector_field(
        520.0,
        minimum=0,
        tooltip="Forward or reverse speed gained per second.",
    )
    braking: float = inspector_field(
        900.0,
        minimum=0,
        tooltip="Speed removed per second while braking.",
    )
    coasting: float = inspector_field(
        180.0,
        minimum=0,
        tooltip="Speed removed per second with no throttle.",
    )
    lateral_grip: float = inspector_field(
        720.0,
        minimum=0,
        tooltip="Sideways speed removed per second.",
    )
    steering_speed: float = inspector_field(
        150.0,
        minimum=0,
        tooltip="Maximum steering speed in degrees per second.",
    )

    def __init__(
        self,
        *,
        max_forward_speed: float = 420,
        max_reverse_speed: float = 160,
        acceleration: float = 520,
        braking: float = 900,
        coasting: float = 180,
        lateral_grip: float = 720,
        steering_speed: float = 150,
        enabled: bool = True,
    ) -> None:
        super().__init__(enabled=enabled)
        self.max_forward_speed = max_forward_speed
        self.max_reverse_speed = max_reverse_speed
        self.acceleration = acceleration
        self.braking = braking
        self.coasting = coasting
        self.lateral_grip = lateral_grip
        self.steering_speed = steering_speed
        self._throttle = 0.0
        self._steering = 0.0
        self._braking = False
        self._body: PhysicsBody | None = None
        self._forward_speed = 0.0
        self._lateral_speed = 0.0
        self._grip_multiplier = 1.0

    @property
    def forward_speed(self) -> float:
        return self._forward_speed

    @property
    def lateral_speed(self) -> float:
        return self._lateral_speed

    @property
    def slip(self) -> float:
        return abs(self._lateral_speed) / max(
            abs(self._forward_speed),
            1.0,
        )

    @property
    def grip_multiplier(self) -> float:
        return self._grip_multiplier

    def set_grip_multiplier(self, value: float) -> None:
        if not isfinite(value) or value < 0:
            raise ValueError(
                "car grip multiplier must be finite and non-negative"
            )
        self._grip_multiplier = value

    def on_ready(self) -> None:
        self._validate_parameters()
        body = self.game_object.require_component(PhysicsBody)
        if body.body_type is not BodyType.DYNAMIC:
            raise ValueError("Car requires a dynamic PhysicsBody")
        body.gravity_scale = 0
        body.fixed_rotation = False
        self._body = body

    def drive(
        self,
        throttle: float,
        steering: float,
        *,
        brake: bool = False,
    ) -> None:
        """Set normalized controls for the next update."""
        if not isfinite(throttle) or not isfinite(steering):
            raise ValueError("car controls must be finite")
        self._throttle = max(-1.0, min(1.0, throttle))
        self._steering = max(-1.0, min(1.0, steering))
        self._braking = brake

    def update(self, delta_time: float) -> None:
        controller = self.game_object.get_component(CarController)
        if controller is not None and controller.enabled:
            return
        self._update_motion(delta_time)

    def _update_motion(self, delta_time: float) -> None:
        self._validate_parameters()
        if not isfinite(delta_time) or delta_time < 0:
            raise ValueError("Car delta_time must be finite and non-negative")
        body = self._body
        if body is None or not body.enabled:
            return

        angle = radians(self.game_object.world_rotation)
        forward_x, forward_y = sin(angle), cos(angle)
        right_x, right_y = cos(angle), -sin(angle)
        velocity_x, velocity_y = body.velocity
        forward_speed = (
            velocity_x * forward_x + velocity_y * forward_y
        )
        lateral_speed = velocity_x * right_x + velocity_y * right_y
        self._forward_speed = forward_speed
        self._lateral_speed = lateral_speed

        if self._braking:
            target_speed = 0.0
            speed_change = self.braking
        elif self._throttle > 0:
            target_speed = self._throttle * self.max_forward_speed
            speed_change = self.acceleration
        elif self._throttle < 0:
            target_speed = self._throttle * self.max_reverse_speed
            speed_change = self.acceleration
        else:
            target_speed = 0.0
            speed_change = self.coasting

        forward_speed = _move_toward(
            forward_speed,
            target_speed,
            speed_change * delta_time,
        )
        lateral_speed = _move_toward(
            lateral_speed,
            0.0,
            self.lateral_grip * self._grip_multiplier * delta_time,
        )
        body.velocity = (
            forward_x * forward_speed + right_x * lateral_speed,
            forward_y * forward_speed + right_y * lateral_speed,
        )

        steering_ratio = min(
            1.0,
            abs(forward_speed) / max(1.0, self.max_forward_speed * 0.35),
        )
        travel_direction = -1.0 if forward_speed < 0 else 1.0
        body.angular_velocity = (
            self._steering
            * self.steering_speed
            * steering_ratio
            * travel_direction
        )

        self._throttle = 0.0
        self._steering = 0.0
        self._braking = False

    def _validate_parameters(self) -> None:
        values = {
            "max_forward_speed": self.max_forward_speed,
            "max_reverse_speed": self.max_reverse_speed,
            "acceleration": self.acceleration,
            "braking": self.braking,
            "coasting": self.coasting,
            "lateral_grip": self.lateral_grip,
            "steering_speed": self.steering_speed,
        }
        invalid = next(
            (
                name
                for name, value in values.items()
                if not isfinite(value) or value < 0
            ),
            None,
        )
        if invalid is not None:
            raise ValueError(f"Car {invalid} must be finite and non-negative")


@requires_components(Car)
@scene_component("car.CarController", display_name="Car Controller")
class CarController(Script):
    """Drive a :class:`Car` with WASD or the arrow keys."""

    def on_ready(self) -> None:
        self.accept_input = getattr(self, "accept_input", True)
        self.car = self.game_object.require_component(Car)
        self.network_object = self.game_object.get_component(
            NetworkedGameObject
        )
        self.camera_follow = self.game_object.get_component(CameraFollow)

    def update(self, delta_time: float) -> None:
        if not self.accept_input:
            self.car.drive(0, 0, brake=True)
            self.car._update_motion(delta_time)
            return
        network = self.game.network
        marker = self.network_object
        if marker is not None and network.is_server:
            if self.camera_follow is not None:
                self.camera_follow.enabled = False
            controls = network.input_for(marker)
            throttle = controls.get("throttle", 0.0)
            steering = controls.get("steering", 0.0)
            if not isinstance(throttle, (int, float)):
                throttle = 0.0
            if not isinstance(steering, (int, float)):
                steering = 0.0
            self.car.drive(
                float(throttle),
                float(steering),
                brake=bool(controls.get("brake", False)),
            )
            self.car._update_motion(delta_time)
            return
        if marker is not None and network.is_client:
            local = marker.is_local_owner
            if self.camera_follow is not None:
                self.camera_follow.enabled = local
            if not local:
                return
            throttle = self.input.get_axis("y")
            steering = self.input.get_axis("x")
            brake = self.input.pressed(Key.SPACE)
            if network.state == "connected":
                network.set_input(
                    marker,
                    throttle=throttle,
                    steering=steering,
                    brake=brake,
                )
            # Predict the supported car motor immediately. Authoritative
            # snapshots continuously reconcile its pose and velocity.
            self.car.drive(throttle, steering, brake=brake)
            self.car._update_motion(delta_time)
            return
        self.car.drive(
            self.input.get_axis("y"),
            self.input.get_axis("x"),
            brake=self.input.pressed(Key.SPACE),
        )
        self.car._update_motion(delta_time)


@requires_components(Car)
@scene_component("car.CarEffects", display_name="Car Effects")
class CarEffects(Script):
    minimum_drift_speed: float = inspector_field(
        90.0,
        minimum=0,
        tooltip="Minimum forward speed before drift effects start.",
    )
    minimum_lateral_speed: float = inspector_field(
        45.0,
        minimum=0,
        tooltip="Minimum sideways speed before drift effects start.",
    )
    minimum_slip: float = inspector_field(
        0.18,
        minimum=0,
        tooltip="Minimum lateral-to-forward speed ratio for drifting.",
    )
    oil_grip: float = inspector_field(
        0.0,
        minimum=0,
        maximum=1,
        tooltip="Lateral grip during the oil effect; zero means no grip.",
    )
    oil_duration: float = inspector_field(
        6.0,
        minimum=0.001,
        tooltip="Seconds the oil effect lasts after leaving all oil surfaces.",
    )

    def __init__(
        self,
        *,
        minimum_drift_speed: float = 90,
        minimum_lateral_speed: float = 45,
        minimum_slip: float = 0.18,
        oil_grip: float = 0.0,
        oil_duration: float = 6.0,
        enabled: bool = True,
    ) -> None:
        super().__init__(enabled=enabled)
        self.minimum_drift_speed = minimum_drift_speed
        self.minimum_lateral_speed = minimum_lateral_speed
        self.minimum_slip = minimum_slip
        self.oil_grip = oil_grip
        self.oil_duration = oil_duration
        self._oil_contacts = 0
        self._oil_remaining = 0.0
        self._last_oil_positions: tuple[
            tuple[float, float], ...
        ] | None = None

    def on_ready(self) -> None:
        self._validate_oil()
        self.car = self.game_object.require_component(Car)
        left = self.game_object.require_child("Rear Left Effects")
        right = self.game_object.require_child("Rear Right Effects")
        self._wheels = (left, right)
        self.trails = (
            left.require_component(TrailRenderer),
            right.require_component(TrailRenderer),
        )
        self.sparks = (
            self._effect(left, "Drift Sparks.particle.json"),
            self._effect(right, "Drift Sparks.particle.json"),
        )
        self.oil_splashes = (
            self._effect(left, "Oil Splash.particle.json"),
            self._effect(right, "Oil Splash.particle.json"),
        )
        for trail in self.trails:
            trail.emitting = False
        for effect in (*self.sparks, *self.oil_splashes):
            effect.stop(clear=True)

    def update(self, delta_time: float) -> None:
        if not isfinite(delta_time) or delta_time < 0:
            raise ValueError(
                "car effects delta_time must be finite and non-negative"
            )
        self._validate_oil()
        if self._oil_contacts:
            self._oil_remaining = self.oil_duration
        else:
            self._oil_remaining = max(0.0, self._oil_remaining - delta_time)
        oily = self._oil_contacts > 0 or self._oil_remaining > 0
        self.car.set_grip_multiplier(self.oil_grip if oily else 1.0)
        if oily:
            positions = self._oil_positions()
            if self._last_oil_positions is None or any(
                hypot(current[0] - previous[0], current[1] - previous[1]) >= 8
                for current, previous in zip(positions, self._last_oil_positions)
            ):
                self.splash_oil()
        else:
            self._last_oil_positions = None
        drifting = (
            abs(self.car.forward_speed) >= self.minimum_drift_speed
            and abs(self.car.lateral_speed) >= self.minimum_lateral_speed
            and self.car.slip >= self.minimum_slip
        )
        for trail in self.trails:
            trail.emitting = drifting and not oily
        for sparks in self.sparks:
            if drifting and not oily:
                sparks.play()
            else:
                sparks.stop(clear=False)

    def enter_oil(self) -> None:
        self._validate_oil()
        self._oil_contacts += 1
        self._oil_remaining = self.oil_duration
        self.car.set_grip_multiplier(self.oil_grip)
        self.splash_oil()

    def stay_on_oil(self) -> None:
        self._validate_oil()
        self._oil_remaining = self.oil_duration

    def exit_oil(self) -> None:
        self._oil_contacts = max(0, self._oil_contacts - 1)
        if self._oil_contacts == 0:
            self._oil_remaining = self.oil_duration

    def splash_oil(self) -> None:
        for effect in self.oil_splashes:
            effect.emit(1)
        self._last_oil_positions = self._oil_positions()

    def _oil_positions(self) -> tuple[tuple[float, float], ...]:
        return tuple(
            (wheel.world_position.x, wheel.world_position.y)
            for wheel in self._wheels
        )

    def _validate_oil(self) -> None:
        if not isfinite(self.oil_grip) or not 0 <= self.oil_grip <= 1:
            raise ValueError("oil grip must be finite and between zero and one")
        if not isfinite(self.oil_duration) or self.oil_duration <= 0:
            raise ValueError("oil duration must be finite and positive")

    @staticmethod
    def _effect(
        owner: GameObject,
        asset: str,
    ) -> ParticleEffect:
        for component in owner.components:
            if (
                isinstance(component, ParticleEffect)
                and component.effect == asset
            ):
                return component
        raise LookupError(
            f"{owner.name or 'GameObject'} requires particle effect {asset}"
        )


@requires_components(Area)
@scene_component("car.OilSurface", display_name="Oil Surface")
class OilSurface(Script):
    splash_interval: float = inspector_field(
        0.12,
        minimum=0.01,
        tooltip="Seconds between oil contact refreshes while a car remains on oil.",
    )

    def __init__(
        self,
        *,
        splash_interval: float = 0.12,
        enabled: bool = True,
    ) -> None:
        super().__init__(enabled=enabled)
        self.splash_interval = splash_interval
        self._cooldowns: dict[GameObject, float] = {}

    def on_ready(self) -> None:
        area = self.game_object.require_component(Area)
        area.on_enter(self._on_enter)
        area.on_stay(self._on_stay)
        area.on_exit(self._on_exit)

    def update(self, delta_time: float) -> None:
        if not isfinite(self.splash_interval) or self.splash_interval <= 0:
            raise ValueError(
                "oil splash interval must be finite and positive"
            )
        for owner in tuple(self._cooldowns):
            self._cooldowns[owner] -= delta_time

    def _on_enter(self, owner: GameObject) -> None:
        effects = owner.get_component(CarEffects)
        if effects is None:
            return
        effects.enter_oil()
        self._cooldowns[owner] = self.splash_interval

    def _on_stay(self, owner: GameObject) -> None:
        effects = owner.get_component(CarEffects)
        if effects is None or self._cooldowns.get(owner, 0) > 0:
            return
        effects.stay_on_oil()
        self._cooldowns[owner] = self.splash_interval

    def _on_exit(self, owner: GameObject) -> None:
        self._cooldowns.pop(owner, None)
        effects = owner.get_component(CarEffects)
        if effects is not None:
            effects.exit_oil()
