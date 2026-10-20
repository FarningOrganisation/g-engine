from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence as CollectionSequence
from enum import Enum, auto
from typing import TYPE_CHECKING, TypeAlias

from .math import Vec2
from .resources import Color

if TYPE_CHECKING:
    from .components import BehaviourRunner, Component
    from .nodes import GameObject


class BehaviourStatus(Enum):
    """Outcome returned by a behaviour update."""

    RUNNING = auto()
    SUCCESS = auto()
    FAILURE = auto()
    CANCELLED = auto()


class Behaviour(ABC):
    """Base class for per-object behaviours updated over time."""

    @abstractmethod
    def update(
        self, game_object: GameObject, delta_time: float
    ) -> BehaviourStatus:
        """Advance the behaviour for one update step.

        Args:
            game_object (GameObject): Object being controlled.
            delta_time (float): Elapsed time in seconds.

        Returns:
            BehaviourStatus: The behaviour's current status.
        """
        pass

    def reset(self) -> None:
        """Restore the behaviour to its initial state."""
        pass


class Sequence(Behaviour):
    """Run behaviours in order, stopping when one is running or fails.

    Args:
        *behaviours (Behaviour): Non-empty ordered set of behaviours to execute.

    Raises:
        ValueError: If no behaviours are supplied.
    """

    def __init__(self, *behaviours: Behaviour) -> None:
        """Initialize an ordered behaviour sequence."""
        if not behaviours:
            raise ValueError("behaviours must not be empty")
        self.behaviours = behaviours
        self._index = 0

    def update(
        self, game_object: GameObject, delta_time: float
    ) -> BehaviourStatus:
        """Advance the current step and any immediately completed steps."""
        while self._index < len(self.behaviours):
            status = self.behaviours[self._index].update(
                game_object, delta_time
            )
            if status is BehaviourStatus.RUNNING:
                return status
            if status is BehaviourStatus.FAILURE:
                return status
            self._index += 1
            delta_time = 0
        return BehaviourStatus.SUCCESS

    def reset(self) -> None:
        """Reset this sequence and all of its steps."""
        self._index = 0
        for behaviour in self.behaviours:
            behaviour.reset()


class Parallel(Behaviour):
    """Run behaviours concurrently and succeed after all succeed.

    Args:
        *behaviours (Behaviour): Non-empty set of behaviours to execute concurrently.

    Raises:
        ValueError: If no behaviours are supplied.
    """

    def __init__(self, *behaviours: Behaviour) -> None:
        """Initialize a concurrent behaviour group."""
        if not behaviours:
            raise ValueError("behaviours must not be empty")
        self.behaviours = behaviours
        self._completed: set[int] = set()

    def update(
        self, game_object: GameObject, delta_time: float
    ) -> BehaviourStatus:
        """Advance each unfinished behaviour and combine their outcomes."""
        for index, behaviour in enumerate(self.behaviours):
            if index in self._completed:
                continue
            status = behaviour.update(game_object, delta_time)
            if status is BehaviourStatus.FAILURE:
                return status
            if status is BehaviourStatus.SUCCESS:
                self._completed.add(index)
        if len(self._completed) == len(self.behaviours):
            return BehaviourStatus.SUCCESS
        return BehaviourStatus.RUNNING

    def reset(self) -> None:
        """Clear completion state and reset every child behaviour."""
        self._completed.clear()
        for behaviour in self.behaviours:
            behaviour.reset()


class Repeat(Behaviour):
    """Repeat a behaviour until it fails or the requested count is reached.

    Args:
        behaviour (Behaviour): Behaviour to run repeatedly.
        times (int | None): Number of successful iterations, or ``None`` to repeat forever.

    Raises:
        ValueError: If ``times`` is not positive when specified.
    """

    def __init__(self, behaviour: Behaviour, times: int | None = None) -> None:
        """Initialize a repeated behaviour."""
        if times is not None and times <= 0:
            raise ValueError("times must be positive or None")
        self.behaviour = behaviour
        self.times = times
        self._completed = 0

    def update(
        self, game_object: GameObject, delta_time: float
    ) -> BehaviourStatus:
        """Advance the current iteration and restart after success if needed."""
        status = self.behaviour.update(game_object, delta_time)
        if status is not BehaviourStatus.SUCCESS:
            return status
        self._completed += 1
        if self.times is not None and self._completed >= self.times:
            return BehaviourStatus.SUCCESS
        self.behaviour.reset()
        return BehaviourStatus.RUNNING

    def reset(self) -> None:
        """Reset the iteration count and child behaviour."""
        self._completed = 0
        self.behaviour.reset()


