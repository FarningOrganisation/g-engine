from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast
from uuid import uuid4

from g.persistence import JsonValue, SceneFormatError


PREFAB_FORMAT = "g.prefab"
PREFAB_VERSION = 2
PREFAB_SUFFIX = ".prefab.json"
PREFAB_KINDS = {"game_object", "ui_view"}


@dataclass(frozen=True)
class PrefabDocument:
    prefab_id: str
    revision: int
    kind: str
    template: dict[str, JsonValue]

    @property
    def object(self) -> dict[str, JsonValue]:
        return self.template

    @classmethod
    def create(
        cls,
        record: dict[str, JsonValue],
        *,
        prefab_id: str | None = None,
        revision: int = 1,
        kind: str | None = None,
    ) -> PrefabDocument:
        prefab_kind = kind or (
            "ui_view" if record.get("type") == "ui" else "game_object"
        )
        if prefab_kind not in PREFAB_KINDS:
            raise ValueError(f"unknown prefab kind: {prefab_kind!r}")
        template = deepcopy(record)
        template.pop("prefab", None)
        if prefab_kind == "ui_view":
            template.pop("startup", None)
        _assign_template_ids(template, prefab_kind)
        _validate_template(template, prefab_kind)
        if prefab_kind == "game_object":
            _validate_object_references(template)
        return cls(
            prefab_id or str(uuid4()),
            revision,
            prefab_kind,
            template,
        )

    @classmethod
    def read(cls, filename: str | Path) -> PrefabDocument:
        path = Path(filename)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SceneFormatError(
                f"could not read prefab {path}: {error}"
            ) from error
        if not isinstance(raw, dict):
            raise SceneFormatError("prefab root must be an object")
        if raw.get("format") != PREFAB_FORMAT:
            raise SceneFormatError(
                f"expected prefab format {PREFAB_FORMAT!r}"
            )
        if raw.get("version") != PREFAB_VERSION:
            raise SceneFormatError(
                f"unsupported prefab version {raw.get('version')!r}"
            )
        prefab_id = raw.get("prefab_id")
        revision = raw.get("revision")
        kind = raw.get("kind")
        record = raw.get("template")
        if not isinstance(prefab_id, str) or not prefab_id:
            raise SceneFormatError("prefab ID must be a non-empty string")
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
        ):
            raise SceneFormatError("prefab revision must be positive")
        if kind not in PREFAB_KINDS:
            raise SceneFormatError(f"unknown prefab kind: {kind!r}")
        if not isinstance(record, dict):
            raise SceneFormatError("prefab template must be an object")
        typed = cast(dict[str, JsonValue], deepcopy(record))
        prefab_kind = cast(str, kind)
        _validate_template(typed, prefab_kind)
        if prefab_kind == "game_object":
            _validate_object_references(typed)
        return cls(prefab_id, revision, prefab_kind, typed)

    def write(self, filename: str | Path) -> Path:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            "format": PREFAB_FORMAT,
            "version": PREFAB_VERSION,
            "prefab_id": self.prefab_id,
            "revision": self.revision,
            "kind": self.kind,
            "template": self.template,
        }
        try:
            text = json.dumps(
                raw,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(text)
                temporary.write("\n")
                temporary_path = Path(temporary.name)
            temporary_path.replace(path)
        except (OSError, ValueError) as error:
            raise SceneFormatError(
                f"could not write prefab {path}: {error}"
            ) from error
        return path


def instantiate_prefab_record(
    prefab: PrefabDocument,
    source: str,
) -> dict[str, JsonValue]:
    record = deepcopy(prefab.template)
    regenerate_record_ids(record, kind=prefab.kind)
    if prefab.kind == "ui_view":
        record["startup"] = False
    record["prefab"] = {
        "source": source,
        "prefab_id": prefab.prefab_id,
        "revision": prefab.revision,
        "kind": prefab.kind,
        "base": deepcopy(prefab.template),
    }
    return record


def refresh_prefab_record(
    instance: dict[str, JsonValue],
    prefab: PrefabDocument,
    source: str,
) -> dict[str, JsonValue]:
    metadata = instance.get("prefab")
    old_base = (
        metadata.get("base")
        if isinstance(metadata, dict)
        else None
    )
    if not isinstance(old_base, dict):
        old_base = {}
    merged = _merge_overrides(
        cast(dict[str, JsonValue], old_base),
        instance,
        prefab.template,
        kind=prefab.kind,
        root=True,
    )
    old_ids = _template_scene_ids(instance, prefab.kind)
    old_root_id = instance.get("id")
    startup = instance.get("startup")
    instance_name = instance.get("name")
    regenerate_record_ids(merged, kind=prefab.kind)
    _restore_scene_ids(merged, old_ids, prefab.kind)
    generated_root_id = merged.get("id")
    if (
        isinstance(old_root_id, str)
        and isinstance(generated_root_id, str)
        and generated_root_id != old_root_id
    ):
        merged["id"] = old_root_id
        _remap_references(
            merged,
            {generated_root_id: old_root_id},
        )
    if prefab.kind == "ui_view" and isinstance(startup, bool):
        merged["startup"] = startup
    if prefab.kind == "ui_view" and isinstance(instance_name, str):
        merged["name"] = instance_name
    merged["prefab"] = {
        "source": source,
        "prefab_id": prefab.prefab_id,
        "revision": prefab.revision,
        "kind": prefab.kind,
        "base": deepcopy(prefab.template),
    }
    return merged


def prefab_override_count(record: dict[str, JsonValue]) -> int:
    metadata = record.get("prefab")
    if not isinstance(metadata, dict):
        return 0
    base = metadata.get("base")
    if not isinstance(base, dict):
        return 0
    return _difference_count(
        cast(dict[str, JsonValue], base),
        record,
        kind=(
            str(metadata.get("kind"))
            if metadata.get("kind") in PREFAB_KINDS
            else "game_object"
        ),
        root=True,
    )


def regenerate_record_ids(
    record: dict[str, JsonValue],
    *,
    only_missing: bool = False,
    kind: str = "game_object",
) -> None:
    replacements: dict[str, str] = {}

    def visit(current: dict[str, JsonValue]) -> None:
        old = current.get("id")
        if not only_missing or not isinstance(old, str):
            new = str(uuid4())
            if isinstance(old, str):
                replacements[old] = new
            current["id"] = new
        for key in _hierarchy_keys(current, kind):
            children = current.get(key)
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        visit(cast(dict[str, JsonValue], child))

    visit(record)
    _remap_references(record, replacements)


def _hierarchy_keys(
    record: dict[str, JsonValue],
    kind: str,
) -> tuple[str, ...]:
    if kind == "ui_view" and record.get("type") == "ui":
        return ("elements",)
    return ("children",)


def _assign_template_ids(
    record: dict[str, JsonValue],
    kind: str,
) -> None:
    if not isinstance(record.get("template_id"), str):
        record["template_id"] = str(uuid4())
    components = record.get("components")
    if isinstance(components, list):
        for component in components:
            if (
                isinstance(component, dict)
                and not isinstance(component.get("template_id"), str)
            ):
                component["template_id"] = str(uuid4())
    for key in _hierarchy_keys(record, kind):
        children = record.get(key)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    _assign_template_ids(
                        cast(dict[str, JsonValue], child),
                        kind,
                    )


def _validate_template(
    record: dict[str, JsonValue],
    kind: str,
    *,
    root: bool = True,
) -> None:
    if not isinstance(record.get("template_id"), str):
        raise SceneFormatError("prefab template ID is missing")
    if not isinstance(record.get("type"), str):
        raise SceneFormatError("prefab template type is missing")
    if root and kind == "ui_view" and record.get("type") != "ui":
        raise SceneFormatError("UI-view prefab root must have type 'ui'")
    if root and kind == "game_object" and record.get("type") == "ui":
        raise SceneFormatError("game-object prefab cannot contain a UI view")
    components = record.get("components", [])
    if not isinstance(components, list):
        raise SceneFormatError("prefab components must be a list")
    for component in components:
        if (
            not isinstance(component, dict)
            or not isinstance(component.get("template_id"), str)
        ):
            raise SceneFormatError(
                "prefab component template ID is missing"
            )
    for key in _hierarchy_keys(record, kind):
        children = record.get(key, [])
        if not isinstance(children, list):
            raise SceneFormatError(f"prefab {key} must be a list")
        for child in children:
            if not isinstance(child, dict):
                raise SceneFormatError("prefab child must be an object")
            _validate_template(
                cast(dict[str, JsonValue], child),
                kind,
                root=False,
            )


def _validate_object_references(record: dict[str, JsonValue]) -> None:
    identifiers: set[str] = set()
    references: set[str] = set()

    def collect(current: JsonValue) -> None:
        if isinstance(current, dict):
            identifier = current.get("id")
            if (
                isinstance(identifier, str)
                and isinstance(current.get("type"), str)
                and isinstance(current.get("children"), list)
            ):
                identifiers.add(identifier)
            reference = current.get("$object")
            if isinstance(reference, str):
                references.add(reference)
            for value in current.values():
                collect(value)
        elif isinstance(current, list):
            for value in current:
                collect(value)

    collect(record)
    external = references - identifiers
    if external:
        names = ", ".join(sorted(external))
        raise SceneFormatError(
            "prefab references objects outside its hierarchy: "
            f"{names}"
        )


def _merge_overrides(
    old: dict[str, JsonValue],
    current: dict[str, JsonValue],
    new: dict[str, JsonValue],
    *,
    kind: str,
    root: bool = False,
) -> dict[str, JsonValue]:
    if old.get("template_id") != new.get("template_id"):
        result = deepcopy(new)
        if (
            root
            and kind == "game_object"
            and isinstance(current.get("transform"), dict)
        ):
            result["transform"] = deepcopy(current["transform"])
        if root and kind == "ui_view":
            result["startup"] = bool(current.get("startup", False))
            if isinstance(current.get("name"), str):
                result["name"] = current["name"]
        return result
    result = deepcopy(new)
    for key in set(old) | set(current):
        if key in {
            "id",
            "prefab",
            "template_id",
            "children",
            "components",
            "elements",
        }:
            continue
        if root and kind == "game_object" and key == "transform":
            result[key] = deepcopy(current.get(key, new.get(key)))
        elif root and kind == "ui_view" and key in {"startup", "name"}:
            result[key] = deepcopy(current.get(key, new.get(key)))
        elif (
            isinstance(old.get(key), dict)
            and isinstance(current.get(key), dict)
            and isinstance(new.get(key), dict)
        ):
            result[key] = _merge_mapping(
                cast(dict[str, JsonValue], old[key]),
                cast(dict[str, JsonValue], current[key]),
                cast(dict[str, JsonValue], new[key]),
            )
        elif current.get(key) != old.get(key):
            if key in current:
                result[key] = deepcopy(current[key])
            else:
                result.pop(key, None)
    for key in ("components", "children", "elements"):
        if key not in old and key not in current and key not in new:
            continue
        result[key] = _merge_template_list(
            old.get(key),
            current.get(key),
            new.get(key),
            kind=kind,
        )
    return result


def _merge_mapping(
    old: dict[str, JsonValue],
    current: dict[str, JsonValue],
    new: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    result = deepcopy(new)
    for key in set(old) | set(current):
        old_value = old.get(key)
        current_value = current.get(key)
        new_value = new.get(key)
        if (
            isinstance(old_value, dict)
            and isinstance(current_value, dict)
            and isinstance(new_value, dict)
        ):
            result[key] = _merge_mapping(
                cast(dict[str, JsonValue], old_value),
                cast(dict[str, JsonValue], current_value),
                cast(dict[str, JsonValue], new_value),
            )
        elif current_value != old_value:
            if key in current:
                result[key] = deepcopy(current[key])
            else:
                result.pop(key, None)
    return result


def _merge_template_list(
    old: JsonValue | None,
    current: JsonValue | None,
    new: JsonValue | None,
    *,
    kind: str,
) -> list[JsonValue]:
    old_items = _template_map(old)
    current_items = _template_map(current)
    new_list = new if isinstance(new, list) else []
    new_template_ids = {
        template_id
        for candidate in new_list
        if isinstance(candidate, dict)
        and isinstance(
            template_id := candidate.get("template_id"),
            str,
        )
    }
    merged: list[JsonValue] = []
    for item in new_list:
        if not isinstance(item, dict):
            merged.append(deepcopy(item))
            continue
        template_id = item.get("template_id")
        current_item = current_items.get(template_id)
        old_item = old_items.get(template_id)
        if current_item is None or old_item is None:
            merged.append(deepcopy(item))
        else:
            merged.append(
                _merge_overrides(
                    old_item,
                    current_item,
                    item,
                    kind=kind,
                )
            )
    for template_id, item in current_items.items():
        if (
            template_id not in old_items
            and template_id not in new_template_ids
        ):
            merged.append(deepcopy(item))
    return merged


def _template_map(value: JsonValue | None) -> dict[object, dict[str, JsonValue]]:
    if not isinstance(value, list):
        return {}
    return {
        item.get("template_id"): cast(dict[str, JsonValue], item)
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("template_id"), str)
    }


def _template_scene_ids(
    record: dict[str, JsonValue],
    kind: str,
) -> dict[str, str]:
    result: dict[str, str] = {}

    def visit(current: dict[str, JsonValue]) -> None:
        template_id = current.get("template_id")
        identifier = current.get("id")
        if isinstance(template_id, str) and isinstance(identifier, str):
            result[template_id] = identifier
        for key in _hierarchy_keys(current, kind):
            children = current.get(key)
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        visit(cast(dict[str, JsonValue], child))

    visit(record)
    return result


def _restore_scene_ids(
    record: dict[str, JsonValue],
    identifiers: dict[str, str],
    kind: str,
) -> None:
    replacements: dict[str, str] = {}

    def visit(current: dict[str, JsonValue]) -> None:
        template_id = current.get("template_id")
        current_id = current.get("id")
        if (
            isinstance(template_id, str)
            and isinstance(current_id, str)
            and template_id in identifiers
        ):
            restored_id = identifiers[template_id]
            replacements[current_id] = restored_id
            current["id"] = restored_id
        for key in _hierarchy_keys(current, kind):
            children = current.get(key)
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        visit(cast(dict[str, JsonValue], child))

    visit(record)
    _remap_references(record, replacements)


def _difference_count(
    base: dict[str, JsonValue],
    current: dict[str, JsonValue],
    *,
    kind: str,
    root: bool,
) -> int:
    count = 0
    for key in set(base) | set(current):
        if key in {
            "id",
            "prefab",
            "template_id",
            "children",
            "components",
            "elements",
        }:
            continue
        if root and (
            (kind == "game_object" and key == "transform")
            or (kind == "ui_view" and key in {"startup", "name"})
        ):
            continue
        if base.get(key) != current.get(key):
            count += 1
    for key in ("components", "children", "elements"):
        base_items = _template_map(base.get(key))
        current_items = _template_map(current.get(key))
        for template_id in set(base_items) | set(current_items):
            if template_id not in base_items or template_id not in current_items:
                count += 1
            else:
                count += _difference_count(
                    base_items[template_id],
                    current_items[template_id],
                    kind=kind,
                    root=False,
                )
    return count


def _remap_references(value: JsonValue, replacements: dict[str, str]) -> None:
    if isinstance(value, dict):
        reference = value.get("$object")
        if isinstance(reference, str) and reference in replacements:
            value["$object"] = replacements[reference]
        for child in value.values():
            _remap_references(child, replacements)
    elif isinstance(value, list):
        for child in value:
            _remap_references(child, replacements)
