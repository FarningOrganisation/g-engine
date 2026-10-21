from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import TypeAlias

from pyglet.window import key as pyglet_key
from pyglet.window import mouse as pyglet_mouse


class Key(IntEnum):
    """Pyglet-compatible keyboard symbols with readable engine names."""

    A = pyglet_key.A
    B = pyglet_key.B
    C = pyglet_key.C
    D = pyglet_key.D
    E = pyglet_key.E
    F = pyglet_key.F
    G = pyglet_key.G
    H = pyglet_key.H
    I = pyglet_key.I
    J = pyglet_key.J
    K = pyglet_key.K
    L = pyglet_key.L
    M = pyglet_key.M
    N = pyglet_key.N
    O = pyglet_key.O
    P = pyglet_key.P
    Q = pyglet_key.Q
    R = pyglet_key.R
    S = pyglet_key.S
    T = pyglet_key.T
    U = pyglet_key.U
    V = pyglet_key.V
    W = pyglet_key.W
    X = pyglet_key.X
    Y = pyglet_key.Y
    Z = pyglet_key.Z
    DIGIT_0 = pyglet_key._0
    DIGIT_1 = pyglet_key._1
    DIGIT_2 = pyglet_key._2
    DIGIT_3 = pyglet_key._3
    DIGIT_4 = pyglet_key._4
    DIGIT_5 = pyglet_key._5
    DIGIT_6 = pyglet_key._6
    DIGIT_7 = pyglet_key._7
    DIGIT_8 = pyglet_key._8
    DIGIT_9 = pyglet_key._9
    SPACE = pyglet_key.SPACE
    ENTER = pyglet_key.ENTER
    ESCAPE = pyglet_key.ESCAPE
    TAB = pyglet_key.TAB
    BACKSPACE = pyglet_key.BACKSPACE
    DELETE = pyglet_key.DELETE
    LEFT = pyglet_key.LEFT
    RIGHT = pyglet_key.RIGHT
    UP = pyglet_key.UP
    DOWN = pyglet_key.DOWN
    HOME = pyglet_key.HOME
    END = pyglet_key.END
    PAGE_UP = pyglet_key.PAGEUP
    PAGE_DOWN = pyglet_key.PAGEDOWN
    LEFT_SHIFT = pyglet_key.LSHIFT
    RIGHT_SHIFT = pyglet_key.RSHIFT
    LEFT_CTRL = pyglet_key.LCTRL
    RIGHT_CTRL = pyglet_key.RCTRL
    LEFT_ALT = pyglet_key.LALT
    RIGHT_ALT = pyglet_key.RALT
    LEFT_COMMAND = pyglet_key.LCOMMAND
    RIGHT_COMMAND = pyglet_key.RCOMMAND
    F1 = pyglet_key.F1
    F2 = pyglet_key.F2
    F3 = pyglet_key.F3
    F4 = pyglet_key.F4
    F5 = pyglet_key.F5
    F6 = pyglet_key.F6
    F7 = pyglet_key.F7
    F8 = pyglet_key.F8
    F9 = pyglet_key.F9
    F10 = pyglet_key.F10
    F11 = pyglet_key.F11
    F12 = pyglet_key.F12

    @classmethod
    def _missing_(cls, value: object) -> Key | None:
        if not isinstance(value, int):
            return None
        key = int.__new__(cls, value)
        key._name_ = pyglet_key.symbol_string(value)
        key._value_ = value
        return key


class Modifiers(IntFlag):
    """Bit flags describing keyboard modifiers active during an event."""

    NONE = 0
    SHIFT = pyglet_key.MOD_SHIFT
    CTRL = pyglet_key.MOD_CTRL
    ALT = pyglet_key.MOD_ALT
    CAPS_LOCK = pyglet_key.MOD_CAPSLOCK
    NUM_LOCK = pyglet_key.MOD_NUMLOCK
    WINDOWS = pyglet_key.MOD_WINDOWS
    COMMAND = pyglet_key.MOD_COMMAND
    OPTION = pyglet_key.MOD_OPTION


