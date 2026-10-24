from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pyglet
from pyglet.event import EventDispatcher

if TYPE_CHECKING:
    from pyglet.window.cocoa import CocoaWindow


_target_class: Any = None


def _make_target(controller: MacOSFullscreen) -> Any:
    from pyglet.libs.darwin.cocoapy import ObjCClass, ObjCSubclass

    global _target_class
    if _target_class is None:
        _target_class = ObjCSubclass("NSObject", "GFullscreenButtonTarget")

        @_target_class.method("v@")
        def toggleBorderless_(target: Any, sender: Any) -> None:
            # Keep Python errors out of the Objective-C callback boundary.
            controller = target.controller
            try:
                pyglet.app.platform_event_loop.post_event(
                    controller, "on_toggle_fullscreen",
                )
            except Exception as error:
                controller._delivery_error = error

        @_target_class.method("v@")
        def leaveBorderless_(target: Any, sender: Any) -> None:
            controller = target.controller
            try:
                pyglet.app.platform_event_loop.post_event(
                    controller, "on_leave_fullscreen",
                )
            except Exception as error:
                controller._delivery_error = error

    target = ObjCClass("GFullscreenButtonTarget").alloc().init()
    if target is None:
        raise RuntimeError("Cannot create the macOS fullscreen button target")
    target.controller = controller
    return target


def _make_windowed_menu_item(target: Any) -> tuple[Any, Any]:
    from pyglet.libs.darwin.cocoapy import (
        ObjCClass,
        get_NSString,
        get_selector,
    )

    application = ObjCClass("NSApplication").sharedApplication()
    main_menu = application.mainMenu()
    if main_menu is None or main_menu.numberOfItems() == 0:
        raise RuntimeError("Cannot find the macOS application menu")
    application_menu = main_menu.itemAtIndex_(0).submenu()
    if application_menu is None:
        raise RuntimeError("Cannot find the macOS application submenu")
    item = ObjCClass("NSMenuItem").alloc().initWithTitle_action_keyEquivalent_(
        get_NSString("Switch to Windowed Mode"),
        get_selector("leaveBorderless:"),
        get_NSString(""),
    )
    if item is None:
        raise RuntimeError("Cannot create the macOS windowed-mode menu item")
    item.setTarget_(target)
    application_menu.insertItem_atIndex_(item, 1)
    return application_menu, item


class MacOSFullscreen(EventDispatcher):
    """Use a borderless window without entering a native fullscreen Space.

    The application menu exposes a windowed-mode action while this custom
    fullscreen state is active.
    """

    def __init__(self, window: CocoaWindow) -> None:
        super().__init__()
        self._window = window
        self._closed = False
        self._delivery_error: Exception | None = None
        self._saved: tuple[Any, int] | None = None
        self._responder: Any = None
        native = window._nswindow
        if native is None:
            raise RuntimeError("Cannot configure fullscreen for a closed window")
        self._collection = native.collectionBehavior()
        self._target = _make_target(self)
        self._application_menu, self._windowed_item = (
            _make_windowed_menu_item(self._target)
        )
        self._windowed_item.setEnabled_(False)
        button = native.standardWindowButton_(2)
        self._button_target = button.target()
        self._button_action = button.action()
        # Keep Cocoa's fullscreen glyph while replacing the button's action.
        native.setCollectionBehavior_((self._collection & ~(1 << 9)) | (1 << 7))
        self._wire_button()
        pyglet.clock.schedule_interval(self._check_error, 1)
        window.push_handlers(on_close=self.close)

    def _check_error(self, delta_time: float) -> None:
        if self._delivery_error is not None:
            raise RuntimeError("macOS fullscreen action delivery failed") from self._delivery_error

    def _wire_button(self) -> None:
        from pyglet.libs.darwin.cocoapy import get_selector

        native = self._window._nswindow
        if native is None:
            raise RuntimeError("Cannot configure fullscreen for a closed window")
        button = native.standardWindowButton_(2)
        button.setTarget_(self._target)
        button.setAction_(get_selector("toggleBorderless:"))

    @property
    def fullscreen(self) -> bool:
        return self._saved is not None

    def set_fullscreen(self, fullscreen: bool) -> None:
        from pyglet.libs.darwin.cocoapy import NSMakeRect

        if self._closed:
            raise RuntimeError("Cannot change fullscreen after the controller has closed")
        if self.fullscreen == fullscreen:
            return
        native = self._window._nswindow
        if native is None:
            raise RuntimeError("Cannot change fullscreen for a closed window")
        if fullscreen:
            self._responder = native.firstResponder()
            frame = native.frame()
            saved_frame = NSMakeRect(
                frame.origin.x, frame.origin.y, frame.size.width, frame.size.height,
            )
            self._saved = saved_frame, native.styleMask()
            native.setStyleMask_(0)
            native.setFrame_display_(native.screen().frame(), True)
        else:
            if self._saved is None:
                raise RuntimeError("Missing the windowed macOS frame")
            frame, style = self._saved
            native.setStyleMask_(style)
            native.setFrame_display_(frame, True)
            self._saved = None
            # Cocoa recreates title-bar buttons when their style is restored.
            self._wire_button()
        self._windowed_item.setEnabled_(fullscreen)
        native.makeKeyAndOrderFront_(None)
        # Changing style discards pyglet's text/key responder.
        if not native.makeFirstResponder_(self._responder):
            raise RuntimeError("Cannot restore the macOS game keyboard responder")
        self._window.context.update_geometry()

    def on_toggle_fullscreen(self) -> None:
        if not self._closed:
            self.set_fullscreen(not self.fullscreen)

    def on_leave_fullscreen(self) -> None:
        if not self._closed and self.fullscreen:
            self.set_fullscreen(False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        pyglet.clock.unschedule(self._check_error)
        self._window.remove_handlers(on_close=self.close)
        if self._window._nswindow is not None:
            button = self._window._nswindow.standardWindowButton_(2)
            if button is not None:
                button.setTarget_(self._button_target)
                button.setAction_(self._button_action)
            self._window._nswindow.setCollectionBehavior_(self._collection)
        self._application_menu.removeItem_(self._windowed_item)
        self._windowed_item.release()
        self._target.controller = None
        self._target.release()


MacOSFullscreen.register_event_type("on_toggle_fullscreen")
MacOSFullscreen.register_event_type("on_leave_fullscreen")
