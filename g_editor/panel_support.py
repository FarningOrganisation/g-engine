from __future__ import annotations

from dataclasses import dataclass
from g.persistence import JsonValue


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif"}
HIERARCHY_DRAG_PAYLOAD = "G_HIERARCHY_ITEM"
ASSET_DRAG_PAYLOAD = "G_ASSET"


@dataclass
class TileCollisionDrag:
    handle: str
    start: tuple[float, float]
    original: dict[str, JsonValue]
    latest: dict[str, JsonValue]
    revision: int


@dataclass(frozen=True)
class AssetPickerTarget:
    field_id: str
    label: str
    kind: str


@dataclass(frozen=True)
class RenameTarget:
    kind: str
    identifier: str
