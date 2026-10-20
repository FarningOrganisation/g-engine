"""Components used by the native 2D lighting pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from math import cos, isfinite, radians, sin, tau

from ..inspector import inspector_field
from ..resources import Color
from .base import Component
from .renderer_utils import _transform_point


Point = tuple[float, float]
DEFAULT_OCCLUDER_POINTS: tuple[Point, ...] = (
    (-32.0, -32.0),
    (32.0, -32.0),
    (32.0, 32.0),
    (-32.0, 32.0),
)


def _positive_finite(value: float, name: str) -> float:
    number = float(value)
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def _non_negative_finite(value: float, name: str) -> float:
    number = float(value)
    if not isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _color(value: Color) -> Color:
    channels = tuple(value)
    if len(channels) not in {3, 4} or any(
        not isinstance(channel, int) or not 0 <= channel <= 255
        for channel in channels
    ):
        raise ValueError("color must contain three or four byte channels")
    return channels  # type: ignore[return-value]


def _mask(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"{name} must be a 32-bit unsigned integer")
    return value


class Light2D(Component, ABC):
    """Shared settings for lights rendered by the native 2D lighting pipeline.

    Attach a concrete :class:`PointLight2D` or :class:`SpotLight2D` rather than
    this base class. An enabled light automatically activates lighting for its
    scene; scenes without enabled lights retain the direct rendering path.
    """

    __inspector_fields__ = {
        "color": inspector_field(
            [255, 255, 255, 255],
            widget="color",
            group="Light",
            tooltip="Color contributed by this light.",
        ),
        "intensity": inspector_field(
            1.0,
            minimum=0,
            group="Light",
            tooltip="Brightness multiplier; zero contributes no light.",
        ),
        "falloff": inspector_field(
            1.0,
            minimum=0.001,
            group="Light",
            tooltip="How quickly brightness decreases toward the light edge.",
        ),
        "casts_shadows": inspector_field(
            False,
            group="Shadows",
            tooltip="Clip this light against matching Shadow Occluder 2D components.",
        ),
        "shadow_mask": inspector_field(
            0xFFFFFFFF,
            widget="mask",
            group="Filtering",
            group_expanded=False,
            tooltip="Occluder layers that can block this light.",
        ),
    }

    def __init__(
        self,
        *,
        color: Color = (255, 255, 255, 255),
        intensity: float = 1.0,
        falloff: float = 1.0,
        casts_shadows: bool = False,
        shadow_mask: int = 0xFFFFFFFF,
        enabled: bool = True,
    ) -> None:
        """Configure settings shared by point and spot lights.

        Args:
            color: RGB or RGBA byte color contributed by the light.
            intensity: Non-negative brightness multiplier.
            falloff: Positive radial falloff exponent.
            casts_shadows: Whether matching occluders block the light.
            shadow_mask: 32-bit mask selecting blocking occluders.
            enabled: Whether this light participates in rendering.
        """
        super().__init__(enabled=enabled)
        self.color = _color(color)
        self.intensity = _non_negative_finite(intensity, "intensity")
        self.falloff = _positive_finite(falloff, "falloff")
        self.casts_shadows = bool(casts_shadows)
        self.shadow_mask = _mask(shadow_mask, "shadow_mask")

    @property
    @abstractmethod
    def maximum_range(self) -> float:
        """Maximum world-space reach used for culling and shadow rays."""
        raise NotImplementedError


class PointLight2D(Light2D):
    """Omnidirectional light centered on its owner's world position."""

    __inspector_fields__ = {
        "radius": inspector_field(
            256.0,
            minimum=0.001,
            group="Light",
            tooltip="Maximum light range in world pixels.",
        ),
    }

    def __init__(
        self,
        radius: float = 256,
        *,
        color: Color = (255, 255, 255, 255),
        intensity: float = 1.0,
        falloff: float = 1.0,
        casts_shadows: bool = False,
        shadow_mask: int = 0xFFFFFFFF,
        enabled: bool = True,
    ) -> None:
        """Create an omnidirectional point light.

        Args:
            radius: Positive maximum range in world pixels.
            color: RGB or RGBA byte color contributed by the light.
            intensity: Non-negative brightness multiplier.
            falloff: Positive radial falloff exponent.
            casts_shadows: Whether matching occluders block the light.
            shadow_mask: 32-bit mask selecting blocking occluders.
            enabled: Whether this light participates in rendering.
        """
        super().__init__(
            color=color,
            intensity=intensity,
            falloff=falloff,
            casts_shadows=casts_shadows,
            shadow_mask=shadow_mask,
            enabled=enabled,
        )
        self.radius = _positive_finite(radius, "radius")

    @property
    def maximum_range(self) -> float:
        """Point-light radius in world pixels."""
        return self.radius


