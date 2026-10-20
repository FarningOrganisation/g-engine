"""Project-wide validation for editor preview, export, and automation."""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Literal

from .persistence import (
    JsonValue,
    SceneDocument,
    SceneFormatError,
    scene_types,
)
from .presentation import SCALING_MODES
from .project import (
    ProjectConfig,
    ProjectFormatError,
    ProjectImportError,
)


ValidationPurpose = Literal["project", "preview", "export"]
ValidationTarget = Literal["linux", "macos", "windows"]

_EXCLUDED_DIRECTORIES = {
    ".git",
    ".g-export",
    ".idea",
    ".pytest_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
}
_IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}
_FONT_EXTENSIONS = {".otf", ".ttc", ".ttf", ".woff", ".woff2"}
_AUDIO_EXTENSIONS = {".flac", ".m4a", ".mp3", ".ogg", ".wav"}
_SHADER_EXTENSIONS = {".frag", ".glsl", ".shader", ".vert"}
_RESOURCE_KEYS = {
    "audio",
    "background_image",
    "effect",
    "font",
    "fragment_shader",
    "icon",
    "image",
    "images",
    "music",
    "shader",
    "sound",
    "vertex_shader",
}
_GUI_TYPES = {
    "anchor",
    "button",
    "canvas",
    "grid",
    "hbox",
    "label",
    "panel",
    "scroll",
    "slider",
    "stack",
    "text_input",
    "toggle",
    "vbox",
}
_WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CLOCK$",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_VERSION_PATTERN = re.compile(
    r"^[0-9]+(?:\.[0-9]+){0,3}(?:[-+][0-9A-Za-z.-]+)?$"
)


@dataclass(frozen=True)
class ValidationIssue:
    """One actionable project validation result."""

    code: str
    message: str
    path: Path | None = None
    location: str | None = None
    hint: str | None = None

    def format(self, root: Path | None = None) -> str:
        """Format this issue with an optional project-relative path."""
        source = ""
        if self.path is not None:
            path = self.path
            if root is not None:
                try:
                    path = path.relative_to(root)
                except ValueError:
                    pass
            source = str(path)
            if self.location:
                source += f": {self.location}"
            source += ": "
        text = f"[{self.code}] {source}{self.message}"
        if self.hint:
            text += f" Fix: {self.hint}"
        return text


@dataclass(frozen=True)
class ValidationReport:
    """The complete result of one project validation pass."""

    project: ProjectConfig | None
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        """Whether validation completed without any errors."""
        return not self.issues

    def format(self) -> str:
        """Format the complete report as readable multi-line text."""
        if self.is_valid:
            return "Project validation passed."
        root = self.project.root if self.project is not None else None
        lines = [
            f"Project validation found {len(self.issues)} error"
            f"{'' if len(self.issues) == 1 else 's'}:"
        ]
        lines.extend(
            f"{index}. {issue.format(root)}"
            for index, issue in enumerate(self.issues, 1)
        )
        return "\n".join(lines)


