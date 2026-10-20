from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

from imgui_bundle import icons_fontawesome_6, imgui
from g.math import Vec2

from .components import flag, icon_button


Value = TypeVar("Value")


@dataclass(frozen=True)
class FieldResult(Generic[Value]):
    changed: bool
    value: Value


@dataclass(frozen=True)
class AssetFieldResult:
    picker_clicked: bool
    dropped: bool
    button_minimum: tuple[float, float]
    button_maximum: tuple[float, float]
    cleared: bool = False


class PropertyField(ABC, Generic[Value]):
    def __init__(
        self,
        label: str,
        identifier: str | None = None,
        *,
        maximum_width: float = 220,
        minimum_width: float = 80,
    ) -> None:
        self.label = label.split("##", 1)[0]
        self.identifier = identifier or label
        self.maximum_width = maximum_width
        self.minimum_width = minimum_width

    def draw(self, value: Value) -> FieldResult[Value]:
        start_x = imgui.get_cursor_pos_x()
        label_column_width, width = self._layout_widths()
        if self.label:
            self._draw_label(start_x, label_column_width)
        imgui.set_next_item_width(width)
        changed, result = self._draw_value(
            f"##property-{self.identifier}",
            value,
        )
        return FieldResult(changed, result)

    def _layout_widths(self) -> tuple[float, float]:
        available = imgui.get_content_region_avail().x
        if not self.label:
            return 0, min(self.maximum_width, max(self.minimum_width, available))
        label_column_width = max(
            imgui.calc_text_size(self.label).x,
            min(120.0, available * 0.4),
        )
        spacing = imgui.get_style().item_spacing.x
        value_width = min(
            self.maximum_width,
            max(self.minimum_width, available - label_column_width - spacing),
        )
        return label_column_width, value_width

    def _draw_label(self, start_x: float, label_column_width: float) -> None:
        imgui.align_text_to_frame_padding()
        imgui.text(self.label)
        imgui.same_line()
        imgui.set_cursor_pos_x(
            start_x
            + label_column_width
            + imgui.get_style().item_spacing.x
        )

    @abstractmethod
    def _draw_value(
        self,
        identifier: str,
        value: Value,
    ) -> tuple[bool, Value]:
        raise NotImplementedError


class IntegerField(PropertyField[int]):
    def __init__(
        self,
        label: str,
        identifier: str | None = None,
        *,
        step: int = 1,
        minimum: int = 0,
        maximum: int = 0,
        maximum_width: float = 220,
    ) -> None:
        super().__init__(
            label,
            identifier,
            maximum_width=maximum_width,
        )
        self.step = step
        self.minimum = minimum
        self.maximum = maximum

    def _draw_value(
        self,
        identifier: str,
        value: int,
    ) -> tuple[bool, int]:
        return imgui.drag_int(
            identifier,
            value,
            self.step,
            self.minimum,
            self.maximum,
        )


class FloatField(PropertyField[float]):
    def __init__(
        self,
        label: str,
        identifier: str | None = None,
        *,
        speed: float = 0.1,
        minimum: float = 0,
        maximum: float = 0,
        maximum_width: float = 220,
    ) -> None:
        super().__init__(
            label,
            identifier,
            maximum_width=maximum_width,
        )
        self.speed = speed
        self.minimum = minimum
        self.maximum = maximum

    def _draw_value(
        self,
        identifier: str,
        value: float,
    ) -> tuple[bool, float]:
        return imgui.drag_float(
            identifier,
            value,
            self.speed,
            self.minimum,
            self.maximum,
        )


class Vec2Field(PropertyField[Vec2]):
    def __init__(
        self,
        label: str,
        identifier: str | None = None,
        *,
        speed: float = 0.1,
        minimum: float = 0,
        maximum: float = 0,
        maximum_width: float = 220,
    ) -> None:
        super().__init__(label, identifier, maximum_width=maximum_width)
        self.speed = speed
        self.minimum = minimum
        self.maximum = maximum

    def _draw_value(self, identifier: str, value: Vec2) -> tuple[bool, Vec2]:
        changed, coordinates = imgui.drag_float2(
            identifier,
            [value.x, value.y],
            self.speed,
            self.minimum,
            self.maximum,
        )
        return changed, Vec2(*coordinates)


class SliderFloatField(FloatField):
    def _draw_value(
        self,
        identifier: str,
        value: float,
    ) -> tuple[bool, float]:
        return imgui.slider_float(
            identifier,
            value,
            self.minimum,
            self.maximum,
        )


