from __future__ import annotations

from typing import Any

from imgui_bundle import imgui


def flag(value: Any) -> int:
    return int(value.value)


def color(
    red: int,
    green: int,
    blue: int,
    alpha: int = 255,
) -> int:
    return imgui.get_color_u32(
        imgui.ImVec4(
            red / 255,
            green / 255,
            blue / 255,
            alpha / 255,
        )
    )


def begin_panel(
    title: str,
    *,
    flags: int = 0,
) -> bool:
    visible, _ = imgui.begin(
        title,
        None,
        flags | flag(imgui.WindowFlags_.no_collapse),
    )
    return visible


def selected_button(
    label: str,
    selected: bool,
    size: imgui.ImVec2 | None = None,
) -> bool:
    if selected:
        imgui.push_style_color(
            imgui.Col_.button,
            imgui.ImVec4(0.06, 0.46, 0.63, 1),
        )
    clicked = (
        imgui.button(label)
        if size is None
        else imgui.button(label, size)
    )
    if selected:
        imgui.pop_style_color()
    return clicked


def icon_button(
    icon: str,
    identifier: str,
    tooltip: str,
    *,
    size: imgui.ImVec2 | None = None,
    selected: bool = False,
) -> bool:
    if selected:
        imgui.push_style_color(
            imgui.Col_.button,
            imgui.ImVec4(0.06, 0.46, 0.63, 1),
        )
    clicked = (
        imgui.button(f"{icon}##{identifier}")
        if size is None
        else imgui.button(f"{icon}##{identifier}", size)
    )
    if selected:
        imgui.pop_style_color()
    if imgui.is_item_hovered(
        flag(imgui.HoveredFlags_.allow_when_disabled)
    ):
        imgui.set_tooltip(tooltip)
    return clicked
