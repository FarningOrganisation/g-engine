"""Loading runtime game-object prefabs.

``PREFAB_FORMAT`` identifies the JSON format as ``"g.prefab"`` and
``PREFAB_VERSION`` is the supported format version (2).
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .nodes import GameObject
from .persistence import JsonValue, SceneDocument, SceneFormatError, SceneSerializer


PREFAB_FORMAT = "g.prefab"
PREFAB_VERSION = 2


def instantiate_prefab(
    game: Any,
    filename: str | Path,
    *,
    asset_root: str | Path | None = None,
    scene_id: str | None = None,
) -> GameObject:
    """Load a prefab file and return an unattached game-object instance.

    Args:
        game: Game providing resources and services during restoration.
        filename: Path to the prefab JSON file.
        asset_root: Root used to resolve prefab resources; defaults to the
            prefab file's directory.
        scene_id: Optional ID to assign to the root object.

    Returns:
        A restored root object with regenerated IDs for the root and children.

    Raises:
        SceneFormatError: If the file cannot be read, its format/version or
            root kind is unsupported, or restoration does not produce exactly
            one root object.
    """
    path = Path(filename).expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SceneFormatError(f"could not read prefab {path}: {error}") from error
    if not isinstance(raw, dict) or raw.get("format") != PREFAB_FORMAT:
        raise SceneFormatError(f"expected prefab format {PREFAB_FORMAT!r}")
    if raw.get("version") != PREFAB_VERSION:
        raise SceneFormatError(
            f"unsupported prefab version {raw.get('version')!r}"
        )
    if raw.get("kind") != "game_object":
        raise SceneFormatError("runtime prefab must contain a game object")
    template = raw.get("template")
    if not isinstance(template, dict):
        raise SceneFormatError("prefab template must be an object")

    record = deepcopy(template)
    replacements: dict[str, str] = {}
    _regenerate_ids(record, replacements)
    _remap_ids(record, replacements)
    if scene_id is not None:
        generated = record.get("id")
        if isinstance(generated, str):
            _remap_ids(record, {generated: scene_id})
        record["id"] = scene_id

    document = SceneDocument(
        settings={},
        objects=[record],
    )
    staging = SceneSerializer().restore(
        game,
        document,
        asset_root=path.parent if asset_root is None else asset_root,
        initialize_components=False,
    )
    if len(staging.objects) != 1:
        raise SceneFormatError("game object prefab must have exactly one root")
    instance = staging.objects[0]
    instance.parent = None
    staging.physics.refresh()
    for node in instance.walk():
        if isinstance(node, GameObject):
            node._scene = None
            node._layer = None
    return instance


def _regenerate_ids(
    record: dict[str, JsonValue],
    replacements: dict[str, str],
) -> None:
    identifier = record.get("id")
    replacement = str(uuid4())
    if isinstance(identifier, str):
        replacements[identifier] = replacement
    record["id"] = replacement
    children = record.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                _regenerate_ids(child, replacements)


def _remap_ids(value: object, replacements: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, item in tuple(value.items()):
            if isinstance(item, str) and item in replacements:
                value[key] = replacements[item]
            else:
                _remap_ids(item, replacements)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str) and item in replacements:
                value[index] = replacements[item]
            else:
                _remap_ids(item, replacements)
