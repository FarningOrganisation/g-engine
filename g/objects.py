from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeVar

from .components import (
    Area, Collider, CircleRenderer, Component,
    LineRenderer, PolygonRenderer, RectRenderer, RepeatingImageRenderer, ShapeStyle, SpriteRenderer,
    SpriteAnimation, SpriteAnimator, TextRenderer, TriangleRenderer,
)
from .math import Vec2
from .nodes import GameObject
from .resources import Animation, Color, Font, Image, ImageSource


ComponentType = TypeVar("ComponentType", bound=Component)


def create_rect(
    x: float, y: float, color: Color, width: float, height: float,
    angle: float = 0, layer: int = 0, *, z_index: int | None = None,
    style: ShapeStyle | None = None,
) -> GameObject:
    """Create a rectangle object with a renderer and box collider.

    Args:
        x (float): Center x-coordinate in world pixels.
        y (float): Center y-coordinate in world pixels.
        color (Color): Fill color.
        width (float): Rectangle width in pixels.
        height (float): Rectangle height in pixels.
        angle (float): Rotation in degrees.
        layer (int): Legacy alias for ``z_index``.
        z_index (int | None): Drawing order; takes precedence over ``layer``.
        style (ShapeStyle | None): Optional fill/outline styling.

    Returns:
        GameObject: A detached game object.
    """
    obj = GameObject(x, y, rotation=angle, z_index=layer if z_index is None else z_index)
    obj.add_component(RectRenderer(width, height, color, style=style))
    obj.add_component(Collider(shape="box", width=width, height=height))
    return obj


def create_circle(
    x: float, y: float, color: Color, radius: float,
    angle: float = 0, layer: int = 0, *, z_index: int | None = None,
    style: ShapeStyle | None = None,
) -> GameObject:
    """Create a circle object with a renderer and matching circle collider.

    Args:
        x (float): Center x-coordinate in world pixels.
        y (float): Center y-coordinate in world pixels.
        color (Color): Circle fill color.
        radius (float): Circle radius in pixels.
        angle (float): Rotation in degrees.
        layer (int): Legacy alias for ``z_index``.
        z_index (int | None): Drawing order; takes precedence over ``layer``.
        style (ShapeStyle | None): Optional fill/outline styling.

    Returns:
        GameObject: A detached game object.
    """
    obj = GameObject(x, y, rotation=angle, z_index=layer if z_index is None else z_index)
    obj.add_component(CircleRenderer(radius, color, style=style))
    obj.add_component(Collider(shape="circle", radius=radius))
    return obj


def create_sprite(
    image: ImageSource | None, x: float, y: float, angle: float = 0,
    layer: int = 0, *, z_index: int | None = None,
) -> GameObject:
    """Create a sprite object, with a box collider sized to its image.

    Args:
        image (ImageSource | Animation | None): Image, animation, or ``None`` for a placeholder sprite.
        x (float): Center x-coordinate in world pixels.
        y (float): Center y-coordinate in world pixels.
        angle (float): Rotation in degrees.
        layer (int): Legacy alias for ``z_index``.
        z_index (int | None): Drawing order; takes precedence over ``layer``.

    Returns:
        GameObject: A detached object, with an animator when ``image`` is an animation.
    """
    obj = GameObject(x, y, rotation=angle, z_index=layer if z_index is None else z_index)
    animator = None
    if isinstance(image, Animation):
        animator = SpriteAnimator({"default": image})
        image = animator.states["default"].frames[0].image
    renderer = obj.add_component(SpriteRenderer(image))
    obj.add_component(Collider(shape="box", width=renderer.drawable.width, height=renderer.drawable.height))
    if animator is not None:
        obj.add_component(animator)
    return obj


def create_text(
    text: str, x: float, y: float, font: Font | None = None,
    font_size: float = 12, color: Color = (255, 255, 255), angle: float = 0,
    layer: int = 0, *, z_index: int | None = None, screen_space: bool = False,
) -> GameObject:
    """Create a text object anchored at a world-pixel position.

    Args:
        text (str): Text content.
        x (float): Anchor x-coordinate in world pixels.
        y (float): Anchor y-coordinate in world pixels.
        font (Font | None): Optional font resource.
        font_size (float): Font size in points.
        color (Color): Text color.
        angle (float): Rotation in degrees.
        layer (int): Legacy alias for ``z_index``.
        z_index (int | None): Drawing order; takes precedence over ``layer``.
        screen_space (bool): Whether to ignore the camera transform.
    """
    obj = GameObject(x, y, rotation=angle, z_index=layer if z_index is None else z_index)
    obj.add_component(TextRenderer(text, font, font_size, color, screen_space=screen_space))
    return obj


def _center_points(
    points: tuple[tuple[float, float], ...],
) -> tuple[Vec2, tuple[tuple[float, float], ...]]:
    center = Vec2(
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )
    return center, tuple((point[0] - center.x, point[1] - center.y) for point in points)


