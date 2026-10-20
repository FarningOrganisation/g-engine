"""ImGui scene editor for the g game engine."""

from pathlib import Path

from .state import (
    new_object_record,
    new_scene_document,
    walk_object_records,
)


def run_editor(
    filename: str | Path | None = None,
    *,
    asset_root: str | Path | None = None,
    modules: tuple[str, ...] = (),
) -> int:
    from .application import run_editor as start_editor

    return start_editor(
        filename,
        asset_root=asset_root,
        modules=modules,
    )


def main(argv: list[str] | None = None) -> int:
    from .application import main as application_main

    return application_main(argv)


__all__ = [
    "main",
    "new_object_record",
    "new_scene_document",
    "run_editor",
    "walk_object_records",
]
