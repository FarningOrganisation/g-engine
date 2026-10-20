"""Component editing tools and their shared viewport activation state."""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from imgui_bundle import imgui

from g import Collider, Spline
from g.components.renderer_utils import _transform_point
from g.persistence import JsonValue, SceneFormatError, scene_types

from .collider_edit import edge_projection, local_point
from .panels.base import EditorPanel
from .ui import color


@dataclass(frozen=True)
class ComponentTool:
    name: str
    label: str
    component_type: type[Collider] | type[Spline]
    help: str


COMPONENT_TOOLS = (
    ComponentTool(
        "collider", "Edit Collider", Collider,
        "Drag gold handles | Shift+click edge: add point | "
        "Cmd/Ctrl+Delete: remove point | Esc: cancel drag / exit",
    ),
    ComponentTool(
        "spline", "Edit Spline", Spline,
        "Drag points | Shift+click curve: add point | "
        "Delete: remove point | Esc: cancel drag / exit",
    ),
)


@dataclass
class _ToolTarget:
    object_id: str
    component_index: int
    record: dict[str, JsonValue]


@dataclass
class _SplineDrag:
    spline: Spline
    point: int
    original: tuple[tuple[float, float], ...]
    revision: int


class ComponentToolsPanel(EditorPanel):
    _viewport_tool: str
    _tool_target: _ToolTarget | None
    _spline_drag: _SplineDrag | None
    _spline_point: int | None

    def _initialize_component_tools(self) -> None:
        self._viewport_tool = "transform"
        self._tool_target = None
        self._spline_drag = None
        self._spline_point = None

    @property
    def _collider_edit_mode(self) -> bool:
        return self._viewport_tool == "collider"

    def _tool_components(self, tool: ComponentTool) -> list[tuple[int, Collider | Spline]]:
        if self.preview.mode != "edit" or len(self.state.selected_records) != 1:
            return []
        owner = self.preview.selected_runtime_object()
        if owner is None:
            return []
        return [
            (index, component) for index, component in enumerate(owner.components)
            if isinstance(component, tool.component_type)
        ]

    def _activate_component_tool(self, name: str, component_index: int | None = None) -> None:
        if name == "transform":
            self._cancel_collider_drag()
            self._cancel_spline_drag()
            self._viewport_tool = name
            self._tool_target = None
            self._collider_point = None
            self._spline_point = None
            return
        tool = next((tool for tool in COMPONENT_TOOLS if tool.name == name), None)
        if tool is None:
            raise ValueError(f"Unknown viewport tool: {name}")
        self.preview.synchronize()
        components = self._tool_components(tool)
        target = next(
            (item for item in components if component_index is None or item[0] == component_index),
            None,
        )
        if target is None or self.state.selected_id is None:
            raise ValueError(f"{tool.label} requires one selected object with a {tool.component_type.__name__}")
        self._activate_component_tool("transform")
        index = target[0]
        _, record, _ = self.state._selected_component(index)
        self._tool_target = _ToolTarget(self.state.selected_id, index, record)
        self._viewport_tool = name
        self._shortcut_context = "viewport"
        if name == "collider":
            self._editable_colliders()
            self._collider_component_index = index

    def _validate_component_tool(self) -> None:
        if self._viewport_tool == "transform":
            return
        tool = next(tool for tool in COMPONENT_TOOLS if tool.name == self._viewport_tool)
        target = self._tool_target
        components = self._tool_components(tool)
        if target is not None and self.state.selected_id == target.object_id:
            record = self.state.selected
            records = record.get("components") if record is not None else None
            if (
                isinstance(records, list)
                and target.component_index < len(records)
                and records[target.component_index] is target.record
                and any(index == target.component_index for index, _ in components)
            ):
                return
        self._activate_component_tool("transform")

    def _component_tool_is_active(self, name: str, index: int) -> bool:
        self._validate_component_tool()
        return (
            self._viewport_tool == name
            and self._tool_target is not None
            and self._tool_target.component_index == index
        )

    def _component_tool_for_type(self, component_type: str) -> str | None:
        value_type = scene_types.component_type_for_name(component_type)
        return next(
            (tool.name for tool in COMPONENT_TOOLS if issubclass(value_type, tool.component_type)),
            None,
        )

    def _component_tool_help(self) -> str:
        return next(
            (tool.help for tool in COMPONENT_TOOLS if tool.name == self._viewport_tool),
            "",
        )

    def _draw_component_tool_selector(self) -> None:
        self._validate_component_tool()
        label = next(
            (tool.label for tool in COMPONENT_TOOLS if tool.name == self._viewport_tool),
            "Transform",
        )
        imgui.set_next_item_width(155)
        if imgui.begin_combo("##viewport-tool", label):
            try:
                clicked, _ = imgui.selectable("Transform", self._viewport_tool == "transform")
                if clicked:
                    self._activate_component_tool("transform")
                for tool in COMPONENT_TOOLS:
                    available = bool(self._tool_components(tool))
                    imgui.begin_disabled(not available)
                    clicked, _ = imgui.selectable(tool.label, self._viewport_tool == tool.name)
                    imgui.end_disabled()
                    if not available and imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
                        imgui.set_tooltip(
                            f"Stop Play and select one object with a {tool.component_type.__name__}."
                        )
                    if clicked:
                        self._activate_component_tool(tool.name)
            finally:
                imgui.end_combo()
        if self._tool_target is None:
            return
        tool = next(tool for tool in COMPONENT_TOOLS if tool.name == self._viewport_tool)
        components = self._tool_components(tool)
        if len(components) > 1:
            imgui.same_line()
            imgui.set_next_item_width(165)
            selected = next(
                i for i, (index, _) in enumerate(components)
                if index == self._tool_target.component_index
            )
            changed, selected = imgui.combo(
                "##component-tool-target", selected,
                [f"{type(component).__name__} ({index + 1})" for index, component in components],
            )
            if changed:
                self._activate_component_tool(tool.name, components[selected][0])

    def _active_spline(self) -> Spline | None:
        target = self._tool_target
        if self._viewport_tool != "spline" or target is None:
            return None
        owner = self.preview.selected_runtime_object()
        if owner is None or owner.scene_id != target.object_id or target.component_index >= len(owner.components):
            return None
        component = owner.components[target.component_index]
        return component if isinstance(component, Spline) else None

    def _draw_spline_overlay(self, minimum: imgui.ImVec2, maximum: imgui.ImVec2) -> None:
        if self.preview.mode != "edit":
            return
        drawing = imgui.get_window_draw_list()

        def screen(point: tuple[float, float]) -> imgui.ImVec2:
            x, y = self.preview.world_to_screen(*point)
            return imgui.ImVec2(
                minimum.x + x * (maximum.x - minimum.x) / self.render_target.width,
                maximum.y - y * (maximum.y - minimum.y) / self.render_target.height,
            )

        for owner in self.preview.selected_runtime_objects():
            for spline in owner.components:
                if not isinstance(spline, Spline):
                    continue
                active = spline is self._active_spline()
                stroke = color(160, 125, 255, 255 if active else 140)
                try:
                    samples = spline.world_curve.sampled_points
                except ValueError as error:
                    self.state.error = str(error)
                    continue
                for a, b in zip(samples, samples[1:]):
                    drawing.add_line(screen(a), screen(b), stroke, 2)
                if active:
                    points = tuple(_transform_point(owner, point) for point in spline.points)
                    polygon = (*points, points[0]) if spline.closed else points
                    for a, b in zip(polygon, polygon[1:]):
                        drawing.add_line(screen(a), screen(b), color(160, 125, 255, 90), 1)
                    for index, point in enumerate(points):
                        drawing.add_circle_filled(
                            screen(point), 7 if index == self._spline_point else 6,
                            color(240, 225, 255) if index == self._spline_point else stroke, 12,
                        )

    def _spline_hit(self, spline: Spline, x: float, y: float) -> tuple[str, int, float] | None:
        if spline.owner is None:
            return None
        points = [
            self.preview.world_to_screen(*_transform_point(spline.owner, point))
            for point in spline.points
        ]
        index, distance = min(
            ((i, hypot(x - point[0], y - point[1])) for i, point in enumerate(points)),
            key=lambda item: item[1],
        )
        if distance <= 16:
            return "point", index, 0.0
        curve = spline.world_curve
        samples = [self.preview.world_to_screen(*point) for point in curve.sampled_points]
        edge, (distance, amount) = min(
            ((i, edge_projection((x, y), a, b)) for i, (a, b) in enumerate(zip(samples, samples[1:]))),
            key=lambda item: item[1][0],
        )
        if distance <= 8:
            parameters = curve.sample_parameters
            parameter = parameters[edge] + amount * (parameters[edge + 1] - parameters[edge])
            segments = len(spline.points) if spline.closed else len(spline.points) - 1
            return "curve", min(int(parameter * segments), segments - 1), parameter
        return None

    def _begin_spline_drag(self, index: int) -> None:
        spline = self._active_spline()
        if spline is None:
            return
        self._spline_point = index
        self._spline_drag = _SplineDrag(spline, index, spline.points, self.state.revision)

    def _cancel_spline_drag(self) -> None:
        if self._spline_drag is not None:
            self._spline_drag = None
            self.preview.reload()

    def _update_spline_drag(self, world: tuple[float, float], *, down: bool) -> None:
        drag = self._spline_drag
        if drag is None:
            return
        if self._active_spline() is not drag.spline or self.state.revision != drag.revision:
            self._cancel_spline_drag()
            return
        if down:
            try:
                assert drag.spline.owner is not None
                points = list(drag.original)
                points[drag.point] = local_point(drag.spline.owner, world)
                drag.spline.points = points
                self.state.error = None
            except ValueError as error:
                self.state.error = str(error)
        else:
            self._spline_drag = None
            if drag.spline.points != drag.original:
                assert self._tool_target is not None
                self.state.set_spline_geometry(
                    self._tool_target.component_index,
                    {"points": [list(point) for point in drag.spline.points]},
                )

    def _handle_spline_click(self, x: float, y: float) -> bool:
        spline = self._active_spline()
        if spline is None:
            return False
        try:
            hit = self._spline_hit(spline, x, y)
            if hit is None:
                self._spline_point = None
                return False
            kind, index, parameter = hit
            if kind == "point":
                self._begin_spline_drag(index)
            elif imgui.get_io().key_shift:
                assert self._tool_target is not None
                point = spline.curve.point_at(parameter)
                points: list[JsonValue] = [list(point) for point in spline.points]
                points.insert(index + 1, list(point))
                self.state.set_spline_geometry(self._tool_target.component_index, {"points": points})
                self._spline_point = index + 1
            return True
        except (ValueError, SceneFormatError) as error:
            self.state.error = str(error)
            return True

    def _delete_spline_point(self) -> None:
        spline = self._active_spline()
        if spline is None or self._spline_point is None:
            return
        if not 0 <= self._spline_point < len(spline.points):
            self._spline_point = None
            return
        minimum = 3 if spline.closed else 2
        if len(spline.points) <= minimum:
            raise ValueError(f"Spline needs at least {minimum} points")
        assert self._tool_target is not None
        points: list[JsonValue] = [
            list(point) for i, point in enumerate(spline.points) if i != self._spline_point
        ]
        self.state.set_spline_geometry(self._tool_target.component_index, {"points": points})
        self._spline_point = None

    def _component_tool_escape(self) -> None:
        if self._collider_drag is not None:
            self._cancel_collider_drag()
        elif self._spline_drag is not None:
            self._cancel_spline_drag()
        else:
            self._activate_component_tool("transform")

    def _refresh_component_tool_after_history(self) -> None:
        target = self._tool_target
        if target is None:
            return
        if (
            self.state.selected_id == target.object_id
            and len(self.state.selected_records) == 1
        ):
            record = self.state.selected
            components = record.get("components") if record is not None else None
            if isinstance(components, list) and target.component_index < len(components):
                component = components[target.component_index]
                if isinstance(component, dict) and component.get("type") == target.record.get("type"):
                    target.record = component
        self._collider_point = None
        self._spline_point = None
        self._validate_component_tool()
