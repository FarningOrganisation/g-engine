from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Literal


ThemePreference = Literal["system", "light", "dark"]
ResolvedTheme = Literal["light", "dark"]
THEME_PREFERENCES: tuple[ThemePreference, ...] = (
    "system",
    "light",
    "dark",
)


def editor_config_directory() -> Path:
    """Return the directory containing editor-local preferences and layout."""
    return Path.home() / ".g-editor"


def _command_output(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def system_theme() -> ResolvedTheme:
    """Detect the operating system's application appearance.

    Dark remains the fallback to preserve the editor's established appearance
    when the desktop does not expose a supported preference.
    """
    operating_system = platform.system()
    if operating_system == "Darwin":
        appearance = _command_output(
            ["defaults", "read", "-g", "AppleInterfaceStyle"]
        )
        return "dark" if appearance == "Dark" else "light"
    if operating_system == "Windows":
        try:
            registry = import_module("winreg")
            with getattr(registry, "OpenKey")(
                getattr(registry, "HKEY_CURRENT_USER"),
                (
                    r"Software\Microsoft\Windows\CurrentVersion"
                    r"\Themes\Personalize"
                ),
            ) as key:
                value, _ = getattr(registry, "QueryValueEx")(
                    key,
                    "AppsUseLightTheme",
                )
            return "light" if int(value) else "dark"
        except (
            AttributeError,
            OSError,
            ImportError,
            TypeError,
            ValueError,
        ):
            return "dark"
    gtk_theme = os.environ.get("GTK_THEME", "").lower()
    if gtk_theme:
        return "dark" if "dark" in gtk_theme else "light"
    color_scheme = _command_output(
        ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"]
    )
    if color_scheme is not None:
        return "dark" if "dark" in color_scheme.lower() else "light"
    return "dark"


def resolve_theme(preference: ThemePreference) -> ResolvedTheme:
    """Resolve an explicit or system-following theme preference."""
    return system_theme() if preference == "system" else preference


@dataclass
class EditorPreferences:
    """Persistent user preferences for the editor application."""

    theme: ThemePreference = "system"

    @classmethod
    def load(cls, path: Path | None = None) -> EditorPreferences:
        """Load preferences, returning defaults when no file exists."""
        filename = path or editor_config_directory() / "settings.json"
        try:
            raw = json.loads(filename.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("editor settings must contain a JSON object")
        theme = raw.get("theme", "system")
        if theme not in THEME_PREFERENCES:
            raise ValueError(
                "editor theme must be 'system', 'light', or 'dark'"
            )
        return cls(theme=theme)

    def save(self, path: Path | None = None) -> None:
        """Write preferences atomically as human-readable JSON."""
        filename = path or editor_config_directory() / "settings.json"
        filename.parent.mkdir(parents=True, exist_ok=True)
        temporary = filename.with_suffix(f"{filename.suffix}.tmp")
        temporary.write_text(
            json.dumps({"theme": self.theme}, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.replace(filename)
        except OSError:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise


def load_editor_preferences(
    path: Path | None = None,
) -> EditorPreferences:
    """Load preferences and explicitly report a recoverable invalid file."""
    try:
        return EditorPreferences.load(path)
    except (OSError, ValueError) as error:
        print(
            f"Could not load editor settings; using defaults: {error}",
            file=sys.stderr,
        )
        return EditorPreferences()
