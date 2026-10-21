from __future__ import annotations

import inspect
from pathlib import Path
import platform
import shutil
import subprocess

from g.components import Script


def _vscode_command() -> str:
    command = shutil.which("code")
    if command is not None:
        return command
    if platform.system() == "Darwin":
        for applications in (Path("/Applications"), Path.home() / "Applications"):
            command_path = (
                applications
                / "Visual Studio Code.app/Contents/Resources/app/bin/code"
            )
            if command_path.is_file():
                return str(command_path)
    raise ValueError(
        "Visual Studio Code was not found. Install VS Code and run "
        "'Shell Command: Install code command in PATH' from its Command Palette."
    )


def open_script_in_editor(script_type: type[Script]) -> Path:
    try:
        source = inspect.getsourcefile(script_type)
        if source is None:
            raise ValueError(f"No Python source is available for {script_type.__name__}")
        path = Path(source).resolve()
        if not path.is_file():
            raise ValueError(f"Script source does not exist: {path}")
        _, line = inspect.getsourcelines(script_type)
    except (OSError, TypeError) as error:
        raise ValueError(
            f"Cannot locate source for {script_type.__name__}: {error}"
        ) from error
    try:
        subprocess.run(
            [_vscode_command(), "--reuse-window", "--goto", f"{path}:{line}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or str(error)
        raise ValueError(f"Could not open script in VS Code: {detail}") from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Could not open script in VS Code: {error}") from error
    return path
