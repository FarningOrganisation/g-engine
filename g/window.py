from __future__ import annotations

from collections.abc import Callable
import sys
from typing import TYPE_CHECKING, Any, TypeAlias

import pyglet
from pyglet import gl
from pyglet.image.buffer import Framebuffer
from pyglet.math import Mat4

from .game import Game
from .input import Input
from .presentation import PresentationRect, presentation_rect
from .rendering import RenderInterpolation, SceneRenderer
from .resources import clamp_texture_edges
from .timing import FixedStepAccumulator

if TYPE_CHECKING:
    from .macos_fullscreen import MacOSFullscreen


UpdateCallback: TypeAlias = Callable[[float], None]
WindowMoveHandler: TypeAlias = Callable[[int, int, int, int], None]


class _RenderWindow:
    def __init__(self, window: Any, width: int, height: int):
        self._window = window
        self._width = width
        self._height = height

    @property
    def view(self):
        return self._window.view

    @view.setter
    def view(self, matrix: Any) -> None:
        self._window.view = matrix

    def get_framebuffer_size(self) -> tuple[int, int]:
        return self._width, self._height


class _PresentationSurface:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.texture = pyglet.image.Texture.create(
            width,
            height,
            min_filter=gl.GL_LINEAR,
            mag_filter=gl.GL_LINEAR,
        )
        clamp_texture_edges(self.texture)
        self.framebuffer = Framebuffer()
        self.framebuffer.attach_texture(self.texture)
        if not self.framebuffer.is_complete:
            status = self.framebuffer.get_status()
            self.delete()
            raise RuntimeError(
                f"game presentation framebuffer is incomplete: {status}"
            )

    def render(
        self,
        game: Game,
        renderer: SceneRenderer,
        window: Any,
    ) -> None:
        previous_framebuffer = (gl.GLint * 1)()
        previous_viewport = (gl.GLint * 4)()
        previous_projection = window.projection
        gl.glGetIntegerv(
            gl.GL_FRAMEBUFFER_BINDING,
            previous_framebuffer,
        )
        gl.glGetIntegerv(gl.GL_VIEWPORT, previous_viewport)
        try:
            self.framebuffer.bind()
            gl.glViewport(0, 0, self.width, self.height)
            window.projection = Mat4.orthogonal_projection(
                0,
                self.width,
                0,
                self.height,
                -8192,
                8192,
            )
            gl.glClearColor(0, 0, 0, 1)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            renderer.render(
                game,
                _RenderWindow(window, self.width, self.height),
            )
        finally:
            gl.glBindFramebuffer(
                gl.GL_FRAMEBUFFER,
                previous_framebuffer[0],
            )
            gl.glViewport(*previous_viewport)
            window.projection = previous_projection

    def delete(self) -> None:
        framebuffer = getattr(self, "framebuffer", None)
        if framebuffer is not None:
            framebuffer.delete()
        texture = getattr(self, "texture", None)
        if texture is not None:
            texture.delete()


