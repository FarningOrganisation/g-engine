from __future__ import annotations

import ctypes
from collections.abc import Callable
from threading import Lock
from time import perf_counter
from typing import TYPE_CHECKING

import pyglet
from pyglet.event import EventDispatcher

if TYPE_CHECKING:
    from pyglet.window.cocoa import CocoaWindow


_OutputCallback = ctypes.CFUNCTYPE(
    ctypes.c_int32,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p,
)


class _DisplayLink:
    def __init__(self, display_id: int, request_frame: Callable[[], None]) -> None:
        self._library = ctypes.CDLL(
            "/System/Library/Frameworks/CoreVideo.framework/CoreVideo"
        )
        self._link = ctypes.c_void_p()
        self._callback = _OutputCallback(
            lambda *_: self._request(request_frame)
        )
        signatures = {
            "CVDisplayLinkCreateWithCGDisplay": ([ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int32),
            "CVDisplayLinkSetOutputCallback": ([ctypes.c_void_p, _OutputCallback, ctypes.c_void_p], ctypes.c_int32),
            "CVDisplayLinkSetCurrentCGDisplay": ([ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int32),
            "CVDisplayLinkStart": ([ctypes.c_void_p], ctypes.c_int32),
            "CVDisplayLinkStop": ([ctypes.c_void_p], ctypes.c_int32),
            "CVDisplayLinkRelease": ([ctypes.c_void_p], None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._library, name)
            function.argtypes, function.restype = arguments, result
        self._check(self._library.CVDisplayLinkCreateWithCGDisplay(display_id, ctypes.byref(self._link)), "create")
        try:
            self._check(self._library.CVDisplayLinkSetOutputCallback(self._link, self._callback, None), "set callback")
        except BaseException:
            self._library.CVDisplayLinkRelease(self._link)
            raise
        self._started = False
        self._closed = False

    @staticmethod
    def _request(request_frame: Callable[[], None]) -> int:
        request_frame()
        return 0

    @staticmethod
    def _check(status: int, operation: str) -> None:
        if status != 0:
            raise RuntimeError(f"CoreVideo display link {operation} failed (status {status})")

    def start(self) -> None:
        self._check(self._library.CVDisplayLinkStart(self._link), "start")
        self._started = True

    def set_display(self, display_id: int) -> None:
        self._check(self._library.CVDisplayLinkSetCurrentCGDisplay(self._link, display_id), "change display")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._started:
                self._check(self._library.CVDisplayLinkStop(self._link), "stop")
        finally:
            self._library.CVDisplayLinkRelease(self._link)


class MacOSFrameDriver(EventDispatcher):
    """Coalesce display-link requests and run simulation/GL only on the main thread."""

    def __init__(
        self,
        window: CocoaWindow,
        update: Callable[[float], None],
        reset: Callable[[], None],
    ) -> None:
        super().__init__()
        self._window = window
        self._update = update
        self._reset = reset
        self._lock = Lock()
        self._pending = False
        self._active = False
        self._error: Exception | None = None
        self._previous = perf_counter()
        self._geometry = self._window.get_framebuffer_size()
        self._display_id = self._current_display()
        self._link = _DisplayLink(self._display_id, self._request_frame)
        self._vsync = window.vsync
        self._closed = False
        self._timing_reset = False

    def _current_display(self) -> int:
        from pyglet.libs.darwin.cocoapy import get_NSString

        native_window = self._window._nswindow
        if native_window is None:
            raise RuntimeError("Cannot query the display of a closed macOS window")
        screen = native_window.screen()
        return int(screen.deviceDescription().objectForKey_(get_NSString("NSScreenNumber")).intValue())

    def _request_frame(self) -> None:
        with self._lock:
            if not self._active or self._pending:
                return
            self._pending = True
        try:
            pyglet.app.platform_event_loop.post_event(self, "on_display_frame")
        except Exception as error:
            # ctypes callbacks cannot propagate exceptions; surface them on the main loop.
            with self._lock:
                self._error = error
                self._pending = False

    def start(self) -> None:
        self._window.set_vsync(False)
        self._previous = perf_counter()
        with self._lock:
            self._active = True
        try:
            self._link.start()
        except BaseException:
            with self._lock:
                self._active = False
            self._window.set_vsync(self._vsync)
            raise
        pyglet.clock.schedule_interval(self._check_error, 1)
        self._window.push_handlers(on_close=self._on_close, on_activate=self._reset_timing)

    def _on_close(self) -> None:
        with self._lock:
            self._active = False

    def _reset_timing(self) -> None:
        self._timing_reset = True

    def _check_error(self, delta_time: float) -> None:
        with self._lock:
            error = self._error
        if error is not None:
            raise RuntimeError("Display-link frame delivery failed") from error

    def on_display_frame(self) -> None:
        with self._lock:
            if not self._active:
                self._pending = False
                return
        try:
            self._check_error(0)
            now = perf_counter()
            geometry = self._window.get_framebuffer_size()
            display_id = self._current_display()
            if self._timing_reset or geometry != self._geometry or display_id != self._display_id:
                if display_id != self._display_id:
                    self._link.set_display(display_id)
                self._geometry, self._display_id = geometry, display_id
                self._previous = now
                self._reset()
                self._timing_reset = False
            elapsed = now - self._previous
            self._previous = now
            self._update(elapsed)
            self._window.draw(elapsed)
        finally:
            with self._lock:
                self._pending = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            self._active = False
        pyglet.clock.unschedule(self._check_error)
        self._window.remove_handlers(on_close=self._on_close, on_activate=self._reset_timing)
        try:
            self._link.close()
        finally:
            if self._window.context is not None:
                self._window.set_vsync(self._vsync)


MacOSFrameDriver.register_event_type("on_display_frame")
