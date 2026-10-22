from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import cast

from g import (
    Animation,
    Image,
    SpriteAnimation,
    SpriteAnimationFrame,
    SpriteRenderer,
    SpriteSheet,
    SpriteSheetGrid,
    SpriteSheetRegion,
)
from g.persistence import JsonValue, SceneFormatError
from g.resources import source_of
from imgui_bundle import icons_fontawesome_6, imgui
from pyglet.image import Texture
from pyglet.image.codecs import ImageDecodeException

from ..panel_support import ASSET_DRAG_PAYLOAD, IMAGE_SUFFIXES
from ..ui import flag, icon_button
from .base import EditorPanel


@dataclass
class AnimationPreview:
    clip: SpriteAnimation | None = None
    elapsed: float = 0.0
    playing: bool = False

    @property
    def frame_index(self) -> int:
        return self.clip.frame_index_at(self.elapsed) if self.clip is not None else 0

    def update(self, delta_time: float) -> None:
        if not isfinite(delta_time) or delta_time < 0:
            raise ValueError("animation preview delta time must be non-negative")
        if not self.playing or self.clip is None:
            return
        self.elapsed += delta_time
        if self.clip.loop:
            self.elapsed %= self.clip.duration
        elif self.elapsed >= self.clip.duration:
            self.elapsed = self.clip.duration
            self.playing = False

    def stop(self) -> None:
        self.playing = False
        self.elapsed = 0.0


