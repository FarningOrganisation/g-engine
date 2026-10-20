from __future__ import annotations

from .base import Component
from ..behaviours import AnimationHandle, Behaviour, BehaviourStatus

class BehaviourRunner(Component):
    """Execute one behavior and expose its running, paused, and result state.

    Attributes:
        status: Current BehaviourStatus, or ``None`` when no behavior is set.
        paused: Whether execution is paused.
    """
    def __init__(self, behaviour: Behaviour | None = None) -> None:
        """Create a runner with an optional behavior to execute."""
        super().__init__()
        self._behaviour: Behaviour | None = None
        self._animation: AnimationHandle | None = None
        self.status: BehaviourStatus | None = None
        self.paused = False
        if behaviour is not None:
            self.behaviour = behaviour

    @property
    def behaviour(self) -> Behaviour | None:
        """Current behavior, if one is assigned."""
        return self._behaviour

    @behaviour.setter
    def behaviour(self, value: Behaviour | None) -> None:
        """Replace the current behavior and reset its execution state."""
        self.cancel()
        self._animation = None
        self._behaviour = value
        self.paused = False
        if value is None:
            self.status = None
            return
        value.reset()
        self.status = BehaviourStatus.RUNNING

    def update(self, delta_time: float) -> None:
        """Advance the active behavior and update its animation handle."""
        if (
            self.owner is None
            or self._behaviour is None
            or self.paused
        ):
            return
        if self.status is not BehaviourStatus.RUNNING:
            return
        self.status = self._behaviour.update(self.owner, delta_time)
        animation = self._animation
        if animation is None:
            return
        animation._status = self.status
        if self.status is not BehaviourStatus.SUCCESS:
            return
        if animation._loop:
            self._behaviour.reset()
            self.status = BehaviourStatus.RUNNING
            animation._status = BehaviourStatus.RUNNING
            return
        callback = animation._on_complete
        if callback is not None:
            callback(self.owner)

    def play_animation(
        self,
        behaviour: Behaviour,
        *,
        loop: bool = False,
        on_complete: Callable[[GameObject], None] | None = None,
    ) -> AnimationHandle:
        """Start a behavior as an animation and return its control handle.

        Args:
            behaviour: Behavior to execute.
            loop: Restart the behavior after each successful run.
            on_complete: Optional callback invoked after a non-looping success.
        """
        handle = AnimationHandle(
            self,
            behaviour,
            loop=loop,
            on_complete=on_complete,
        )
        self._activate_handle(handle)
        return handle

    def cancel(self) -> None:
        """Cancel the active run, if it is currently running."""
        if self.status is not BehaviourStatus.RUNNING:
            return
        self.status = BehaviourStatus.CANCELLED
        self.paused = False
        if self._animation is not None:
            self._animation._status = BehaviourStatus.CANCELLED
            self._animation._paused = False

    def restart(self) -> None:
        """Reset and restart the current behavior when one is assigned."""
        if self._behaviour is None:
            return
        self._behaviour.reset()
        self.status = BehaviourStatus.RUNNING
        self.paused = False
        if self._animation is not None:
            self._animation._status = BehaviourStatus.RUNNING
            self._animation._paused = False

    def pause(self) -> None:
        """Pause the active run."""
        if self.status is BehaviourStatus.RUNNING:
            self.paused = True
            if self._animation is not None:
                self._animation._paused = True

    def resume(self) -> None:
        """Resume the active run."""
        if self.status is BehaviourStatus.RUNNING:
            self.paused = False
            if self._animation is not None:
                self._animation._paused = False

    def _activate_handle(self, handle: AnimationHandle) -> None:
        self.cancel()
        self._animation = handle
        self._behaviour = handle._behaviour
        self._behaviour.reset()
        self.status = BehaviourStatus.RUNNING
        self.paused = False
        handle._status = BehaviourStatus.RUNNING
        handle._paused = False

    def _pause_handle(self, handle: AnimationHandle) -> None:
        if self._animation is handle:
            self.pause()

    def _resume_handle(self, handle: AnimationHandle) -> None:
        if self._animation is handle:
            self.resume()

    def _cancel_handle(self, handle: AnimationHandle) -> None:
        if self._animation is handle:
            self.cancel()

    def _restart_handle(self, handle: AnimationHandle) -> None:
        self._activate_handle(handle)
