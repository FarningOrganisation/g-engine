from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys

from .persistence import SceneDocument, SceneFormatError
from .project import ProjectConfig, ProjectFormatError


_EXCLUDED_NAMES = {
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


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedExport:
    project: ProjectConfig
    staging_directory: Path
    build_directory: Path
    dist_directory: Path
    bootstrap: Path
    spec: Path
    python: Path
    server_bootstrap: Path | None = None
    server_spec: Path | None = None


@dataclass(frozen=True)
class ExportResult:
    output_directory: Path
    executable: Path
    server_output_directory: Path | None = None
    server_executable: Path | None = None


def validate_export_project(
    project: ProjectConfig,
) -> Path:
    export = project.export
    if not export.startup_scene:
        raise ExportError("choose a startup scene before exporting")
    if any(
        character in export.name
        for character in '<>:"/\\|?*\0'
    ):
        raise ExportError(
            "export name contains characters that are invalid in filenames"
        )
    startup_scene = _project_path(
        project,
        export.startup_scene,
        "startup scene",
    )
    if not startup_scene.is_file():
        raise ExportError(
            f"startup scene does not exist: {export.startup_scene}"
        )
    try:
        SceneDocument.read(startup_scene)
    except SceneFormatError as error:
        raise ExportError(
            f"startup scene is invalid: {error}"
        ) from error
    if project.network.enabled and export.server_startup_scene:
        server_startup_scene = _project_path(
            project,
            export.server_startup_scene,
            "server startup scene",
        )
        if not server_startup_scene.is_file():
            raise ExportError(
                "server startup scene does not exist: "
                f"{export.server_startup_scene}"
            )
        try:
            SceneDocument.read(server_startup_scene)
        except SceneFormatError as error:
            raise ExportError(
                f"server startup scene is invalid: {error}"
            ) from error
    if export.icon:
        icon = _project_path(project, export.icon, "export icon")
        if not icon.is_file():
            raise ExportError(f"export icon does not exist: {export.icon}")
    for module in (
        *project.script_modules,
        *export.hidden_imports,
        *export.collect_packages,
    ):
        if any(not part.isidentifier() for part in module.split(".")):
            raise ExportError(f"invalid Python module name: {module!r}")
    return startup_scene


def prepare_export(
    project_file: str | Path,
    *,
    python: str | Path | None = None,
) -> PreparedExport:
    project = ProjectConfig.read(project_file)
    validate_export_project(project)
    root = project.root
    build_directory = root / ".g-export"
    staging_directory = build_directory / "staging"
    work_directory = build_directory / "work"
    dist_directory = root / "dist"
    _replace_directory(staging_directory)
    work_directory.mkdir(parents=True, exist_ok=True)
    dist_directory.mkdir(parents=True, exist_ok=True)
    _stage_project(root, staging_directory)
    bootstrap = staging_directory / "_g_bootstrap.py"
    bootstrap.write_text(
        _bootstrap_source(project),
        encoding="utf-8",
    )
    spec = build_directory / "game.spec"
    spec.write_text(
        _spec_source(project, staging_directory, bootstrap),
        encoding="utf-8",
    )
    server_bootstrap = None
    server_spec = None
    if project.network.enabled:
        server_bootstrap = staging_directory / "_g_server_bootstrap.py"
        server_bootstrap.write_text(
            _server_bootstrap_source(project),
            encoding="utf-8",
        )
        server_spec = build_directory / "server.spec"
        server_spec.write_text(
            _spec_source(
                project,
                staging_directory,
                server_bootstrap,
                name=f"{project.export.name}Server",
                console=True,
            ),
            encoding="utf-8",
        )
    return PreparedExport(
        project,
        staging_directory,
        work_directory,
        dist_directory,
        bootstrap,
        spec,
        Path(python).expanduser().resolve()
        if python is not None
        else project_python(root),
        server_bootstrap,
        server_spec,
    )


def export_project(
    project_file: str | Path,
    *,
    python: str | Path | None = None,
    output: Callable[[str], None] = print,
) -> ExportResult:
    prepared = prepare_export(project_file, python=python)
    _ensure_pyinstaller(prepared.python)
    output(
        f"Exporting {prepared.project.export.name} with "
        f"{prepared.python}"
    )
    _run_pyinstaller(prepared, prepared.spec, output)
    output_directory = (
        prepared.dist_directory / prepared.project.export.name
    )
    executable_name = prepared.project.export.name + (
        ".exe" if sys.platform == "win32" else ""
    )
    executable = output_directory / executable_name
    if not executable.is_file():
        raise ExportError(
            f"PyInstaller completed but executable is missing: {executable}"
        )
    server_output_directory = None
    server_executable = None
    if prepared.server_spec is not None:
        server_name = f"{prepared.project.export.name}Server"
        output(f"Exporting {server_name}")
        _run_pyinstaller(prepared, prepared.server_spec, output)
        server_output_directory = prepared.dist_directory / server_name
        server_executable = server_output_directory / (
            server_name + (".exe" if sys.platform == "win32" else "")
        )
        if not server_executable.is_file():
            raise ExportError(
                "PyInstaller completed but server executable is missing: "
                f"{server_executable}"
            )
    output(f"Export complete: {output_directory}")
    if server_output_directory is not None:
        output(f"Server export complete: {server_output_directory}")
    return ExportResult(
        output_directory,
        executable,
        server_output_directory,
        server_executable,
    )


def _run_pyinstaller(
    prepared: PreparedExport,
    spec: Path,
    output: Callable[[str], None],
) -> None:
    command = [
        str(prepared.python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(prepared.dist_directory),
        "--workpath",
        str(prepared.build_directory / "work"),
        str(spec),
    ]
    process = subprocess.Popen(
        command,
        cwd=prepared.project.root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is not None:
        for line in process.stdout:
            output(line.rstrip())
    return_code = process.wait()
    if return_code != 0:
        raise ExportError(
            f"PyInstaller failed with exit code {return_code}"
        )


def project_python(root: str | Path) -> Path:
    project_root = Path(root).expanduser().resolve()
    candidates = (
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(sys.executable).resolve()


def _ensure_pyinstaller(python: Path) -> None:
    result = subprocess.run(
        [str(python), "-m", "PyInstaller", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ExportError(
            "PyInstaller is not installed in the project environment. "
            f"Run: {python} -m pip install 'pyinstaller>=6,<7'"
        )


def _project_path(
    project: ProjectConfig,
    relative: str,
    label: str,
) -> Path:
    path = (project.root / relative).resolve()
    try:
        path.relative_to(project.root)
    except ValueError:
        raise ExportError(
            f"{label} must be inside the project directory"
        ) from None
    return path


def _replace_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _stage_project(root: Path, staging: Path) -> None:
    for source in root.iterdir():
        if source.name in _EXCLUDED_NAMES or source.name.startswith("."):
            continue
        destination = staging / source.name
        if source.is_dir():
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                    "*.pyo",
                ),
            )
        elif source.is_file():
            shutil.copy2(source, destination)


def _bootstrap_source(project: ProjectConfig) -> str:
    startup = project.export.startup_scene
    return (
        "from pathlib import Path\n"
        "import sys\n\n"
        "import pyglet\n"
        "pyglet.options['debug_gl'] = False\n\n"
        "from g import Game\n\n"
        "ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))\n"
        "game = Game.from_project(ROOT / 'g.project.json', network_role='client')\n"
        f"game.scenes.load('startup', str(ROOT / {startup!r}), "
        "asset_root=str(ROOT))\n"
        "game.scenes.switch('startup')\n"
        + (
            "if game.network.state == 'disconnected':\n"
            f"    game.network.connect({project.network.endpoint!r})\n"
            if project.network.enabled and project.network.auto_connect else ""
        )
        +
        f"game.run(title={project.export.name!r})\n"
    )


def _server_bootstrap_source(project: ProjectConfig) -> str:
    startup = (
        project.export.server_startup_scene
        or project.export.startup_scene
    )
    return (
        "import argparse\n"
        "import os\n"
        "from pathlib import Path\n"
        "import platform\n"
        "import sys\n\n"
        "os.environ['G_HEADLESS_SERVER'] = '1'\n"
        "if (platform.system() == 'Linux'\n"
        "        and not os.environ.get('DISPLAY')\n"
        "        and not os.environ.get('WAYLAND_DISPLAY')):\n"
        "    os.environ['PYGLET_HEADLESS'] = 'true'\n"
        "import pyglet\n"
        "pyglet.options['audio'] = ('silent',)\n\n"
        "pyglet.options['shadow_window'] = False\n\n"
        "from g import Game\n\n"
        "parser = argparse.ArgumentParser(description='g dedicated server')\n"
        f"parser.add_argument('--bind', default={project.network.bind!r})\n"
        f"parser.add_argument('--port', type=int, default={project.network.port!r})\n"
        "args = parser.parse_args()\n"
        "ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))\n"
        "game = Game.from_project(ROOT / 'g.project.json', network_role='server')\n"
        f"game.scenes.load('startup', str(ROOT / {startup!r}), "
        "asset_root=str(ROOT))\n"
        "game.scenes.switch('startup')\n"
        "port = game.network.listen(args.bind, args.port)\n"
        "print(f'Server listening on {args.bind}:{port}', flush=True)\n"
        "game.run_server()\n"
    )


def _spec_source(
    project: ProjectConfig,
    staging: Path,
    bootstrap: Path,
    *,
    name: str | None = None,
    console: bool = False,
) -> str:
    target_name = name or project.export.name
    engine_root = Path(__file__).resolve().parent.parent
    datas: list[tuple[str, str]] = []
    for source in staging.rglob("*"):
        if not source.is_file() or source == bootstrap:
            continue
        relative = source.relative_to(staging)
        datas.append((str(source), str(relative.parent)))
    hidden_imports = tuple(
        dict.fromkeys(
            (*project.script_modules, *project.export.hidden_imports)
        )
    )
    icon = (
        str(staging / project.export.icon)
        if project.export.icon
        else None
    )
    collect_lines = "\n".join(
        f"datas += collect_data_files({package!r})"
        for package in project.export.collect_packages
    )
    return (
        "from PyInstaller.utils.hooks import collect_data_files\n\n"
        f"datas = {datas!r}\n"
        f"{collect_lines}\n"
        "analysis = Analysis(\n"
        f"    [{str(bootstrap)!r}],\n"
        f"    pathex={[str(staging), str(engine_root)]!r},\n"
        "    binaries=[],\n"
        "    datas=datas,\n"
        f"    hiddenimports={list(hidden_imports)!r},\n"
        "    hookspath=[],\n"
        "    runtime_hooks=[],\n"
        "    excludes=[],\n"
        "    noarchive=False,\n"
        ")\n"
        "pyz = PYZ(analysis.pure)\n"
        "exe = EXE(\n"
        "    pyz,\n"
        "    analysis.scripts,\n"
        "    [],\n"
        "    exclude_binaries=True,\n"
        f"    name={target_name!r},\n"
        "    debug=False,\n"
        "    bootloader_ignore_signals=False,\n"
        "    strip=False,\n"
        "    upx=True,\n"
        f"    console={console!r},\n"
        f"    icon={icon!r},\n"
        ")\n"
        "app = COLLECT(\n"
        "    exe,\n"
        "    analysis.binaries,\n"
        "    analysis.datas,\n"
        "    strip=False,\n"
        "    upx=True,\n"
        "    upx_exclude=[],\n"
        f"    name={target_name!r},\n"
        ")\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a g project with PyInstaller"
    )
    parser.add_argument(
        "project",
        nargs="?",
        default="g.project.json",
        help="path to g.project.json",
    )
    parser.add_argument(
        "--python",
        help="Python interpreter containing PyInstaller and dependencies",
    )
    args = parser.parse_args(argv)
    try:
        export_project(args.project, python=args.python)
    except (ExportError, ProjectFormatError, OSError) as error:
        print(f"Export failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
