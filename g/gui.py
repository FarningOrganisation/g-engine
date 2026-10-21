from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING, Literal, TypeVar, cast
from uuid import uuid4

import pyglet
from pyglet.gui import TextEntry as PygletTextEntry
from pyglet.window import key, mouse

from .behaviours import (
    Behaviour,
    BehaviourStatus,
    Ease,
    Easing,
    Parallel,
    Repeat,
    Sequence as BehaviourSequence,
    Tween,
    Wait,
)
from .resources import Color, Font

if TYPE_CHECKING:
    from .render_stats import _RenderStatsBuilder
    from .scene import Scene


ClickHandler = Callable[[], None]
ToggleHandler = Callable[[bool], None]
ValueHandler = Callable[[float], None]
CommitHandler = Callable[[str], None]
Padding = float | tuple[float, float] | tuple[float, float, float, float]


def _rgba(color: Color) -> tuple[int, int, int, int]:
    return color if len(color) == 4 else (*color, 255)


def _padding(value: Padding) -> tuple[float, float, float, float]:
    if isinstance(value, (int, float)):
        return value, value, value, value
    if len(value) == 2:
        horizontal, vertical = value
        return horizontal, vertical, horizontal, vertical
    return value


@dataclass(frozen=True)
class SliderStyle:
    """Track and knob appearance settings for sliders.

    Attributes:
        track: Track RGB or RGBA color.
        knob: Knob RGB or RGBA color.
        track_thickness: Track height in UI units.
        knob_radius: Knob radius in UI units.
    """

    track: Color = (100, 100, 100)
    knob: Color = (220, 220, 220)
    track_thickness: float = 4
    knob_radius: float = 8


@dataclass(frozen=True)
class TextInputStyle:
    """Background, text, and caret colors for text inputs.

    Attributes:
        background: Entry background color.
        text_color: Text color.
        caret_color: Caret color.
    """

    background: Color = (255, 255, 255)
    text_color: Color = (0, 0, 0)
    caret_color: Color = (0, 0, 0)


class WidgetAnimationHandle:
    """Control and inspect an active widget animation."""

    def __init__(
        self,
        widget: Widget,
        behaviour: Behaviour,
        channel: str,
        *,
        loop: bool,
        on_complete: Callable[[Widget], None] | None,
    ) -> None:
        """Create a handle for one widget animation.

        Args:
            widget (Widget): Animated widget.
            behaviour (Behaviour): Built animation sequence.
            channel (str): Channel used to identify the animation.
            loop (bool): Whether to restart after successful completion.
            on_complete (Callable[[Widget], None] | None): Optional callback invoked after successful completion.
        """
        self.widget = widget
        self.behaviour = behaviour
        self.channel = channel
        self.loop = loop
        self.on_complete = on_complete
        self.status = BehaviourStatus.RUNNING
        self.paused = False

    @property
    def running(self) -> bool:
        """Whether the animation is running and not paused."""
        return self.status is BehaviourStatus.RUNNING and not self.paused

    def pause(self) -> None:
        """Pause animation updates."""
        self.paused = True

    def resume(self) -> None:
        """Resume updates if the animation is still running."""
        if self.status is BehaviourStatus.RUNNING:
            self.paused = False

    def cancel(self) -> None:
        """Cancel the animation and remove it from its widget."""
        self.status = BehaviourStatus.CANCELLED
        self.widget._animations.pop(self.channel, None)

    def restart(self) -> None:
        """Reset and restart the animation on its widget."""
        self.behaviour.reset()
        self.status = BehaviourStatus.RUNNING
        self.paused = False
        self.widget._animations[self.channel] = self

    def _update(self, delta_time: float) -> None:
        if not self.running:
            return
        status = self.behaviour.update(self.widget, delta_time)  # type: ignore[arg-type]
        if status is BehaviourStatus.RUNNING:
            return
        if status is BehaviourStatus.SUCCESS and self.loop:
            self.behaviour.reset()
            return
        self.status = status
        self.widget._animations.pop(self.channel, None)
        if status is BehaviourStatus.SUCCESS and self.on_complete is not None:
            self.on_complete(self.widget)


class WidgetAnimationBuilder:
    """Fluent builder for animations of widget properties.

    Args:
        widget (Widget): Widget whose properties will be animated.
        channel (str): Animation channel, replacing any existing animation on it.
    """

    def __init__(self, widget: Widget, channel: str = "default") -> None:
        """Initialize a builder for the widget and named animation channel."""
        self._widget = widget
        self._channel = channel
        self._steps: list[Behaviour] = []

    def to(
        self,
        duration: float,
        easing: Easing = Ease.linear,
        **properties: int | float | Color,
    ) -> WidgetAnimationBuilder:
        """Append a property tween and return this builder.

        Args:
            duration (float): Tween duration in seconds.
            easing (Easing): Function mapping normalized progress to eased progress.
            **properties (int | float | Color): Target numeric or color values.
        """
        self._steps.append(Tween(duration, properties, easing))
        return self

    def wait(self, duration: float) -> WidgetAnimationBuilder:
        """Append a delay step and return this builder."""
        self._steps.append(Wait(duration))
        return self

    def repeat(self, times: int | None = None) -> WidgetAnimationBuilder:
        """Repeat accumulated steps.

        Args:
            times (int | None): Successful repetitions, or ``None`` for an infinite loop.

        Returns:
            WidgetAnimationBuilder: This builder.

        Raises:
            ValueError: If no step has been added.
        """
        if not self._steps:
            raise ValueError("animation must contain a step before repeat")
        self._steps = [Repeat(BehaviourSequence(*self._steps), times)]
        return self

    def parallel(
        self,
        *animations: WidgetAnimationBuilder | Behaviour,
    ) -> WidgetAnimationBuilder:
        """Append concurrent animations or behaviours.

        Args:
            *animations (WidgetAnimationBuilder | Behaviour): Builders for this widget or behaviour instances.

        Returns:
            WidgetAnimationBuilder: This builder.

        Raises:
            ValueError: If no animations are given or a builder targets another widget.
        """
        if not animations:
            raise ValueError("animations must not be empty")
        behaviours: list[Behaviour] = []
        for animation in animations:
            if isinstance(animation, WidgetAnimationBuilder):
                if animation._widget is not self._widget:
                    raise ValueError(
                        "parallel animations must target the same widget"
                    )
                behaviours.append(animation._build())
            else:
                behaviours.append(animation)
        self._steps.append(Parallel(*behaviours))
        return self

    def _build(self) -> Behaviour:
        if not self._steps:
            raise ValueError("animation must contain at least one step")
        return BehaviourSequence(*self._steps)

    def start(
        self,
        *,
        loop: bool = False,
        on_complete: Callable[[Widget], None] | None = None,
    ) -> WidgetAnimationHandle:
        """Start the animation and return its control handle.

        Args:
            loop (bool): Whether to restart after successful completion.
            on_complete (Callable[[Widget], None] | None): Optional callback invoked when the animation succeeds.

        Returns:
            WidgetAnimationHandle: A handle for pausing, resuming, cancelling, or restarting.
        """
        existing = self._widget._animations.get(self._channel)
        if existing is not None:
            existing.cancel()
        handle = WidgetAnimationHandle(
            self._widget,
            self._build(),
            self._channel,
            loop=loop,
            on_complete=on_complete,
        )
        self._widget._animations[self._channel] = handle
        return handle


