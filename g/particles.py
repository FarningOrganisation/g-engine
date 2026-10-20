from __future__ import annotations

from array import array
from dataclasses import dataclass, field
import json
from math import cos, isfinite, pi, radians, sin, sqrt
from pathlib import Path
from random import Random
from typing import Mapping, TypeAlias, cast


PARTICLE_FORMAT = "g.particle-effect"
PARTICLE_VERSION = 1
PARTICLE_SUFFIX = ".particle.json"
_MAX_PARTICLE_CAPACITY = 100_000
Color = tuple[int, int, int, int]


class ParticleFormatError(ValueError):
    """Raised when a particle-effect document cannot be parsed or validated."""
    pass


def _number(value: object, name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _vector(value: object, name: str) -> tuple[float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError(f"{name} must contain two finite numbers")
    return _number(value[0], f"{name} x"), _number(value[1], f"{name} y")


def _color(value: object, name: str) -> Color:
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 4
        or any(
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or not 0 <= channel <= 255
            for channel in value
        )
    ):
        raise ValueError(f"{name} must contain four byte channels")
    return cast(Color, tuple(value))


@dataclass(frozen=True)
class FloatRange:
    """Inclusive numeric bounds used when sampling particle properties.

    Attributes:
        minimum: Lower bound.
        maximum: Upper bound.
    """
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        """Validate and normalize finite ordered bounds."""
        minimum = _number(self.minimum, "range minimum")
        maximum = _number(self.maximum, "range maximum")
        if minimum > maximum:
            raise ValueError("range minimum must not exceed maximum")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    def sample(self, random: Random) -> float:
        """Draw a uniform sample using the supplied random generator."""
        return random.uniform(self.minimum, self.maximum)

    def to_list(self) -> list[float]:
        """Return the bounds as a two-element list for serialization."""
        return [self.minimum, self.maximum]

    @classmethod
    def from_value(cls, value: object, name: str) -> FloatRange:
        """Parse a two-number JSON array.

        Args:
            value: Value expected to be a two-element list.
            name: Field name used in parse errors.

        Raises:
            ParticleFormatError: The value is malformed or its bounds invalid.
        """
        if not isinstance(value, list) or len(value) != 2:
            raise ParticleFormatError(f"{name} must be a two-number array")
        try:
            return cls(value[0], value[1])
        except ValueError as error:
            raise ParticleFormatError(f"invalid {name}: {error}") from error


@dataclass(frozen=True)
class ParticleBurst:
    """A scheduled emission count for an emitter.

    Attributes:
        time: Non-negative time in seconds from the emitter's start.
        count: Positive number of particles to emit at that time.
    """
    time: float
    count: int

    def __post_init__(self) -> None:
        """Validate the scheduled time and count."""
        time = _number(self.time, "burst time")
        if time < 0:
            raise ValueError("burst time must be non-negative")
        if (
            not isinstance(self.count, int)
            or isinstance(self.count, bool)
            or self.count <= 0
        ):
            raise ValueError("burst count must be a positive integer")
        object.__setattr__(self, "time", time)


@dataclass(frozen=True)
class ParticlePoint:
    """Spawn shape that always samples the origin."""
    def sample(self, random: Random) -> tuple[float, float]:
        """Return the origin; the random generator is unused."""
        del random
        return 0.0, 0.0


@dataclass(frozen=True)
class ParticleLine:
    """Spawn shape sampled uniformly along a line segment.

    Attributes:
        start: First endpoint in emitter-local coordinates.
        end: Second endpoint in emitter-local coordinates.
    """
    start: tuple[float, float] = (-0.5, 0.0)
    end: tuple[float, float] = (0.5, 0.0)

    def __post_init__(self) -> None:
        """Validate and normalize both endpoints."""
        object.__setattr__(self, "start", _vector(self.start, "line start"))
        object.__setattr__(self, "end", _vector(self.end, "line end"))

    def sample(self, random: Random) -> tuple[float, float]:
        """Return a uniformly selected point along the segment."""
        amount = random.random()
        return (
            self.start[0] + (self.end[0] - self.start[0]) * amount,
            self.start[1] + (self.end[1] - self.start[1]) * amount,
        )


@dataclass(frozen=True)
class ParticleRectangle:
    """Rectangular spawn region, optionally restricted to its perimeter.

    Attributes:
        width: Non-negative rectangle width.
        height: Non-negative rectangle height.
        edge_only: Sample the perimeter instead of the interior.
    """
    width: float = 1.0
    height: float = 1.0
    edge_only: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize the dimensions."""
        width = _number(self.width, "rectangle width")
        height = _number(self.height, "rectangle height")
        if width < 0 or height < 0:
            raise ValueError("rectangle dimensions must be non-negative")
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)

    def sample(self, random: Random) -> tuple[float, float]:
        """Sample a point uniformly from the area or perimeter."""
        half_width, half_height = self.width / 2, self.height / 2
        if not self.edge_only:
            return (
                random.uniform(-half_width, half_width),
                random.uniform(-half_height, half_height),
            )
        perimeter = 2 * (self.width + self.height)
        if perimeter == 0:
            return 0.0, 0.0
        distance = random.random() * perimeter
        if distance < self.width:
            return -half_width + distance, -half_height
        distance -= self.width
        if distance < self.height:
            return half_width, -half_height + distance
        distance -= self.height
        if distance < self.width:
            return half_width - distance, half_height
        return -half_width, half_height - (distance - self.width)


@dataclass(frozen=True)
class ParticleCircle:
    """Circular spawn region, optionally restricted to its circumference.

    Attributes:
        radius: Non-negative circle radius.
        edge_only: Sample the circumference instead of the disk interior.
    """
    radius: float = 0.5
    edge_only: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize the radius."""
        radius = _number(self.radius, "circle radius")
        if radius < 0:
            raise ValueError("circle radius must be non-negative")
        object.__setattr__(self, "radius", radius)

    def sample(self, random: Random) -> tuple[float, float]:
        """Sample a point uniformly from the disk or its circumference."""
        angle = random.random() * 2 * pi
        radius = self.radius if self.edge_only else self.radius * sqrt(random.random())
        return cos(angle) * radius, sin(angle) * radius


SpawnShape: TypeAlias = (
    ParticlePoint | ParticleLine | ParticleRectangle | ParticleCircle
)


@dataclass(frozen=True)
class ParticleMotionDefinition:
    """Gravity and linear drag applied to an emitter's particles.

    Attributes:
        gravity: Acceleration vector in simulation units per second squared.
        linear_drag: Non-negative linear velocity drag factor.
    """
    gravity: tuple[float, float] = (0.0, 0.0)
    linear_drag: float = 0.0

    def __post_init__(self) -> None:
        """Validate and normalize gravity and drag."""
        gravity = _vector(self.gravity, "gravity")
        drag = _number(self.linear_drag, "linear drag")
        if drag < 0:
            raise ValueError("linear drag must be non-negative")
        object.__setattr__(self, "gravity", gravity)
        object.__setattr__(self, "linear_drag", drag)


@dataclass(frozen=True)
class ParticleAppearanceDefinition:
    """Appearance settings and interpolation ranges for emitted particles.

    Attributes:
        image: Optional image path used for particles.
        size: Initial particle size range.
        end_size: Particle size range at the end of its lifetime.
        color: Initial RGBA color.
        end_color: Final RGBA color.
        initial_rotation: Initial rotation range in degrees.
        angular_velocity: Rotation speed range in degrees per second.
        blend_mode: Either ``"alpha"`` or ``"additive"``.
    """
    image: str | None = None
    size: FloatRange = field(default_factory=lambda: FloatRange(8.0, 8.0))
    end_size: FloatRange = field(default_factory=lambda: FloatRange(0.0, 0.0))
    color: Color = (255, 255, 255, 255)
    end_color: Color = (255, 255, 255, 0)
    initial_rotation: FloatRange = field(
        default_factory=lambda: FloatRange(0.0, 0.0)
    )
    angular_velocity: FloatRange = field(
        default_factory=lambda: FloatRange(0.0, 0.0)
    )
    blend_mode: str = "alpha"

    def __post_init__(self) -> None:
        """Validate appearance paths, colors, sizes, and blend mode."""
        if self.image is not None and (
            not isinstance(self.image, str) or not self.image
        ):
            raise ValueError("particle image must be a non-empty path or None")
        if self.size.minimum < 0 or self.end_size.minimum < 0:
            raise ValueError("particle size must be non-negative")
        object.__setattr__(self, "color", _color(self.color, "particle color"))
        object.__setattr__(
            self, "end_color", _color(self.end_color, "particle end color")
        )
        if self.blend_mode not in {"alpha", "additive"}:
            raise ValueError("particle blend mode must be alpha or additive")


@dataclass(frozen=True)
class ParticleEmitterDefinition:
    """Immutable configuration for one particle emitter.

    Attributes:
        name: Emitter name, unique within its effect.
        enabled: Whether the emitter is active.
        capacity: Maximum number of live particles.
        duration: Emission duration in seconds, or ``None`` for unbounded.
        loop: Whether timed emission repeats after its duration.
        warmup: Initial simulated time before playback begins.
        rate: Continuous emission rate in particles per second.
        bursts: Ordered scheduled bursts.
        lifetime: Particle lifetime range in seconds.
        speed: Initial speed range.
        direction: Emission direction in degrees.
        spread: Emission angle spread in degrees.
        shape: Spawn region sampled for each particle.
        motion: Particle gravity and drag settings.
        appearance: Particle image, size, color, and rotation settings.
    """
    name: str = "Emitter"
    enabled: bool = True
    capacity: int = 1_000
    duration: float | None = None
    loop: bool = True
    warmup: float = 0.0
    rate: float = 10.0
    bursts: tuple[ParticleBurst, ...] = ()
    lifetime: FloatRange = field(default_factory=lambda: FloatRange(1.0, 1.0))
    speed: FloatRange = field(default_factory=lambda: FloatRange(0.0, 0.0))
    direction: float = 0.0
    spread: float = 360.0
    shape: SpawnShape = field(default_factory=ParticlePoint)
    motion: ParticleMotionDefinition = field(
        default_factory=ParticleMotionDefinition
    )
    appearance: ParticleAppearanceDefinition = field(
        default_factory=ParticleAppearanceDefinition
    )

    def __post_init__(self) -> None:
        """Validate emitter limits and normalize numeric configuration."""
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("emitter name must be non-empty")
        if (
            not isinstance(self.capacity, int)
            or isinstance(self.capacity, bool)
            or not 1 <= self.capacity <= _MAX_PARTICLE_CAPACITY
        ):
            raise ValueError(
                f"particle capacity must be between 1 and {_MAX_PARTICLE_CAPACITY}"
            )
        duration = self.duration
        if duration is not None:
            duration = _number(duration, "emitter duration")
            if duration <= 0:
                raise ValueError("emitter duration must be positive or None")
        warmup = _number(self.warmup, "emitter warmup")
        rate = _number(self.rate, "emission rate")
        direction = _number(self.direction, "emission direction")
        spread = _number(self.spread, "emission spread")
        if warmup < 0 or rate < 0:
            raise ValueError("warmup and emission rate must be non-negative")
        if not 0 <= spread <= 360:
            raise ValueError("emission spread must be between 0 and 360")
        if self.lifetime.minimum <= 0:
            raise ValueError("particle lifetime must be positive")
        if any(
            self.bursts[index].time > self.bursts[index + 1].time
            for index in range(len(self.bursts) - 1)
        ):
            raise ValueError("bursts must be ordered by time")
        object.__setattr__(self, "duration", duration)
        object.__setattr__(self, "warmup", warmup)
        object.__setattr__(self, "rate", rate)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "spread", spread)