class ChoiceField(PropertyField[int]):
    def __init__(
        self,
        label: str,
        values: list[str],
        identifier: str | None = None,
        *,
        maximum_width: float = 220,
    ) -> None:
        super().__init__(
            label,
            identifier,
            maximum_width=maximum_width,
        )
        self.values = values

    def _draw_value(
        self,
        identifier: str,
        value: int,
    ) -> tuple[bool, int]:
        return imgui.combo(identifier, value, self.values)


class BooleanField(PropertyField[bool]):
    def _draw_value(
        self,
        identifier: str,
        value: bool,
    ) -> tuple[bool, bool]:
        return imgui.checkbox(identifier, value)

class MaskField(PropertyField[int]):
    def __init__(
        self, label: str, identifier: str | None = None, *,
        bit_names: tuple[str, ...] = (),
    ) -> None:
        super().__init__(label, identifier)
        self.bit_names = bit_names

    def _draw_value(self, identifier: str, value: int) -> tuple[bool, int]:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("mask must be an unsigned 32-bit integer")
        preview = "All" if value == 0xFFFFFFFF else "None" if value == 0 else f"{value.bit_count()} bits / 0x{value:08X}"
        changed = False
        if imgui.begin_combo(identifier, preview):
            try:
                if imgui.button(f"All##{identifier}-all"):
                    value, changed = 0xFFFFFFFF, True
                imgui.same_line()
                if imgui.button(f"None##{identifier}-none"):
                    value, changed = 0, True
                for index in range(32):
                    name = self.bit_names[index] if index < len(self.bit_names) else f"Bit {index + 1}"
                    toggled, enabled = imgui.checkbox(
                        f"{name}##{identifier}-bit-{index}", bool(value & (1 << index)),
                    )
                    if toggled:
                        value = value | (1 << index) if enabled else value & ~(1 << index)
                        changed = True
            finally:
                imgui.end_combo()
        return changed, value


class TextField(PropertyField[str]):
    def __init__(
        self,
        label: str,
        identifier: str | None = None,
        *,
        submit_on_enter: bool = False,
        maximum_width: float = 220,
    ) -> None:
        super().__init__(
            label,
            identifier,
            maximum_width=maximum_width,
        )
        self.submit_on_enter = submit_on_enter

    def _draw_value(
        self,
        identifier: str,
        value: str,
    ) -> tuple[bool, str]:
        flags = (
            flag(imgui.InputTextFlags_.enter_returns_true)
            if self.submit_on_enter
            else 0
        )
        return imgui.input_text(identifier, value, flags)


class ColorField(PropertyField[list[float]]):
    def __init__(
        self,
        label: str,
        identifier: str | None = None,
        *,
        has_alpha: bool = True,
        maximum_width: float = 220,
    ) -> None:
        super().__init__(
            label,
            identifier,
            maximum_width=maximum_width,
        )
        self.has_alpha = has_alpha

    def _draw_value(
        self,
        identifier: str,
        value: list[float],
    ) -> tuple[bool, list[float]]:
        flags = (
            flag(imgui.ColorEditFlags_.uint8)
            | flag(imgui.ColorEditFlags_.display_hex)
            | flag(imgui.ColorEditFlags_.alpha_preview_half)
        )
        if not self.has_alpha:
            flags |= flag(imgui.ColorEditFlags_.no_alpha)
        return imgui.color_edit4(identifier, value, flags)


class OptionalColorField(PropertyField[list[float] | None]):
    def __init__(
        self,
        label: str,
        identifier: str,
        *,
        fallback: list[float],
        has_alpha: bool = True,
        can_disable: bool = True,
        maximum_width: float = 220,
    ) -> None:
        super().__init__(
            label,
            identifier,
            maximum_width=maximum_width,
        )
        self.fallback = fallback
        self.has_alpha = has_alpha
        self.can_disable = can_disable

    def draw(
        self,
        value: list[float] | None,
    ) -> FieldResult[list[float] | None]:
        start_x = imgui.get_cursor_pos_x()
        label_column_width, width = self._layout_widths()
        self._draw_label(start_x, label_column_width)
        enabled = value is not None
        imgui.begin_disabled(enabled and not self.can_disable)
        toggle_changed, enabled = imgui.checkbox(
            f"##property-{self.identifier}-enabled",
            enabled,
        )
        imgui.end_disabled()
        if imgui.is_item_hovered(
            flag(imgui.HoveredFlags_.allow_when_disabled)
        ):
            message = (
                f"{'Disable' if enabled else 'Enable'} {self.label}"
                if self.can_disable
                else "A shape needs a fill or outline"
            )
            imgui.set_tooltip(message)
        imgui.same_line()
        color_width = max(
            40.0,
            width
            - imgui.get_frame_height()
            - imgui.get_style().item_spacing.x,
        )
        imgui.set_next_item_width(color_width)
        imgui.begin_disabled(not enabled)
        flags = (
            flag(imgui.ColorEditFlags_.uint8)
            | flag(imgui.ColorEditFlags_.display_hex)
            | flag(imgui.ColorEditFlags_.alpha_preview_half)
        )
        if not self.has_alpha:
            flags |= flag(imgui.ColorEditFlags_.no_alpha)
        color_changed, color = imgui.color_edit4(
            f"##property-{self.identifier}-color",
            value if value is not None else self.fallback,
            flags,
        )
        imgui.end_disabled()
        if not enabled:
            return FieldResult(toggle_changed, None)
        return FieldResult(
            toggle_changed or color_changed,
            color,
        )

    def _draw_value(
        self,
        identifier: str,
        value: list[float] | None,
    ) -> tuple[bool, list[float] | None]:
        del identifier, value
        raise NotImplementedError


