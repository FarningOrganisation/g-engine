"""Scene serialization API.

``SCENE_FORMAT`` is ``"g.scene"`` and ``SCENE_VERSION`` is the currently
supported scene document version (1). ``scene_types`` is the shared registry;
``scene_object`` and ``scene_component`` are its registration decorators.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from copy import deepcopy
from math import isfinite, radians
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Generic, TypeVar, cast
from uuid import uuid4

from .components import (
    Area,
    Collider,
    ColliderMode,
    CircleRenderer,
    Component,
    LineRenderer,
    PointLight2D,
    NetworkClient,
    NetworkedGameObject,
    ParticleEffect,
    PolygonRenderer,
    TileRenderer,
    RectRenderer,
    Renderer,
    RepeatingImageRenderer,
    Script,
    ShapeStyle,
    ShadowOccluder2D,
    SpriteAnimation,
    SpriteAnimationFrame,
    SpriteAnimator,
    SpriteRenderer,
    SpotLight2D,
    Spline,
    TextRenderer,
    TrailRenderer,
    TriangleRenderer,
)
from .gui import (
    AnchorLayout,
    Button,
    ButtonStyle,
    CanvasLayout,
    Checkbox,
    CheckboxStyle,
    Container,
    Dropdown,
    DropdownStyle,
    GridLayout,
    HBox,
    ImageFit,
    Label,
    ListView,
    ListViewStyle,
    Panel,
    ProgressBar,
    ProgressBarStyle,
    ScrollView,
    ScrollbarStyle,
    Slider,
    StackLayout,
    TextInput,
    Toggle,
    UIImage,
    UIView,
    VBox,
    Widget,
)
from .math import Vec2
from .inspector import inspector_fields, public_field_names
from .nodes import GameObject
from .physics import (
    BodyType,
    PhysicsBody,
)
from .particles import ParticleEffectDefinition, ParticleFormatError
from .scripts import CameraFollow, PlatformerMotor, TopDownMotor
from .presentation import (
    SCALING_MODES,
    PresentationSettings,
    ScalingMode,
)
from .resources import Animation, Color, Font, Image, SpriteSheetRegion
from .scene import ObjectLayer, Scene
from .tilemap import (
    ColorTileSet,
    ImageCollectionTileSet,
    TileAnimation,
    TileAnimationFrame,
    TileChunk,
    TileCollision,
    TileLayer,
    TileNavigation,
    TileSet,
    decode_tile_cell,
)


SCENE_FORMAT = "g.scene"
SCENE_VERSION = 1
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
PersistedType = TypeVar("PersistedType")
ObjectType = TypeVar("ObjectType", bound=GameObject)
ComponentType = TypeVar("ComponentType", bound=Component)


class SceneFormatError(ValueError):
    """Invalid, unsupported, or unserializable scene data."""

    pass


def _validate_gui_element_records(elements: object) -> None:
    if not isinstance(elements, list):
        raise SceneFormatError(
            "UI view elements must be a list of objects"
        )
    identifiers: set[str] = set()

    def visit(element: object) -> None:
        if not isinstance(element, dict):
            raise SceneFormatError("GUI element must be an object")
        identifier = element.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise SceneFormatError(
                "GUI element ID must be a non-empty string"
            )
        if identifier in identifiers:
            raise SceneFormatError(
                f"duplicate GUI element ID: {identifier!r}"
            )
        identifiers.add(identifier)
        children = element.get("children", [])
        if not isinstance(children, list):
            raise SceneFormatError(
                "GUI element children must be a list"
            )
        container_types = {
            "panel",
            "stack",
            "vbox",
            "hbox",
            "grid",
            "anchor",
            "scroll",
            "list_view",
        }
        if children and element.get("type") not in container_types:
            raise SceneFormatError(
                "only GUI containers can contain child elements"
            )
        if element.get("type") == "anchor" and len(children) > 1:
            raise SceneFormatError(
                "GUI anchor layout can contain only one child"
            )
        if element.get("type") == "scroll" and len(children) > 1:
            raise SceneFormatError(
                "GUI scroll view can contain only one child"
            )
        for child in children:
            visit(child)

    for element in elements:
        visit(element)


@dataclass(init=False)
class SceneDocument:
    """JSON-compatible scene data independent of a running game.

    Attributes:
        settings: Scene-level settings and checkpoint data.
        layers: Serialized object and tile layers.
        ui_views: Serialized UI views and their element trees.
        version: Scene format version.
    """

    settings: dict[str, JsonValue]
    layers: list[dict[str, JsonValue]]
    ui_views: list[dict[str, JsonValue]]
    version: int

    def __init__(
        self,
        settings: dict[str, JsonValue],
        objects: list[dict[str, JsonValue]] | None = None,
        version: int = SCENE_VERSION,
        *,
        layers: list[dict[str, JsonValue]] | None = None,
        ui_views: list[dict[str, JsonValue]] | None = None,
    ) -> None:
        """Create a document from settings and either objects or layers.

        Args:
            settings: JSON-compatible scene settings.
            objects: Optional objects placed in a generated default layer.
            version: Scene format version.
            layers: Optional explicit layer records.
            ui_views: Optional UI view records.

        Raises:
            ValueError: If both ``objects`` and ``layers`` are supplied.
        """
        if layers is not None and objects is not None:
            raise ValueError("provide either objects or layers")
        self.settings = settings
        self.layers = layers or cast(
            list[dict[str, JsonValue]],
            [
                {
                    "type": "object",
                    "id": "default",
                    "name": "Default",
                    "visible": True,
                    "enabled": True,
                    "default": True,
                    "objects": objects or [],
                }
            ],
        )
        self.ui_views = ui_views or [
            {
                "type": "ui",
                "id": "hud",
                "name": "HUD",
                "startup": True,
                "transparent": True,
                "modal": False,
                "pauses_below": True,
                "pauses_scene": False,
                "elements": [],
            }
        ]
        self.version = version

    @property
    def objects(self) -> list[dict[str, JsonValue]]:
        """Objects in the default layer, or first layer if none is marked.

        Returns:
            Mutable object records for the selected object layer.

        Raises:
            SceneFormatError: If that layer's ``objects`` value is not a list.
        """
        layer = next(
            (
                candidate
                for candidate in self.layers
                if candidate.get("default") is True
            ),
            self.layers[0] if self.layers else None,
        )
        if layer is None:
            return []
        objects = layer.get("objects")
        if not isinstance(objects, list):
            raise SceneFormatError("scene layer objects must be a list")
        return cast(list[dict[str, JsonValue]], objects)

    def to_dict(self) -> dict[str, object]:
        """Return the document as a format-tagged mapping.

        Returns:
            Mapping containing format, version, settings, layers, and UI views.
        """
        return {
            "format": SCENE_FORMAT,
            "version": self.version,
            "settings": self.settings,
            "layers": self.layers,
            "ui_views": self.ui_views,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SceneDocument:
        """Validate and create a document from a decoded mapping.

        Args:
            value: Candidate scene document mapping.

        Returns:
            Validated scene document.

        Raises:
            SceneFormatError: If format, version, layers, UI views, or records
                are invalid.
        """
        if value.get("format") != SCENE_FORMAT:
            raise SceneFormatError(f"expected scene format {SCENE_FORMAT!r}")
        version = value.get("version")
        if not isinstance(version, int) or version != SCENE_VERSION:
            raise SceneFormatError(
                f"unsupported scene version {version!r}; "
                f"expected {SCENE_VERSION}"
            )
        settings = value.get("settings")
        if not isinstance(settings, dict):
            raise SceneFormatError("scene settings must be an object")
        typed_settings = cast(dict[str, JsonValue], dict(settings))
        layers = value.get("layers")
        if not isinstance(layers, list) or not layers:
            raise SceneFormatError("scene layers must be a non-empty list")
        typed_layers: list[dict[str, JsonValue]] = []
        identifiers: set[str] = set()
        names: set[str] = set()
        default_count = 0
        for raw_layer in layers:
            if not isinstance(raw_layer, dict):
                raise SceneFormatError("scene layer must be an object")
            identifier = raw_layer.get("id")
            name = raw_layer.get("name")
            layer_type = raw_layer.get("type", "object")
            if not isinstance(identifier, str) or not identifier:
                raise SceneFormatError("scene layer ID must be a non-empty string")
            if identifier in identifiers:
                raise SceneFormatError(
                    f"duplicate scene layer ID: {identifier!r}"
                )
            if not isinstance(name, str) or not name:
                raise SceneFormatError(
                    "scene layer name must be a non-empty string"
                )
            if name in names:
                raise SceneFormatError(
                    f"duplicate scene layer name: {name!r}"
                )
            if layer_type not in {"object", "tile"}:
                raise SceneFormatError(
                    f"unknown scene layer type: {layer_type!r}"
                )
            if layer_type == "object":
                objects = raw_layer.get("objects")
                if not isinstance(objects, list) or not all(
                    isinstance(item, dict) for item in objects
                ):
                    raise SceneFormatError(
                        "object layer objects must be a list of objects"
                    )
            elif layer_type == "tile":
                if raw_layer.get("default") is True:
                    raise SceneFormatError(
                        "the default layer must be an object layer"
                    )
                chunks = raw_layer.get("chunks", [])
                if not isinstance(chunks, list) or not all(
                    isinstance(item, dict) for item in chunks
                ):
                    raise SceneFormatError(
                        "tile layer chunks must be a list of objects"
                    )
            identifiers.add(identifier)
            names.add(name)
            default_count += raw_layer.get("default") is True
            normalized_layer = dict(raw_layer)
            normalized_layer.pop("locked", None)
            typed_layers.append(
                cast(dict[str, JsonValue], normalized_layer)
            )
        if default_count != 1:
            raise SceneFormatError(
                "scene must contain exactly one default layer"
            )
        ui_views = value.get("ui_views")
        if not isinstance(ui_views, list) or not ui_views:
            raise SceneFormatError(
                "scene UI views must be a non-empty list"
            )
        typed_ui_views: list[dict[str, JsonValue]] = []
        view_identifiers: set[str] = set()
        view_names: set[str] = set()
        startup_count = 0
        for raw_view in ui_views:
            if not isinstance(raw_view, dict):
                raise SceneFormatError("scene UI view must be an object")
            identifier = raw_view.get("id")
            name = raw_view.get("name")
            if raw_view.get("type") != "ui":
                raise SceneFormatError(
                    "scene UI view type must be 'ui'"
                )
            if not isinstance(identifier, str) or not identifier:
                raise SceneFormatError(
                    "scene UI view ID must be a non-empty string"
                )
            if identifier in view_identifiers:
                raise SceneFormatError(
                    f"duplicate scene UI view ID: {identifier!r}"
                )
            if not isinstance(name, str) or not name:
                raise SceneFormatError(
                    "scene UI view name must be a non-empty string"
                )
            if name in view_names:
                raise SceneFormatError(
                    f"duplicate scene UI view name: {name!r}"
                )
            for property_name in (
                "startup",
                "transparent",
                "modal",
                "pauses_below",
                "pauses_scene",
            ):
                if not isinstance(raw_view.get(property_name), bool):
                    raise SceneFormatError(
                        f"scene UI view {property_name} must be a boolean"
                    )
            _validate_gui_element_records(
                raw_view.get("elements", [])
            )
            view_identifiers.add(identifier)
            view_names.add(name)
            startup_count += raw_view.get("startup") is True
            typed_ui_views.append(
                cast(dict[str, JsonValue], dict(raw_view))
            )
        if startup_count != 1:
            raise SceneFormatError(
                "scene must contain exactly one startup UI view"
            )
        return cls(
            typed_settings,
            version=SCENE_VERSION,
            layers=typed_layers,
            ui_views=typed_ui_views,
        )

    @classmethod
    def read(cls, filename: str | Path) -> SceneDocument:
        """Read JSON from a file and validate it as a scene document.

        Args:
            filename: Scene JSON path.

        Returns:
            Validated scene document.

        Raises:
            SceneFormatError: If the file cannot be read or is invalid.
        """
        path = Path(filename)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SceneFormatError(f"could not read scene {path}: {error}") from error
        if not isinstance(value, dict):
            raise SceneFormatError("scene root must be an object")
        return cls.from_dict(value)

    def write(self, filename: str | Path) -> None:
        """Atomically write this document as indented UTF-8 JSON.

        Args:
            filename: Destination scene path.

        Raises:
            SceneFormatError: If writing the temporary file or replacing the
                destination fails.
            OSError: If the destination directory cannot be created.
            ValueError: If document data cannot be encoded as JSON.
        """
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            self.to_dict(),
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        try:
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
        except OSError as error:
            raise SceneFormatError(f"could not write scene {path}: {error}") from error


@dataclass
class SceneLoadContext:
    game: Any
    asset_root: Path
    objects: dict[str, GameObject]
    allow_missing_resources: bool = False

    def resource_exists(self, value: object) -> bool:
        return self.resource_path(value, must_exist=False).exists()

    def resource_path(
        self,
        value: object,
        *,
        must_exist: bool = True,
    ) -> Path:
        if not isinstance(value, str) or not value:
            raise SceneFormatError("resource path must be a non-empty string")
        path = Path(value)
        if path.is_absolute():
            raise SceneFormatError("resource paths must be relative")
        resolved = (self.asset_root / path).resolve()
        try:
            resolved.relative_to(self.asset_root.resolve())
        except ValueError:
            raise SceneFormatError(
                f"resource path leaves the project root: {value!r}"
            ) from None
        if must_exist and not resolved.exists():
            raise SceneFormatError(f"missing resource: {value}")
        return resolved

    def decode(self, value: object) -> Any:
        if isinstance(value, list):
            return [self.decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"$vec2"}:
            coordinates = value["$vec2"]
            if (
                not isinstance(coordinates, list)
                or len(coordinates) != 2
                or not all(isinstance(item, (int, float)) for item in coordinates)
            ):
                raise SceneFormatError("invalid Vec2 value")
            return Vec2(*coordinates)
        if set(value) == {"$object"}:
            identifier = value["$object"]
            if not isinstance(identifier, str):
                raise SceneFormatError("object reference ID must be a string")
            try:
                return self.objects[identifier]
            except KeyError:
                raise SceneFormatError(
                    f"object reference does not exist: {identifier!r}"
                ) from None
        return {str(key): self.decode(item) for key, item in value.items()}


@dataclass
class SceneSaveContext:
    asset_root: Path
    include_runtime_state: bool = False

    def resource_path(self, resource: Image | Animation | Font) -> str:
        filename = resource._filename
        if filename is None:
            raise SceneFormatError(
                "generated images and animations cannot be stored as resource paths"
            )
        try:
            return filename.resolve().relative_to(self.asset_root.resolve()).as_posix()
        except ValueError:
            raise SceneFormatError(
                f"resource is outside the project root: {filename}"
            ) from None

    def encode(self, value: Any) -> JsonValue:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Vec2):
            return {"$vec2": [value.x, value.y]}
        if isinstance(value, GameObject):
            if value.scene_id is None:
                raise SceneFormatError("referenced game object has no scene ID")
            return {"$object": value.scene_id}
        if isinstance(value, Mapping):
            return {str(key): self.encode(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [self.encode(item) for item in value]
        raise SceneFormatError(
            f"value of type {type(value).__name__} is not scene-serializable"
        )


CreateFunction = Callable[[SceneLoadContext, Mapping[str, object]], PersistedType]
DumpFunction = Callable[[PersistedType, SceneSaveContext], Mapping[str, object]]
RestoreFunction = Callable[
    [PersistedType, Mapping[str, object], SceneLoadContext], None
]


@dataclass
class _TypeCodec(Generic[PersistedType]):
    name: str
    value_type: type[PersistedType]
    create: CreateFunction[PersistedType]
    dump: DumpFunction[PersistedType]
    restore: RestoreFunction[PersistedType] | None = None
    fields: tuple[str, ...] = ()
    display_name: str | None = None
    category: str | None = None
    builtin: bool = False


class SceneTypeRegistry:
    """Registry mapping scene type names to object/component codecs.

    The module-level :data:`scene_types` contains the built-in registrations.
    """

    def __init__(self) -> None:
        """Create an empty registry."""
        self._objects_by_name: dict[str, _TypeCodec[GameObject]] = {}
        self._objects_by_type: dict[type[GameObject], _TypeCodec[GameObject]] = {}
        self._components_by_name: dict[str, _TypeCodec[Component]] = {}
        self._components_by_type: dict[type[Component], _TypeCodec[Component]] = {}

    @property
    def object_types(self) -> tuple[str, ...]:
        """Registered game-object type names in sorted order."""
        return tuple(sorted(self._objects_by_name))

    @property
    def component_types(self) -> tuple[str, ...]:
        """Registered component type names in sorted order."""
        return tuple(sorted(self._components_by_name))

    @property
    def editable_component_types(self) -> tuple[str, ...]:
        """Sorted component names exposing editor fields or script behavior."""
        return tuple(
            sorted(
                name
                for name, codec in self._components_by_name.items()
                if codec.fields or issubclass(codec.value_type, Script)
            )
        )

    def component_template(self, name: str) -> dict[str, JsonValue]:
        """Build a default serialized template for an editable component.

        Args:
            name: Registered component name.

        Returns:
            Type and default properties record.

        Raises:
            SceneFormatError: If the type is unknown or has no editor fields.
        """
        codec = self.component_codec_for_name(name)
        if not codec.fields and not issubclass(codec.value_type, Script):
            raise SceneFormatError(
                f"component type has no editor-exposed fields: {name!r}"
            )
        context = SceneSaveContext(Path.cwd())
        metadata = inspector_fields(codec.value_type)
        properties: dict[str, JsonValue] = {}
        for field in codec.fields:
            properties[field] = context.encode(
                metadata[field].default if field in metadata
                else getattr(codec.value_type, field, None)
            )
        properties["enabled"] = True
        return {"type": name, "properties": properties}

    def register_object(
        self,
        name: str,
        value_type: type[ObjectType],
        *,
        create: CreateFunction[ObjectType] | None = None,
        dump: DumpFunction[ObjectType] | None = None,
        restore: RestoreFunction[ObjectType] | None = None,
        fields: Sequence[str] | None = None,
    ) -> None:
        """Register a game-object type and its persistence codec.

        Args:
            name: Unique serialized type name.
            value_type: Game-object class handled by this codec.
            create: Optional custom factory from load context and properties.
            dump: Optional custom property serializer.
            restore: Optional callback to restore post-construction state.
            fields: Optional fields automatically saved and restored.

        Raises:
            ValueError: If the name or class is already registered.
        """
        codec = self._make_codec(
            name,
            value_type,
            create=create,
            dump=dump,
            restore=restore,
            fields=fields,
        )
        self._register(codec, self._objects_by_name, self._objects_by_type)

    def register_component(
        self,
        name: str,
        value_type: type[ComponentType],
        *,
        create: CreateFunction[ComponentType] | None = None,
        dump: DumpFunction[ComponentType] | None = None,
        restore: RestoreFunction[ComponentType] | None = None,
        fields: Sequence[str] | None = None,
        display_name: str | None = None,
        category: str | None = None,
        builtin: bool = False,
    ) -> None:
        """Register a component type and its persistence/editor metadata.

        Args:
            name: Unique serialized type name.
            value_type: Component class handled by this codec.
            create: Optional custom factory.
            dump: Optional custom property serializer.
            restore: Optional post-construction restoration callback.
            fields: Optional editor-exposed fields.
            display_name: Optional trimmed editor label.
            category: Optional trimmed editor category.
            builtin: Whether this registration is a built-in type.

        Raises:
            ValueError: If metadata is invalid or name/class is already
                registered.
        """
        for label, value in (("display_name", display_name), ("category", category)):
            if value is not None and (
                not isinstance(value, str) or not value.strip() or value != value.strip()
            ):
                raise ValueError(f"{label} must be a non-empty, trimmed string")
        codec = self._make_codec(
            name,
            value_type,
            create=create,
            dump=dump,
            restore=restore,
            fields=fields,
            include_component_enabled=True,
        )
        codec.display_name = display_name
        codec.category = category
        codec.builtin = builtin
        self._register(codec, self._components_by_name, self._components_by_type)

    def object(
        self,
        name: str,
        *,
        fields: Sequence[str] | None = None,
        create: CreateFunction[GameObject] | None = None,
    ) -> Callable[[type[PersistedType]], type[PersistedType]]:
        """Return a decorator that registers a game-object class.

        Args:
            name: Serialized type name.
            fields: Optional automatically persisted field names.
            create: Optional custom factory.

        Raises:
            TypeError: When the decorated class does not extend
                :class:`GameObject`.
        """
        def register(value_type: type[PersistedType]) -> type[PersistedType]:
            if not issubclass(value_type, GameObject):
                raise TypeError("registered scene object must extend GameObject")
            self.register_object(name, value_type, create=create, fields=fields)
            return value_type

        return register

    def component(
        self,
        name: str,
        *,
        fields: Sequence[str] | None = None,
        create: CreateFunction[Component] | None = None,
        display_name: str | None = None,
        category: str | None = None,
    ) -> Callable[[type[PersistedType]], type[PersistedType]]:
        """Return a decorator that registers a component class.

        Args:
            name: Serialized type name.
            fields: Optional editor-exposed field names.
            create: Optional custom factory.
            display_name: Optional editor label.
            category: Optional editor category.

        Raises:
            TypeError: When the decorated class does not extend
                :class:`Component`.
        """
        def register(value_type: type[PersistedType]) -> type[PersistedType]:
            if not issubclass(value_type, Component):
                raise TypeError("registered scene component must extend Component")
            self.register_component(
                name, value_type, create=create, fields=fields,
                display_name=display_name, category=category,
            )
            return value_type

        return register

    def _save_state(self) -> tuple[dict[Any, Any], ...]:
        return (
            dict(self._objects_by_name),
            dict(self._objects_by_type),
            dict(self._components_by_name),
            dict(self._components_by_type),
        )

    def _load_state(self, state: tuple[dict[Any, Any], ...]) -> None:
        (
            self._objects_by_name,
            self._objects_by_type,
            self._components_by_name,
            self._components_by_type,
        ) = (dict(item) for item in state)

    def unregister_module(self, module: str) -> None:
        """Forget registered object and component classes defined in a module.

        Args:
            module: Exact Python module name to unregister.
        """
        self._unregister_module(
            module, self._objects_by_name, self._objects_by_type
        )
        self._unregister_module(
            module, self._components_by_name, self._components_by_type
        )

    @staticmethod
    def _unregister_module(
        module: str,
        by_name: dict[str, Any],
        by_type: dict[type[Any], Any],
    ) -> None:
        for name, codec in tuple(by_name.items()):
            if codec.value_type.__module__ == module:
                del by_name[name]
                by_type.pop(codec.value_type, None)

    def object_codec_for_name(self, name: str) -> _TypeCodec[GameObject]:
        """Return the registered game-object codec for a serialized name.

        Raises:
            SceneFormatError: If ``name`` is not registered.
        """
        try:
            return self._objects_by_name[name]
        except KeyError:
            raise SceneFormatError(f"unregistered game object type: {name!r}") from None

    def component_codec_for_name(self, name: str) -> _TypeCodec[Component]:
        """Return the registered component codec for a serialized name.

        Raises:
            SceneFormatError: If ``name`` is not registered.
        """
        try:
            return self._components_by_name[name]
        except KeyError:
            raise SceneFormatError(f"unregistered component type: {name!r}") from None

    def component_type_for_name(
        self,
        name: str,
    ) -> type[Component]:
        """Return the component class registered under ``name``.

        Raises:
            SceneFormatError: If ``name`` is not registered.
        """
        return self.component_codec_for_name(name).value_type

    def component_name_for_type(
        self,
        value_type: type[Component],
    ) -> str:
        """Return the registered serialized name for a component class.

        Raises:
            SceneFormatError: If the exact class is not registered.
        """
        try:
            return self._components_by_type[value_type].name
        except KeyError:
            raise SceneFormatError(
                f"component type is not registered: "
                f"{value_type.__name__}"
            ) from None

    def object_codec_for_value(self, value: GameObject) -> _TypeCodec[GameObject]:
        """Return the codec registered for the exact game-object class.

        Raises:
            SceneFormatError: If the exact class is not registered.
        """
        try:
            return self._objects_by_type[type(value)]
        except KeyError:
            raise SceneFormatError(
                f"game object type is not registered: {type(value).__name__}"
            ) from None

    def component_codec_for_value(self, value: Component) -> _TypeCodec[Component]:
        """Return the codec registered for the exact component class.

        Raises:
            SceneFormatError: If the exact class is not registered.
        """
        try:
            return self._components_by_type[type(value)]
        except KeyError:
            raise SceneFormatError(
                f"component type is not registered: {type(value).__name__}"
            ) from None

    @staticmethod
    def _register(
        codec: _TypeCodec[Any],
        by_name: dict[str, Any],
        by_type: dict[type[Any], Any],
    ) -> None:
        if not codec.name or codec.name in by_name:
            raise ValueError(f"scene type name is already registered: {codec.name!r}")
        if codec.value_type in by_type:
            raise ValueError(
                f"scene class is already registered: {codec.value_type.__name__}"
            )
        by_name[codec.name] = codec
        by_type[codec.value_type] = codec

    @staticmethod
    def _make_codec(
        name: str,
        value_type: type[PersistedType],
        *,
        create: CreateFunction[PersistedType] | None,
        dump: DumpFunction[PersistedType] | None,
        restore: RestoreFunction[PersistedType] | None,
        fields: Sequence[str] | None,
        include_component_enabled: bool = False,
    ) -> _TypeCodec[PersistedType]:
        metadata = inspector_fields(value_type)
        field_names = tuple(fields) if fields is not None else tuple(dict.fromkeys((
            *public_field_names(value_type),
            *(name for name in metadata if name != "enabled" or not include_component_enabled),
        )))

        def default_create(
            context: SceneLoadContext, properties: Mapping[str, object]
        ) -> PersistedType:
            del context, properties
            try:
                return value_type()
            except TypeError as error:
                raise SceneFormatError(
                    f"{value_type.__name__} needs a registered scene factory"
                ) from error

        def default_dump(
            value: PersistedType, context: SceneSaveContext
        ) -> Mapping[str, object]:
            dumped = {
                field: context.encode(getattr(value, field))
                for field in field_names
            }
            if include_component_enabled:
                dumped["enabled"] = bool(
                    getattr(value, "enabled", True)
                )
            return dumped

        def default_restore(
            value: PersistedType,
            properties: Mapping[str, object],
            context: SceneLoadContext,
        ) -> None:
            allowed = set(field_names)
            if include_component_enabled:
                allowed.add("enabled")
            unknown = set(properties) - allowed
            # Scripts tolerate fields that were removed from their source.
            if unknown and not issubclass(value_type, Script):
                raise SceneFormatError(
                    f"unknown properties for {name!r}: {sorted(unknown)}"
                )
            for field in field_names:
                if field in properties:
                    setattr(value, field, context.decode(properties[field]))
            if include_component_enabled:
                value.enabled = bool(properties.get("enabled", True))  # type: ignore[attr-defined]

        return _TypeCodec(
            name,
            value_type,
            create or default_create,
            dump or default_dump,
            restore if restore is not None else default_restore,
            field_names,
        )


def _empty_object(value_type: type[GameObject]) -> CreateFunction[GameObject]:
    def create(
        context: SceneLoadContext, properties: Mapping[str, object]
    ) -> GameObject:
        del context, properties
        value = value_type.__new__(value_type)
        GameObject.__init__(value)
        return value

    return create


def _style(renderer: Any) -> dict[str, object]:
    return {
        "fill": renderer.fill_color,
        "outline": renderer.outline_color,
        "outline_thickness": renderer.outline_thickness,
        "fill_visible": renderer.fill_visible,
        "outline_visible": renderer.outline_visible,
        "opacity": renderer.opacity,
    }


def _renderer_common(renderer: Renderer) -> dict[str, object]:
    return {
        "visible": renderer.visible,
        "screen_space": renderer.screen_space,
        "enabled": renderer.enabled,
    }


def _number(
    properties: Mapping[str, object],
    name: str,
    default: float | None = None,
) -> float:
    value = properties.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SceneFormatError(f"{name} must be a number")
    return float(value)


def _integer(
    properties: Mapping[str, object],
    name: str,
    default: int | None = None,
) -> int:
    value = properties.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SceneFormatError(f"{name} must be an integer")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise SceneFormatError(f"{name} must be a string or null")


def _font(
        context: SceneLoadContext,
        value: object,
        name: str = "font",
) -> Font | None:
    path = _optional_string(value, name)
    if path is None:
        return None
    try:
        return context.game.resources.load_font(
            context.resource_path(path)
        )
    except (OSError, ValueError) as error:
        raise SceneFormatError(f"invalid {name}: {error}") from error


def _color(value: object, name: str, *, optional: bool = False) -> Color | None:
    if value is None and optional:
        return None
    if (
        not isinstance(value, (list, tuple))
        or len(value) not in (3, 4)
        or not all(
            isinstance(channel, int)
            and not isinstance(channel, bool)
            and 0 <= channel <= 255
            for channel in value
        )
    ):
        raise SceneFormatError(f"{name} must be an RGB or RGBA color")
    return cast(Color, tuple(value))


def _scrollbar_style(record: Mapping[str, object]) -> ScrollbarStyle:
    defaults = ScrollbarStyle()
    width = _number(record, "scrollbar_width", defaults.width)
    minimum_thumb = _number(
        record,
        "minimum_thumb",
        defaults.minimum_thumb,
    )
    if width <= 0 or minimum_thumb <= 0:
        raise SceneFormatError(
            "GUI scrollbar width and minimum thumb must be positive"
        )
    return ScrollbarStyle(
        track=cast(
            Color,
            _color(
                record.get("scrollbar_track", defaults.track),
                "GUI scrollbar track",
            ),
        ),
        thumb=cast(
            Color,
            _color(
                record.get("scrollbar_thumb", defaults.thumb),
                "GUI scrollbar thumb",
            ),
        ),
        width=width,
        minimum_thumb=minimum_thumb,
    )


def _point(value: object, name: str) -> tuple[float, float]:
    if (
        not isinstance(value, (list, tuple, Vec2))
        or len(value) != 2
        or not all(
            isinstance(coordinate, (int, float))
            and not isinstance(coordinate, bool)
            for coordinate in value
        )
    ):
        raise SceneFormatError(f"{name} must contain two numbers")
    return float(value[0]), float(value[1])


def _dash_pattern(
    value: object,
    name: str = "dash_pattern",
) -> tuple[float, float] | None:
    if value is None:
        return None
    pattern = _point(value, name)
    if (
        not isfinite(pattern[0])
        or not isfinite(pattern[1])
        or pattern[0] <= 0
        or pattern[1] <= 0
    ):
        raise SceneFormatError(
            f"{name} must contain positive dash and gap lengths"
        )
    return pattern


def _points(value: object, name: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)):
        raise SceneFormatError(f"{name} must be a list of points")
    return tuple(_point(point, name) for point in value)


def _gui_padding(
    value: object,
    name: str,
) -> tuple[float, float, float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or not all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            for item in value
        )
    ):
        raise SceneFormatError(f"{name} must contain four numbers")
    left, bottom, right, top = value
    return (
        float(left),
        float(bottom),
        float(right),
        float(top),
    )


def _shape_style(properties: Mapping[str, object]) -> ShapeStyle:
    return ShapeStyle(
        fill=_color(properties.get("fill"), "fill", optional=True),
        outline=_color(properties.get("outline"), "outline", optional=True),
        outline_thickness=_number(properties, "outline_thickness", 1),
    )


def _body_type(properties: Mapping[str, object]) -> BodyType:
    name = properties.get("body_type", "dynamic")
    if not isinstance(name, str):
        raise SceneFormatError("body_type must be a string")
    try:
        return BodyType[name.upper()]
    except KeyError:
        raise SceneFormatError(f"unknown body type: {name}") from None


def _register_builtins(registry: SceneTypeRegistry) -> None:
    registry.register_object(
        "g.game_object", GameObject, create=_empty_object(GameObject),
        dump=lambda value, context: {},
        restore=lambda value, properties, context: None,
    )
    registry.register_component(
        "g.networked_game_object",
        NetworkedGameObject,
        category="Network",
        display_name="Networked Game Object",
        builtin=True,
    )
    registry.register_component(
        "g.network_client",
        NetworkClient,
        category="Network",
        display_name="Network Client",
        builtin=True,
    )
    registry.register_component(
        "g.area", Area, category="Physics", display_name="Area", builtin=True,
        create=lambda context, p: Area(_integer(p, "collider_index", 0), enabled=bool(p.get("enabled", True))),
        dump=lambda value, context: {"collider_index": value.collider_index, "enabled": value.enabled},
        restore=lambda value, p, context: None,
    )
    def light_common(
        value: PointLight2D | SpotLight2D,
    ) -> dict[str, JsonValue]:
        return {
            "color": list(value.color),
            "intensity": value.intensity,
            "falloff": value.falloff,
            "casts_shadows": value.casts_shadows,
            "shadow_mask": value.shadow_mask,
            "enabled": value.enabled,
        }

    registry.register_component(
        "g.point_light_2d",
        PointLight2D,
        category="Lighting",
        display_name="Point Light 2D",
        builtin=True,
        create=lambda context, p: PointLight2D(
            _number(p, "radius", 256),
            color=cast(Color, _color(p.get("color", [255, 255, 255, 255]), "light color")),
            intensity=_number(p, "intensity", 1),
            falloff=_number(p, "falloff", 1),
            casts_shadows=bool(p.get("casts_shadows", False)),
            shadow_mask=_integer(p, "shadow_mask", 0xFFFFFFFF),
            enabled=bool(p.get("enabled", True)),
        ),
        dump=lambda value, context: {
            "radius": value.radius,
            **light_common(value),
        },
    )
    registry.register_component(
        "g.spot_light_2d",
        SpotLight2D,
        category="Lighting",
        display_name="Spot Light 2D",
        builtin=True,
        create=lambda context, p: SpotLight2D(
            _number(p, "range", 256),
            angle=_number(p, "angle", 50),
            inner_angle=_number(p, "inner_angle", 40),
            color=cast(Color, _color(p.get("color", [255, 255, 255, 255]), "light color")),
            intensity=_number(p, "intensity", 1),
            falloff=_number(p, "falloff", 1),
            casts_shadows=bool(p.get("casts_shadows", False)),
            shadow_mask=_integer(p, "shadow_mask", 0xFFFFFFFF),
            enabled=bool(p.get("enabled", True)),
        ),
        dump=lambda value, context: {
            "range": value.range,
            "angle": value.angle,
            "inner_angle": value.inner_angle,
            **light_common(value),
        },
    )
    registry.register_component(
        "g.shadow_occluder_2d",
        ShadowOccluder2D,
        category="Lighting",
        display_name="Shadow Occluder 2D",
        builtin=True,
        create=lambda context, p: ShadowOccluder2D(
            cast(
                list[list[float]],
                p.get(
                    "points",
                    [list(point) for point in ShadowOccluder2D().points],
                ),
            ),
            occlusion_mask=_integer(p, "occlusion_mask", 0xFFFFFFFF),
            enabled=bool(p.get("enabled", True)),
        ),
        dump=lambda value, context: {
            "points": [list(point) for point in value.points],
            "occlusion_mask": value.occlusion_mask,
            "enabled": value.enabled,
        },
    )

    def restore_visual(
        value: Renderer,
        properties: Mapping[str, object],
        context: SceneLoadContext,
    ) -> None:
        del context
        if hasattr(value, "opacity"):
            setattr(value, "opacity", _integer(properties, "opacity", 255))
        value.enabled = bool(properties.get("enabled", True))

    def restore_shape(
        value: CircleRenderer
        | RectRenderer
        | TriangleRenderer
        | PolygonRenderer,
        properties: Mapping[str, object],
        context: SceneLoadContext,
    ) -> None:
        restore_visual(value, properties, context)
        value.fill_visible = bool(properties.get("fill_visible", True))
        value.outline_visible = bool(
            properties.get("outline_visible", True)
        )

    registry.register_component(
        "g.circle_renderer",
        CircleRenderer,
        category="Renderers", display_name="Circle Renderer", builtin=True,
        create=lambda context, p: CircleRenderer(
            _number(p, "radius"),
            style=_shape_style(p),
            visible=bool(p.get("visible", True)),
            screen_space=bool(p.get("screen_space", False)),
        ),
        dump=lambda value, context: {
            "radius": value.radius,
            **_style(value),
            **_renderer_common(value),
        },
        restore=restore_shape,
    )
    registry.register_component(
        "g.rect_renderer",
        RectRenderer,
        category="Renderers", display_name="Rect Renderer", builtin=True,
        create=lambda context, p: RectRenderer(
            _number(p, "width"),
            _number(p, "height"),
            style=_shape_style(p),
            visible=bool(p.get("visible", True)),
            screen_space=bool(p.get("screen_space", False)),
        ),
        dump=lambda value, context: {
            "width": value.width,
            "height": value.height,
            **_style(value),
            **_renderer_common(value),
        },
        restore=restore_shape,
    )

    def _load_sprite_image(
        context: SceneLoadContext,
        path: Path,
        region: object,
    ) -> Image:
        image = context.game.resources.load_image(path)
        if region is None:
            return image
        if (
            not isinstance(region, list)
            or len(region) != 4
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in region
            )
        ):
            raise SceneFormatError(
                "sprite image region must contain four integers"
            )
        try:
            return SpriteSheetRegion(image, *region)
        except (TypeError, ValueError) as error:
            raise SceneFormatError(str(error)) from error

    def _dump_sprite_image(
        image: Image | Animation | None,
        context: SceneSaveContext,
    ) -> dict[str, object]:
        if image is None:
            return {"image": None}
        result: dict[str, object] = {
            "image": context.resource_path(image)
        }
        if isinstance(image, SpriteSheetRegion):
            result["region"] = [
                image.x,
                image.y,
                image.width,
                image.height,
            ]
        return result

    def create_sprite_renderer(
        context: SceneLoadContext,
        properties: Mapping[str, object],
    ) -> SpriteRenderer:
        image = properties.get("image")
        region = properties.get("region")
        if properties.get("animated", False):
            raise SceneFormatError("Sprite Renderer no longer plays animations; use Sprite Animator")
        if region is not None and (not isinstance(image, str) or not image):
            raise SceneFormatError(
                "sprite image region requires an image asset"
            )
        for name in ("flip_x", "flip_y"):
            if not isinstance(properties.get(name, False), bool):
                raise SceneFormatError(f"Sprite Renderer {name} must be a boolean")
        source: Image | None = None
        if image is not None and image != "" and (
            not context.allow_missing_resources
            or context.resource_exists(image)
        ):
            path = context.resource_path(image)
            source = _load_sprite_image(
                context,
                path,
                region,
            )
        return SpriteRenderer(
            source,
            visible=bool(properties.get("visible", True)),
            screen_space=bool(properties.get("screen_space", False)),
            flip_x=bool(properties.get("flip_x", False)),
            flip_y=bool(properties.get("flip_y", False)),
        )

    registry.register_component(
        "g.sprite_renderer",
        SpriteRenderer,
        category="Renderers", display_name="Sprite Renderer", builtin=True,
        create=create_sprite_renderer,
        dump=lambda value, context: {
            **_dump_sprite_image(
                None
                if value.image is not None
                and value.image._filename is None
                and value.owner is not None
                and value.owner.get_component(SpriteAnimator) is not None
                else value.image,
                context,
            ),
            "opacity": value.opacity,
            "flip_x": value.flip_x,
            "flip_y": value.flip_y,
            **_renderer_common(value),
        },
        restore=restore_visual,
    )

    def create_tile_renderer(context: SceneLoadContext, p: Mapping[str, object]) -> TileRenderer:
        raw = p.get("patches", [])
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise SceneFormatError("tile renderer patches must be an array of tilemaps")
        serializer = SceneSerializer()
        return TileRenderer(
            tuple(serializer._restore_tile_layer(None, patch, context, detached=True) for patch in raw),
            visible=bool(p.get("visible", True)),
            enabled=bool(p.get("enabled", True)),
            screen_space=bool(p.get("screen_space", False)),
            opacity=_integer(p, "opacity", 255),
        )

    registry.register_component(
        "g.tile_renderer", TileRenderer,
        category="Renderers", display_name="Tile Renderer", builtin=True,
        create=create_tile_renderer,
        dump=lambda value, context: {
            "patches": [{
                "id": patch.id, "name": patch.name,
                "visible": patch.visible, "enabled": patch.enabled,
                **SceneSerializer()._dump_tile_layer(patch, context),
            } for patch in value.patches],
            "opacity": value.opacity, **_renderer_common(value),
        },
        restore=lambda value, p, context: None,
    )

    def create_repeating_image(
        context: SceneLoadContext, p: Mapping[str, object],
    ) -> RepeatingImageRenderer:
        path = p.get("image")
        if path is not None and (not isinstance(path, str) or not path):
            raise SceneFormatError("background image must be an asset path or null")
        return RepeatingImageRenderer(
            context.game.resources.load_image(context.asset_root / path) if isinstance(path, str) else None,
            tile_size=_point(context.decode(p.get("tile_size", {"$vec2": [64, 64]})), "tile_size"),
            repeat_x=bool(p.get("repeat_x", True)), repeat_y=bool(p.get("repeat_y", True)),
            offset=_point(context.decode(p.get("offset", {"$vec2": [0, 0]})), "offset"),
            parallax=_point(context.decode(p.get("parallax", {"$vec2": [0, 0]})), "parallax"),
            scroll_speed=_point(context.decode(p.get("scroll_speed", {"$vec2": [0, 0]})), "scroll_speed"),
            opacity=_integer(p, "opacity", 255), visible=bool(p.get("visible", True)),
            enabled=bool(p.get("enabled", True)),
        )

    registry.register_component(
        "g.repeating_image_renderer", RepeatingImageRenderer,
        category="Renderers", display_name="Repeating Image Renderer", builtin=True,
        create=create_repeating_image,
        dump=lambda value, context: {
            "image": context.resource_path(value.image) if value.image is not None else None,
            "tile_size": context.encode(value.tile_size),
            "repeat_x": value.repeat_x, "repeat_y": value.repeat_y,
            "offset": context.encode(value.offset), "parallax": context.encode(value.parallax),
            "scroll_speed": context.encode(value.scroll_speed),
            "opacity": value.opacity, "visible": value.visible, "enabled": value.enabled,
        },
        restore=lambda value, p, context: None,
    )

    def create_sprite_animator(
        context: SceneLoadContext,
        properties: Mapping[str, object],
    ) -> SpriteAnimator:
        states_value = properties.get("states")
        if not isinstance(states_value, list) or not states_value:
            raise SceneFormatError(
                "sprite animator states must be a non-empty list"
            )
        states: dict[str, SpriteAnimation] = {}
        for state_value in states_value:
            if not isinstance(state_value, dict):
                raise SceneFormatError(
                    "sprite animation state must be an object"
                )
            name = state_value.get("name")
            if not isinstance(name, str) or not name:
                raise SceneFormatError(
                    "sprite animation state name must be a non-empty string"
                )
            if name in states:
                raise SceneFormatError(
                    f"duplicate sprite animation state: {name!r}"
                )
            loop = state_value.get("loop")
            if "loop" in state_value and not isinstance(loop, bool):
                raise SceneFormatError("sprite animation loop must be a boolean")
            if "image" in state_value:
                if "frames" in state_value:
                    raise SceneFormatError("sprite state must use either an image asset or frames")
                path = state_value["image"]
                region = state_value.get("region")
                animated = state_value.get("animated", False)
                if not isinstance(animated, bool):
                    raise SceneFormatError("sprite state animated must be a boolean")
                if region is not None and (
                    animated or not isinstance(path, str) or not path
                ):
                    raise SceneFormatError(
                        "sprite state region requires a static image asset"
                    )
                durations_value = state_value.get("durations")
                durations: list[float] | None = None
                if durations_value is not None:
                    if not animated or not state_value.get("image") or not isinstance(durations_value, list) or not durations_value:
                        raise SceneFormatError("duration overrides require an animation asset")
                    durations = [_number({"duration": duration}, "duration") for duration in durations_value]
                if path is None:
                    animation = SpriteAnimation.from_image(None)
                elif isinstance(path, str) and path:
                    resolved = context.resource_path(path)
                    try:
                        animation = (
                            SpriteAnimation.from_animation(
                                context.game.resources.load_animation(resolved),
                                durations=durations,
                            )
                            if animated else SpriteAnimation.from_image(
                                _load_sprite_image(context, resolved, region)
                            )
                        )
                    except ValueError as error:
                        raise SceneFormatError(str(error)) from error
                else:
                    raise SceneFormatError("sprite state image must be an asset path or null")
                states[name] = SpriteAnimation(
                    animation.frames,
                    loop=loop if isinstance(loop, bool) else animation.loop,
                    source=animation.source,
                )
                continue
            frames_value = state_value.get("frames")
            if not isinstance(frames_value, list) or not frames_value:
                raise SceneFormatError(
                    f"sprite animation state {name!r} must contain frames"
                )
            frames: list[SpriteAnimationFrame] = []
            for frame_value in frames_value:
                if not isinstance(frame_value, dict):
                    raise SceneFormatError(
                        "sprite animation frame must be an object"
                    )
                image = frame_value.get("image")
                region = frame_value.get("region")
                if "image" not in frame_value or (image is not None and (not isinstance(image, str) or not image)):
                    raise SceneFormatError(
                        "sprite animation frame image must be a "
                        "non-empty string or null"
                    )
                if region is not None and image is None:
                    raise SceneFormatError(
                        "sprite animation frame region requires an image asset"
                    )
                duration = _number(frame_value, "duration")
                try:
                    frame = SpriteAnimationFrame(
                        _load_sprite_image(
                            context,
                            context.resource_path(image),
                            region,
                        )
                        if isinstance(image, str) else None,
                        duration,
                    )
                except ValueError as error:
                    raise SceneFormatError(str(error)) from error
                frames.append(frame)
            states[name] = SpriteAnimation(
                tuple(frames),
                loop=loop if isinstance(loop, bool) else True,
            )
        initial_state = properties.get("initial_state")
        if not isinstance(initial_state, str) or not initial_state:
            raise SceneFormatError(
                "sprite animator initial_state must be a non-empty string"
            )
        try:
            return SpriteAnimator(
                states,
                initial_state,
                autoplay=bool(properties.get("autoplay", True)),
            )
        except ValueError as error:
            raise SceneFormatError(str(error)) from error

    def dump_sprite_animator(
        value: SpriteAnimator,
        context: SceneSaveContext,
    ) -> Mapping[str, object]:
        states: list[dict[str, object]] = []
        for name, animation in value.states.items():
            state: dict[str, object] = {"name": name, "loop": animation.loop}
            if animation.source is not None:
                state.update(_dump_sprite_image(animation.source, context))
                state["animated"] = isinstance(animation.source, Animation)
                if isinstance(animation.source, Animation):
                    state["durations"] = [frame.duration for frame in animation.frames]
            else:
                state["frames"] = [
                    {
                        **_dump_sprite_image(frame.image, context),
                        "duration": frame.duration,
                    }
                    for frame in animation.frames
                ]
            states.append(state)
        return {
            "initial_state": value.initial_state,
            "autoplay": value.autoplay,
            "states": states,
            "enabled": value.enabled,
        }

    def restore_sprite_animator(
        value: SpriteAnimator,
        properties: Mapping[str, object],
        context: SceneLoadContext,
    ) -> None:
        del context
        value.enabled = bool(properties.get("enabled", True))

    registry.register_component(
        "g.sprite_animator",
        SpriteAnimator,
        category="Animation", display_name="Sprite Animator", builtin=True,
        create=create_sprite_animator,
        dump=dump_sprite_animator,
        restore=restore_sprite_animator,
        fields=("initial_state", "autoplay", "states"),
    )
    def create_particle_effect(
        context: SceneLoadContext,
        properties: Mapping[str, object],
    ) -> ParticleEffect:
        effect = properties.get("effect", "")
        if not isinstance(effect, str):
            raise SceneFormatError(
                "particle effect must be a project-relative asset path"
            )
        definition = ParticleEffectDefinition()
        images: dict[str, Image] = {}
        if effect:
            source = context.resource_path(effect)
            try:
                definition = ParticleEffectDefinition.read(source)
            except ParticleFormatError as error:
                raise SceneFormatError(str(error)) from error
            for emitter in definition.emitters:
                image = emitter.appearance.image
                if image is not None:
                    images[emitter.name] = Image(
                        context.resource_path(image)
                    )
        seed = properties.get("seed", 0)
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise SceneFormatError("particle seed must be an integer")
        return ParticleEffect(
            definition,
            effect=effect,
            images=images,
            autoplay=bool(properties.get("autoplay", True)),
            simulation_space=str(
                properties.get("simulation_space", "world")
            ),
            seed=seed,
            visible=bool(properties.get("visible", True)),
            enabled=bool(properties.get("enabled", True)),
            screen_space=bool(properties.get("screen_space", False)),
        )

    registry.register_component(
        "g.particle_effect",
        ParticleEffect,
        category="Renderers",
        display_name="Particle Effect",
        builtin=True,
        create=create_particle_effect,
        dump=lambda value, context: {
            "effect": value.effect,
            "autoplay": value.autoplay,
            "simulation_space": value.simulation_space,
            "seed": value.seed,
            **_renderer_common(value),
        },
        restore=restore_visual,
        fields=(
            "effect", "autoplay", "simulation_space", "seed",
            "visible", "screen_space",
        ),
    )
    registry.register_component(
        "g.text_renderer",
        TextRenderer,
        category="Renderers", display_name="Text Renderer", builtin=True,
        create=lambda context, p: TextRenderer(
            str(p.get("text", "")),
            _font(context, p.get("font")),
            _number(p, "font_size", 12),
            cast(Color, _color(p.get("color", [255, 255, 255]), "color")),
            visible=bool(p.get("visible", True)),
            screen_space=bool(p.get("screen_space", False)),
            alignment=str(p.get("alignment", "center")),
        ),
        dump=lambda value, context: {
            "text": value.text,
            "font": (
                None
                if value.font is None
                else context.resource_path(value.font)
            ),
            "font_size": value.font_size,
            "color": value.color,
            "opacity": value.opacity,
            "alignment": value.alignment,
            **_renderer_common(value),
        },
        restore=restore_visual,
    )
    registry.register_component(
        "g.line_renderer",
        LineRenderer,
        category="Renderers", display_name="Line Renderer", builtin=True,
        create=lambda context, p: LineRenderer(
            _point(p.get("start"), "start"),
            _point(p.get("end"), "end"),
            cast(Color, _color(p.get("color", [255, 255, 255]), "color")),
            _number(p, "thickness", 1),
            dash_pattern=_dash_pattern(p.get("dash_pattern")),
            visible=bool(p.get("visible", True)),
            screen_space=bool(p.get("screen_space", False)),
        ),
        dump=lambda value, context: {
            "start": value.start,
            "end": value.end,
            "color": value.color,
            "thickness": value.thickness,
            "dash_pattern": value.dash_pattern,
            "opacity": value.opacity,
            **_renderer_common(value),
        },
        restore=restore_visual,
    )
    registry.register_component(
        "g.trail_renderer",
        TrailRenderer,
        category="Renderers",
        display_name="Trail Renderer",
        builtin=True,
        create=lambda context, p: TrailRenderer(
            image=(
                context.game.resources.load_image(
                    context.resource_path(path)
                )
                if isinstance((path := p.get("image")), str)
                else None
            ),
            emitting=bool(p.get("emitting", True)),
            offset=_point(p.get("offset", [0, 0]), "offset"),
            width=_number(p, "width", 5),
            minimum_distance=_number(p, "minimum_distance", 3),
            lifetime=_number(p, "lifetime", 12),
            color=cast(
                Color,
                _color(p.get("color", [40, 40, 40, 180]), "color"),
            ),
            fade=bool(p.get("fade", True)),
            max_segments=_integer(p, "max_segments", 2000),
            visible=bool(p.get("visible", True)),
            enabled=bool(p.get("enabled", True)),
            screen_space=bool(p.get("screen_space", False)),
        ),
        dump=lambda value, context: {
            "image": (
                context.resource_path(value.image)
                if value.image is not None
                else None
            ),
            "emitting": value.emitting,
            "offset": list(value.offset),
            "width": value.width,
            "minimum_distance": value.minimum_distance,
            "lifetime": value.lifetime,
            "color": list(value.color),
            "fade": value.fade,
            "max_segments": value.max_segments,
            **_renderer_common(value),
        },
        restore=restore_visual,
        fields=(
            "image", "emitting", "offset", "width", "minimum_distance",
            "lifetime", "color", "fade", "max_segments",
            "visible", "screen_space",
        ),
    )

    def triangle(
        context: SceneLoadContext, properties: Mapping[str, object]
    ) -> TriangleRenderer:
        del context
        points = _points(properties.get("points"), "points")
        if len(points) != 3:
            raise SceneFormatError("triangle points must contain three points")
        return TriangleRenderer(
            (points[0], points[1], points[2]),
            style=_shape_style(properties),
            visible=bool(properties.get("visible", True)),
            screen_space=bool(properties.get("screen_space", False)),
        )

    registry.register_component(
        "g.triangle_renderer",
        TriangleRenderer,
        category="Renderers", display_name="Triangle Renderer", builtin=True,
        create=triangle,
        dump=lambda value, context: {
            "points": value.points,
            **_style(value),
            **_renderer_common(value),
        },
        restore=restore_shape,
    )
    registry.register_component(
        "g.polygon_renderer",
        PolygonRenderer,
        category="Renderers", display_name="Polygon Renderer", builtin=True,
        create=lambda context, p: PolygonRenderer(
            _points(p.get("points"), "points"),
            style=_shape_style(p),
            visible=bool(p.get("visible", True)),
            screen_space=bool(p.get("screen_space", False)),
        ),
        dump=lambda value, context: {
            "points": value.points,
            **_style(value),
            **_renderer_common(value),
        },
        restore=restore_shape,
    )
    registry.register_component(
        "g.collider",
        Collider,
        category="Physics", display_name="Collider", builtin=True,
        create=lambda context, p: Collider(
            shape=str(p.get("shape", "box")),
            width=_number(p, "width", 64),
            height=_number(p, "height", 64),
            radius=_number(p, "radius", 32),
            radius_x=_number(p, "radius_x", 32),
            radius_y=_number(p, "radius_y", 16),
            points=_points(p.get("points", [list(point) for point in Collider.DEFAULT_POINTS]), "points"),
            offset_x=_number(p, "offset_x", 0.0),
            offset_y=_number(p, "offset_y", 0.0),
            density=_number(p, "density", 1.0),
            friction=_number(p, "friction", 0.7),
            elasticity=_number(p, "elasticity", 0.0),
            mode=ColliderMode(str(p.get("mode", "solid"))),
            collision_type=_integer(p, "collision_type", 0),
            categories=_integer(p, "categories", 0xFFFFFFFF),
            mask=_integer(p, "mask", 0xFFFFFFFF),
            group=_integer(p, "group", 0),
            override_collision_filtering=(
                bool(p["override_collision_filtering"]) if "override_collision_filtering" in p else None
            ),
            enabled=bool(p.get("enabled", True)),
        ),
        dump=lambda value, context: {
            "shape": value.shape,
            "width": value.width,
            "height": value.height,
            "radius": value.radius,
            "radius_x": value.radius_x,
            "radius_y": value.radius_y,
            "points": [list(point) for point in value.points],
            "offset_x": value.offset_x,
            "offset_y": value.offset_y,
            "density": value.density,
            "friction": value.friction,
            "elasticity": value.elasticity,
            "mode": value.mode.value,
            "collision_type": value.collision_type,
            "categories": value.categories,
            "mask": value.mask,
            "group": value.group,
            "override_collision_filtering": value.override_collision_filtering,
            "enabled": value.enabled,
        },
        restore=lambda value, p, context: None,
    )
    registry.register_component(
        "g.physics_body",
        PhysicsBody,
        category="Physics", display_name="Physics Body", builtin=True,
        create=lambda context, p: PhysicsBody(
            _body_type(p),
            velocity=_point(context.decode(p.get("velocity", {"$vec2": [0, 0]})), "velocity"),
            angular_velocity=_number(p, "angular_velocity", 0),
            gravity_scale=_number(p, "gravity_scale", 1),
            fixed_rotation=bool(p.get("fixed_rotation", False)),
            categories=_integer(p, "categories", 0xFFFFFFFF),
            mask=_integer(p, "mask", 0xFFFFFFFF),
            group=_integer(p, "group", 0),
            enabled=bool(p.get("enabled", True)),
        ),
        dump=lambda value, context: {
            "body_type": value.body_type.name.lower(),
            "velocity": context.encode(value.velocity),
            "angular_velocity": value.angular_velocity,
            "gravity_scale": value.gravity_scale,
            "fixed_rotation": value.fixed_rotation,
            "categories": value.categories,
            "mask": value.mask,
            "group": value.group,
            "enabled": value.enabled,
        },
        restore=lambda value, p, context: None,
    )
    registry.register_component(
        "g.top_down_motor",
        TopDownMotor,
        category="Movement", display_name="Top-Down Motor", builtin=True,
        create=lambda context, p: TopDownMotor(
            _number(p, "speed", 200),
            acceleration=_number(p, "acceleration", 1200),
            deceleration=_number(p, "deceleration", 1600),
            enabled=bool(p.get("enabled", True)),
        ),
        dump=lambda value, context: {
            "speed": value.speed,
            "acceleration": value.acceleration,
            "deceleration": value.deceleration,
            "enabled": value.enabled,
        },
        restore=lambda value, p, context: None,
    )
    registry.register_component(
        "g.platformer_motor",
        PlatformerMotor,
        category="Movement", display_name="Platformer Motor", builtin=True,
        create=lambda context, p: PlatformerMotor(
            _number(p, "speed", 240),
            acceleration=_number(p, "acceleration", 1800),
            air_acceleration=_number(p, "air_acceleration", 900),
            deceleration=_number(p, "deceleration", 2200),
            jump_speed=_number(p, "jump_speed", 520),
            max_fall_speed=_number(p, "max_fall_speed", 900),
            gravity_scale=_number(p, "gravity_scale", 1),
            max_slope=_number(p, "max_slope", 50),
            ground_snap_distance=_number(p, "ground_snap_distance", 8),
            coyote_time=_number(p, "coyote_time", 0.1),
            jump_buffer_time=_number(p, "jump_buffer_time", 0.1),
            allow_double_jump=bool(p.get("allow_double_jump", False)),
            enabled=bool(p.get("enabled", True)),
        ),
        dump=lambda value, context: {
            "speed": value.speed,
            "acceleration": value.acceleration,
            "air_acceleration": value.air_acceleration,
            "deceleration": value.deceleration,
            "jump_speed": value.jump_speed,
            "max_fall_speed": value.max_fall_speed,
            "gravity_scale": value.gravity_scale,
            "max_slope": value.max_slope,
            "ground_snap_distance": value.ground_snap_distance,
            "coyote_time": value.coyote_time,
            "jump_buffer_time": value.jump_buffer_time,
            "allow_double_jump": value.allow_double_jump,
            "enabled": value.enabled,
        },
        restore=lambda value, p, context: None,
    )
    registry.register_component(
        "g.camera_follow",
        CameraFollow,
        category="Camera", display_name="Camera Follow", builtin=True,
    )
    def create_spline(
        context: SceneLoadContext,
        properties: Mapping[str, object],
    ) -> Spline:
        del context
        closed = properties.get("closed", False)
        if not isinstance(closed, bool):
            raise SceneFormatError("spline closed must be a boolean")
        try:
            points = _points(
                properties.get("points", Spline.DEFAULT_POINTS),
                "spline points",
            )
            return Spline(
                points,
                closed=closed,
                tolerance=_number(properties, "tolerance", 0.5),
                enabled=bool(properties.get("enabled", True)),
            )
        except (TypeError, ValueError) as error:
            raise SceneFormatError(f"invalid spline: {error}") from error

    registry.register_component(
        "g.spline",
        Spline,
        fields=("points", "closed", "tolerance"),
        category="Movement",
        display_name="Spline",
        builtin=True,
        create=create_spline,
        dump=lambda value, context: {
            "points": context.encode(value.points),
            "closed": value.closed,
            "tolerance": value.tolerance,
            "enabled": value.enabled,
        },
        restore=lambda value, properties, context: None,
    )


scene_types = SceneTypeRegistry()
_register_builtins(scene_types)
scene_object = scene_types.object
scene_component = scene_types.component


class SceneSerializer:
    """Capture, save, load, or restore complete scenes using a type registry.

    Attributes:
        registry: Type registry used for object and component codecs.
    """

    def __init__(self, registry: SceneTypeRegistry | None = None) -> None:
        """Create a serializer using ``registry`` or the shared registry.

        Args:
            registry: Custom registry, or ``None`` to use :data:`scene_types`.
        """
        self.registry = registry or scene_types

    def capture(
        self,
        scene: Scene,
        *,
        asset_root: str | Path,
        include_runtime_state: bool = False,
    ) -> SceneDocument:
        """Capture a scene into a serializable document.

        Args:
            scene: Scene to capture.
            asset_root: Root against which resource paths are stored.
            include_runtime_state: Include checkpoint state when possible.

        Returns:
            Scene document containing scene settings, layers, and UI views.

        Raises:
            SceneFormatError: If a scene element is unsupported, a resource
                cannot be represented relative to the root, or active callback
                timers prevent checkpoint capture.

        Game objects without scene IDs receive IDs during capture.
        """
        context = SceneSaveContext(Path(asset_root).resolve(), include_runtime_state)
        for node in scene.walk():
            if isinstance(node, GameObject) and node.scene_id is None:
                node.scene_id = str(uuid4())
        settings: dict[str, JsonValue] = {
            "background": context.encode(scene.bg_color),
            "ambient_light": context.encode(scene.ambient_light),
            "size": [scene.width, scene.height],
            "grid_cell_size": scene.grid.cell_size,
            "camera": {
                "position": context.encode(scene.camera.position),
                "zoom": scene.camera.zoom,
                "rotation": scene.camera.rotation,
            },
            "physics": {
                "gravity": list(scene.physics.gravity),
                "damping": scene.physics.damping,
            },
            "presentation": {
                "scaling": scene.game.presentation.scaling,
                "matte_color": context.encode(
                    scene.game.presentation.matte_color
                ),
                "resizable": scene.game.presentation.resizable,
                "fullscreen": scene.game.presentation.fullscreen,
            },
        }
        layers: list[dict[str, JsonValue]] = []
        for layer in scene.layers:
            common: dict[str, JsonValue] = {
                "id": layer.id,
                "name": layer.name,
                "visible": layer.visible,
                "enabled": layer.enabled,
                "default": layer is scene.default_layer,
            }
            if isinstance(layer, ObjectLayer):
                common.update(
                    {
                        "type": "object",
                        "objects": [
                            self._dump_object(value, context)
                            for value in layer.objects
                        ],
                    }
                )
            elif isinstance(layer, TileLayer):
                common.update(self._dump_tile_layer(layer, context))
            else:
                raise SceneFormatError(
                    f"unsupported scene layer type: {type(layer).__name__}"
                )
            layers.append(common)
        ui_views = [
            self._dump_ui_view(
                name,
                view,
                context,
                startup=view is scene.gui.default,
            )
            for name, view in scene.gui.view_items
        ]
        if include_runtime_state:
            if any(not timer.done for timer in (*scene.time._timers, *scene.time._pending)):
                raise SceneFormatError(
                    "Active callback timers cannot be checkpointed; store timer progress in component state instead"
                )
            settings["checkpoint"] = {
                "version": 1,
                "elapsed": scene.time.elapsed,
                "original_scene": (
                    context.encode(scene._source_document.to_dict()) if scene._source_document is not None else None
                ),
            }
        return SceneDocument(
            settings,
            layers=layers,
            ui_views=ui_views,
        )

    def save(
        self,
        scene: Scene,
        filename: str | Path,
        *,
        asset_root: str | Path | None = None,
        include_runtime_state: bool = False,
    ) -> None:
        """Capture and write a scene document.

        Args:
            scene: Scene to serialize.
            filename: Destination scene file.
            asset_root: Resource root; defaults to the destination directory.
            include_runtime_state: Include checkpoint state when possible.

        Raises:
            SceneFormatError: If capture or document writing fails.
        """
        path = Path(filename)
        root = Path(asset_root) if asset_root is not None else path.parent
        self.capture(scene, asset_root=root, include_runtime_state=include_runtime_state).write(path)

    def load(
        self,
        game: Any,
        filename: str | Path,
        *,
        asset_root: str | Path | None = None,
    ) -> Scene:
        """Read a scene document and restore it into ``game``.

        Args:
            game: Game receiving the restored scene.
            filename: Scene JSON path.
            asset_root: Resource root; defaults to the project's asset root
                when available, otherwise the scene file's directory.

        Returns:
            Restored scene.

        Raises:
            SceneFormatError: If the file, document, resources, or scene
                records are invalid.
        """
        path = Path(filename)
        root = (
            Path(asset_root) if asset_root is not None
            else game.project.asset_root if game.project is not None
            else path.parent
        )
        return self.restore(game, SceneDocument.read(path), asset_root=root)

    def restore(
        self,
        game: Any,
        document: SceneDocument,
        *,
        asset_root: str | Path,
        allow_missing_resources: bool = False,
        initialize_components: bool = True,
    ) -> Scene:
        """Restore a document into a new scene.

        Args:
            game: Game receiving the restored scene.
            document: Validated scene document.
            asset_root: Root used to resolve serialized resource paths.
            allow_missing_resources: Permit unresolved resource references
                where supported by restoration.
            initialize_components: Whether to initialize restored components.

        Returns:
            Restored scene.

        Raises:
            SceneFormatError: If settings, resources, checkpoint data, or
                serialized elements cannot be restored.
        """
        checkpoint = document.settings.get("checkpoint")
        if checkpoint is not None:
            if not isinstance(checkpoint, dict) or checkpoint.get("version") != 1:
                raise SceneFormatError("unsupported checkpoint version")
            if not initialize_components:
                raise SceneFormatError("checkpoint restoration requires component initialization")
            elapsed = _number(checkpoint, "elapsed", 0)
            if not isfinite(elapsed) or elapsed < 0:
                raise SceneFormatError("checkpoint elapsed time must be finite and non-negative")
            original = checkpoint.get("original_scene")
            if original is not None:
                if not isinstance(original, dict):
                    raise SceneFormatError("checkpoint original_scene must be a scene document")
                SceneDocument.from_dict(original)
        scene = Scene(
            bg_color=_color(document.settings.get("background"), "background", optional=True),
            ambient_light=cast(
                Color,
                _color(
                    document.settings.get("ambient_light", [64, 64, 64, 255]),
                    "ambient light",
                ),
            ),
            grid_cell_size=_integer(document.settings, "grid_cell_size", 32),
            presentation_enabled=game.presentation_enabled,
        )
        previous_root, previous_presentation = game.resources.asset_root, game.presentation
        try:
            return self._restore_scene(
                game, document, scene, asset_root=asset_root,
                allow_missing_resources=allow_missing_resources,
                initialize_components=initialize_components,
            )
        except Exception as error:
            try:
                scene.layers._clear()
            except Exception as cleanup_error:
                raise ExceptionGroup("Scene restore and cleanup failed", [error, cleanup_error]) from error
            finally:
                game.resources.asset_root = previous_root
                game.presentation = previous_presentation
            raise

    def _restore_scene(
        self, game: Any, document: SceneDocument, scene: Scene, *,
        asset_root: str | Path, allow_missing_resources: bool,
        initialize_components: bool,
    ) -> Scene:
        game.resources.asset_root = asset_root
        settings = document.settings
        presentation = settings.get("presentation")
        if presentation is not None:
            if not isinstance(presentation, dict):
                raise SceneFormatError(
                    "scene presentation settings must be an object"
                )
            scaling = presentation.get("scaling", "fit")
            if scaling not in SCALING_MODES:
                raise SceneFormatError(
                    f"unknown presentation scaling mode: {scaling!r}"
                )
            matte_color = _color(
                presentation.get("matte_color", [0, 0, 0, 255]),
                "presentation matte color",
            )
            resizable = presentation.get("resizable", True)
            fullscreen = presentation.get("fullscreen", False)
            if not isinstance(resizable, bool):
                raise SceneFormatError(
                    "presentation resizable must be a boolean"
                )
            if not isinstance(fullscreen, bool):
                raise SceneFormatError(
                    "presentation fullscreen must be a boolean"
                )
            game.presentation = PresentationSettings(
                cast(ScalingMode, scaling),
                cast(Color, matte_color),
                resizable,
                fullscreen,
            )
        scene._bind(game)
        scene._components_ready_enabled = False
        physics = settings.get("physics")
        if physics is not None:
            if not isinstance(physics, dict):
                raise SceneFormatError(
                    "scene physics settings must be an object"
                )
            scene.physics.gravity = _point(
                physics.get("gravity", (0, 0)),
                "physics gravity",
            )
            damping = _number(physics, "damping", 1)
            if damping < 0:
                raise SceneFormatError(
                    "physics damping must not be negative"
                )
            scene.physics.damping = damping
        context = SceneLoadContext(
            game,
            Path(asset_root).resolve(),
            {},
            allow_missing_resources,
        )
        runtime_layers = {}
        scene.layers._clear()
        for layer_record in document.layers:
            identifier = self._required_string(layer_record, "id")
            name = self._required_string(layer_record, "name")
            layer_type = layer_record.get("type", "object")
            if layer_type == "object":
                layer = scene.layers.add_object(
                    name,
                    identifier=identifier,
                    visible=bool(layer_record.get("visible", True)),
                    enabled=bool(layer_record.get("enabled", True)),
                )
            elif layer_type == "tile":
                layer = self._restore_tile_layer(
                    scene,
                    layer_record,
                    context,
                )
            else:
                raise SceneFormatError(
                    f"unknown scene layer type: {layer_type!r}"
                )
            runtime_layers[identifier] = layer
            if layer_record.get("default") is True:
                if not isinstance(layer, ObjectLayer):
                    raise SceneFormatError(
                        "the default layer must be an object layer"
                    )
                scene.default_layer = layer

        if game.presentation_enabled:
            startup_view_record = next(
                view
                for view in document.ui_views
                if view.get("startup") is True
            )
            self._restore_ui_view(
                scene,
                startup_view_record,
                context,
                startup=True,
            )
            for view_record in document.ui_views:
                if view_record is startup_view_record:
                    continue
                self._restore_ui_view(
                    scene,
                    view_record,
                    context,
                    startup=False,
                )

        records: list[dict[str, JsonValue]] = []
        record_layers: dict[int, Any] = {}
        for layer_record in document.layers:
            if layer_record.get("type", "object") != "object":
                continue
            identifier = self._required_string(layer_record, "id")
            objects = layer_record.get("objects")
            if not isinstance(objects, list):
                raise SceneFormatError(
                    "scene layer objects must be a list"
                )
            for record in objects:
                if not isinstance(record, dict):
                    raise SceneFormatError("scene object must be an object")
                for descendant in self._walk_records(
                    [cast(dict[str, JsonValue], record)]
                ):
                    records.append(descendant)
                    record_layers[id(descendant)] = runtime_layers[identifier]

        for record in records:
            identifier = self._required_string(record, "id")
            if identifier in context.objects:
                raise SceneFormatError(f"duplicate game object ID: {identifier!r}")
            type_name = self._required_string(record, "type")
            properties = self._properties(record)
            value = self.registry.object_codec_for_name(type_name).create(
                context, properties
            )
            if not isinstance(value, GameObject):
                raise SceneFormatError(f"factory for {type_name!r} returned no GameObject")
            value.scene_id = identifier
            context.objects[identifier] = value

        for record in records:
            value = context.objects[self._required_string(record, "id")]
            self._restore_object(value, record, context)

        child_ids: set[str] = set()
        for record in records:
            parent = context.objects[self._required_string(record, "id")]
            children = record.get("children", [])
            if not isinstance(children, list):
                raise SceneFormatError("object children must be a list")
            for child_record in children:
                if not isinstance(child_record, dict):
                    raise SceneFormatError("child record must be an object")
                child_id = self._required_string(child_record, "id")
                parent.add_child(context.objects[child_id])
                child_ids.add(child_id)

        for record in records:
            identifier = self._required_string(record, "id")
            if identifier not in child_ids:
                scene.add(
                    context.objects[identifier],
                    layer=record_layers[id(record)],
                )

        camera = settings.get("camera")
        if isinstance(camera, dict):
            position = context.decode(camera.get("position"))
            if isinstance(position, Vec2):
                scene.camera.position = position
            scene.camera.zoom = _number(camera, "zoom", 1)
            scene.camera.rotation = _number(camera, "rotation", 0)
        component_layout = {id(owner): tuple(owner.components) for owner in context.objects.values()}
        if initialize_components:
            scene._initialize_components()
        checkpoint = settings.get("checkpoint")
        if checkpoint is not None:
            if not isinstance(checkpoint, dict) or checkpoint.get("version") != 1:
                raise SceneFormatError("unsupported checkpoint version")
            if not initialize_components:
                raise SceneFormatError("checkpoint restoration requires component initialization")
            scene.time.elapsed = _number(checkpoint, "elapsed", 0)
            if not isfinite(scene.time.elapsed) or scene.time.elapsed < 0:
                raise SceneFormatError("checkpoint elapsed time must be finite and non-negative")
            # Reapply physical state after on_ready, which may set initial velocity.
            for record in records:
                owner = context.objects[self._required_string(record, "id")]
                transform = record.get("transform")
                if not isinstance(transform, dict):
                    raise SceneFormatError("checkpoint object transform must be an object")
                restored_position = context.decode(transform.get("position"))
                if not isinstance(restored_position, Vec2):
                    raise SceneFormatError("checkpoint position must be a Vec2")
                owner.position = restored_position
                owner.rotation = _number(transform, "rotation", 0)
                owner.scale_x, owner.scale_y = _point(transform.get("scale"), "scale")
                owner.reset_interpolation()
                component_records = record.get("components", [])
                if (not isinstance(component_records, list)
                        or tuple(owner.components) != component_layout[id(owner)]):
                    raise SceneFormatError("on_ready changed the checkpoint component layout")
                for component, raw in zip(owner.components, component_records):
                    if not isinstance(raw, dict):
                        raise SceneFormatError("checkpoint component must be an object")
                    if isinstance(component, PhysicsBody):
                        properties = self._properties(raw)
                        component.velocity = _point(context.decode(properties.get("velocity", {"$vec2": [0, 0]})), "velocity")
                        component.angular_velocity = _number(properties, "angular_velocity", 0)
                        component.gravity_scale = _number(properties, "gravity_scale", 1)
                        component.fixed_rotation = bool(properties.get("fixed_rotation", False))
                        component._body.position = tuple(owner.world_position)
                        component._body.angle = -radians(owner.world_rotation)
            for record in records:
                owner = context.objects[self._required_string(record, "id")]
                component_records = record.get("components", [])
                assert isinstance(component_records, list)
                for component, raw in zip(owner.components, component_records):
                    assert isinstance(raw, dict)
                    if "runtime_state" in raw:
                        state = context.decode(raw["runtime_state"])
                        if not isinstance(state, dict):
                            raise SceneFormatError("component runtime_state must be an object")
                        component.load_state(state)
            original = checkpoint.get("original_scene")
            scene._source_document = SceneDocument.from_dict(original) if isinstance(original, dict) else None
        else:
            scene._source_document = deepcopy(document)
        scene._source_registry = self.registry
        return scene

    def _dump_ui_view(
        self,
        name: str,
        view: UIView,
        context: SceneSaveContext,
        *,
        startup: bool,
    ) -> dict[str, JsonValue]:
        if not isinstance(view.root, CanvasLayout):
            raise SceneFormatError(
                f"UI view {name!r} must use CanvasLayout to be saved"
            )
        return {
            "type": "ui",
            "id": view.id,
            "name": name,
            "startup": startup,
            "transparent": view.transparent,
            "modal": view.modal,
            "pauses_below": view.pauses_below,
            "pauses_scene": view.pauses_scene,
            "elements": [
                self._dump_gui_widget(widget, view.root, context)
                for widget in view.root.children
            ],
        }

    def _dump_gui_widget(
        self,
        widget: Widget,
        parent: Container,
        context: SceneSaveContext,
    ) -> dict[str, JsonValue]:
        x, y = (
            parent.position_of(widget)
            if isinstance(parent, CanvasLayout)
            else (0, 0)
        )
        record: dict[str, JsonValue] = {
            "id": widget.id,
            "name": widget.name,
            "x": x,
            "y": y,
            "width": widget.requested_width,
            "height": widget.requested_height,
            "visible": widget.visible,
            "enabled": widget.enabled,
            "z_index": widget.layer,
            "min_width": widget.min_width,
            "min_height": widget.min_height,
            "grow": widget.grow,
            "margin": cast(JsonValue, list(widget.margin)),
            "offset_x": widget.offset_x,
            "offset_y": widget.offset_y,
            "scale_x": widget.scale_x,
            "scale_y": widget.scale_y,
            "opacity": widget.opacity,
            "focusable": widget.focusable,
        }
        if isinstance(widget, Panel):
            record.update(
                {
                    "type": "panel",
                    "color": cast(JsonValue, list(widget.color)),
                    "children": [
                        self._dump_gui_widget(child, widget, context)
                        for child in widget.children
                    ],
                }
            )
        elif isinstance(widget, StackLayout):
            record.update(
                {
                    "type": "stack",
                    "padding": cast(JsonValue, list(widget.padding)),
                    "children": [
                        self._dump_gui_widget(child, widget, context)
                        for child in widget.children
                    ],
                }
            )
        elif isinstance(widget, VBox):
            record.update(
                {
                    "type": "vbox",
                    "spacing": widget.spacing,
                    "padding": cast(JsonValue, list(widget.padding)),
                    "alignment": widget.alignment,
                    "children": [
                        self._dump_gui_widget(child, widget, context)
                        for child in widget.children
                    ],
                }
            )
        elif isinstance(widget, HBox):
            record.update(
                {
                    "type": "hbox",
                    "spacing": widget.spacing,
                    "padding": cast(JsonValue, list(widget.padding)),
                    "alignment": widget.alignment,
                    "children": [
                        self._dump_gui_widget(child, widget, context)
                        for child in widget.children
                    ],
                }
            )
        elif isinstance(widget, GridLayout):
            record.update(
                {
                    "type": "grid",
                    "columns": widget.columns,
                    "row_spacing": widget.row_spacing,
                    "column_spacing": widget.column_spacing,
                    "padding": cast(JsonValue, list(widget.padding)),
                    "children": [
                        self._dump_gui_widget(child, widget, context)
                        for child in widget.children
                    ],
                }
            )
        elif isinstance(widget, AnchorLayout):
            record.update(
                {
                    "type": "anchor",
                    "horizontal": widget.horizontal,
                    "vertical": widget.vertical,
                    "anchor_margin": cast(
                        JsonValue,
                        list(widget.anchor_margin),
                    ),
                    "children": [
                        self._dump_gui_widget(child, widget, context)
                        for child in widget.children
                    ],
                }
            )
        elif isinstance(widget, ListView):
            record.update(
                {
                    "type": "list_view",
                    "selected_index": widget.selected_index,
                    "spacing": widget.spacing,
                    "selected_color": cast(
                        JsonValue,
                        list(widget.style.selected),
                    ),
                    "scroll_speed": widget.scroll_speed,
                    "scrollbar_track": cast(
                        JsonValue,
                        list(widget.scrollbar_style.track),
                    ),
                    "scrollbar_thumb": cast(
                        JsonValue,
                        list(widget.scrollbar_style.thumb),
                    ),
                    "scrollbar_width": widget.scrollbar_style.width,
                    "minimum_thumb": widget.scrollbar_style.minimum_thumb,
                    "children": [
                        self._dump_gui_widget(child, widget, context)
                        for child in widget.items
                    ],
                }
            )
        elif isinstance(widget, ScrollView):
            record.update(
                {
                    "type": "scroll",
                    "scroll_speed": widget.scroll_speed,
                    "horizontal": widget.horizontal,
                    "scrollbar_track": cast(
                        JsonValue,
                        list(widget.scrollbar_style.track),
                    ),
                    "scrollbar_thumb": cast(
                        JsonValue,
                        list(widget.scrollbar_style.thumb),
                    ),
                    "scrollbar_width": widget.scrollbar_style.width,
                    "minimum_thumb": widget.scrollbar_style.minimum_thumb,
                    "children": [
                        self._dump_gui_widget(child, widget, context)
                        for child in widget.children
                    ],
                }
            )
        elif isinstance(widget, UIImage):
            record.update(
                {
                    "type": "image",
                    "image": (
                        None
                        if widget.image is None
                        else context.resource_path(widget.image)
                    ),
                    "fit": widget.fit,
                }
            )
        elif isinstance(widget, ProgressBar):
            record.update(
                {
                    "type": "progress_bar",
                    "minimum": widget.minimum,
                    "maximum": widget.maximum,
                    "value": widget.value,
                    "background": cast(
                        JsonValue,
                        list(widget.style.background),
                    ),
                    "fill": cast(JsonValue, list(widget.style.fill)),
                }
            )
        elif isinstance(widget, Label):
            record.update(
                {
                    "type": "label",
                    "text": widget.text,
                    "font": (
                        None
                        if widget.font is None
                        else context.resource_path(widget.font)
                    ),
                    "font_size": widget.font_size,
                    "color": cast(JsonValue, list(widget.color)),
                    "align": widget.align,
                    "multiline": widget.multiline,
                }
            )
        elif isinstance(widget, Checkbox):
            record.update(
                {
                    "type": "checkbox",
                    "text": widget.text,
                    "value": widget.value,
                    "box": cast(JsonValue, list(widget.style.box)),
                    "check": cast(JsonValue, list(widget.style.check)),
                    "text_color": cast(
                        JsonValue,
                        list(widget.style.text_color),
                    ),
                    "disabled": cast(
                        JsonValue,
                        list(widget.style.disabled),
                    ),
                    "font": (
                        None
                        if widget.style.font is None
                        else context.resource_path(widget.style.font)
                    ),
                    "font_size": widget.style.font_size,
                }
            )
        elif isinstance(widget, Dropdown):
            record.update(
                {
                    "type": "dropdown",
                    "options": cast(JsonValue, list(widget.options)),
                    "selected_index": widget.selected_index,
                    "background": cast(
                        JsonValue,
                        list(widget.style.background),
                    ),
                    "options_color": cast(
                        JsonValue,
                        list(widget.style.options),
                    ),
                    "selected_color": cast(
                        JsonValue,
                        list(widget.style.selected),
                    ),
                    "disabled": cast(
                        JsonValue,
                        list(widget.style.disabled),
                    ),
                    "text_color": cast(
                        JsonValue,
                        list(widget.style.text_color),
                    ),
                    "font": (
                        None
                        if widget.style.font is None
                        else context.resource_path(widget.style.font)
                    ),
                    "font_size": widget.style.font_size,
                }
            )
        elif isinstance(widget, Toggle):
            record.update(
                {
                    "type": "toggle",
                    "text": widget.text,
                    "value": widget.value,
                    "background": cast(
                        JsonValue,
                        list(widget.style.background),
                    ),
                    "hover": cast(
                        JsonValue,
                        list(widget.style.hover),
                    ),
                    "pressed": cast(
                        JsonValue,
                        list(widget.style.pressed),
                    ),
                    "disabled": cast(
                        JsonValue,
                        list(widget.style.disabled),
                    ),
                    "text_color": cast(
                        JsonValue,
                        list(widget.style.text_color),
                    ),
                    "font": (
                        None
                        if widget.style.font is None
                        else context.resource_path(widget.style.font)
                    ),
                    "font_size": widget.style.font_size,
                }
            )
        elif isinstance(widget, Button):
            record.update(
                {
                    "type": "button",
                    "text": widget.text,
                    "background": cast(
                        JsonValue,
                        list(widget.style.background),
                    ),
                    "hover": cast(
                        JsonValue,
                        list(widget.style.hover),
                    ),
                    "pressed": cast(
                        JsonValue,
                        list(widget.style.pressed),
                    ),
                    "disabled": cast(
                        JsonValue,
                        list(widget.style.disabled),
                    ),
                    "text_color": cast(
                        JsonValue,
                        list(widget.style.text_color),
                    ),
                    "font": (
                        None
                        if widget.style.font is None
                        else context.resource_path(widget.style.font)
                    ),
                    "font_size": widget.style.font_size,
                }
            )
        elif isinstance(widget, Slider):
            record.update(
                {
                    "type": "slider",
                    "minimum": widget.minimum,
                    "maximum": widget.maximum,
                    "value": widget.value,
                }
            )
        elif isinstance(widget, TextInput):
            record.update(
                {
                    "type": "text_input",
                    "text": widget.text,
                    "color": cast(JsonValue, list(widget.color)),
                    "text_color": cast(
                        JsonValue,
                        list(widget.text_color),
                    ),
                    "caret_color": cast(
                        JsonValue,
                        list(widget.caret_color),
                    ),
                }
            )
        else:
            raise SceneFormatError(
                f"unsupported GUI widget type: {type(widget).__name__}"
            )
        return record

    def _restore_ui_view(
        self,
        scene: Scene,
        record: Mapping[str, object],
        context: SceneLoadContext,
        *,
        startup: bool,
    ) -> UIView:
        name = self._required_string(record, "name")
        identifier = self._required_string(record, "id")
        if startup:
            view = scene.gui.default
            scene.gui.rename(view, name)
            view.id = identifier
            if not isinstance(view.root, CanvasLayout):
                raise SceneFormatError(
                    "startup UI view must use CanvasLayout"
                )
        else:
            view = scene.gui.register(
                name,
                UIView(
                    CanvasLayout(),
                    identifier=identifier,
                    name=name,
                ),
            )
        view.transparent = bool(record.get("transparent", True))
        view.modal = bool(record.get("modal", False))
        view.pauses_below = bool(record.get("pauses_below", True))
        view.pauses_scene = bool(record.get("pauses_scene", False))
        raw_elements = record.get("elements", [])
        if not isinstance(raw_elements, list):
            raise SceneFormatError("UI view elements must be a list")
        identifiers: set[str] = set()
        for raw_element in raw_elements:
            self._restore_gui_widget_tree(
                raw_element,
                view.root,
                identifiers,
                context,
            )
        return view

    def _restore_gui_widget_tree(
        self,
        raw_element: object,
        parent: Container,
        identifiers: set[str],
        context: SceneLoadContext,
    ) -> Widget:
        if not isinstance(raw_element, dict):
            raise SceneFormatError("GUI element must be an object")
        identifier = self._required_string(raw_element, "id")
        if identifier in identifiers:
            raise SceneFormatError(
                f"duplicate GUI element ID: {identifier!r}"
            )
        identifiers.add(identifier)
        widget = self._restore_gui_widget(raw_element, context)
        widget.offset_x = _number(raw_element, "offset_x", 0)
        widget.offset_y = _number(raw_element, "offset_y", 0)
        widget.scale_x = _number(raw_element, "scale_x", 1)
        widget.scale_y = _number(raw_element, "scale_y", 1)
        widget.opacity = _integer(raw_element, "opacity", 255)
        if widget.scale_x < 0 or widget.scale_y < 0:
            raise SceneFormatError("GUI widget scale must not be negative")
        if not 0 <= widget.opacity <= 255:
            raise SceneFormatError(
                "GUI widget opacity must be between 0 and 255"
            )
        if "focusable" in raw_element:
            widget.focusable = bool(raw_element["focusable"])
        if isinstance(parent, CanvasLayout):
            parent.add_at(
                widget,
                _number(raw_element, "x", 0),
                _number(raw_element, "y", 0),
            )
        else:
            parent.add(widget)
        children = raw_element.get("children", [])
        if not isinstance(children, list):
            raise SceneFormatError("GUI element children must be a list")
        if children and not isinstance(widget, Container):
            raise SceneFormatError(
                "only GUI containers can contain child elements"
            )
        if isinstance(widget, AnchorLayout) and len(children) > 1:
            raise SceneFormatError(
                "GUI anchor layout can contain only one child"
            )
        if (
            isinstance(widget, ScrollView)
            and not isinstance(widget, ListView)
            and len(children) > 1
        ):
            raise SceneFormatError(
                "GUI scroll view can contain only one child"
            )
        if isinstance(widget, Container):
            for child in children:
                self._restore_gui_widget_tree(
                    child,
                    widget,
                    identifiers,
                    context,
                )
        if isinstance(widget, ListView):
            selected = raw_element.get("selected_index")
            if selected is not None and (
                not isinstance(selected, int)
                or isinstance(selected, bool)
                or not 0 <= selected < len(widget.items)
            ):
                raise SceneFormatError(
                    "GUI list view selected index is out of range"
                )
            widget.selected_index = selected
        return widget

    def _restore_gui_widget(
        self,
        record: Mapping[str, object],
        context: SceneLoadContext,
    ) -> Widget:
        widget_type = self._required_string(record, "type")
        width_value = record.get("width")
        height_value = record.get("height")
        width = (
            None
            if width_value is None
            else _number(record, "width")
        )
        height = (
            None
            if height_value is None
            else _number(record, "height")
        )
        common: dict[str, Any] = {
            "width": width,
            "height": height,
            "min_width": _number(record, "min_width", 0),
            "min_height": _number(record, "min_height", 0),
            "grow": _number(record, "grow", 0),
            "margin": _gui_padding(
                record.get("margin", [0, 0, 0, 0]),
                "GUI widget margin",
            ),
            "visible": bool(record.get("visible", True)),
            "enabled": bool(record.get("enabled", True)),
            "layer": _integer(record, "z_index", 0),
            "identifier": self._required_string(record, "id"),
            "name": self._required_string(record, "name"),
        }

        if widget_type == "panel":
            return Panel(
                cast(
                    Color,
                    _color(record.get("color"), "GUI panel color"),
                ),
                **common,
            )
        if widget_type == "stack":
            return StackLayout(
                padding=_gui_padding(
                    record.get("padding", [0, 0, 0, 0]),
                    "GUI stack padding",
                ),
                **common,
            )
        if widget_type in {"vbox", "hbox"}:
            alignment = self._required_string(record, "alignment")
            if alignment not in {"start", "center", "end"}:
                raise SceneFormatError(
                    "GUI linear layout alignment must be "
                    "'start', 'center', or 'end'"
                )
            spacing = _number(record, "spacing", 0)
            if spacing < 0:
                raise SceneFormatError(
                    "GUI linear layout spacing must not be negative"
                )
            layout_type = VBox if widget_type == "vbox" else HBox
            return layout_type(
                spacing=spacing,
                padding=_gui_padding(
                    record.get("padding", [0, 0, 0, 0]),
                    f"GUI {widget_type} padding",
                ),
                alignment=alignment,
                **common,
            )
        if widget_type == "grid":
            columns = _integer(record, "columns", 1)
            row_spacing = _number(record, "row_spacing", 0)
            column_spacing = _number(
                record,
                "column_spacing",
                0,
            )
            if columns <= 0:
                raise SceneFormatError(
                    "GUI grid columns must be positive"
                )
            if row_spacing < 0 or column_spacing < 0:
                raise SceneFormatError(
                    "GUI grid spacing must not be negative"
                )
            return GridLayout(
                columns=columns,
                row_spacing=row_spacing,
                column_spacing=column_spacing,
                padding=_gui_padding(
                    record.get("padding", [0, 0, 0, 0]),
                    "GUI grid padding",
                ),
                **common,
            )
        if widget_type == "anchor":
            horizontal = self._required_string(
                record,
                "horizontal",
            )
            vertical = self._required_string(record, "vertical")
            if horizontal not in {"left", "center", "right"}:
                raise SceneFormatError(
                    "GUI anchor horizontal value must be "
                    "'left', 'center', or 'right'"
                )
            if vertical not in {"bottom", "center", "top"}:
                raise SceneFormatError(
                    "GUI anchor vertical value must be "
                    "'bottom', 'center', or 'top'"
                )
            anchor = AnchorLayout(
                horizontal=horizontal,
                vertical=vertical,
                margin=_gui_padding(
                    record.get(
                        "anchor_margin",
                        [0, 0, 0, 0],
                    ),
                    "GUI anchor margin",
                ),
                **{
                    key: value
                    for key, value in common.items()
                    if key != "margin"
                },
            )
            anchor.margin = common["margin"]
            return anchor
        if widget_type == "list_view":
            scroll_speed = _number(record, "scroll_speed", 32)
            spacing = _number(record, "spacing", 0)
            if scroll_speed <= 0 or spacing < 0:
                raise SceneFormatError(
                    "GUI list view speed must be positive and spacing non-negative"
                )
            defaults = ListViewStyle()
            return ListView(
                scroll_speed=scroll_speed,
                spacing=spacing,
                style=ListViewStyle(
                    selected=cast(
                        Color,
                        _color(
                            record.get(
                                "selected_color",
                                defaults.selected,
                            ),
                            "GUI list view selection",
                        ),
                    ),
                ),
                scrollbar_style=_scrollbar_style(record),
                **common,
            )
        if widget_type == "scroll":
            scroll_speed = _number(record, "scroll_speed", 32)
            if scroll_speed <= 0:
                raise SceneFormatError(
                    "GUI scroll view speed must be positive"
                )
            return ScrollView(
                scroll_speed=scroll_speed,
                horizontal=bool(record.get("horizontal", False)),
                scrollbar_style=_scrollbar_style(record),
                **common,
            )
        if widget_type == "image":
            fit = record.get("fit", "contain")
            if fit not in {"contain", "cover", "stretch", "native"}:
                raise SceneFormatError(
                    "GUI image fit must be contain, cover, stretch, or native"
                )
            raw_path = record.get("image")
            if raw_path is not None and not isinstance(raw_path, str):
                raise SceneFormatError(
                    "GUI image resource must be a string or null"
                )
            return UIImage(
                (
                    None
                    if raw_path is None
                    else context.game.resources.load_image(
                        context.resource_path(raw_path)
                    )
                ),
                fit=cast(ImageFit, fit),
                **common,
            )
        if widget_type == "progress_bar":
            minimum = _number(record, "minimum", 0)
            maximum = _number(record, "maximum", 1)
            if maximum <= minimum:
                raise SceneFormatError(
                    "GUI progress bar maximum must be greater than minimum"
                )
            defaults = ProgressBarStyle()
            return ProgressBar(
                minimum,
                maximum,
                _number(record, "value", minimum),
                style=ProgressBarStyle(
                    background=cast(
                        Color,
                        _color(
                            record.get(
                                "background",
                                defaults.background,
                            ),
                            "GUI progress bar background",
                        ),
                    ),
                    fill=cast(
                        Color,
                        _color(
                            record.get("fill", defaults.fill),
                            "GUI progress bar fill",
                        ),
                    ),
                ),
                **common,
            )
        if widget_type == "label":
            align = str(record.get("align", "center"))
            if align not in {"left", "center", "right"}:
                raise SceneFormatError(
                    "GUI label align must be 'left', 'center', or 'right'"
                )
            return Label(
                self._required_string(record, "text"),
                font=_font(context, record.get("font"), "GUI label font"),
                font_size=_number(record, "font_size", 12),
                color=cast(
                    Color,
                    _color(record.get("color"), "GUI label color"),
                ),
                align=align,
                multiline=bool(record.get("multiline", False)),
                **common,
            )
        if widget_type == "checkbox":
            defaults = CheckboxStyle()
            return Checkbox(
                self._required_string(record, "text"),
                value=bool(record.get("value", False)),
                style=CheckboxStyle(
                    box=cast(
                        Color,
                        _color(
                            record.get("box", defaults.box),
                            "GUI checkbox box",
                        ),
                    ),
                    check=cast(
                        Color,
                        _color(
                            record.get("check", defaults.check),
                            "GUI checkbox check",
                        ),
                    ),
                    text_color=cast(
                        Color,
                        _color(
                            record.get(
                                "text_color",
                                defaults.text_color,
                            ),
                            "GUI checkbox text color",
                        ),
                    ),
                    disabled=cast(
                        Color,
                        _color(
                            record.get("disabled", defaults.disabled),
                            "GUI checkbox disabled",
                        ),
                    ),
                    font=_font(
                        context,
                        record.get("font"),
                        "GUI checkbox font",
                    ),
                    font_size=_number(record, "font_size", 14),
                ),
                **common,
            )
        if widget_type == "dropdown":
            options = record.get("options", [])
            if (
                not isinstance(options, list)
                or not all(isinstance(option, str) for option in options)
            ):
                raise SceneFormatError(
                    "GUI dropdown options must be a list of strings"
                )
            selected = record.get("selected_index")
            if selected is not None and (
                not isinstance(selected, int)
                or isinstance(selected, bool)
                or not 0 <= selected < len(options)
            ):
                raise SceneFormatError(
                    "GUI dropdown selected index is out of range"
                )
            defaults = DropdownStyle()
            return Dropdown(
                options,
                selected_index=selected,
                style=DropdownStyle(
                    background=cast(
                        Color,
                        _color(
                            record.get(
                                "background",
                                defaults.background,
                            ),
                            "GUI dropdown background",
                        ),
                    ),
                    options=cast(
                        Color,
                        _color(
                            record.get(
                                "options_color",
                                defaults.options,
                            ),
                            "GUI dropdown options",
                        ),
                    ),
                    selected=cast(
                        Color,
                        _color(
                            record.get(
                                "selected_color",
                                defaults.selected,
                            ),
                            "GUI dropdown selection",
                        ),
                    ),
                    disabled=cast(
                        Color,
                        _color(
                            record.get("disabled", defaults.disabled),
                            "GUI dropdown disabled",
                        ),
                    ),
                    text_color=cast(
                        Color,
                        _color(
                            record.get(
                                "text_color",
                                defaults.text_color,
                            ),
                            "GUI dropdown text color",
                        ),
                    ),
                    font=_font(
                        context,
                        record.get("font"),
                        "GUI dropdown font",
                    ),
                    font_size=_number(record, "font_size", 14),
                ),
                **common,
            )
        if widget_type in {"button", "toggle"}:
            style = ButtonStyle(
                background=cast(
                    Color,
                    _color(
                        record.get("background"),
                        "GUI button background",
                    ),
                ),
                hover=cast(
                    Color,
                    _color(record.get("hover"), "GUI button hover"),
                ),
                pressed=cast(
                    Color,
                    _color(
                        record.get("pressed"),
                        "GUI button pressed",
                    ),
                ),
                disabled=cast(
                    Color,
                    _color(
                        record.get("disabled"),
                        "GUI button disabled",
                    ),
                ),
                text_color=cast(
                    Color,
                    _color(
                        record.get("text_color"),
                        "GUI button text color",
                    ),
                ),
                font=_font(
                    context,
                    record.get("font"),
                    f"GUI {widget_type} font",
                ),
                font_size=_number(record, "font_size", 16),
            )
            if widget_type == "toggle":
                return Toggle(
                    self._required_string(record, "text"),
                    value=bool(record.get("value", False)),
                    style=style,
                    **common,
                )
            return Button(
                self._required_string(record, "text"),
                style=style,
                **common,
            )
        if widget_type == "slider":
            minimum = _number(record, "minimum", 0)
            maximum = _number(record, "maximum", 1)
            if maximum <= minimum:
                raise SceneFormatError(
                    "GUI slider maximum must be greater than minimum"
                )
            return Slider(
                minimum,
                maximum,
                _number(record, "value", minimum),
                **common,
            )
        if widget_type == "text_input":
            text = record.get("text", "")
            if not isinstance(text, str):
                raise SceneFormatError(
                    "GUI text input text must be a string"
                )
            return TextInput(
                text,
                color=cast(
                    Color,
                    _color(
                        record.get("color"),
                        "GUI text input color",
                    ),
                ),
                text_color=cast(
                    Color,
                    _color(
                        record.get("text_color"),
                        "GUI text input text color",
                    ),
                ),
                caret_color=cast(
                    Color,
                    _color(
                        record.get("caret_color"),
                        "GUI text input caret color",
                    ),
                ),
                **common,
            )
        raise SceneFormatError(
            f"unknown GUI widget type: {widget_type!r}"
        )

    def _dump_tile_layer(
        self,
        layer: TileLayer,
        context: SceneSaveContext,
    ) -> dict[str, JsonValue]:
        chunks: list[JsonValue] = [
            {
                "position": [chunk.column, chunk.row],
                "size": chunk.size,
                "tiles": list(chunk.cells),
            }
            for _, chunk in sorted(layer.chunks.items())
        ]
        record: dict[str, JsonValue] = {
            "type": "tile",
            "tilesets": [
                {
                    **self._dump_tileset(binding.tileset, context),
                    "id": binding.identifier,
                    "name": binding.name,
                    "first_gid": binding.first_gid,
                }
                for binding in layer.tilesets
            ],
            "cell_size": [layer.cell_width, layer.cell_height],
            "chunk_size": layer.chunk_size,
            "opacity": layer.opacity,
            "offset": [layer.offset[0], layer.offset[1]],
            "collision_enabled": layer.collision_enabled,
            "navigation_enabled": layer.navigation_enabled,
            "properties": context.encode(layer.properties),
            "chunks": chunks,
        }
        if context.include_runtime_state:
            record["runtime_elapsed"] = layer._elapsed
        return record

    def _dump_tileset(
        self,
        tileset: TileSet,
        context: SceneSaveContext,
    ) -> dict[str, JsonValue]:
        definitions: list[JsonValue] = []
        for definition in tileset.definitions:
            record: dict[str, JsonValue] = {
                "id": definition.tile_id,
                "name": definition.name,
                "tags": cast(JsonValue, sorted(definition.tags)),
                "properties": context.encode(definition.properties),
            }
            if definition.collision is not None:
                collision = definition.collision
                record["collision"] = (
                    {
                        "type": "polygon",
                        "polygons": [
                            [list(point) for point in polygon]
                            for polygon in collision.polygons
                        ],
                        "generated_from_alpha": (
                            collision.generated_from_alpha
                        ),
                        "alpha_threshold": collision.alpha_threshold,
                    }
                    if collision.shape == "polygon"
                    else {
                        "type": "rectangle",
                        "x": collision.x,
                        "y": collision.y,
                        "width": collision.width,
                        "height": collision.height,
                        "generated_from_alpha": (
                            collision.generated_from_alpha
                        ),
                        "alpha_threshold": collision.alpha_threshold,
                    }
                )
            if definition.navigation is not None:
                record["navigation"] = {
                    "walkable": definition.navigation.walkable,
                    "cost": definition.navigation.cost,
                }
            if definition.animation is not None:
                record["animation"] = [
                    {
                        "tile_id": frame.tile_id,
                        "duration": frame.duration,
                    }
                    for frame in definition.animation.frames
                ]
            definitions.append(record)
        source: dict[str, JsonValue]
        if isinstance(tileset, ColorTileSet):
            source = {
                "type": "color",
                "colors": [list(color) for color in tileset.colors],
                "tile_count": tileset.tile_count,
            }
        elif isinstance(tileset, ImageCollectionTileSet):
            source = {
                "type": "images",
                "images": [
                    context.resource_path(image)
                    for image in tileset.images
                ],
                "tile_count": tileset.tile_count,
            }
        else:
            source = {
                "type": "atlas",
                "image": context.resource_path(tileset.image),
                "tile_size": [
                    tileset.tile_width,
                    tileset.tile_height,
                ],
                "columns": tileset.columns,
                "tile_count": tileset.tile_count,
                "margin": tileset.margin,
                "spacing": tileset.spacing,
            }
        return {
            **source,
            "properties": context.encode(tileset.properties),
            "tiles": definitions,
        }

    def _restore_tile_layer(
        self,
        scene: Scene | None,
        record: Mapping[str, object],
        context: SceneLoadContext,
        *,
        detached: bool = False,
    ) -> TileLayer:
        raw_tilesets = record.get("tilesets")
        if raw_tilesets is None:
            legacy_tileset = record.get("tileset")
            if legacy_tileset is not None and not isinstance(
                legacy_tileset,
                dict,
            ):
                raise SceneFormatError(
                    "tile layer tileset must be an object or null"
                )
            raw_tilesets = (
                [
                    {
                        **legacy_tileset,
                        "id": "default",
                        "name": "Tileset",
                        "first_gid": 0,
                    }
                ]
                if isinstance(legacy_tileset, dict)
                else []
            )
        if not isinstance(raw_tilesets, list):
            raise SceneFormatError("tile layer tilesets must be a list")
        cell_width, cell_height = self._integer_pair(
            record.get("cell_size", [32, 32]),
            "tile layer cell_size",
            positive=True,
        )
        offset = _point(record.get("offset", [0, 0]), "tile layer offset")
        properties = context.decode(record.get("properties", {}))
        if not isinstance(properties, dict):
            raise SceneFormatError("tile layer properties must be an object")
        if scene is None and not detached:
            raise SceneFormatError("Restoring a scene tile layer requires a scene")
        def create_detached(name: str, tileset: TileSet | None, **options: Any) -> TileLayer:
            return TileLayer(None, name, tileset, **options)

        if detached:
            factory = create_detached
        elif scene is not None:
            factory = scene.layers.add_tile
        else:
            raise SceneFormatError("Restoring a scene tile layer requires a scene")
        layer = factory(
            self._required_string(record, "name"),
            None,
            identifier=self._required_string(record, "id"),
            visible=bool(record.get("visible", True)),
            enabled=bool(record.get("enabled", True)),
            cell_width=cell_width,
            cell_height=cell_height,
            chunk_size=_integer(record, "chunk_size", 16),
            opacity=_number(record, "opacity", 1),
            offset=offset,
            collision_enabled=bool(
                record.get("collision_enabled", False)
            ),
            navigation_enabled=bool(
                record.get("navigation_enabled", False)
            ),
            properties=cast(dict[str, Any], properties),
        )
        for index, tileset_record in enumerate(raw_tilesets):
            if not isinstance(tileset_record, dict):
                raise SceneFormatError("tileset must be an object")
            identifier = tileset_record.get(
                "id",
                f"tileset-{index + 1}",
            )
            name = tileset_record.get(
                "name",
                f"Tileset {index + 1}",
            )
            first_gid = tileset_record.get("first_gid", 0)
            if not isinstance(identifier, str) or not identifier:
                raise SceneFormatError(
                    "tileset id must be a non-empty string"
                )
            if not isinstance(name, str) or not name:
                raise SceneFormatError(
                    "tileset name must be a non-empty string"
                )
            if (
                not isinstance(first_gid, int)
                or isinstance(first_gid, bool)
                or first_gid < 0
            ):
                raise SceneFormatError(
                    "tileset first_gid must be a non-negative integer"
                )
            try:
                layer.add_tileset(
                    self._restore_tileset(tileset_record, context),
                    identifier=identifier,
                    name=name,
                    first_gid=first_gid,
                )
            except ValueError as error:
                raise SceneFormatError(
                    f"invalid tileset {identifier!r}: {error}"
                ) from error
        chunks = record.get("chunks", [])
        if not isinstance(chunks, list):
            raise SceneFormatError("tile layer chunks must be a list")
        for chunk_record in chunks:
            if not isinstance(chunk_record, dict):
                raise SceneFormatError("tile chunk must be an object")
            column, row = self._integer_pair(
                chunk_record.get("position"),
                "tile chunk position",
            )
            size = _integer(chunk_record, "size")
            if size != layer.chunk_size:
                raise SceneFormatError(
                    "tile chunk size must match its layer chunk size"
                )
            raw_tiles = chunk_record.get("tiles")
            if (
                not isinstance(raw_tiles, list)
                or len(raw_tiles) != size * size
                or not all(
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                    for value in raw_tiles
                )
            ):
                raise SceneFormatError(
                    "tile chunk tiles must match its declared size"
                )
            chunk = TileChunk(column, row, size)
            chunk.cells = cast(list[int], list(raw_tiles))
            for encoded in chunk.cells:
                try:
                    cell = decode_tile_cell(encoded)
                    if cell is not None:
                        if not layer.tilesets:
                            raise SceneFormatError(
                                "non-empty tile layer requires a tileset"
                            )
                        tileset, local_id = layer.resolve_tile(
                            cell.tile_id
                        )
                        tileset.definition(local_id)
                except ValueError as error:
                    raise SceneFormatError(
                        f"invalid tile chunk cell: {error}"
                    ) from error
            if not chunk.empty:
                layer.chunks[(column, row)] = chunk
        if "runtime_elapsed" in record:
            elapsed = _number(record, "runtime_elapsed", 0)
            if not isfinite(elapsed) or elapsed < 0:
                raise SceneFormatError("tile animation elapsed must be finite and non-negative")
            layer._elapsed = elapsed
        return layer

    def _restore_tileset(
        self,
        record: Mapping[str, object],
        context: SceneLoadContext,
    ) -> TileSet:
        properties = context.decode(record.get("properties", {}))
        if not isinstance(properties, dict):
            raise SceneFormatError("tileset properties must be an object")
        tileset_type = record.get("type", "atlas")
        if tileset_type == "color":
            raw_colors = record.get("colors")
            if not isinstance(raw_colors, list) or not raw_colors:
                raise SceneFormatError(
                    "color tileset colors must be a non-empty list"
                )
            colors: list[Color] = []
            for raw_color in raw_colors:
                if (
                    not isinstance(raw_color, list)
                    or len(raw_color) not in {3, 4}
                    or not all(
                        isinstance(channel, int)
                        and not isinstance(channel, bool)
                        and 0 <= channel <= 255
                        for channel in raw_color
                    )
                ):
                    raise SceneFormatError(
                        "color tile must contain three or four "
                        "channels from 0 to 255"
                    )
                colors.append(cast(Color, tuple(raw_color)))
            tileset = ColorTileSet(
                colors,
                properties=cast(dict[str, Any], properties),
            )
        elif tileset_type == "images":
            raw_images = record.get("images")
            if not isinstance(raw_images, list) or not raw_images:
                raise SceneFormatError(
                    "image collection tileset images must be "
                    "a non-empty list"
                )
            images = [
                context.game.resources.load_image(
                    context.resource_path(image)
                )
                for image in raw_images
            ]
            tileset = ImageCollectionTileSet(
                images,
                properties=cast(dict[str, Any], properties),
            )
        elif tileset_type == "atlas":
            image_path = context.resource_path(
                self._required_string(record, "image")
            )
            tile_width, tile_height = self._integer_pair(
                record.get("tile_size"),
                "tileset tile_size",
                positive=True,
            )
            raw_columns = record.get("columns")
            columns = (
                None
                if raw_columns is None
                else _integer(record, "columns")
            )
            tileset = TileSet(
                context.game.resources.load_image(image_path),
                tile_width,
                tile_height,
                columns=columns,
                margin=_integer(record, "margin", 0),
                spacing=_integer(record, "spacing", 0),
                properties=cast(dict[str, Any], properties),
            )
        else:
            raise SceneFormatError(
                f"unknown tileset type: {tileset_type!r}"
            )
        raw_tile_count = record.get("tile_count")
        if (
            raw_tile_count is not None
            and _integer(record, "tile_count") != tileset.tile_count
        ):
            raise SceneFormatError(
                "tileset tile_count does not match its atlas grid"
            )
        definitions = record.get("tiles", [])
        if not isinstance(definitions, list):
            raise SceneFormatError("tileset tiles must be a list")
        for raw_definition in definitions:
            if not isinstance(raw_definition, dict):
                raise SceneFormatError("tileset tile must be an object")
            tile_id = _integer(raw_definition, "id")
            name = _optional_string(raw_definition.get("name"), "tile name")
            tags = raw_definition.get("tags", [])
            if not isinstance(tags, list) or not all(
                isinstance(tag, str) and tag for tag in tags
            ):
                raise SceneFormatError(
                    "tile tags must be non-empty strings"
                )
            definition_properties = context.decode(
                raw_definition.get("properties", {})
            )
            if not isinstance(definition_properties, dict):
                raise SceneFormatError("tile properties must be an object")
            collision = self._restore_tile_collision(
                raw_definition.get("collision")
            )
            navigation = self._restore_tile_navigation(
                raw_definition.get("navigation")
            )
            animation = self._restore_tile_animation(
                raw_definition.get("animation")
            )
            try:
                tileset.define(
                    tile_id,
                    name=name,
                    collision=collision,
                    navigation=navigation,
                    animation=animation,
                    tags=set(tags),
                    properties=cast(
                        dict[str, Any],
                        definition_properties,
                    ),
                )
            except ValueError as error:
                raise SceneFormatError(
                    f"invalid tile definition {tile_id}: {error}"
                ) from error
        return tileset

    @staticmethod
    def _restore_tile_collision(
        value: object,
    ) -> TileCollision | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise SceneFormatError("tile collision must be an object")
        collision_type = value.get("type", "rectangle")
        generated = bool(value.get("generated_from_alpha", False))
        threshold = _integer(value, "alpha_threshold", 255)
        if collision_type == "polygon":
            polygons_value = value.get("polygons")
            if not isinstance(polygons_value, list):
                raise SceneFormatError(
                    "tile collision polygons must be a list"
                )
            polygons: list[tuple[tuple[float, float], ...]] = []
            for polygon_value in polygons_value:
                if not isinstance(polygon_value, list):
                    raise SceneFormatError(
                        "tile collision polygon must be a list"
                    )
                polygons.append(
                    tuple(
                        _point(point, "tile collision polygon point")
                        for point in polygon_value
                    )
                )
            try:
                return TileCollision(
                    shape="polygon",
                    polygons=tuple(polygons),
                    generated_from_alpha=generated,
                    alpha_threshold=threshold,
                )
            except ValueError as error:
                raise SceneFormatError(
                    f"invalid tile collision: {error}"
                ) from error
        if collision_type != "rectangle":
            raise SceneFormatError("unsupported tile collision type")
        width = value.get("width")
        height = value.get("height")
        try:
            return TileCollision(
                _number(value, "x", 0),
                _number(value, "y", 0),
                None if width is None else _number(value, "width"),
                None if height is None else _number(value, "height"),
                generated_from_alpha=generated,
                alpha_threshold=threshold,
            )
        except ValueError as error:
            raise SceneFormatError(f"invalid tile collision: {error}") from error

    @staticmethod
    def _restore_tile_navigation(
        value: object,
    ) -> TileNavigation | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise SceneFormatError("tile navigation must be an object")
        try:
            return TileNavigation(
                bool(value.get("walkable", True)),
                _number(value, "cost", 1),
            )
        except ValueError as error:
            raise SceneFormatError(
                f"invalid tile navigation: {error}"
            ) from error

    @staticmethod
    def _restore_tile_animation(
        value: object,
    ) -> TileAnimation | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise SceneFormatError("tile animation must be a list")
        frames: list[TileAnimationFrame] = []
        for raw_frame in value:
            if not isinstance(raw_frame, dict):
                raise SceneFormatError(
                    "tile animation frame must be an object"
                )
            try:
                frames.append(
                    TileAnimationFrame(
                        _integer(raw_frame, "tile_id"),
                        _number(raw_frame, "duration"),
                    )
                )
            except ValueError as error:
                raise SceneFormatError(
                    f"invalid tile animation frame: {error}"
                ) from error
        try:
            return TileAnimation(tuple(frames))
        except ValueError as error:
            raise SceneFormatError(f"invalid tile animation: {error}") from error

    @staticmethod
    def _integer_pair(
        value: object,
        name: str,
        *,
        positive: bool = False,
    ) -> tuple[int, int]:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or not all(
                isinstance(item, int) and not isinstance(item, bool)
                for item in value
            )
        ):
            raise SceneFormatError(f"{name} must contain two integers")
        first, second = cast(tuple[int, int], tuple(value))
        if positive and (first <= 0 or second <= 0):
            raise SceneFormatError(f"{name} must be positive")
        return first, second

    def _dump_object(
        self, value: GameObject, context: SceneSaveContext
    ) -> dict[str, JsonValue]:
        codec = self.registry.object_codec_for_value(value)
        return {
            "id": value.scene_id,
            "type": codec.name,
            "name": value.name,
            "enabled": value.enabled,
            "visible": value.visible,
            "z_index": value.z_index,
            "render_behind_parent": value.render_behind_parent,
            "transform": {
                "position": context.encode(value.position),
                "rotation": value.rotation,
                "scale": [value.scale_x, value.scale_y],
            },
            "speed": context.encode(value.speed),
            "properties": {
                str(key): context.encode(item)
                for key, item in codec.dump(value, context).items()
            },
            "script_properties": context.encode(value.properties),
            "components": [
                self._dump_component(component, context)
                for component in value.components
            ],
            "children": [
                self._dump_object(child, context)
                for child in value.children
                if isinstance(child, GameObject)
            ],
        }

    def _dump_component(
        self, value: Component, context: SceneSaveContext
    ) -> dict[str, JsonValue]:
        codec = self.registry.component_codec_for_value(value)
        record: dict[str, JsonValue] = {
            "type": codec.name,
            "properties": {
                str(key): context.encode(item)
                for key, item in codec.dump(value, context).items()
            },
        }
        if context.include_runtime_state:
            state = value.save_state()
            if not isinstance(state, Mapping):
                raise SceneFormatError(f"{type(value).__name__}.save_state() must return a mapping")
            record["runtime_state"] = context.encode(dict(state))
        return record

    def _restore_object(
        self,
        value: GameObject,
        record: Mapping[str, object],
        context: SceneLoadContext,
    ) -> None:
        name = record.get("name")
        if name is not None and not isinstance(name, str):
            raise SceneFormatError("object name must be a string or null")
        value.name = name
        value.enabled = bool(record.get("enabled", True))
        value.visible = bool(record.get("visible", True))
        value.z_index = _integer(record, "z_index", 0)
        render_behind_parent = record.get("render_behind_parent", False)
        if not isinstance(render_behind_parent, bool):
            raise SceneFormatError("render_behind_parent must be a boolean")
        value.render_behind_parent = render_behind_parent
        transform = record.get("transform", {})
        if not isinstance(transform, dict):
            raise SceneFormatError("object transform must be an object")
        position = context.decode(transform.get("position", {"$vec2": [0, 0]}))
        if not isinstance(position, Vec2):
            raise SceneFormatError("object position must be a Vec2")
        value.position = position
        value.rotation = float(transform.get("rotation", 0))
        scale = transform.get("scale", [1, 1])
        if not isinstance(scale, list) or len(scale) != 2:
            raise SceneFormatError("object scale must contain two numbers")
        value.scale_x, value.scale_y = float(scale[0]), float(scale[1])
        speed = context.decode(record.get("speed", {"$vec2": [0, 0]}))
        if not isinstance(speed, Vec2):
            raise SceneFormatError("object speed must be a Vec2")
        value.speed = speed
        script_properties = record.get("script_properties", {})
        if not isinstance(script_properties, dict):
            raise SceneFormatError(
                "object script_properties must be an object"
            )
        decoded_properties = context.decode(script_properties)
        if not isinstance(decoded_properties, dict):
            raise SceneFormatError(
                "object script_properties must decode to an object"
            )
        value.properties = decoded_properties

        components = record.get("components", [])
        if not isinstance(components, list):
            raise SceneFormatError("object components must be a list")
        created: list[tuple[Component, _TypeCodec[Component], Mapping[str, object]]] = []
        for component_record in components:
            if not isinstance(component_record, dict):
                raise SceneFormatError("component record must be an object")
            type_name = self._required_string(component_record, "type")
            properties = self._properties(component_record)
            codec = self.registry.component_codec_for_name(type_name)
            component = codec.create(context, properties)
            if not isinstance(component, Component):
                raise SceneFormatError(
                    f"factory for {type_name!r} returned no Component"
                )
            created.append((component, codec, properties))
        try:
            value._add_restored_components(
                component for component, _, _ in created
            )
        except ValueError as error:
            raise SceneFormatError(
                f"invalid component dependencies on game object "
                f"{value.name!r} ({value.scene_id}): {error}"
            ) from error

        object_codec = self.registry.object_codec_for_name(
            self._required_string(record, "type")
        )
        if object_codec.restore is not None:
            object_codec.restore(value, self._properties(record), context)
        for component, codec, properties in created:
            if codec.restore is not None:
                codec.restore(component, properties, context)

    @staticmethod
    def _walk_records(
        roots: Sequence[dict[str, JsonValue]],
    ) -> Sequence[dict[str, JsonValue]]:
        records: list[dict[str, JsonValue]] = []

        def visit(record: dict[str, JsonValue]) -> None:
            records.append(record)
            children = record.get("children", [])
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        visit(child)

        for root in roots:
            visit(root)
        return records

    @staticmethod
    def _required_string(record: Mapping[str, object], name: str) -> str:
        value = record.get(name)
        if not isinstance(value, str) or not value:
            raise SceneFormatError(f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _properties(record: Mapping[str, object]) -> Mapping[str, object]:
        properties = record.get("properties", {})
        if not isinstance(properties, dict):
            raise SceneFormatError("properties must be an object")
        return properties
