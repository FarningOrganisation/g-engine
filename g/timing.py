from __future__ import annotations

from collections.abc import Callable
from math import isfinite


UpdateCallback = Callable[[float], None]
TimerCallback = Callable[[], None]


class TimerHandle:
    """Controls one scheduled timer and exposes its current status."""

    def __init__(
        self,
        owner: SceneTime,
        delay: float,
        callback: TimerCallback,
        *,
        interval: float | None,
        times: int | None,
    ) -> None:
        """Initialize a timer; normally created by :class:`SceneTime`."""
        self._owner = owner
        self._callback = callback
        self._interval = interval
        self._remaining = delay
        self._calls_remaining = times
        self._paused = False
        self._cancelled = False
        self._completed = False

    @property
    def remaining(self) -> float:
        """Return seconds until the next callback, clamped to zero."""
        return max(0.0, self._remaining)

    @property
    def running(self) -> bool:
        """Whether the timer is active and not paused."""
        return not (
            self._paused or self._cancelled or self._completed
        )

    @property
    def paused(self) -> bool:
        """Whether the timer is paused."""
        return self._paused

    @property
    def cancelled(self) -> bool:
        """Whether the timer was cancelled."""
        return self._cancelled

    @property
    def done(self) -> bool:
        """Whether the timer was cancelled or completed."""
        return self._cancelled or self._completed

    def pause(self) -> None:
        """Pause an unfinished timer without discarding its remaining time."""
        if not self.done:
            self._paused = True

    def resume(self) -> None:
        """Resume an unfinished timer."""
        if not self.done:
            self._paused = False

    def cancel(self) -> None:
        """Cancel this timer; completed or cancelled timers are unchanged."""
        if self.done:
            return
        self._cancelled = True
        self._paused = False

    def _advance(self, delta_time: float) -> int:
        if not self.running:
            return 0
        self._remaining -= delta_time
        callbacks = 0
        while self._remaining <= 0 and self.running:
            callbacks += 1
            if callbacks > self._owner.max_callbacks_per_update:
                raise RuntimeError(
                    "timer exceeded the callback limit for one update"
                )
            if self._calls_remaining is not None:
                self._calls_remaining -= 1
                if self._calls_remaining == 0:
                    self._completed = True
            if not self._completed:
                if self._interval is None:
                    raise RuntimeError(
                        "repeating timer requires an interval"
                    )
                self._remaining += self._interval
            self._callback()
        return callbacks


