"""Project configuration API.

``PROJECT_FORMAT`` is ``"g.project"``, ``PROJECT_VERSION`` is the supported
configuration version (1), and ``PROJECT_FILENAME`` is the conventional file
name used by project discovery and creation.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module, invalidate_caches, reload
from importlib.util import cache_from_source
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
import traceback
from types import ModuleType

from .presentation import SCALING_MODES, ScalingMode
from .resources import Color

PROJECT_FORMAT = "g.project"
PROJECT_VERSION = 1
PROJECT_FILENAME = "g.project.json"


class ProjectFormatError(ValueError):
    """Invalid project file or project configuration."""

    pass


class ProjectImportError(RuntimeError):
    """A configured project script could not be imported."""

    pass


@dataclass(frozen=True)
class ProjectExportConfig:
    """Application metadata and module-collection options for exports.

    Attributes:
        name: Display name for the exported application.
        version: Application version string.
        startup_scene: Scene used on client/application startup.
        icon: Optional icon resource path.
        hidden_imports: Additional import module names to include.
        collect_packages: Package names to collect in the export.
        server_startup_scene: Scene used when starting as a server.
    """

    name: str = "Game"
    version: str = "0.1.0"
    startup_scene: str = ""
    icon: str | None = None
    hidden_imports: tuple[str, ...] = ()
    collect_packages: tuple[str, ...] = ()
    server_startup_scene: str = ""


@dataclass(frozen=True)
class ProjectNetworkConfig:
    """Network startup and listener settings stored in a project.

    Attributes:
        enabled: Whether project networking is enabled.
        endpoint: WebSocket endpoint used by automatic client connection.
        bind: Host address used by a server listener.
        port: Listener port.
        max_clients: Maximum number of clients.
        snapshot_hz: Network snapshot frequency.
        auto_connect: Whether configured clients connect automatically.
    """

    enabled: bool = False
    endpoint: str = "ws://127.0.0.1:7777"
    bind: str = "127.0.0.1"
    port: int = 7777
    max_clients: int = 8
    snapshot_hz: int = 20
    auto_connect: bool = True

    def __post_init__(self) -> None:
        """Validate endpoint, address, port, and network limits.

        Raises:
            ValueError: If any setting is invalid.
        """
        if not isinstance(self.enabled, bool):
            raise ValueError("network enabled must be a boolean")
        if not self.endpoint.startswith(("ws://", "wss://")):
            raise ValueError(
                "network endpoint must be a ws:// or wss:// address"
            )
        if not self.bind:
            raise ValueError("network bind must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("network port must be between 1 and 65535")
        if self.max_clients <= 0:
            raise ValueError("network max_clients must be positive")
        if self.snapshot_hz <= 0:
            raise ValueError("network snapshot_hz must be positive")
        if not isinstance(self.auto_connect, bool):
            raise ValueError("network auto_connect must be a boolean")


@dataclass(frozen=True)
class ProjectConfig:
    """Immutable game-project configuration loaded from a project file.

    Attributes:
        path: Path to the project file.
        script_modules: Python modules managed by the project.
        width: Logical game width in pixels.
        height: Logical game height in pixels.
        grid_cell_size: Editor grid cell size in pixels.
        scaling: Presentation scaling mode.
        matte_color: Color used outside the presented game area.
        resizable: Whether the game window may be resized.
        fullscreen: Whether the game starts fullscreen.
        export: Application export settings.
        network: Network settings.
    """

    path: Path
    script_modules: tuple[str, ...] = ()
    width: int = 800
    height: int = 600
    grid_cell_size: int = 32
    scaling: ScalingMode = "fit"
    matte_color: Color = (0, 0, 0, 255)
    resizable: bool = True
    fullscreen: bool = False
    export: ProjectExportConfig = ProjectExportConfig()
    network: ProjectNetworkConfig = ProjectNetworkConfig()

    @property
    def root(self) -> Path:
        """Project directory containing the project file."""
        return self.path.parent

    @property
    def asset_root(self) -> Path:
        """Project directory used as the asset root."""
        return self.root

    @classmethod
    def create(
        cls,
        root: str | Path,
        *,
        width: int = 800,
        height: int = 600,
        grid_cell_size: int = 32,
        scaling: ScalingMode = "fit",
        matte_color: Color = (0, 0, 0, 255),
        resizable: bool = True,
        fullscreen: bool = False,
        export: ProjectExportConfig | None = None,
        network: ProjectNetworkConfig | None = None,
    ) -> ProjectConfig:
        """Create a configuration rooted at ``root`` using the standard name.

        Args:
            root: Project directory.
            width: Logical game width in pixels.
            height: Logical game height in pixels.
            grid_cell_size: Editor grid cell size in pixels.
            scaling: Presentation scaling mode.
            matte_color: Color outside the presentation area.
            resizable: Whether the game window may be resized.
            fullscreen: Whether the game starts fullscreen.
            export: Export settings, or defaults when omitted.
            network: Network settings, or defaults when omitted.

        Returns:
            A configuration at ``root / PROJECT_FILENAME``.
        """
        return cls(
            Path(root).expanduser().resolve() / PROJECT_FILENAME,
            width=width,
            height=height,
            grid_cell_size=grid_cell_size,
            scaling=scaling,
            matte_color=matte_color,
            resizable=resizable,
            fullscreen=fullscreen,
            export=export or ProjectExportConfig(),
            network=network or ProjectNetworkConfig(),
        )

    @classmethod
    def discover(
        cls,
        start: str | Path,
    ) -> ProjectConfig | None:
        """Find and read the nearest project file at or above ``start``.

        Args:
            start: Starting file or directory.

        Returns:
            The discovered project, or ``None`` when no project file exists.

        Raises:
            ProjectFormatError: If the discovered file is invalid.
        """
        current = Path(start).expanduser().resolve()
        if current.is_file():
            current = current.parent
        for directory in (current, *current.parents):
            candidate = directory / PROJECT_FILENAME
            if candidate.is_file():
                return cls.read(candidate)
        return None

    @classmethod
    def read(cls, filename: str | Path) -> ProjectConfig:
        """Read and validate a project JSON file.

        Args:
            filename: Project file path.

        Returns:
            Parsed project configuration.

        Raises:
            ProjectFormatError: If the file cannot be read or is invalid.
        """
        path = Path(filename).expanduser().resolve()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectFormatError(
                f"could not read project {path}: {error}"
            ) from error
        if not isinstance(raw, dict):
            raise ProjectFormatError("project root must be an object")
        if raw.get("format") != PROJECT_FORMAT:
            raise ProjectFormatError(
                f"expected project format {PROJECT_FORMAT!r}"
            )
        if raw.get("version") != PROJECT_VERSION:
            raise ProjectFormatError(
                f"unsupported project version {raw.get('version')!r}"
            )
        modules = raw.get("script_modules", [])
        if (
            not isinstance(modules, list)
            or not all(
                isinstance(module, str)
                and module
                and all(
                    part.isidentifier()
                    for part in module.split(".")
                )
                for module in modules
            )
        ):
            raise ProjectFormatError(
                "script_modules must contain valid Python module names"
            )
        game = raw.get("game", {})
        if not isinstance(game, dict):
            raise ProjectFormatError("game must be an object")
        width = _positive_integer(game.get("width", 800), "game width")
        height = _positive_integer(game.get("height", 600), "game height")
        grid = _positive_integer(
            game.get("grid_cell_size", 32),
            "grid cell size",
        )
        scaling = game.get("scaling", "fit")
        if scaling not in SCALING_MODES:
            raise ProjectFormatError(
                f"unknown presentation scaling mode: {scaling!r}"
            )
        matte_color = _color(
            game.get("matte_color", [0, 0, 0, 255]),
            "matte color",
        )
        resizable = game.get("resizable", True)
        fullscreen = game.get("fullscreen", False)
        if not isinstance(resizable, bool):
            raise ProjectFormatError("resizable must be a boolean")
        if not isinstance(fullscreen, bool):
            raise ProjectFormatError("fullscreen must be a boolean")
        export = _export_config(raw.get("export", {}))
        network = _network_config(raw.get("network", {}))
        return cls(
            path,
            tuple(dict.fromkeys(modules)),
            width,
            height,
            grid,
            scaling,
            matte_color,
            resizable,
            fullscreen,
            export,
            network,
        )

    def write(self) -> Path:
        """Atomically write this configuration and return its file path.

        Raises:
            ProjectFormatError: If the temporary-file write or replacement
                fails.
            OSError: If the parent directory cannot be created.
            ValueError: If the configuration cannot be encoded as JSON.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            "format": PROJECT_FORMAT,
            "version": PROJECT_VERSION,
            "game": {
                "width": self.width,
                "height": self.height,
                "grid_cell_size": self.grid_cell_size,
                "scaling": self.scaling,
                "matte_color": list(self.matte_color),
                "resizable": self.resizable,
                "fullscreen": self.fullscreen,
            },
            "script_modules": list(self.script_modules),
            "export": {
                "name": self.export.name,
                "version": self.export.version,
                "startup_scene": self.export.startup_scene,
                "icon": self.export.icon,
                "hidden_imports": list(self.export.hidden_imports),
                "collect_packages": list(
                    self.export.collect_packages
                ),
                "server_startup_scene": (
                    self.export.server_startup_scene
                ),
            },
            "network": {
                "enabled": self.network.enabled,
                "endpoint": self.network.endpoint,
                "bind": self.network.bind,
                "port": self.network.port,
                "max_clients": self.network.max_clients,
                "snapshot_hz": self.network.snapshot_hz,
                "auto_connect": self.network.auto_connect,
            },
        }
        text = json.dumps(
            raw,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(text)
                temporary.write("\n")
                temporary_path = Path(temporary.name)
            temporary_path.replace(self.path)
        except OSError as error:
            raise ProjectFormatError(
                f"could not write project {self.path}: {error}"
            ) from error
        return self.path

    def with_module(self, module: str) -> ProjectConfig:
        """Return a copy with ``module`` appended if not already present.

        Args:
            module: Dotted Python module name.

        Raises:
            ProjectFormatError: If ``module`` is not a valid Python name.
        """
        if not module or any(
            not part.isidentifier() for part in module.split(".")
        ):
            raise ProjectFormatError(
                f"invalid Python module name: {module!r}"
            )
        return ProjectConfig(
            self.path,
            tuple(dict.fromkeys((*self.script_modules, module))),
            self.width,
            self.height,
            self.grid_cell_size,
            self.scaling,
            self.matte_color,
            self.resizable,
            self.fullscreen,
            self.export,
            self.network,
        )

    def without_module(self, module: str) -> ProjectConfig:
        """Return a copy with all occurrences of ``module`` removed.

        Args:
            module: Dotted module name to remove.
        """
        return ProjectConfig(
            self.path,
            tuple(
                current
                for current in self.script_modules
                if current != module
            ),
            self.width,
            self.height,
            self.grid_cell_size,
            self.scaling,
            self.matte_color,
            self.resizable,
            self.fullscreen,
            self.export,
            self.network,
        )

    def with_export(
        self,
        export: ProjectExportConfig,
    ) -> ProjectConfig:
        """Return a copy using the supplied export settings.

        Args:
            export: Replacement export settings.
        """
        return ProjectConfig(
            self.path,
            self.script_modules,
            self.width,
            self.height,
            self.grid_cell_size,
            self.scaling,
            self.matte_color,
            self.resizable,
            self.fullscreen,
            export,
            self.network,
        )

    def with_network(
        self,
        network: ProjectNetworkConfig,
    ) -> ProjectConfig:
        """Return a copy using the supplied network settings.

        Args:
            network: Replacement network settings.
        """
        return ProjectConfig(
            self.path,
            self.script_modules,
            self.width,
            self.height,
            self.grid_cell_size,
            self.scaling,
            self.matte_color,
            self.resizable,
            self.fullscreen,
            self.export,
            network,
        )

    def with_game_settings(
        self,
        *,
        width: int,
        height: int,
        grid_cell_size: int,
        scaling: ScalingMode,
        matte_color: Color,
        resizable: bool,
        fullscreen: bool,
    ) -> ProjectConfig:
        """Return a copy with the supplied game display and grid settings.

        Args:
            width: Logical game width in pixels.
            height: Logical game height in pixels.
            grid_cell_size: Editor grid cell size in pixels.
            scaling: Presentation scaling mode.
            matte_color: Color outside the presentation area.
            resizable: Whether the game window may be resized.
            fullscreen: Whether the game starts fullscreen.
        """
        return ProjectConfig(
            self.path,
            self.script_modules,
            width,
            height,
            grid_cell_size,
            scaling,
            matte_color,
            resizable,
            fullscreen,
            self.export,
            self.network,
        )

    def module_for_path(self, filename: str | Path) -> str:
        """Convert a project-local Python file path to a module name.

        Args:
            filename: Python source file inside the project directory.

        Returns:
            Dotted import name corresponding to the path.

        Raises:
            ProjectFormatError: If the path is outside the project, is not a
                Python file, or contains invalid module-name parts.
        """
        path = Path(filename).expanduser().resolve()
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise ProjectFormatError(
                "script must be inside the project directory"
            ) from None
        if path.suffix != ".py":
            raise ProjectFormatError("project scripts must be Python files")
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if not parts or any(not part.isidentifier() for part in parts):
            raise ProjectFormatError(
                "script folders and filenames must be valid Python names"
            )
        return ".".join(parts)

    def import_scripts(self) -> tuple[ModuleType, ...]:
        """Import or reload configured modules from this project.

        Returns:
            Imported module objects in configured order.

        Raises:
            ProjectImportError: If a configured module raises while importing.
            ProjectFormatError: If a module has no source file or resolves
                outside this project.
        """
        invalidate_caches()
        root_text = str(self.root)
        inserted = root_text not in sys.path
        if inserted:
            sys.path.insert(0, root_text)
        try:
            imported: list[ModuleType] = []
            for module_name in self.script_modules:
                expected = self._module_source(module_name)
                try:
                    module = _import_or_reload(module_name, expected)
                except Exception as error:
                    raise ProjectImportError(
                        f"could not import project module "
                        f"{module_name!r}:\n{traceback.format_exc()}"
                    ) from error
                source = getattr(module, "__file__", None)
                if source is None:
                    raise ProjectFormatError(
                        f"project module has no source file: {module_name}"
                    )
                resolved = Path(source).resolve()
                if (
                    resolved != expected
                    and not _frozen_module_matches(
                        resolved,
                        expected,
                        self.root,
                    )
                ):
                    raise ProjectFormatError(
                        f"module {module_name!r} resolved outside the "
                        f"project: {resolved}"
                    )
                imported.append(module)
            return tuple(imported)
        finally:
            if inserted:
                sys.path.remove(root_text)

    def changed_scripts(self) -> tuple[str, ...]:
        """Return loaded script modules whose source changed on disk."""
        changed: list[str] = []
        for module_name in self.script_modules:
            stamp = _script_stamps.get(module_name)
            try:
                current = _source_stamp(self._module_source(module_name))
            except (OSError, ProjectFormatError):
                continue
            if current != stamp:
                changed.append(module_name)
        return tuple(changed)

    def _module_source(self, module: str) -> Path:
        relative = Path(*module.split("."))
        source = (self.root / relative).with_suffix(".py")
        package = self.root / relative / "__init__.py"
        if source.is_file():
            resolved = source.resolve()
        elif package.is_file():
            resolved = package.resolve()
        else:
            raise ProjectFormatError(
                f"project module does not exist: {module}"
            )
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise ProjectFormatError(
                f"project module resolves outside the project: {module}"
            ) from None
        return resolved


def _positive_integer(value: object, name: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ProjectFormatError(f"{name} must be a positive integer")
    return value


def _color(value: object, name: str) -> Color:
    if (
        not isinstance(value, list)
        or len(value) not in {3, 4}
        or any(
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or not 0 <= channel <= 255
            for channel in value
        )
    ):
        raise ProjectFormatError(
            f"{name} must contain three or four integer channels"
        )
    return tuple(value)


def _export_config(value: object) -> ProjectExportConfig:
    if not isinstance(value, dict):
        raise ProjectFormatError("export must be an object")
    name = value.get("name", "Game")
    version = value.get("version", "0.1.0")
    startup_scene = value.get("startup_scene", "")
    server_startup_scene = value.get("server_startup_scene", "")
    icon = value.get("icon")
    if not isinstance(name, str) or not name.strip():
        raise ProjectFormatError("export name must be a non-empty string")
    if not isinstance(version, str) or not version.strip():
        raise ProjectFormatError(
            "export version must be a non-empty string"
        )
    if not isinstance(startup_scene, str):
        raise ProjectFormatError("export startup_scene must be a string")
    if not isinstance(server_startup_scene, str):
        raise ProjectFormatError(
            "export server_startup_scene must be a string"
        )
    if icon is not None and not isinstance(icon, str):
        raise ProjectFormatError("export icon must be a string or null")
    hidden_imports = _string_tuple(
        value.get("hidden_imports", []),
        "export hidden_imports",
    )
    collect_packages = _string_tuple(
        value.get("collect_packages", []),
        "export collect_packages",
    )
    return ProjectExportConfig(
        name.strip(),
        version.strip(),
        startup_scene,
        icon,
        hidden_imports,
        collect_packages,
        server_startup_scene,
    )


def _network_config(value: object) -> ProjectNetworkConfig:
    if not isinstance(value, dict):
        raise ProjectFormatError("network must be an object")
    enabled = value.get("enabled", False)
    endpoint = value.get("endpoint", "ws://127.0.0.1:7777")
    bind = value.get("bind", "127.0.0.1")
    if not isinstance(enabled, bool):
        raise ProjectFormatError("network enabled must be a boolean")
    if (
        not isinstance(endpoint, str)
        or not endpoint.startswith(("ws://", "wss://"))
    ):
        raise ProjectFormatError(
            "network endpoint must be a ws:// or wss:// address"
        )
    if not isinstance(bind, str) or not bind:
        raise ProjectFormatError("network bind must be a non-empty string")
    port = _positive_integer(value.get("port", 7777), "network port")
    if port > 65535:
        raise ProjectFormatError("network port must not exceed 65535")
    max_clients = _positive_integer(
        value.get("max_clients", 8),
        "network max_clients",
    )
    snapshot_hz = _positive_integer(
        value.get("snapshot_hz", 20),
        "network snapshot_hz",
    )
    auto_connect = value.get("auto_connect", True)
    if not isinstance(auto_connect, bool):
        raise ProjectFormatError("network auto_connect must be a boolean")
    return ProjectNetworkConfig(
        enabled,
        endpoint,
        bind,
        port,
        max_clients,
        snapshot_hz,
        auto_connect,
    )


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(
            not isinstance(item, str) or not item.strip()
            for item in value
        )
    ):
        raise ProjectFormatError(
            f"{name} must contain non-empty strings"
        )
    return tuple(dict.fromkeys(item.strip() for item in value))


_script_stamps: dict[str, tuple[int, int]] = {}


def _source_stamp(path: Path) -> tuple[int, int]:
    status = path.stat()
    return status.st_mtime_ns, status.st_size


def _import_or_reload(module_name: str, source: Path) -> ModuleType:
    stamp = _source_stamp(source)
    existing = sys.modules.get(module_name)
    previous = _script_stamps.get(module_name)
    if existing is None or previous is None or previous == stamp:
        _script_stamps[module_name] = stamp
        module = import_module(module_name)
        return module
    from .persistence import scene_types

    # Record the stamp first so a broken edit is reported once, not retried.
    _script_stamps[module_name] = stamp
    registry_state = scene_types._save_state()
    scene_types.unregister_module(module_name)
    try:
        Path(cache_from_source(str(source))).unlink(missing_ok=True)
    except OSError:
        pass
    invalidate_caches()
    try:
        return reload(existing)
    except Exception:
        scene_types._load_state(registry_state)
        raise


def _frozen_module_matches(
    resolved: Path,
    expected: Path,
    root: Path,
) -> bool:
    if not getattr(sys, "frozen", False):
        return False
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    if resolved == expected:
        return True
    if resolved.suffix in {".pyc", ".pyo"}:
        return resolved.with_suffix(".py") == expected
    return False