class Window:
    """Pyglet output window that presents and routes input to a :class:`Game`.

    Pointer coordinates delivered to game callbacks are converted to virtual
    canvas pixels; window-move callbacks use the window's screen coordinates.
    """

    def __init__(
        self,
        title: str,
        *,
        width: int | None = None,
        height: int | None = None,
        resizable: bool = True,
        fullscreen: bool = False,
    ) -> None:
        """Create a window; attach a game with :meth:`show_game` before running.

        Args:
            title (str): Window caption.
            width (int | None): Optional output width in pixels; must be paired with height.
            height (int | None): Optional output height in pixels; must be paired with width.
            resizable (bool): Whether the output window can be resized.
            fullscreen (bool): Whether to start fullscreen.

        Raises:
            ValueError: If only one dimension is provided or dimensions are
                non-positive.
        """
        if (width is None) != (height is None):
            raise ValueError("window width and height must be provided together")
        if width is not None and (width <= 0 or height is None or height <= 0):
            raise ValueError("window dimensions must be positive")
        options: dict[str, object] = {
            "caption": title,
            "resizable": resizable,
            "fullscreen": fullscreen if sys.platform != "darwin" else False,
            "vsync": True,
        }
        if width is not None and height is not None:
            options["width"] = width
            options["height"] = height
        self._window = pyglet.window.Window(**options)
        self._macos_fullscreen: MacOSFullscreen | None = None
        self._start_fullscreen = fullscreen and sys.platform == "darwin"
        if sys.platform == "darwin":
            from .macos_fullscreen import MacOSFullscreen

            self._macos_fullscreen = MacOSFullscreen(self._window)
        self._size_explicit = width is not None
        self._game: Game | None = None
        self._renderer = SceneRenderer()
        self._surface: _PresentationSurface | None = None
        self._interpolation: RenderInterpolation | None = None
        self._accumulator: FixedStepAccumulator | None = None
        self._move_handlers: list[WindowMoveHandler] = []
        self._window_position = self._window.get_location()
        self._window.push_handlers(
            on_draw=self._on_draw,
            on_key_press=self._on_key_press,
            on_key_release=self._on_key_release,
            on_mouse_motion=self._on_mouse_motion,
            on_mouse_press=self._on_mouse_press,
            on_mouse_release=self._on_mouse_release,
            on_mouse_drag=self._on_mouse_drag,
            on_mouse_scroll=self._on_mouse_scroll,
            on_text=self._on_text,
            on_text_motion=self._on_text_motion,
            on_text_motion_select=self._on_text_motion_select,
            on_deactivate=self._on_deactivate,
            on_move=self._on_move,
            on_resize=self._on_resize,
        )

    def show_game(self, game: Game) -> None:
        """Attach ``game`` and prepare its virtual-resolution presentation.

        If no explicit window size was supplied, the window is resized to the
        game's virtual dimensions.
        """
        if self._surface is not None:
            self._surface.delete()
        self._game = game
        self._surface = _PresentationSurface(
            game.pixel_width,
            game.pixel_height,
        )
        if not self._size_explicit:
            self._window.set_size(game.pixel_width, game.pixel_height)
        if self._start_fullscreen:
            self.set_fullscreen(True)
            self._start_fullscreen = False

    @property
    def fullscreen(self) -> bool:
        """Whether the native window is currently fullscreen."""
        if self._macos_fullscreen is not None:
            return self._macos_fullscreen.fullscreen
        return self._window.fullscreen

    def set_fullscreen(self, fullscreen: bool = True) -> None:
        """Set fullscreen state, including the macOS-specific implementation."""
        if self._macos_fullscreen is not None:
            self._macos_fullscreen.set_fullscreen(fullscreen)
        else:
            self._window.set_fullscreen(fullscreen)

    def toggle_fullscreen(self) -> None:
        """Toggle the current fullscreen state."""
        self.set_fullscreen(not self.fullscreen)

    @property
    def input(self) -> Input:
        """Return the attached game's input state.

        Raises:
            RuntimeError: If :meth:`show_game` has not been called.
        """
        if self._game is None:
            raise RuntimeError("show_game must be called before accessing input")
        return self._game.input

    def on_move(self, handler: WindowMoveHandler) -> WindowMoveHandler:
        """Register a window-move callback receiving ``(x, y, dx, dy)``.

        Values are integer screen coordinates and deltas. Returns ``handler``
        to support decorator-style registration.
        """
        self._move_handlers.append(handler)
        return handler

    def _on_move(self, x: int, y: int) -> None:
        previous_x, previous_y = self._window_position
        self._window_position = x, y
        delta_x = x - previous_x
        delta_y = y - previous_y
        for handler in self._move_handlers:
            handler(x, y, delta_x, delta_y)

    def _on_draw(self) -> None:
        game = self._game
        interpolation = getattr(self, "_interpolation", None)
        accumulator = getattr(self, "_accumulator", None)
        if game is not None and interpolation is not None and accumulator is not None:
            with interpolation.apply(game, accumulator.alpha):
                self._draw_frame()
        else:
            self._draw_frame()

    def _draw_frame(self) -> None:
        game = self._game
        surface = getattr(self, "_surface", None)
        if game is None or surface is None:
            self._window.clear()
            if game is not None:
                renderer = getattr(self, "_renderer", None)
                if renderer is None:
                    renderer = self._renderer = SceneRenderer()
                renderer.render(game, self._window)
            return
        matte = game.presentation.matte_color
        alpha = matte[3] if len(matte) == 4 else 255
        gl.glClearColor(
            matte[0] / 255,
            matte[1] / 255,
            matte[2] / 255,
            alpha / 255,
        )
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        surface.render(game, self._renderer, self._window)
        rect = self._presentation_rect()
        surface.texture.blit(
            rect.x,
            rect.y,
            width=rect.width,
            height=rect.height,
        )

    def _presentation_rect(self) -> PresentationRect:
        if self._game is None:
            raise RuntimeError("show_game must be called first")
        return presentation_rect(
            self._game.width,
            self._game.height,
            self._window.width,
            self._window.height,
            self._game.presentation.scaling,
        )

    def _virtual_point(
        self,
        x: float,
        y: float,
        *,
        clamp: bool = False,
    ) -> tuple[float, float] | None:
        return self._presentation_rect().to_virtual(
            x,
            y,
            clamp=clamp,
        )

    def _on_deactivate(self) -> None:
        if self._game is not None:
            self._game.input._reset()
            self._game.scenes.current.gui.clear_focus()

    def _on_resize(self, width: int, height: int) -> None:
        del width, height
        if self._game is not None:
            self._game.scenes.current.gui.invalidate_layout()

    def _on_text(self, text: str) -> None:
        if self._game is not None:
            self._game._handle_text(text)

    def _on_text_motion(self, motion: int) -> None:
        if self._game is not None:
            self._game._handle_text_motion(motion)

    def _on_text_motion_select(self, motion: int) -> None:
        if self._game is not None:
            self._game._handle_text_motion_select(motion)

    def _on_key_press(self, symbol: int, modifiers: int) -> bool | None:
        if self._macos_fullscreen is not None:
            key = pyglet.window.key
            if symbol == key.F11 or (
                symbol == key.F
                and modifiers & key.MOD_CTRL
                and modifiers & key.MOD_COMMAND
            ):
                self.toggle_fullscreen()
                return pyglet.event.EVENT_HANDLED
            if symbol == key.ESCAPE and self.fullscreen:
                self.set_fullscreen(False)
                return pyglet.event.EVENT_HANDLED
            if symbol == key.M and modifiers & key.MOD_COMMAND and self.fullscreen:
                self.set_fullscreen(False)
                self._window.minimize()
                return pyglet.event.EVENT_HANDLED
        if self._game is not None:
            self._game._handle_key_press(symbol, modifiers)

    def _on_key_release(self, symbol: int, modifiers: int) -> None:
        if self._game is not None:
            self._game._handle_key_release(symbol, modifiers)

    def _on_mouse_motion(
        self, x: float, y: float, delta_x: float, delta_y: float
    ) -> None:
        if self._game is not None:
            point = self._virtual_point(x, y)
            if point is None:
                return
            virtual_delta = self._presentation_rect().delta_to_virtual(
                delta_x,
                delta_y,
            )
            self._game._handle_mouse_motion(
                *point,
                *virtual_delta,
            )

    def _on_mouse_press(
        self, x: float, y: float, button: int, modifiers: int
    ) -> None:
        if self._game is not None:
            point = self._virtual_point(x, y)
            if point is not None:
                self._game._handle_mouse_press(
                    *point,
                    button,
                    modifiers,
                )

    def _on_mouse_release(
        self, x: float, y: float, button: int, modifiers: int
    ) -> None:
        if self._game is not None:
            point = self._virtual_point(x, y, clamp=True)
            if point is not None:
                self._game._handle_mouse_release(
                    *point,
                    button,
                    modifiers,
                )

    def _on_mouse_drag(
        self,
        x: float,
        y: float,
        delta_x: float,
        delta_y: float,
        buttons: int,
        modifiers: int,
    ) -> None:
        if self._game is not None:
            point = self._virtual_point(x, y, clamp=True)
            if point is None:
                return
            virtual_delta = self._presentation_rect().delta_to_virtual(
                delta_x,
                delta_y,
            )
            self._game._handle_mouse_drag(
                *point,
                *virtual_delta,
                buttons,
                modifiers,
            )

    def _on_mouse_scroll(
        self, x: float, y: float, scroll_x: float, scroll_y: float
    ) -> None:
        if self._game is not None:
            point = self._virtual_point(x, y)
            if point is not None:
                self._game._handle_mouse_scroll(
                    *point,
                    scroll_x,
                    scroll_y,
                )

    def run(
        self,
        update: UpdateCallback | None = None,
        interval: float = 1 / 60,
    ) -> None:
        """Run the attached game's event loop with fixed-step updates.

        Args:
            update (UpdateCallback | None): Optional callback invoked after each game update with
                ``delta_time`` in seconds.
            interval (float): Fixed simulation step in seconds.

        Raises:
            RuntimeError: If no game is attached.
            ValueError: If ``interval`` is not positive.
        """
        if self._game is None:
            raise RuntimeError("show_game must be called before run")
        if interval <= 0:
            raise ValueError("interval must be positive")
        accumulator = FixedStepAccumulator(interval)
        interpolation = RenderInterpolation()
        self._accumulator = accumulator
        self._interpolation = interpolation

        def simulation_update(delta_time: float) -> None:
            if self._game is None:
                return
            interpolation.begin_step(self._game)
            self._game._update(delta_time)
            if update is not None:
                update(delta_time)
            interpolation.end_step(self._game)

        def process_frame(delta_time: float) -> None:
            accumulator.advance(delta_time, simulation_update)

        self._game._start()
        driver = None
        try:
            if sys.platform == "darwin":
                from .macos_frames import MacOSFrameDriver

                def reset_frame_timing() -> None:
                    nonlocal interpolation
                    accumulator.reset()
                    interpolation = RenderInterpolation()
                    self._interpolation = interpolation

                driver = MacOSFrameDriver(self._window, process_frame, reset_frame_timing)
                driver.start()
                pyglet.app.run(interval=None)
            else:
                pyglet.clock.schedule(process_frame)
                pyglet.app.run(interval=0)
        finally:
            try:
                if driver is not None:
                    driver.close()
                else:
                    pyglet.clock.unschedule(process_frame)
            finally:
                try:
                    if self._macos_fullscreen is not None:
                        self._macos_fullscreen.close()
                finally:
                    self._interpolation = None
                    self._accumulator = None
                    try:
                        self._game._stop()
                    finally:
                        if self._surface is not None:
                            self._surface.delete()
                            self._surface = None
                        renderer = getattr(self, "_renderer", None)
                        if renderer is not None:
                            renderer.delete()