class AnimationPanel(EditorPanel):
    def _initialize_animation_panel(self) -> None:
        self._animation_preview = AnimationPreview()
        self._animation_owner: tuple[str, int] | None = None
        self._animation_state_name = ""
        self._animation_frame = 0
        self._animation_zoom = 800.0
        self._animation_clip_key: object = None
        self._animation_error: str | None = None
        self._animation_rect: tuple[float, float, float, float] | None = None
        self._animation_focus_pending = False
        self._animation_resize: tuple[int, float, float] | None = None
        self._animation_resize_duration = 0.1
        self._animation_drag_frame: int | None = None
        self._animation_name_buffer = ""
        self._animation_frame_rects: dict[int, tuple[float, float, float, float]] = {}
        self._animation_frame_textures: list[Texture] = []
        self._animation_viewport_active = False
        self._sprite_sheet_path: Path | None = None
        self._sprite_sheet_cell_width = 32
        self._sprite_sheet_cell_height = 32
        self._sprite_sheet_margin = 0
        self._sprite_sheet_spacing = 0
        self._sprite_sheet_selection: list[int] = []
        self._sprite_sheet_selection_initialized = False

    def _show_animation_panel(self) -> None:
        self._animation_focus_pending = True

    def _selected_animation(self) -> tuple[int, dict[str, JsonValue]] | None:
        record = self.state.selected
        if record is None:
            return None
        components = record.get("components")
        if not isinstance(components, list):
            return None
        for index, component in enumerate(components):
            if isinstance(component, dict) and component.get("type") == "g.sprite_animator":
                properties = component.get("properties")
                if not isinstance(properties, dict):
                    raise SceneFormatError("sprite animator properties must be an object")
                return index, properties
        return None

    @staticmethod
    def _animation_states(properties: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
        states = properties.get("states")
        if not isinstance(states, list) or not states or any(not isinstance(state, dict) for state in states):
            raise SceneFormatError("sprite animator states must be a non-empty list of objects")
        return cast(list[dict[str, JsonValue]], states)

    def _sync_animation_selection(self) -> tuple[int, dict[str, JsonValue], list[dict[str, JsonValue]]] | None:
        selected = self._selected_animation()
        if selected is None:
            if self._rename_target is not None and self._rename_target.kind == "animation-state":
                self._rename_target = None
            self._animation_owner = None
            self._animation_viewport_active = False
            self._animation_preview.stop()
            self._animation_preview.clip = None
            self._animation_clip_key = None
            self._animation_resize = None
            return None
        index, properties = selected
        record = self.state.selected
        assert record is not None
        owner = str(record.get("id")), index
        states = self._animation_states(properties)
        names = [str(state.get("name")) for state in states]
        if owner != self._animation_owner:
            if self._rename_target is not None and self._rename_target.kind == "animation-state":
                self._rename_target = None
            self._animation_owner = owner
            self._animation_state_name = str(properties.get("initial_state") or names[0])
            self._animation_focus_pending = True
            self._reset_animation_preview()
        if self._animation_state_name not in names:
            self._animation_state_name = names[0]
            self._reset_animation_preview()
        return index, properties, states

    def _reset_animation_preview(self) -> None:
        self._animation_viewport_active = False
        self._animation_preview.stop()
        self._animation_clip_key = None
        self._animation_frame = 0
        self._animation_resize = None
        self._animation_drag_frame = None
        self._animation_name_buffer = self._animation_state_name
        if self._asset_picker is not None and self._asset_picker.field_id.startswith(("sprite-animation-frame:", "sprite-animation-state:")):
            self._asset_picker = None

    @contextmanager
    def _animation_viewport_override(self) -> Iterator[None]:
        selected = self._sync_animation_selection()
        if self.preview.mode != "edit":
            self._animation_viewport_active = False
        if self._animation_viewport_active and selected is not None:
            state = next(state for state in selected[2] if state.get("name") == self._animation_state_name)
            self._refresh_animation_clip(state)
        runtime = self.preview.selected_runtime_object()
        renderer = runtime.get_component(SpriteRenderer) if runtime is not None else None
        clip = self._animation_preview.clip
        if not self._animation_viewport_active or selected is None or renderer is None or clip is None:
            yield
            return
        original = renderer.image
        try:
            renderer.image = clip.frames[self._animation_preview.frame_index].image
            yield
        finally:
            renderer.image = original
            renderer.sync()

    def _load_animation_clip(self, state: dict[str, JsonValue]) -> SpriteAnimation:
        root = self.state.asset_root
        if "image" in state:
            path = state["image"]
            if path is None:
                clip = SpriteAnimation.from_image(None)
            elif not isinstance(path, str):
                raise SceneFormatError("animation image must be an asset path or null")
            elif state.get("animated", False):
                durations = state.get("durations")
                if durations is not None and (
                    not isinstance(durations, list)
                    or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in durations)
                ):
                    raise SceneFormatError("animation durations must be a list of numbers")
                clip = SpriteAnimation.from_animation(
                    Animation(root / path),
                    durations=cast(list[float] | None, durations),
                )
            else:
                image = Image(root / path)
                region = state.get("region")
                if region is not None:
                    image = self._sprite_sheet_region(image, region)
                clip = SpriteAnimation.from_image(image)
        else:
            frames = state.get("frames")
            if not isinstance(frames, list) or not frames:
                raise SceneFormatError("animation state must contain frames")
            decoded: list[SpriteAnimationFrame] = []
            for frame in frames:
                if not isinstance(frame, dict):
                    raise SceneFormatError("animation frame must be an object")
                path, duration = frame.get("image"), frame.get("duration")
                if not isinstance(duration, (int, float)) or isinstance(duration, bool):
                    raise SceneFormatError("animation frame duration must be a number")
                if path is not None and not isinstance(path, str):
                    raise SceneFormatError("animation frame image must be an asset path or null")
                image = Image(root / path) if isinstance(path, str) else None
                region = frame.get("region")
                if image is not None and region is not None:
                    image = self._sprite_sheet_region(image, region)
                decoded.append(SpriteAnimationFrame(image, duration))
            clip = SpriteAnimation(tuple(decoded))
        return SpriteAnimation(clip.frames, loop=bool(state.get("loop", clip.loop)), source=clip.source)

    @staticmethod
    def _sprite_sheet_region(
        image: Image,
        region: JsonValue,
    ) -> SpriteSheetRegion:
        if (
            not isinstance(region, list)
            or len(region) != 4
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in region
            )
        ):
            raise SceneFormatError(
                "sprite-sheet region must contain four integers"
            )
        try:
            return SpriteSheetRegion(
                image,
                *cast(list[int], region),
            )
        except (TypeError, ValueError) as error:
            raise SceneFormatError(str(error)) from error

    def _refresh_animation_clip(self, state: dict[str, JsonValue]) -> None:
        paths: list[str] = []
        if isinstance(state.get("image"), str):
            paths.append(cast(str, state["image"]))
        frames = state.get("frames")
        if isinstance(frames, list):
            paths.extend(
                cast(str, frame["image"])
                for frame in frames if isinstance(frame, dict) and isinstance(frame.get("image"), str)
            )
        stamps = tuple(
            (path, (self.state.asset_root / path).stat().st_mtime_ns)
            for path in paths if (self.state.asset_root / path).is_file()
        )
        key = self.state.asset_root, repr(state), stamps
        if key == self._animation_clip_key:
            return
        self._animation_clip_key = key
        previous_error = self._animation_error
        self._animation_error = None
        try:
            clip = self._load_animation_clip(state)
        except (OSError, ValueError, SceneFormatError, ImageDecodeException) as error:
            self._animation_preview.clip = None
            self._animation_preview.stop()
            self._animation_error = f"Could not preview animation: {error}"
            self.state.error = self._animation_error
            return
        self._animation_preview.clip = clip
        if previous_error is not None and self.state.error == previous_error:
            self.state.error = None
        self._animation_preview.elapsed = min(self._animation_preview.elapsed, clip.duration)
        self._animation_frame = min(self._animation_frame, len(clip.frames) - 1)

    def _draw_animation_panel(self) -> None:
        self._animation_rect = None
        self._animation_frame_rects.clear()
        selected = self._sync_animation_selection()
        if self._animation_focus_pending:
            imgui.set_next_window_focus()
            window = imgui.internal.find_window_by_name("Animation")
            if window is not None and window.dock_id != 0:
                self._animation_focus_pending = False
        if self._begin_panel("Animation"):
            if imgui.is_window_focused(flag(imgui.FocusedFlags_.root_and_child_windows)):
                self._shortcut_context = "animation"
            if selected is None:
                imgui.text_disabled("Select a GameObject with Sprite Animator")
            else:
                position, size = imgui.get_window_pos(), imgui.get_window_size()
                if self.preview.mode == "edit":
                    self._animation_rect = position.x, position.y, position.x + size.x, position.y + size.y
                self._draw_animation_contents(*selected)
        imgui.end()

    def _draw_animation_contents(
        self, index: int, properties: dict[str, JsonValue], states: list[dict[str, JsonValue]],
    ) -> None:
        record = self.state.selected
        assert record is not None
        imgui.text(f"{record.get('name', 'GameObject')} / Sprite Animator")
        if self.preview.mode != "edit":
            imgui.text_disabled("Stop Play to edit. This preview never runs gameplay.")
        available = imgui.get_content_region_avail()
        state_index = next(i for i, state in enumerate(states) if state.get("name") == self._animation_state_name)
        state = states[state_index]
        self._refresh_animation_clip(state)
        if imgui.begin_child("##animation-states", imgui.ImVec2(160, available.y), flag(imgui.ChildFlags_.borders)):
            if imgui.begin_child("##animation-state-list", imgui.ImVec2(0, -imgui.get_frame_height_with_spacing())):
                if imgui.is_window_focused():
                    self._shortcut_context = "animation-states"
                for state in states:
                    name = str(state.get("name"))
                    label = f"{name}{' *' if properties.get('initial_state') == name else ''}"
                    if imgui.selectable(f"{label}##animation-state-{name}", name == self._animation_state_name)[0]:
                        playing = self._animation_preview.playing
                        active = self._animation_viewport_active
                        self._animation_state_name = name
                        self._reset_animation_preview()
                        self._animation_preview.playing = playing
                        self._animation_viewport_active = active
                    minimum, maximum = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                    double_clicked = imgui.is_item_hovered() and imgui.is_mouse_double_clicked(0)
                    if self._draw_animation_state_context(index, properties, states, name):
                        break
                    if self.preview.mode == "edit":
                        if double_clicked:
                            self._begin_rename("animation-state", name, name)
                        self._draw_inline_rename(
                            "animation-state", name, name,
                            minimum, maximum,
                        )
            imgui.end_child()
            selected = self._sync_animation_selection()
            assert selected is not None
            index, properties, states = selected
            imgui.begin_disabled(self.preview.mode != "edit")
            if imgui.button("+ State"):
                self._add_animation_state(index, properties, states)
            imgui.same_line()
            imgui.begin_disabled(len(states) == 1)
            if imgui.button("Remove"):
                self._remove_animation_state()
            imgui.end_disabled()
            imgui.end_disabled()
        imgui.end_child()
        selected = self._sync_animation_selection()
        assert selected is not None
        index, properties, states = selected
        state_index = next(i for i, state in enumerate(states) if state.get("name") == self._animation_state_name)
        self._refresh_animation_clip(states[state_index])
        imgui.same_line()
        if imgui.begin_child("##animation-editor", imgui.ImVec2(0, available.y)):
            imgui.begin_disabled(self.preview.mode != "edit")
            settings_changed = self._draw_animation_state_name(index, properties, states, state_index)
            if not settings_changed and imgui.collapsing_header("State Settings##animation"):
                settings_changed = self._draw_animation_state_settings(index, properties, states, state_index)
            if not settings_changed:
                self._draw_animation_timeline(index, states, state_index)
            imgui.end_disabled()
            if not settings_changed:
                spacing = imgui.get_style().item_spacing.x
                width = imgui.get_content_region_avail().x
                preview_width = min(220.0, width * 0.4)
                if imgui.begin_child("##animation-frame-details", imgui.ImVec2(max(1, width - preview_width - spacing), 0)):
                    imgui.begin_disabled(self.preview.mode != "edit")
                    self._draw_animation_frame_settings(index, states, state_index)
                    imgui.end_disabled()
                imgui.end_child()
                imgui.same_line()
                if imgui.begin_child("##animation-preview", imgui.ImVec2(0, 0)):
                    self._draw_animation_transport()
                imgui.end_child()
        imgui.end_child()
        self._draw_sprite_sheet_popup(index, states, state_index)

    def _draw_animation_state_context(
        self, index: int, properties: dict[str, JsonValue], states: list[dict[str, JsonValue]], name: str,
    ) -> bool:
        if not imgui.begin_popup_context_item(f"##animation-state-context-{name}"):
            return False
        changed = False
        try:
            self._shortcut_context = "animation-states"
            if self._animation_state_name != name:
                self._animation_state_name = name
                self._reset_animation_preview()
            editable = self.preview.mode == "edit"
            is_default = properties.get("initial_state") == name
            if imgui.menu_item("Set default", "", is_default, editable and not is_default)[0]:
                self.state.set_sprite_animator_configuration(
                    index, cast(JsonValue, states), initial_state=name,
                    history_label="Set default animation state",
                )
                changed = True
            if imgui.menu_item("Delete", "", False, editable and len(states) > 1)[0]:
                self._remove_animation_state()
                changed = True
        finally:
            imgui.end_popup()
        return changed

    def _add_animation_state(
        self, index: int, properties: dict[str, JsonValue], states: list[dict[str, JsonValue]],
    ) -> None:
        names = {str(state.get("name")) for state in states}
        name, suffix = "animation", 1
        while name in names:
            suffix += 1
            name = f"animation_{suffix}"
        updated = deepcopy(states)
        updated.append({"name": name, "loop": True, "frames": [{"image": None, "duration": 0.1}]})
        self.state.set_sprite_animator_configuration(
            index, cast(JsonValue, updated), initial_state=str(properties.get("initial_state")),
            history_label="Added animation state",
        )
        self._animation_state_name = name
        self._reset_animation_preview()

    def _draw_animation_state_name(
        self, index: int, properties: dict[str, JsonValue], states: list[dict[str, JsonValue]], state_index: int,
    ) -> bool:
        state = states[state_index]
        name = str(state.get("name"))
        imgui.text("Name")
        imgui.same_line()
        imgui.set_next_item_width(-1)
        changed, self._animation_name_buffer = imgui.input_text(
            "##animation-name", self._animation_name_buffer,
            flag(imgui.InputTextFlags_.enter_returns_true),
        )
        new_name = self._animation_name_buffer
        if changed:
            try:
                self._rename_animation_state(name, new_name.strip())
            except ValueError as error:
                self.state.error = str(error)
            else:
                return True
        if imgui.is_item_hovered():
            imgui.set_tooltip("Rename this state; press Enter to confirm")
        return False

    def _rename_animation_state(self, name: str, new_name: str) -> None:
        selected = self._sync_animation_selection()
        if selected is None or self.preview.mode != "edit":
            raise ValueError("select a Sprite Animator in Edit mode to rename a state")
        index, properties, states = selected
        state_index = next((i for i, state in enumerate(states) if state.get("name") == name), None)
        if state_index is None:
            raise ValueError("animation state is unavailable")
        updated = deepcopy(states)
        updated[state_index]["name"] = new_name
        initial = str(properties.get("initial_state"))
        self.state.set_sprite_animator_configuration(
            index, cast(JsonValue, updated),
            initial_state=new_name if initial == name else initial,
            history_label="Renamed animation state",
        )
        self._animation_state_name = new_name
        self._reset_animation_preview()

    def _remove_animation_state(self) -> None:
        selected = self._sync_animation_selection()
        if selected is None or self.preview.mode != "edit":
            raise ValueError("select a Sprite Animator in Edit mode to remove a state")
        index, properties, states = selected
        if len(states) == 1:
            raise ValueError("Sprite Animator must keep at least one state")
        updated = [deepcopy(state) for state in states if state.get("name") != self._animation_state_name]
        initial = str(properties.get("initial_state"))
        if initial == self._animation_state_name:
            initial = str(updated[0].get("name"))
        self.state.set_sprite_animator_configuration(
            index, cast(JsonValue, updated), initial_state=initial,
            history_label="Removed animation state",
        )
        self._animation_state_name = initial
        self._reset_animation_preview()

    def _draw_animation_state_settings(
        self, index: int, properties: dict[str, JsonValue], states: list[dict[str, JsonValue]], state_index: int,
    ) -> bool:
        state = states[state_index]
        name = str(state.get("name"))
        mode = 2 if "frames" in state else 1 if state.get("animated", False) else 0
        changed, selected_mode = self._choice_property(
            "Source", mode, ["Image", "Animation Asset", "Frames"],
            f"animation-source:{index}:{state_index}",
        )
        if changed:
            updated = deepcopy(states)
            image = state.get("image")
            frames = state.get("frames")
            if isinstance(frames, list) and frames and isinstance(frames[0], dict):
                image = frames[0].get("image")
            updated[state_index] = {"name": state["name"], "loop": state.get("loop", True)}
            if selected_mode == 2:
                updated[state_index]["frames"] = [{"image": image, "duration": 0.1}]
            else:
                updated[state_index]["image"] = image
                updated[state_index]["animated"] = selected_mode == 1
            self.state.set_sprite_animator_configuration(
                index, cast(JsonValue, updated), history_label="Switched animation source",
            )
            self._reset_animation_preview()
            return True
        changed, loop = imgui.checkbox("Loop##animation", bool(state.get("loop", True)))
        if changed:
            updated = deepcopy(states)
            updated[state_index]["loop"] = loop
            self.state.set_sprite_animator_configuration(index, cast(JsonValue, updated))
        imgui.same_line()
        if imgui.button("Set Default") and properties.get("initial_state") != name:
            self.state.set_sprite_animator_configuration(index, cast(JsonValue, states), initial_state=name)
        imgui.same_line()
        changed, autoplay = imgui.checkbox("Autoplay", bool(properties.get("autoplay", True)))
        if changed:
            self.state.set_sprite_animator_configuration(index, cast(JsonValue, states), autoplay=autoplay)
        return False

    def _draw_animation_transport(self) -> None:
        preview = self._animation_preview
        clip = preview.clip
        imgui.text("Animation Preview")
        if imgui.is_item_hovered():
            imgui.set_tooltip("Previews here and on the selected sprite in Edit mode; never runs gameplay or changes scene data")
        if self._animation_error is not None:
            imgui.text_wrapped(self._animation_error)
        imgui.begin_disabled(clip is None)
        if icon_button(
            icons_fontawesome_6.ICON_FA_PAUSE if preview.playing else icons_fontawesome_6.ICON_FA_PLAY,
            "animation-playback", "Pause preview" if preview.playing else "Play preview",
            size=imgui.ImVec2(32, 0),
        ):
            if clip is not None and not clip.loop and preview.elapsed >= clip.duration:
                preview.elapsed = 0
            preview.playing = not preview.playing
            self._animation_viewport_active = self.preview.mode == "edit"
        imgui.same_line()
        if icon_button(
            icons_fontawesome_6.ICON_FA_STOP, "animation-stop", "Stop preview",
            size=imgui.ImVec2(32, 0),
        ):
            preview.stop()
            self._animation_viewport_active = False
        if clip is not None:
            imgui.set_next_item_width(-1)
            changed, elapsed = imgui.slider_float("##animation-time", preview.elapsed, 0, clip.duration, "%.3f s")
            if changed:
                preview.elapsed, preview.playing = elapsed, False
                self._animation_viewport_active = self.preview.mode == "edit"
            image = clip.frames[preview.frame_index].image
            if image is not None:
                texture = source_of(image).get_texture()
                assert texture.id is not None
                self._animation_frame_textures.append(texture)
                available = imgui.get_content_region_avail()
                height = max(1, min(90, available.y - imgui.get_text_line_height_with_spacing()))
                scale = min(max(1, available.x) / texture.width, height / texture.height)
                imgui.image(
                    imgui.ImTextureRef(texture.id),
                    imgui.ImVec2(texture.width * scale, texture.height * scale),
                    imgui.ImVec2(0, 1), imgui.ImVec2(1, 0),
                )
            else:
                imgui.text_disabled("Empty frame")
            imgui.text_disabled(f"{preview.frame_index + 1}/{len(clip.frames)} | {clip.duration:.3f} s")
        imgui.end_disabled()

    def _draw_animation_timeline(
        self, index: int, states: list[dict[str, JsonValue]], state_index: int,
    ) -> None:
        clip = self._animation_preview.clip
        if clip is None:
            if imgui.begin_child(
                "##animation-timeline", imgui.ImVec2(0, 120),
                flag(imgui.ChildFlags_.borders),
            ):
                imgui.text_disabled("Drop images or a GIF here")
            imgui.end_child()
            self._accept_animation_asset_drop()
            return
        imgui.text("Zoom")
        imgui.same_line()
        imgui.set_next_item_width(-1)
        _, self._animation_zoom = imgui.slider_float("##animation-zoom", self._animation_zoom, 100, 2000, "%.0f px/s")
        if imgui.begin_child(
            "##animation-timeline", imgui.ImVec2(0, 120),
            flag(imgui.ChildFlags_.borders), flag(imgui.WindowFlags_.horizontal_scrollbar),
        ):
            origin = imgui.get_cursor_screen_pos()
            draw = imgui.get_window_draw_list()
            total_duration = clip.duration
            if self._animation_resize is not None:
                resized_index = self._animation_resize[0]
                total_duration += self._animation_resize_duration - clip.frames[resized_index].duration
            width = max(1.0, total_duration * self._animation_zoom)
            imgui.invisible_button("##animation-ruler", imgui.ImVec2(width, 20))
            if imgui.is_item_active():
                self._animation_preview.elapsed = min(clip.duration, max(0, (imgui.get_mouse_pos().x - origin.x) / self._animation_zoom))
                self._animation_preview.playing = False
                self._animation_viewport_active = self.preview.mode == "edit"
            draw.add_text(origin, imgui.get_color_u32(imgui.Col_.text_disabled), "0 s")
            elapsed = 0.0
            for frame_index, frame in enumerate(clip.frames):
                x = origin.x + elapsed * self._animation_zoom
                duration = (
                    self._animation_resize_duration
                    if self._animation_resize is not None and self._animation_resize[0] == frame_index
                    else frame.duration
                )
                frame_width = duration * self._animation_zoom
                handle_width = min(6.0, frame_width / 4)
                y = origin.y + 22
                self._animation_frame_rects[frame_index] = x, y, x + frame_width, y + 62
                imgui.set_cursor_screen_pos(imgui.ImVec2(x, y))
                imgui.invisible_button(f"##animation-frame-{frame_index}", imgui.ImVec2(frame_width - handle_width, 62))
                if imgui.is_item_clicked():
                    self._animation_frame = frame_index
                    self._animation_preview.elapsed, self._animation_preview.playing = elapsed, False
                    self._animation_viewport_active = self.preview.mode == "edit"
                if imgui.is_item_hovered():
                    imgui.set_tooltip(f"Frame {frame_index + 1}: {duration:.3f} s")
                color = imgui.Col_.header_active if self._animation_frame == frame_index else imgui.Col_.header
                draw.add_rect_filled(imgui.ImVec2(x, y), imgui.ImVec2(x + frame_width, y + 62), imgui.get_color_u32(color), 3)
                if frame.image is not None and frame_width >= 20:
                    texture = source_of(frame.image).get_texture()
                    assert texture.id is not None
                    self._animation_frame_textures.append(texture)
                    size = min(40.0, frame_width - 10)
                    scale = size / max(texture.width, texture.height)
                    draw.add_image(
                        imgui.ImTextureRef(texture.id), imgui.ImVec2(x + 3, y + 3),
                        imgui.ImVec2(x + 3 + texture.width * scale, y + 3 + texture.height * scale),
                        imgui.ImVec2(0, 1), imgui.ImVec2(1, 0),
                    )
                draw.push_clip_rect(imgui.ImVec2(x, y), imgui.ImVec2(x + frame_width - handle_width, y + 62), True)
                draw.add_text(imgui.ImVec2(x + 3, y + 44), imgui.get_color_u32(imgui.Col_.text), f"{duration:.3f}s")
                draw.pop_clip_rect()
                self._animation_frame_drop(index, states, state_index, frame_index)
                imgui.begin_disabled("frames" not in states[state_index] and not states[state_index].get("animated", False))
                imgui.set_cursor_screen_pos(imgui.ImVec2(x + frame_width - handle_width, y))
                imgui.invisible_button(f"##animation-resize-{frame_index}", imgui.ImVec2(handle_width, 62))
                draw.add_rect_filled(imgui.ImVec2(x + frame_width - handle_width, y), imgui.ImVec2(x + frame_width, y + 62), imgui.get_color_u32(imgui.Col_.separator))
                if imgui.is_item_hovered() or imgui.is_item_active():
                    imgui.set_mouse_cursor(imgui.MouseCursor_.resize_ew)
                if imgui.is_item_activated():
                    self._animation_resize = frame_index, imgui.get_mouse_pos().x, frame.duration
                    self._animation_resize_duration = frame.duration
                    self._animation_preview.playing = False
                if imgui.is_item_active() and self._animation_resize is not None:
                    _, start_x, duration = self._animation_resize
                    self._animation_resize_duration = max(0.001, duration + (imgui.get_mouse_pos().x - start_x) / self._animation_zoom)
                    imgui.set_tooltip(f"{self._animation_resize_duration:.3f} s")
                if imgui.is_item_deactivated() and self._animation_resize is not None:
                    self._set_animation_frame_duration(
                        index, states, state_index, frame_index, self._animation_resize_duration,
                        history_label="Resized animation frame",
                    )
                    self._animation_resize = None
                imgui.end_disabled()
                elapsed += duration
            playhead = origin.x + self._animation_preview.elapsed * self._animation_zoom
            draw.add_line(imgui.ImVec2(playhead, origin.y), imgui.ImVec2(playhead, origin.y + 86), imgui.get_color_u32(imgui.Col_.check_mark), 2)
            imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x, origin.y + 88))
            imgui.dummy(imgui.ImVec2(width, 1))
            if all(frame.image is None for frame in clip.frames):
                draw.add_text(
                    imgui.ImVec2(origin.x + 12, origin.y + 30),
                    imgui.get_color_u32(imgui.Col_.text_disabled),
                    "Drop images or a GIF here",
                )
        imgui.end_child()
        self._accept_animation_asset_drop()

    def _accept_animation_asset_drop(self) -> None:
        if imgui.begin_drag_drop_target():
            payload = imgui.accept_drag_drop_payload_py_id(ASSET_DRAG_PAYLOAD)
            if payload is not None:
                try:
                    self._drop_animation_assets(self._asset_drag_paths or ((self._asset_drag_path,) if self._asset_drag_path else ()))
                except (OSError, ValueError, SceneFormatError, ImageDecodeException) as error:
                    self.state.error = f"Could not add animation assets: {error}"
            imgui.end_drag_drop_target()

    def _animation_frame_drop(self, index: int, states: list[dict[str, JsonValue]], state_index: int, frame_index: int) -> None:
        if "frames" not in states[state_index]:
            return
        if imgui.begin_drag_drop_source():
            self._animation_drag_frame = frame_index
            imgui.set_drag_drop_payload_py_id("g-animation-frame", 1)
            imgui.text(f"Frame {frame_index + 1}")
            imgui.end_drag_drop_source()
        if imgui.begin_drag_drop_target():
            payload = imgui.accept_drag_drop_payload_py_id("g-animation-frame")
            if payload is not None and self._animation_drag_frame is not None:
                self._move_animation_frame(index, states, state_index, self._animation_drag_frame, frame_index)
                self._animation_drag_frame = None
            imgui.end_drag_drop_target()

    def _move_animation_frame(self, index: int, states: list[dict[str, JsonValue]], state_index: int, source: int, destination: int) -> None:
        updated = deepcopy(states)
        frames = updated[state_index].get("frames")
        if not isinstance(frames, list) or not 0 <= source < len(frames) or not 0 <= destination < len(frames):
            raise ValueError("animation frame is unavailable")
        frames.insert(destination, frames.pop(source))
        self.state.set_sprite_animator_configuration(index, cast(JsonValue, updated), history_label="Reordered animation frames")
        self._animation_frame = destination

    def _set_animation_frame_duration(
        self, index: int, states: list[dict[str, JsonValue]], state_index: int, frame_index: int, duration: float,
        *, history_label: str = "Changed animation frame duration",
    ) -> None:
        if not isfinite(duration) or duration <= 0:
            raise ValueError("animation frame duration must be positive")
        updated = deepcopy(states)
        state = updated[state_index]
        frames = state.get("frames")
        if isinstance(frames, list):
            frame = frames[frame_index]
            if not isinstance(frame, dict):
                raise SceneFormatError("animation frame must be an object")
            frame["duration"] = duration
        elif state.get("animated", False) and state.get("image"):
            clip = self._load_animation_clip(state)
            durations: list[JsonValue] = [frame.duration for frame in clip.frames]
            durations[frame_index] = duration
            state["durations"] = durations
        else:
            raise ValueError("static image states do not have frame timing")
        self.state.set_sprite_animator_configuration(index, cast(JsonValue, updated), history_label=history_label)

    def _draw_animation_frame_settings(self, index: int, states: list[dict[str, JsonValue]], state_index: int) -> None:
        state = states[state_index]
        clip = self._animation_preview.clip
        frames = state.get("frames")
        if not isinstance(frames, list):
            self._draw_asset_reference_field(
                "Animation" if state.get("animated", False) else "Image",
                str(state.get("image") or ""), f"sprite-animation-state:{index}:{state_index}:-1", kind="image",
            )
            if not state.get("animated", False) or not state.get("image"):
                if imgui.button("Use Frames##animation"):
                    updated = deepcopy(states)
                    updated[state_index] = {"name": state["name"], "loop": state.get("loop", True), "frames": [{"image": state.get("image"), "duration": 0.1}]}
                    self.state.set_sprite_animator_configuration(index, cast(JsonValue, updated))
                if imgui.button(
                    "Add Frames from Sheet...##animation-static"
                ):
                    self._open_sprite_sheet_popup(state)
                return
        else:
            self._animation_frame = min(self._animation_frame, len(frames) - 1)
            frame = frames[self._animation_frame]
            assert isinstance(frame, dict)
            self._draw_asset_reference_field(
                f"Frame {self._animation_frame + 1} Image", str(frame.get("image") or ""),
                f"sprite-animation-frame:{index}:{state_index}:{self._animation_frame}", kind="image",
            )
        if clip is None:
            return
        imgui.text("Duration")
        imgui.same_line()
        imgui.set_next_item_width(110)
        changed, duration = imgui.drag_float("##animation-duration", clip.frames[self._animation_frame].duration, 0.005, 0.001, 60, "%.3f s")
        if changed:
            self._set_animation_frame_duration(index, states, state_index, self._animation_frame, duration)
        if isinstance(frames, list):
            imgui.same_line()
            if imgui.button("Add Frame##animation"):
                updated = deepcopy(states)
                updated_frames = updated[state_index]["frames"]
                assert isinstance(updated_frames, list)
                updated_frames.append({"image": None, "duration": 0.1})
                self.state.set_sprite_animator_configuration(
                    index, cast(JsonValue, updated), history_label="Added animation frame",
                )
            if imgui.button("Add Frames from Sheet...##animation"):
                self._open_sprite_sheet_popup(state)
            imgui.same_line()
            imgui.begin_disabled(len(frames) == 1)
            if imgui.button("Remove Frame##animation"):
                updated = deepcopy(states)
                updated_frames = updated[state_index]["frames"]
                assert isinstance(updated_frames, list)
                updated_frames.pop(self._animation_frame)
                self._animation_frame = max(0, self._animation_frame - 1)
                self.state.set_sprite_animator_configuration(
                    index, cast(JsonValue, updated), history_label="Removed animation frame",
                )
            imgui.end_disabled()
        else:
            imgui.text_disabled("GIF frame order comes from the asset. Timing edits are stored in the scene.")

    def _open_sprite_sheet_popup(
        self,
        state: dict[str, JsonValue],
    ) -> None:
        candidate = self._selected_asset
        if (
            candidate is None
            or candidate.suffix.lower() not in IMAGE_SUFFIXES
            or candidate.suffix.lower() == ".gif"
        ):
            frames = state.get("frames")
            current: JsonValue = None
            if isinstance(frames, list) and frames:
                frame_index = min(self._animation_frame, len(frames) - 1)
                frame = frames[frame_index]
                if isinstance(frame, dict):
                    current = frame.get("image")
            candidate = (
                self.state.asset_root / current
                if isinstance(current, str)
                else None
            )
        self._sprite_sheet_path = candidate
        self._sprite_sheet_selection = []
        self._sprite_sheet_selection_initialized = False
        imgui.open_popup("Add Frames from Sprite Sheet")

    def _draw_sprite_sheet_popup(
        self,
        component_index: int,
        states: list[dict[str, JsonValue]],
        state_index: int,
    ) -> None:
        opened, _ = imgui.begin_popup_modal(
            "Add Frames from Sprite Sheet",
            None,
            flag(imgui.WindowFlags_.always_auto_resize),
        )
        if not opened:
            return
        try:
            selected = self._selected_asset
            can_use_selected = (
                selected is not None
                and selected.suffix.lower() in IMAGE_SUFFIXES
                and selected.suffix.lower() != ".gif"
            )
            imgui.begin_disabled(not can_use_selected)
            if imgui.button("Use selected image asset"):
                self._sprite_sheet_path = selected
                self._sprite_sheet_selection = []
                self._sprite_sheet_selection_initialized = False
            imgui.end_disabled()
            path = self._sprite_sheet_path
            imgui.same_line()
            imgui.text_disabled(
                path.name if path is not None else "Select a PNG/JPG/BMP asset"
            )
            changed_width, self._sprite_sheet_cell_width = imgui.input_int(
                "Cell Width",
                self._sprite_sheet_cell_width,
            )
            changed_height, self._sprite_sheet_cell_height = imgui.input_int(
                "Cell Height",
                self._sprite_sheet_cell_height,
            )
            changed_margin, self._sprite_sheet_margin = imgui.input_int(
                "Margin",
                self._sprite_sheet_margin,
            )
            changed_spacing, self._sprite_sheet_spacing = imgui.input_int(
                "Spacing",
                self._sprite_sheet_spacing,
            )
            grid_changed = (
                changed_width
                or changed_height
                or changed_margin
                or changed_spacing
            )
            grid = None
            error: str | None = None
            if path is not None:
                try:
                    image = Image(path)
                    grid = SpriteSheet(image).grid(
                        self._sprite_sheet_cell_width,
                        self._sprite_sheet_cell_height,
                        margin=self._sprite_sheet_margin,
                        spacing=self._sprite_sheet_spacing,
                    )
                except (OSError, TypeError, ValueError, ImageDecodeException) as caught:
                    error = str(caught)
            if grid is not None:
                if grid_changed or not self._sprite_sheet_selection_initialized:
                    self._sprite_sheet_selection = list(range(len(grid)))
                    self._sprite_sheet_selection_initialized = True
                self._sprite_sheet_selection = [
                    index
                    for index in self._sprite_sheet_selection
                    if index < len(grid)
                ]
                texture = source_of(grid.image).get_texture()
                self._animation_frame_textures.append(texture)
                assert texture.id is not None
                maximum_width, maximum_height = 560.0, 320.0
                scale = min(
                    maximum_width / texture.width,
                    maximum_height / texture.height,
                    1.0,
                )
                size = imgui.ImVec2(
                    texture.width * scale,
                    texture.height * scale,
                )
                imgui.image(
                    imgui.ImTextureRef(texture.id),
                    size,
                    imgui.ImVec2(0, 1),
                    imgui.ImVec2(1, 0),
                )
                minimum = imgui.get_item_rect_min()
                if imgui.is_item_clicked():
                    mouse = imgui.get_mouse_pos()
                    column = int(
                        (mouse.x - minimum.x) / scale
                        - grid.margin
                    ) // (grid.cell_width + grid.spacing)
                    row = int(
                        (mouse.y - minimum.y) / scale
                        - grid.margin
                    ) // (grid.cell_height + grid.spacing)
                    if 0 <= column < grid.columns and 0 <= row < grid.rows:
                        region = grid[column, row]
                        local_x = (mouse.x - minimum.x) / scale
                        local_y = (mouse.y - minimum.y) / scale
                        if (
                            region.x <= local_x < region.x + region.width
                            and region.y <= local_y < region.y + region.height
                        ):
                            cell = row * grid.columns + column
                            if cell in self._sprite_sheet_selection:
                                self._sprite_sheet_selection.remove(cell)
                            else:
                                self._sprite_sheet_selection.append(cell)
                draw = imgui.get_window_draw_list()
                selected_cells = set(self._sprite_sheet_selection)
                for cell in range(len(grid)):
                    region = grid[cell]
                    top_left = imgui.ImVec2(
                        minimum.x + region.x * scale,
                        minimum.y + region.y * scale,
                    )
                    bottom_right = imgui.ImVec2(
                        top_left.x + region.width * scale,
                        top_left.y + region.height * scale,
                    )
                    color = (
                        imgui.get_color_u32(imgui.Col_.plot_histogram)
                        if cell in selected_cells
                        else imgui.get_color_u32(imgui.Col_.border)
                    )
                    draw.add_rect(
                        top_left,
                        bottom_right,
                        color,
                        thickness=2.0 if cell in selected_cells else 1.0,
                    )
                imgui.text(
                    f"{len(self._sprite_sheet_selection)} of "
                    f"{len(grid)} cells selected (selection order is frame order)"
                )
                if imgui.button("Select All"):
                    self._sprite_sheet_selection = list(range(len(grid)))
                imgui.same_line()
                if imgui.button("Clear Selection"):
                    self._sprite_sheet_selection.clear()
            elif error is not None:
                imgui.text_wrapped(error)
            imgui.separator()
            imgui.begin_disabled(
                grid is None or not self._sprite_sheet_selection
            )
            if imgui.button("Add Selected Frames") and grid is not None:
                assert path is not None
                self._add_sprite_sheet_frames(
                    component_index,
                    states,
                    state_index,
                    path,
                    grid,
                )
                imgui.close_current_popup()
            imgui.end_disabled()
            imgui.same_line()
            if imgui.button("Cancel"):
                imgui.close_current_popup()
        except (OSError, ValueError, SceneFormatError) as caught:
            self.state.error = f"Could not slice sprite sheet: {caught}"
        finally:
            imgui.end_popup()

    def _add_sprite_sheet_frames(
        self,
        component_index: int,
        states: list[dict[str, JsonValue]],
        state_index: int,
        path: Path,
        grid: SpriteSheetGrid,
    ) -> None:
        relative = path.resolve().relative_to(
            self.state.asset_root
        ).as_posix()
        updated = deepcopy(states)
        state = updated[state_index]
        frames = state.get("frames")
        if not isinstance(frames, list):
            frames = []
            state.pop("image", None)
            state.pop("animated", None)
            state.pop("durations", None)
            state.pop("region", None)
            state["frames"] = frames
        if (
            len(frames) == 1
            and isinstance(frames[0], dict)
            and frames[0].get("image") is None
        ):
            frames.clear()
        for cell in self._sprite_sheet_selection:
            region = grid[cell]
            frames.append({
                "image": relative,
                "region": [
                    region.x,
                    region.y,
                    region.width,
                    region.height,
                ],
                "duration": 0.1,
            })
        self.state.set_sprite_animator_configuration(
            component_index,
            cast(JsonValue, updated),
            history_label="Added sprite-sheet frames",
        )
        self._reset_animation_preview()

    def _drop_animation_assets(self, paths: tuple[Path, ...]) -> None:
        selected = self._sync_animation_selection()
        if selected is None or self.preview.mode != "edit":
            raise ValueError("select a GameObject with Sprite Animator in Edit mode")
        if not paths or any(path.suffix.lower() not in IMAGE_SUFFIXES for path in paths):
            raise ValueError("drop image files or one GIF")
        if any(path.suffix.lower() == ".gif" for path in paths) and len(paths) != 1:
            raise ValueError("drop a GIF on its own, or select only still images")
        index, _, states = selected
        state_index = next(i for i, state in enumerate(states) if state.get("name") == self._animation_state_name)
        updated = deepcopy(states)
        state = updated[state_index]
        relative_paths = tuple(path.resolve().relative_to(self.state.asset_root).as_posix() for path in paths)
        for path in paths:
            if not path.is_file():
                raise ValueError(f"animation asset does not exist: {path.name}")
        if paths[0].suffix.lower() == ".gif":
            clip = SpriteAnimation.from_animation(Animation(paths[0]))
            updated[state_index] = {"name": state["name"], "loop": clip.loop, "image": relative_paths[0], "animated": True}
        else:
            for path in paths:
                Image(path)
            frames = state.get("frames")
            if not isinstance(frames, list):
                previous = state.get("image")
                frames = cast(list[JsonValue], [{"image": previous, "duration": 0.1}]) if isinstance(previous, str) and not state.get("animated", False) else []
                state.pop("image", None)
                state.pop("animated", None)
                state.pop("durations", None)
                state["frames"] = frames
            if len(frames) == 1 and isinstance(frames[0], dict) and frames[0].get("image") is None:
                frames.clear()
            frames.extend({"image": path, "duration": 0.1} for path in relative_paths)
        self.state.set_sprite_animator_configuration(
            index, cast(JsonValue, updated), history_label="Added animation assets",
        )
        self._reset_animation_preview()