@dataclass(frozen=True)
class ParticleEffectDefinition:
    """Immutable collection of uniquely named particle emitters.

    Attributes:
        emitters: Ordered, non-empty emitter definitions.
    """
    emitters: tuple[ParticleEmitterDefinition, ...] = field(
        default_factory=lambda: (ParticleEmitterDefinition(),)
    )

    def __post_init__(self) -> None:
        """Require at least one emitter and unique emitter names."""
        if not self.emitters:
            raise ValueError("particle effect must contain at least one emitter")
        names = [emitter.name for emitter in self.emitters]
        if len(names) != len(set(names)):
            raise ValueError("particle emitter names must be unique")

    def emitter(self, name: str) -> ParticleEmitterDefinition:
        """Return the named emitter.

        Args:
            name: Emitter name to find.

        Raises:
            KeyError: No emitter has that name.
        """
        for emitter in self.emitters:
            if emitter.name == name:
                return emitter
        raise KeyError(name)

    def to_dict(self) -> dict[str, object]:
        """Serialize the definition to the versioned JSON-compatible format."""
        return {
            "format": PARTICLE_FORMAT,
            "version": PARTICLE_VERSION,
            "emitters": [_emitter_to_dict(emitter) for emitter in self.emitters],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ParticleEffectDefinition:
        """Parse and validate a serialized particle-effect mapping.

        Args:
            value: Mapping containing format, version, and emitter data.

        Raises:
            ParticleFormatError: The format, version, or emitter data is invalid.
        """
        if value.get("format") != PARTICLE_FORMAT:
            raise ParticleFormatError(
                f"expected particle format {PARTICLE_FORMAT!r}"
            )
        if value.get("version") != PARTICLE_VERSION:
            raise ParticleFormatError(
                f"unsupported particle version {value.get('version')!r}"
            )
        emitters = value.get("emitters")
        if not isinstance(emitters, list) or not emitters:
            raise ParticleFormatError("particle emitters must be a non-empty array")
        try:
            return cls(tuple(_emitter_from_dict(item) for item in emitters))
        except (TypeError, ValueError) as error:
            if isinstance(error, ParticleFormatError):
                raise
            raise ParticleFormatError(f"invalid particle effect: {error}") from error

    @classmethod
    def read(cls, path: str | Path) -> ParticleEffectDefinition:
        """Read and parse a particle-effect JSON file.

        Args:
            path: Source file path.

        Raises:
            ParticleFormatError: The file cannot be read or parsed.
        """
        source = Path(path)
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ParticleFormatError(
                f"could not read particle effect {source}: {error}"
            ) from error
        if not isinstance(value, dict):
            raise ParticleFormatError("particle effect root must be an object")
        return cls.from_dict(value)

    def write(self, path: str | Path) -> None:
        """Write formatted particle-effect JSON to a file.

        Args:
            path: Destination file path.

        Raises:
            ParticleFormatError: The destination cannot be written.
        """
        destination = Path(path)
        try:
            destination.write_text(
                json.dumps(self.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            raise ParticleFormatError(
                f"could not write particle effect {destination}: {error}"
            ) from error


@dataclass(frozen=True)
class ParticleTransform:
    position: tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0
    scale: tuple[float, float] = (1.0, 1.0)


@dataclass(frozen=True)
class ParticleSnapshot:
    position: tuple[float, float]
    velocity: tuple[float, float]
    age: float
    lifetime: float
    size: float
    color: Color
    rotation: float


class PythonParticleSimulation:
    def __init__(
        self,
        definition: ParticleEmitterDefinition,
        *,
        seed: int = 0,
    ) -> None:
        self.definition = definition
        self._seed = seed
        self._random = Random(seed)
        self._count = 0
        self._dropped = 0
        self._arrays = tuple(
            array("f", [0.0]) * definition.capacity for _ in range(10)
        )
        (
            self._position_x, self._position_y, self._velocity_x,
            self._velocity_y, self._age, self._lifetime, self._start_size,
            self._end_size, self._rotation, self._angular_velocity,
        ) = self._arrays

    @property
    def count(self) -> int:
        return self._count

    @property
    def dropped(self) -> int:
        return self._dropped

    def emit(
        self,
        count: int,
        transform: ParticleTransform = ParticleTransform(),
    ) -> int:
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("particle count must be a non-negative integer")
        emitted = min(count, self.definition.capacity - self._count)
        self._dropped += count - emitted
        for _ in range(emitted):
            self._emit_one(transform)
        return emitted

    def update(self, delta_time: float) -> None:
        delta_time = _number(delta_time, "particle delta time")
        if delta_time < 0:
            raise ValueError("particle delta time must be non-negative")
        gravity_x, gravity_y = self.definition.motion.gravity
        drag = max(0.0, 1.0 - self.definition.motion.linear_drag * delta_time)
        index = 0
        while index < self._count:
            age = self._age[index] + delta_time
            if age >= self._lifetime[index]:
                self._remove(index)
                continue
            velocity_x = (self._velocity_x[index] + gravity_x * delta_time) * drag
            velocity_y = (self._velocity_y[index] + gravity_y * delta_time) * drag
            self._velocity_x[index], self._velocity_y[index] = velocity_x, velocity_y
            self._position_x[index] += velocity_x * delta_time
            self._position_y[index] += velocity_y * delta_time
            self._rotation[index] += self._angular_velocity[index] * delta_time
            self._age[index] = age
            index += 1

    def clear(self) -> None:
        self._count = 0

    def reset(self) -> None:
        self.clear()
        self._dropped = 0
        self._random.seed(self._seed)

    def snapshot(self) -> tuple[ParticleSnapshot, ...]:
        start_color = self.definition.appearance.color
        end_color = self.definition.appearance.end_color
        particles: list[ParticleSnapshot] = []
        for index in range(self._count):
            amount = self._age[index] / self._lifetime[index]
            particles.append(ParticleSnapshot(
                (self._position_x[index], self._position_y[index]),
                (self._velocity_x[index], self._velocity_y[index]),
                self._age[index],
                self._lifetime[index],
                self._start_size[index] + (
                    self._end_size[index] - self._start_size[index]
                ) * amount,
                cast(Color, tuple(
                    round(start + (end - start) * amount)
                    for start, end in zip(start_color, end_color)
                )),
                self._rotation[index],
            ))
        return tuple(particles)

    def _emit_one(self, transform: ParticleTransform) -> None:
        index = self._count
        local_x, local_y = self.definition.shape.sample(self._random)
        local_x *= transform.scale[0]
        local_y *= transform.scale[1]
        angle = -radians(transform.rotation)
        self._position_x[index] = (
            transform.position[0] + local_x * cos(angle) - local_y * sin(angle)
        )
        self._position_y[index] = (
            transform.position[1] + local_x * sin(angle) + local_y * cos(angle)
        )
        heading = radians(
            self.definition.direction - transform.rotation
            + self._random.uniform(
                -self.definition.spread / 2, self.definition.spread / 2
            )
        )
        speed = self.definition.speed.sample(self._random)
        self._velocity_x[index], self._velocity_y[index] = (
            cos(heading) * speed, sin(heading) * speed
        )
        self._age[index] = 0
        self._lifetime[index] = self.definition.lifetime.sample(self._random)
        self._start_size[index] = self.definition.appearance.size.sample(self._random)
        self._end_size[index] = self.definition.appearance.end_size.sample(self._random)
        self._rotation[index] = (
            self.definition.appearance.initial_rotation.sample(self._random)
            + transform.rotation
        )
        self._angular_velocity[index] = (
            self.definition.appearance.angular_velocity.sample(self._random)
        )
        self._count += 1

    def _remove(self, index: int) -> None:
        last = self._count - 1
        if index != last:
            for values in self._arrays:
                values[index] = values[last]
        self._count = last


@dataclass
class _EmitterPlayback:
    definition: ParticleEmitterDefinition
    simulation: PythonParticleSimulation
    elapsed: float = 0.0
    emission: float = 0.0
    next_burst: int = 0
    emitting: bool = True
    warmed: bool = False


class ParticlePlayer:
    def __init__(
        self,
        definition: ParticleEffectDefinition,
        *,
        seed: int = 0,
    ) -> None:
        self.definition = definition
        self.seed = seed
        self.playing = False
        self.paused = False
        self._emitters = tuple(
            _EmitterPlayback(
                emitter,
                PythonParticleSimulation(emitter, seed=seed + index),
            )
            for index, emitter in enumerate(definition.emitters)
        )

    @property
    def particle_count(self) -> int:
        return sum(item.simulation.count for item in self._emitters)

    @property
    def dropped_count(self) -> int:
        return sum(item.simulation.dropped for item in self._emitters)

    def snapshots(self) -> dict[str, tuple[ParticleSnapshot, ...]]:
        return {
            item.definition.name: item.simulation.snapshot()
            for item in self._emitters
        }

    def play(self, *, restart: bool = False) -> None:
        if restart:
            self.clear()
        self.playing = True
        self.paused = False
        for item in self._emitters:
            item.emitting = item.definition.enabled

    def pause(self) -> None:
        if self.playing:
            self.paused = True

    def resume(self) -> None:
        if self.playing:
            self.paused = False

    def stop(self, *, clear: bool = False) -> None:
        self.playing = False
        self.paused = False
        for item in self._emitters:
            item.emitting = False
        if clear:
            self.clear()

    def clear(self) -> None:
        for item in self._emitters:
            item.simulation.reset()
            item.elapsed = 0
            item.emission = 0
            item.next_burst = 0
            item.warmed = False

    def emit(
        self,
        count: int,
        *,
        emitter: str | None = None,
        transform: ParticleTransform = ParticleTransform(),
    ) -> int:
        targets = (
            self._emitters
            if emitter is None
            else tuple(
                item for item in self._emitters
                if item.definition.name == emitter
            )
        )
        if emitter is not None and not targets:
            raise KeyError(emitter)
        return sum(item.simulation.emit(count, transform) for item in targets)

    def update(
        self,
        delta_time: float,
        transform: ParticleTransform = ParticleTransform(),
    ) -> None:
        delta_time = _number(delta_time, "particle delta time")
        if delta_time < 0:
            raise ValueError("particle delta time must be non-negative")
        if self.paused:
            return
        for item in self._emitters:
            if self.playing and item.emitting and not item.warmed:
                remaining = item.definition.warmup
                while remaining > 0:
                    step = min(1 / 60, remaining)
                    self._advance_emitter(item, step, transform)
                    item.simulation.update(step)
                    remaining -= step
                item.warmed = True
            item.simulation.update(delta_time)
            if not self.playing or not item.emitting:
                continue
            self._advance_emitter(item, delta_time, transform)
        if self.playing and all(
            not item.emitting and item.simulation.count == 0
            for item in self._emitters
        ):
            self.playing = False

    @staticmethod
    def _advance_emitter(
        item: _EmitterPlayback,
        delta_time: float,
        transform: ParticleTransform,
    ) -> None:
        definition = item.definition
        previous = item.elapsed
        current = previous + delta_time
        active_delta = delta_time
        if definition.duration is not None:
            active_delta = max(
                0.0, min(current, definition.duration) - previous
            )
        item.emission += definition.rate * active_delta
        count = int(item.emission)
        item.emission -= count
        if count:
            item.simulation.emit(count, transform)
        while (
            item.next_burst < len(definition.bursts)
            and definition.bursts[item.next_burst].time <= current
        ):
            burst = definition.bursts[item.next_burst]
            if burst.time >= previous:
                item.simulation.emit(burst.count, transform)
            item.next_burst += 1
        item.elapsed = current
        if definition.duration is None or current < definition.duration:
            return
        if definition.loop:
            item.elapsed %= definition.duration
            item.next_burst = 0
            while (
                item.next_burst < len(definition.bursts)
                and definition.bursts[item.next_burst].time < item.elapsed
            ):
                item.next_burst += 1
        else:
            item.emitting = False


def _shape_to_dict(shape: SpawnShape) -> dict[str, object]:
    if isinstance(shape, ParticlePoint):
        return {"type": "point"}
    if isinstance(shape, ParticleLine):
        return {"type": "line", "start": list(shape.start), "end": list(shape.end)}
    if isinstance(shape, ParticleRectangle):
        return {
            "type": "rectangle", "width": shape.width,
            "height": shape.height, "edge_only": shape.edge_only,
        }
    return {"type": "circle", "radius": shape.radius, "edge_only": shape.edge_only}


def _shape_from_dict(value: object) -> SpawnShape:
    if not isinstance(value, dict):
        raise ParticleFormatError("emitter shape must be an object")
    kind = value.get("type")
    if kind == "point":
        return ParticlePoint()
    if kind == "line":
        return ParticleLine(
            _vector(value.get("start"), "line start"),
            _vector(value.get("end"), "line end"),
        )
    if kind == "rectangle":
        return ParticleRectangle(
            _number(value.get("width"), "rectangle width"),
            _number(value.get("height"), "rectangle height"),
            bool(value.get("edge_only", False)),
        )
    if kind == "circle":
        return ParticleCircle(
            _number(value.get("radius"), "circle radius"),
            bool(value.get("edge_only", False)),
        )
    raise ParticleFormatError(f"unknown particle shape: {kind!r}")


def _emitter_to_dict(value: ParticleEmitterDefinition) -> dict[str, object]:
    appearance = value.appearance
    return {
        "name": value.name, "enabled": value.enabled,
        "capacity": value.capacity, "duration": value.duration,
        "loop": value.loop, "warmup": value.warmup, "rate": value.rate,
        "bursts": [
            {"time": burst.time, "count": burst.count} for burst in value.bursts
        ],
        "lifetime": value.lifetime.to_list(),
        "speed": value.speed.to_list(), "direction": value.direction,
        "spread": value.spread, "shape": _shape_to_dict(value.shape),
        "motion": {
            "gravity": list(value.motion.gravity),
            "linear_drag": value.motion.linear_drag,
        },
        "appearance": {
            "image": appearance.image,
            "size": appearance.size.to_list(),
            "end_size": appearance.end_size.to_list(),
            "color": list(appearance.color),
            "end_color": list(appearance.end_color),
            "initial_rotation": appearance.initial_rotation.to_list(),
            "angular_velocity": appearance.angular_velocity.to_list(),
            "blend_mode": appearance.blend_mode,
        },
    }


def _emitter_from_dict(value: object) -> ParticleEmitterDefinition:
    if not isinstance(value, dict):
        raise ParticleFormatError("particle emitter must be an object")
    motion = value.get("motion", {})
    appearance = value.get("appearance", {})
    if not isinstance(motion, dict) or not isinstance(appearance, dict):
        raise ParticleFormatError("emitter motion and appearance must be objects")
    raw_bursts = value.get("bursts", [])
    if not isinstance(raw_bursts, list):
        raise ParticleFormatError("emitter bursts must be an array")
    bursts: list[ParticleBurst] = []
    for raw in raw_bursts:
        if not isinstance(raw, dict):
            raise ParticleFormatError("particle burst must be an object")
        bursts.append(ParticleBurst(
            _number(raw.get("time"), "burst time"),
            cast(int, raw.get("count")),
        ))
    return ParticleEmitterDefinition(
        name=cast(str, value.get("name", "Emitter")),
        enabled=bool(value.get("enabled", True)),
        capacity=cast(int, value.get("capacity", 1000)),
        duration=cast(float | None, value.get("duration")),
        loop=bool(value.get("loop", True)),
        warmup=_number(value.get("warmup", 0), "warmup"),
        rate=_number(value.get("rate", 10), "rate"),
        bursts=tuple(bursts),
        lifetime=FloatRange.from_value(value.get("lifetime", [1, 1]), "lifetime"),
        speed=FloatRange.from_value(value.get("speed", [0, 0]), "speed"),
        direction=_number(value.get("direction", 0), "direction"),
        spread=_number(value.get("spread", 360), "spread"),
        shape=_shape_from_dict(value.get("shape", {"type": "point"})),
        motion=ParticleMotionDefinition(
            _vector(motion.get("gravity", [0, 0]), "gravity"),
            _number(motion.get("linear_drag", 0), "linear drag"),
        ),
        appearance=ParticleAppearanceDefinition(
            image=cast(str | None, appearance.get("image")),
            size=FloatRange.from_value(appearance.get("size", [8, 8]), "size"),
            end_size=FloatRange.from_value(
                appearance.get("end_size", [0, 0]), "end size"
            ),
            color=_color(appearance.get("color", [255] * 4), "color"),
            end_color=_color(
                appearance.get("end_color", [255, 255, 255, 0]), "end color"
            ),
            initial_rotation=FloatRange.from_value(
                appearance.get("initial_rotation", [0, 0]), "initial rotation"
            ),
            angular_velocity=FloatRange.from_value(
                appearance.get("angular_velocity", [0, 0]), "angular velocity"
            ),
            blend_mode=cast(str, appearance.get("blend_mode", "alpha")),
        ),
    )


def _fire_preset() -> ParticleEffectDefinition:
    return ParticleEffectDefinition((
        ParticleEmitterDefinition(
            name="Flames",
            capacity=600,
            rate=90,
            lifetime=FloatRange(0.45, 0.9),
            speed=FloatRange(28, 65),
            direction=90,
            spread=24,
            shape=ParticleLine((-10, 0), (10, 0)),
            motion=ParticleMotionDefinition((0, 35), 0.25),
            appearance=ParticleAppearanceDefinition(
                size=FloatRange(10, 18),
                end_size=FloatRange(2, 6),
                color=(255, 225, 90, 230),
                end_color=(230, 45, 8, 0),
                blend_mode="additive",
            ),
        ),
        ParticleEmitterDefinition(
            name="Embers",
            capacity=180,
            rate=12,
            lifetime=FloatRange(0.8, 1.8),
            speed=FloatRange(45, 110),
            direction=90,
            spread=55,
            motion=ParticleMotionDefinition((0, -15), 0.08),
            appearance=ParticleAppearanceDefinition(
                size=FloatRange(2, 4),
                end_size=FloatRange(0, 1),
                color=(255, 190, 40, 255),
                end_color=(255, 60, 10, 0),
                blend_mode="additive",
            ),
        ),
    ))


def _smoke_preset() -> ParticleEffectDefinition:
    return ParticleEffectDefinition((
        ParticleEmitterDefinition(
            name="Smoke",
            capacity=500,
            rate=28,
            lifetime=FloatRange(1.8, 3.2),
            speed=FloatRange(12, 28),
            direction=90,
            spread=35,
            shape=ParticleCircle(6),
            motion=ParticleMotionDefinition((0, 10), 0.18),
            appearance=ParticleAppearanceDefinition(
                size=FloatRange(10, 18),
                end_size=FloatRange(34, 58),
                color=(85, 88, 94, 150),
                end_color=(35, 38, 45, 0),
            ),
        ),
    ))


def _sparks_preset() -> ParticleEffectDefinition:
    return ParticleEffectDefinition((
        ParticleEmitterDefinition(
            name="Sparks",
            capacity=300,
            duration=0.08,
            loop=False,
            rate=0,
            bursts=(ParticleBurst(0, 45),),
            lifetime=FloatRange(0.35, 0.9),
            speed=FloatRange(90, 260),
            direction=90,
            spread=360,
            motion=ParticleMotionDefinition((0, -240), 0.05),
            appearance=ParticleAppearanceDefinition(
                size=FloatRange(2, 5),
                end_size=FloatRange(0, 1),
                color=(255, 230, 120, 255),
                end_color=(255, 70, 15, 0),
                blend_mode="additive",
            ),
        ),
    ))


def _explosion_preset() -> ParticleEffectDefinition:
    return ParticleEffectDefinition((
        ParticleEmitterDefinition(
            name="Flash",
            capacity=12,
            duration=0.06,
            loop=False,
            bursts=(ParticleBurst(0, 1),),
            lifetime=FloatRange(0.12, 0.2),
            appearance=ParticleAppearanceDefinition(
                size=FloatRange(35, 50),
                end_size=FloatRange(90, 120),
                color=(255, 245, 190, 255),
                end_color=(255, 90, 20, 0),
                blend_mode="additive",
            ),
        ),
        ParticleEmitterDefinition(
            name="Debris",
            capacity=350,
            duration=0.08,
            loop=False,
            bursts=(ParticleBurst(0, 70),),
            lifetime=FloatRange(0.5, 1.3),
            speed=FloatRange(80, 280),
            spread=360,
            motion=ParticleMotionDefinition((0, -190), 0.08),
            appearance=ParticleAppearanceDefinition(
                size=FloatRange(3, 8),
                end_size=FloatRange(0, 2),
                color=(255, 165, 55, 255),
                end_color=(110, 35, 15, 0),
                blend_mode="additive",
            ),
        ),
        ParticleEmitterDefinition(
            name="Smoke",
            capacity=180,
            duration=0.12,
            loop=False,
            bursts=(ParticleBurst(0.04, 25),),
            lifetime=FloatRange(1.0, 2.2),
            speed=FloatRange(18, 65),
            spread=360,
            motion=ParticleMotionDefinition((0, 18), 0.25),
            appearance=ParticleAppearanceDefinition(
                size=FloatRange(12, 22),
                end_size=FloatRange(40, 70),
                color=(90, 75, 65, 170),
                end_color=(35, 35, 40, 0),
            ),
        ),
    ))


def _rain_preset() -> ParticleEffectDefinition:
    return ParticleEffectDefinition((
        ParticleEmitterDefinition(
            name="Rain",
            capacity=1_500,
            rate=240,
            lifetime=FloatRange(1.2, 1.8),
            speed=FloatRange(320, 430),
            direction=-90,
            spread=5,
            shape=ParticleLine((-320, 180), (320, 180)),
            motion=ParticleMotionDefinition((0, -180), 0),
            appearance=ParticleAppearanceDefinition(
                size=FloatRange(1, 2),
                end_size=FloatRange(1, 2),
                color=(150, 195, 255, 180),
                end_color=(150, 195, 255, 40),
            ),
        ),
    ))


def _snow_preset() -> ParticleEffectDefinition:
    return ParticleEffectDefinition((
        ParticleEmitterDefinition(
            name="Snow",
            capacity=1_000,
            rate=90,
            lifetime=FloatRange(4, 7),
            speed=FloatRange(20, 45),
            direction=-90,
            spread=28,
            shape=ParticleLine((-320, 180), (320, 180)),
            motion=ParticleMotionDefinition((8, -4), 0.05),
            appearance=ParticleAppearanceDefinition(
                size=FloatRange(2, 6),
                end_size=FloatRange(1, 4),
                color=(245, 250, 255, 220),
                end_color=(225, 240, 255, 40),
            ),
        ),
    ))


_PARTICLE_PRESET_FACTORIES = {
    "fire": _fire_preset,
    "smoke": _smoke_preset,
    "sparks": _sparks_preset,
    "explosion": _explosion_preset,
    "rain": _rain_preset,
    "snow": _snow_preset,
}
PARTICLE_PRESETS = tuple(_PARTICLE_PRESET_FACTORIES)


def particle_preset(name: str) -> ParticleEffectDefinition:
    """Return a fresh definition for a built-in particle-effect preset.

    Args:
        name: Preset name (case-insensitive).

    Raises:
        KeyError: The requested preset is not known.
    """
    try:
        factory = _PARTICLE_PRESET_FACTORIES[name.lower()]
    except (AttributeError, KeyError):
        raise KeyError(f"unknown particle preset: {name!r}") from None
    return factory()
