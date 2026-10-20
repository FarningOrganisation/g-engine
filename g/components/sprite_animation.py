from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from math import isfinite

from ..resources import Animation, Image, source_of

@dataclass(frozen=True)
class SpriteAnimationFrame:
    """One static image and its display duration.

    Attributes:
        image: Frame image, or ``None`` for a blank frame.
        duration: Positive display duration in seconds.
    """
    image: Image | None
    duration: float

    def __post_init__(self) -> None:
        """Validate the image type and positive finite duration."""
        if self.image is not None and not isinstance(self.image, Image):
            raise TypeError("sprite animation frames must contain static Images or None")
        if not isfinite(self.duration) or self.duration <= 0:
            raise ValueError(
                "sprite animation frame duration must be positive"
            )

@dataclass(frozen=True)
class SpriteAnimation:
    """Timed sequence of static sprite frames.

    Attributes:
        frames: Ordered frame sequence.
        loop: Whether playback repeats after the final frame.
        source: Original image or animation asset, when available.
    """
    frames: tuple[SpriteAnimationFrame, ...]
    loop: bool = True
    source: Image | Animation | None = None

    def __post_init__(self) -> None:
        """Require at least one frame."""
        if not self.frames:
            raise ValueError("sprite animation must contain frames")

    @classmethod
    def from_image(cls, image: Image | None) -> SpriteAnimation:
        """Wrap an image in a single one-second frame.

        Args:
            image: Frame image, or ``None`` for a blank frame.

        Returns:
            A looping animation containing a single one-second frame.
        """
        return cls((SpriteAnimationFrame(image, 1.0),), source=image)

    @classmethod
    def from_animation(
        cls, animation: Animation, *, durations: Sequence[float] | None = None,
    ) -> SpriteAnimation:
        """Convert a resource animation into timed static-image frames.

        Args:
            animation: Animation asset to convert.
            durations: Optional per-frame durations overriding asset timings.

        Raises:
            ValueError: The asset has no frames or duration count differs from
                the number of frames.
        """
        frames = source_of(animation).frames
        if not frames:
            raise ValueError("sprite animation must contain frames")
        if durations is not None and len(durations) != len(frames):
            raise ValueError("animation durations must match the asset frame count")
        # Pyglet marks a non-looping final frame with duration=None.
        fallback = next(
            (frame.duration for frame in reversed(frames) if frame.duration is not None),
            0.1,
        )
        return cls(
            tuple(
                SpriteAnimationFrame(
                    Image._from_source(frame.image),
                    durations[index] if durations is not None else
                    frame.duration if frame.duration is not None else fallback,
                )
                for index, frame in enumerate(frames)
            ),
            loop=frames[-1].duration is not None,
            source=animation,
        )

    @property
    def duration(self) -> float:
        """Sum of all frame durations in seconds."""
        return sum(frame.duration for frame in self.frames)

    def frame_index_at(self, elapsed: float) -> int:
        """Find the frame displayed at elapsed playback time.

        Args:
            elapsed: Non-negative elapsed time in seconds.

        Raises:
            ValueError: Elapsed time is negative or non-finite.
        """
        if not isfinite(elapsed) or elapsed < 0:
            raise ValueError(
                "sprite animation elapsed time must be non-negative"
            )
        position = (
            elapsed % self.duration
            if self.loop
            else min(elapsed, self.duration)
        )
        for index, frame in enumerate(self.frames):
            if position < frame.duration:
                return index
            position -= frame.duration
        return len(self.frames) - 1