class Wait(Behaviour):
    """Wait for a duration before succeeding.

    Args:
        duration (float): Wait time in seconds; must not be negative.

    Raises:
        ValueError: If ``duration`` is negative.
    """

    def __init__(self, duration: float) -> None:
        """Initialize a timed wait."""
        if duration < 0:
            raise ValueError("duration must not be negative")
        self.duration = duration
        self._elapsed = 0.0

    def update(
        self, game_object: GameObject, delta_time: float
    ) -> BehaviourStatus:
        """Accumulate elapsed time and report whether the wait has finished."""
        del game_object
        self._elapsed += delta_time
        return (
            BehaviourStatus.SUCCESS
            if self._elapsed >= self.duration
            else BehaviourStatus.RUNNING
        )

    def reset(self) -> None:
        """Set accumulated wait time back to zero."""
        self._elapsed = 0.0


class Call(Behaviour):
    """Invoke a callback once, then complete.

    Args:
        callback (Callable[[GameObject], None]): Function called with the animated game object.
    """

    def __init__(self, callback: Callable[[GameObject], None]) -> None:
        """Initialize a one-shot callback behaviour."""
        self.callback = callback
        self._called = False

    def update(
        self, game_object: GameObject, delta_time: float
    ) -> BehaviourStatus:
        """Run the callback on the first update and return success."""
        del delta_time
        if not self._called:
            self.callback(game_object)
            self._called = True
        return BehaviourStatus.SUCCESS

    def reset(self) -> None:
        """Allow the callback to run again on the next update."""
        self._called = False


class SetState(Behaviour):
    """Set a game object's sprite animation state.

    Args:
        state (str): Animation state name to select.
    """

    def __init__(self, state: str) -> None:
        """Initialize a behaviour that selects an animation state."""
        self.state = state

    def update(
        self, game_object: GameObject, delta_time: float
    ) -> BehaviourStatus:
        """Set the state, returning failure if the object has no animator."""
        del delta_time
        try:
            game_object.set_state(self.state)
        except (AttributeError, ValueError):
            return BehaviourStatus.FAILURE
        return BehaviourStatus.SUCCESS


class Ease:
    """Common easing functions for tween progress values."""

    @staticmethod
    def linear(value: float) -> float:
        """Return progress unchanged."""
        return value

    @staticmethod
    def in_quad(value: float) -> float:
        """Apply quadratic ease-in to progress."""
        return value * value

    @staticmethod
    def out_quad(value: float) -> float:
        """Apply quadratic ease-out to progress."""
        return 1 - (1 - value) * (1 - value)

    @staticmethod
    def in_out_quad(value: float) -> float:
        """Apply quadratic ease-in/ease-out to progress."""
        if value < 0.5:
            return 2 * value * value
        return 1 - ((-2 * value + 2) ** 2) / 2


Easing: TypeAlias = Callable[[float], float]
AnimatableValue: TypeAlias = int | float | Vec2 | Color


def _validate_color(value: Color, name: str) -> None:
    if not all(
        isinstance(channel, int) and 0 <= channel <= 255
        for channel in value
    ):
        raise TypeError(
            f"{name!r} color channels must be integers from 0 to 255"
        )


def _interpolate_value(
    name: str,
    start: AnimatableValue,
    target: AnimatableValue,
    progress: float,
) -> AnimatableValue:
    if isinstance(start, (int, float)) and isinstance(
        target, (int, float)
    ):
        value = start + (target - start) * progress
        return round(value) if isinstance(start, int) else value

    if isinstance(start, Vec2) and isinstance(target, Vec2):
        return start.lerp(target, progress)

    if isinstance(start, tuple) and isinstance(target, tuple):
        if len(start) not in (3, 4) or len(target) not in (3, 4):
            raise TypeError(f"{name!r} must be an RGB or RGBA color")
        _validate_color(start, name)
        _validate_color(target, name)
        start_rgba = start if len(start) == 4 else (*start, 255)
        target_rgba = (
            target
            if len(target) == 4
            else (*target, start_rgba[3])
        )
        interpolated = tuple(
            max(0, min(255, round(first + (second - first) * progress)))
            for first, second in zip(start_rgba, target_rgba)
        )
        return (
            interpolated[0],
            interpolated[1],
            interpolated[2],
            interpolated[3],
        )

    if start is None:
        raise TypeError(f"cannot animate {name!r} from None")
    raise TypeError(
        f"{name!r} must use matching numbers, Vec2 values, or colors"
    )


