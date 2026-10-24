from __future__ import annotations

from contextvars import ContextVar, Token
import os
from typing import Any


_presentation_enabled = os.environ.get("G_HEADLESS_SERVER") != "1"
_direct_draw_game: ContextVar[Any | None] = ContextVar(
    "g_direct_draw_game",
    default=None,
)


def presentation_enabled() -> bool:
    return _presentation_enabled


def set_presentation_enabled(enabled: bool) -> None:
    global _presentation_enabled
    _presentation_enabled = bool(enabled)


def begin_direct_draw(game: Any) -> Token[Any | None]:
    return _direct_draw_game.set(game)


def end_direct_draw(token: Token[Any | None]) -> None:
    _direct_draw_game.reset(token)


def current_direct_draw_game() -> Any | None:
    return _direct_draw_game.get()


class NullDrawable:
    def __init__(self) -> None:
        self.batch: object | None = None
        self.group: object | None = None
        self.program: object | None = None
        self.visible = False
        self.paused = True
        self.opacity = 255
        self.color: tuple[int, int, int] = (255, 255, 255)
        self.x = self.y = self.z = 0.0
        self.rotation = 0.0
        self.scale_x = self.scale_y = 1.0

    @property
    def position(self) -> tuple[float, float, float]:
        return self.x, self.y, self.z

    @position.setter
    def position(self, value: tuple[float, float, float]) -> None:
        self.x, self.y, self.z = value

    def delete(self) -> None:
        pass


class NullBatch:
    def draw(self) -> None:
        pass


class NullGroup:
    def __init__(self, order: int = 0, parent: object | None = None) -> None:
        self.order = order
        self.parent = parent
