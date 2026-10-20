from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any, Protocol

import pyglet

from .game import Game
from .camera import Camera
from .math import Vec2
from .nodes import Node
from .scene import ObjectLayer, Scene
from .shader import PostProcess, PostProcessPipeline


@dataclass(frozen=True)
class _Pose:
    position: tuple[float, float]
    rotation: float
    scale: tuple[float, float]
    parent: Node | None
    revision: int

    @classmethod
    def capture(cls, node: Node) -> _Pose:
        return cls(
            (node.x, node.y), node.rotation, (node.scale_x, node.scale_y),
            node.parent, node._interpolation_revision,
        )


def _blend_rotation(previous: float, current: float, alpha: float) -> float:
    delta = (current - previous + 180) % 360 - 180
    return previous + delta * alpha


class RenderInterpolation:
    """Temporarily blends simulation poses for smooth rendering between steps."""

    def __init__(self) -> None:
        """Create interpolation state without an associated scene."""
        self._scene: Scene | None = None
        self._camera: Camera | None = None
        self._previous: dict[Node, _Pose] = {}
        self._current: dict[Node, _Pose] = {}
        self._previous_camera: tuple[float, float, float, float, int] | None = None
        self._current_camera: tuple[float, float, float, float, int] | None = None

    def begin_step(self, game: Game) -> None:
        """Capture the current scene and camera poses before a simulation step."""
        self._scene = game.scenes.current
        self._previous = {node: _Pose.capture(node) for node in self._scene.walk()}
        camera = self._scene.camera
        self._camera = camera
        self._previous_camera = camera.x, camera.y, camera.rotation, camera.zoom, camera._interpolation_revision

    def end_step(self, game: Game) -> None:
        """Capture post-update poses; discard interpolation if the scene changed."""
        scene = game.scenes.current
        if scene is not self._scene:
            self._scene = None
            self._previous.clear()
            self._current.clear()
            return
        self._current = {node: _Pose.capture(node) for node in scene.walk()}
        camera = scene.camera
        if camera is not self._camera:
            self._previous_camera = None
        self._current_camera = camera.x, camera.y, camera.rotation, camera.zoom, camera._interpolation_revision

    @contextmanager
    def apply(self, game: Game, alpha: float) -> Iterator[None]:
        """Temporarily apply interpolated poses, restoring simulation state after.

        Args:
            game (Game): Game whose current scene is rendered.
            alpha (float): Fractional progress between captured fixed steps.

        Yields:
            Control while interpolated transforms are applied.
        """
        scene = game.scenes.current
        if scene is not self._scene:
            yield
            return
        # Readiness callbacks must run on simulation transforms, not blended ones.
        for layer in scene.layers:
            if isinstance(layer, ObjectLayer) and layer.visible:
                layer._prepare_render_structure()
        saved: list[tuple[Node, Vec2, float, float, float]] = []
        camera = scene.camera
        camera_state = camera.position, camera.rotation, camera.zoom
        try:
            for node in scene.walk():
                previous = self._previous.get(node)
                current = self._current.get(node)
                if (
                    previous is None or current is None or previous == current
                    or previous.parent is not current.parent
                    or previous.revision != current.revision
                    or _Pose.capture(node) != current
                ):
                    continue
                saved.append((node, node.position, node.rotation, node.scale_x, node.scale_y))
                node.position = (
                    previous.position[0] + (current.position[0] - previous.position[0]) * alpha,
                    previous.position[1] + (current.position[1] - previous.position[1]) * alpha,
                )
                node.rotation = _blend_rotation(previous.rotation, current.rotation, alpha)
                node.scale_x = previous.scale[0] + (current.scale[0] - previous.scale[0]) * alpha
                node.scale_y = previous.scale[1] + (current.scale[1] - previous.scale[1]) * alpha
            previous_camera, current_camera = self._previous_camera, self._current_camera
            if (
                previous_camera is not None and current_camera is not None
                and camera is self._camera
                and previous_camera[-1] == current_camera[-1]
                and (camera.x, camera.y, camera.rotation, camera.zoom, camera._interpolation_revision) == current_camera
            ):
                x, y, _, zoom = (
                    previous + (current - previous) * alpha
                    for previous, current in zip(previous_camera[:4], current_camera[:4])
                )
                camera.position = x, y
                camera.rotation = _blend_rotation(previous_camera[2], current_camera[2], alpha)
                camera.zoom = zoom
            yield
        finally:
            for node, position, rotation, scale_x, scale_y in saved:
                node._position = position
                node.rotation, node.scale_x, node.scale_y = rotation, scale_x, scale_y
            camera._position, camera.rotation, camera.zoom = camera_state


class RenderWindow(Protocol):
    """Window surface interface required by :class:`SceneRenderer`."""

    @property
    def view(self) -> Any:
        """Current view matrix."""
        ...

    @view.setter
    def view(self, matrix: Any) -> None:
        """Set the current view matrix."""
        ...

    def get_framebuffer_size(self) -> tuple[int, int]:
        """Return framebuffer dimensions in pixels."""
        ...


class SceneRenderer:
    """Draws the active scene, UI, post-processing, and transition overlay."""

    def render(self, game: Game, window: RenderWindow) -> None:
        """Render one frame and restore the window view and viewport afterward.

        The scene background and world use the camera transform; UI is rendered
        without that transform. Enabled post-processing wraps world rendering.
        """
        previous_view = window.view
        previous_viewport: tuple[int, int, int, int] | None = None
        framebuffer_size: tuple[int, int] | None = None
        try:
            scene = game.scenes.current
            effect = scene.post_process
            if not isinstance(effect, (PostProcess, PostProcessPipeline)):
                effect = None
            if effect is not None and effect.enabled:
                viewport = (pyglet.gl.GLint * 4)()
                pyglet.gl.glGetIntegerv(
                    pyglet.gl.GL_VIEWPORT,
                    viewport,
                )
                previous_viewport = tuple(viewport)
                framebuffer_size = window.get_framebuffer_size()
                width, height = framebuffer_size
                effect.begin(width, height)
            try:
                window.view = previous_view
                scene._draw_background()
                window.view = scene.camera.view_matrix
                scene._draw()
                window.view = previous_view
                scene._draw_ui()
            finally:
                if effect is not None and effect.enabled:
                    effect.end()
            if effect is not None and effect.enabled:
                if framebuffer_size is None:
                    raise RuntimeError(
                        "post-process framebuffer size is unavailable"
                    )
                width, height = framebuffer_size
                window.view = previous_view
                effect.draw(width, height)
            game._draw_transition()
        finally:
            window.view = previous_view
            if previous_viewport is not None:
                pyglet.gl.glViewport(*previous_viewport)