class Widget:
    """Base class for a laid-out, interactive GUI element.

    Args:
        width (float | None): Requested width, or ``None`` to use preferred size.
        height (float | None): Requested height, or ``None`` to use preferred size.
        min_width (float): Minimum measured width.
        min_height (float): Minimum measured height.
        grow (float): Relative share of available layout space.
        margin (Padding): Uniform, horizontal/vertical, or four-sided margin.
        visible (bool): Whether the widget is visible.
        enabled (bool): Whether the widget accepts input.
        layer (int): Drawing order within its view.
        identifier (str | None): Optional stable identifier.
        name (str | None): Optional readable name.
        focusable (bool): Whether keyboard focus can be assigned.
        interaction (InteractionStyle | None): Optional state-driven visual style.

    Raises:
        ValueError: If dimensions, minimums, or growth are negative.
    """

    def __init__(
        self,
        *,
        width: float | None = None,
        height: float | None = None,
        min_width: float = 0,
        min_height: float = 0,
        grow: float = 0,
        margin: Padding = 0,
        visible: bool = True,
        enabled: bool = True,
        layer: int = 0,
        identifier: str | None = None,
        name: str | None = None,
        focusable: bool = False,
        interaction: InteractionStyle | None = None,
    ) -> None:
        """Initialize layout, interaction, and presentation defaults."""
        if width is not None and width < 0 or height is not None and height < 0:
            raise ValueError("widget width and height must not be negative")
        if min_width < 0 or min_height < 0 or grow < 0:
            raise ValueError("widget minimums and grow must not be negative")
        self.requested_width = width
        self.requested_height = height
        self.min_width = min_width
        self.min_height = min_height
        self.grow = grow
        self.margin = _padding(margin)
        self.visible = visible
        self.enabled = enabled
        self.layer = layer
        self.id = identifier or str(uuid4())
        self.name = name or type(self).__name__
        self.focusable = focusable
        self.interaction = interaction
        self.parent: Container | None = None
        self.view: UIView | None = None
        self.x = 0.0
        self.y = 0.0
        self.width = 0.0
        self.height = 0.0
        self.hovered = False
        self.focused = False
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.opacity = 255
        self._capturing_pointer = False
        self._mounted = False
        self._animations: dict[str, WidgetAnimationHandle] = {}
        self._interaction_state: str | None = None

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Untransformed bounds as ``(left, bottom, right, top)``."""
        return self.x, self.y, self.x + self.width, self.y + self.height

    @property
    def visible_in_hierarchy(self) -> bool:
        """Whether this mounted widget and its ancestors are visible."""
        return self._mounted and self.visible and (
            self.parent is None or self.parent.visible_in_hierarchy
        )

    @property
    def enabled_in_hierarchy(self) -> bool:
        """Whether this widget and its ancestors are enabled."""
        return self.enabled and (
            self.parent is None or self.parent.enabled_in_hierarchy
        )

    def measure(
        self, available_width: float, available_height: float
    ) -> tuple[float, float]:
        """Measure the widget within available space.

        Args:
            available_width (float): Width available to the widget.
            available_height (float): Height available to the widget.

        Returns:
            tuple[float, float]: Requested or preferred size, bounded by minimum dimensions.
        """
        del available_width, available_height
        preferred_width, preferred_height = self.preferred_size()
        return (
            max(self.min_width, self.requested_width or preferred_width),
            max(self.min_height, self.requested_height or preferred_height),
        )

    @property
    def ui_scale(self) -> float:
        """Scale applied by the containing GUI manager, or one if unmounted."""
        if self.view is None or self.view.manager is None:
            return 1.0
        return self.view.manager.scale

    @property
    def effective_opacity(self) -> int:
        """Opacity after multiplying opacity from all ancestors."""
        opacity = self.opacity / 255
        parent = self.parent
        while parent is not None:
            opacity *= parent.opacity / 255
            parent = parent.parent
        return max(0, min(255, round(opacity * 255)))

    def _transform_point(self, x: float, y: float) -> tuple[float, float]:
        current: Widget | None = self
        while current is not None:
            center_x = current.x + current.width / 2
            center_y = current.y + current.height / 2
            x = (
                center_x
                + (x - center_x) * current.scale_x
                + current.offset_x
            )
            y = (
                center_y
                + (y - center_y) * current.scale_y
                + current.offset_y
            )
            current = current.parent
        scale = self.ui_scale
        return x * scale, y * scale

    def _inverse_transform_point(
        self,
        x: float,
        y: float,
    ) -> tuple[float, float] | None:
        scale = self.ui_scale
        x, y = x / scale, y / scale
        chain: list[Widget] = []
        current: Widget | None = self
        while current is not None:
            chain.append(current)
            current = current.parent
        for item in reversed(chain):
            if item.scale_x == 0 or item.scale_y == 0:
                return None
            center_x = item.x + item.width / 2
            center_y = item.y + item.height / 2
            x = center_x + (x - item.offset_x - center_x) / item.scale_x
            y = center_y + (y - item.offset_y - center_y) / item.scale_y
        return x, y

    @property
    def presented_bounds(self) -> tuple[float, float, float, float]:
        """Bounds after widget transforms and GUI scaling."""
        first = self._transform_point(self.x, self.y)
        second = self._transform_point(
            self.x + self.width,
            self.y + self.height,
        )
        return (
            min(first[0], second[0]),
            min(first[1], second[1]),
            max(first[0], second[0]),
            max(first[1], second[1]),
        )

    def preferred_size(self) -> tuple[float, float]:
        """Return the widget's intrinsic size; the base implementation is zero."""
        return 0, 0

    def arrange(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        """Assign bounds in UI coordinates and synchronize drawables."""
        self.x, self.y = x, y
        self.width, self.height = max(0, width), max(0, height)
        self._sync_drawables()

    def hit_test(self, x: float, y: float) -> Widget | None:
        """Return this widget if it contains the point in its local bounds."""
        """Return this widget if the presented point falls within its bounds."""
        if not self.visible_in_hierarchy or not self.enabled_in_hierarchy:
            return None
        local = self._inverse_transform_point(x, y)
        if local is None:
            return None
        x, y = local
        left, bottom, right, top = self.bounds
        return self if left <= x <= right and bottom <= y <= top else None

    def _bind(
        self,
        view: UIView,
        parent_group: pyglet.graphics.Group | None = None,
    ) -> None:
        if self.view is not None and self.view is not view:
            raise ValueError("widget already belongs to another GUI view")
        if self.view is view:
            return
        self.view = view
        self._create_drawables(
            view.batch,
            view.group_for(self.layer, parent_group),
        )

    def _create_drawables(
        self,
        batch: pyglet.graphics.Batch,
        group: pyglet.graphics.Group,
    ) -> None:
        del batch, group

    def _sync_drawables(self) -> None:
        pass

    def _sync_tree(self) -> None:
        self._sync_drawables()

    def _set_mounted(self, mounted: bool) -> None:
        self._mounted = mounted
        self._sync_tree()

    def on_pointer_enter(self) -> None:
        """Handle pointer entry by updating hover and interaction state."""
        self.hovered = True
        self._update_interaction_state()

    def on_pointer_leave(self) -> None:
        """Handle pointer exit by clearing hover state."""
        self.hovered = False
        self._update_interaction_state()

    def on_pointer_press(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Handle a pointer press; return whether it was consumed."""
        del x, y, button, modifiers
        return False

    def on_pointer_release(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Handle a pointer release; return whether it was consumed."""
        del x, y, button, modifiers
        return False

    def on_pointer_drag(
        self,
        x: float,
        y: float,
        delta_x: float,
        delta_y: float,
        buttons: int,
        modifiers: int,
    ) -> bool:
        """Handle a pointer drag; return whether it was consumed."""
        del x, y, delta_x, delta_y, buttons, modifiers
        return False

    def on_scroll(self, scroll_x: float, scroll_y: float) -> bool:
        """Handle pointer scrolling and report whether it was consumed."""
        del scroll_x, scroll_y
        return False

    def on_key_press(self, symbol: int, modifiers: int) -> bool:
        """Handle a key press and report whether it was consumed."""
        del symbol, modifiers
        return False

    def on_key_release(self, symbol: int, modifiers: int) -> bool:
        """Handle a key release and report whether it was consumed."""
        del symbol, modifiers
        return False

    def on_text(self, text: str) -> bool:
        """Handle text input and report whether it was consumed."""
        del text
        return False

    def on_text_motion(self, motion: int, *, select: bool = False) -> bool:
        """Handle a text-navigation command and report whether it was consumed."""
        del motion, select
        return False

    def set_focus(self, focused: bool) -> None:
        """Set keyboard focus and refresh interaction visuals."""
        self.focused = focused
        self._update_interaction_state()

    def animate(self, channel: str = "default") -> WidgetAnimationBuilder:
        """Create an animation builder for this widget and channel."""
        return WidgetAnimationBuilder(self, channel)

    def _update_animations(self, delta_time: float) -> None:
        for handle in tuple(self._animations.values()):
            handle._update(delta_time)
        self._sync_drawables()

    def _update_animation_tree(self, delta_time: float) -> None:
        self._update_animations(delta_time)

    def _resolved_interaction(self) -> InteractionStyle | None:
        return self.interaction

    def _update_interaction_state(self) -> None:
        interaction = self._resolved_interaction()
        if interaction is None:
            return
        if not self.enabled_in_hierarchy:
            state = "disabled"
        elif bool(getattr(self, "pressed", False)):
            state = "pressed"
        elif bool(getattr(self, "value", False)):
            state = "selected"
        elif self.hovered:
            state = "hovered"
        elif self.focused:
            state = "focused"
        else:
            state = "normal"
        if state == self._interaction_state:
            return
        self._interaction_state = state
        visual = getattr(interaction, state)
        transition = interaction.transition
        self.animate("interaction").to(
            transition.duration,
            transition.easing,
            offset_x=visual.offset_x,
            offset_y=visual.offset_y,
            scale_x=visual.scale_x,
            scale_y=visual.scale_y,
            opacity=visual.opacity,
        ).start()


WidgetType = TypeVar("WidgetType", bound=Widget)


class Container(Widget):
    """Widget that owns and arranges child widgets."""

    def __init__(self, *children: Widget, **kwargs) -> None:
        """Initialize a container and optionally add its initial children."""
        super().__init__(**kwargs)
        self._children: list[Widget] = []
        self._content_group: pyglet.graphics.Group | None = None
        for child in children:
            self.add(child)

    @property
    def children(self) -> tuple[Widget, ...]:
        """Direct child widgets as an immutable snapshot."""
        return tuple(self._children)

    def add(self, widget: WidgetType) -> WidgetType:
        """Add and return a child widget.

        Raises:
            ValueError: If the widget already belongs to a container.
        """
        if widget.parent is not None:
            raise ValueError("widget already belongs to a container")
        widget.parent = self
        self._children.append(widget)
        widget._set_mounted(self._mounted)
        if self.view is not None:
            widget._bind(self.view, self._content_group)
            self.view.invalidate_layout()
        return widget

    def remove(self, widget: WidgetType) -> WidgetType:
        """Detach and return a child widget.

        Raises:
            ValueError: If the widget is not a child of this container.
        """
        if widget.parent is not self:
            raise ValueError("widget does not belong to this container")
        self._children.remove(widget)
        widget._set_mounted(False)
        widget.parent = None
        if self.view is not None:
            self.view.invalidate_layout()
        return widget

    def _bind(
        self,
        view: UIView,
        parent_group: pyglet.graphics.Group | None = None,
    ) -> None:
        self._content_group = parent_group
        super()._bind(view, parent_group)
        for child in self._children:
            child._bind(view, parent_group)

    def _sync_tree(self) -> None:
        super()._sync_tree()
        for child in self._children:
            child._sync_tree()

    def _update_animation_tree(self, delta_time: float) -> None:
        super()._update_animation_tree(delta_time)
        for child in self._children:
            child._update_animation_tree(delta_time)

    def _set_mounted(self, mounted: bool) -> None:
        self._mounted = mounted
        for child in self._children:
            child._set_mounted(mounted)
        self._sync_drawables()

    def hit_test(self, x: float, y: float) -> Widget | None:
        """Return the topmost hit child, or this container if hit."""
        if not self.visible_in_hierarchy or not self.enabled_in_hierarchy:
            return None
        for child in sorted(
            self._children,
            key=lambda item: item.layer,
            reverse=True,
        ):
            target = child.hit_test(x, y)
            if target is not None:
                return target
        return super().hit_test(x, y)


class StackLayout(Container):
    """Overlay children in a shared area with optional padding.

    Args:
        *children (Widget): Initial widgets to add.
        padding (Padding): Uniform, horizontal/vertical, or four-sided inner padding.
        **kwargs (Any): Options forwarded to :class:`Widget`.
    """

    def __init__(
        self,
        *children: Widget,
        padding: Padding = 0,
        **kwargs,
    ) -> None:
        """Initialize a padded overlapping layout."""
        super().__init__(*children, **kwargs)
        self.padding = _padding(padding)

    def preferred_size(self) -> tuple[float, float]:
        """Return the maximum child size plus layout padding."""
        left, bottom, right, top = self.padding
        sizes = [child.measure(float("inf"), float("inf")) for child in self.children]
        return (
            max((size[0] for size in sizes), default=0) + left + right,
            max((size[1] for size in sizes), default=0) + bottom + top,
        )

    def arrange(
        self, x: float, y: float, width: float, height: float
    ) -> None:
        """Center and size children within the available inner area."""
        super().arrange(x, y, width, height)
        left, bottom, right, top = self.padding
        for child in self.children:
            margin_left, margin_bottom, margin_right, margin_top = child.margin
            desired_width, desired_height = child.measure(width, height)
            child_width = (
                max(0, width - left - right - margin_left - margin_right)
                if child.grow
                else min(desired_width, width - left - right)
            )
            child_height = (
                max(0, height - bottom - top - margin_bottom - margin_top)
                if child.grow
                else min(desired_height, height - bottom - top)
            )
            child.arrange(
                x + left + (width - left - right - child_width) / 2,
                y + bottom + (height - bottom - top - child_height) / 2,
                child_width,
                child_height,
            )


class CanvasLayout(Container):
    """Arrange children at explicit coordinates on a free-form canvas.

    Args:
        *children (Widget): Initial widgets, placed at the origin.
        **kwargs (Any): Options forwarded to :class:`Container`.
    """

    def __init__(self, *children: Widget, **kwargs) -> None:
        """Initialize a canvas and place initial children at its origin."""
        super().__init__(**kwargs)
        self._positions: dict[Widget, tuple[float, float]] = {}
        for child in children:
            self.add_at(child)

    def add_at(
        self,
        widget: WidgetType,
        x: float = 0,
        y: float = 0,
    ) -> WidgetType:
        """Add a child at a canvas position.

        Args:
            widget (WidgetType): Widget to add.
            x (float): Left coordinate relative to the canvas.
            y (float): Bottom coordinate relative to the canvas.

        Returns:
            WidgetType: The added widget.
        """
        super().add(widget)
        self._positions[widget] = (float(x), float(y))
        return widget

    def add(self, widget: WidgetType) -> WidgetType:
        """Add a child at the canvas origin."""
        return self.add_at(widget)

    def remove(self, widget: WidgetType) -> WidgetType:
        """Remove a child and its stored position."""
        removed = super().remove(widget)
        del self._positions[widget]
        return removed

    def position_of(self, widget: Widget) -> tuple[float, float]:
        """Return a child's canvas position.

        Raises:
            ValueError: If the widget is not on this canvas.
        """
        if widget not in self._positions:
            raise ValueError("widget does not belong to this canvas")
        return self._positions[widget]

    def set_position(self, widget: Widget, x: float, y: float) -> None:
        """Set a child's canvas position and invalidate layout.

        Raises:
            ValueError: If the widget is not on this canvas.
        """
        if widget not in self._positions:
            raise ValueError("widget does not belong to this canvas")
        self._positions[widget] = (float(x), float(y))
        if self.view is not None:
            self.view.invalidate_layout()

    def preferred_size(self) -> tuple[float, float]:
        """Return extents of positioned children."""
        extents = [
            (
                self._positions[child][0]
                + child.measure(float("inf"), float("inf"))[0],
                self._positions[child][1]
                + child.measure(float("inf"), float("inf"))[1],
            )
            for child in self.children
        ]
        return (
            max((extent[0] for extent in extents), default=0),
            max((extent[1] for extent in extents), default=0),
        )

    def arrange(
        self, x: float, y: float, width: float, height: float
    ) -> None:
        """Arrange children at their configured canvas coordinates."""
        Widget.arrange(self, x, y, width, height)
        for child in self.children:
            child_x, child_y = self._positions[child]
            child_width, child_height = child.measure(width, height)
            child.arrange(
                x + child_x,
                y + child_y,
                child_width,
                child_height,
            )


class LinearLayout(Container):
    """Arrange children along one axis and align them on the other.

    Args:
        *children (Widget): Initial widgets.
        spacing (float): Gap between adjacent children.
        padding (Padding): Inner spacing.
        alignment (str): Cross-axis alignment.
        **kwargs (Any): Options forwarded to :class:`Container`.
    """

    vertical = False

    def __init__(
        self,
        *children: Widget,
        spacing: float = 0,
        padding: Padding = 0,
        alignment: str = "center",
        **kwargs,
    ) -> None:
        """Create a linear layout.

        Args:
            *children (Widget): Initial widgets.
            spacing (float): Gap between adjacent children.
            padding (Padding): Inner spacing.
            alignment (str): Cross-axis alignment (``"start"``, ``"center"``, or
                ``"end"``).
            **kwargs (Any): Options forwarded to :class:`Container`.

        Raises:
            ValueError: If spacing is negative.
        """
        if spacing < 0:
            raise ValueError("spacing must not be negative")
        super().__init__(*children, **kwargs)
        self.spacing = spacing
        self.padding = _padding(padding)
        self.alignment = alignment

    def preferred_size(self) -> tuple[float, float]:
        """Return the combined preferred size of children and spacing."""
        sizes = [child.measure(float("inf"), float("inf")) for child in self.children]
        left, bottom, right, top = self.padding
        spacing = self.spacing * max(0, len(sizes) - 1)
        if self.vertical:
            return (
                max((size[0] for size in sizes), default=0) + left + right,
                sum(size[1] for size in sizes) + spacing + bottom + top,
            )
        return (
            sum(size[0] for size in sizes) + spacing + left + right,
            max((size[1] for size in sizes), default=0) + bottom + top,
        )

    def arrange(
        self, x: float, y: float, width: float, height: float
    ) -> None:
        """Arrange children along the layout axis, distributing grow space."""
        super().arrange(x, y, width, height)
        left, bottom, right, top = self.padding
        available_main = (
            height - bottom - top if self.vertical else width - left - right
        )
        available_cross = (
            width - left - right if self.vertical else height - bottom - top
        )
        measured = [
            child.measure(width, height) for child in self.children
        ]
        main_sizes = [
            size[1] if self.vertical else size[0] for size in measured
        ]
        total_grow = sum(child.grow for child in self.children)
        remaining = max(
            0,
            available_main
            - sum(main_sizes)
            - self.spacing * max(0, len(self.children) - 1),
        )
        if total_grow:
            main_sizes = [
                size + remaining * child.grow / total_grow
                for child, size in zip(self.children, main_sizes)
            ]
        cursor = y + height - top if self.vertical else x + left
        for child, measured_size, main_size in zip(
            self.children, measured, main_sizes
        ):
            margin_left, margin_bottom, margin_right, margin_top = child.margin
            desired_cross = measured_size[0] if self.vertical else measured_size[1]
            cross_size = min(
                desired_cross,
                available_cross
                - (margin_left + margin_right if self.vertical else margin_bottom + margin_top),
            )
            if child.grow:
                cross_size = available_cross
            if self.alignment == "start":
                cross_position = (
                    x + left + margin_left
                    if self.vertical
                    else y + bottom + margin_bottom
                )
            elif self.alignment == "end":
                cross_position = (
                    x + width - right - cross_size - margin_right
                    if self.vertical
                    else y + height - top - cross_size - margin_top
                )
            else:
                cross_position = (
                    x + left + (available_cross - cross_size) / 2
                    if self.vertical
                    else y + bottom + (available_cross - cross_size) / 2
                )
            if self.vertical:
                cursor -= main_size
                child.arrange(cross_position, cursor, cross_size, main_size)
                cursor -= self.spacing
            else:
                child.arrange(cursor, cross_position, main_size, cross_size)
                cursor += main_size + self.spacing


class VBox(LinearLayout):
    """Arrange children vertically from top to bottom."""

    vertical = True


class HBox(LinearLayout):
    """Arrange children horizontally from left to right."""

    vertical = False


class AnchorLayout(Container):
    """Position one child against selected horizontal and vertical anchors.

    Args:
        child (Widget | None): Optional initial child.
        horizontal (str): ``"left"``, ``"center"``, or ``"right"`` alignment.
        vertical (str): ``"bottom"``, ``"center"``, or ``"top"`` alignment.
        margin (Padding): Inner spacing around the child.
        **kwargs (Any): Options forwarded to :class:`Container`.
    """

    def __init__(
        self,
        child: Widget | None = None,
        *,
        horizontal: str = "center",
        vertical: str = "center",
        margin: Padding = 0,
        **kwargs,
    ) -> None:
        """Initialize an anchored single-child layout."""
        children = (child,) if child is not None else ()
        super().__init__(*children, **kwargs)
        self.horizontal = horizontal
        self.vertical = vertical
        self.anchor_margin = _padding(margin)

    def add(self, widget: WidgetType) -> WidgetType:
        """Add the layout's only child.

        Raises:
            ValueError: If this layout already contains a child.
        """
        if self.children:
            raise ValueError("anchor layout can contain only one child")
        return super().add(widget)

    def preferred_size(self) -> tuple[float, float]:
        """Return the child's preferred size plus anchor margins."""
        if not self.children:
            left, bottom, right, top = self.anchor_margin
            return left + right, bottom + top
        child_width, child_height = self.children[0].measure(
            float("inf"), float("inf")
        )
        left, bottom, right, top = self.anchor_margin
        return child_width + left + right, child_height + bottom + top

    def arrange(
        self, x: float, y: float, width: float, height: float
    ) -> None:
        """Place the child against its horizontal and vertical anchors."""
        super().arrange(x, y, width, height)
        if not self.children:
            return
        child = self.children[0]
        child_width, child_height = child.measure(width, height)
        left, bottom, right, top = self.anchor_margin
        child_x = {
            "left": x + left,
            "center": x + (width - child_width) / 2,
            "right": x + width - right - child_width,
        }[self.horizontal]
        child_y = {
            "bottom": y + bottom,
            "center": y + (height - child_height) / 2,
            "top": y + height - top - child_height,
        }[self.vertical]
        child.arrange(child_x, child_y, child_width, child_height)


class GridLayout(Container):
    """Arrange children in a fixed-column grid.

    Args:
        *children (Widget): Initial widgets.
        columns (int): Number of grid columns; must be positive.
        row_spacing (float): Vertical spacing between rows.
        column_spacing (float): Horizontal spacing between columns.
        padding (Padding): Inner spacing around the grid.
        **kwargs (Any): Options forwarded to :class:`Container`.

    Raises:
        ValueError: If ``columns`` is not positive.
    """

    def __init__(
        self,
        *children: Widget,
        columns: int,
        row_spacing: float = 0,
        column_spacing: float = 0,
        padding: Padding = 0,
        **kwargs,
    ) -> None:
        """Initialize a fixed-column grid."""
        if columns <= 0:
            raise ValueError("columns must be positive")
        super().__init__(*children, **kwargs)
        self.columns = columns
        self.row_spacing = row_spacing
        self.column_spacing = column_spacing
        self.padding = _padding(padding)

    def preferred_size(self) -> tuple[float, float]:
        """Return the preferred dimensions of the full grid."""
        sizes = [child.measure(float("inf"), float("inf")) for child in self.children]
        rows = max(1, ceil(len(sizes) / self.columns))
        cell_width = max((size[0] for size in sizes), default=0)
        cell_height = max((size[1] for size in sizes), default=0)
        left, bottom, right, top = self.padding
        return (
            cell_width * self.columns
            + self.column_spacing * (self.columns - 1)
            + left
            + right,
            cell_height * rows
            + self.row_spacing * (rows - 1)
            + bottom
            + top,
        )

    def arrange(
        self, x: float, y: float, width: float, height: float
    ) -> None:
        """Arrange children in row-major order in equal-sized cells."""
        super().arrange(x, y, width, height)
        left, bottom, right, top = self.padding
        rows = max(1, ceil(len(self.children) / self.columns))
        cell_width = (
            width - left - right - self.column_spacing * (self.columns - 1)
        ) / self.columns
        cell_height = (
            height - bottom - top - self.row_spacing * (rows - 1)
        ) / rows
        for index, child in enumerate(self.children):
            column = index % self.columns
            row = index // self.columns
            child.arrange(
                x + left + column * (cell_width + self.column_spacing),
                y
                + height
                - top
                - (row + 1) * cell_height
                - row * self.row_spacing,
                cell_width,
                cell_height,
            )


class Label(Widget):
    """Display styled single-line or multiline text.

    Args:
        text (str): Initial text.
        font (Font | None): Optional font resource.
        font_size (float | None): Font size in points.
        color (Color | None): Text RGB or RGBA color.
        style (LabelStyle | None): Optional style overriding theme defaults.
        align (str): Horizontal text alignment.
        multiline (bool): Whether text may wrap to multiple lines.
        **kwargs (Any): Options forwarded to :class:`Widget`.

    Raises:
        ValueError: If ``align`` is not ``"left"``, ``"center"``, or ``"right"``.
    """

    def __init__(
        self,
        text: str,
        *,
        font: Font | None = None,
        font_size: float | None = None,
        color: Color | None = None,
        style: LabelStyle | None = None,
        align: str = "center",
        multiline: bool = False,
        **kwargs,
    ) -> None:
        """Initialize label text, typography, and alignment."""
        super().__init__(**kwargs)
        if align not in {"left", "center", "right"}:
            raise ValueError("label align must be 'left', 'center', or 'right'")
        resolved = style or LabelStyle()
        self._text = text
        self.font = font if font is not None else resolved.font
        self.font_size = font_size if font_size is not None else resolved.font_size
        self.color = color if color is not None else resolved.color
        self.align: Literal["left", "center", "right"] = cast(
            Literal["left", "center", "right"],
            align,
        )
        self.multiline = multiline
        self._uses_theme = style is None and font is None and font_size is None and color is None
        self._label: pyglet.text.Label | None = None

    @property
    def text(self) -> str:
        """Current label text."""
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        """Set label text and refresh layout and rendering."""
        if value == self._text:
            return
        self._text = value
        if self.view is not None:
            self.view.invalidate_layout()
        self._sync_drawables()

    def _bind(
        self,
        view: UIView,
        parent_group: pyglet.graphics.Group | None = None,
    ) -> None:
        if self._uses_theme:
            style = view.theme.label
            self.font = style.font
            self.font_size = style.font_size
            self.color = style.color
        super()._bind(view, parent_group)

    def preferred_size(self) -> tuple[float, float]:
        """Return the measured or estimated text dimensions."""
        if self._label is not None:
            return self._label.content_width, self._label.content_height
        return max(1, len(self.text)) * self.font_size * 0.6, self.font_size * 1.4

    def _create_drawables(self, batch, group) -> None:
        self._label = pyglet.text.Label(
            self.text,
            font_name=None if self.font is None else self.font.family,
            font_size=self.font_size,
            color=self.color,
            anchor_x=self.align,
            anchor_y="center",
            width=(
                round(max(1, self.requested_width or self.preferred_size()[0]))
                if self.multiline
                else None
            ),
            multiline=self.multiline,
            batch=batch,
            group=group,
        )

    def _sync_drawables(self) -> None:
        if self._label is not None:
            left, bottom, right, top = self.presented_bounds
            anchor_x = {
                "left": left,
                "center": (left + right) / 2,
                "right": right,
            }[self.align]
            self._label.position = (
                anchor_x,
                (bottom + top) / 2,
                0,
            )
            self._label.text = self.text
            self._label.color = (*_rgba(self.color)[:3], self.effective_opacity)
            if self.multiline:
                self._label.width = round(max(1, right - left))
            self._label.visible = self.visible_in_hierarchy


class Panel(CanvasLayout):
    """Canvas container with a solid background rectangle.

    Args:
        color (Color | None): Optional background RGB or RGBA color.
        style (PanelStyle | None): Optional panel style.
        **kwargs (Any): Options forwarded to :class:`CanvasLayout`.
    """

    def __init__(
        self,
        color: Color | None = None,
        style: PanelStyle | None = None,
        **kwargs,
    ) -> None:
        """Initialize a panel background and canvas content."""
        super().__init__(**kwargs)
        self.color = color or (style or PanelStyle()).color
        self._uses_theme = style is None and color is None
        self._shape: pyglet.shapes.Rectangle | None = None

    def _bind(
        self,
        view: UIView,
        parent_group: pyglet.graphics.Group | None = None,
    ) -> None:
        if self._uses_theme:
            self.color = view.theme.panel.color
        super()._bind(view, parent_group)

    def _create_drawables(self, batch, group) -> None:
        self._shape = pyglet.shapes.Rectangle(
            0,
            0,
            0,
            0,
            color=self.color[:3],
            batch=batch,
            group=group,
        )
        self._shape.opacity = _rgba(self.color)[3]

    def _sync_drawables(self) -> None:
        if self._shape is not None:
            left, bottom, right, top = self.presented_bounds
            self._shape.position = left, bottom
            self._shape.width = right - left
            self._shape.height = top - bottom
            self._shape.opacity = round(
                _rgba(self.color)[3] * self.effective_opacity / 255
            )
            self._shape.visible = self.visible_in_hierarchy


@dataclass(frozen=True)
class WidgetVisual:
    """Target transform and opacity for one interaction state.

    Attributes:
        offset_x: Horizontal presentation offset.
        offset_y: Vertical presentation offset.
        scale_x: Horizontal presentation scale.
        scale_y: Vertical presentation scale.
        opacity: Presentation opacity from zero to 255.
    """

    offset_x: float = 0
    offset_y: float = 0
    scale_x: float = 1
    scale_y: float = 1
    opacity: int = 255


@dataclass(frozen=True)
class StateTransition:
    """Timing and easing used to animate interaction-state changes.

    Attributes:
        duration: Transition duration in seconds.
        easing: Function mapping normalized progress to eased progress.
    """

    duration: float = 0.12
    easing: Easing = Ease.out_quad


@dataclass(frozen=True)
class InteractionStyle:
    """Visual targets for normal, hovered, pressed, focused, selected, and disabled states.

    Attributes:
        normal: Appearance when no other state applies.
        hovered: Appearance while the pointer is over the widget.
        pressed: Appearance while activated.
        focused: Appearance while keyboard-focused.
        selected: Appearance while selected.
        disabled: Appearance while disabled.
        transition: Animation timing for state changes.
    """

    normal: WidgetVisual = WidgetVisual()
    hovered: WidgetVisual = WidgetVisual(scale_x=1.04, scale_y=1.04)
    pressed: WidgetVisual = WidgetVisual(scale_x=0.97, scale_y=0.97)
    focused: WidgetVisual = WidgetVisual(scale_x=1.04, scale_y=1.04)
    selected: WidgetVisual = WidgetVisual()
    disabled: WidgetVisual = WidgetVisual(opacity=160)
    transition: StateTransition = StateTransition()


@dataclass(frozen=True)
class LabelStyle:
    """Font and color defaults for labels.

    Attributes:
        font: Optional font resource.
        font_size: Font size in points.
        color: Text RGB or RGBA color.
    """

    font: Font | None = None
    font_size: float = 12
    color: Color = (255, 255, 255)


@dataclass(frozen=True)
class PanelStyle:
    """Background color default for panels.

    Attributes:
        color: Panel RGB or RGBA background color.
    """

    color: Color = (40, 40, 40, 230)


@dataclass(frozen=True)
class ButtonStyle:
    """Background, text, and font settings for buttons.

    Attributes:
        background: Default background color.
        hover: Background color while hovered.
        pressed: Background color while pressed or selected.
        disabled: Background color while disabled.
        text_color: Label color.
        font: Optional font resource.
        font_size: Label font size in points.
    """

    background: Color = (55, 65, 85)
    hover: Color = (75, 90, 120)
    pressed: Color = (35, 42, 58)
    disabled: Color = (65, 65, 65)
    text_color: Color = (255, 255, 255)
    font: Font | None = None
    font_size: float = 16


@dataclass(frozen=True)
class UITheme:
    """Collection of default styles for GUI controls.

    Attributes:
        label: Default label style.
        panel: Default panel style.
        button: Default button style.
        slider: Default slider style.
        text_input: Default text-input style.
        button_interaction: Default button interaction animation.
    """

    label: LabelStyle = field(default_factory=LabelStyle)
    panel: PanelStyle = field(default_factory=PanelStyle)
    button: ButtonStyle = field(default_factory=ButtonStyle)
    slider: SliderStyle = field(default_factory=SliderStyle)
    text_input: TextInputStyle = field(default_factory=TextInputStyle)
    button_interaction: InteractionStyle = field(
        default_factory=InteractionStyle
    )


class Button(Widget):
    """Clickable, keyboard-focusable text button.

    Args:
        text (str): Button label.
        on_click (ClickHandler | None): Optional callback invoked on activation.
        style (ButtonStyle | None): Optional button style; otherwise uses the view theme.
        width (float | None): Requested width.
        height (float | None): Requested height.
        **kwargs (Any): Options forwarded to :class:`Widget`.
    """

    def __init__(
        self,
        text: str,
        on_click: ClickHandler | None = None,
        *,
        style: ButtonStyle | None = None,
        width: float | None = 180,
        height: float | None = 48,
        **kwargs,
    ) -> None:
        """Initialize the button label, callback, and visual style."""
        kwargs.setdefault("focusable", True)
        super().__init__(width=width, height=height, **kwargs)
        self.text = text
        self.on_click = on_click
        self.style = style or ButtonStyle()
        self._uses_theme = style is None
        self.pressed = False
        self._background: pyglet.shapes.Rectangle | None = None
        self._label: pyglet.text.Label | None = None

    def _bind(
        self,
        view: UIView,
        parent_group: pyglet.graphics.Group | None = None,
    ) -> None:
        if self._uses_theme:
            self.style = view.theme.button
        if self.interaction is None:
            self.interaction = view.theme.button_interaction
        super()._bind(view, parent_group)
        self._update_interaction_state()

    def preferred_size(self) -> tuple[float, float]:
        """Return the default button dimensions."""
        return 180, 48

    def _create_drawables(self, batch, group) -> None:
        self._background = pyglet.shapes.Rectangle(
            0, 0, 0, 0, batch=batch, group=group
        )
        self._label = pyglet.text.Label(
            self.text,
            font_name=(
                None if self.style.font is None else self.style.font.family
            ),
            font_size=self.style.font_size,
            color=self.style.text_color,
            anchor_x="center",
            anchor_y="center",
            batch=batch,
            group=pyglet.graphics.Group(order=self.layer + 1, parent=group),
        )
        self._sync_drawables()

    def _current_color(self) -> Color:
        if not self.enabled_in_hierarchy:
            return self.style.disabled
        if self.pressed:
            return self.style.pressed
        if self.hovered:
            return self.style.hover
        return self.style.background

    def _sync_drawables(self) -> None:
        if self._background is None or self._label is None:
            return
        color = _rgba(self._current_color())
        left, bottom, right, top = self.presented_bounds
        self._background.position = left, bottom
        self._background.width = right - left
        self._background.height = top - bottom
        self._background.color = color[:3]
        self._background.opacity = round(
            color[3] * self.effective_opacity / 255
        )
        self._background.visible = self.visible_in_hierarchy
        self._label.position = (
            (left + right) / 2,
            (bottom + top) / 2,
            0,
        )
        self._label.text = self.text
        self._label.color = (
            *_rgba(self.style.text_color)[:3],
            self.effective_opacity,
        )
        self._label.visible = self.visible_in_hierarchy

    def on_pointer_enter(self) -> None:
        """Refresh visuals when the pointer enters the button."""
        super().on_pointer_enter()
        self._sync_drawables()

    def on_pointer_leave(self) -> None:
        """Refresh visuals when the pointer leaves the button."""
        super().on_pointer_leave()
        self._sync_drawables()

    def on_pointer_press(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Press on left-click and capture the pointer."""
        del x, y, modifiers
        if button != mouse.LEFT:
            return False
        self.pressed = True
        self._capturing_pointer = True
        self._update_interaction_state()
        self._sync_drawables()
        return True

    def on_pointer_release(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Release the button and activate it if the pointer is still inside."""
        del button, modifiers
        was_pressed = self.pressed
        self.pressed = False
        self._capturing_pointer = False
        self._update_interaction_state()
        self._sync_drawables()
        if was_pressed and self.hit_test(x, y) is self and self.on_click is not None:
            self.on_click()
        return was_pressed

    def on_key_press(self, symbol: int, modifiers: int) -> bool:
        """Press on Enter, Return, or Space."""
        del modifiers
        if symbol not in (key.ENTER, key.RETURN, key.SPACE):
            return False
        self.pressed = True
        self._update_interaction_state()
        return True

    def on_key_release(self, symbol: int, modifiers: int) -> bool:
        """Release and activate after a matching keyboard press."""
        del modifiers
        if symbol not in (key.ENTER, key.RETURN, key.SPACE):
            return False
        activate = self.pressed
        self.pressed = False
        self._update_interaction_state()
        if activate and self.on_click is not None:
            self.on_click()
        return True


class Toggle(Button):
    """Button that switches between selected and unselected states.

    Args:
        text (str): Button label.
        value (bool): Initial selected state.
        on_change (ToggleHandler | None): Optional callback receiving the new selected state.
        **kwargs (Any): Options forwarded to :class:`Button`.
    """

    def __init__(
        self,
        text: str,
        value: bool = False,
        on_change: ToggleHandler | None = None,
        **kwargs,
    ) -> None:
        """Initialize a toggle button and its selected state."""
        super().__init__(text, **kwargs)
        self.value = value
        self.on_change = on_change

    def _current_color(self) -> Color:
        if self.value and self.enabled_in_hierarchy and not self.pressed:
            return self.style.pressed
        return super()._current_color()

    def on_pointer_release(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Toggle the value after an activated pointer click."""
        activate = self.pressed and self.hit_test(x, y) is self
        handled = super().on_pointer_release(x, y, button, modifiers)
        if activate:
            self.value = not self.value
            self._update_interaction_state()
            self._sync_drawables()
            if self.on_change is not None:
                self.on_change(self.value)
        return handled

    def on_key_release(self, symbol: int, modifiers: int) -> bool:
        """Toggle the value after an activated keyboard click."""
        activate = self.pressed and symbol in (
            key.ENTER,
            key.RETURN,
            key.SPACE,
        )
        handled = super().on_key_release(symbol, modifiers)
        if activate:
            self.value = not self.value
            self._update_interaction_state()
            self._sync_drawables()
            if self.on_change is not None:
                self.on_change(self.value)
        return handled


class Slider(Widget):
    """Focusable numeric slider with pointer and arrow-key input.

    Args:
        minimum (float): Lowest selectable value.
        maximum (float): Highest selectable value; must exceed ``minimum``.
        value (float): Initial value, clamped to the range.
        on_change (ValueHandler | None): Optional callback invoked when the value changes.
        style (SliderStyle | None): Optional slider style; otherwise uses the view theme.
        width (float | None): Requested width.
        height (float | None): Requested height.
        **kwargs (Any): Options forwarded to :class:`Widget`.

    Raises:
        ValueError: If ``maximum`` is not greater than ``minimum``.
    """

    def __init__(
        self,
        minimum: float = 0,
        maximum: float = 1,
        value: float = 0,
        on_change: ValueHandler | None = None,
        *,
        style: SliderStyle | None = None,
        width: float | None = 200,
        height: float | None = 24,
        **kwargs,
    ) -> None:
        """Initialize the slider range, value, callback, and style."""
        if maximum <= minimum:
            raise ValueError("slider maximum must be greater than minimum")
        kwargs.setdefault("focusable", True)
        super().__init__(width=width, height=height, **kwargs)
        self.minimum = minimum
        self.maximum = maximum
        self.value = min(maximum, max(minimum, value))
        self.on_change = on_change
        self.style = style or SliderStyle()
        self._uses_theme = style is None
        self._track: pyglet.shapes.Rectangle | None = None
        self._knob: pyglet.shapes.Circle | None = None

    def _bind(
        self,
        view: UIView,
        parent_group: pyglet.graphics.Group | None = None,
    ) -> None:
        if self._uses_theme:
            self.style = view.theme.slider
        super()._bind(view, parent_group)

    def preferred_size(self) -> tuple[float, float]:
        """Return the default slider dimensions."""
        return 200, 24

    def _create_drawables(self, batch, group) -> None:
        self._track = pyglet.shapes.Rectangle(
            0,
            0,
            0,
            self.style.track_thickness,
            color=self.style.track[:3],
            batch=batch,
            group=group,
        )
        self._knob = pyglet.shapes.Circle(
            0,
            0,
            self.style.knob_radius,
            color=self.style.knob[:3],
            batch=batch,
            group=pyglet.graphics.Group(order=self.layer + 1, parent=group),
        )
        self._sync_drawables()

    def _sync_drawables(self) -> None:
        if self._track is None or self._knob is None:
            return
        ratio = (self.value - self.minimum) / (self.maximum - self.minimum)
        left, bottom, right, top = self.presented_bounds
        self._track.position = (
            left,
            (bottom + top - self.style.track_thickness) / 2,
        )
        self._track.width = right - left
        self._knob.position = left + (right - left) * ratio, (bottom + top) / 2
        self._track.opacity = self.effective_opacity
        self._knob.opacity = self.effective_opacity
        self._track.visible = self.visible_in_hierarchy
        self._knob.visible = self.visible_in_hierarchy

    def _set_from_x(self, x: float) -> None:
        local = self._inverse_transform_point(x, self.y)
        local_x = self.x if local is None else local[0]
        ratio = min(
            1.0,
            max(0.0, (local_x - self.x) / max(1, self.width)),
        )
        value = self.minimum + ratio * (self.maximum - self.minimum)
        if value != self.value:
            self.value = value
            self._sync_drawables()
            if self.on_change is not None:
                self.on_change(value)

    def on_pointer_press(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Begin dragging the knob and update from the pointer position."""
        del y, modifiers
        if button != mouse.LEFT:
            return False
        self._capturing_pointer = True
        self._set_from_x(x)
        return True

    def on_pointer_drag(
        self,
        x: float,
        y: float,
        delta_x: float,
        delta_y: float,
        buttons: int,
        modifiers: int,
    ) -> bool:
        """Update the slider value while the pointer is dragged."""
        del y, delta_x, delta_y, buttons, modifiers
        self._set_from_x(x)
        return True

    def on_pointer_release(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Update from the release position and stop pointer capture."""
        del y, button, modifiers
        self._set_from_x(x)
        self._capturing_pointer = False
        return True

    def on_key_press(self, symbol: int, modifiers: int) -> bool:
        """Adjust the value with arrow keys and report handled keys."""
        del modifiers
        step = (self.maximum - self.minimum) / 20
        if symbol in (key.LEFT, key.DOWN):
            value = max(self.minimum, self.value - step)
        elif symbol in (key.RIGHT, key.UP):
            value = min(self.maximum, self.value + step)
        else:
            return False
        if value != self.value:
            self.value = value
            self._sync_drawables()
            if self.on_change is not None:
                self.on_change(value)
        return True


class TextInput(Widget):
    """Focusable text field that commits edited text through a callback.

    Args:
        text (str): Initial contents.
        on_commit (CommitHandler | None): Optional callback called when editing is committed.
        color (Color | None): Optional background color.
        text_color (Color | None): Optional text color.
        caret_color (Color | None): Optional caret color.
        style (TextInputStyle | None): Optional style; otherwise uses the view theme.
        width (float | None): Requested width.
        height (float | None): Requested height.
        **kwargs (Any): Options forwarded to :class:`Widget`.
    """

    def __init__(
        self,
        text: str = "",
        on_commit: CommitHandler | None = None,
        *,
        color: Color | None = None,
        text_color: Color | None = None,
        caret_color: Color | None = None,
        style: TextInputStyle | None = None,
        width: float | None = 200,
        height: float | None = 28,
        **kwargs,
    ) -> None:
        """Initialize text contents, commit callback, and colors."""
        kwargs.setdefault("focusable", True)
        super().__init__(width=width, height=height, **kwargs)
        resolved = style or TextInputStyle()
        self.text = text
        self.on_commit = on_commit
        self.color = color if color is not None else resolved.background
        self.text_color = (
            text_color if text_color is not None else resolved.text_color
        )
        self.caret_color = (
            caret_color if caret_color is not None else resolved.caret_color
        )
        self._uses_theme = (
            style is None
            and color is None
            and text_color is None
            and caret_color is None
        )
        self._entry: PygletTextEntry | None = None

    def _bind(
        self,
        view: UIView,
        parent_group: pyglet.graphics.Group | None = None,
    ) -> None:
        if self._uses_theme:
            style = view.theme.text_input
            self.color = style.background
            self.text_color = style.text_color
            self.caret_color = style.caret_color
        super()._bind(view, parent_group)

    def preferred_size(self) -> tuple[float, float]:
        """Return the default text-input dimensions."""
        return 200, 28

    def _create_drawables(self, batch, group) -> None:
        self._entry = PygletTextEntry(
            self.text,
            0,
            0,
            max(1, round(self.width)),
            color=_rgba(self.color),
            text_color=_rgba(self.text_color),
            caret_color=_rgba(self.caret_color),
            batch=batch,
            group=group,
        )
        self._entry.set_handler("on_commit", self._on_commit)

    def _on_commit(self, entry: PygletTextEntry, text: str) -> None:
        del entry
        self.text = text
        if self.view is not None and self.view.manager is not None:
            self.view.manager.clear_focus()
        if self.on_commit is not None:
            self.on_commit(text)

    def _sync_drawables(self) -> None:
        if self._entry is not None:
            left, bottom, right, top = self.presented_bounds
            position = (
                (round(left), round(bottom))
                if self.visible_in_hierarchy
                else (-100_000, -100_000)
            )
            self._entry.position = position
            self._entry.width = max(1, round(right - left))
            self._entry.height = max(1, round(top - bottom))
            self._entry.enabled = (
                self.visible_in_hierarchy and self.enabled_in_hierarchy
            )

    def set_focus(self, focused: bool) -> None:
        """Update widget focus and forward it to the text-entry control."""
        super().set_focus(focused)
        if self._entry is not None:
            self._entry.focus = focused

    def on_pointer_press(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Forward pointer press to the text-entry control and capture it."""
        if self._entry is not None:
            self._entry.on_mouse_press(round(x), round(y), button, modifiers)
        self._capturing_pointer = True
        return True

    def on_pointer_release(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Release pointer capture."""
        del x, y, button, modifiers
        self._capturing_pointer = False
        return True

    def on_pointer_drag(
        self,
        x: float,
        y: float,
        delta_x: float,
        delta_y: float,
        buttons: int,
        modifiers: int,
    ) -> bool:
        """Forward pointer dragging to the text-entry control."""
        if self._entry is not None:
            self._entry.on_mouse_drag(
                round(x),
                round(y),
                round(delta_x),
                round(delta_y),
                buttons,
                modifiers,
            )
        return True

    def on_text(self, text: str) -> bool:
        """Insert text into the entry and synchronize the public value."""
        if self._entry is not None:
            self._entry.on_text(text)
            self.text = self._entry.value
        return True

    def on_key_press(self, symbol: int, modifiers: int) -> bool:
        """Consume key presses while the text input is focused."""
        del symbol, modifiers
        return True

    def on_key_release(self, symbol: int, modifiers: int) -> bool:
        """Consume key releases while the text input is focused."""
        del symbol, modifiers
        return True

    def on_text_motion(self, motion: int, *, select: bool = False) -> bool:
        """Move the caret, optionally extending the selection."""
        if self._entry is not None:
            if select:
                self._entry.on_text_motion_select(motion)
            else:
                self._entry.on_text_motion(motion)
        return True


class _ScissorGroup(pyglet.graphics.Group):
    def __init__(
        self,
        owner: Widget,
        *,
        parent: pyglet.graphics.Group | None = None,
    ) -> None:
        super().__init__(parent=parent)
        self.owner = owner

    def set_state(self) -> None:
        left, bottom, right, top = self.owner.presented_bounds
        pyglet.gl.glEnable(pyglet.gl.GL_SCISSOR_TEST)
        pyglet.gl.glScissor(
            round(left),
            round(bottom),
            max(0, round(right - left)),
            max(0, round(top - bottom)),
        )

    def unset_state(self) -> None:
        pyglet.gl.glDisable(pyglet.gl.GL_SCISSOR_TEST)


class ScrollView(Container):
    """Clip and scroll a single child within the view's bounds.

    Args:
        child (Widget | None): Optional initial content widget.
        scroll_speed (float): Scroll distance per wheel unit; must be positive.
        horizontal (bool): Whether horizontal scrolling is enabled.
        **kwargs (Any): Options forwarded to :class:`Container`.

    Raises:
        ValueError: If ``scroll_speed`` is not positive.
    """

    def __init__(
        self,
        child: Widget | None = None,
        *,
        scroll_speed: float = 32,
        horizontal: bool = False,
        **kwargs,
    ) -> None:
        """Initialize a clipped viewport and optional content child."""
        children = () if child is None else (child,)
        super().__init__(*children, **kwargs)
        if scroll_speed <= 0:
            raise ValueError("scroll speed must be positive")
        self.scroll_speed = scroll_speed
        self.horizontal = horizontal
        self.scroll_x = 0.0
        self.scroll_y = 0.0
        self._scissor_group: _ScissorGroup | None = None

    @property
    def child(self) -> Widget | None:
        """The sole content widget, if present."""
        return self.children[0] if self.children else None

    @property
    def max_scroll_x(self) -> float:
        """Maximum horizontal scroll offset."""
        child = self.child
        return 0 if child is None else max(0, child.width - self.width)

    @property
    def max_scroll_y(self) -> float:
        """Maximum vertical scroll offset."""
        child = self.child
        return 0 if child is None else max(0, child.height - self.height)

    def add(self, widget: WidgetType) -> WidgetType:
        """Add the sole content widget.

        Raises:
            ValueError: If a content widget is already present.
        """
        if self.children:
            raise ValueError("scroll view can contain only one child")
        return super().add(widget)

    def _bind(
        self,
        view: UIView,
        parent_group: pyglet.graphics.Group | None = None,
    ) -> None:
        Widget._bind(self, view, parent_group)
        base_group = view.group_for(self.layer, parent_group)
        self._scissor_group = _ScissorGroup(self, parent=base_group)
        self._content_group = self._scissor_group
        for child in self._children:
            child._bind(view, self._scissor_group)

    def preferred_size(self) -> tuple[float, float]:
        """Return the preferred size of the content widget."""
        if self.child is None:
            return 0, 0
        return self.child.measure(float("inf"), float("inf"))

    def arrange(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        """Arrange content and clamp offsets to the available viewport."""
        Widget.arrange(self, x, y, width, height)
        child = self.child
        if child is None:
            return
        child_width, child_height = child.measure(
            float("inf"),
            float("inf"),
        )
        self.scroll_x = min(self.scroll_x, max(0, child_width - width))
        self.scroll_y = min(self.scroll_y, max(0, child_height - height))
        child.arrange(
            x - self.scroll_x,
            y + height - child_height + self.scroll_y,
            max(width, child_width),
            max(height, child_height),
        )

    def hit_test(self, x: float, y: float) -> Widget | None:
        """Hit-test the viewport and its visible content."""
        own_target = Widget.hit_test(self, x, y)
        if own_target is None:
            return None
        child = self.child
        if child is not None:
            target = child.hit_test(x, y)
            if target is not None:
                return target
        return self

    def on_scroll(self, scroll_x: float, scroll_y: float) -> bool:
        """Scroll content and report whether its offset changed."""
        old = self.scroll_x, self.scroll_y
        if self.horizontal:
            self.scroll_x = min(
                self.max_scroll_x,
                max(0, self.scroll_x - (scroll_y + scroll_x) * self.scroll_speed),
            )
        else:
            self.scroll_y = min(
                self.max_scroll_y,
                max(0, self.scroll_y - scroll_y * self.scroll_speed),
            )
        if old == (self.scroll_x, self.scroll_y):
            return False
        if self.view is not None:
            self.view.invalidate_layout()
        return True


class UIView:
    """A named GUI screen containing a root widget tree.

    Args:
        layout (Container | None): Root container; a stack layout is created by default.
        transparent (bool): Whether views below remain visible.
        modal (bool): Whether the view is modal.
        pauses_below (bool): Whether updating stops for views below this one.
        pauses_scene (bool): Whether the view pauses the game scene.
        theme (UITheme | None): Optional theme override.
        identifier (str | None): Optional stable view identifier.
        name (str): Readable view name; must not be empty.

    Raises:
        ValueError: If ``name`` is empty.

    Attributes:
        id: Stable view identifier.
        name: Registered view name.
        root: Root container for widgets.
        transparent: Whether lower views remain visible.
        modal: Whether input is blocked from lower views.
        pauses_below: Whether lower views stop updating.
        pauses_scene: Whether this view pauses the game scene.
        manager: Owning GUI manager, once registered.
    """

    def __init__(
        self,
        layout: Container | None = None,
        *,
        transparent: bool = True,
        modal: bool = False,
        pauses_below: bool = True,
        pauses_scene: bool = False,
        theme: UITheme | None = None,
        identifier: str | None = None,
        name: str = "UI View",
    ) -> None:
        """Initialize a view and its root widget container."""
        if not name:
            raise ValueError("GUI view name must not be empty")
        self.id = identifier or str(uuid4())
        self.name = name
        self.root = layout or StackLayout()
        self.transparent = transparent
        self.modal = modal
        self.pauses_below = pauses_below
        self.pauses_scene = pauses_scene
        self._theme = theme
        self.manager: GUIManager | None = None
        self.batch = pyglet.graphics.Batch()
        self._groups: dict[
            tuple[int, int | None],
            pyglet.graphics.Group,
        ] = {}
        self._layout_invalid = True
        self.offset_x = 0.0
        self.offset_y = 0.0
        self._loaded = False

    @property
    def theme(self) -> UITheme:
        """Theme override or the manager's theme."""
        if self._theme is not None:
            return self._theme
        if self.manager is not None:
            return self.manager.theme
        return UITheme()

    def group_for(
        self,
        layer: int,
        parent: pyglet.graphics.Group | None = None,
    ) -> pyglet.graphics.Group:
        """Return the cached drawing group for a layer and parent group."""
        group_key = layer, id(parent) if parent is not None else None
        if group_key not in self._groups:
            self._groups[group_key] = pyglet.graphics.Group(
                order=layer,
                parent=parent,
            )
        return self._groups[group_key]

    def _bind(self, manager: GUIManager) -> None:
        if self.manager is not None and self.manager is not manager:
            raise ValueError("GUI view already belongs to another manager")
        self.manager = manager
        self.root._bind(self)
        self.root._set_mounted(True)

    def add(self, widget: WidgetType) -> WidgetType:
        """Add a widget to the root container."""
        return self.root.add(widget)

    def remove(self, widget: WidgetType) -> WidgetType:
        """Remove a widget from the root container."""
        return self.root.remove(widget)

    def invalidate_layout(self) -> None:
        """Mark this view's layout for recalculation."""
        self._layout_invalid = True

    def _layout(self) -> None:
        if self.manager is None:
            return
        scene = self.manager.scene
        scale = self.manager.scale
        self.root.arrange(
            self.offset_x / scale,
            self.offset_y / scale,
            scene.width / scale,
            scene.height / scale,
        )
        self._layout_invalid = False

    def _draw(self) -> None:
        if self._layout_invalid:
            self._layout()
        self.root._sync_tree()
        self.batch.draw()

    def hit_test(self, x: float, y: float) -> Widget | None:
        """Return the widget under a point in UI coordinates."""
        if self._layout_invalid:
            self._layout()
        return self.root.hit_test(x, y)

    def on_load(self) -> None:
        """Run once when the view is first entered; override to initialize it."""
        pass

    def on_enter(self, previous: UIView | None) -> None:
        """Run whenever the view becomes active.

        Args:
            previous (UIView | None): Previously active view, if any.
        """
        del previous

    def on_shown(self) -> None:
        """Run when the view becomes fully visible after any transition."""
        pass

    def on_pause(self) -> None:
        """Run when another view is pushed above this one."""
        pass

    def on_resume(self) -> None:
        """Run when the view becomes active after a view above it is popped."""
        pass

    def on_exit(self, next_view: UIView | None) -> None:
        """Run when leaving the active stack.

        Args:
            next_view (UIView | None): View replacing this one, if any.
        """
        del next_view

    def on_unload(self) -> None:
        """Run when the view is unregistered after loading."""
        pass

    def update(self, delta_time: float) -> None:
        """Advance widget animations for this view."""
        self.root._update_animation_tree(delta_time)

    def focusable_widgets(self) -> tuple[Widget, ...]:
        """Return visible, enabled focusable widgets in tree order."""
        widgets: list[Widget] = []

        def visit(widget: Widget) -> None:
            if (
                widget.focusable
                and widget.visible_in_hierarchy
                and widget.enabled_in_hierarchy
            ):
                widgets.append(widget)
            if isinstance(widget, Container):
                for child in widget.children:
                    visit(child)

        visit(self.root)
        return tuple(widgets)


class UITransition:
    """Base class for animated changes between GUI views.

    Args:
        duration (float): Positive transition duration in seconds.
        block_input (bool): Whether input is blocked during the transition.

    Raises:
        ValueError: If ``duration`` is not positive.

    Attributes:
        duration: Transition duration in seconds.
        block_input: Whether the manager consumes input during the transition.
        elapsed: Elapsed transition time in seconds.
        incoming: Incoming view once the swap has occurred.
    """

    def __init__(self, duration: float, *, block_input: bool = True) -> None:
        """Initialize a timed transition and its input-blocking policy."""
        if duration <= 0:
            raise ValueError("duration must be positive")
        self.duration = duration
        self.block_input = block_input
        self.elapsed = 0.0
        self._swapped = False
        self._swap: Callable[[], UIView] | None = None
        self.incoming: UIView | None = None

    @property
    def progress(self) -> float:
        """Elapsed transition fraction from zero to one."""
        return self.elapsed / self.duration

    def _start(self, swap: Callable[[], UIView]) -> None:
        self.elapsed = 0
        self._swapped = False
        self._swap = swap
        self.incoming = None

    def _update(self, delta_time: float) -> bool:
        self.elapsed = min(self.duration, self.elapsed + delta_time)
        if not self._swapped and self.progress >= 0.5:
            if self._swap is None:
                raise RuntimeError("GUI transition has not been started")
            self.incoming = self._swap()
            self._swapped = True
        return self.progress >= 1

    @property
    def overlay_color(self) -> Color | None:
        """Optional color overlay drawn over the scene."""
        return None

    @property
    def offset(self) -> tuple[float, float]:
        """Current view offset as an ``(x, y)`` pair."""
        return 0, 0


class UIFade(UITransition):
    """Cross-fade between views using a color overlay.

    Args:
        duration (float): Positive transition duration in seconds.
        color (Color): RGB or RGBA overlay color.
        block_input (bool): Whether input is blocked during the transition.
    """

    def __init__(
        self,
        duration: float = 0.3,
        color: Color = (0, 0, 0),
        *,
        block_input: bool = True,
    ) -> None:
        """Initialize a color-overlay transition."""
        super().__init__(duration, block_input=block_input)
        self.color = color

    @property
    def overlay_color(self) -> Color:
        """Overlay color and alpha for the current transition progress."""
        alpha = round(
            255
            * (
                self.progress * 2
                if self.progress <= 0.5
                else (1 - self.progress) * 2
            )
        )
        rgba = _rgba(self.color)
        return *rgba[:3], round(alpha * rgba[3] / 255)


class UISlide(UITransition):
    """Slide the outgoing and incoming views in a cardinal direction.

    Args:
        direction (str): One of ``"left"``, ``"right"``, ``"up"``, or ``"down"``.
        duration (float): Positive transition duration in seconds.
        block_input (bool): Whether input is blocked during the transition.

    Raises:
        ValueError: If ``direction`` is not supported.
    """

    def __init__(
        self,
        direction: str = "left",
        duration: float = 0.3,
        *,
        block_input: bool = True,
    ) -> None:
        """Initialize a cardinal-direction slide transition."""
        if direction not in ("left", "right", "up", "down"):
            raise ValueError("unknown slide direction")
        super().__init__(duration, block_input=block_input)
        self.direction = direction
        self.viewport_width = 0.0
        self.viewport_height = 0.0

    @property
    def offset(self) -> tuple[float, float]:
        """Current transition offset in viewport coordinates."""
        distance = (
            self.progress * 2
            if self.progress < 0.5
            else (1 - self.progress) * 2
        )
        sign = -1 if self.progress < 0.5 else 1
        if self.direction == "left":
            return sign * self.viewport_width * distance, 0
        if self.direction == "right":
            return -sign * self.viewport_width * distance, 0
        if self.direction == "up":
            return 0, -sign * self.viewport_height * distance
        return 0, sign * self.viewport_height * distance


ViewType = TypeVar("ViewType", bound=UIView)


class GUIManager:
    """Manage GUI views, navigation, input dispatch, and drawing.

    Args:
        scene (Scene): Scene whose dimensions and lifecycle are used by the GUI.
        theme (UITheme | None): Default theme for views and widgets.
        scale (float): Positive UI scale factor.

    Raises:
        ValueError: If ``scale`` is not positive.

    Attributes:
        scene: Scene whose dimensions and input are used.
        theme: Default widget theme.
    """

    def __init__(
        self,
        scene: Scene,
        *,
        theme: UITheme | None = None,
        scale: float = 1,
    ) -> None:
        """Initialize the manager and its default HUD view."""
        if scale <= 0:
            raise ValueError("GUI scale must be positive")
        self.scene = scene
        self.theme = theme or UITheme()
        self._scale = float(scale)
        self._views: dict[str, UIView] = {}
        self._stack: list[UIView] = []
        self._transition: UITransition | None = None
        self._hovered: Widget | None = None
        self._focused: Widget | None = None
        self._captured: Widget | None = None
        self._overlay_batch = pyglet.graphics.Batch()
        self._overlay = pyglet.shapes.Rectangle(
            0,
            0,
            scene.width,
            scene.height,
            color=(0, 0, 0),
            batch=self._overlay_batch,
        )
        self._overlay.visible = False
        default = self.register(
            "HUD",
            UIView(CanvasLayout(), identifier="hud", name="HUD"),
        )
        self._default = default
        self._stack.append(default)
        self._enter(default, None, shown=True)

    @property
    def scale(self) -> float:
        """UI scaling factor."""
        return self._scale

    @scale.setter
    def scale(self, value: float) -> None:
        """Set the positive UI scale and invalidate view layouts."""
        if value <= 0:
            raise ValueError("GUI scale must be positive")
        self._scale = float(value)
        self.invalidate_layout()

    @property
    def default(self) -> UIView:
        """Default HUD view created with the manager."""
        return self._default

    @property
    def views(self) -> tuple[UIView, ...]:
        """Registered views as an immutable snapshot."""
        return tuple(self._views.values())

    @property
    def view_items(self) -> tuple[tuple[str, UIView], ...]:
        """Registered view names and instances in registration order."""
        return tuple(self._views.items())

    @property
    def current(self) -> UIView:
        """Topmost active view."""
        return self._stack[-1]

    @property
    def transition(self) -> UITransition | None:
        """Current view transition, if one is active."""
        return self._transition

    @property
    def pauses_scene(self) -> bool:
        """Whether the active view stack currently pauses the scene."""
        for view in reversed(self._stack):
            if view.pauses_scene:
                return True
            if view.pauses_below:
                break
        return False

    def add(self, widget: WidgetType) -> WidgetType:
        """Add a widget to the default view, centering it on canvas roots."""
        root = self.default.root
        if isinstance(root, CanvasLayout):
            width, height = widget.measure(
                self.scene.width,
                self.scene.height,
            )
            return root.add_at(
                widget,
                (self.scene.width - width) / 2,
                (self.scene.height - height) / 2,
            )
        return self.default.add(widget)

    def remove(self, widget: WidgetType) -> WidgetType:
        """Remove a widget from the default view."""
        return self.default.remove(widget)

    def register(self, name: str, view: ViewType) -> ViewType:
        """Register and bind a named view.

        Args:
            name (str): Non-empty unique view name.
            view (ViewType): View instance to register.

        Returns:
            ViewType: The registered view.

        Raises:
            ValueError: If the name or view identity is already registered.
        """
        if not name:
            raise ValueError("GUI view name must not be empty")
        if name in self._views:
            raise ValueError(f"GUI view already exists: {name!r}")
        if view in self._views.values():
            raise ValueError("GUI view is already registered")
        if any(candidate.id == view.id for candidate in self._views.values()):
            raise ValueError(f"GUI view ID already exists: {view.id!r}")
        view.name = name
        view._bind(self)
        self._views[name] = view
        return view

    def unregister(self, name: str) -> UIView:
        """Unregister an inactive view by name and return it.

        Raises:
            ValueError: If the view is currently active.
        """
        view = self.get(name)
        if view in self._stack:
            raise ValueError("cannot unregister an active GUI view")
        del self._views[name]
        if view._loaded:
            view.on_unload()
            view._loaded = False
        return view

    def rename(self, view: UIView, name: str) -> None:
        """Rename a registered view.

        Raises:
            ValueError: If the view is unregistered or the name is unavailable.
        """
        if not name:
            raise ValueError("GUI view name must not be empty")
        if view not in self._views.values():
            raise ValueError("GUI view is not registered")
        if name in self._views and self._views[name] is not view:
            raise ValueError(f"GUI view already exists: {name!r}")
        current_name = next(
            key for key, candidate in self._views.items()
            if candidate is view
        )
        if current_name == name:
            return
        self._views = {
            (name if key == current_name else key): candidate
            for key, candidate in self._views.items()
        }
        view.name = name

    def get(self, name: str) -> UIView:
        """Return a registered view by name.

        Raises:
            ValueError: If no view has the requested name.
        """
        try:
            return self._views[name]
        except KeyError:
            raise ValueError(f"unknown GUI view: {name!r}") from None

    def replace(
        self,
        name: str,
        *,
        transition: UITransition | None = None,
    ) -> UIView:
        """Replace the active view.

        Args:
            name (str): Registered view to activate.
            transition (UITransition | None): Optional transition animation.

        Returns:
            UIView: The target view.

        Raises:
            ValueError: If the target is already active.
            RuntimeError: If another transition is in progress.
        """
        target = self.get(name)
        if target in self._stack:
            raise ValueError("GUI view is already active")
        return self._change(
            target,
            transition,
            lambda: self._replace_now(target, shown=False),
            lambda: self._replace_now(target, shown=True),
        )

    def push(
        self,
        name: str,
        *,
        transition: UITransition | None = None,
    ) -> UIView:
        """Push a view onto the active stack.

        Args:
            name (str): Registered view to push.
            transition (UITransition | None): Optional transition animation.

        Returns:
            UIView: The target view.

        Raises:
            ValueError: If the target is already active.
            RuntimeError: If another transition is in progress.
        """
        target = self.get(name)
        if target in self._stack:
            raise ValueError("GUI view is already active")
        return self._change(
            target,
            transition,
            lambda: self._push_now(target, shown=False),
            lambda: self._push_now(target, shown=True),
        )

    def pop(self, *, transition: UITransition | None = None) -> UIView:
        """Pop and return the current view.

        Args:
            transition (UITransition | None): Optional transition animation.

        Returns:
            UIView: The view removed from the active stack.

        Raises:
            RuntimeError: If only the default view remains.
        """
        if len(self._stack) == 1:
            raise RuntimeError("cannot pop the last GUI view")
        outgoing = self.current
        self._change(
            self._stack[-2],
            transition,
            lambda: self._pop_now(shown=False),
            lambda: self._pop_now(shown=True),
        )
        return outgoing

    def _change(
        self,
        target: UIView,
        transition: UITransition | None,
        animated: Callable[[], UIView],
        immediate: Callable[[], UIView],
    ) -> UIView:
        self.clear_focus()
        if transition is None:
            immediate()
            return target
        if self._transition is not None:
            raise RuntimeError("a GUI transition is already active")
        if isinstance(transition, UISlide):
            transition.viewport_width = self.scene.width
            transition.viewport_height = self.scene.height
        self._transition = transition
        transition._start(animated)
        return target

    def _replace_now(self, target: UIView, *, shown: bool) -> UIView:
        outgoing = self._stack.pop()
        outgoing.on_exit(target)
        self._stack.append(target)
        self._enter(target, outgoing, shown)
        return target

    def _push_now(self, target: UIView, *, shown: bool) -> UIView:
        previous = self.current
        previous.on_pause()
        self._stack.append(target)
        self._enter(target, previous, shown)
        return target

    def _pop_now(self, *, shown: bool) -> UIView:
        outgoing = self._stack.pop()
        incoming = self.current
        outgoing.on_exit(incoming)
        incoming.on_resume()
        if shown:
            incoming.on_shown()
        return incoming

    def _enter(
        self, view: UIView, previous: UIView | None, shown: bool
    ) -> None:
        if not view._loaded:
            view.on_load()
            view._loaded = True
        view.on_enter(previous)
        if shown:
            view.on_shown()

    @property
    def visible_views(self) -> tuple[UIView, ...]:
        """Active stack views visible beneath the current opaque view."""
        visible: list[UIView] = []
        for view in reversed(self._stack):
            visible.append(view)
            if not view.transparent:
                break
        return tuple(reversed(visible))

    @property
    def updating_views(self) -> tuple[UIView, ...]:
        """Active stack views that are not paused by a view above them."""
        updating: list[UIView] = []
        for view in reversed(self._stack):
            updating.append(view)
            if view.pauses_below:
                break
        return tuple(reversed(updating))

    def _update(self, delta_time: float) -> None:
        if self._transition is not None:
            transition = self._transition
            if transition._update(delta_time):
                self._transition = None
                self.current.offset_x = 0
                self.current.offset_y = 0
                self.current.invalidate_layout()
                if transition.incoming is not None:
                    transition.incoming.on_shown()
            else:
                offset_x, offset_y = transition.offset
                self.current.offset_x = offset_x
                self.current.offset_y = offset_y
                self.current.invalidate_layout()
        for view in self.updating_views:
            view.update(delta_time)

    def _draw(self, stats: _RenderStatsBuilder | None = None) -> None:
        for view in self.visible_views:
            view._draw()
            if stats is not None:
                stats.record_batch(view.batch)
        color = (
            self._transition.overlay_color
            if self._transition is not None
            else None
        )
        if color is None:
            self._overlay.visible = False
            return
        rgba = _rgba(color)
        self._overlay.color = rgba[:3]
        self._overlay.opacity = rgba[3]
        self._overlay.visible = True
        self._overlay_batch.draw()
        if stats is not None:
            stats.record_batch(self._overlay_batch)

    def invalidate_layout(self) -> None:
        """Invalidate layouts in all registered views."""
        for view in self._views.values():
            view.invalidate_layout()

    def clear_focus(self) -> None:
        """Clear keyboard focus and pointer capture."""
        if self._focused is not None:
            self._focused.set_focus(False)
        self._focused = None
        self._captured = None

    def _views_for_input(self) -> tuple[UIView, ...]:
        views: list[UIView] = []
        for view in reversed(self._stack):
            views.append(view)
            if view.modal:
                break
        return tuple(views)

    def _target_at(self, x: float, y: float) -> Widget | None:
        for view in self._views_for_input():
            target = view.hit_test(x - view.offset_x, y - view.offset_y)
            if target is not None and target is not view.root:
                return target
            if view.modal:
                return None
        return None

    def on_mouse_motion(
        self, x: float, y: float, dx: float, dy: float
    ) -> bool:
        """Dispatch pointer movement and update hover state.

        Returns:
            bool: Whether a widget is under the pointer or input is blocked.
        """
        del dx, dy
        if self._transition is not None and self._transition.block_input:
            return True
        target = self._target_at(x, y)
        if target is not self._hovered:
            if self._hovered is not None:
                self._hovered.on_pointer_leave()
            self._hovered = target
            if target is not None:
                target.on_pointer_enter()
        return bool(target)

    def on_mouse_press(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Dispatch a pointer press, focus eligible widgets, and capture input."""
        if self._transition is not None and self._transition.block_input:
            return True
        target = self._target_at(x, y)
        if target is None:
            self.clear_focus()
            return any(view.modal for view in self._views_for_input())
        if self._focused is not target:
            self.clear_focus()
            if target.focusable:
                self._focused = target
                target.set_focus(True)
                self.scene.game.input._reset()
        handled = target.on_pointer_press(x, y, button, modifiers)
        if target._capturing_pointer:
            self._captured = target
        return handled

    def on_mouse_release(
        self, x: float, y: float, button: int, modifiers: int
    ) -> bool:
        """Dispatch a pointer release to the captured or hit-tested widget."""
        if self._transition is not None and self._transition.block_input:
            return True
        target = self._captured or self._target_at(x, y)
        if target is None:
            return False
        handled = target.on_pointer_release(x, y, button, modifiers)
        if not target._capturing_pointer:
            self._captured = None
        return handled

    def on_mouse_drag(
        self,
        x: float,
        y: float,
        dx: float,
        dy: float,
        buttons: int,
        modifiers: int,
    ) -> bool:
        """Dispatch pointer dragging to the captured or hit-tested widget."""
        if self._transition is not None and self._transition.block_input:
            return True
        target = self._captured or self._target_at(x, y)
        return bool(
            target
            and target.on_pointer_drag(
                x, y, dx, dy, buttons, modifiers
            )
        )

    def on_mouse_scroll(
        self, x: float, y: float, scroll_x: float, scroll_y: float
    ) -> bool:
        """Dispatch scrolling from the hit widget through its ancestors."""
        if self._transition is not None and self._transition.block_input:
            return True
        target = self._target_at(x, y)
        while target is not None:
            if target.on_scroll(scroll_x, scroll_y):
                return True
            target = target.parent
        return False

    def on_key_press(self, symbol: int, modifiers: int) -> bool:
        """Dispatch a key press, including Tab focus and Escape navigation."""
        if self._transition is not None and self._transition.block_input:
            return True
        if symbol == key.TAB:
            self._move_focus(
                backwards=bool(modifiers & key.MOD_SHIFT)
            )
            return True
        if symbol == key.ESCAPE and len(self._stack) > 1:
            self.pop()
            return True
        return bool(
            self._focused
            and self._focused.on_key_press(symbol, modifiers)
        )

    def on_key_release(self, symbol: int, modifiers: int) -> bool:
        """Dispatch a key release to the focused widget."""
        if self._transition is not None and self._transition.block_input:
            return True
        return bool(
            self._focused
            and self._focused.on_key_release(symbol, modifiers)
        )

    def on_text(self, text: str) -> bool:
        """Dispatch text input to the focused widget."""
        if self._transition is not None and self._transition.block_input:
            return True
        return bool(self._focused and self._focused.on_text(text))

    def on_text_motion(self, motion: int, *, select: bool = False) -> bool:
        """Dispatch text navigation to the focused widget."""
        if self._transition is not None and self._transition.block_input:
            return True
        return bool(
            self._focused
            and self._focused.on_text_motion(motion, select=select)
        )

    def _move_focus(self, *, backwards: bool) -> None:
        widgets = self.current.focusable_widgets()
        if not widgets:
            self.clear_focus()
            return
        try:
            index = widgets.index(self._focused) if self._focused else -1
        except ValueError:
            index = -1
        direction = -1 if backwards else 1
        next_widget = widgets[(index + direction) % len(widgets)]
        self.clear_focus()
        self._focused = next_widget
        next_widget.set_focus(True)
        self.scene.game.input._reset()