class SpotLight2D(Light2D):
    """Cone light aimed along its owner's positive local x-axis."""

    __inspector_fields__ = {
        "range": inspector_field(
            256.0,
            minimum=0.001,
            group="Light",
            tooltip="Maximum spotlight range in world pixels.",
        ),
        "angle": inspector_field(
            50.0,
            minimum=0.001,
            maximum=360,
            group="Cone",
            tooltip="Outer cone angle in degrees.",
        ),
        "inner_angle": inspector_field(
            40.0,
            minimum=0,
            maximum=360,
            group="Cone",
            tooltip="Fully illuminated cone angle before edge fading begins.",
        ),
    }

    def __init__(
        self,
        range: float = 256,
        *,
        angle: float = 50,
        inner_angle: float | None = None,
        color: Color = (255, 255, 255, 255),
        intensity: float = 1.0,
        falloff: float = 1.0,
        casts_shadows: bool = False,
        shadow_mask: int = 0xFFFFFFFF,
        enabled: bool = True,
    ) -> None:
        """Create a spotlight aimed by the owner's world rotation.

        Args:
            range: Positive maximum range in world pixels.
            angle: Outer cone angle in degrees, greater than zero and at most 360.
            inner_angle: Fully lit cone angle. Defaults to 80 percent of
                ``angle`` and must be between zero and ``angle``.
            color: RGB or RGBA byte color contributed by the light.
            intensity: Non-negative brightness multiplier.
            falloff: Positive radial falloff exponent.
            casts_shadows: Whether matching occluders block the light.
            shadow_mask: 32-bit mask selecting blocking occluders.
            enabled: Whether this light participates in rendering.
        """
        super().__init__(
            color=color,
            intensity=intensity,
            falloff=falloff,
            casts_shadows=casts_shadows,
            shadow_mask=shadow_mask,
            enabled=enabled,
        )
        self.range = _positive_finite(range, "range")
        outer = _positive_finite(angle, "angle")
        if outer > 360:
            raise ValueError("angle must not exceed 360 degrees")
        inner = outer * 0.8 if inner_angle is None else float(inner_angle)
        if not isfinite(inner) or not 0 <= inner <= outer:
            raise ValueError("inner_angle must be finite and between zero and angle")
        self.angle = outer
        self.inner_angle = inner

    @property
    def maximum_range(self) -> float:
        """Spotlight range in world pixels."""
        return self.range


class ShadowOccluder2D(Component):
    """Explicit local polygon whose world-space edges block shadowed lights."""

    __inspector_fields__ = {
        "points": inspector_field(
            [list(point) for point in DEFAULT_OCCLUDER_POINTS],
            group="Shape",
            tooltip="Closed polygon vertices in local coordinates.",
        ),
        "occlusion_mask": inspector_field(
            0xFFFFFFFF,
            widget="mask",
            group="Filtering",
            group_expanded=False,
            tooltip="Shadow masks that this polygon blocks.",
        ),
    }

    def __init__(
        self,
        points: Sequence[Sequence[float]] = DEFAULT_OCCLUDER_POINTS,
        *,
        occlusion_mask: int = 0xFFFFFFFF,
        enabled: bool = True,
    ) -> None:
        """Create a closed polygonal shadow occluder.

        Args:
            points: At least three finite local-space polygon vertices.
            occlusion_mask: 32-bit mask matched against a light's shadow mask.
            enabled: Whether this occluder blocks shadow-casting lights.
        """
        super().__init__(enabled=enabled)
        converted = tuple((float(point[0]), float(point[1])) for point in points)
        if len(converted) < 3:
            raise ValueError("shadow occluder requires at least three points")
        if any(not isfinite(value) for point in converted for value in point):
            raise ValueError("shadow occluder points must be finite")
        if len(set(converted)) < 3:
            raise ValueError("shadow occluder requires three distinct points")
        self.points = converted
        self.occlusion_mask = _mask(occlusion_mask, "occlusion_mask")

    @classmethod
    def rectangle(
        cls,
        width: float,
        height: float,
        **options: object,
    ) -> ShadowOccluder2D:
        """Create a centered rectangular occluder.

        Args:
            width: Positive local width.
            height: Positive local height.
            **options: Additional constructor options such as
                ``occlusion_mask`` and ``enabled``.
        """
        half_width = _positive_finite(width, "width") / 2
        half_height = _positive_finite(height, "height") / 2
        return cls(
            (
                (-half_width, -half_height),
                (half_width, -half_height),
                (half_width, half_height),
                (-half_width, half_height),
            ),
            **options,
        )

    @classmethod
    def circle(
        cls,
        radius: float,
        *,
        segments: int = 16,
        **options: object,
    ) -> ShadowOccluder2D:
        """Create a polygonal approximation of a centered circle.

        Args:
            radius: Positive local radius.
            segments: Number of polygon edges, at least three.
            **options: Additional constructor options such as
                ``occlusion_mask`` and ``enabled``.
        """
        radius = _positive_finite(radius, "radius")
        if not isinstance(segments, int) or isinstance(segments, bool) or segments < 3:
            raise ValueError("segments must be an integer of at least three")
        return cls(
            tuple(
                (
                    cos(tau * index / segments) * radius,
                    sin(tau * index / segments) * radius,
                )
                for index in range(segments)
            ),
            **options,
        )

    @property
    def world_points(self) -> tuple[Point, ...]:
        """Polygon vertices transformed into world coordinates."""
        if self.owner is None:
            return self.points
        return tuple(_transform_point(self.owner, point) for point in self.points)

    @property
    def world_segments(self) -> tuple[tuple[Point, Point], ...]:
        """Closed polygon edges transformed into world coordinates."""
        points = self.world_points
        return tuple(
            (points[index], points[(index + 1) % len(points)])
            for index in range(len(points))
        )


__all__ = [
    "Light2D",
    "PointLight2D",
    "ShadowOccluder2D",
    "SpotLight2D",
]
