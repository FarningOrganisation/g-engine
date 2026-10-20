import unittest

from pyglet.window import key

from g import (
    AnchorLayout,
    Button,
    CanvasLayout,
    Game,
    GridLayout,
    HBox,
    Label,
    MouseButton,
    Panel,
    ScrollView,
    Scene,
    Slider,
    ButtonStyle,
    UITheme,
    TextInput,
    Toggle,
    UIFade,
    UISlide,
    UIView,
    VBox,
)


class GUIWidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(400, 300)
        self.gui = self.game.scenes.current.gui

    def test_scene_has_default_gui_view(self) -> None:
        label = self.gui.add(Label("Score"))

        self.assertIs(label.parent, self.gui.default.root)
        self.assertIs(label.view, self.gui.default)
        self.assertIs(self.gui.current, self.gui.default)

    def test_removing_widget_unmounts_its_drawables(self) -> None:
        button = self.gui.add(Button("Remove"))
        self.gui.default._layout()

        self.gui.remove(button)

        self.assertIsNone(button.parent)
        self.assertFalse(button.visible_in_hierarchy)
        background = button._background
        if background is None:
            self.fail("button background was not created")
        self.assertFalse(background.visible)
        self.assertIsNone(self.gui._target_at(button.x, button.y))

    def test_vbox_and_anchor_layout_children(self) -> None:
        first = Button("First", width=100, height=30)
        second = Button("Second", width=120, height=40)
        column = VBox(first, second, spacing=10)
        view = self.gui.register(
            "menu",
            UIView(AnchorLayout(column)),
        )
        self.gui.replace("menu")

        view._layout()

        self.assertEqual(column.width, 120)
        self.assertEqual(column.height, 80)
        self.assertEqual(first.y - second.y, 50)
        self.assertEqual(first.x + first.width / 2, 200)
        self.assertEqual(second.x + second.width / 2, 200)

        with self.assertRaisesRegex(ValueError, "only one child"):
            view.root.add(Label("Extra"))

    def test_hbox_and_grid_layout_arrange_children(self) -> None:
        left = Label("L", width=20, height=10)
        right = Label("R", width=30, height=10)
        row = HBox(left, right, spacing=5)
        row.arrange(0, 0, 100, 20)
        self.assertEqual(right.x - (left.x + left.width), 5)

        widgets = [Label(str(index)) for index in range(4)]
        grid = GridLayout(*widgets, columns=2, row_spacing=4, column_spacing=6)
        grid.arrange(0, 0, 106, 104)
        self.assertEqual(widgets[1].x - (widgets[0].x + widgets[0].width), 6)
        self.assertEqual(widgets[0].y - (widgets[2].y + widgets[2].height), 4)

    def test_canvas_layout_free_positions_widgets(self) -> None:
        panel = Label("Panel", width=120, height=40)
        button = Button("Start", width=180, height=48)
        canvas = CanvasLayout()
        canvas.add_at(panel, 25, 35)
        canvas.add_at(button, 200, 100)

        canvas.arrange(0, 0, 400, 300)
        canvas.set_position(panel, 50, 60)
        canvas.arrange(0, 0, 400, 300)

        self.assertEqual((panel.x, panel.y), (50, 60))
        self.assertEqual((button.x, button.y), (200, 100))
        self.assertEqual(canvas.position_of(panel), (50, 60))

    def test_panel_positions_and_routes_input_to_nested_children(self) -> None:
        clicks: list[str] = []
        panel = Panel(width=240, height=140)
        button = panel.add_at(
            Button("Start", lambda: clicks.append("click")),
            20,
            30,
        )
        canvas = CanvasLayout(panel)
        canvas.set_position(panel, 40, 50)
        view = self.gui.register("nested", UIView(canvas))
        self.gui.replace("nested")
        view._layout()

        self.assertEqual((button.x, button.y), (60, 80))
        self.gui.on_mouse_press(70, 90, MouseButton.LEFT, 0)
        self.gui.on_mouse_release(70, 90, MouseButton.LEFT, 0)

        self.assertEqual(clicks, ["click"])
        self.assertIs(button.parent, panel)

    def test_button_click_is_routed_and_not_exposed_as_game_input(self) -> None:
        clicks: list[str] = []
        button = self.gui.add(Button("Start", lambda: clicks.append("click")))
        self.gui.default._layout()
        x = button.x + button.width / 2
        y = button.y + button.height / 2

        self.game._handle_mouse_press(x, y, MouseButton.LEFT, 0)
        self.game._handle_mouse_release(x, y, MouseButton.LEFT, 0)

        self.assertEqual(clicks, ["click"])
        self.assertFalse(self.game.input.pressed(MouseButton.LEFT))

    def test_toggle_and_slider_dispatch_changes(self) -> None:
        toggles: list[bool] = []
        values: list[float] = []
        toggle = self.gui.add(Toggle("Music", on_change=toggles.append))
        slider = self.gui.add(
            Slider(0, 100, 50, values.append, width=200, height=24)
        )
        self.gui.default._layout()

        toggle_x = toggle.x + toggle.width / 2
        toggle_y = toggle.y + toggle.height / 2
        self.gui.on_mouse_press(toggle_x, toggle_y, MouseButton.LEFT, 0)
        self.gui.on_mouse_release(toggle_x, toggle_y, MouseButton.LEFT, 0)

        slider_y = slider.y + slider.height / 2
        self.gui.on_mouse_press(slider.x, slider_y, MouseButton.LEFT, 0)
        self.gui.on_mouse_drag(
            slider.x + slider.width,
            slider_y,
            slider.width,
            0,
            MouseButton.LEFT,
            0,
        )
        self.gui.on_mouse_release(
            slider.x + slider.width,
            slider_y,
            MouseButton.LEFT,
            0,
        )

        self.assertEqual(toggles, [True])
        self.assertEqual(slider.value, 100)
        self.assertEqual(values[-1], 100)

    def test_text_input_uses_pyglet_text_entry_and_focus(self) -> None:
        commits: list[str] = []
        text_input = self.gui.add(TextInput(on_commit=commits.append))
        self.gui.default._layout()
        x = text_input.x + 5
        y = text_input.y + text_input.height / 2

        self.game._handle_mouse_press(x, y, MouseButton.LEFT, 0)
        self.game._handle_text("a")
        self.game._handle_text("\n")

        self.assertEqual(text_input.text, "a")
        self.assertEqual(commits, ["a"])
        self.assertFalse(text_input.focused)

    def test_view_inherits_theme_and_widget_can_override_it(self) -> None:
        themed = ButtonStyle(background=(12, 34, 56))
        self.gui.theme = UITheme(button=themed)

        inherited = self.gui.add(Button("Inherited"))
        overridden = self.gui.add(
            Button("Override", style=ButtonStyle(background=(90, 80, 70)))
        )

        self.assertIs(inherited.style, themed)
        self.assertEqual(overridden.style.background, (90, 80, 70))

    def test_widget_animation_updates_presentation_without_changing_layout(self) -> None:
        button = self.gui.add(Button("Animate"))
        self.gui.default._layout()
        layout_x = button.x

        handle = button.animate("entrance").to(
            1,
            offset_x=40,
            opacity=128,
        ).start()
        self.gui._update(0.5)

        self.assertEqual(button.x, layout_x)
        self.assertAlmostEqual(button.offset_x, 20)
        self.assertAlmostEqual(button.opacity, 192, delta=1)
        self.assertTrue(handle.running)

        button.animate("entrance").to(0, offset_x=10).start()
        self.gui._update(0)
        self.assertEqual(button.offset_x, 10)
        self.assertFalse(handle.running)

    def test_transformed_widget_uses_presented_bounds_for_hit_testing(self) -> None:
        clicks: list[str] = []
        canvas = CanvasLayout()
        button = canvas.add_at(
            Button(
                "Scaled",
                lambda: clicks.append("click"),
                width=100,
                height=40,
            ),
            100,
            100,
        )
        button.scale_x = 2
        button.offset_x = 30
        view = self.gui.register("transformed", UIView(canvas))
        self.gui.replace("transformed")
        view._layout()

        left, bottom, _, _ = button.presented_bounds
        self.gui.on_mouse_press(left + 2, bottom + 2, MouseButton.LEFT, 0)
        self.gui.on_mouse_release(left + 2, bottom + 2, MouseButton.LEFT, 0)

        self.assertEqual(clicks, ["click"])

    def test_hover_interaction_animation_uses_separate_channel(self) -> None:
        button = self.gui.add(Button("Hover"))
        self.gui.default._layout()
        x = button.x + button.width / 2
        y = button.y + button.height / 2

        self.gui.on_mouse_motion(x, y, 0, 0)
        self.gui._update(0.12)

        self.assertAlmostEqual(button.scale_x, 1.04)

    def test_scroll_view_clips_input_and_scrolls_oversized_content(self) -> None:
        child = Label("Content", width=100, height=300)
        scroll = ScrollView(child, width=100, height=100)
        self.gui.add(scroll)
        self.gui.default._layout()
        child_y = child.y

        x = scroll.x + scroll.width / 2
        y = scroll.y + scroll.height / 2
        handled = self.gui.on_mouse_scroll(x, y, 0, -1)

        self.assertTrue(handled)
        self.assertEqual(scroll.scroll_y, 32)
        self.gui.default._layout()
        self.assertEqual(child.y, child_y + 32)
        self.assertIsNone(scroll.hit_test(x, scroll.y - 1))

    def test_tab_focus_and_keyboard_activation(self) -> None:
        clicks: list[str] = []
        first = self.gui.add(Button("First", lambda: clicks.append("first")))
        second = self.gui.add(Button("Second", lambda: clicks.append("second")))
        self.gui.default._layout()

        self.gui.on_key_press(key.TAB, 0)
        self.assertTrue(first.focused)
        self.gui.on_key_press(key.TAB, 0)
        self.assertTrue(second.focused)
        self.gui.on_key_press(key.ENTER, 0)
        self.gui.on_key_release(key.ENTER, 0)

        self.assertEqual(clicks, ["second"])

    def test_ui_scale_affects_layout_drawing_and_hit_testing(self) -> None:
        self.gui.scale = 2
        clicks: list[str] = []
        button = self.gui.add(Button("Scaled", lambda: clicks.append("click")))
        self.gui.default._layout()
        left, bottom, right, top = button.presented_bounds

        self.gui.on_mouse_press(
            (left + right) / 2,
            (bottom + top) / 2,
            MouseButton.LEFT,
            0,
        )
        self.gui.on_mouse_release(
            (left + right) / 2,
            (bottom + top) / 2,
            MouseButton.LEFT,
            0,
        )

        self.assertEqual(self.gui.default.root.width, 200)
        self.assertEqual(clicks, ["click"])


class GUIViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.game = Game(400, 300)
        self.gui = self.game.scenes.current.gui
        self.menu = self.gui.register("menu", UIView())
        self.dialog = self.gui.register(
            "dialog",
            UIView(transparent=True, modal=True, pauses_scene=True),
        )

    def test_push_and_pop_manage_visible_views_and_scene_pause(self) -> None:
        self.gui.replace("menu")
        self.gui.push("dialog")

        self.assertEqual(self.gui.visible_views, (self.menu, self.dialog))
        self.assertTrue(self.gui.pauses_scene)

        popped = self.gui.pop()

        self.assertIs(popped, self.dialog)
        self.assertIs(self.gui.current, self.menu)
        self.assertFalse(self.gui.pauses_scene)

    def test_fade_switches_view_at_midpoint(self) -> None:
        transition = UIFade(duration=1)

        self.gui.replace("menu", transition=transition)
        self.gui._update(0.25)
        self.assertIs(self.gui.current, self.gui.default)
        self.assertEqual(transition.overlay_color, (0, 0, 0, 128))

        self.gui._update(0.25)
        self.assertIs(self.gui.current, self.menu)
        self.assertEqual(transition.overlay_color, (0, 0, 0, 255))

        self.gui._update(0.5)
        self.assertIsNone(self.gui.transition)

    def test_slide_offsets_outgoing_and_incoming_views(self) -> None:
        transition = UISlide("left", duration=1)

        self.gui.replace("menu", transition=transition)
        self.gui._update(0.25)
        self.assertEqual(self.gui.current.offset_x, -200)

        self.gui._update(0.25)
        self.assertIs(self.gui.current, self.menu)
        self.assertEqual(self.menu.offset_x, 400)

        self.gui._update(0.5)
        self.assertEqual(self.menu.offset_x, 0)

    def test_pausing_gui_stops_scene_update_but_transition_advances(self) -> None:
        updates: list[float] = []

        class UpdatingScene(Scene):
            def update(self, delta_time: float) -> None:
                updates.append(delta_time)

        scene = self.game.scenes.add("updating", UpdatingScene())
        self.game.scenes.switch("updating")
        scene.gui.register(
            "pause",
            UIView(transparent=True, modal=True, pauses_scene=True),
        )
        scene.gui.push("pause", transition=UIFade(0.5))

        self.game._update(0.25)

        self.assertEqual(updates, [])
        self.assertIs(scene.gui.current, scene.gui.get("pause"))


if __name__ == "__main__":
    unittest.main()