class MouseButton(IntFlag):
    """Bit flags identifying mouse buttons."""

    LEFT = pyglet_mouse.LEFT
    MIDDLE = pyglet_mouse.MIDDLE
    RIGHT = pyglet_mouse.RIGHT


InputButton: TypeAlias = Key | MouseButton
_ButtonToken: TypeAlias = tuple[type[Key] | type[MouseButton], InputButton]


@dataclass(frozen=True, slots=True)
class AxisBinding:
    """Keys whose held state determines the negative and positive axis sides."""

    negative: tuple[Key, ...]
    positive: tuple[Key, ...]


class Input:
    """Frame-based keyboard, mouse, and named-axis input state.

    Press/release edges, mouse deltas, and scroll deltas are accumulated from
    window events and exposed for the next simulation frame.
    """

    def __init__(self) -> None:
        """Create empty input state with default ``x`` and ``y`` axis bindings."""
        self._keys: dict[Key, Modifiers] = {}
        self._mouse_buttons: set[MouseButton] = set()
        self._pressed: set[_ButtonToken] = set()
        self._released: set[_ButtonToken] = set()
        self._pending_pressed: set[_ButtonToken] = set()
        self._pending_released: set[_ButtonToken] = set()
        self._mouse_position = (0.0, 0.0)
        self._mouse_delta = (0.0, 0.0)
        self._pending_mouse_delta = (0.0, 0.0)
        self._scroll_delta = (0.0, 0.0)
        self._pending_scroll_delta = (0.0, 0.0)
        self._axes: dict[str, AxisBinding] = {}
        self.bind_axis("x", (Key.A, Key.LEFT), (Key.D, Key.RIGHT))
        self.bind_axis("y", (Key.S, Key.DOWN), (Key.W, Key.UP))

    @property
    def mouse_position(self) -> tuple[float, float]:
        """Return the latest pointer position in virtual pixels."""
        return self._mouse_position

    @property
    def mouse_delta(self) -> tuple[float, float]:
        """Return the accumulated pointer delta for the current frame."""
        return self._mouse_delta

    @property
    def scroll_delta(self) -> tuple[float, float]:
        """Return the accumulated scroll delta for the current frame."""
        return self._scroll_delta

    def bind_axis(
        self,
        name: str,
        negative: Key | Iterable[Key],
        positive: Key | Iterable[Key],
    ) -> None:
        """Bind keys to the negative and positive sides of a named axis.

        Names are stripped and lowercased. Multiple keys on one side act as
        alternatives; if both sides are held they cancel to zero.

        Args:
            name (str): Axis name.
            negative (Key | Iterable[Key]): One key or an iterable of keys for the negative side.
            positive (Key | Iterable[Key]): One key or an iterable of keys for the positive side.

        Raises:
            ValueError: If the name is empty or either key collection is empty.
            TypeError: If a collection contains a non-``Key`` value.
        """
        axis_name = name.strip().lower()
        if not axis_name:
            raise ValueError("axis name must not be empty")
        self._axes[axis_name] = AxisBinding(
            self._key_tuple(negative),
            self._key_tuple(positive),
        )

    def get_axis(self, name: str) -> float:
        """Return a named axis as -1, 0, or 1 from currently held keys.

        Raises:
            KeyError: If ``name`` is not bound.
        """
        axis_name = name.strip().lower()
        try:
            binding = self._axes[axis_name]
        except KeyError:
            raise KeyError(f"unknown input axis: {name!r}") from None
        negative = any(key in self._keys for key in binding.negative)
        positive = any(key in self._keys for key in binding.positive)
        return float(positive) - float(negative)

    def pressed(self, button: InputButton) -> bool:
        """Return whether a key or mouse button is currently held."""
        if isinstance(button, Key):
            return button in self._keys
        return button in self._mouse_buttons

    def just_pressed(self, button: InputButton) -> bool:
        """Return whether a key or mouse button was pressed this frame."""
        return (type(button), button) in self._pressed

    def just_released(self, button: InputButton) -> bool:
        """Return whether a key or mouse button was released this frame."""
        return (type(button), button) in self._released

    def key_pressed(self, key: Key) -> bool:
        """Return whether a keyboard key is currently held."""
        return key in self._keys

    def key_released(self, key: Key) -> bool:
        """Return whether a keyboard key is currently not held."""
        return not self.key_pressed(key)

    def key_just_pressed(self, key: Key) -> bool:
        """Return whether a keyboard key was pressed this simulation frame."""
        return (Key, key) in self._pressed

    def key_just_released(self, key: Key) -> bool:
        """Return whether a keyboard key was released this simulation frame."""
        return (Key, key) in self._released

    def mouse_pressed(self, button: MouseButton = MouseButton.LEFT) -> bool:
        """Return whether a mouse button is held; defaults to the left button."""
        return button in self._mouse_buttons

    def mouse_released(self, button: MouseButton = MouseButton.LEFT) -> bool:
        """Return whether a mouse button is not held; defaults to the left button."""
        return not self.mouse_pressed(button)

    def mouse_just_pressed(self, button: MouseButton = MouseButton.LEFT) -> bool:
        """Return whether a mouse button was pressed this frame; defaults to left."""
        return (MouseButton, button) in self._pressed

    def mouse_just_released(self, button: MouseButton = MouseButton.LEFT) -> bool:
        """Return whether a mouse button was released this frame; defaults to left."""
        return (MouseButton, button) in self._released

    def modifiers_for(self, key: Key) -> Modifiers:
        """Return the active modifiers for a held key, or ``Modifiers.NONE``."""
        return self._keys.get(key, Modifiers.NONE)

    @staticmethod
    def _key_tuple(keys: Key | Iterable[Key]) -> tuple[Key, ...]:
        if isinstance(keys, Key):
            return (keys,)
        values = tuple(keys)
        if not values:
            raise ValueError("an axis direction needs at least one key")
        if any(not isinstance(key, Key) for key in values):
            raise TypeError("axis bindings must contain Key values")
        return values

    def _begin_frame(self) -> None:
        self._pressed = self._pending_pressed
        self._released = self._pending_released
        self._pending_pressed = set()
        self._pending_released = set()
        self._mouse_delta = self._pending_mouse_delta
        self._pending_mouse_delta = (0.0, 0.0)
        self._scroll_delta = self._pending_scroll_delta
        self._pending_scroll_delta = (0.0, 0.0)

    def _press_key(self, key: Key, modifiers: Modifiers) -> None:
        if key not in self._keys:
            self._pending_pressed.add((Key, key))
        self._keys[key] = modifiers

    def _release_key(self, key: Key) -> None:
        if self._keys.pop(key, None) is not None:
            self._pending_released.add((Key, key))

    def _press_mouse(self, button: MouseButton) -> None:
        if button not in self._mouse_buttons:
            self._pending_pressed.add((MouseButton, button))
        self._mouse_buttons.add(button)

    def _release_mouse(self, button: MouseButton) -> None:
        if button in self._mouse_buttons:
            self._mouse_buttons.remove(button)
            self._pending_released.add((MouseButton, button))

    def _move_mouse(
        self,
        x: float,
        y: float,
        delta_x: float,
        delta_y: float,
    ) -> None:
        self._mouse_position = x, y
        pending_x, pending_y = self._pending_mouse_delta
        self._pending_mouse_delta = pending_x + delta_x, pending_y + delta_y

    def _scroll_mouse(self, scroll_x: float, scroll_y: float) -> None:
        pending_x, pending_y = self._pending_scroll_delta
        self._pending_scroll_delta = (
            pending_x + scroll_x,
            pending_y + scroll_y,
        )

    def _reset(self) -> None:
        self._keys.clear()
        self._mouse_buttons.clear()
        self._pressed.clear()
        self._released.clear()
        self._pending_pressed.clear()
        self._pending_released.clear()
        self._mouse_delta = (0.0, 0.0)
        self._pending_mouse_delta = (0.0, 0.0)
        self._scroll_delta = (0.0, 0.0)
        self._pending_scroll_delta = (0.0, 0.0)