def create_line(
    start: tuple[float, float], end: tuple[float, float],
    color: Color = (255, 255, 255), thickness: float = 1, layer: int = 0,
    *, z_index: int | None = None, screen_space: bool = False,
) -> GameObject:
    """Create a line object from two world-pixel endpoints.

    Args:
        start (tuple[float, float]): First world-pixel endpoint.
        end (tuple[float, float]): Second world-pixel endpoint.
        color (Color): Line color.
        thickness (float): Line thickness in pixels.
        layer (int): Legacy alias for ``z_index``.
        z_index (int | None): Drawing order; takes precedence over ``layer``.
        screen_space (bool): Whether to ignore the camera transform.
    """
    center, points = _center_points((start, end))
    obj = GameObject(*center, z_index=layer if z_index is None else z_index)
    obj.add_component(LineRenderer(points[0], points[1], color, thickness, screen_space=screen_space))
    return obj


def create_triangle(
    first: tuple[float, float], second: tuple[float, float], third: tuple[float, float],
    color: Color = (255, 255, 255), layer: int = 0, *,
    z_index: int | None = None, style: ShapeStyle | None = None, screen_space: bool = False,
) -> GameObject:
    """Create a triangle object from three world-pixel vertices.

    Args:
        first (tuple[float, float]): First world-pixel vertex.
        second (tuple[float, float]): Second world-pixel vertex.
        third (tuple[float, float]): Third world-pixel vertex.
        color (Color): Fill color.
        layer (int): Legacy alias for ``z_index``.
        z_index (int | None): Drawing order; takes precedence over ``layer``.
        style (ShapeStyle | None): Optional fill/outline styling.
        screen_space (bool): Whether to ignore the camera transform.
    """
    center, points = _center_points((first, second, third))
    obj = GameObject(*center, z_index=layer if z_index is None else z_index)
    obj.add_component(TriangleRenderer((points[0], points[1], points[2]), color, style=style, screen_space=screen_space))
    return obj


def create_polygon(
    points: Sequence[tuple[float, float]], color: Color = (255, 255, 255),
    layer: int = 0, *, z_index: int | None = None,
    style: ShapeStyle | None = None, screen_space: bool = False,
) -> GameObject:
    """Create a polygon object from at least three world-pixel vertices.

    Args:
        points (Sequence[tuple[float, float]]): World-pixel vertices.
        color (Color): Fill color.
        layer (int): Legacy alias for ``z_index``.
        z_index (int | None): Drawing order; takes precedence over ``layer``.
        style (ShapeStyle | None): Optional fill/outline styling.
        screen_space (bool): Whether to ignore the camera transform.

    Raises:
        ValueError: If ``points`` contains fewer than three vertices.
    """
    point_tuple = tuple(points)
    if len(point_tuple) < 3:
        raise ValueError("polygon requires at least three points")
    center, local_points = _center_points(point_tuple)
    obj = GameObject(*center, z_index=layer if z_index is None else z_index)
    obj.add_component(PolygonRenderer(local_points, color, style=style, screen_space=screen_space))
    return obj


def create_area_box(
    x: float, y: float, width: float, height: float, *,
    rotation: float = 0, enabled: bool = True,
) -> GameObject:
    """Create a box sensor object with an ``Area`` component.

    Args:
        x (float): Center x-coordinate in world pixels.
        y (float): Center y-coordinate in world pixels.
        width (float): Box width in pixels.
        height (float): Box height in pixels.
        rotation (float): Rotation in degrees.
        enabled (bool): Whether the object starts enabled.
    """
    obj = GameObject(x, y, rotation=rotation, enabled=enabled)
    obj.add_component(Collider(shape="box", width=width, height=height, sensor=True))
    obj.add_component(Area())
    return obj


def create_background(
    image: Image | None = None, *,
    tile_size: Vec2 | tuple[float, float] | None = None,
    repeat_x: bool = True, repeat_y: bool = True,
    offset: Vec2 | tuple[float, float] = (0, 0),
    parallax: Vec2 | tuple[float, float] = (0, 0),
    scroll_speed: Vec2 | tuple[float, float] = (0, 0),
    opacity: int = 255,
) -> GameObject:
    """Create a repeating background renderer object.

    Args:
        image (Image | None): Optional background image.
        tile_size (Vec2 | tuple[float, float] | None): Tile size in pixels.
        repeat_x (bool): Whether to repeat horizontally.
        repeat_y (bool): Whether to repeat vertically.
        offset (Vec2 | tuple[float, float]): Image offset in pixels.
        parallax (Vec2 | tuple[float, float]): Camera-motion multiplier.
        scroll_speed (Vec2 | tuple[float, float]): Scroll speed in pixels per second.
        opacity (int): Opacity from 0 to 255.
    """
    obj = GameObject()
    obj.add_component(RepeatingImageRenderer(
        image, tile_size=tile_size, repeat_x=repeat_x, repeat_y=repeat_y,
        offset=offset, parallax=parallax, scroll_speed=scroll_speed, opacity=opacity,
    ))
    return obj


