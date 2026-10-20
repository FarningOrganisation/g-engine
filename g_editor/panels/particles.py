from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from g.particles import (
    FloatRange,
    ParticleEffectDefinition,
    ParticleEmitterDefinition,
    ParticleBurst,
    ParticleFormatError,
    ParticlePlayer,
    ParticleCircle,
    ParticleLine,
    ParticlePoint,
    ParticleRectangle,
)
from imgui_bundle import imgui

from ..ui import color, flag
from .base import EditorPanel


class ParticlesPanel(EditorPanel):
    def _initialize_particles_panel(self) -> None:
        self._particle_path: Path | None = None
        self._particle_definition: ParticleEffectDefinition | None = None
        self._particle_player: ParticlePlayer | None = None
        self._particle_emitter = ""
        self._particle_focus_pending = False
        self._particle_dirty = False
        self._particle_error: str | None = None
        self._particle_zoom = 1.0
        self._particle_name_buffer = ""

    def _open_particle_asset(self, path: Path) -> None:
        path = path.resolve()
        path.relative_to(self.state.asset_root.resolve())
        if (
            self._particle_dirty
            and self._particle_path is not None
            and path != self._particle_path
        ):
            self.state.error = (
                f"Save {self._particle_path.name} before opening another "
                "particle effect"
            )
            self._particle_focus_pending = True
            return
        try:
            definition = ParticleEffectDefinition.read(path)
        except ParticleFormatError as error:
            self._particle_error = str(error)
            self.state.error = str(error)
            self._particle_focus_pending = True
            return
        self._particle_path = path
        self._particle_definition = definition
        self._particle_emitter = definition.emitters[0].name
        self._particle_name_buffer = self._particle_emitter
        self._particle_player = ParticlePlayer(definition, seed=0)
        self._particle_dirty = False
        self._particle_error = None
        self._particle_focus_pending = True
        self.state.error = None

    def _show_particles_panel(self) -> None:
        record = self.state.selected
        if record is not None:
            components = record.get("components")
            if isinstance(components, list):
                for component in components:
                    if not isinstance(component, dict):
                        continue
                    properties = component.get("properties")
                    if (
                        component.get("type") == "g.particle_effect"
                        and isinstance(properties, dict)
                    ):
                        effect = properties.get("effect")
                        if isinstance(effect, str) and effect:
                            self._open_particle_asset(
                                self.state.asset_root / effect
                            )
                            return
        self._particle_focus_pending = True

    def _update_particles_preview(self, delta_time: float) -> None:
        if self._particle_player is not None:
            self._particle_player.update(delta_time)

    def _draw_particles_panel(self) -> None:
        if self._particle_focus_pending:
            imgui.set_next_window_focus()
            window = imgui.internal.find_window_by_name("Particles")
            if window is not None and window.dock_id != 0:
                self._particle_focus_pending = False
        if self._begin_panel("Particles"):
            if imgui.is_window_focused(
                flag(imgui.FocusedFlags_.root_and_child_windows)
            ):
                self._shortcut_context = "particles"
            if self._particle_definition is None:
                imgui.text_disabled(
                    "Open or create a .particle.json asset to begin"
                )
                if self._particle_error:
                    imgui.text_colored(
                        imgui.ImVec4(1, 0.35, 0.3, 1),
                        self._particle_error,
                    )
            else:
                self._draw_particle_contents()
        imgui.end()

    def _draw_particle_contents(self) -> None:
        definition = self._particle_definition
        player = self._particle_player
        assert definition is not None and player is not None
        name = self._particle_path.name if self._particle_path else "Particle Effect"
        imgui.text(f"{name}{' *' if self._particle_dirty else ''}")
        imgui.same_line()
        if imgui.button("Save##particle-save"):
            self._save_particle_asset()
        imgui.separator()
        available = imgui.get_content_region_avail()
        if imgui.begin_child(
            "##particle-emitters",
            imgui.ImVec2(170, available.y),
            flag(imgui.ChildFlags_.borders),
        ):
            for emitter in definition.emitters:
                if imgui.selectable(
                    f"{emitter.name}##particle-{emitter.name}",
                    emitter.name == self._particle_emitter,
                )[0]:
                    self._particle_emitter = emitter.name
                    self._particle_name_buffer = emitter.name
            imgui.separator()
            if imgui.button("+ Emitter"):
                self._add_particle_emitter()
            imgui.same_line()
            imgui.begin_disabled(len(definition.emitters) == 1)
            if imgui.button("-##remove-particle-emitter"):
                self._remove_particle_emitter()
            imgui.end_disabled()
        imgui.end_child()
        imgui.same_line()
        if imgui.begin_child("##particle-properties", imgui.ImVec2(300, available.y)):
            self._draw_particle_emitter_properties()
        imgui.end_child()
        imgui.same_line()
        if imgui.begin_child(
            "##particle-preview",
            imgui.ImVec2(0, available.y),
            flag(imgui.ChildFlags_.borders),
        ):
            self._draw_particle_preview()
        imgui.end_child()

    def _draw_particle_emitter_properties(self) -> None:
        emitter = self._selected_particle_emitter()
        changed = False
        imgui.separator_text("Emission")
        changed_name, name = imgui.input_text(
            "Name##particle",
            self._particle_name_buffer,
        )
        if changed_name:
            self._particle_name_buffer = name
            names = {
                item.name for item in self._particle_definition.emitters
                if item.name != emitter.name
            } if self._particle_definition is not None else set()
            if name.strip() and name not in names:
                previous_name = emitter.name
                emitter = replace(emitter, name=name)
                self._particle_emitter = name
                self._replace_particle_emitter(
                    emitter,
                    previous_name=previous_name,
                )
                emitter = self._selected_particle_emitter()
                changed = False
                self.state.error = None
            elif name in names:
                self.state.error = f"Emitter name already exists: {name}"
        changed_enabled, enabled = imgui.checkbox(
            "Enabled##particle", emitter.enabled
        )
        if changed_enabled:
            emitter = replace(emitter, enabled=enabled)
            changed = True
        changed_rate, rate = imgui.drag_float(
            "Rate##particle", emitter.rate, 0.5, 0, 10_000
        )
        if changed_rate:
            emitter = replace(emitter, rate=max(0.0, rate))
            changed = True
        changed_capacity, capacity = imgui.drag_int(
            "Capacity##particle", emitter.capacity, 10, 1, 100_000
        )
        if changed_capacity:
            emitter = replace(emitter, capacity=max(1, capacity))
            changed = True
        changed_loop, loop = imgui.checkbox("Loop##particle", emitter.loop)
        if changed_loop:
            emitter = replace(emitter, loop=loop)
            changed = True
        finite = emitter.duration is not None
        changed_finite, finite_value = imgui.checkbox(
            "Finite duration##particle", finite
        )
        if changed_finite:
            emitter = replace(
                emitter,
                duration=1.0 if finite_value else None,
            )
            changed = True
        if emitter.duration is not None:
            changed_duration, duration = imgui.drag_float(
                "Duration##particle",
                emitter.duration,
                0.05,
                0.01,
                600,
            )
            if changed_duration:
                emitter = replace(emitter, duration=max(0.01, duration))
                changed = True
        changed_warmup, warmup = imgui.drag_float(
            "Warmup##particle",
            emitter.warmup,
            0.05,
            0,
            60,
        )
        if changed_warmup:
            emitter = replace(emitter, warmup=max(0, warmup))
            changed = True
        imgui.text_disabled("Bursts")
        updated_bursts = list(emitter.bursts)
        remove_burst: int | None = None
        for index, burst in enumerate(emitter.bursts):
            imgui.push_id(f"particle-burst-{index}")
            changed_time, burst_time = imgui.drag_float(
                "Time", burst.time, 0.05, 0, 600
            )
            imgui.same_line()
            changed_count, burst_count = imgui.drag_int(
                "Count", burst.count, 1, 1, 100_000
            )
            imgui.same_line()
            if imgui.button("-"):
                remove_burst = index
            if changed_time or changed_count:
                updated_bursts[index] = ParticleBurst(
                    max(0, burst_time),
                    max(1, burst_count),
                )
                updated_bursts.sort(key=lambda item: item.time)
                changed = True
            imgui.pop_id()
        if remove_burst is not None:
            del updated_bursts[remove_burst]
            changed = True
        if imgui.button("+ Burst"):
            updated_bursts.append(ParticleBurst(0, 10))
            updated_bursts.sort(key=lambda item: item.time)
            changed = True
        if tuple(updated_bursts) != emitter.bursts:
            emitter = replace(emitter, bursts=tuple(updated_bursts))
        imgui.separator_text("Spawn")
        changed_lifetime, lifetime = imgui.drag_float2(
            "Lifetime##particle",
            [emitter.lifetime.minimum, emitter.lifetime.maximum],
            0.05,
            0.01,
            120,
        )
        if changed_lifetime:
            emitter = replace(
                emitter,
                lifetime=FloatRange(
                    max(0.01, min(lifetime)),
                    max(0.01, max(lifetime)),
                ),
            )
            changed = True
        changed_speed, speed = imgui.drag_float2(
            "Speed##particle",
            [emitter.speed.minimum, emitter.speed.maximum],
            1,
            -10_000,
            10_000,
        )
        if changed_speed:
            emitter = replace(
                emitter,
                speed=FloatRange(min(speed), max(speed)),
            )
            changed = True
        changed_direction, direction = imgui.drag_float(
            "Direction##particle", emitter.direction, 1
        )
        if changed_direction:
            emitter = replace(emitter, direction=direction)
            changed = True
        changed_spread, spread = imgui.slider_float(
            "Spread##particle", emitter.spread, 0, 360
        )
        if changed_spread:
            emitter = replace(emitter, spread=spread)
            changed = True
        shape_types = ["Point", "Line", "Rectangle", "Circle"]
        shape_index = (
            1 if isinstance(emitter.shape, ParticleLine)
            else 2 if isinstance(emitter.shape, ParticleRectangle)
            else 3 if isinstance(emitter.shape, ParticleCircle)
            else 0
        )
        changed_shape, shape_index = imgui.combo(
            "Shape##particle",
            shape_index,
            shape_types,
        )
        if changed_shape:
            emitter = replace(
                emitter,
                shape=(
                    ParticlePoint()
                    if shape_index == 0
                    else ParticleLine()
                    if shape_index == 1
                    else ParticleRectangle(32, 32)
                    if shape_index == 2
                    else ParticleCircle(16)
                ),
            )
            changed = True
        if isinstance(emitter.shape, ParticleCircle):
            changed_radius, radius = imgui.drag_float(
                "Radius##particle", emitter.shape.radius, 0.5, 0, 10_000
            )
            if changed_radius:
                emitter = replace(
                    emitter,
                    shape=replace(emitter.shape, radius=max(0, radius)),
                )
                changed = True
        elif isinstance(emitter.shape, ParticleRectangle):
            changed_dimensions, dimensions = imgui.drag_float2(
                "Dimensions##particle",
                [emitter.shape.width, emitter.shape.height],
                0.5,
                0,
                10_000,
            )
            if changed_dimensions:
                emitter = replace(
                    emitter,
                    shape=replace(
                        emitter.shape,
                        width=max(0, dimensions[0]),
                        height=max(0, dimensions[1]),
                    ),
                )
                changed = True
        imgui.separator_text("Motion")
        gravity = emitter.motion.gravity
        changed_gravity, gravity_value = imgui.drag_float2(
            "Gravity##particle", list(gravity), 1, -10_000, 10_000
        )
        if changed_gravity:
            emitter = replace(
                emitter,
                motion=replace(
                    emitter.motion,
                    gravity=(gravity_value[0], gravity_value[1]),
                ),
            )
            changed = True
        changed_drag, drag = imgui.drag_float(
            "Drag##particle",
            emitter.motion.linear_drag,
            0.01,
            0,
            100,
        )
        if changed_drag:
            emitter = replace(
                emitter,
                motion=replace(
                    emitter.motion,
                    linear_drag=max(0, drag),
                ),
            )
            changed = True
        imgui.separator_text("Appearance")
        self._draw_asset_reference_field(
            "Texture",
            emitter.appearance.image or "",
            f"particle-image:{emitter.name}",
            kind="image",
        )
        changed_size, size = imgui.drag_float2(
            "Start size##particle",
            [emitter.appearance.size.minimum, emitter.appearance.size.maximum],
            0.5,
            0,
            10_000,
        )
        if changed_size:
            emitter = replace(
                emitter,
                appearance=replace(
                    emitter.appearance,
                    size=FloatRange(min(size), max(size)),
                ),
            )
            changed = True
        changed_end_size, end_size = imgui.drag_float2(
            "End size##particle",
            [
                emitter.appearance.end_size.minimum,
                emitter.appearance.end_size.maximum,
            ],
            0.5,
            0,
            10_000,
        )
        if changed_end_size:
            emitter = replace(
                emitter,
                appearance=replace(
                    emitter.appearance,
                    end_size=FloatRange(min(end_size), max(end_size)),
                ),
            )
            changed = True
        start_rgba = [channel / 255 for channel in emitter.appearance.color]
        changed_color, start_rgba = imgui.color_edit4(
            "Start color##particle", start_rgba
        )
        if changed_color:
            emitter = replace(
                emitter,
                appearance=replace(
                    emitter.appearance,
                    color=tuple(
                        round(channel * 255) for channel in start_rgba
                    ),
                ),
            )
            changed = True
        end_rgba = [
            channel / 255 for channel in emitter.appearance.end_color
        ]
        changed_end_color, end_rgba = imgui.color_edit4(
            "End color##particle", end_rgba
        )
        if changed_end_color:
            emitter = replace(
                emitter,
                appearance=replace(
                    emitter.appearance,
                    end_color=tuple(
                        round(channel * 255) for channel in end_rgba
                    ),
                ),
            )
            changed = True
        blend_modes = ["alpha", "additive"]
        blend_index = blend_modes.index(emitter.appearance.blend_mode)
        changed_blend, blend_index = imgui.combo(
            "Blend##particle",
            blend_index,
            blend_modes,
        )
        if changed_blend:
            emitter = replace(
                emitter,
                appearance=replace(
                    emitter.appearance,
                    blend_mode=blend_modes[blend_index],
                ),
            )
            changed = True
        if changed:
            self._replace_particle_emitter(emitter)

    def _draw_particle_preview(self) -> None:
        player = self._particle_player
        assert player is not None
        if imgui.button(
            "Pause" if player.playing and not player.paused else "Play"
        ):
            if player.playing:
                player.pause() if not player.paused else player.resume()
            else:
                player.play(restart=True)
        imgui.same_line()
        if imgui.button("Restart"):
            player.play(restart=True)
        imgui.same_line()
        if imgui.button("Stop"):
            player.stop(clear=True)
        imgui.same_line()
        if imgui.button("Step"):
            player.update(1 / 60)
        imgui.same_line()
        imgui.text_disabled(
            f"{player.particle_count} alive / {player.dropped_count} dropped"
        )
        size = imgui.get_content_region_avail()
        size.x = max(1, size.x)
        size.y = max(1, size.y)
        origin = imgui.get_cursor_screen_pos()
        imgui.invisible_button("##particle-canvas", size)
        draw = imgui.get_window_draw_list()
        maximum = imgui.ImVec2(origin.x + size.x, origin.y + size.y)
        draw.add_rect_filled(origin, maximum, color(14, 16, 21), 4)
        center_x, center_y = origin.x + size.x / 2, origin.y + size.y / 2
        draw.add_line(
            imgui.ImVec2(origin.x, center_y),
            imgui.ImVec2(maximum.x, center_y),
            color(120, 130, 145, 45),
        )
        draw.add_line(
            imgui.ImVec2(center_x, origin.y),
            imgui.ImVec2(center_x, maximum.y),
            color(120, 130, 145, 45),
        )
        assert self._particle_definition is not None
        definitions = {
            emitter.name: emitter
            for emitter in self._particle_definition.emitters
        }
        for emitter_name, particles in player.snapshots().items():
            image = definitions[emitter_name].appearance.image
            texture = None
            if image is not None:
                try:
                    texture = self._atlas_texture(image)
                except (OSError, ValueError) as error:
                    self._particle_error = (
                        f"Could not preview particle texture: {error}"
                    )
            for particle in particles:
                x = center_x + particle.position[0] * self._particle_zoom
                y = center_y - particle.position[1] * self._particle_zoom
                radius = max(1.0, particle.size * self._particle_zoom / 2)
                if (
                    origin.x - radius <= x <= maximum.x + radius
                    and origin.y - radius <= y <= maximum.y + radius
                ):
                    tint = color(*particle.color)
                    if texture is not None and texture.id is not None:
                        draw.add_image(
                            imgui.ImTextureRef(texture.id),
                            imgui.ImVec2(x - radius, y - radius),
                            imgui.ImVec2(x + radius, y + radius),
                            col=tint,
                        )
                    else:
                        draw.add_circle_filled(
                            imgui.ImVec2(x, y),
                            radius,
                            tint,
                        )

    def _selected_particle_emitter(self) -> ParticleEmitterDefinition:
        assert self._particle_definition is not None
        return self._particle_definition.emitter(self._particle_emitter)

    def _set_particle_image(self, path: str | None) -> None:
        emitter = self._selected_particle_emitter()
        self._replace_particle_emitter(
            replace(
                emitter,
                appearance=replace(emitter.appearance, image=path),
            )
        )

    def _replace_particle_emitter(
        self,
        replacement: ParticleEmitterDefinition,
        *,
        previous_name: str | None = None,
    ) -> None:
        assert self._particle_definition is not None
        emitters = tuple(
            replacement
            if item.name == (previous_name or self._particle_emitter)
            else item
            for item in self._particle_definition.emitters
        )
        self._particle_definition = ParticleEffectDefinition(emitters)
        was_playing = bool(
            self._particle_player and self._particle_player.playing
        )
        self._particle_player = ParticlePlayer(self._particle_definition)
        if was_playing:
            player = self._particle_player
            player.play()
        self._particle_dirty = True

    def _add_particle_emitter(self) -> None:
        assert self._particle_definition is not None
        names = {item.name for item in self._particle_definition.emitters}
        index = 1
        while (name := f"Emitter {index}") in names:
            index += 1
        self._particle_definition = ParticleEffectDefinition(
            (*self._particle_definition.emitters, ParticleEmitterDefinition(name=name))
        )
        self._particle_emitter = name
        self._particle_name_buffer = name
        self._particle_player = ParticlePlayer(self._particle_definition)
        self._particle_dirty = True

    def _remove_particle_emitter(self) -> None:
        assert self._particle_definition is not None
        emitters = tuple(
            item for item in self._particle_definition.emitters
            if item.name != self._particle_emitter
        )
        if not emitters:
            return
        self._particle_definition = ParticleEffectDefinition(emitters)
        self._particle_emitter = emitters[0].name
        self._particle_name_buffer = self._particle_emitter
        self._particle_player = ParticlePlayer(self._particle_definition)
        self._particle_dirty = True

    def _save_particle_asset(self) -> None:
        if self._particle_path is None or self._particle_definition is None:
            return
        try:
            self._particle_definition.write(self._particle_path)
        except ParticleFormatError as error:
            self._particle_error = str(error)
            self.state.error = str(error)
            return
        self._particle_dirty = False
        self._particle_error = None
        self.state.error = None
        self.state.status = f"Saved particle effect {self._particle_path.name}"