class Tween(Behaviour):
    """Interpolate object or component properties over a duration.

    Args:
        duration (float): Tween duration in seconds; must not be negative.
        properties (Mapping[str, AnimatableValue]): Target property values.
        easing (Easing): Function mapping normalized progress to eased progress.
        target (Component | None): Optional component whose properties are animated explicitly.

    Raises:
        ValueError: If the duration is negative or no properties are provided.
        TypeError: If start and target values cannot be interpolated.
    """

    def __init__(
        self,
        duration: float,
        properties: Mapping[str, AnimatableValue],
        easing: Easing = Ease.linear,
        *,
        target: Component | None = None,
    ) -> None:
        """Initialize property targets, easing, duration, and optional target component."""
        if duration < 0:
            raise ValueError("duration must not be negative")
        if not properties:
            raise ValueError("properties must not be empty")
        self.duration = duration
        self.properties = dict(properties)
        self.easing = easing
        self._elapsed = 0.0
        self._start_values: dict[str, AnimatableValue] | None = None
        self._target = target
        self._property_targets: dict[str, GameObject | Component] = {}

    def update(
        self, game_object: GameObject, delta_time: float
    ) -> BehaviourStatus:
        """Capture starting values as needed and apply the current tween step."""
        from .components import Component

        if self._target is not None and self._target.owner is not game_object:
            raise ValueError("animation component must belong to the animated game object")
        if self._start_values is None:
            self._start_values = {}
            for name in self.properties:
                property_target: GameObject | Component = game_object
                if self._target is not None:
                    property_target = self._target
                elif not hasattr(game_object, name):
                    renderers = [renderer for renderer in game_object.renderers if hasattr(renderer, name)]
                    if len(renderers) != 1:
                        reason = "is ambiguous across renderers" if renderers else "has no matching renderer"
                        raise ValueError(
                            f"animation property {name!r} {reason}; "
                            "use component.animate() to select a target explicitly"
                        )
                    property_target = renderers[0]
                value = getattr(property_target, name)
                if not isinstance(value, (int, float, Vec2, tuple)):
                    if value is None:
                        raise TypeError(f"cannot animate {name!r} from None")
                    raise TypeError(
                        f"{name!r} must be a number, Vec2, or color"
                    )
                self._start_values[name] = (
                    value.copy() if isinstance(value, Vec2) else value
                )
                self._property_targets[name] = property_target

        self._elapsed += delta_time
        progress = 1.0 if self.duration == 0 else min(
            self._elapsed / self.duration, 1.0
        )
        eased_progress = self.easing(progress)
        for name, target in self.properties.items():
            start = self._start_values[name]
            property_target = self._property_targets[name]
            if isinstance(property_target, Component) and property_target.owner is not game_object:
                raise ValueError("animated component was removed from its game object")
            setattr(
                property_target,
                name,
                _interpolate_value(name, start, target, eased_progress),
            )
        return (
            BehaviourStatus.SUCCESS
            if progress >= 1
            else BehaviourStatus.RUNNING
        )

    def reset(self) -> None:
        """Clear elapsed time and recapture starting property values next update."""
        self._elapsed = 0.0
        self._start_values = None
        self._property_targets.clear()


BehaviourInput: TypeAlias = Behaviour | CollectionSequence[Behaviour]


def as_behaviour(value: BehaviourInput) -> Behaviour:
    """Return a behaviour unchanged or wrap a sequence of behaviours.

    Args:
        value (BehaviourInput): Existing behaviour or ordered collection of behaviours.

    Returns:
        Behaviour: A behaviour suitable for direct execution.

    Raises:
        ValueError: If an empty collection is supplied.
    """
    if isinstance(value, Behaviour):
        return value
    return Sequence(*tuple(value))


