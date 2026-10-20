import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from random import Random

import pyglet

from g_editor.state import (
    DEFAULT_SCENE_SIZE,
    SCENE_RESOLUTION_PRESETS,
    EditorState,
    new_object_record,
    normalize_record_order,
    position,
    record_z_index,
    transform_tile_flags,
    transform_tile_matrix,
)
from g_editor.prefab import PrefabDocument
from g_editor.panels.inspector import (
    InspectorPanel,
    _component_display_properties,
    _component_field_label,
    _component_group,
    _segment_projection,
)
from g_editor.panels.tile_palette import TilePalettePanel
from g import (
    Component,
    SceneDocument,
    Script,
    TileCell,
    TileFlags,
    TileLayer,
    requires_components,
    scene_component,
)
from g.persistence import SceneFormatError


@scene_component("tests.editor_required_component")
class EditorRequiredComponent(Component):
    strength: float = 1


@scene_component("tests.editor_dependent_component")
@requires_components(EditorRequiredComponent, auto_add=True)
class EditorDependentComponent(Component):
    multiplier: float = 2


@scene_component("tests.editor_manual_dependency")
@requires_components(EditorRequiredComponent)
class EditorManualDependency(Component):
    strength: float = 1


@scene_component("tests.editor_game_script")
class EditorGameScript(Script):
    speed: float = 1


