import unittest

from g import Input, Key, Modifiers, MouseButton


class InputTests(unittest.TestCase):
    def test_keyboard_lifecycle_and_legacy_queries(self) -> None:
        state = Input()
        self.assertTrue(state.key_released(Key.SPACE))
        self.assertFalse(state.key_pressed(Key.SPACE))
        self.assertFalse(state.key_just_pressed(Key.SPACE))
        self.assertFalse(state.key_just_released(Key.SPACE))

        state._press_key(Key.SPACE, Modifiers.SHIFT)
        self.assertTrue(state.key_pressed(Key.SPACE))
        self.assertFalse(state.key_just_pressed(Key.SPACE))
        state._begin_frame()
        self.assertTrue(state.key_just_pressed(Key.SPACE))
        self.assertTrue(state.just_pressed(Key.SPACE))
        self.assertTrue(state.pressed(Key.SPACE))
        self.assertFalse(state.key_released(Key.SPACE))
        self.assertEqual(state.modifiers_for(Key.SPACE), Modifiers.SHIFT)

        state._press_key(Key.SPACE, Modifiers.NONE)
        state._begin_frame()
        self.assertTrue(state.key_pressed(Key.SPACE))
        self.assertFalse(state.key_just_pressed(Key.SPACE))

        state._release_key(Key.SPACE)
        state._begin_frame()
        self.assertTrue(state.key_released(Key.SPACE))
        self.assertTrue(state.key_just_released(Key.SPACE))
        self.assertTrue(state.just_released(Key.SPACE))
        self.assertFalse(state.pressed(Key.SPACE))
        state._release_key(Key.SPACE)
        state._begin_frame()
        self.assertFalse(state.key_just_released(Key.SPACE))

    def test_mouse_lifecycle_defaults_to_left(self) -> None:
        state = Input()
        self.assertTrue(state.mouse_released())
        self.assertFalse(state.mouse_pressed())
        self.assertFalse(state.mouse_just_pressed())
        self.assertFalse(state.mouse_just_released())
        state._press_mouse(MouseButton.LEFT)
        self.assertTrue(state.mouse_pressed())
        self.assertFalse(state.mouse_just_pressed())
        state._begin_frame()
        self.assertTrue(state.mouse_just_pressed())
        self.assertTrue(state.just_pressed(MouseButton.LEFT))
        self.assertTrue(state.pressed(MouseButton.LEFT))
        self.assertFalse(state.mouse_released())
        state._press_mouse(MouseButton.LEFT)
        state._begin_frame()
        self.assertFalse(state.mouse_just_pressed())
        self.assertTrue(state.mouse_pressed())
        state._release_mouse(MouseButton.LEFT)
        state._begin_frame()
        self.assertTrue(state.mouse_just_released())
        self.assertTrue(state.just_released(MouseButton.LEFT))
        self.assertTrue(state.mouse_released())
        self.assertFalse(state.pressed(MouseButton.LEFT))
        state._release_mouse(MouseButton.LEFT)
        state._begin_frame()
        self.assertFalse(state.mouse_just_released())

    def test_explicit_mouse_buttons_are_independent(self) -> None:
        for button in (MouseButton.RIGHT, MouseButton.MIDDLE):
            with self.subTest(button=button):
                state = Input()
                state._press_mouse(button)
                state._begin_frame()
                self.assertTrue(state.mouse_pressed(button))
                self.assertTrue(state.mouse_just_pressed(button))
                self.assertFalse(state.mouse_released(button))
                self.assertFalse(state.mouse_pressed())
                self.assertFalse(state.mouse_just_pressed())
                state._release_mouse(button)
                state._begin_frame()
                self.assertTrue(state.mouse_released(button))
                self.assertTrue(state.mouse_just_released(button))
                self.assertFalse(state.mouse_just_released())

    def test_equal_numeric_codes_do_not_mix_devices(self) -> None:
        button = MouseButton.LEFT
        key = Key(int(button))
        self.assertEqual(key, button)
        for keyboard in (True, False):
            with self.subTest(keyboard=keyboard):
                state = Input()
                if keyboard:
                    state._press_key(key, Modifiers.NONE)
                else:
                    state._press_mouse(button)
                state._begin_frame()
                self.assertEqual(state.key_pressed(key), keyboard)
                self.assertEqual(state.key_just_pressed(key), keyboard)
                self.assertEqual(state.mouse_pressed(button), not keyboard)
                self.assertEqual(state.mouse_just_pressed(button), not keyboard)
                self.assertEqual(state.just_pressed(key), keyboard)
                self.assertEqual(state.just_pressed(button), not keyboard)
                if keyboard:
                    state._release_key(key)
                else:
                    state._release_mouse(button)
                state._begin_frame()
                self.assertEqual(state.key_just_released(key), keyboard)
                self.assertEqual(state.mouse_just_released(button), not keyboard)
                self.assertEqual(state.just_released(key), keyboard)
                self.assertEqual(state.just_released(button), not keyboard)

    def test_short_taps_keep_both_edges_and_reset_clears_state(self) -> None:
        state = Input()
        state._press_key(Key.A, Modifiers.NONE)
        state._release_key(Key.A)
        state._press_mouse(MouseButton.LEFT)
        state._release_mouse(MouseButton.LEFT)
        state._begin_frame()
        self.assertTrue(state.key_just_pressed(Key.A))
        self.assertTrue(state.key_just_released(Key.A))
        self.assertTrue(state.key_released(Key.A))
        self.assertTrue(state.mouse_just_pressed())
        self.assertTrue(state.mouse_just_released())
        self.assertTrue(state.mouse_released())
        state._press_key(Key.A, Modifiers.NONE)
        state._press_mouse(MouseButton.LEFT)
        state._reset()
        state._begin_frame()
        self.assertFalse(state.key_pressed(Key.A))
        self.assertFalse(state.key_just_pressed(Key.A))
        self.assertFalse(state.key_just_released(Key.A))
        self.assertFalse(state.mouse_pressed())
        self.assertFalse(state.mouse_just_pressed())
        self.assertFalse(state.mouse_just_released())