class SceneTime:
    """Scene-local elapsed time and scheduler advanced by simulation updates.

    All time values and delays are in seconds. Timers do not advance while the
    scene is paused or inactive.
    """

    def __init__(self, *, max_callbacks_per_update: int = 1_000) -> None:
        """Create a clock with a per-update repeating-callback safety limit.

        Raises:
            ValueError: If ``max_callbacks_per_update`` is not a positive int.
        """
        if (
            not isinstance(max_callbacks_per_update, int)
            or isinstance(max_callbacks_per_update, bool)
            or max_callbacks_per_update <= 0
        ):
            raise ValueError("callback limit must be positive")
        self.max_callbacks_per_update = max_callbacks_per_update
        self.elapsed = 0.0
        self.delta = 0.0
        self._timers: list[TimerHandle] = []
        self._pending: list[TimerHandle] = []
        self._updating = False

    def after(
        self,
        delay: float,
        callback: TimerCallback,
    ) -> TimerHandle:
        """Schedule ``callback`` once after ``delay`` seconds.

        A zero delay runs on the next scene update. Timers scheduled from
        within an update are eligible on a subsequent update.

        Args:
            delay (float): Finite, non-negative delay in seconds.
            callback (TimerCallback): Callable invoked with no arguments.

        Returns:
            TimerHandle: A handle that can pause, resume, or cancel the timer.

        Raises:
            ValueError: If ``delay`` is negative or non-finite.
            TypeError: If ``callback`` is not callable.
        """
        if (
            not isinstance(delay, (int, float))
            or isinstance(delay, bool)
            or not isfinite(delay)
            or delay < 0
        ):
            raise ValueError("timer delay must be finite and non-negative")
        return self._schedule(
            delay,
            callback,
            interval=None,
            times=1,
        )

    def every(
        self,
        interval: float,
        callback: TimerCallback,
        *,
        times: int | None = None,
    ) -> TimerHandle:
        """Schedule a callback at a positive interval in seconds.

        Args:
            interval (float): Finite positive time between invocations.
            callback (TimerCallback): Callable invoked with no arguments.
            times (int | None): Maximum number of calls, or ``None`` to repeat indefinitely.

        Returns:
            TimerHandle: A controllable timer handle.

        Raises:
            ValueError: If the interval or finite repetition count is invalid.
            TypeError: If ``callback`` is not callable.
        """
        if (
            not isinstance(interval, (int, float))
            or isinstance(interval, bool)
            or not isfinite(interval)
            or interval <= 0
        ):
            raise ValueError("timer interval must be finite and positive")
        if times is not None and (
            not isinstance(times, int)
            or isinstance(times, bool)
            or times <= 0
        ):
            raise ValueError("timer repetitions must be positive or None")
        return self._schedule(
            interval,
            callback,
            interval=interval,
            times=times,
        )

    def next_update(self, callback: TimerCallback) -> TimerHandle:
        """Schedule a no-argument callback for the next scene update."""
        return self.after(0, callback)

    def cancel_all(self) -> None:
        """Cancel all scheduled and pending timers."""
        for timer in (*self._timers, *self._pending):
            timer.cancel()
        if not self._updating:
            self._timers.clear()
            self._pending.clear()

    def _schedule(
        self,
        delay: float,
        callback: TimerCallback,
        *,
        interval: float | None,
        times: int | None,
    ) -> TimerHandle:
        if not callable(callback):
            raise TypeError("timer callback must be callable")
        timer = TimerHandle(
            self,
            delay,
            callback,
            interval=interval,
            times=times,
        )
        target = self._pending if self._updating else self._timers
        target.append(timer)
        return timer

    def _update(self, delta_time: float) -> None:
        if not isfinite(delta_time) or delta_time < 0:
            raise ValueError("timer delta must be finite and non-negative")
        self.delta = delta_time
        self.elapsed += delta_time
        self._updating = True
        try:
            for timer in tuple(self._timers):
                timer._advance(delta_time)
        finally:
            self._updating = False
            self._timers = [
                timer for timer in self._timers if not timer.done
            ]
            self._timers.extend(
                timer for timer in self._pending if not timer.done
            )
            self._pending.clear()

    def _reset(self) -> None:
        self.cancel_all()
        self._timers.clear()
        self._pending.clear()
        self.elapsed = 0.0
        self.delta = 0.0


class FixedStepAccumulator:
    """Accumulate wall time and invoke updates at a fixed simulation step."""

    def __init__(
        self,
        step: float = 1 / 60,
        *,
        max_steps_per_frame: int = 8,
    ) -> None:
        """Create an accumulator with a step and catch-up limit in seconds.

        Raises:
            ValueError: If ``step`` or ``max_steps_per_frame`` is not positive.
        """
        if step <= 0:
            raise ValueError("step must be positive")
        if max_steps_per_frame <= 0:
            raise ValueError("max_steps_per_frame must be positive")
        self.step = step
        self.max_steps_per_frame = max_steps_per_frame
        self._accumulated = 0.0

    @property
    def alpha(self) -> float:
        """Return the fractional remainder as a value in the range 0–1."""
        return self._accumulated / self.step

    def advance(
        self,
        elapsed: float,
        update: UpdateCallback,
    ) -> int:
        """Advance by elapsed seconds and call ``update(step)`` per fixed step.

        Excess elapsed time beyond the per-frame step limit is discarded.

        Args:
            elapsed (float): Wall-clock time elapsed since the previous call.
            update (UpdateCallback): Callback receiving each fixed step in seconds.

        Returns:
            int: Number of fixed updates performed.

        Raises:
            ValueError: If ``elapsed`` is negative.
        """
        if elapsed < 0:
            raise ValueError("elapsed time must not be negative")
        maximum = self.step * self.max_steps_per_frame
        self._accumulated = min(self._accumulated + elapsed, maximum)
        steps = 0
        while (
            self._accumulated >= self.step
            and steps < self.max_steps_per_frame
        ):
            update(self.step)
            self._accumulated -= self.step
            steps += 1
        return steps

    def reset(self) -> None:
        """Discard accumulated time and reset interpolation alpha to zero."""
        self._accumulated = 0.0