class EditorStateTests(unittest.TestCase):
    def test_collision_segment_projection_clamps_to_segment(self) -> None:
        projection, distance = _segment_projection(
            (0.5, 0.25),
            (0.0, 0.0),
            (1.0, 0.0),
        )
        self.assertEqual(projection, (0.5, 0.0))
        self.assertEqual(distance, 0.25)

        projection, distance = _segment_projection(
            (2.0, 0.0),
            (0.0, 0.0),
            (1.0, 0.0),
        )
        self.assertEqual(projection, (1.0, 0.0))
        self.assertEqual(distance, 1.0)

    def test_collision_editor_normalizes_valid_polygon_points(self) -> None:
        polygons = InspectorPanel._collision_polygons(
            {
                "type": "polygon",
                "polygons": [
                    [[0, 0], [1, 0], [0.5, 1]],
                    [[0, 0], ["invalid", 0], [1, 1]],
                ],
            },
        )

        self.assertEqual(
            polygons,
            [[[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]]],
        )

    def test_all_registered_builtins_have_editor_creation_defaults(self) -> None:
        from g.persistence import scene_types

        supported = set(scene_types.editable_component_types)
        for name in scene_types.component_types:
            if scene_types.component_codec_for_name(name).builtin:
                with self.subTest(component=name):
                    self.assertIn(name, supported)

    def test_sprite_animator_add_reset_and_paste_add_sprite_renderer(self) -> None:
        from g_editor.panels.inspector import _component_group, _component_label

        self.assertEqual(_component_group("g.sprite_animator"), "Animation")
        self.assertEqual(_component_label("g.sprite_animator"), "Sprite Animator")
        record = self.state.add_object("empty")
        self.assertNotIn("g.image_states", self.state.available_component_types)
        self.state.add_component("g.sprite_animator")
        components = record["components"]
        assert isinstance(components, list)
        types = [
            component["type"]
            for component in components
            if isinstance(component, dict)
        ]
        self.assertEqual(types, ["g.sprite_renderer", "g.sprite_animator"])
        animator = components[1]
        assert isinstance(animator, dict)
        properties = animator["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["initial_state"], "default")
        self.assertEqual(properties["states"], [{"name": "default", "loop": True, "image": None, "animated": False}])

        self.state.set_component_value(1, "initial_state", "idle")
        self.state.reset_component(1)
        reset = components[1]
        assert isinstance(reset, dict)
        reset_properties = reset["properties"]
        assert isinstance(reset_properties, dict)
        self.assertEqual(reset_properties["initial_state"], "default")
        self.assertEqual(reset_properties["states"], [{"name": "default", "loop": True, "image": None, "animated": False}])

        target = self.state.add_object("empty")
        self.state.paste_component(reset)
        target_components = target["components"]
        assert isinstance(target_components, list)
        self.assertEqual(
            [
                component["type"]
                for component in target_components
                if isinstance(component, dict)
            ],
            ["g.sprite_renderer", "g.sprite_animator"],
        )

    def test_area_presets_create_objects_with_area_components(self) -> None:
        from g import Area

        box = self.state.add_object("area_box")
        circle = self.state.add_object("area_circle")

        for record, shape in (
            (box, "box"),
            (circle, "circle"),
        ):
            with self.subTest(preset=record["name"]):
                self.assertEqual(record["type"], "g.game_object")
                component_records = record["components"]
                assert isinstance(component_records, list)
                self.assertIn(
                    "g.area",
                    [
                        component["type"]
                        for component in component_records
                        if isinstance(component, dict)
                    ],
                )
                collider = next(
                    component for component in component_records
                    if isinstance(component, dict)
                    and component["type"] == "g.collider"
                )
                self.assertEqual(collider["properties"]["shape"], shape)

        self.state.add_object("empty")
        self.assertIn("g.area", self.state.available_component_types)

    def test_area_index_validation_and_collider_reset_paste_keep_sensor_mode(
        self,
    ) -> None:
        record = self.state.add_object("area_box")
        components = record["components"]
        assert isinstance(components, list)
        area_index = next(
            index
            for index, component in enumerate(components)
            if isinstance(component, dict) and component["type"] == "g.area"
        )
        collider_index = next(
            index
            for index, component in enumerate(components)
            if isinstance(component, dict)
            and component["type"] == "g.collider"
        )
        area_record = components[area_index]
        assert isinstance(area_record, dict)

        with self.assertRaises(ValueError):
            self.state.set_component_value(area_index, "collider_index", 1)

        self.state.reset_component(collider_index)
        collider = components[collider_index]
        assert isinstance(collider, dict)
        properties = collider["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["mode"], "sensor")

        target = self.state.add_object("rectangle")
        self.state.paste_component(area_record)

        target_components = target["components"]
        assert isinstance(target_components, list)
        pasted_area_index = next(
            index
            for index, component in enumerate(target_components)
            if isinstance(component, dict)
            and component["type"] == "g.area"
        )
        pasted_collider = next(
            component
            for component in target_components
            if isinstance(component, dict)
            and component["type"] == "g.collider"
        )
        self.assertEqual(pasted_collider["properties"]["mode"], "sensor")
        with self.assertRaises(ValueError):
            self.state.set_component_value(
                pasted_area_index, "collider_index", 1
            )

    def test_empty_object_can_add_sprite_renderer_then_assign_image(self) -> None:
        from g import Game, Image, SceneSerializer, SpriteRenderer

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "hero.png"
            pyglet.image.SolidColorImagePattern(
                (20, 40, 60, 255)
            ).create_image(8, 8).save(str(image_path))
            state = EditorState(asset_root=root)
            record = state.add_object("empty")
            state.add_component("g.sprite_renderer")
            state.set_component_value(0, "image", image_path.name)

            game = Game(320, 180)
            scene = SceneSerializer().restore(
                game, state.document, asset_root=root
            )
            renderer = scene.objects[0].require_component(SpriteRenderer)
            image_value = record["components"][0]["properties"]["image"]

        self.assertIsInstance(renderer.image, Image)
        self.assertEqual(image_value, image_path.name)
        self.assertEqual(record["type"], "g.game_object")

    def test_scene_document_preserves_prefab_base_without_conversion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.state.asset_root = root
            source = self.state.add_object("empty")
            self.state.add_object("empty")
            prefab_path = root / "Nested.prefab.json"
            PrefabDocument.create(source).write(prefab_path)
            self.state.delete_selected()
            instance = self.state.instantiate_prefab(prefab_path)
            metadata = instance["prefab"]
            assert isinstance(metadata, dict)
            base = metadata["base"]
            assert isinstance(base, dict)
            base["type"] = "g.sprite"
            base_children = base["children"]
            assert isinstance(base_children, list)
            base_children[0]["type"] = "g.rect"

            raw = self.state.document.to_dict()
            original = deepcopy(raw)
            document = SceneDocument.from_dict(raw)

        self.assertEqual(raw, original)
        loaded_layer = next(
            layer
            for layer in document.to_dict()["layers"]
            if "objects" in layer
        )
        loaded_instance = next(
            record
            for record in loaded_layer["objects"]
            if "prefab" in record
        )
        loaded_metadata = loaded_instance["prefab"]
        assert isinstance(loaded_metadata, dict)
        loaded_base = loaded_metadata["base"]
        assert isinstance(loaded_base, dict)
        self.assertEqual(loaded_base["type"], "g.sprite")
        self.assertEqual(
            loaded_base["children"][0]["type"], "g.rect"
        )

    def test_sprite_renderer_can_be_added_empty_reset_and_restored(self) -> None:
        from g import Game, SceneSerializer, SpriteRenderer

        record = self.state.add_object("empty")
        self.assertIn("g.circle_renderer", self.state.available_component_types)
        self.assertIn("g.sprite_renderer", self.state.available_component_types)
        self.state.add_component("g.sprite_renderer")
        self.assertNotIn("g.sprite_renderer", self.state.available_component_types)
        self.assertIn("g.sprite_animator", self.state.available_component_types)
        self.state.set_component_value(0, "opacity", 100)
        self.state.reset_component(0)
        components = record["components"]
        assert isinstance(components, list)
        component = components[0]
        assert isinstance(component, dict)
        properties = component["properties"]
        assert isinstance(properties, dict)
        self.assertIsNone(properties["image"])
        self.assertEqual(properties["opacity"], 255)
        game = Game(320, 180)
        scene = SceneSerializer().restore(game, self.state.document, asset_root=".")
        renderer = scene.objects[0].require_component(SpriteRenderer)
        self.assertIsNone(renderer.image)
        self.assertEqual(renderer.opacity, 255)
        self.assertTrue(self.state.undo())
        self.assertTrue(self.state.undo())
        self.assertTrue(self.state.undo())
        self.assertIn("g.sprite_renderer", self.state.available_component_types)
        self.assertTrue(self.state.redo())
        self.assertNotIn("g.sprite_renderer", self.state.available_component_types)

    def test_existing_platformer_exposes_ground_snap_default(self) -> None:
        properties = {"speed": 175}
        displayed = _component_display_properties("g.platformer_motor", properties)
        self.assertEqual(displayed["ground_snap_distance"], 8)
        self.assertFalse(displayed["allow_double_jump"])
        self.assertNotIn("ground_snap_distance", properties)
        self.assertNotIn("allow_double_jump", properties)

    def setUp(self) -> None:
        self.state = EditorState()

    def test_game_scripts_use_scripts_component_category(self) -> None:
        self.assertEqual(
            _component_group("tests.editor_game_script"),
            "Scripts",
        )
        self.assertEqual(
            _component_group("tests.editor_required_component"),
            "Project Components",
        )
        self.assertEqual(
            _component_group("g.sprite_animator"),
            "Animation",
        )

    def test_component_field_names_use_friendly_labels(self) -> None:
        self.assertEqual(
            _component_field_label("world_x"),
            "World X",
        )
        self.assertEqual(
            _component_field_label("max_fall_speed"),
            "Max Fall Speed",
        )
        displayed = _component_display_properties("g.collider", {})
        self.assertEqual(displayed["offset_x"], 0.0)
        self.assertEqual(displayed["offset_y"], 0.0)
        self.assertEqual(_component_field_label("offset_x"), "Offset X")
        self.assertEqual(_component_field_label("offset_y"), "Offset Y")

    def test_object_component_warnings_report_missing_dependencies(self) -> None:
        record = self.state.add_object("empty")
        components = record["components"]
        assert isinstance(components, list)
        components.append({
            "type": "g.platformer_motor",
            "properties": {"enabled": True},
        })
        self.assertEqual(
            self.state.object_component_warnings(record),
            ("PlatformerMotor requires Collider",),
        )
        self.state.add_component("g.collider")
        self.assertEqual(self.state.object_component_warnings(record), ())

    def test_object_component_warnings_report_unknown_components(self) -> None:
        record = self.state.add_object("empty")
        components = record["components"]
        assert isinstance(components, list)
        components.append({"type": "unknown.behavior", "properties": {}})
        warnings = self.state.object_component_warnings(record)
        self.assertEqual(len(warnings), 1)
        self.assertIn("unknown.behavior", warnings[0])

    def test_sprite_animator_component_uses_sprite_image_and_edits_states(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            dir=self.state.asset_root
        ) as directory:
            root = Path(directory)
            idle = root / "idle.png"
            walk = root / "walk.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(16, 16).save(str(idle))
            pyglet.image.SolidColorImagePattern(
                (255, 0, 0, 255)
            ).create_image(16, 16).save(str(walk))
            record = self.state.add_sprite(idle)

            self.assertIn(
                "g.sprite_animator",
                self.state.available_component_types,
            )
            self.state.add_component("g.sprite_animator")

            components = record["components"]
            assert isinstance(components, list)
            animator = components[-1]
            assert isinstance(animator, dict)
            self.assertEqual(animator["type"], "g.sprite_animator")
            properties = animator["properties"]
            assert isinstance(properties, dict)
            states = properties["states"]
            assert isinstance(states, list)
            first_state = states[0]
            assert isinstance(first_state, dict)
            self.assertEqual(
                first_state["image"],
                idle.relative_to(self.state.asset_root).as_posix(),
            )

            updated_states = deepcopy(states)
            updated_states.append(
                {
                    "name": "walk",
                    "loop": True,
                    "frames": [
                        {
                            "image": first_state["image"],
                            "duration": 0.1,
                        }
                    ],
                }
            )
            self.state.set_sprite_animator_configuration(
                len(components) - 1,
                updated_states,
                initial_state="walk",
            )
            self.state.set_sprite_animation_frame_image(
                len(components) - 1,
                1,
                0,
                walk,
            )

            self.assertEqual(properties["initial_state"], "walk")
            stored_states = properties["states"]
            assert isinstance(stored_states, list)
            walk_state = stored_states[1]
            assert isinstance(walk_state, dict)
            walk_frames = walk_state["frames"]
            assert isinstance(walk_frames, list)
            walk_frame = walk_frames[0]
            assert isinstance(walk_frame, dict)
            self.assertEqual(
                walk_frame["image"],
                walk.relative_to(self.state.asset_root).as_posix(),
            )

            renamed = walk.with_name("walking.png")
            walk.rename(renamed)
            self.state.rename_asset_references(
                walk.relative_to(self.state.asset_root).as_posix(),
                renamed.relative_to(self.state.asset_root).as_posix(),
            )
            self.assertEqual(
                walk_frame["image"],
                renamed.relative_to(self.state.asset_root).as_posix(),
            )

    def test_collider_inspector_shape_choices_and_shape_change_are_undoable(self) -> None:
        from g import Collider
        from g.inspector import inspector_fields

        self.assertEqual(inspector_fields(Collider)["shape"].choices, Collider.SHAPES)
        self.assertEqual(
            inspector_fields(Collider)["mode"].choices,
            ("solid", "sensor", "query_only"),
        )
        self.state.add_object("empty")
        self.state.add_component("g.collider")
        index = 0
        for name, value in (
            ("width", 84),
            ("height", 27),
            ("radius", 11),
            ("radius_x", 23),
            ("radius_y", 15),
        ):
            self.state.set_component_value(index, name, value)
        self.state.set_component_value(index, "shape", "circle")
        properties = self.state._selected_component(index)[1]["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["shape"], "circle")
        self.assertEqual(
            tuple(properties[name] for name in ("width", "height", "radius", "radius_x", "radius_y")),
            (84, 27, 11, 23, 15),
        )
        self.assertTrue(self.state.undo())
        properties = self.state._selected_component(index)[1]["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["shape"], "box")
        self.assertEqual((properties["width"], properties["radius"]), (84, 11))
        self.assertTrue(self.state.redo())
        properties = self.state._selected_component(index)[1]["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["shape"], "circle")

    def test_invalid_collider_shape_can_be_repaired_and_undone(self) -> None:
        self.state.add_object("empty")
        self.state.add_component("g.collider")
        properties = self.state._selected_component(0)[1]["properties"]
        assert isinstance(properties, dict)
        properties["shape"] = "invalid"
        self.state.mark_changed("Injected invalid collider shape")
        history = self.state._history_cursor

        self.state.set_collider_shape(0, "circle")

        self.assertEqual(self.state._history_cursor, history + 1)
        properties = self.state._selected_component(0)[1]["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["shape"], "circle")
        self.assertTrue(self.state.undo())
        properties = self.state._selected_component(0)[1]["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["shape"], "invalid")

    def test_selection_and_component_change_use_canonical_record(self) -> None:
        record = self.state.add_object("rectangle")
        self.assertIs(self.state.selected, record)

        self.state.set_component_value(0, "width", 96)

        components = record["components"]
        self.assertIsInstance(components, list)
        assert isinstance(components, list)
        renderer = components[0]
        collider = components[1]
        self.assertIsInstance(renderer, dict)
        self.assertIsInstance(collider, dict)
        assert isinstance(renderer, dict)
        assert isinstance(collider, dict)
        self.assertEqual(renderer["properties"]["width"], 96)  # type: ignore[index]
        self.assertEqual(collider["properties"]["width"], 96)  # type: ignore[index]

    def test_builtin_components_can_be_added_removed_and_undone(self) -> None:
        record = self.state.add_object("empty")

        self.assertIn(
            "g.physics_body",
            self.state.available_component_types,
        )
        self.state.add_component("g.physics_body")
        components = record["components"]
        assert isinstance(components, list)
        self.assertEqual(components[0]["type"], "g.physics_body")  # type: ignore[index]
        self.assertNotIn(
            "g.physics_body",
            self.state.available_component_types,
        )

        self.state.remove_component(0)
        self.assertEqual(components, [])
        self.assertTrue(self.state.undo())
        restored = self.state.selected
        assert restored is not None
        restored_components = restored["components"]
        assert isinstance(restored_components, list)
        self.assertEqual(
            restored_components[0]["type"],  # type: ignore[index]
            "g.physics_body",
        )

    def test_component_reset_and_dependency_removal_status(self) -> None:
        record = self.state.add_object("rectangle")
        self.state.set_component_value(0, "width", 120)
        self.state.set_component_value(0, "enabled", False)

        self.state.reset_component(0)

        components = record["components"]
        assert isinstance(components, list)
        renderer = components[0]
        assert isinstance(renderer, dict)
        properties = renderer["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["width"], 64)
        self.assertTrue(properties["enabled"])

        self.state.add_component("g.platformer_motor")
        physics_index = next(
            index
            for index, component in enumerate(components)
            if isinstance(component, dict)
            and component.get("type") == "g.physics_body"
        )
        self.assertIn(
            "PlatformerMotor",
            self.state.component_removal_error(physics_index) or "",
        )

    def test_component_values_can_be_pasted_for_matching_type(self) -> None:
        record = self.state.add_object("rectangle")

        self.state.replace_component_properties(
            0,
            "g.rect_renderer",
            {
                "width": 90,
                "height": 40,
                "fill": [10, 20, 30, 255],
                "outline": None,
                "outline_thickness": 2,
                "visible": True,
                "screen_space": False,
                "enabled": False,
            },
        )

        components = record["components"]
        assert isinstance(components, list)
        renderer = components[0]
        assert isinstance(renderer, dict)
        properties = renderer["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["width"], 90)
        self.assertFalse(properties["enabled"])
        collider = components[1]
        assert isinstance(collider, dict)
        collider_properties = collider["properties"]
        assert isinstance(collider_properties, dict)
        self.assertEqual(collider_properties["width"], 90)

    def test_existing_component_type_cannot_be_added_twice(self) -> None:
        self.state.add_object("rectangle")

        self.assertNotIn(
            "g.rect_renderer",
            self.state.available_component_types,
        )
        with self.assertRaises(ValueError):
            self.state.add_component("g.rect_renderer")

    def test_pasting_component_adds_independent_copy_and_undo_redo(self) -> None:
        source = self.state.add_object("sprite")
        components = source["components"]
        assert isinstance(components, list)
        clipboard = components[0]
        assert isinstance(clipboard, dict)
        properties = clipboard["properties"]
        assert isinstance(properties, dict)
        properties["image"] = "player.png"
        properties["enabled"] = False
        target = self.state.add_object("empty")
        self.state.paste_component(clipboard)
        pasted = target["components"]
        self.assertEqual(pasted, [clipboard])
        properties["image"] = "changed.png"
        self.assertNotEqual(pasted, [clipboard])
        self.assertTrue(self.state.undo())
        selected = self.state.selected
        assert selected is not None
        self.assertEqual(selected["components"], [])
        self.assertTrue(self.state.redo())
        selected = self.state.selected
        assert selected is not None
        self.assertEqual(selected["components"], pasted)

    def test_paste_controller_adds_dependencies_in_one_undo_entry(self) -> None:
        self.state.add_object("empty")
        self.state.paste_component({
            "type": "g.platformer_motor",
            "properties": {"speed": 175, "enabled": False},
        })
        selected = self.state.selected
        assert selected is not None
        components = selected["components"]
        assert isinstance(components, list)
        self.assertEqual(
            [component["type"] for component in components if isinstance(component, dict)],
            ["g.physics_body", "g.collider", "g.platformer_motor"],
        )
        self.assertTrue(self.state.undo())
        selected = self.state.selected
        assert selected is not None
        components = selected["components"]
        assert isinstance(components, list)
        self.assertEqual(len(components), 0)

    def test_invalid_component_paste_is_atomic(self) -> None:
        record = self.state.add_object("empty")
        for clipboard in (
            {"type": "g.sprite_renderer", "properties": []},
            {"type": "unregistered.component", "properties": {}},
        ):
            with self.subTest(clipboard=clipboard):
                revision = self.state.revision
                with self.assertRaises((ValueError, SceneFormatError)):
                    self.state.paste_component(clipboard)
                self.assertEqual(record["components"], [])
                self.assertEqual(self.state.revision, revision)
        self.state.paste_component({"type": "g.sprite_renderer", "properties": {}})
        with self.assertRaises(ValueError):
            self.state.paste_component({"type": "g.sprite_renderer", "properties": {}})

    def test_motors_are_available_and_editor_adds_missing_dependencies(self) -> None:
        from g import Game, PlatformerMotor, SceneSerializer

        for existing_collider in (None, "g.collider"):
            with self.subTest(collider=existing_collider):
                self.state = EditorState()
                record = self.state.add_object("empty")
                self.assertIn("g.platformer_motor", self.state.available_component_types)
                if existing_collider:
                    self.state.add_component(existing_collider)
                self.state.add_component("g.platformer_motor")
                components = record["components"]
                assert isinstance(components, list)
                names = [item["type"] for item in components if isinstance(item, dict)]
                self.assertEqual(names, (
                    [existing_collider, "g.physics_body", "g.platformer_motor"]
                    if existing_collider else
                    ["g.physics_body", "g.collider", "g.platformer_motor"]
                ))
                game = Game(320, 180)
                scene = SceneSerializer().restore(game, self.state.document, asset_root=".")
                self.assertIsNotNone(scene.objects[0].get_component(PlatformerMotor))
                self.assertEqual(scene.objects[0].collider.friction, 0)

    def test_adding_platformer_motor_sets_friction_once_and_undo_restores_it(self) -> None:
        record = self.state.add_object("rectangle")
        self.state.set_component_value(1, "friction", 0.9)
        before = deepcopy(record["components"])
        self.state.add_component("g.platformer_motor")
        components = record["components"]
        assert isinstance(components, list)
        self.assertEqual(components[1]["properties"]["friction"], 0)
        self.assertTrue(self.state.undo())
        restored = self.state.selected
        assert restored is not None
        self.assertEqual(restored["components"], before)
        self.assertTrue(self.state.redo())
        self.state.set_component_value(1, "friction", 0.2)
        self.assertEqual(self.state.selected["components"][1]["properties"]["friction"], 0.2)

    def test_pasting_platformer_motor_sets_collider_friction(self) -> None:
        record = self.state.add_object("empty")
        self.state.add_component("g.collider")
        self.state.paste_component({"type": "g.platformer_motor", "properties": {}})
        self.assertEqual(record["components"][0]["properties"]["friction"], 0)

    def test_sprite_animator_adds_missing_renderer(self) -> None:
        record = self.state.add_object("empty")
        self.assertIn("g.sprite_animator", self.state.available_component_types)
        self.state.add_component("g.sprite_animator")
        components = record["components"]
        assert isinstance(components, list)
        self.assertEqual(
            [item["type"] for item in components if isinstance(item, dict)],
            ["g.sprite_renderer", "g.sprite_animator"],
        )
        self.assertTrue(self.state.undo())
        assert self.state.selected is not None
        self.assertEqual(self.state.selected["components"], [])

    def test_only_one_player_controller_can_be_added(self) -> None:
        self.state.add_object("rectangle")
        self.state.add_component("g.top_down_motor")

        self.assertNotIn(
            "g.top_down_motor",
            self.state.available_component_types,
        )
        self.assertNotIn(
            "g.platformer_motor",
            self.state.available_component_types,
        )

    def test_custom_component_dependencies_drive_editor_actions(self) -> None:
        record = self.state.add_object("empty")

        self.state.add_component(
            "tests.editor_dependent_component"
        )

        components = record["components"]
        assert isinstance(components, list)
        self.assertEqual(
            [
                component["type"]  # type: ignore[index]
                for component in components
            ],
            [
                "tests.editor_required_component",
                "tests.editor_dependent_component",
            ],
        )
        with self.assertRaisesRegex(
            ValueError,
            "cannot remove tests.editor_required_component",
        ):
            self.state.remove_component(0)

    def test_editor_adds_dependencies_even_without_runtime_auto_add(self) -> None:
        record = self.state.add_object("empty")
        self.assertIn("tests.editor_manual_dependency", self.state.available_component_types)
        self.state.add_component("tests.editor_manual_dependency")
        components = record["components"]
        assert isinstance(components, list)
        self.assertEqual(
            [item["type"] for item in components if isinstance(item, dict)],
            ["tests.editor_required_component", "tests.editor_manual_dependency"],
        )

    def test_script_vec2_defaults_edits_reset_and_round_trip(self) -> None:
        from g import Game, SceneSerializer, Vec2
        from g.persistence import scene_types

        class VectorScript(Script):
            offset: Vec2 = Vec2(10, 20)

        VectorScript.__module__ = "editor_vector_test"
        name = "editor_vector_test.VectorScript"
        scene_types.register_component(name, VectorScript)
        try:
            record = self.state.add_object("empty")
            self.state.add_component(name)
            components = record["components"]
            assert isinstance(components, list)
            component = components[0]
            assert isinstance(component, dict)
            properties = component["properties"]
            assert isinstance(properties, dict)
            self.assertEqual(properties["offset"], {"$vec2": [10, 20]})
            self.state.set_component_value(0, "offset", {"$vec2": [-3.5, 4.25]})
            game = Game(320, 180)
            scene = SceneSerializer().restore(game, self.state.document, asset_root=".")
            offset = scene.objects[0].require_component(VectorScript).offset
            self.assertIsInstance(offset, Vec2)
            self.assertEqual(offset, Vec2(-3.5, 4.25))
            offset.x = 99
            self.assertEqual(VectorScript.offset, Vec2(10, 20))
            self.state.reset_component(0)
            self.assertTrue(self.state.undo())
            assert self.state.selected is not None
            saved = self.state.selected["components"]
            assert isinstance(saved, list) and isinstance(saved[0], dict)
            restored_properties = saved[0]["properties"]
            assert isinstance(restored_properties, dict)
            self.assertEqual(restored_properties["offset"], {"$vec2": [-3.5, 4.25]})
            self.assertTrue(self.state.redo())
            assert self.state.selected is not None
            saved = self.state.selected["components"]
            assert isinstance(saved, list) and isinstance(saved[0], dict)
            restored_properties = saved[0]["properties"]
            assert isinstance(restored_properties, dict)
            self.assertEqual(restored_properties["offset"], {"$vec2": [10, 20]})
        finally:
            scene_types.unregister_module(VectorScript.__module__)

    def test_nested_motor_conflict_is_atomic(self) -> None:
        from g import PlatformerMotor
        from g.persistence import scene_types

        class Player(Script):
            pass

        Player.__module__ = "editor_nested_motor_test"
        requires_components(PlatformerMotor)(Player)
        name = "editor_nested_motor_test.Player"
        scene_types.register_component(name, Player)
        try:
            record = self.state.add_object("empty")
            self.state.add_component("g.top_down_motor")
            before = deepcopy(record["components"])
            revision = self.state.revision
            self.assertIn(name, self.state.available_component_types)
            with self.assertRaisesRegex(ValueError, "movement motor"):
                self.state.add_component(name)
            self.assertEqual(record["components"], before)
            self.assertEqual(self.state.revision, revision)
        finally:
            scene_types.unregister_module(Player.__module__)

    def test_dependency_cycle_add_and_paste_fail_atomically(self) -> None:
        from g.persistence import scene_types

        class First(Script):
            pass

        class Second(Script):
            pass

        First.__module__ = Second.__module__ = "editor_cycle_test"
        requires_components(Second)(First)
        requires_components(First)(Second)
        scene_types.register_component("editor_cycle_test.First", First)
        scene_types.register_component("editor_cycle_test.Second", Second)
        try:
            record = self.state.add_object("empty")
            revision = self.state.revision
            clipboard = {"type": "editor_cycle_test.First", "properties": {}}
            self.assertIn("dependency cycle", self.state.component_paste_error(clipboard) or "")
            with self.assertRaisesRegex(ValueError, "dependency cycle"):
                self.state.add_component("editor_cycle_test.First")
            with self.assertRaisesRegex(ValueError, "dependency cycle"):
                self.state.paste_component(clipboard)
            self.assertEqual(record["components"], [])
            self.assertEqual(self.state.revision, revision)
        finally:
            scene_types.unregister_module(First.__module__)

    def test_game_object_properties_are_editable(self) -> None:
        record = self.state.add_object("empty")

        self.state.set_object_script_properties(
            {"team": "player", "damage": 3}
        )

        self.assertEqual(
            record["script_properties"],
            {"team": "player", "damage": 3},
        )

    def test_undo_and_redo_restore_document_and_selection(self) -> None:
        record = self.state.add_object("rectangle")
        identifier = str(record["id"])
        self.state.set_object_value("name", "Platform")

        self.assertTrue(self.state.can_undo)
        self.assertEqual(self.state.undo_label, "Changed name")
        self.assertTrue(self.state.undo())
        restored = self.state.selected
        assert restored is not None
        self.assertEqual(restored["id"], identifier)
        self.assertEqual(restored["name"], "Rectangle")

        self.assertTrue(self.state.redo())
        restored = self.state.selected
        assert restored is not None
        self.assertEqual(restored["name"], "Platform")

    def test_new_change_clears_redo_history(self) -> None:
        self.state.add_object("rectangle")
        self.assertTrue(self.state.undo())
        self.assertTrue(self.state.can_redo)

        self.state.add_object("circle")

        self.assertFalse(self.state.can_redo)

    def test_history_coalesces_continuous_property_changes(self) -> None:
        self.state.add_object("rectangle")
        self.state.set_object_value("x", 10)
        self.state.set_object_value("x", 20)

        self.assertTrue(self.state.undo())
        record = self.state.selected
        assert record is not None
        self.assertEqual(position(record)[0], 0)

    def test_undo_back_to_saved_document_clears_dirty_state(self) -> None:
        original = deepcopy(self.state.document.to_dict())
        self.state.add_object("rectangle")
        self.assertTrue(self.state.dirty)

        self.state.undo()

        self.assertFalse(self.state.dirty)
        self.assertEqual(self.state.document.to_dict(), original)

    def test_layer_visibility_toggle_does_not_change_active_layer(self) -> None:
        first = self.state.selected_layer
        assert first is not None
        first_id = str(first["id"])
        second = self.state.add_layer("Foreground")
        second_id = str(second["id"])

        self.state.set_layer_value_for(first_id, "visible", False)

        self.assertEqual(self.state.selected_layer_id, second_id)
        self.assertFalse(first["visible"])

    def test_linked_prefab_refresh_preserves_instance_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.state.asset_root = root
            source = self.state.add_object("rectangle", x=10, y=20)
            child = new_object_record("circle", x=4, y=5)
            children = source["children"]
            assert isinstance(children, list)
            children.append(child)
            prefab_path = root / "Platform.prefab.json"
            first = self.state.prefab_document_for_record(source)
            first.write(prefab_path)

            self.state.delete_selected()
            instance = self.state.instantiate_prefab(
                prefab_path,
                position=(200, 300),
            )
            components = instance["components"]
            assert isinstance(components, list)
            renderer = components[0]
            assert isinstance(renderer, dict)
            properties = renderer["properties"]
            assert isinstance(properties, dict)
            properties["fill"] = [255, 0, 0, 255]
            instance["name"] = "Local Name"

            updated_source = deepcopy(first.object)
            updated_source["name"] = "Prefab Name"
            updated_components = updated_source["components"]
            assert isinstance(updated_components, list)
            updated_renderer = updated_components[0]
            assert isinstance(updated_renderer, dict)
            updated_properties = updated_renderer["properties"]
            assert isinstance(updated_properties, dict)
            updated_properties["width"] = 128
            second = self.state.prefab_document_for_record(
                updated_source,
                first,
            )
            second.write(prefab_path)

            self.assertEqual(
                self.state.refresh_prefab_instances(
                    second,
                    "Platform.prefab.json",
                ),
                1,
            )
            refreshed = self.state.selected
            assert refreshed is not None
            self.assertEqual(position(refreshed), (200, 300))
            self.assertEqual(refreshed["name"], "Local Name")
            refreshed_components = refreshed["components"]
            assert isinstance(refreshed_components, list)
            refreshed_renderer = refreshed_components[0]
            assert isinstance(refreshed_renderer, dict)
            refreshed_properties = refreshed_renderer["properties"]
            assert isinstance(refreshed_properties, dict)
            self.assertEqual(
                refreshed_properties["fill"],
                [255, 0, 0, 255],
            )
            self.assertEqual(refreshed_properties["width"], 128)
            self.assertGreater(
                self.state.selected_prefab_override_count(),
                0,
            )

            self.assertTrue(self.state.revert_selected_prefab())
            reverted = self.state.selected
            assert reverted is not None
            self.assertEqual(position(reverted), (200, 300))
            self.assertEqual(reverted["name"], "Prefab Name")
            self.assertEqual(
                self.state.selected_prefab_override_count(),
                0,
            )

    def test_unpack_prefab_removes_link_and_template_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.state.asset_root = root
            source = self.state.add_object("rectangle")
            path = root / "Box.prefab.json"
            PrefabDocument.create(source).write(path)
            self.state.delete_selected()
            self.state.instantiate_prefab(path)

            self.assertTrue(self.state.unpack_selected_prefab())

            record = self.state.selected
            assert record is not None
            self.assertNotIn("prefab", record)
            self.assertNotIn("template_id", record)

    def test_ui_view_prefab_instances_refresh_and_preserve_scene_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.state.asset_root = root
            view = self.state.document.ui_views[0]
            self.state.select_layer(str(view["id"]))
            button = self.state.add_gui_element("button")
            button["text"] = "Play"
            self.state.select_layer(str(view["id"]))
            path = root / "HUD.prefab.json"
            first = self.state.prefab_document_for_record(view)
            self.assertEqual(first.kind, "ui_view")
            first.write(path)
            loaded = PrefabDocument.read(path)
            self.assertEqual(loaded.kind, "ui_view")
            self.assertNotIn("startup", loaded.template)

            linked = self.state.link_selected_to_prefab(
                loaded,
                "HUD.prefab.json",
            )
            linked["name"] = "Local HUD"
            linked["transparent"] = False
            instance = self.state.instantiate_ui_view_prefab(path)

            updated_template = deepcopy(first.template)
            updated_template["pauses_scene"] = True
            elements = updated_template["elements"]
            assert isinstance(elements, list)
            updated_button = elements[0]
            assert isinstance(updated_button, dict)
            updated_button["text"] = "Continue"
            second = PrefabDocument.create(
                updated_template,
                prefab_id=first.prefab_id,
                revision=2,
                kind="ui_view",
            )
            second.write(path)

            self.assertEqual(
                self.state.refresh_prefab_instances(
                    second,
                    "HUD.prefab.json",
                ),
                2,
            )
            startup = self.state.document.ui_views[0]
            secondary = self.state.document.ui_views[1]
            self.assertTrue(startup["startup"])
            self.assertFalse(secondary["startup"])
            self.assertEqual(startup["name"], "Local HUD")
            self.assertFalse(startup["transparent"])
            self.assertTrue(startup["pauses_scene"])
            startup_elements = startup["elements"]
            assert isinstance(startup_elements, list)
            refreshed_button = startup_elements[0]
            assert isinstance(refreshed_button, dict)
            self.assertEqual(refreshed_button["text"], "Continue")
            self.assertNotEqual(startup["id"], secondary["id"])
            self.state.select_layer(str(startup["id"]))
            self.assertGreater(
                self.state.selected_prefab_override_count(),
                0,
            )

            scene_path = root / "ui-prefabs.scene.json"
            self.state.save(scene_path)
            reopened = EditorState(asset_root=root)
            reopened.open(scene_path)
            self.assertEqual(len(reopened.document.ui_views), 2)
            reopened_startup = reopened.document.ui_views[0]
            self.assertTrue(reopened_startup["startup"])
            reopened_metadata = reopened_startup["prefab"]
            assert isinstance(reopened_metadata, dict)
            self.assertEqual(reopened_metadata["revision"], 2)
            reopened_elements = reopened_startup["elements"]
            assert isinstance(reopened_elements, list)
            reopened_button = reopened_elements[0]
            assert isinstance(reopened_button, dict)
            self.assertEqual(reopened_button["text"], "Continue")

            self.assertTrue(self.state.revert_selected_prefab())
            reverted = self.state.selected_ui_view
            assert reverted is not None
            self.assertTrue(reverted["startup"])
            self.assertEqual(reverted["name"], "Local HUD")
            self.assertEqual(
                self.state.selected_prefab_override_count(),
                0,
            )

            self.state.select_layer(str(instance["id"]))
            self.assertTrue(self.state.unpack_selected_prefab())
            unpacked = self.state.selected_ui_view
            assert unpacked is not None
            self.assertNotIn("prefab", unpacked)
            self.assertNotIn("template_id", unpacked)
            unpacked_elements = unpacked["elements"]
            assert isinstance(unpacked_elements, list)
            unpacked_button = unpacked_elements[0]
            assert isinstance(unpacked_button, dict)
            self.assertNotIn("template_id", unpacked_button)

    def test_open_refreshes_prefab_revision_without_marking_dirty(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.state.asset_root = root
            source = self.state.add_object("circle")
            prefab_path = root / "Enemy.prefab.json"
            first = PrefabDocument.create(source)
            first.write(prefab_path)
            self.state.delete_selected()
            self.state.instantiate_prefab(
                prefab_path,
                position=(80, 90),
            )
            scene_path = root / "level.scene.json"
            self.state.save(scene_path)
            updated = deepcopy(first.object)
            updated["name"] = "Updated Enemy"
            PrefabDocument.create(
                updated,
                prefab_id=first.prefab_id,
                revision=2,
            ).write(prefab_path)

            reopened = EditorState(asset_root=root)
            reopened.open(scene_path)

            instance = reopened.records[0]
            self.assertEqual(instance["name"], "Updated Enemy")
            self.assertEqual(position(instance), (80, 90))
            metadata = instance["prefab"]
            assert isinstance(metadata, dict)
            self.assertEqual(metadata["revision"], 2)
            self.assertFalse(reopened.dirty)

    def test_prefab_rejects_external_object_references(self) -> None:
        record = self.state.add_object("empty")
        record["properties"] = {
            "target": {"$object": "outside-object"}
        }

        with self.assertRaisesRegex(
            SceneFormatError,
            "outside its hierarchy",
        ):
            PrefabDocument.create(record)

    def test_grid_snapping_updates_scene_data(self) -> None:
        self.state.add_object("circle")
        self.state.set_grid_size(20)

        self.state.move_selected(33, 49)

        record = self.state.selected
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(position(record), (30, 50))

        self.state.move_selected(-1, -21)

        self.assertEqual(position(record), (-10, -30))

    def test_hierarchy_order_controls_generated_z_indices(self) -> None:
        first = self.state.add_object("rectangle")
        self.state.select(None)
        second = self.state.add_object("circle")

        self.assertEqual(first["z_index"], 1)
        self.assertEqual(second["z_index"], 0)
        self.state.select(str(first["id"]))
        with self.assertRaises(KeyError):
            self.state.set_object_value("z_index", 99)

    def test_render_behind_parent_orders_nested_subtrees(self) -> None:
        parent = new_object_record()
        behind_first = new_object_record()
        behind_second = new_object_record()
        front_first = new_object_record()
        front_second = new_object_record()
        nested = new_object_record()
        other_root = new_object_record()
        behind_first["render_behind_parent"] = True
        behind_second["render_behind_parent"] = True
        parent["render_behind_parent"] = True
        behind_first["children"] = [nested]
        parent["children"] = [
            behind_first, front_first, behind_second, front_second,
        ]

        normalize_record_order([parent, other_root])

        expected = [
            front_first, front_second, parent, nested,
            behind_first, behind_second, other_root,
        ]
        self.assertEqual(
            [record["z_index"] for record in expected],
            list(range(len(expected) - 1, -1, -1)),
        )

    def test_render_behind_parent_toggle_supports_undo_and_redo(self) -> None:
        parent = self.state.add_object("empty")
        child = self.state.add_object("rectangle")
        parent_id, child_id = str(parent["id"]), str(child["id"])
        self.assertGreater(record_z_index(child), record_z_index(parent))

        self.state.set_object_value("render_behind_parent", True)

        self.assertLess(record_z_index(child), record_z_index(parent))
        self.assertTrue(self.state.undo())
        parent = next(
            record for record in self.state.records if record["id"] == parent_id
        )
        child = next(
            record for record in self.state.records if record["id"] == child_id
        )
        self.assertFalse(child["render_behind_parent"])
        self.assertGreater(record_z_index(child), record_z_index(parent))
        self.assertTrue(self.state.redo())
        parent = next(
            record for record in self.state.records if record["id"] == parent_id
        )
        child = next(
            record for record in self.state.records if record["id"] == child_id
        )
        self.assertTrue(child["render_behind_parent"])
        self.assertLess(record_z_index(child), record_z_index(parent))
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            self.state.set_object_value("render_behind_parent", 1)

    def test_render_behind_parent_survives_save_reload_and_runtime(self) -> None:
        from g import Game, RectRenderer, SceneSerializer

        parent = self.state.add_object("rectangle")
        parent["name"] = "Parent"
        child = self.state.add_object("rectangle")
        child["name"] = "Trail"
        self.state.set_object_value("render_behind_parent", True)
        with tempfile.TemporaryDirectory() as directory:
            path = self.state.save(Path(directory) / "scene.scene.json")
            reopened = EditorState(path)
            scene = SceneSerializer().restore(
                Game(320, 180), reopened.document, asset_root=directory,
            )
        loaded_parent = scene.require_child("Parent")
        loaded_child = loaded_parent.require_child("Trail")
        self.assertTrue(loaded_child.render_behind_parent)
        self.assertFalse(loaded_parent.render_behind_parent)
        self.assertLess(loaded_child.z_index, loaded_parent.z_index)
        layer = scene.default_layer
        self.assertIs(
            loaded_child.require_component(RectRenderer).drawable.group,
            layer._group_for(loaded_child.z_index),
        )
        captured = SceneSerializer().capture(scene, asset_root=".")
        children = captured.objects[0]["children"]
        assert isinstance(children, list)
        captured_child = children[0]
        assert isinstance(captured_child, dict)
        self.assertTrue(captured_child["render_behind_parent"])

    def test_configuring_tileset_matches_grid_and_selects_first_tile(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(48, 32).save(str(root / "tiles.png"))
            state = EditorState(asset_root=root)
            layer = state.add_tile_layer("Ground")
            state.paint_tile_id = 12

            state.configure_tile_layer("tiles.png", 16, 16)

        self.assertEqual(state.grid_size, 16)
        self.assertEqual(layer["cell_size"], [16, 16])
        self.assertEqual(state.paint_tile_id, 0)
        tileset = layer["tileset"]
        assert isinstance(tileset, dict)
        self.assertEqual(tileset["columns"], 3)
        self.assertEqual(tileset["tile_count"], 6)

    def test_color_palette_tiles_can_be_added_edited_and_painted(
        self,
    ) -> None:
        layer = self.state.add_tile_layer("Prototype")

        palette = self.state.add_color_tileset(
            color=[40, 120, 60, 255],
        )
        second_id = self.state.add_color_tile([70, 90, 180])
        self.state.set_tile_color(second_id, [80, 100, 200, 220])
        self.state.paint_tile(1, 1, second_id)

        self.assertEqual(palette["type"], "color")
        self.assertEqual(palette["tile_count"], 2)
        self.assertEqual(
            palette["colors"],
            [
                [40, 120, 60, 255],
                [80, 100, 200, 220],
            ],
        )
        self.assertEqual(
            self.state.tile_color(second_id),
            [80, 100, 200, 220],
        )
        self.assertEqual(layer["cell_size"], [32, 32])
        self.assertEqual(self.state.tile_at(0, 0), TileCell(second_id))

    def test_color_palette_cannot_expand_into_following_tileset(
        self,
    ) -> None:
        layer = self.state.add_tile_layer("Prototype")
        palette = self.state.add_color_tileset()
        first_gid = palette["first_gid"]
        assert isinstance(first_gid, int)
        layer["tilesets"] = [
            palette,
            {
                "type": "atlas",
                "id": "following",
                "name": "Following",
                "first_gid": first_gid + 1,
                "image": "tiles.png",
                "tile_size": [16, 16],
                "columns": 1,
                "tile_count": 1,
                "margin": 0,
                "spacing": 0,
                "properties": {},
                "tiles": [],
            },
        ]
        self.state.select_tileset(str(palette["id"]))

        with self.assertRaisesRegex(ValueError, "cannot grow"):
            self.state.add_color_tile()

    def test_color_palettes_reserve_space_for_stable_appended_ids(
        self,
    ) -> None:
        self.state.add_tile_layer("Prototype")
        first = self.state.add_color_tileset()
        second = self.state.add_color_tileset()

        self.assertEqual(first["first_gid"], 0)
        self.assertEqual(second["first_gid"], 256)

    def test_image_collection_tiles_can_be_added_and_painted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, color, size in (
                ("crate.png", (150, 90, 40, 255), (12, 20)),
                ("key.png", (240, 210, 50, 255), (24, 10)),
            ):
                pyglet.image.SolidColorImagePattern(color).create_image(
                    *size
                ).save(str(root / name))
            state = EditorState(asset_root=root)
            layer = state.add_tile_layer("Items")

            collection = state.add_image_tileset("crate.png")
            key_id = state.add_image_tile("key.png")
            state.paint_tile(1, 1, key_id)

        self.assertEqual(collection["type"], "images")
        self.assertEqual(
            collection["images"],
            ["crate.png", "key.png"],
        )
        self.assertEqual(collection["tile_count"], 2)
        self.assertEqual(layer["cell_size"], [12, 20])
        self.assertEqual(state.tile_at(0, 0), TileCell(key_id))

    def test_image_collections_reserve_stable_id_space(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("first.png", "second.png"):
                pyglet.image.SolidColorImagePattern(
                    (255, 255, 255, 255)
                ).create_image(16, 16).save(str(root / name))
            state = EditorState(asset_root=root)
            state.add_tile_layer("Items")
            first = state.add_image_tileset("first.png")
            second = state.add_image_tileset("second.png")

        self.assertEqual(first["first_gid"], 0)
        self.assertEqual(second["first_gid"], 256)

    def test_color_palette_hit_testing_matches_swatch_grid(self) -> None:
        self.assertEqual(
            TilePalettePanel._color_tile_at(43, 1, 3, 5, 36, 6),
            1,
        )
        self.assertEqual(
            TilePalettePanel._color_tile_at(1, 43, 3, 5, 36, 6),
            3,
        )
        self.assertIsNone(
            TilePalettePanel._color_tile_at(38, 1, 3, 5, 36, 6)
        )
        self.assertIsNone(
            TilePalettePanel._color_tile_at(85, 43, 3, 5, 36, 6)
        )

    def test_palette_drag_preview_calculates_rectangular_selection(self) -> None:
        self.assertEqual(
            TilePalettePanel._palette_selection_rectangle(
                1,
                7,
                columns=4,
                tile_count=12,
            ),
            ([1, 2, 3, 5, 6, 7], 3, 2),
        )
        self.assertEqual(
            TilePalettePanel._palette_selection_rectangle(
                7,
                1,
                columns=4,
                tile_count=12,
            ),
            ([1, 2, 3, 5, 6, 7], 3, 2),
        )
        self.assertIsNone(
            TilePalettePanel._palette_selection_rectangle(
                4,
                7,
                columns=4,
                tile_count=7,
            )
        )

    def test_tile_palette_zoom_stays_within_supported_range(self) -> None:
        self.assertEqual(TilePalettePanel._clamp_palette_zoom(0.01), 0.25)
        self.assertEqual(TilePalettePanel._clamp_palette_zoom(1.0), 1.0)
        self.assertEqual(TilePalettePanel._clamp_palette_zoom(8.0), 4.0)

    def test_scene_background_requires_rgba_color(self) -> None:
        self.state.set_scene_background([12, 34, 56, 200])

        self.assertEqual(
            self.state.document.settings["background"],
            [12, 34, 56, 200],
        )

    def test_scene_resolution_requires_positive_integers(self) -> None:
        self.assertEqual(
            self.state.document.settings["size"],
            list(DEFAULT_SCENE_SIZE),
        )
        self.assertEqual(
            SCENE_RESOLUTION_PRESETS,
            (
                (640, 360),
                (960, 540),
                (1280, 720),
                (1600, 900),
                (1920, 1080),
            ),
        )
        revision = self.state.revision

        self.state.set_scene_size(1920, 1080)

        self.assertEqual(
            self.state.document.settings["size"],
            [1920, 1080],
        )
        self.assertGreater(self.state.revision, revision)
        unchanged_revision = self.state.revision
        self.state.set_scene_size(1920, 1080)
        self.assertEqual(self.state.revision, unchanged_revision)
        for size in ((0, 720), (1280, -1), (True, 720), (800.5, 600)):
            with self.subTest(size=size):
                with self.assertRaisesRegex(
                    ValueError,
                    "two positive integers",
                ):
                    self.state.set_scene_size(*size)
        with self.assertRaisesRegex(ValueError, "four color channels"):
            self.state.set_scene_background([255, 255, 255])

    def test_starting_camera_settings_are_validated_and_persisted(
        self,
    ) -> None:
        self.state.set_scene_camera_value("position", [120, -45])
        self.state.set_scene_camera_value("zoom", 1.75)
        self.state.set_scene_camera_value("rotation", 30)

        camera = self.state.document.settings["camera"]
        assert isinstance(camera, dict)
        self.assertEqual(camera["position"], {"$vec2": [120.0, -45.0]})
        self.assertEqual(camera["zoom"], 1.75)
        self.assertEqual(camera["rotation"], 30.0)
        with self.assertRaisesRegex(ValueError, "positive"):
            self.state.set_scene_camera_value("zoom", 0)
        with self.assertRaisesRegex(ValueError, "two numbers"):
            self.state.set_scene_camera_value("position", [100])
        with self.assertRaisesRegex(ValueError, "finite"):
            self.state.set_scene_camera_value("rotation", float("inf"))

    def test_empty_sprite_gets_image_and_collider_later(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pyglet.image.SolidColorImagePattern(
                (255, 0, 0, 255)
            ).create_image(24, 32).save(str(root / "hero.png"))
            state = EditorState(asset_root=root)

            record = state.add_sprite()
            components = record["components"]
            assert isinstance(components, list)
            self.assertEqual(record["name"], "Sprite")
            self.assertEqual(len(components), 1)
            renderer = components[0]
            assert isinstance(renderer, dict)
            renderer_properties = renderer["properties"]
            assert isinstance(renderer_properties, dict)
            self.assertIsNone(renderer_properties["image"])
            self.assertIn(record, state.document.objects)

            state.set_selected_sprite_image("hero.png")

        self.assertEqual(renderer_properties["image"], "hero.png")
        self.assertEqual(record["name"], "hero")
        collider = components[1]
        assert isinstance(collider, dict)
        self.assertEqual(collider["type"], "g.collider")
        self.assertEqual(
            collider["properties"],
            {"shape": "box", "width": 24, "height": 32, "enabled": True},
        )

    def test_sprite_uses_selected_project_image_and_updates_collider(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "hero.png"
            second = root / "hero-large.png"
            pyglet.image.SolidColorImagePattern(
                (255, 0, 0, 255)
            ).create_image(24, 32).save(str(first))
            pyglet.image.SolidColorImagePattern(
                (0, 255, 0, 255)
            ).create_image(48, 64).save(str(second))
            state = EditorState(asset_root=root)

            parent = state.add_object("empty")
            record = state.add_sprite(
                "hero.png",
                x=10,
                y=20,
                at_root=True,
            )
            state.set_selected_sprite_image(second)

        self.assertEqual(record["type"], "g.game_object")
        self.assertEqual(record["name"], "hero-large")
        self.assertIn(record, state.document.objects)
        self.assertEqual(parent["children"], [])
        components = record["components"]
        assert isinstance(components, list)
        renderer = components[0]
        collider = components[1]
        assert isinstance(renderer, dict)
        assert isinstance(collider, dict)
        renderer_properties = renderer["properties"]
        collider_properties = collider["properties"]
        assert isinstance(renderer_properties, dict)
        assert isinstance(collider_properties, dict)
        self.assertEqual(
            renderer_properties["image"],
            "hero-large.png",
        )
        self.assertEqual(
            (
                collider_properties["width"],
                collider_properties["height"],
            ),
            (48, 64),
        )

    def test_deleting_nested_selection_removes_canonical_record(self) -> None:
        parent = new_object_record()
        child = new_object_record("text")
        parent["children"] = [child]
        self.state.document.objects.append(parent)
        identifier = child["id"]
        self.assertIsInstance(identifier, str)
        assert isinstance(identifier, str)
        self.state.select(identifier)

        self.assertTrue(self.state.delete_selected())

        self.assertEqual(parent["children"], [])
        self.assertIsNone(self.state.selected)

    def test_ordered_selection_supports_toggle_and_ranges(self) -> None:
        first = self.state.add_object("empty")
        second = self.state.add_object("empty")
        third = self.state.add_object("empty")
        identifiers = [
            str(first["id"]),
            str(second["id"]),
            str(third["id"]),
        ]

        self.state.select(identifiers[0])
        self.state.select(identifiers[2], toggle=True)

        self.assertEqual(
            self.state.selected_ids,
            [identifiers[0], identifiers[2]],
        )
        self.assertIs(self.state.selected, third)

        self.state.select(identifiers[0])
        self.state.select_range(identifiers[2], identifiers)

        self.assertEqual(
            self.state.selected_ids,
            [identifiers[0], identifiers[1], identifiers[2]],
        )
        self.assertIs(self.state.selected, third)

        self.state.select(identifiers[1], toggle=True)

        self.assertEqual(
            self.state.selected_ids,
            [identifiers[0], identifiers[2]],
        )

    def test_deleting_multiple_objects_ignores_selected_descendants(self) -> None:
        parent = self.state.add_object("empty")
        child = self.state.add_object("rectangle")
        self.state.select(str(parent["id"]))
        self.state.select(str(child["id"]), additive=True)

        self.assertTrue(self.state.delete_selected())

        self.assertEqual(self.state.document.objects, [])
        self.assertEqual(self.state.selected_ids, [])

    def test_selection_does_not_mix_scene_layers(self) -> None:
        first = self.state.add_object("rectangle")
        foreground = self.state.add_layer("Foreground")
        second = self.state.add_object("circle")

        self.state.select(str(first["id"]))
        self.state.select(str(second["id"]), toggle=True)

        self.assertEqual(self.state.selected_ids, [second["id"]])
        self.assertEqual(
            self.state.selected_layer_id,
            foreground["id"],
        )

    def test_copy_paste_regenerates_recursive_object_ids(self) -> None:
        parent = self.state.add_object("empty", x=10, y=20)
        child = self.state.add_object("rectangle", x=5, y=6)
        self.state.select(str(parent["id"]))
        self.state.select(str(child["id"]), additive=True)

        copied = self.state.copy_selected()

        self.assertIsNotNone(copied)
        assert copied is not None
        kind, records = copied
        self.assertEqual(kind, "object")
        self.assertEqual(len(records), 1)
        self.state.select(None)
        pasted = self.state.paste_copied(kind, records)

        self.assertEqual(len(pasted), 1)
        clone = pasted[0]
        self.assertNotEqual(clone["id"], parent["id"])
        clone_children = clone["children"]
        assert isinstance(clone_children, list)
        self.assertEqual(len(clone_children), 1)
        clone_child = clone_children[0]
        assert isinstance(clone_child, dict)
        self.assertNotEqual(clone_child["id"], child["id"])
        self.assertEqual(len(self.state.document.objects), 2)

    def test_gui_clipboard_pastes_into_selected_container(self) -> None:
        layer = self.state.add_ui_view("Test HUD")
        panel = self.state.add_gui_element("panel")
        self.state.select(None)
        button = self.state.add_gui_element("button")
        copied = self.state.copy_selected()
        assert copied is not None
        kind, records = copied
        self.state.select(str(panel["id"]))

        pasted = self.state.paste_copied(
            kind,
            records,
            parent_identifier=str(panel["id"]),
        )

        children = panel["children"]
        assert isinstance(children, list)
        self.assertEqual(children, list(pasted))
        self.assertEqual(layer["elements"], [panel, button])
        self.assertNotEqual(pasted[0]["id"], button["id"])

    def test_object_reparent_preserves_world_transform_and_order(self) -> None:
        first_parent = new_object_record(x=100, y=100)
        child = new_object_record("rectangle", x=10, y=20)
        first_parent["children"] = [child]
        second_parent = new_object_record(x=300, y=200)
        first_parent["transform"]["rotation"] = 30  # type: ignore[index]
        first_parent["transform"]["scale"] = [2, 3]  # type: ignore[index]
        child["transform"]["rotation"] = 20  # type: ignore[index]
        child["transform"]["scale"] = [0.75, 1.25]  # type: ignore[index]
        second_parent["transform"]["rotation"] = -15  # type: ignore[index]
        second_parent["transform"]["scale"] = [0.5, 2]  # type: ignore[index]
        roots = self.state.document.objects
        roots.extend([first_parent, second_parent])
        child_id = str(child["id"])
        second_parent_id = str(second_parent["id"])
        self.state.select(child_id)
        before = self.state.object_world_transform(child)

        self.state.reparent_selected_objects(
            second_parent_id,
            "inside",
        )

        self.assertEqual(first_parent["children"], [])
        self.assertEqual(second_parent["children"], [child])
        child_z = child["z_index"]
        parent_z = second_parent["z_index"]
        assert isinstance(child_z, int)
        assert isinstance(parent_z, int)
        self.assertGreater(child_z, parent_z)
        for actual, expected in zip(
            self.state.object_world_transform(child),
            before,
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected)

        self.state.select(second_parent_id)
        self.state.reparent_selected_objects(
            str(first_parent["id"]),
            "before",
        )

        self.assertEqual(roots, [second_parent, first_parent])
        second_z = second_parent["z_index"]
        first_z = first_parent["z_index"]
        assert isinstance(second_z, int)
        assert isinstance(first_z, int)
        self.assertGreater(second_z, first_z)

    def test_object_reparent_rejects_own_descendant(self) -> None:
        parent = self.state.add_object("empty")
        child = self.state.add_object("rectangle")
        self.state.select(str(parent["id"]))

        with self.assertRaisesRegex(ValueError, "own subtree"):
            self.state.reparent_selected_objects(
                str(child["id"]),
                "inside",
            )

    def test_editor_can_set_selected_world_position_and_rotation(
        self,
    ) -> None:
        parent = self.state.add_object(
            "empty",
            x=100,
            y=200,
        )
        parent["transform"]["rotation"] = 30  # type: ignore[index]
        parent["transform"]["scale"] = [2, 3]  # type: ignore[index]
        child = self.state.add_object("rectangle", x=10, y=20)
        self.state.set_snap_enabled(False)

        self.state.move_selected_world(400, 500)
        self.state.rotate_selected_world(75)

        world = self.state.object_world_transform(child)
        self.assertAlmostEqual(world[0], 400)
        self.assertAlmostEqual(world[1], 500)
        self.assertAlmostEqual(world[2], 75)

    def test_ui_views_are_separate_and_properties_are_editable(self) -> None:
        self.assertEqual(
            [layer["type"] for layer in self.state.document.layers],
            ["tile", "object"],
        )
        self.assertEqual(
            [view["name"] for view in self.state.document.ui_views],
            ["HUD"],
        )

        pause = self.state.add_ui_view("Pause")
        self.state.set_layer_value("modal", True)
        self.state.set_layer_value("pauses_scene", True)
        self.state.make_selected_ui_view_startup()

        self.assertTrue(pause["modal"])
        self.assertTrue(pause["pauses_scene"])
        self.assertTrue(pause["startup"])
        self.assertFalse(self.state.document.ui_views[0]["startup"])
        self.assertNotIn(pause, self.state.document.layers)

    def test_gui_reparent_preserves_screen_position_for_panels(self) -> None:
        layer = self.state.add_ui_view("Test HUD")
        first_panel = self.state.add_gui_element("panel")
        child = self.state.add_gui_element("button")
        self.state.select(None)
        second_panel = self.state.add_gui_element("panel")
        first_panel["x"] = 100
        first_panel["y"] = 120
        child["x"] = 20
        child["y"] = 30
        second_panel["x"] = 300
        second_panel["y"] = 250
        self.state.select(str(child["id"]))

        self.state.reparent_selected_gui_elements(
            str(second_panel["id"]),
            "inside",
        )

        self.assertEqual(first_panel["children"], [])
        self.assertEqual(second_panel["children"], [child])
        self.assertEqual((child["x"], child["y"]), (-180.0, -100.0))
        self.assertEqual(
            self.state.gui_element_screen_position(child),
            (120.0, 150.0),
        )
        self.assertEqual(layer["elements"], [first_panel, second_panel])

    def test_new_object_is_added_below_selected_object(self) -> None:
        parent = self.state.add_object("empty")
        child = self.state.add_object("rectangle")

        self.assertEqual(parent["children"], [child])
        self.assertNotIn(child, self.state.document.objects)
        self.assertIs(self.state.selected, child)

    def test_save_and_open_round_trip(self) -> None:
        self.state.add_object("rectangle", x=10, y=20)
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "scene.scene.json"

            self.state.save(filename)
            self.state.new()
            self.state.open(filename)

        self.assertFalse(self.state.dirty)
        self.assertEqual(len(self.state.document.objects), 1)
        self.assertEqual(position(self.state.document.objects[0]), (10, 20))

    def test_layers_control_default_object_placement(self) -> None:
        layer = self.state.add_layer("Foreground")
        record = self.state.add_object("circle")

        objects = layer["objects"]
        assert isinstance(objects, list)
        self.assertIn(record, objects)
        self.assertEqual(self.state.selected_layer_id, layer["id"])

        self.state.make_selected_layer_default()

        self.assertTrue(layer["default"])
        self.assertFalse(self.state.document.layers[0]["default"])

    def test_moving_nested_object_to_layer_detaches_it_as_root(self) -> None:
        parent = new_object_record()
        child = new_object_record("text")
        parent["children"] = [child]
        self.state.document.objects.append(parent)
        layer = self.state.add_layer("Foreground")
        identifier = child["id"]
        assert isinstance(identifier, str)
        self.state.select(identifier)

        layer_id = layer["id"]
        assert isinstance(layer_id, str)
        self.state.move_selected_to_layer(layer_id)

        self.assertEqual(parent["children"], [])
        objects = layer["objects"]
        assert isinstance(objects, list)
        self.assertIn(child, objects)

    def test_tile_layer_paints_compact_chunks_and_erases_them(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 2,
            "tile_count": 4,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }
        layer["cell_size"] = [16, 16]
        layer["chunk_size"] = 4

        self.assertEqual(self.state.paint_tile(20, 36, 3), (1, 2))
        self.assertEqual(self.state.tile_at(1, 2).tile_id, 3)  # type: ignore[union-attr]
        self.assertEqual(self.state.paint_tile(-1, -1, 2), (-1, -1))
        self.assertEqual(self.state.tile_at(-1, -1).tile_id, 2)  # type: ignore[union-attr]
        self.assertEqual(self.state.paint_tile(64, 0, 1), (4, 0))
        chunks = layer["chunks"]
        assert isinstance(chunks, list)
        self.assertEqual(len(chunks), 3)

        self.state.paint_tile(20, 36, None)

        self.assertIsNone(self.state.tile_at(1, 2))
        self.assertEqual(len(chunks), 2)

    def test_invalid_tile_id_does_not_modify_chunks(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 2,
            "tile_count": 4,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }

        with self.assertRaisesRegex(ValueError, "between 0 and 3"):
            self.state.paint_tile(0, 0, 4)

        self.assertEqual(layer["chunks"], [])

    def test_tile_rectangle_selects_only_occupied_cells_without_editing(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        self.state.add_color_tileset()
        layer["cell_size"] = [16, 8]
        layer["offset"] = [10, -20]
        for position in ((-1, -1), (0, 0), (1, 1), (20, 20)):
            self.state._set_tile_cell(layer, *position, TileCell(0))
        self.state.mark_changed("Seed tiles")
        revision = self.state.revision
        chunks = deepcopy(layer["chunks"])

        self.state.select_tile_rectangle((2, 2), (-2, -2))

        self.assertEqual(
            set(self.state.selected_tile_cells),
            {(-1, -1), (0, 0), (1, 1)},
        )
        self.assertEqual(self.state.revision, revision)
        self.assertEqual(layer["chunks"], chunks)
        self.assertEqual(self.state.world_to_tile_cell(9, -21), (-1, -1))
        self.state.select_tile_rectangle((10, 10), (10, 10))
        self.assertEqual(self.state.selected_tile_cells, {})

    def test_tile_selection_reveals_tileset_without_replacing_stamp(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        first = {
            "id": "base",
            "name": "Base",
            "first_gid": 0,
            "colors": [[255, 255, 255, 255]],
            "tile_count": 1,
            "properties": {},
            "tiles": [],
        }
        second = {
            **first,
            "id": "details",
            "name": "Details",
            "first_gid": 8,
            "colors": [[255, 0, 0, 255], [0, 255, 0, 255]],
            "tile_count": 2,
        }
        layer["tilesets"] = [first, second]
        layer["tileset"] = second
        self.state.select_tileset("details")
        self.state.select_palette_tiles([8, 9], 2, 1)
        self.state._set_tile_cell(layer, 0, 0, TileCell(0))

        self.state.select_tile_rectangle((0, 0), (0, 0))

        self.assertEqual(self.state.selected_placed_tile_ids, (0,))
        self.assertIs(self.state.active_tileset(), first)
        self.assertEqual(self.state.palette_tile_ids, [8, 9])
        self.assertEqual(self.state.palette_selection_size, (2, 1))

    def test_command_palette_selection_builds_sparse_stamp(self) -> None:
        self.state.add_tile_layer("Ground")
        self.state.add_color_tileset()
        for color in (
            [255, 0, 0, 255],
            [0, 255, 0, 255],
            [0, 0, 255, 255],
        ):
            self.state.add_color_tile(color)
        self.state.select_palette_tiles([0], 1, 1)

        self.state.toggle_palette_tile(3, columns=2)

        self.assertEqual(self.state.palette_tile_ids, [0, 3])
        self.assertEqual(
            self.state.palette_stamp,
            (
                [
                    TileCell(0),
                    None,
                    None,
                    TileCell(3),
                ],
                (2, 2),
            ),
        )
        self.state.toggle_palette_tile(0, columns=2)
        self.assertEqual(
            self.state.palette_stamp,
            ([TileCell(3)], (1, 1)),
        )
        self.state.toggle_palette_tile(3, columns=2)
        self.assertFalse(self.state.has_tile_stamp)

    def test_command_viewport_selection_toggles_individual_tiles(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        self.state.add_color_tileset()
        self.state._set_tile_cell(layer, 0, 0, TileCell(0))
        self.state._set_tile_cell(layer, 2, 0, TileCell(0))
        self.state.select_tile_rectangle((0, 0), (0, 0))

        self.assertTrue(self.state.toggle_tile_selection_cell(2, 0))
        self.assertEqual(
            set(self.state.selected_tile_cells),
            {(0, 0), (2, 0)},
        )
        self.assertTrue(self.state.toggle_tile_selection_cell(0, 0))
        self.assertEqual(
            set(self.state.selected_tile_cells),
            {(2, 0)},
        )
        self.assertFalse(self.state.toggle_tile_selection_cell(1, 0))
        self.assertEqual(
            set(self.state.selected_tile_cells),
            {(2, 0)},
        )

    def test_tile_move_preserves_flags_overlap_holes_and_history(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        self.state.add_color_tileset()
        self.state.add_color_tile([255, 0, 0, 255])
        layer["chunk_size"] = 2
        first = TileCell(0, TileFlags.FLIP_HORIZONTAL)
        second = TileCell(1, TileFlags.FLIP_VERTICAL | TileFlags.FLIP_DIAGONAL)
        for position, cell in (
            ((-1, 0), first),
            ((0, 0), second),
            ((1, 0), first),
            ((1, 1), second),
        ):
            self.state._set_tile_cell(layer, *position, cell)
        self.state.mark_changed("Seed tiles")
        self.state.select_tile_rectangle((-1, 0), (0, 1))
        original_chunks = deepcopy(layer["chunks"])
        revision = self.state.revision

        self.assertTrue(self.state.move_selected_tiles(1, 0))

        self.assertIsNone(self.state.tile_at(-1, 0))
        self.assertEqual(self.state.tile_at(0, 0), first)
        self.assertEqual(self.state.tile_at(1, 0), second)
        self.assertEqual(self.state.tile_at(1, 1), second)
        self.assertEqual(set(self.state.selected_tile_cells), {(0, 0), (1, 0)})
        self.assertEqual(self.state.revision, revision + 1)
        self.assertEqual(self.state.undo_label, "Move tiles")
        moved_chunks = deepcopy(layer["chunks"])
        self.assertTrue(self.state.undo())
        restored_layer = self.state.selected_layer
        assert restored_layer is not None
        self.assertEqual(restored_layer["chunks"], original_chunks)
        self.assertEqual(set(self.state.selected_tile_cells), {(-1, 0), (0, 0)})
        self.assertTrue(self.state.redo())
        restored_layer = self.state.selected_layer
        assert restored_layer is not None
        self.assertEqual(restored_layer["chunks"], moved_chunks)
        self.assertEqual(set(self.state.selected_tile_cells), {(0, 0), (1, 0)})

        # A second move is its own undo step, even when performed immediately.
        self.assertTrue(self.state.move_selected_tiles(-4, -3))
        self.assertEqual(self.state.tile_at(-4, -3), first)
        self.assertEqual(self.state.tile_at(-3, -3), second)
        self.assertTrue(self.state.undo())
        self.assertEqual(self.state.tile_at(0, 0), first)
        self.assertEqual(self.state.tile_at(1, 0), second)

    def test_delete_selected_tiles_preserves_other_cells_and_undo_selection(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        self.state.add_color_tileset()
        layer["chunk_size"] = 2
        cell = TileCell(0, TileFlags.FLIP_HORIZONTAL)
        for position in ((-3, -1), (0, 0), (20, 20)):
            self.state._set_tile_cell(layer, *position, cell)
        self.state.mark_changed("Seed tiles")
        self.state.select_tile_rectangle((-3, -1), (0, 0))
        chunks = deepcopy(layer["chunks"])
        revision = self.state.revision
        self.assertTrue(self.state.delete_selected())
        self.assertIsNone(self.state.tile_at(-3, -1))
        self.assertIsNone(self.state.tile_at(0, 0))
        self.assertEqual(self.state.tile_at(20, 20), cell)
        self.assertEqual(self.state.selected_tile_cells, {})
        self.assertEqual(self.state.revision, revision + 1)
        self.assertEqual(self.state.undo_label, "Delete tiles")
        self.assertFalse(self.state.delete_selected())
        self.assertEqual(self.state.revision, revision + 1)
        self.assertTrue(self.state.undo())
        restored = self.state.selected_layer
        assert restored is not None
        self.assertEqual(restored["chunks"], chunks)
        self.assertEqual(set(self.state.selected_tile_cells), {(-3, -1), (0, 0)})
        self.assertTrue(self.state.redo())
        self.assertEqual(self.state.selected_tile_cells, {})
        self.assertIsNone(self.state.tile_at(0, 0))

    def test_tile_selection_is_scoped_to_layer_and_scene(self) -> None:
        first = self.state.add_tile_layer("First")
        self.state.add_color_tileset()
        self.state.paint_tile(1, 1, 0)
        self.state.select_tile_rectangle((0, 0), (0, 0))
        second = self.state.add_tile_layer("Second")
        self.assertEqual(self.state.selected_tile_cells, {})
        self.state.select_layer(str(first["id"]))
        self.state.select_tile_rectangle((0, 0), (0, 0))
        self.state.select_layer(str(second["id"]))
        self.state.select_layer(str(first["id"]))
        self.assertEqual(self.state.selected_tile_cells, {})
        self.state.select_tile_rectangle((0, 0), (0, 0))
        self.state.new()
        self.assertEqual(self.state.selected_tile_cells, {})

    def test_empty_and_zero_tile_moves_do_not_create_history(self) -> None:
        self.state.add_tile_layer("Ground")
        self.state.add_color_tileset()
        self.state.paint_tile(1, 1, 0)
        revision = self.state.revision
        self.assertFalse(self.state.move_selected_tiles(1, 0))
        self.state.select_tile_rectangle((0, 0), (0, 0))
        self.assertFalse(self.state.move_selected_tiles(0, 0))
        self.assertEqual(self.state.revision, revision)
        with self.assertRaisesRegex(ValueError, "whole cells"):
            self.state.move_selected_tiles(True, 0)

    def test_moved_tiles_persist_when_scene_is_saved_and_reopened(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        self.state.add_color_tileset()
        cell = TileCell(0, TileFlags.FLIP_HORIZONTAL | TileFlags.FLIP_DIAGONAL)
        self.state._set_tile_cell(layer, 0, 0, cell)
        self.state.mark_changed("Seed tiles")
        self.state.select_tile_rectangle((0, 0), (0, 0))
        self.state.move_selected_tiles(-20, 17)
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "moved.scene.json"
            self.state.save(filename)
            restored = EditorState(filename=filename)
            restored.select_layer(str(layer["id"]))
            self.assertIsNone(restored.tile_at(0, 0))
            self.assertEqual(restored.tile_at(-20, 17), cell)
            self.assertEqual(restored.selected_tile_cells, {})

    def test_failed_tile_move_does_not_erase_sources_or_change_selection(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        self.state.add_color_tileset()
        self.state.paint_tile(1, 1, 0)
        chunks = layer["chunks"]
        assert isinstance(chunks, list)
        chunks.append({
            "position": [1, 0],
            "tiles": [],
        })
        self.state.mark_changed("Seed invalid destination")
        self.state.select_tile_rectangle((0, 0), (0, 0))
        before = deepcopy(layer["chunks"])
        revision = self.state.revision
        with self.assertRaisesRegex(SceneFormatError, "invalid size"):
            self.state.move_selected_tiles(16, 0)
        self.assertEqual(layer["chunks"], before)
        self.assertEqual(self.state.tile_at(0, 0), TileCell(0))
        self.assertEqual(set(self.state.selected_tile_cells), {(0, 0)})
        self.assertEqual(self.state.revision, revision)

    def test_tile_stamps_fill_random_and_tileset_removal(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        first = {
            "id": "base",
            "name": "Base",
            "first_gid": 0,
            "image": "base.png",
            "tile_size": [16, 16],
            "columns": 2,
            "tile_count": 4,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }
        second = {
            **first,
            "id": "details",
            "name": "Details",
            "first_gid": 8,
            "image": "details.png",
        }
        layer["tilesets"] = [first, second]
        layer["tileset"] = first
        layer["cell_size"] = [16, 16]
        self.state.select_tileset("details")
        top_left = TilePalettePanel._atlas_tile_at(
            1,
            1,
            16,
            16,
            2,
            4,
            0,
            0,
        )
        bottom_right = TilePalettePanel._atlas_tile_at(
            17,
            17,
            16,
            16,
            2,
            4,
            0,
            0,
        )
        assert top_left is not None
        assert bottom_right is not None
        selection = TilePalettePanel._palette_selection_rectangle(
            top_left,
            bottom_right,
            columns=2,
            tile_count=4,
        )
        assert selection is not None
        local_ids, width, height = selection
        self.assertEqual(local_ids, [0, 1, 2, 3])
        self.state.select_palette_tiles(
            [8 + local_id for local_id in local_ids],
            width,
            height,
        )

        self.state.paint_stamp(1, 1)

        self.assertEqual(self.state.tile_at(0, 0), TileCell(8))
        self.assertEqual(self.state.tile_at(1, 0), TileCell(9))
        self.assertEqual(self.state.tile_at(0, -1), TileCell(10))
        self.assertEqual(self.state.tile_at(1, -1), TileCell(11))
        self.assertTrue(self.state.undo())
        self.assertTrue(
            all(
                self.state.tile_at(column, row) is None
                for column, row in (
                    (0, 0),
                    (1, 0),
                    (0, -1),
                    (1, -1),
                )
            )
        )
        self.assertTrue(self.state.redo())
        layer = self.state.selected_layer
        assert layer is not None

        self.state.fill_tile_rectangle((2, 0), (5, 1))

        self.assertEqual(
            [self.state.tile_at(column, 0).tile_id for column in range(2, 6)],  # type: ignore[union-attr]
            [10, 11, 10, 11],
        )
        self.assertEqual(
            [self.state.tile_at(column, 1).tile_id for column in range(2, 6)],  # type: ignore[union-attr]
            [8, 9, 8, 9],
        )
        self.state.fill_tile_rectangle((2, 2), (3, 3))
        self.assertEqual(
            [self.state.tile_at(column, 2).tile_id for column in (2, 3)],  # type: ignore[union-attr]
            [10, 11],
        )
        self.assertEqual(
            [self.state.tile_at(column, 3).tile_id for column in (2, 3)],  # type: ignore[union-attr]
            [8, 9],
        )
        self.state.select_palette_tiles([8, 9], 2, 1)
        random_paint = self.state.fill_tile_rectangle(
            (0, 3),
            (7, 3),
            randomize=True,
            random_source=Random(2),
        )
        self.assertTrue(
            {tile_id for _, _, tile_id in random_paint} <= {8, 9}
        )
        usage = self.state.tileset_usage_count("details")
        self.assertGreater(usage, 0)
        with self.assertRaisesRegex(ValueError, "used by"):
            self.state.remove_tileset("details")
        self.assertEqual(
            self.state.remove_tileset("details", erase_used=True),
            usage,
        )
        self.assertEqual(
            [tileset["id"] for tileset in layer["tilesets"]],  # type: ignore[index]
            ["base"],
        )
        self.assertEqual(self.state.tileset_usage_count("base"), 0)

    def test_tile_matrix_rotates_clockwise_as_one_oriented_stamp(
        self,
    ) -> None:
        cells, width, height = transform_tile_matrix(
            [TileCell(tile_id) for tile_id in (1, 2, 3, 4)],
            2,
            2,
            rotation=1,
        )

        clockwise = (
            TileFlags.FLIP_DIAGONAL | TileFlags.FLIP_HORIZONTAL
        )
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(
            cells,
            [
                TileCell(3, clockwise),
                TileCell(1, clockwise),
                TileCell(4, clockwise),
                TileCell(2, clockwise),
            ],
        )
        self.assertEqual(
            transform_tile_flags(clockwise, "rotate_clockwise"),
            TileFlags.FLIP_HORIZONTAL | TileFlags.FLIP_VERTICAL,
        )
        coordinates = tuple(float(value) for value in range(12))
        points = [
            coordinates[index : index + 3]
            for index in range(0, 12, 3)
        ]
        self.assertEqual(
            TileLayer._texture_coordinates(coordinates, clockwise),
            tuple((*points[1], *points[2], *points[3], *points[0])),
        )

    def test_palette_transform_rotates_stamp_and_resets_on_selection(
        self,
    ) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 4,
            "tile_count": 8,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }
        self.state.select_palette_tiles([0, 1], 2, 1)

        self.assertFalse(self.state.rotate_tiles_clockwise())
        self.state.paint_stamp(1, 1)

        clockwise = (
            TileFlags.FLIP_DIAGONAL | TileFlags.FLIP_HORIZONTAL
        )
        self.assertEqual(self.state.tile_at(0, 0), TileCell(0, clockwise))
        self.assertEqual(self.state.tile_at(0, -1), TileCell(1, clockwise))
        self.assertEqual(self.state.palette_stamp[1], (1, 2))

        self.state.select_palette_tiles([2], 1, 1)

        self.assertEqual(self.state.tile_rotation, 0)
        self.assertFalse(self.state.tile_flip_horizontal)
        self.assertFalse(self.state.tile_flip_vertical)

    def test_viewport_sampled_stamp_preserves_flags_and_holes(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 4,
            "tile_count": 4,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }
        horizontal = TileFlags.FLIP_HORIZONTAL
        vertical = TileFlags.FLIP_VERTICAL
        diagonal = TileFlags.FLIP_DIAGONAL
        self.state._set_tile_cell(layer, 0, 1, TileCell(0, horizontal))
        self.state._set_tile_cell(layer, 0, 0, TileCell(1, vertical))
        self.state._set_tile_cell(layer, 1, 0, TileCell(2, diagonal))

        self.assertTrue(
            self.state.sample_tile_rectangle((0, 0), (1, 1))
        )
        self.assertEqual(
            self.state.palette_stamp,
            (
                [
                    TileCell(0, horizontal),
                    None,
                    TileCell(1, vertical),
                    TileCell(2, diagonal),
                ],
                (2, 2),
            ),
        )
        self.assertEqual(self.state.sampled_tile_size, (2, 2))
        sampled = self.state.palette_stamp
        self.assertFalse(
            self.state.sample_tile_rectangle((10, 10), (11, 11))
        )
        self.assertEqual(self.state.palette_stamp, sampled)

        self.state._set_tile_cell(layer, 5, 4, TileCell(3))
        self.state.paint_stamp(65, 65)

        self.assertEqual(
            self.state.tile_at(4, 4), TileCell(0, horizontal)
        )
        self.assertEqual(self.state.tile_at(5, 4), TileCell(3))
        self.assertEqual(
            self.state.tile_at(4, 3), TileCell(1, vertical)
        )
        self.assertEqual(
            self.state.tile_at(5, 3), TileCell(2, diagonal)
        )

        self.assertFalse(self.state.rotate_tiles_clockwise())
        expected, width, height = transform_tile_matrix(
            [
                TileCell(0, horizontal),
                None,
                TileCell(1, vertical),
                TileCell(2, diagonal),
            ],
            2,
            2,
            rotation=1,
        )
        self.assertEqual(self.state.palette_stamp, (expected, (width, height)))

        self.state.select_palette_tiles([3], 1, 1)
        self.assertIsNone(self.state.sampled_tile_size)
        self.assertEqual(self.state.palette_stamp, ([TileCell(3)], (1, 1)))

    def test_selected_tiles_flip_as_group_toggle_and_remain_selected(
        self,
    ) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 2,
            "tile_count": 2,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }
        self.state.paint_tile(1, 1, 0)
        self.state.paint_tile(17, 1, 1)
        self.state.select_tile_rectangle((0, 0), (1, 0))

        self.assertTrue(self.state.toggle_tile_flip("horizontal"))

        self.assertEqual(
            self.state.tile_at(0, 0),
            TileCell(1, TileFlags.FLIP_HORIZONTAL),
        )
        self.assertEqual(
            self.state.tile_at(1, 0),
            TileCell(0, TileFlags.FLIP_HORIZONTAL),
        )
        self.assertEqual(
            set(self.state.selected_tile_cells),
            {(0, 0), (1, 0)},
        )

        self.assertTrue(self.state.toggle_tile_flip("horizontal"))

        self.assertEqual(self.state.tile_at(0, 0), TileCell(0))
        self.assertEqual(self.state.tile_at(1, 0), TileCell(1))
        self.assertEqual(self.state.undo_label, "Flip tiles horizontal")
        self.assertTrue(self.state.undo())
        self.assertEqual(
            self.state.tile_at(0, 0),
            TileCell(1, TileFlags.FLIP_HORIZONTAL),
        )
        self.assertEqual(self.state.tile_rotation, 0)
        self.assertFalse(self.state.tile_flip_horizontal)

    def test_selected_tiles_rotate_clockwise_as_one_non_square_group(
        self,
    ) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["cell_size"] = [16, 16]
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 2,
            "tile_count": 2,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }
        self.state.paint_tile(1, 1, 0)
        self.state.paint_tile(17, 1, 1)
        self.state.select_tile_rectangle((0, 0), (1, 0))

        self.assertTrue(self.state.rotate_tiles_clockwise())

        clockwise = (
            TileFlags.FLIP_DIAGONAL | TileFlags.FLIP_HORIZONTAL
        )
        self.assertEqual(self.state.tile_at(0, 1), TileCell(0, clockwise))
        self.assertEqual(self.state.tile_at(0, 0), TileCell(1, clockwise))
        self.assertIsNone(self.state.tile_at(1, 0))
        self.assertEqual(
            set(self.state.selected_tile_cells),
            {(0, 0), (0, 1)},
        )
        self.assertEqual(self.state.tile_rotation, 1)

        self.assertTrue(self.state.move_world_selection(2, 0))
        self.assertEqual(
            set(self.state.selected_tile_cells),
            {(2, 0), (2, 1)},
        )
        self.assertEqual(self.state.tile_rotation, 0)

        self.state.select_tile_rectangle((2, 0), (2, 0))

        self.assertEqual(self.state.tile_rotation, 0)
        self.assertFalse(self.state.tile_flip_horizontal)
        self.assertFalse(self.state.tile_flip_vertical)

    def test_tile_layer_cannot_be_default_or_receive_objects(self) -> None:
        tile_layer = self.state.add_tile_layer("Ground")

        with self.assertRaisesRegex(ValueError, "object layer"):
            self.state.make_selected_layer_default()

        record = self.state.add_object("rectangle")

        self.assertIn(record, self.state.document.objects)
        tile_objects = tile_layer.get("objects", [])
        assert isinstance(tile_objects, list)
        self.assertNotIn(record, tile_objects)

    def test_tile_definition_separates_builtin_and_custom_values(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 2,
            "tile_count": 4,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }

        self.state.set_tile_definition_value(1, "name", "grass")
        self.state.set_tile_definition_value(
            1,
            "navigation",
            {"walkable": True, "cost": 1.0},
        )
        self.state.set_tile_definition_value(
            1,
            "properties",
            {"footstep_sound": "grass"},
        )

        definition = self.state.tile_definition(1)
        assert definition is not None
        self.assertEqual(definition["name"], "grass")
        self.assertEqual(
            definition["navigation"],
            {"walkable": True, "cost": 1.0},
        )
        self.assertEqual(
            definition["properties"],
            {"footstep_sound": "grass"},
        )

    def test_generate_collision_marks_painted_tiles_by_default(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["collision_generation"] = "rectangle"
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 2,
            "tile_count": 4,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }
        self.state.paint_tile(1, 1, 1)

        self.state.set_tile_layer_value(
            "collision_enabled",
            True,
        )

        definition = self.state.tile_definition(1)
        assert definition is not None
        self.assertIsInstance(definition["collision"], dict)
        self.state.set_tile_definition_value(1, "collision", None)
        self.state.paint_tile(17, 1, 1)
        self.assertIsInstance(definition["collision"], dict)

    def test_generation_targets_every_tile_in_active_tileset(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["collision_generation"] = "rectangle"
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 2,
            "tile_count": 4,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }

        collision_count = self.state.generate_tile_collisions(
            replace=True,
        )
        navigation_count = self.state.generate_tile_navigation()

        self.assertEqual(collision_count, 4)
        self.assertEqual(navigation_count, 4)
        for tile_id in range(4):
            definition = self.state.tile_definition(
                tile_id,
                create=False,
            )
            assert definition is not None
            self.assertIsInstance(definition["collision"], dict)
            self.assertEqual(
                definition["navigation"],
                {"walkable": True, "cost": 1.0},
            )

    def test_alpha_collision_generation_classifies_tile_shapes(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=self.state.asset_root
        ) as directory:
            root = Path(directory)
            image_path = root / "collision-tiles.png"
            solid_pixels = {
                (4 + x, y)
                for y in range(2)
                for x in range(4)
            }
            solid_pixels.update(
                {
                    (8, 0),
                    (9, 0),
                    (8, 1),
                }
            )
            solid_pixels.update(
                {
                    (12, 0),
                    (15, 3),
                }
            )
            pixels = bytearray()
            for y in range(4):
                for x in range(16):
                    alpha = 255 if (x, y) in solid_pixels else 0
                    pixels.extend((255, 255, 255, alpha))
            pyglet.image.ImageData(
                16,
                4,
                "RGBA",
                bytes(pixels),
                pitch=16 * 4,
            ).save(str(image_path))
            layer = self.state.add_tile_layer("Ground")
            layer["cell_size"] = [16, 16]
            layer["tileset"] = {
                "image": str(
                    image_path.relative_to(self.state.asset_root)
                ),
                "tile_size": [4, 4],
                "columns": 4,
                "tile_count": 4,
                "margin": 0,
                "spacing": 0,
                "properties": {},
                "tiles": [],
            }

            generated = self.state.generate_tile_collisions(
                [0, 1, 2, 3],
                replace=True,
            )

        self.assertEqual(generated, 3)
        transparent = self.state.tile_definition(0, create=False)
        self.assertIsNotNone(transparent)
        assert transparent is not None
        self.assertNotIn("collision", transparent)
        rectangle = self.state.tile_definition(1, create=False)
        assert rectangle is not None
        self.assertEqual(
            rectangle["collision"],
            {
                "type": "rectangle",
                "x": 0.0,
                "y": 0.0,
                "width": 16.0,
                "height": 8.0,
                "generated_from_alpha": True,
                "alpha_threshold": 255,
            },
        )
        polygon = self.state.tile_definition(2, create=False)
        assert polygon is not None
        polygon_collision = polygon["collision"]
        assert isinstance(polygon_collision, dict)
        self.assertEqual(polygon_collision["type"], "polygon")
        polygons = polygon_collision["polygons"]
        assert isinstance(polygons, list)
        self.assertEqual(len(polygons), 1)
        islands = self.state.tile_definition(3, create=False)
        assert islands is not None
        island_collision = islands["collision"]
        assert isinstance(island_collision, dict)
        island_polygons = island_collision["polygons"]
        assert isinstance(island_polygons, list)
        self.assertEqual(len(island_polygons), 2)

    def test_alpha_collision_generation_can_use_transparent_pixels(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            dir=self.state.asset_root
        ) as directory:
            root = Path(directory)
            image_path = root / "transparent-tile.png"
            pyglet.image.ImageData(
                2,
                2,
                "RGBA",
                bytes((255, 255, 255, 0)) * 4,
                pitch=2 * 4,
            ).save(str(image_path))
            layer = self.state.add_tile_layer("Ground")
            layer["cell_size"] = [16, 16]
            layer["tileset"] = {
                "image": str(
                    image_path.relative_to(self.state.asset_root)
                ),
                "tile_size": [2, 2],
                "columns": 1,
                "tile_count": 1,
                "margin": 0,
                "spacing": 0,
                "properties": {},
                "tiles": [],
            }
            self.state.set_tile_layer_value(
                "collision_alpha_inverted",
                True,
            )

            generated = self.state.generate_tile_collisions(
                [0],
                replace=True,
            )

        self.assertEqual(generated, 1)
        transparent = self.state.tile_definition(0, create=False)
        assert transparent is not None
        self.assertEqual(
            transparent["collision"],
            {
                "type": "rectangle",
                "x": 0,
                "y": 0,
                "width": None,
                "height": None,
                "generated_from_alpha": True,
                "alpha_threshold": 255,
            },
        )

    def test_batch_tile_definition_edit_is_atomic(self) -> None:
        layer = self.state.add_tile_layer("Ground")
        layer["tileset"] = {
            "image": "tiles.png",
            "tile_size": [16, 16],
            "columns": 4,
            "tile_count": 4,
            "margin": 0,
            "spacing": 0,
            "properties": {},
            "tiles": [],
        }

        self.state.set_tile_definitions_value(
            [1, 2, 3],
            "tags",
            ["ground", "solid"],
        )

        for tile_id in (1, 2, 3):
            definition = self.state.tile_definition(tile_id)
            assert definition is not None
            self.assertEqual(definition["tags"], ["ground", "solid"])
        self.assertEqual(
            self.state.undo_label,
            "Changed tags for 3 tiles",
        )

    def test_batch_color_edit_updates_selected_tiles(self) -> None:
        self.state.add_tile_layer("Ground")
        palette = self.state.add_color_tileset(
            "Colors",
            [20, 30, 40, 255],
        )
        second_id = self.state.add_color_tile(
            [80, 90, 100, 255]
        )
        first_id = palette["first_gid"]
        assert isinstance(first_id, int)

        self.state.set_tile_colors(
            [first_id, second_id],
            [120, 130, 140, 255],
        )

        self.assertEqual(
            self.state.tile_color(first_id),
            [120, 130, 140, 255],
        )
        self.assertEqual(
            self.state.tile_color(second_id),
            [120, 130, 140, 255],
        )
        self.assertEqual(
            self.state.undo_label,
            "Changed color for 2 tiles",
        )

    def test_gui_layer_adds_selects_moves_and_deletes_elements(self) -> None:
        layer = self.state.add_ui_view("Test HUD")
        button = self.state.add_gui_element("button")

        self.assertEqual(layer["type"], "ui")
        self.assertIs(self.state.selected_gui_element, button)
        identifier = button["id"]
        assert isinstance(identifier, str)
        self.assertEqual(
            self.state.gui_element_at(320, 280),
            identifier,
        )

        self.state.move_selected_gui_element(111, 77)

        self.assertEqual((button["x"], button["y"]), (96.0, 64.0))
        self.assertTrue(self.state.delete_selected())
        self.assertEqual(layer["elements"], [])

    def test_selected_free_gui_elements_move_as_a_group(self) -> None:
        layer = self.state.add_ui_view("Test HUD")
        first = self.state.add_gui_element("button")
        self.state.select(None)
        second = self.state.add_gui_element("button")
        first_id = str(first["id"])
        second_id = str(second["id"])
        self.state.select(first_id)
        self.state.select(second_id, additive=True)
        self.state.set_snap_enabled(False)
        first_x, first_y = first["x"], first["y"]
        second_x, second_y = second["x"], second["y"]
        assert isinstance(first_x, (int, float))
        assert isinstance(first_y, (int, float))
        assert isinstance(second_x, (int, float))
        assert isinstance(second_y, (int, float))
        first_before = (float(first_x), float(first_y))
        second_before = (float(second_x), float(second_y))

        self.state.move_selected_gui_elements_by(10, -5)

        self.assertEqual(
            (first["x"], first["y"]),
            (first_before[0] + 10, first_before[1] - 5),
        )
        self.assertEqual(
            (second["x"], second["y"]),
            (second_before[0] + 10, second_before[1] - 5),
        )
        self.assertEqual(
            layer["elements"],
            [first, second],
        )

    def test_gui_elements_are_added_inside_selected_panel(self) -> None:
        layer = self.state.add_ui_view("Test HUD")
        panel = self.state.add_gui_element("panel")
        label = self.state.add_gui_element("label")
        button = self.state.add_gui_element("button")

        children = panel["children"]
        assert isinstance(children, list)
        self.assertEqual(children, [label, button])
        self.assertEqual(layer["elements"], [panel])
        self.assertIs(self.state.gui_element_parent(label), panel)
        self.assertEqual(
            self.state.gui_element_screen_position(label),
            (326.0, 292.0),
        )
        label_id = label["id"]
        assert isinstance(label_id, str)
        self.assertEqual(
            self.state.gui_element_at(330, 300),
            label_id,
        )

        self.state.select(label_id)
        self.assertTrue(self.state.delete_selected())
        self.assertEqual(children, [button])

    def test_remaining_gui_control_templates_are_authorable(self) -> None:
        layer = self.state.add_ui_view("Controls")
        toggle = self.state.add_gui_element("toggle")
        slider = self.state.add_gui_element("slider")
        text_input = self.state.add_gui_element("text_input")

        self.assertEqual(layer["elements"], [toggle, slider, text_input])
        self.assertEqual(toggle["value"], False)
        self.assertEqual(
            (
                slider["minimum"],
                slider["maximum"],
                slider["value"],
            ),
            (0, 100, 50),
        )
        self.assertEqual(text_input["text"], "")
        self.assertEqual(
            text_input["caret_color"],
            [0, 0, 0, 255],
        )

    def test_gui_layout_templates_control_child_positioning(self) -> None:
        layer = self.state.add_ui_view("Menu")
        column = self.state.add_gui_element("vbox")
        button = self.state.add_gui_element("button")
        label = self.state.add_gui_element("label")

        children = column["children"]
        assert isinstance(children, list)
        self.assertEqual(children, [button, label])
        self.assertFalse(
            self.state.gui_element_is_free_positioned(button)
        )
        self.assertEqual((button["x"], button["y"]), (0, 0))

        layer_id = layer["id"]
        assert isinstance(layer_id, str)
        self.state.select_layer(layer_id)
        anchor = self.state.add_gui_element("anchor")
        self.state.add_gui_element("button")
        with self.assertRaisesRegex(ValueError, "only one child"):
            self.state.add_gui_element("label")
        self.assertEqual(anchor["horizontal"], "center")


if __name__ == "__main__":
    unittest.main()
