from __future__ import annotations

from pathlib import Path

from imgui_bundle import icons_fontawesome_6, imgui


_FONT_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "fonts"
    / "Inter"
    / "static"
    / "Inter_18pt-Regular.ttf"
)
_CONSOLE_FONT_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "fonts"
    / "JetBrains_Mono"
    / "static"
    / "JetBrainsMono-Regular.ttf"
)
_ICON_MODULE_PATH = icons_fontawesome_6.__file__
if _ICON_MODULE_PATH is None:
    raise RuntimeError("could not locate the ImGui Bundle icon font")
_ICON_FONT_PATH = (
    Path(_ICON_MODULE_PATH).resolve().parents[1]
    / "assets"
    / "fonts"
    / "Font_Awesome_6_Free-Solid-900.otf"
)


def configure_fonts(size_pixels: float = 16) -> Path:
    if not _FONT_PATH.is_file():
        raise RuntimeError(f"editor font does not exist: {_FONT_PATH}")
    if not _ICON_FONT_PATH.is_file():
        raise RuntimeError(
            f"editor icon font does not exist: {_ICON_FONT_PATH}"
        )
    io = imgui.get_io()
    font = io.fonts.add_font_from_file_ttf(
        str(_FONT_PATH),
        size_pixels,
    )
    icon_config = imgui.ImFontConfig()
    icon_config.merge_mode = True
    icon_config.pixel_snap_h = True
    icon_config.glyph_min_advance_x = size_pixels
    io.fonts.add_font_from_file_ttf(
        str(_ICON_FONT_PATH),
        size_pixels,
        font_cfg=icon_config,
    )
    io.font_default = font
    return _FONT_PATH


def configure_console_font(
    size_pixels: float = 16,
) -> imgui.ImFont:
    if not _CONSOLE_FONT_PATH.is_file():
        raise RuntimeError(
            f"editor console font does not exist: {_CONSOLE_FONT_PATH}"
        )
    return imgui.get_io().fonts.add_font_from_file_ttf(
        str(_CONSOLE_FONT_PATH),
        size_pixels,
    )


def configure_style() -> None:
    imgui.style_colors_dark()
    style = imgui.get_style()
    style.window_rounding = 6
    style.child_rounding = 5
    style.frame_rounding = 4
    style.popup_rounding = 6
    style.tab_rounding = 4
    style.grab_rounding = 4
    style.scrollbar_rounding = 6
    style.window_padding = imgui.ImVec2(10, 10)
    style.frame_padding = imgui.ImVec2(8, 5)
    style.item_spacing = imgui.ImVec2(8, 7)
    style.item_inner_spacing = imgui.ImVec2(6, 5)
    style.indent_spacing = 18
    style.scrollbar_size = 13

    colors = {
        imgui.Col_.window_bg: (0.043, 0.051, 0.067, 1),
        imgui.Col_.child_bg: (0.052, 0.062, 0.081, 1),
        imgui.Col_.popup_bg: (0.058, 0.069, 0.090, 0.98),
        imgui.Col_.border: (0.13, 0.16, 0.20, 1),
        imgui.Col_.frame_bg: (0.13, 0.155, 0.20, 1),
        imgui.Col_.frame_bg_hovered: (0.18, 0.215, 0.27, 1),
        imgui.Col_.frame_bg_active: (0.22, 0.265, 0.33, 1),
        imgui.Col_.button: (0.075, 0.23, 0.32, 1),
        imgui.Col_.button_hovered: (0.08, 0.39, 0.53, 1),
        imgui.Col_.button_active: (0.06, 0.50, 0.68, 1),
        imgui.Col_.header: (0.075, 0.19, 0.26, 1),
        imgui.Col_.header_hovered: (0.08, 0.34, 0.47, 1),
        imgui.Col_.header_active: (0.06, 0.46, 0.63, 1),
        imgui.Col_.check_mark: (0.22, 0.75, 0.98, 1),
        imgui.Col_.slider_grab: (0.12, 0.59, 0.80, 1),
        imgui.Col_.slider_grab_active: (0.26, 0.75, 0.98, 1),
        imgui.Col_.separator: (0.14, 0.17, 0.22, 1),
        imgui.Col_.separator_hovered: (0.13, 0.48, 0.65, 1),
        imgui.Col_.separator_active: (0.18, 0.66, 0.88, 1),
        imgui.Col_.title_bg: (0.035, 0.041, 0.055, 1),
        imgui.Col_.title_bg_active: (0.052, 0.065, 0.085, 1),
        imgui.Col_.menu_bar_bg: (0.038, 0.046, 0.061, 1),
        imgui.Col_.tab: (0.055, 0.070, 0.092, 1),
        imgui.Col_.tab_hovered: (0.08, 0.34, 0.47, 1),
        imgui.Col_.tab_selected: (0.07, 0.27, 0.38, 1),
        imgui.Col_.text: (0.90, 0.93, 0.96, 1),
        imgui.Col_.text_disabled: (0.46, 0.52, 0.60, 1),
        imgui.Col_.text_selected_bg: (0.08, 0.43, 0.60, 0.65),
        imgui.Col_.scrollbar_bg: (0.035, 0.042, 0.055, 1),
        imgui.Col_.scrollbar_grab: (0.14, 0.17, 0.22, 1),
        imgui.Col_.scrollbar_grab_hovered: (0.20, 0.25, 0.32, 1),
        imgui.Col_.scrollbar_grab_active: (0.10, 0.43, 0.58, 1),
    }
    for target, value in colors.items():
        style.set_color_(target.value, imgui.ImVec4(*value))
