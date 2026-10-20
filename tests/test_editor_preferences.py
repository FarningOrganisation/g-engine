import io
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import MagicMock, patch

from pyglet.sprite import Sprite
from pyglet.text import Label

from g_editor.application import ImGuiEditor
from g_editor.preferences import (
    EditorPreferences,
    load_editor_preferences,
    resolve_theme,
    system_theme,
)
from g_editor.splash import EditorSplash, splash_branding
from g_editor.ui.theme import (
    LIGHT_THEME_BUTTON,
    LIGHT_THEME_BUTTON_ACTIVE,
    LIGHT_THEME_BUTTON_HOVERED,
    LIGHT_THEME_BUTTON_SELECTED,
    LIGHT_THEME_TEXT,
    configure_style,
)


def _relative_luminance(color: tuple[float, ...]) -> float:
    def linear(channel: float) -> float:
        return (
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )

    red, green, blue = (linear(channel) for channel in color[:3])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(
    first: tuple[float, ...],
    second: tuple[float, ...],
) -> float:
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


class EditorPreferencesTests(unittest.TestCase):
    def test_preferences_round_trip_theme(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            EditorPreferences(theme="light").save(path)

            loaded = EditorPreferences.load(path)

        self.assertEqual(loaded.theme, "light")

    def test_missing_preferences_use_system_theme(self) -> None:
        with TemporaryDirectory() as directory:
            loaded = EditorPreferences.load(
                Path(directory) / "missing.json"
            )

        self.assertEqual(loaded.theme, "system")

    def test_invalid_preferences_are_reported_before_using_defaults(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"theme": "purple"}', encoding="utf-8")
            errors = io.StringIO()

            with patch("sys.stderr", errors):
                loaded = load_editor_preferences(path)

        self.assertEqual(loaded.theme, "system")
        self.assertIn("Could not load editor settings", errors.getvalue())

    def test_macos_theme_uses_global_application_appearance(self) -> None:
        with (
            patch("g_editor.preferences.platform.system", return_value="Darwin"),
            patch(
                "g_editor.preferences._command_output",
                return_value="Dark",
            ),
        ):
            self.assertEqual(system_theme(), "dark")
        with (
            patch("g_editor.preferences.platform.system", return_value="Darwin"),
            patch(
                "g_editor.preferences._command_output",
                return_value=None,
            ),
        ):
            self.assertEqual(system_theme(), "light")

    def test_windows_theme_uses_application_preference(self) -> None:
        registry = types.SimpleNamespace(
            HKEY_CURRENT_USER=object(),
            OpenKey=MagicMock(),
            QueryValueEx=MagicMock(return_value=(1, 0)),
        )
        registry.OpenKey.return_value.__enter__.return_value = object()
        with (
            patch(
                "g_editor.preferences.platform.system",
                return_value="Windows",
            ),
            patch.dict(sys.modules, {"winreg": registry}),
        ):
            self.assertEqual(system_theme(), "light")
            registry.QueryValueEx.return_value = (0, 0)
            self.assertEqual(system_theme(), "dark")

    def test_linux_theme_uses_gtk_then_falls_back_to_dark(self) -> None:
        with (
            patch("g_editor.preferences.platform.system", return_value="Linux"),
            patch.dict(os.environ, {"GTK_THEME": "Adwaita-dark"}, clear=True),
        ):
            self.assertEqual(system_theme(), "dark")
        with (
            patch("g_editor.preferences.platform.system", return_value="Linux"),
            patch.dict(os.environ, {}, clear=True),
            patch(
                "g_editor.preferences._command_output",
                return_value=None,
            ),
        ):
            self.assertEqual(system_theme(), "dark")

    def test_explicit_theme_does_not_consult_operating_system(self) -> None:
        with patch("g_editor.preferences.system_theme") as detect:
            self.assertEqual(resolve_theme("light"), "light")
            self.assertEqual(resolve_theme("dark"), "dark")
        detect.assert_not_called()


class EditorThemeTests(unittest.TestCase):
    def test_light_button_states_have_high_text_contrast(self) -> None:
        for background in (
            LIGHT_THEME_BUTTON,
            LIGHT_THEME_BUTTON_HOVERED,
            LIGHT_THEME_BUTTON_ACTIVE,
            LIGHT_THEME_BUTTON_SELECTED,
        ):
            with self.subTest(background=background):
                self.assertGreaterEqual(
                    _contrast_ratio(LIGHT_THEME_TEXT, background),
                    7,
                )

    def test_configure_style_selects_requested_base_palette(self) -> None:
        style = MagicMock()
        with (
            patch(
                "g_editor.ui.theme.imgui.get_style",
                return_value=style,
            ),
            patch(
                "g_editor.ui.theme.imgui.style_colors_light"
            ) as light,
            patch(
                "g_editor.ui.theme.imgui.style_colors_dark"
            ) as dark,
        ):
            configure_style("light")
            light.assert_called_once_with()
            dark.assert_not_called()
            configure_style("dark")
            dark.assert_called_once_with()

    def test_splash_uses_packaged_theme_specific_logos(self) -> None:
        light = splash_branding("light")
        dark = splash_branding("dark")

        self.assertEqual(light.background, (255, 255, 255))
        self.assertEqual(dark.background, (34, 45, 88))
        self.assertNotEqual(light.engine_logo, dark.engine_logo)
        self.assertNotEqual(light.company_logo, dark.company_logo)
        for path in (
            light.engine_logo,
            dark.engine_logo,
            light.company_logo,
            dark.company_logo,
        ):
            self.assertTrue(path.is_file(), path)

    def test_splash_retains_drawables_and_centers_overlay_window(self) -> None:
        with EditorSplash(
            "dark",
            minimum_duration=0,
            visible=False,
        ) as splash:
            screen = splash._window.screen
            expected = (
                screen.x
                + (screen.width - splash._window.width) // 2,
                screen.y
                + (screen.height - splash._window.height) // 2,
            )

            self.assertEqual(splash._window.get_location(), expected)
            self.assertEqual(
                splash._window.style,
                splash._window.WINDOW_STYLE_OVERLAY,
            )
            self.assertEqual(len(splash._drawables), 5)
            engine_logo = splash._drawables[1]
            company_logo = splash._drawables[2]
            attribution = splash._drawables[3]
            assert isinstance(engine_logo, Sprite)
            assert isinstance(company_logo, Sprite)
            assert isinstance(attribution, Label)
            self.assertGreater(
                engine_logo.y - engine_logo.height / 2,
                attribution.y + attribution.content_height / 2 + 20,
            )
            self.assertGreater(
                attribution.y - attribution.content_height / 2,
                company_logo.y + company_logo.height / 2 + 20,
            )

    def test_editor_theme_change_applies_and_persists_preference(self) -> None:
        editor = ImGuiEditor.__new__(ImGuiEditor)
        editor._preferences = EditorPreferences()
        editor.state = types.SimpleNamespace(error=None)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            editor._preferences_path = path
            with patch(
                "g_editor.application.configure_style"
            ) as configure:
                editor._set_theme("light")

            saved = EditorPreferences.load(path)

        self.assertEqual(editor._theme_preference, "light")
        self.assertEqual(editor._theme, "light")
        self.assertEqual(saved.theme, "light")
        configure.assert_called_once_with("light")

    def test_help_link_reports_when_no_browser_accepts_url(self) -> None:
        editor = ImGuiEditor.__new__(ImGuiEditor)
        editor.state = types.SimpleNamespace(error=None)
        with patch(
            "g_editor.application.webbrowser.open",
            return_value=False,
        ) as open_url:
            editor._open_external_url(
                "https://example.test/docs",
                "documentation",
            )

        open_url.assert_called_once_with("https://example.test/docs")
        self.assertIn("Could not open documentation", editor.state.error)


if __name__ == "__main__":
    unittest.main()
