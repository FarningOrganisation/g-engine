"""Compatibility entry point for the standalone scene editor."""

from g_editor import (
    main,
    new_object_record,
    new_scene_document,
    run_editor,
    walk_object_records,
)

__all__ = [
    "main",
    "new_object_record",
    "new_scene_document",
    "run_editor",
    "walk_object_records",
]


if __name__ == "__main__":
    raise SystemExit(main())
