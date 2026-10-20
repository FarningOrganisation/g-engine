from __future__ import annotations
from collections.abc import Mapping

from collections.abc import Mapping
from math import isfinite

from .base import Component, requires_components
from .sprite_animation import SpriteAnimation
from .sprite_renderer import SpriteRenderer
from ..inspector import inspector_field
from ..resources import Animation, Image

@requires_components(SpriteRenderer)
class SpriteAnimator(Component):
    """Play named static-image or timed sprite animation states.

    Requires a :class:`SpriteRenderer` on the same game object.

    Attributes:
        states: Animation definitions indexed by state name.
        initial_state: State selected at construction.
        autoplay: Whether playback starts automatically.
    """
    __inspector_fields__ = {
        "initial_state": inspector_field("default", widget="animation", tooltip="Animation state selected on startup."),
        "autoplay": inspector_field(True, widget="animation", tooltip="Start playing the initial animation automatically."),
        "states": inspector_field(
            [{"name": "default", "loop": True, "image": None, "animated": False}],
            widget="animation", tooltip="Named states using an image, animation asset, or timed image frames.",
        ),
    }
    def __init__(
        self,
        states: Mapping[str, SpriteAnimation | Image | Animation | None] | None = None,
        initial_state: str = "default",
        *,
        autoplay: bool = True,
        enabled: bool = True,
    ) -> None:
        """Create an animator with named animation states.

        Args:
            states: Mapping from names to sprite animations, image assets,
                animation assets, or ``None``.
            initial_state: State selected when the component is created.
            autoplay: Begin playing that state automatically.
            enabled: Whether animation updates are enabled.

        Raises:
            ValueError: States are empty, a name is empty, or the initial state
                is unknown.
            TypeError: A state value has an unsupported type.
        """
        super().__init__(enabled=enabled)
        if states is None:
            states = {"default": None}
        if not states:
            raise ValueError("sprite animation states must not be empty")
        if any(not name for name in states):
            raise ValueError(
                "sprite animation state names must not be empty"
            )
        if initial_state not in states:
            raise ValueError(
                f"unknown initial sprite animation state: "
                f"{initial_state!r}"
            )
        if any(value is not None and not isinstance(value, (SpriteAnimation, Image, Animation)) for value in states.values()):
            raise TypeError("sprite states must contain Images, Animations, SpriteAnimations, or None")
        self.states = {
            name: (
                value if isinstance(value, SpriteAnimation)
                else SpriteAnimation.from_animation(value) if isinstance(value, Animation)
                else SpriteAnimation.from_image(value)
            )
            for name, value in states.items()
        }
        self.initial_state = initial_state
        self.autoplay = autoplay
        self._state = initial_state
        self._elapsed = 0.0
        self._frame_index = 0
        self._playing = autoplay
        self._paused = False
        self._renderer: SpriteRenderer | None = None

    def on_attach(self) -> None:
        """Find the required SpriteRenderer and apply the initial frame."""
        if self.owner is None:
            raise RuntimeError("sprite animator is not attached")
        self._renderer = self.owner.require_component(SpriteRenderer)
        self._apply_frame(0)

    @property
    def state(self) -> str:
        """Name of the selected animation state."""
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        """Select and start an animation state."""
        self.play(value)

    @property
    def playing(self) -> bool:
        """Whether animation playback is active."""
        return self._playing

    @property
    def paused(self) -> bool:
        """Whether active playback is paused."""
        return self._paused

    @property
    def frame_index(self) -> int:
        """Index of the currently displayed frame."""
        return self._frame_index

    def play(self, state: str, *, restart: bool = False) -> None:
        """Start a named state, optionally restarting the current state.

        Raises:
            ValueError: The state name is not configured.
        """
        if state not in self.states:
            raise ValueError(
                f"unknown sprite animation state: {state!r}"
            )
        if (
            state == self._state
            and self._playing
            and not restart
        ):
            self._paused = False
            return
        self._state = state
        self._elapsed = 0.0
        self._frame_index = 0
        self._playing = True
        self._paused = False
        self._apply_frame(0)

    def stop(self) -> None:
        """Stop playback while leaving the current frame displayed."""
        self._playing = False
        self._paused = False

    def pause(self) -> None:
        """Pause playback if it is active."""
        if self._playing:
            self._paused = True

    def resume(self) -> None:
        """Resume playback if it is active."""
        if self._playing:
            self._paused = False

    def update(self, delta_time: float) -> None:
        """Advance playback and update the renderer when the frame changes.

        Args:
            delta_time: Non-negative elapsed time in seconds.

        Raises:
            ValueError: ``delta_time`` is negative or non-finite.
        """
        if not isfinite(delta_time) or delta_time < 0:
            raise ValueError(
                "sprite animation delta time must be non-negative"
            )
        if not self._playing or self._paused or delta_time == 0:
            return
        animation = self.states[self._state]
        self._elapsed += delta_time
        frame_index = animation.frame_index_at(self._elapsed)
        if frame_index != self._frame_index:
            self._frame_index = frame_index
            self._apply_frame(frame_index)
        if not animation.loop and self._elapsed >= animation.duration:
            self._playing = False

    def _apply_frame(self, index: int) -> None:
        if self._renderer is not None:
            self._renderer.image = self.states[
                self._state
            ].frames[index].image

    def save_state(self) -> Mapping[str, object]:
        """Return state, elapsed time, and playback flags for checkpoints."""
        return {"state": self._state, "elapsed": self._elapsed,
                "playing": self._playing, "paused": self._paused}

    def load_state(self, state: Mapping[str, object]) -> None:
        """Restore and display a validated animation checkpoint.

        Raises:
            ValueError: The state mapping contains invalid checkpoint values.
        """
        name, elapsed = state.get("state"), state.get("elapsed")
        playing, paused = state.get("playing"), state.get("paused")
        if (not isinstance(name, str) or (name not in self.states and (self.states or name != ""))
                or not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
                or not isfinite(elapsed) or elapsed < 0
                or not isinstance(playing, bool) or not isinstance(paused, bool)):
            raise ValueError("Invalid SpriteAnimator checkpoint state")
        self._state, self._elapsed = name, float(elapsed)
        self._playing, self._paused = playing, paused
        self._frame_index = self.states[name].frame_index_at(self._elapsed) if self.states else 0
        if self.states:
            self._apply_frame(self._frame_index)