class AssetField(PropertyField[str]):
    def __init__(
        self,
        label: str,
        identifier: str,
        *,
        active: bool,
        payload_type: str,
        kind: str,
        maximum_width: float = 220,
        clearable: bool = False,
        missing: bool = False,
    ) -> None:
        super().__init__(
            label,
            identifier,
            maximum_width=maximum_width,
        )
        self.active = active
        self.payload_type = payload_type
        self.kind = kind
        self.clearable = clearable
        self.missing = missing

    def draw_asset(self, path: str) -> AssetFieldResult:
        display = self.abbreviate_path(path)
        start_x = imgui.get_cursor_pos_x()
        label_column_width, width = self._layout_widths()
        self._draw_label(start_x, label_column_width)
        button_size = imgui.get_frame_height()
        show_clear = self.clearable and bool(path)
        buttons = 2 if show_clear else 1
        input_width = max(40.0, width - button_size * buttons)
        imgui.set_next_item_width(input_width)
        missing = self.missing and bool(path)
        if missing:
            imgui.push_style_color(
                imgui.Col_.text,
                imgui.ImVec4(1, 0.4, 0.35, 1),
            )
        imgui.input_text(
            f"##property-{self.identifier}",
            f"{display} (missing)" if missing else display,
            flag(imgui.InputTextFlags_.read_only),
        )
        if missing:
            imgui.pop_style_color()
        if imgui.is_item_hovered():
            imgui.set_tooltip(
                f"Missing asset: {path}"
                if missing
                else path or f"No {self.kind} selected"
            )
        dropped = False
        if imgui.begin_drag_drop_target():
            dropped = (
                imgui.accept_drag_drop_payload_py_id(
                    self.payload_type
                )
                is not None
            )
            imgui.end_drag_drop_target()
        cleared = False
        if show_clear:
            imgui.same_line(0, 0)
            cleared = icon_button(
                icons_fontawesome_6.ICON_FA_XMARK,
                f"clear-asset-{self.identifier}",
                f"Clear {self.kind}",
                size=imgui.ImVec2(button_size, button_size),
            )
        imgui.same_line(0, 0)
        picker_clicked = icon_button(
            icons_fontawesome_6.ICON_FA_CIRCLE_DOT,
            f"pick-asset-{self.identifier}",
            (
                f"Cancel {self.kind} selection"
                if self.active
                else f"Select a {self.kind} from Assets"
            ),
            size=imgui.ImVec2(button_size, button_size),
            selected=self.active,
        )
        button_minimum = imgui.get_item_rect_min()
        button_maximum = imgui.get_item_rect_max()
        return AssetFieldResult(
            picker_clicked,
            dropped,
            (button_minimum.x, button_minimum.y),
            (button_maximum.x, button_maximum.y),
            cleared,
        )

    def _draw_value(
        self,
        identifier: str,
        value: str,
    ) -> tuple[bool, str]:
        del identifier
        return False, value

    @staticmethod
    def abbreviate_path(path: str, limit: int = 42) -> str:
        if not path:
            return "No asset selected"
        normalized = path.replace("\\", "/")
        if len(normalized) <= limit:
            return normalized
        parts = normalized.split("/")
        filename = parts[-1]
        if len(filename) + 4 >= limit:
            return f"...{filename[-(limit - 3):]}"
        prefix = parts[0] if len(parts) > 1 else ""
        remaining = limit - len(filename) - 5
        shortened_prefix = prefix[:remaining]
        return f"{shortened_prefix}/.../{filename}"
