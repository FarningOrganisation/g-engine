"""Find places that still refer to a project asset before it is deleted."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
import json
import os
from pathlib import Path

from g.persistence import JsonValue, SceneDocument
from g.project import ProjectConfig

_MAX_SCANNED_FILE_SIZE = 16 * 1024 * 1024


def find_asset_references(
    asset_root: Path,
    target: Path,
    *,
    document: SceneDocument | None = None,
    document_path: Path | None = None,
    project: ProjectConfig | None = None,
) -> tuple[str, ...]:
    """Describe every reference to ``target`` (a file or folder).

    The open ``document`` is inspected in memory (it may be unsaved) and
    takes the place of ``document_path`` on disk. Every other JSON file
    below ``asset_root`` (scenes, prefabs, the project manifest, ...) is
    scanned for string values naming the target or anything inside it.
    """
    root = asset_root.resolve()
    target = target.resolve()
    relative = target.relative_to(root).as_posix()
    open_path = document_path.resolve() if document_path else None
    references: list[str] = []
    if document is not None:
        label = (
            f"{open_path.relative_to(root).as_posix()} (open scene)"
            if open_path is not None and open_path.is_relative_to(root)
            else "Open scene"
        )
        references.extend(
            f"{label}: {location}"
            for location in _document_references(document, relative)
        )
    for path in _json_files(root):
        if path == open_path or path.is_relative_to(target):
            continue
        try:
            if path.stat().st_size > _MAX_SCANNED_FILE_SIZE:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        count = sum(1 for _ in _matching_strings(data, relative))
        if count:
            plural = "s" if count != 1 else ""
            references.append(
                f"{path.relative_to(root).as_posix()} "
                f"({count} reference{plural})"
            )
    if project is not None:
        for module in project.script_modules:
            base = project.root.joinpath(*module.split("."))
            sources = (base.with_suffix(".py"), base / "__init__.py")
            if any(
                source.exists()
                and (
                    source.resolve() == target
                    or source.resolve().is_relative_to(target)
                )
                for source in sources
            ):
                references.append(
                    f"{project.path.name}: registered script {module}"
                )
    return tuple(references)


def _document_references(
    document: SceneDocument,
    relative: str,
) -> Iterator[str]:
    for layer in document.layers:
        layer_name = str(layer.get("name") or "Layer")
        for tileset in _list(layer.get("tilesets")):
            if isinstance(tileset, dict) and _count(tileset, relative):
                name = str(tileset.get("name") or "Tileset")
                yield f"{layer_name} > tileset {name}"
        for record in _list(layer.get("objects")):
            if isinstance(record, dict):
                yield from _record_references(record, relative)
        others = {
            key: value
            for key, value in layer.items()
            if key not in {"objects", "tilesets"}
        }
        if _count(others, relative):
            yield f"layer {layer_name}"
    for view in document.ui_views:
        if _count(view, relative):
            yield f"UI view {view.get('name') or 'View'}"
    if _count(document.settings, relative):
        yield "scene settings"


def _record_references(
    record: dict[str, JsonValue],
    relative: str,
) -> Iterator[str]:
    own = {key: value for key, value in record.items() if key != "children"}
    if _count(own, relative):
        yield str(record.get("name") or record.get("type") or "Object")
    for child in _list(record.get("children")):
        if isinstance(child, dict):
            yield from _record_references(child, relative)


def _json_files(root: Path) -> Iterator[Path]:
    for current, directory_names, file_names in os.walk(root):
        directory_names[:] = [
            name for name in directory_names if not name.startswith(".")
        ]
        for name in file_names:
            if name.endswith(".json") and not name.startswith("."):
                yield (Path(current) / name).resolve()


def _list(value: object) -> Sequence[object]:
    return value if isinstance(value, list) else ()


def _count(value: object, relative: str) -> int:
    return sum(1 for _ in _matching_strings(value, relative))


def _matching_strings(value: object, relative: str) -> Iterator[str]:
    if isinstance(value, str):
        normalized = value.replace("\\", "/").removeprefix("./")
        if normalized == relative or normalized.startswith(relative + "/"):
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _matching_strings(item, relative)
    elif isinstance(value, list):
        for item in value:
            yield from _matching_strings(item, relative)
