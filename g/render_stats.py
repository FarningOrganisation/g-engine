from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RenderStats:
    """Statistics captured while rendering the most recent frame."""

    submitted_renderers: int = 0
    culled_renderers: int = 0
    submitted_vertices: int = 0
    batch_draws: int = 0
    approximate_draw_calls: int = 0


class _RenderStatsBuilder:
    """Mutable per-frame statistics accumulator used by render passes."""

    def __init__(self) -> None:
        self.submitted_renderers = 0
        self.culled_renderers = 0
        self.submitted_vertices = 0
        self.batch_draws = 0
        self.approximate_draw_calls = 0

    def record_renderer(self, *, culled: bool) -> None:
        if culled:
            self.culled_renderers += 1
        else:
            self.submitted_renderers += 1

    def record_batch(self, batch: Any) -> None:
        """Record visible Pyglet vertex domains in one batch submission."""
        self.batch_draws += 1

        def visit(group: Any) -> None:
            for domain in batch.group_map.get(group, {}).values():
                if domain.is_empty:
                    continue
                self.approximate_draw_calls += 1
                _, sizes = domain.allocator.get_allocated_regions()
                self.submitted_vertices += sum(sizes)
            for child in batch.group_children.get(group, ()):
                if child.visible:
                    visit(child)

        for group in batch.top_groups:
            if group.visible:
                visit(group)

    def finish(self) -> RenderStats:
        return RenderStats(
            submitted_renderers=self.submitted_renderers,
            culled_renderers=self.culled_renderers,
            submitted_vertices=self.submitted_vertices,
            batch_draws=self.batch_draws,
            approximate_draw_calls=self.approximate_draw_calls,
        )