def create_area_circle(
    x: float, y: float, radius: float, *, enabled: bool = True,
) -> GameObject:
    """Create a circular sensor object with an ``Area`` component.

    Args:
        x (float): Center x-coordinate in world pixels.
        y (float): Center y-coordinate in world pixels.
        radius (float): Radius in pixels.
        enabled (bool): Whether the object starts enabled.
    """
    obj = GameObject(x, y, enabled=enabled)
    obj.add_component(Collider(shape="circle", radius=radius, sensor=True))
    obj.add_component(Area())
    return obj


def create_stateful_sprite(
    states: Mapping[str, ImageSource | SpriteAnimation | None], initial_state: str, x: float, y: float,
    angle: float = 0, layer: int = 0, *, z_index: int | None = None,
) -> GameObject:
    """Create a sprite object with named image or animation states.

    Args:
        states (Mapping[str, ImageSource | SpriteAnimation | None]): Mapping from state names to image, animation, or ``None``.
        initial_state (str): State to display initially.
        x (float): Object center x-coordinate in world pixels.
        y (float): Object center y-coordinate in world pixels.
        angle (float): Rotation in degrees.
        layer (int): Legacy alias for ``z_index``.
        z_index (int | None): Drawing order; takes precedence over ``layer``.

    Raises:
        ValueError: If no states are provided or the initial state is unknown.
    """
    if not states:
        raise ValueError("states must not be empty")
    if initial_state not in states:
        raise ValueError(f"unknown initial state: {initial_state!r}")
    animator = SpriteAnimator(states, initial_state)
    obj = create_sprite(animator.states[initial_state].frames[0].image, x, y, angle, layer, z_index=z_index)
    obj.add_component(animator)
    return obj


class StatefulGameObject(GameObject):
    """A game object that displays one of several child visual objects."""

    def __init__(
        self, states: Mapping[str, GameObject], initial_state: str,
        layer: int | None = None, *, z_index: int | None = None,
    ) -> None:
        """Create a stateful parent and attach each supplied visual as a child.

        Args:
            states (Mapping[str, GameObject]): Non-empty mapping of unique state names to distinct objects.
            initial_state (str): Name of the initially visible visual.
            layer (int | None): Legacy drawing-order alias.
            z_index (int | None): Parent drawing order; overrides ``layer``.

        Raises:
            ValueError: If states are empty, the initial state is missing, or a
                visual is reused for multiple states.
        """
        if not states:
            raise ValueError("states must not be empty")
        if initial_state not in states:
            raise ValueError(f"unknown initial state: {initial_state!r}")
        if len({id(visual) for visual in states.values()}) != len(states):
            raise ValueError("each state must use a different game object")
        initial_visual = states[initial_state]
        resolved_z_index = (
            initial_visual.z_index if layer is None and z_index is None
            else layer if z_index is None else z_index
        )
        super().__init__(
            initial_visual.x, initial_visual.y, rotation=initial_visual.rotation,
            z_index=resolved_z_index if resolved_z_index is not None else 0,
        )
        self._states = dict(states)
        self._state = initial_state
        for visual in self._states.values():
            visual.position = (0, 0)
            visual.rotation = 0
            visual.z_index = self.z_index
            self.add_child(visual)
        self._update_visibility()

    @property
    def state(self) -> str:
        """Name of the currently visible state."""
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        """Select a state; unknown names raise ``ValueError``."""
        self.set_state(value)

    @property
    def states(self) -> tuple[str, ...]:
        """Return state names in mapping insertion order."""
        return tuple(self._states)

    def set_state(self, state: str) -> None:
        """Show the visual for ``state`` and hide all other state visuals.

        Raises:
            ValueError: If ``state`` is not defined.
        """
        if state not in self._states:
            raise ValueError(f"unknown state: {state!r}")
        self._state = state
        self._update_visibility()

    def visual_for(self, state: str) -> GameObject:
        """Return the child visual for a state.

        Raises:
            ValueError: If ``state`` is not defined.
        """
        try:
            return self._states[state]
        except KeyError:
            raise ValueError(f"unknown state: {state!r}") from None

    def get_component(self, component_type: type[ComponentType]) -> ComponentType | None:
        """Find a component on this object, then on the active visual."""
        own_component = super().get_component(component_type)
        if own_component is not None:
            return own_component
        return self._states[self._state].get_component(component_type)

    def _update_visibility(self) -> None:
        for name, visual in self._states.items():
            visual.visible = name == self._state
