from typing import ClassVar
import unittest

from g_editor.state import EditorState
from g import Game, SceneSerializer, Script, Vec2, inspector_field
from g.inspector import InspectorField, inspector_fields
from g.persistence import scene_types


class InspectorFieldTests(unittest.TestCase):
    def test_metadata_is_optional_and_public_annotations_remain_exposed(self) -> None:
        class Behavior(Script):
            speed: float = inspector_field(120.0, label="Run Speed", minimum=0, maximum=500, tooltip="Run speed.")
            direction: Vec2 = inspector_field(Vec2(1, 2))
            settings: dict[str, int] = inspector_field({"lives": 3})
            title: str = "Player"
            active: bool = True
            _private: int = 2
            constant: ClassVar[int] = 5

            def __init__(self) -> None:
                super().__init__()
                self.runtime_reference = object()

        Behavior.__module__ = "inspector_fields_test"
        name = "inspector_fields_test.Behavior"
        scene_types.register_component(name, Behavior)
        try:
            codec = scene_types.component_codec_for_name(name)
            self.assertEqual(set(codec.fields), {"speed", "direction", "settings", "title", "active"})
            state = EditorState()
            state.add_object("empty")
            state.add_component(name)
            state.set_component_value(0, "speed", 250.0)
            revision = state.revision
            for invalid in (-1.0, 501.0, float("nan")):
                with self.assertRaises(ValueError):
                    state.set_component_value(0, "speed", invalid)
            self.assertEqual(state.revision, revision)
            game = Game(320, 180)
            scene = SceneSerializer().restore(game, state.document, asset_root=".")
            behavior = scene.objects[0].require_component(Behavior)
            self.assertEqual(behavior.speed, 250)
            self.assertEqual(behavior.direction, Vec2(1, 2))
            self.assertEqual(behavior.settings, {"lives": 3})
            state.reset_component(0)
            template = scene_types.component_template(name)
            properties = template["properties"]
            assert isinstance(properties, dict)
            self.assertEqual(properties["speed"], 120)
            first, second = Behavior(), Behavior()
            first.settings["lives"] = 0
            first.direction.x = 99
            self.assertEqual(second.settings, {"lives": 3})
            self.assertEqual(second.direction, Vec2(1, 2))
            self.assertEqual(Behavior.direction, Vec2(1, 2))
        finally:
            scene_types.unregister_module(Behavior.__module__)

    def test_inherited_fields_can_be_overridden(self) -> None:
        class Base(Script):
            speed: float = inspector_field(10.0, tooltip="Base speed.")

        class Derived(Base):
            speed: float = inspector_field(20.0, label="Derived Speed")

        class PlainOverride(Base):
            speed: float = 30

        self.assertEqual(inspector_fields(Derived)["speed"].default, 20)
        self.assertNotIn("speed", inspector_fields(PlainOverride))

    def test_vector_metadata_limits_apply_to_both_axes(self) -> None:
        from g.inspector import validate_field_value

        field = InspectorField(Vec2(), minimum=-10, maximum=10)
        validate_field_value(field, {"$vec2": [3, -4]})
        for coordinates in ([11, 0], [0, -11], [0, float("inf")], [True, 0]):
            with self.assertRaises(ValueError):
                validate_field_value(field, {"$vec2": coordinates})

    def test_mask_metadata_bounds_names_and_groups(self) -> None:
        from g.inspector import validate_field_value

        class Behavior(Script):
            layers: int = inspector_field(
                0xFFFFFFFF, widget="mask", bit_names=("Player", "Terrain"),
                group="Filtering", group_expanded=False,
            )

        field = inspector_fields(Behavior)["layers"]
        self.assertEqual(field.bit_names, ("Player", "Terrain"))
        self.assertEqual(field.group, "Filtering")
        self.assertFalse(field.group_expanded)
        for value in (0, 1, 1 << 31, 0xFFFFFFFF):
            validate_field_value(field, value)
        for value in (-1, 1 << 32, True, 1.5):
            with self.assertRaises(ValueError):
                validate_field_value(field, value)
        with self.assertRaises(ValueError):
            InspectorField(0, bit_names=("",))
        with self.assertRaises(ValueError):
            InspectorField(0, bit_names=("x",) * 33)

    def test_property_metadata_uses_getter_docstring_without_replacing_property(self) -> None:
        class Behavior(Script):
            __inspector_fields__ = {"speed": inspector_field(12.0)}

            @property
            def speed(self) -> float:
                """Movement speed in pixels per second."""
                return 12

        self.assertIsInstance(Behavior.speed, property)
        self.assertEqual(inspector_fields(Behavior)["speed"].tooltip, "Movement speed in pixels per second.")

    def test_metadata_rejects_invalid_configuration(self) -> None:
        for create in (
            lambda: InspectorField(0, minimum=float("nan")),
            lambda: InspectorField(0, minimum=5, maximum=2),
            lambda: InspectorField(0, label=""),
            lambda: InspectorField(0, choices=("a", "a")),
            lambda: InspectorField(0, optional_color=(256, 0, 0)),
        ):
            with self.assertRaises(ValueError):
                create()

    def test_all_builtin_editable_fields_come_from_component_source(self) -> None:
        for name in scene_types.component_types:
            codec = scene_types.component_codec_for_name(name)
            if codec.builtin:
                with self.subTest(component=name):
                    metadata = inspector_fields(codec.value_type)
                    template = scene_types.component_template(name)
                    properties = template["properties"]
                    assert isinstance(properties, dict)
                    self.assertEqual(set(properties), {*metadata, "enabled"})
                    self.assertTrue(all(field.tooltip for field in metadata.values()))