class ProjectValidationError(ValueError):
    """Raised when a workflow requires a valid project."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(report.format())


def validate_project(
    project: ProjectConfig | str | Path,
    *,
    purpose: ValidationPurpose = "project",
    target: ValidationTarget | None = None,
    document_overrides: Mapping[Path, SceneDocument] | None = None,
    import_scripts: bool = True,
) -> ValidationReport:
    """Collect independent project errors without stopping at the first one."""
    if purpose not in {"project", "preview", "export"}:
        raise ValueError(f"unknown validation purpose: {purpose!r}")
    try:
        configured = (
            project
            if isinstance(project, ProjectConfig)
            else ProjectConfig.read(project)
        )
    except ProjectFormatError as error:
        path = Path(project).expanduser().resolve()
        return ValidationReport(
            None,
            (
                ValidationIssue(
                    "project.invalid",
                    str(error),
                    path,
                    hint="Correct the project file format and settings.",
                ),
            ),
        )

    issues: list[ValidationIssue] = []
    _validate_project_settings(
        configured,
        purpose,
        target or _current_target(),
        issues,
    )
    scripts_valid = _validate_scripts(configured, issues)
    loaded_modules_before = set(sys.modules)
    if import_scripts and scripts_valid:
        try:
            configured.import_scripts()
        except (
            ProjectFormatError,
            ProjectImportError,
            ImportError,
            RuntimeError,
        ) as error:
            issues.append(ValidationIssue(
                "script.import",
                str(error),
                configured.path,
                "script_modules",
                "Correct the exception raised while importing the module.",
            ))

    overrides = {
        path.expanduser().resolve(): document
        for path, document in (document_overrides or {}).items()
    }
    seen_paths: set[Path] = set()
    for path in _project_files(configured.root):
        resolved = path.resolve()
        if path.name.endswith(".scene.json"):
            seen_paths.add(resolved)
            document = overrides.get(resolved)
            if document is None:
                try:
                    document = SceneDocument.read(path)
                except SceneFormatError as error:
                    issues.append(ValidationIssue(
                        "scene.invalid",
                        str(error),
                        path,
                        hint="Open and correct the scene file.",
                    ))
                    continue
            _validate_scene_document(configured, path, document, issues)
        elif path.name.endswith(".prefab.json"):
            seen_paths.add(resolved)
            _validate_prefab(configured, path, issues)
        elif path.suffix.lower() == ".json":
            _validate_json_resources(configured, path, issues)
    for path, document in overrides.items():
        if path not in seen_paths:
            _validate_scene_document(configured, path, document, issues)

    if import_scripts and scripts_valid:
        _cleanup_imported_project_modules(
            configured.root,
            loaded_modules_before,
            configured.script_modules,
        )
    return ValidationReport(configured, tuple(issues))


def require_valid_project(
    project: ProjectConfig | str | Path,
    *,
    purpose: ValidationPurpose = "project",
    target: ValidationTarget | None = None,
    document_overrides: Mapping[Path, SceneDocument] | None = None,
    import_scripts: bool = True,
) -> ValidationReport:
    """Return a valid report or raise :class:`ProjectValidationError`."""
    report = validate_project(
        project,
        purpose=purpose,
        target=target,
        document_overrides=document_overrides,
        import_scripts=import_scripts,
    )
    if not report.is_valid:
        raise ProjectValidationError(report)
    return report


def _validate_project_settings(
    project: ProjectConfig,
    purpose: ValidationPurpose,
    target: ValidationTarget,
    issues: list[ValidationIssue],
) -> None:
    for name, value in (
        ("width", project.width),
        ("height", project.height),
        ("grid_cell_size", project.grid_cell_size),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            issues.append(ValidationIssue(
                "project.display",
                f"{name} must be a positive integer",
                project.path,
                f"game.{name}",
            ))
    if project.scaling not in SCALING_MODES:
        issues.append(ValidationIssue(
            "project.display",
            f"unknown presentation scaling mode: {project.scaling!r}",
            project.path,
            "game.scaling",
        ))
    if (
        len(project.matte_color) not in {3, 4}
        or any(
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or not 0 <= channel <= 255
            for channel in project.matte_color
        )
    ):
        issues.append(ValidationIssue(
            "project.display",
            "matte_color must contain three or four byte channels",
            project.path,
            "game.matte_color",
        ))
    export = project.export
    if not export.name.strip():
        issues.append(ValidationIssue(
            "export.name",
            "export name must not be empty",
            project.path,
            "export.name",
        ))
    elif any(character in export.name for character in '<>:"/\\|?*\0'):
        issues.append(ValidationIssue(
            "export.name",
            "export name contains characters invalid in filenames",
            project.path,
            "export.name",
        ))
    if target == "windows" and export.name:
        stem = export.name.split(".", 1)[0].upper()
        if export.name != export.name.rstrip(" .") or stem in _WINDOWS_RESERVED_NAMES:
            issues.append(ValidationIssue(
                "export.platform",
                f"{export.name!r} is not a valid Windows application name",
                project.path,
                "export.name",
                "Choose a name that is not reserved and does not end in a space or period.",
            ))
    if not export.version.strip() or not _VERSION_PATTERN.fullmatch(
        export.version.strip()
    ):
        issues.append(ValidationIssue(
            "export.version",
            f"invalid export version: {export.version!r}",
            project.path,
            "export.version",
            "Use a numeric version such as 1.0.0.",
        ))
    if purpose == "export" and not export.startup_scene:
        issues.append(ValidationIssue(
            "startup.missing",
            "choose a startup scene before exporting",
            project.path,
            "export.startup_scene",
        ))
    for label, value in (
        ("startup scene", export.startup_scene),
        ("server startup scene", export.server_startup_scene),
    ):
        if value:
            _validate_resource_path(
                project,
                value,
                project.path,
                f"export.{label.replace(' ', '_')}",
                "scene",
                issues,
            )
    if export.icon:
        _validate_resource_path(
            project,
            export.icon,
            project.path,
            "export.icon",
            "image",
            issues,
        )
    for field_name, modules in (
        ("hidden_imports", export.hidden_imports),
        ("collect_packages", export.collect_packages),
    ):
        for module in modules:
            if any(not part.isidentifier() for part in module.split(".")):
                issues.append(ValidationIssue(
                    "export.module",
                    f"invalid Python module name: {module!r}",
                    project.path,
                    f"export.{field_name}",
                ))


def _validate_scripts(
    project: ProjectConfig,
    issues: list[ValidationIssue],
) -> bool:
    valid = True
    for module in project.script_modules:
        base = project.root.joinpath(*module.split("."))
        candidates = (base.with_suffix(".py"), base / "__init__.py")
        source = next((path for path in candidates if path.is_file()), None)
        if source is None:
            valid = False
            issues.append(ValidationIssue(
                "script.missing",
                f"script module does not exist: {module}",
                project.path,
                "script_modules",
                f"Create {module.replace('.', '/')}.py or remove the module from the project.",
            ))
            continue
        try:
            text = source.read_text(encoding="utf-8")
            compile(text, str(source), "exec")
        except (OSError, SyntaxError, UnicodeError) as error:
            valid = False
            issues.append(ValidationIssue(
                "script.invalid",
                f"invalid script module {module}: {error}",
                source,
                hint="Correct the script syntax or encoding.",
            ))
    return valid


def _validate_scene_document(
    project: ProjectConfig,
    path: Path,
    document: SceneDocument,
    issues: list[ValidationIssue],
) -> None:
    try:
        SceneDocument.from_dict(document.to_dict())
    except SceneFormatError as error:
        issues.append(ValidationIssue(
            "scene.invalid",
            str(error),
            path,
            hint="Correct the scene structure in the editor.",
        ))
        return
    records: list[dict[str, JsonValue]] = []
    for layer_index, layer in enumerate(document.layers):
        if layer.get("type", "object") == "object":
            raw_records = layer.get("objects", [])
            if isinstance(raw_records, list):
                records.extend(
                    item for item in raw_records if isinstance(item, dict)
                )
        _scan_resource_values(
            project,
            layer,
            path,
            f"layers[{layer_index}]",
            issues,
        )
    _validate_object_tree(path, records, issues)
    _validate_prefab_references(
        project,
        path,
        _walk_records(records, "children"),
        issues,
    )
    for view_index, view in enumerate(document.ui_views):
        elements = view.get("elements", [])
        if isinstance(elements, list):
            _validate_gui_tree(
                path,
                [
                    item for item in elements
                    if isinstance(item, dict)
                ],
                f"ui_views[{view_index}]",
                issues,
            )
            _validate_prefab_references(
                project,
                path,
                _walk_records(
                    [
                        item for item in elements
                        if isinstance(item, dict)
                    ],
                    "children",
                    f"ui_views[{view_index}].elements",
                ),
                issues,
            )
        _validate_prefab_references(
            project,
            path,
            iter(((f"ui_views[{view_index}]", view),)),
            issues,
        )
        _scan_resource_values(
            project,
            view,
            path,
            f"ui_views[{view_index}]",
            issues,
        )
    _scan_resource_values(
        project,
        document.settings,
        path,
        "settings",
        issues,
    )


def _validate_object_tree(
    path: Path,
    roots: list[dict[str, JsonValue]],
    issues: list[ValidationIssue],
) -> None:
    records = list(_walk_records(roots, "children"))
    identifiers: set[str] = set()
    for location, record in records:
        identifier = record.get("id")
        if isinstance(identifier, str):
            if identifier in identifiers:
                issues.append(ValidationIssue(
                    "reference.duplicate",
                    f"duplicate object ID: {identifier!r}",
                    path,
                    location,
                    "Assign every object a unique ID.",
                ))
            identifiers.add(identifier)
        object_type = record.get("type", "g.game_object")
        if not isinstance(object_type, str):
            issues.append(ValidationIssue(
                "type.object",
                "serialized object type must be a string",
                path,
                location,
            ))
        else:
            try:
                scene_types.object_codec_for_name(object_type)
            except SceneFormatError as error:
                issues.append(ValidationIssue(
                    "type.object",
                    str(error),
                    path,
                    location,
                    "Register the object type from a project script or replace it.",
                ))
        components = record.get("components", [])
        if isinstance(components, list):
            for index, component in enumerate(components):
                if not isinstance(component, dict):
                    issues.append(ValidationIssue(
                        "type.component",
                        "serialized component must be an object",
                        path,
                        f"{location}.components[{index}]",
                    ))
                    continue
                component_type = component.get("type")
                try:
                    if not isinstance(component_type, str):
                        raise SceneFormatError(
                            "serialized component type must be a string"
                        )
                    scene_types.component_codec_for_name(component_type)
                except SceneFormatError as error:
                    issues.append(ValidationIssue(
                        "type.component",
                        str(error),
                        path,
                        f"{location}.components[{index}]",
                        "Register the component from a project script or remove it.",
                    ))
    for location, record in records:
        for reference_location, identifier in _object_references(
            record,
            location,
        ):
            if identifier not in identifiers:
                issues.append(ValidationIssue(
                    "reference.object",
                    f"object reference targets missing ID {identifier!r}",
                    path,
                    reference_location,
                    "Select an existing object or clear the reference.",
                ))


def _validate_gui_tree(
    path: Path,
    roots: list[dict[str, JsonValue]],
    prefix: str,
    issues: list[ValidationIssue],
) -> None:
    for location, record in _walk_records(roots, "children", prefix):
        widget_type = record.get("type")
        if widget_type not in _GUI_TYPES:
            issues.append(ValidationIssue(
                "type.gui",
                f"unsupported GUI element type: {widget_type!r}",
                path,
                location,
                "Replace the element with a supported editor widget.",
            ))


def _validate_prefab(
    project: ProjectConfig,
    path: Path,
    issues: list[ValidationIssue],
) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        issues.append(ValidationIssue(
            "prefab.invalid",
            f"could not read prefab: {error}",
            path,
        ))
        return
    if not isinstance(raw, dict):
        issues.append(ValidationIssue(
            "prefab.invalid",
            "prefab root must be an object",
            path,
        ))
        return
    if raw.get("format") != "g.prefab" or raw.get("version") != 2:
        issues.append(ValidationIssue(
            "prefab.invalid",
            "expected g.prefab format version 2",
            path,
            hint="Open and resave the prefab with a compatible editor.",
        ))
        return
    kind = raw.get("kind")
    template = raw.get("template")
    if kind not in {"game_object", "ui_view"} or not isinstance(
        template,
        dict,
    ):
        issues.append(ValidationIssue(
            "prefab.invalid",
            "prefab kind or template is invalid",
            path,
        ))
        return
    typed = template
    if kind == "game_object":
        _validate_object_tree(path, [typed], issues)
    else:
        elements = typed.get("elements", [])
        if isinstance(elements, list):
            _validate_gui_tree(
                path,
                [item for item in elements if isinstance(item, dict)],
                "template",
                issues,
            )
    _scan_resource_values(project, typed, path, "template", issues)


def _validate_prefab_references(
    project: ProjectConfig,
    path: Path,
    records: Iterator[tuple[str, dict[str, JsonValue]]],
    issues: list[ValidationIssue],
) -> None:
    for location, record in records:
        metadata = record.get("prefab")
        if metadata is None:
            continue
        if not isinstance(metadata, dict):
            issues.append(ValidationIssue(
                "reference.prefab",
                "prefab metadata must be an object",
                path,
                f"{location}.prefab",
            ))
            continue
        source = metadata.get("source")
        if not isinstance(source, str) or not source:
            issues.append(ValidationIssue(
                "reference.prefab",
                "prefab source must be a non-empty project path",
                path,
                f"{location}.prefab.source",
            ))
            continue
        candidate = Path(source)
        if candidate.is_absolute():
            continue
        resolved = (project.root / candidate).resolve()
        try:
            resolved.relative_to(project.root.resolve())
        except ValueError:
            continue
        if not resolved.is_file():
            continue
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            not isinstance(raw, dict)
            or raw.get("format") != "g.prefab"
            or raw.get("version") != 2
        ):
            continue
        for field_name in ("prefab_id", "kind"):
            expected = raw.get(field_name)
            actual = metadata.get(field_name)
            if actual != expected:
                issues.append(ValidationIssue(
                    "reference.prefab",
                    f"prefab {field_name} {actual!r} does not match "
                    f"{source} ({expected!r})",
                    path,
                    f"{location}.prefab.{field_name}",
                    "Refresh or recreate this prefab instance.",
                ))


def _validate_json_resources(
    project: ProjectConfig,
    path: Path,
    issues: list[ValidationIssue],
) -> None:
    if not path.name.endswith(".particle.json"):
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        issues.append(ValidationIssue(
            "asset.invalid",
            f"could not read asset definition: {error}",
            path,
        ))
        return
    _scan_resource_values(project, raw, path, "$", issues)


def _scan_resource_values(
    project: ProjectConfig,
    value: object,
    source: Path,
    location: str,
    issues: list[ValidationIssue],
    key: str | None = None,
) -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            _scan_resource_values(
                project,
                child,
                source,
                f"{location}.{child_key}",
                issues,
                str(child_key),
            )
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_resource_values(
                project,
                child,
                source,
                f"{location}[{index}]",
                issues,
                key,
            )
        return
    if not isinstance(value, str) or not value:
        return
    kind = _resource_kind(value, key)
    if kind is not None:
        _validate_resource_path(
            project,
            value,
            source,
            location,
            kind,
            issues,
        )


def _resource_kind(value: str, key: str | None) -> str | None:
    lowered = value.lower()
    if lowered.endswith(".scene.json"):
        return "scene"
    if lowered.endswith(".prefab.json"):
        return "prefab"
    if lowered.endswith(".particle.json"):
        return "particle"
    suffix = Path(lowered).suffix
    if key not in _RESOURCE_KEYS:
        return None
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    if suffix in _FONT_EXTENSIONS:
        return "font"
    if suffix in _AUDIO_EXTENSIONS:
        return "audio"
    if suffix in _SHADER_EXTENSIONS:
        return "shader"
    if key == "source" and lowered.endswith(".prefab.json"):
        return "prefab"
    if key in {"audio", "music", "sound"}:
        return "audio"
    return None


def _validate_resource_path(
    project: ProjectConfig,
    value: str,
    source: Path,
    location: str,
    kind: str,
    issues: list[ValidationIssue],
) -> None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        issues.append(ValidationIssue(
            "asset.outside",
            f"{kind} path must be relative to the project: {value}",
            source,
            location,
            "Copy the asset into the project and select it again.",
        ))
        return
    resolved = (project.root / candidate).resolve()
    try:
        resolved.relative_to(project.root.resolve())
    except ValueError:
        issues.append(ValidationIssue(
            "asset.outside",
            f"{kind} path must stay inside the project root: {value}",
            source,
            location,
            "Copy the asset into the project and use a relative path.",
        ))
        return
    if not resolved.is_file():
        issues.append(ValidationIssue(
            f"asset.missing.{kind}",
            f"missing {kind} asset: {value}",
            source,
            location,
            "Restore the file or choose an existing project asset.",
        ))


def _walk_records(
    roots: list[dict[str, JsonValue]],
    child_key: str,
    prefix: str = "objects",
) -> Iterator[tuple[str, dict[str, JsonValue]]]:
    stack = [
        (f"{prefix}[{index}]", record)
        for index, record in reversed(tuple(enumerate(roots)))
    ]
    while stack:
        location, record = stack.pop()
        yield location, record
        children = record.get(child_key, [])
        if isinstance(children, list):
            stack.extend(
                (
                    f"{location}.{child_key}[{index}]",
                    child,
                )
                for index, child in reversed(tuple(enumerate(children)))
                if isinstance(child, dict)
            )


def _object_references(
    value: object,
    location: str,
) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        if set(value) == {"$object"} and isinstance(
            value["$object"],
            str,
        ):
            yield location, value["$object"]
            return
        for key, child in value.items():
            yield from _object_references(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _object_references(
                child,
                f"{location}[{index}]",
            )


def _project_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(
            part in _EXCLUDED_DIRECTORIES
            for part in relative.parts
        ):
            continue
        if path.is_file():
            yield path


def _cleanup_imported_project_modules(
    root: Path,
    loaded_before: set[str],
    configured_modules: tuple[str, ...],
) -> None:
    project_root = root.resolve()
    removed: set[str] = set()
    for name in tuple(set(sys.modules) - loaded_before):
        module = sys.modules.get(name)
        source = getattr(module, "__file__", None)
        if source is None:
            continue
        try:
            in_project = Path(source).resolve().is_relative_to(
                project_root
            )
        except OSError:
            continue
        if in_project:
            scene_types.unregister_module(name)
            sys.modules.pop(name, None)
            removed.add(name)
    for name in configured_modules:
        if name not in loaded_before and name not in removed:
            scene_types.unregister_module(name)


def _current_target() -> ValidationTarget:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def main(argv: list[str] | None = None) -> int:
    """Run project validation from the command line."""
    parser = argparse.ArgumentParser(
        prog="g-validate",
        description="Validate a g-engine project before preview or export.",
    )
    parser.add_argument(
        "project",
        nargs="?",
        default="g.project.json",
        help="project file (default: g.project.json)",
    )
    parser.add_argument(
        "--purpose",
        choices=("project", "preview", "export"),
        default="project",
    )
    parser.add_argument(
        "--target",
        choices=("linux", "macos", "windows"),
    )
    parser.add_argument(
        "--no-import-scripts",
        action="store_true",
        help="check script files without importing project modules",
    )
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    report = validate_project(
        options.project,
        purpose=options.purpose,
        target=options.target,
        import_scripts=not options.no_import_scripts,
    )
    if options.json:
        print(json.dumps({
            "valid": report.is_valid,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "path": str(issue.path) if issue.path else None,
                    "location": issue.location,
                    "hint": issue.hint,
                }
                for issue in report.issues
            ],
        }, indent=2))
    else:
        print(report.format())
    return 0 if report.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