class AnimationHandle:
    """Control and inspect a running game-object animation."""

    def __init__(
        self,
        runner: BehaviourRunner,
        behaviour: Behaviour,
        *,
        loop: bool,
        on_complete: Callable[[GameObject], None] | None,
    ) -> None:
        """Initialize an animation control handle."""
        self._runner = runner
        self._behaviour = behaviour
        self._loop = loop
        self._on_complete = on_complete
        self._status = BehaviourStatus.RUNNING
        self._paused = False

    @property
    def status(self) -> BehaviourStatus:
        """Current terminal or running status."""
        return self._status

    @property
    def running(self) -> bool:
        """Whether the animation is running and not paused."""
        return self._status is BehaviourStatus.RUNNING and not self._paused

    @property
    def paused(self) -> bool:
        """Whether updates are currently paused."""
        return self._paused

    @property
    def completed(self) -> bool:
        """Whether the animation finished successfully."""
        return self._status is BehaviourStatus.SUCCESS

    @property
    def cancelled(self) -> bool:
        """Whether the animation was cancelled."""
        return self._status is BehaviourStatus.CANCELLED

    def pause(self) -> None:
        """Pause animation updates."""
        self._runner._pause_handle(self)

    def resume(self) -> None:
        """Resume animation updates."""
        self._runner._resume_handle(self)

    def cancel(self) -> None:
        """Cancel the animation."""
        self._runner._cancel_handle(self)

    def restart(self) -> None:
        """Reset and restart the animation."""
        self._runner._restart_handle(self)


class AnimationBuilder:
    """Fluent builder for a game object's sequential and parallel animations.

    Args:
        game_object (GameObject): Object whose properties will be animated.
        target (Component | None): Optional component to receive tween property writes.
    """

    def __init__(self, game_object: GameObject, target: Component | None = None) -> None:
        """Initialize a builder for the selected object or component."""
        self._game_object = game_object
        self._target = target
        self._steps: list[Behaviour] = []

    def to(
        self,
        duration: float,
        easing: Easing = Ease.linear,
        **properties: AnimatableValue,
    ) -> AnimationBuilder:
        """Append a tween step.

        Args:
            duration (float): Tween duration in seconds.
            easing (Easing): Easing function for normalized progress.
            **properties (AnimatableValue): Target property values.

        Returns:
            AnimationBuilder: This builder for chaining.
        """
        self._steps.append(Tween(duration, properties, easing, target=self._target))
        return self

    def wait(self, duration: float) -> AnimationBuilder:
        """Append a delay step and return this builder."""
        self._steps.append(Wait(duration))
        return self

    def call(
        self, callback: Callable[[GameObject], None]
    ) -> AnimationBuilder:
        """Append a callback step and return this builder."""
        self._steps.append(Call(callback))
        return self

    def repeat(self, times: int | None = None) -> AnimationBuilder:
        """Repeat all steps accumulated so far.

        Args:
            times (int | None): Successful repetitions, or ``None`` for an infinite loop.

        Returns:
            AnimationBuilder: This builder for chaining.
        """
        if not self._steps:
            raise ValueError("animation must contain a step before repeat")
        repeated = Repeat(Sequence(*self._steps), times)
        self._steps = [repeated]
        return self

    def parallel(
        self,
        *animations: AnimationBuilder | Behaviour,
    ) -> AnimationBuilder:
        """Append concurrent animations or behaviours as one step.

        Args:
            *animations (AnimationBuilder | Behaviour): Builders for this object or behaviour instances.

        Returns:
            AnimationBuilder: This builder for chaining.

        Raises:
            ValueError: If empty or if a builder targets another object.
        """
        if not animations:
            raise ValueError("animations must not be empty")
        behaviours: list[Behaviour] = []
        for animation in animations:
            if isinstance(animation, AnimationBuilder):
                if animation._game_object is not self._game_object:
                    raise ValueError(
                        "parallel animations must target the same game object"
                    )
                behaviours.append(animation._build())
            else:
                behaviours.append(animation)
        self._steps.append(Parallel(*behaviours))
        return self

    def _build(self) -> Behaviour:
        if not self._steps:
            raise ValueError("animation must contain at least one step")
        return Sequence(*self._steps)

    def start(
        self,
        *,
        loop: bool = False,
        on_complete: Callable[[GameObject], None] | None = None,
    ) -> AnimationHandle:
        """Start the built animation on the object's behaviour runner.

        Args:
            loop (bool): Whether to restart automatically after successful completion.
            on_complete (Callable[[GameObject], None] | None): Optional callback invoked after successful completion.

        Returns:
            AnimationHandle: A handle for controlling and observing the animation.
        """
        from .components import BehaviourRunner

        behaviour = self._build()
        runner = self._game_object.get_component(BehaviourRunner)
        if runner is None:
            runner = self._game_object.add_component(BehaviourRunner())
        return runner.play_animation(
            behaviour,
            loop=loop,
            on_complete=on_complete,
        )
