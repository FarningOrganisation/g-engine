from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep

import pyglet
from pyglet.window import Window

from .preferences import ResolvedTheme


_BRAND_DIRECTORY = Path(__file__).resolve().parent / "assets" / "brand"
_ENGINE_LOGO_Y = 245
_ATTRIBUTION_Y = 133
_COMPANY_LOGO_Y = 75


def center_window(window: Window) -> None:
    """Center a pyglet window on its current screen."""
    native_center = getattr(window, "_center_window", None)
    if callable(native_center):
        native_center()
        return
    window.set_location(
        window.screen.x + (window.screen.width - window.width) // 2,
        window.screen.y + (window.screen.height - window.height) // 2,
    )


@dataclass(frozen=True)
class SplashBranding:
    """Theme-specific splash colors and logo paths."""

    background: tuple[int, int, int]
    foreground: tuple[int, int, int, int]
    engine_logo: Path
    company_logo: Path


def splash_branding(theme: ResolvedTheme) -> SplashBranding:
    """Return splash assets appropriate for a resolved editor theme."""
    suffix = "-dark" if theme == "dark" else ""
    return SplashBranding(
        background=(34, 45, 88) if theme == "dark" else (255, 255, 255),
        foreground=(255, 255, 255, 255)
        if theme == "dark"
        else (34, 45, 88, 255),
        engine_logo=_BRAND_DIRECTORY / f"g-engine{suffix}.png",
        company_logo=_BRAND_DIRECTORY / f"farning-logo{suffix}.png",
    )


class EditorSplash:
    """Small branded window displayed while the editor initializes."""

    def __init__(
        self,
        theme: ResolvedTheme,
        *,
        minimum_duration: float = 2.0,
        visible: bool = True,
    ) -> None:
        """Create and immediately display the splash window."""
        if minimum_duration < 0:
            raise ValueError("splash minimum duration must not be negative")
        pyglet.options.dpi_scaling = "stretch"
        self._started_at = monotonic()
        self._minimum_duration = minimum_duration
        branding = splash_branding(theme)
        for logo in (branding.engine_logo, branding.company_logo):
            if not logo.is_file():
                raise RuntimeError(f"editor splash logo does not exist: {logo}")
        self._window = Window(
            width=640,
            height=360,
            caption="g-engine",
            resizable=False,
            style=Window.WINDOW_STYLE_OVERLAY,
            vsync=True,
            visible=visible,
        )
        center_window(self._window)
        self._batch = pyglet.graphics.Batch()
        self._drawables: list[object] = []
        self._drawables.append(
            pyglet.shapes.Rectangle(
                0,
                0,
                self._window.width,
                self._window.height,
                color=branding.background,
                batch=self._batch,
            )
        )
        self._add_logo(
            branding.engine_logo,
            y=_ENGINE_LOGO_Y,
            max_width=430,
        )
        self._add_logo(
            branding.company_logo,
            y=_COMPANY_LOGO_Y,
            max_width=220,
        )
        self._drawables.append(
            pyglet.text.Label(
                "Developed by",
                x=self._window.width // 2,
                y=_ATTRIBUTION_Y,
                anchor_x="center",
                anchor_y="center",
                color=branding.foreground,
                font_size=10,
                batch=self._batch,
            )
        )
        self._drawables.append(
            pyglet.shapes.Rectangle(
                0,
                0,
                self._window.width,
                6,
                color=(0, 90, 132),
                batch=self._batch,
            )
        )
        self._draw()

    def _add_logo(
        self,
        filename: Path,
        *,
        y: float,
        max_width: float,
    ) -> None:
        image = pyglet.image.load(str(filename))
        image.anchor_x = image.width // 2
        image.anchor_y = image.height // 2
        sprite = pyglet.sprite.Sprite(
            image,
            x=self._window.width // 2,
            y=y,
            batch=self._batch,
        )
        sprite.scale = min(max_width / image.width, 1)
        self._drawables.append(sprite)

    def _draw(self) -> None:
        self._window.switch_to()
        self._window.dispatch_events()
        self._window.activate()
        self._window.clear()
        self._batch.draw()
        self._window.flip()

    def finish(self) -> None:
        """Keep pumping the splash until its minimum duration has elapsed."""
        deadline = self._started_at + self._minimum_duration
        while monotonic() < deadline:
            self._draw()
            sleep(min(0.01, max(0, deadline - monotonic())))

    def close(self) -> None:
        """Close the splash window."""
        self._window.close()

    def __enter__(self) -> EditorSplash:
        return self

    def __exit__(self, *error: object) -> None:
        del error
        self.close()
