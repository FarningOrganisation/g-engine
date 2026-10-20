"""Source-defined fields shared by persistence and the editor."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any, ClassVar, Generic, Literal, TypeVar, cast, get_origin, get_type_hints


Value = TypeVar("Value")
Widget = Literal[
    "auto", "color", "image", "animation", "particle", "font", "mask"
]


@dataclass
class InspectorField(Generic[Value]):
    default: Value
    label: str | None = None
    tooltip: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    widget: Widget = "auto"
    optional_color: tuple[int, ...] | None = None
    counterpart: str | None = None
    group: str | None = None
    group_expanded: bool = True
    bit_names: tuple[str, ...] = ()
    networked: bool = False

    def __post_init__(self) -> None:
        for name, text in (("label", self.label), ("tooltip", self.tooltip), ("group", self.group)):
            if text is not None and (not isinstance(text, str) or not text.strip()):
                raise ValueError(f"{name} must be a non-empty string")
        for bound in (self.minimum, self.maximum):
            if bound is not None and not isfinite(bound):
                raise ValueError("field bounds must be finite")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("field minimum must not exceed maximum")
        if self.choices and (
            len(set(self.choices)) != len(self.choices)
            or any(not isinstance(choice, str) or not choice for choice in self.choices)
        ):
            raise ValueError("field choices must be unique non-empty strings")
        if self.widget not in {
            "auto", "color", "image", "animation", "particle", "font", "mask"
        }:
            raise ValueError(f"unknown inspector widget: {self.widget!r}")
        if self.optional_color is not None and (
            len(self.optional_color) not in {3, 4}
            or any(not 0 <= channel <= 255 for channel in self.optional_color)
        ):
            raise ValueError("optional color must contain three or four byte channels")
        if len(self.bit_names) > 32 or any(not isinstance(name, str) or not name.strip() for name in self.bit_names):
            raise ValueError("bit_names must contain at most 32 non-empty names")
        if not isinstance(self.networked, bool):
            raise ValueError("networked must be a boolean")
        if self.widget == "mask":
            validate_field_value(self, self.default)

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, instance: object | None, owner: type | None = None) -> Value:
        if instance is None:
            return deepcopy(self.default)
        if self._name not in vars(instance):
            vars(instance)[self._name] = deepcopy(self.default)
        return vars(instance)[self._name]

    def __set__(self, instance: object, value: Value) -> None:
        vars(instance)[self._name] = value


def inspector_field(
    default: Value,
    *,
    label: str | None = None,
    tooltip: str | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    choices: tuple[str, ...] = (),
    widget: Widget = "auto",
    optional_color: tuple[int, ...] | None = None,
    counterpart: str | None = None,
    group: str | None = None,
    group_expanded: bool = True,
    bit_names: tuple[str, ...] = (),
    networked: bool = False,
) -> Value:
    """Declare an editor-inspectable value with a default and UI metadata.

    Use this as a class attribute; the annotation remains the value's type.
    Values are initialized from a deep copy of ``default`` on first instance
    access.

    Args:
        default: Initial value for each instance.
        label: Optional non-empty editor label.
        tooltip: Optional non-empty help text.
        minimum: Optional finite lower bound for numeric values.
        maximum: Optional finite upper bound for numeric values.
        choices: Optional unique, non-empty string choices.
        widget: Requested editor widget kind.
        optional_color: Optional RGB or RGBA fallback color.
        counterpart: Name of a related field, when applicable.
        group: Optional non-empty editor group name.
        group_expanded: Whether the editor group starts expanded.
        bit_names: Optional names for up to 32 mask bits.
        networked: Whether the field is replicated as network state.

    Returns:
        A descriptor exposed as the declared value type.

    Raises:
        ValueError: If field metadata is invalid.
    """
    # The descriptor reads/writes Value; callers annotate the value, not its storage.
    return cast(Value, InspectorField(
        default, label, tooltip, minimum, maximum, choices, widget,
        optional_color, counterpart, group, group_expanded, bit_names,
        networked,
    ))


def network_field(default: Value, **options: Any) -> Value:
    """Declare custom server-owned runtime state for automatic replication.

    Args:
        default: Initial value for each instance.
        **options: Metadata accepted by :func:`inspector_field`, except
            ``networked`` which this helper always enables.

    Returns:
        An inspectable descriptor configured for network replication.

    Raises:
        TypeError: If ``networked`` is included in ``options``.
        ValueError: If the supplied field metadata is invalid.
    """
    if "networked" in options:
        raise TypeError("network_field always enables network replication")
    return inspector_field(default, networked=True, **options)


def inspector_fields(value_type: type) -> dict[str, InspectorField]:
    fields: dict[str, InspectorField] = {}
    for base in reversed(value_type.__mro__):
        for name, declaration in vars(base).items():
            if not name.startswith("_"):
                if isinstance(declaration, InspectorField):
                    fields[name] = declaration
                else:
                    fields.pop(name, None)
        for name, declaration in vars(base).get("__inspector_fields__", {}).items():
            if not isinstance(declaration, InspectorField):
                raise TypeError(f"{base.__name__}.{name} must use inspector_field")
            fields[name] = declaration
    for name, field in fields.items():
        if field.tooltip is None:
            attribute = getattr(value_type, name, None)
            if isinstance(attribute, property) and attribute.__doc__:
                field = deepcopy(field)
                field.tooltip = attribute.__doc__.strip()
                fields[name] = field
    return fields


def validate_field_value(field: InspectorField, value: object) -> None:
    if field.widget == "mask" and (
        not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF
    ):
        raise ValueError("mask must be an unsigned 32-bit integer")
    if field.choices and value not in field.choices:
        raise ValueError(f"value must be one of {', '.join(field.choices)}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not isfinite(value):
            raise ValueError("field value must be finite")
        if field.minimum is not None and value < field.minimum:
            raise ValueError(f"value must be at least {field.minimum}")
        if field.maximum is not None and value > field.maximum:
            raise ValueError(f"value must be at most {field.maximum}")
    elif isinstance(value, dict) and set(value) == {"$vec2"}:
        coordinates = value["$vec2"]
        if not isinstance(coordinates, list) or len(coordinates) != 2 or any(
            not isinstance(item, (int, float)) or isinstance(item, bool)
            for item in coordinates
        ):
            raise ValueError("invalid Vec2 value")
        for coordinate in coordinates:
            validate_field_value(field, coordinate)


def public_field_names(value_type: type) -> tuple[str, ...]:
    return tuple(
        name for name, annotation in get_type_hints(value_type).items()
        if not name.startswith("_") and get_origin(annotation) is not ClassVar
        and not isinstance(getattr(value_type, name, None), property)
    )
