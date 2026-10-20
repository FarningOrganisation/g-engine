import json
import shutil
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pyglet

from g import (
    AnchorLayout,
    Area,
    ButtonStyle,
    Collider,
    Button,
    CanvasLayout,
    ColliderMode,
    Component,
    Game,
    GameObject,
    GridLayout,
    HBox,
    Image,
    Label,
    Panel,
    PlatformerMotor,
    PhysicsBody,
    PresentationSettings,
    RectRenderer,
    SceneDocument,
    SceneFormatError,
    SceneSerializer,
    SceneTypeRegistry,
    Script,
    ScrollView,
    Slider,
    SpriteRenderer,
    StackLayout,
    TextInput,
    TextRenderer,
    Toggle,
    UIView,
    VBox,
    Vec2,
    requires_components,
    create_area_box,
    create_area_circle,
    create_rect,
    create_sprite,
    create_text,
)
from g.persistence import _register_builtins


class TrackingScript(Script):
    speed: float = 25
    target: GameObject | None = None

    def __init__(self) -> None:
        super().__init__()
        self.ready_count = 0
        self.updates: list[float] = []

    def on_ready(self) -> None:
        self.ready_count += 1

    def update(self, delta_time: float) -> None:
        self.updates.append(delta_time)


class NamedObject(GameObject):
    label: str = "object"


class SerializedRequirement(Component):
    pass


@requires_components(SerializedRequirement)
class SerializedDependent(Component):
    pass


@requires_components(SerializedRequirement, auto_add=True)
class AutoSerializedDependent(Component):
    pass


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SceneTypeRegistry()
        _register_builtins(self.registry)
        self.registry.register_component(
            "tests.tracking_script",
            TrackingScript,
            fields=("speed", "target"),
        )
        self.registry.register_object("tests.named_object", NamedObject)
        self.registry.register_component(
            "tests.serialized_requirement",
            SerializedRequirement,
        )
        self.registry.register_component(
            "tests.serialized_dependent",
            SerializedDependent,
        )
        self.registry.register_component(
            "tests.auto_serialized_dependent",
            AutoSerializedDependent,
        )
        self.game = Game(320, 180)

    def test_collider_offsets_round_trip_and_default_in_older_scenes(self) -> None:
        obj = self.game.add(GameObject(20, 30))
        obj.add_component(Collider(width=8, height=12, offset_x=3, offset_y=-7))
        obj.add_component(Collider(shape="circle", radius=4, offset_x=-2, offset_y=9))
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(self.game.scenes.current, asset_root=".")
        restored = serializer.restore(self.game, document, asset_root=".")
        box, circle = [
            component for component in restored.objects[0].components
            if isinstance(component, Collider)
        ]
        self.assertEqual((box.offset_x, box.offset_y), (3, -7))
        self.assertEqual((circle.offset_x, circle.offset_y), (-2, 9))
        self.assertEqual((box.shape, circle.shape), ("box", "circle"))
        components = document.objects[0]["components"]
        assert isinstance(components, list)
        for component in components:
            assert isinstance(component, dict)
            properties = component["properties"]
            assert isinstance(properties, dict)
            properties.pop("offset_x")
            properties.pop("offset_y")
        restored = serializer.restore(self.game, document, asset_root=".")
        for collider in restored.objects[0].components:
            assert isinstance(collider, Collider)
            self.assertEqual((collider.offset_x, collider.offset_y), (0, 0))

    def test_shared_body_filters_and_collider_overrides_round_trip(self) -> None:
        obj = self.game.add(GameObject())
        obj.add_component(PhysicsBody(categories=8, mask=16, group=7))
        obj.add_component(Collider(width=8, height=12, categories=2, override_collision_filtering=False))
        obj.add_component(Collider(shape="circle", radius=4, categories=4))
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(self.game.scenes.current, asset_root=".")
        restored = serializer.restore(self.game, document, asset_root=".")
        obj = restored.objects[0]
        body = obj.require_component(PhysicsBody)
        box, circle = [
            component for component in obj.components
            if isinstance(component, Collider)
        ]
        self.assertEqual((body.categories, body.mask, body.group), (8, 16, 7))
        self.assertEqual((box.shape, circle.shape), ("box", "circle"))
        self.assertFalse(box.override_collision_filtering)
        self.assertEqual(box.categories, 2)
        self.assertEqual(box.effective_filter.categories, 8)
        self.assertTrue(circle.override_collision_filtering)
        self.assertEqual(circle.effective_filter.categories, 4)

    def test_body_velocity_uses_tagged_vec2_and_round_trips(self) -> None:
        obj = self.game.add(GameObject())
        obj.add_component(Collider(width=8, height=12))
        obj.add_component(PhysicsBody(velocity=Vec2(12, -7)))
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(self.game.scenes.current, asset_root=".")
        components = document.objects[0]["components"]
        assert isinstance(components, list) and isinstance(components[1], dict)
        properties = components[1]["properties"]
        assert isinstance(properties, dict)
        self.assertEqual(properties["velocity"], {"$vec2": [12, -7]})
        restored = serializer.restore(self.game, document, asset_root=".")
        velocity = restored.objects[0].require_component(PhysicsBody).velocity
        self.assertIsInstance(velocity, Vec2)
        self.assertEqual(velocity, Vec2(12, -7))

    def test_legacy_collider_filters_become_overrides(self) -> None:
        obj = self.game.add(GameObject())
        obj.add_component(PhysicsBody())
        obj.add_component(Collider(width=8, height=12, categories=2, mask=4, group=3))
        obj.add_component(Collider(shape="circle", radius=4))
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(self.game.scenes.current, asset_root=".")
        components = document.objects[0]["components"]
        assert isinstance(components, list)
        for component in components:
            assert isinstance(component, dict)
            properties = component["properties"]
            assert isinstance(properties, dict)
            properties.pop("override_collision_filtering", None)
            if component["type"] == "g.physics_body":
                for name in ("categories", "mask", "group"):
                    properties.pop(name)
        restored = serializer.restore(self.game, document, asset_root=".")
        obj = restored.objects[0]
        box, circle = [
            component for component in obj.components
            if isinstance(component, Collider)
        ]
        self.assertEqual((box.shape, circle.shape), ("box", "circle"))
        self.assertTrue(box.override_collision_filtering)
        self.assertEqual(box.effective_filter.categories, 2)
        self.assertFalse(circle.override_collision_filtering)
        self.assertEqual(obj.require_component(PhysicsBody).categories, 0xFFFFFFFF)
    def test_component_dependencies_ignore_serialized_order(self) -> None:
        game_object = self.game.add(GameObject())
        game_object.add_component(SerializedRequirement())
        game_object.add_component(SerializedDependent())
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(
            self.game.scenes.current,
            asset_root=".",
        )
        components = document.objects[0]["components"]
        assert isinstance(components, list)
        components.reverse()

        restored = serializer.restore(
            self.game,
            document,
            asset_root=".",
        )

        loaded = restored.objects[0]
        self.assertIsNotNone(
            loaded.get_component(SerializedRequirement)
        )
        self.assertIsNotNone(
            loaded.get_component(SerializedDependent)
        )

    def test_scene_physics_settings_round_trip(self) -> None:
        scene = self.game.scenes.current
        scene.physics.gravity = (25, -750)
        scene.physics.damping = 0.9
        serializer = SceneSerializer(self.registry)

        document = serializer.capture(scene, asset_root=".")
        restored = serializer.restore(
            self.game,
            document,
            asset_root=".",
        )

        self.assertEqual(restored.physics.gravity, (25.0, -750.0))
        self.assertEqual(restored.physics.damping, 0.9)

    def test_scene_presentation_settings_round_trip(self) -> None:
        self.game.presentation = PresentationSettings(
            "fill",
            (15, 25, 35, 255),
            False,
            True,
        )
        serializer = SceneSerializer(self.registry)

        document = serializer.capture(
            self.game.scenes.current,
            asset_root=".",
        )
        restored_game = Game(800, 600)
        serializer.restore(
            restored_game,
            document,
            asset_root=".",
        )

        self.assertEqual(
            restored_game.presentation,
            self.game.presentation,
        )

    def test_missing_serialized_dependency_fails_explicitly(self) -> None:
        game_object = self.game.add(GameObject())
        game_object.add_component(SerializedRequirement())
        game_object.add_component(SerializedDependent())
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(
            self.game.scenes.current,
            asset_root=".",
        )
        components = document.objects[0]["components"]
        assert isinstance(components, list)
        components.pop(0)

        with self.assertRaisesRegex(
            SceneFormatError,
            "SerializedDependent requires SerializedRequirement",
        ):
            serializer.restore(
                self.game,
                document,
                asset_root=".",
            )

    def test_missing_auto_dependency_is_created_while_loading(self) -> None:
        game_object = self.game.add(GameObject())
        game_object.add_component(AutoSerializedDependent())
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(
            self.game.scenes.current,
            asset_root=".",
        )
        components = document.objects[0]["components"]
        assert isinstance(components, list)
        components[:] = [
            component
            for component in components
            if component.get("type")  # type: ignore[union-attr]
            != "tests.serialized_requirement"
        ]

        restored = serializer.restore(
            self.game,
            document,
            asset_root=".",
        )

        self.assertIsNotNone(
            restored.objects[0].get_component(SerializedRequirement)
        )

    def tearDown(self) -> None:
        for scene in tuple(self.game.scenes._scenes.values()):
            for obj in tuple(scene.objects):
                scene.remove(obj)

    def test_round_trip_preserves_hierarchy_components_and_references(self) -> None:
        scene = self.game.scenes.current
        parent = scene.add(
            create_rect(
                30,
                40,
                (20, 40, 60, 200),
                20,
                10,
                collider=True,
            )
        )
        parent.name = "Parent"
        parent.rotation = 15
        parent.scale_x = 2
        parent.speed = Vec2(3, 4)
        child = GameObject(5, 6)
        child.name = "Child"
        parent.add_child(child)
        script = child.add_component(TrackingScript())
        script.speed = 80
        script.target = parent
        script.enabled = False
        scene._prepare_subtree(child)

        with TemporaryDirectory() as directory:
            filename = Path(directory) / "level.scene.json"
            SceneSerializer(self.registry).save(scene, filename)
            restored = SceneSerializer(self.registry).load(self.game, filename)

        self.assertEqual(len(restored.objects), 1)
        loaded_parent = restored.objects[0]
        loaded_child = loaded_parent.children[0]
        self.assertIsInstance(loaded_child, GameObject)
        assert isinstance(loaded_child, GameObject)
        self.assertIs(type(loaded_parent), GameObject)
        self.assertIsInstance(loaded_parent.require_component(RectRenderer), RectRenderer)
        self.assertEqual(loaded_parent.name, "Parent")
        self.assertEqual(loaded_parent.position, Vec2(30, 40))
        self.assertEqual(loaded_parent.rotation, 15)
        self.assertEqual(loaded_parent.scale_x, 2)
        self.assertEqual(loaded_parent.speed, Vec2(3, 4))
        self.assertIsInstance(loaded_parent.get_component(Collider), Collider)
        loaded_script = loaded_child.get_component(TrackingScript)
        self.assertIsNotNone(loaded_script)
        assert loaded_script is not None
        self.assertEqual(loaded_script.speed, 80)
        self.assertFalse(loaded_script.enabled)
        self.assertIs(loaded_script.target, loaded_parent)
        self.assertEqual(loaded_script.ready_count, 1)
        self.assertIs(loaded_script.scene, restored)
        self.assertIs(loaded_script.game, self.game)

    def test_round_trip_preserves_area_collider_settings(self) -> None:
        scene = self.game.scenes.current
        area = create_area_box(40, 50, 80, 30)
        collider = area.require_component(Collider)
        collider.friction = 0.25
        collider.collision_type = 7
        collider.categories = 0b0010
        collider.mask = 0b0101
        scene.add(area)

        with TemporaryDirectory() as directory:
            filename = Path(directory) / "area.scene.json"
            SceneSerializer(self.registry).save(scene, filename)
            restored = SceneSerializer(self.registry).load(
                self.game,
                filename,
            )

        loaded = restored.objects[0]
        self.assertIsInstance(loaded.require_component(Area), Area)
        loaded_collider = loaded.require_component(Collider)
        self.assertIs(loaded_collider.mode, ColliderMode.SENSOR)
        self.assertEqual(loaded_collider.friction, 0.25)
        self.assertEqual(loaded_collider.collision_type, 7)
        self.assertEqual(loaded_collider.categories, 0b0010)
        self.assertEqual(loaded_collider.mask, 0b0101)

    def test_area_selected_collider_index_tracks_removal_and_round_trips(self) -> None:
        scene = self.game.scenes.current
        owner = scene.add(GameObject(40, 50))
        first = owner.add_component(Collider(width=12, height=12))
        selected = owner.add_component(Collider(shape="circle", radius=8, sensor=True))
        area = owner.add_component(Area(collider_index=1))

        with self.assertRaises(ValueError):
            owner.remove_component(selected)

        owner.remove_component(first)
        self.assertEqual(area.collider_index, 0)
        with self.assertRaises(ValueError):
            owner.remove_component(selected)

        serializer = SceneSerializer(self.registry)
        document = serializer.capture(scene, asset_root=".")
        restored = serializer.restore(self.game, document, asset_root=".")
        loaded_owner = restored.objects[0]
        loaded_area = loaded_owner.require_component(Area)
        loaded_colliders = [
            component
            for component in loaded_owner.components
            if isinstance(component, Collider)
        ]

        self.assertEqual(loaded_area.collider_index, 0)
        self.assertEqual(len(loaded_colliders), 1)
        self.assertIsInstance(loaded_colliders[0], Collider)
        self.assertIs(loaded_colliders[0].mode, ColliderMode.SENSOR)

    def test_area_enabled_state_round_trips(self) -> None:
        scene = self.game.scenes.current
        area_object = scene.add(create_area_circle(40, 50, 12))
        area = area_object.require_component(Area)
        area.enabled = False
        self.assertFalse(area.enabled)

        serializer = SceneSerializer(self.registry)
        document = serializer.capture(scene, asset_root=".")
        restored = serializer.restore(self.game, document, asset_root=".")
        loaded = restored.objects[0]
        self.assertIs(type(loaded), GameObject)
        self.assertFalse(loaded.require_component(Area).enabled)

    def test_round_trip_preserves_character_controller(self) -> None:
        scene = self.game.scenes.current
        player = scene.add(create_rect(40, 50, (255, 255, 255), 20, 30))
        player.add_platformer_motor(
            speed=175,
            jump_speed=360,
            coyote_time=0.2,
            ground_snap_distance=12,
            allow_double_jump=True,
        )

        with TemporaryDirectory() as directory:
            filename = Path(directory) / "controller.scene.json"
            SceneSerializer(self.registry).save(scene, filename)
            restored = SceneSerializer(self.registry).load(
                self.game,
                filename,
            )

        loaded = restored.objects[0]
        body = loaded.body
        controller = loaded.get_component(PlatformerMotor)
        assert body is not None
        assert controller is not None
        self.assertEqual(controller.speed, 175)
        self.assertEqual(controller.jump_speed, 360)
        self.assertEqual(controller.coyote_time, 0.2)
        self.assertEqual(controller.ground_snap_distance, 12)
        self.assertTrue(controller.allow_double_jump)
        self.assertTrue(body.fixed_rotation)

    def test_older_platformer_scene_defaults_ground_snap_distance(self) -> None:
        player = self.game.add(create_rect(40, 50, (255, 255, 255), 20, 30))
        player.add_platformer_motor()
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(self.game.scenes.current, asset_root=".")
        components = document.objects[0]["components"]
        assert isinstance(components, list)
        for component in components:
            assert isinstance(component, dict)
            if component["type"] == "g.platformer_motor":
                properties = component["properties"]
                assert isinstance(properties, dict)
                properties.pop("ground_snap_distance")
                properties.pop("allow_double_jump")
        restored = serializer.restore(self.game, document, asset_root=".")
        controller = restored.objects[0].require_component(PlatformerMotor)
        self.assertEqual(controller.ground_snap_distance, 8)
        self.assertFalse(controller.allow_double_jump)

    def test_round_trip_preserves_gui_layer_elements(self) -> None:
        scene = self.game.scenes.current
        view = scene.gui.default
        assert isinstance(view.root, CanvasLayout)
        panel = view.root.add_at(
            Panel(
                (10, 20, 30, 200),
                width=240,
                height=120,
                name="Backdrop",
            ),
            15,
            25,
        )
        assert isinstance(panel, Panel)
        score = panel.add_at(
            Label(
                "Score",
                font_size=22,
                width=120,
                height=30,
                name="Score Label",
                align="left",
                multiline=True,
            ),
            20,
            70,
        )
        score.offset_x = 8
        score.scale_x = 1.2
        score.opacity = 180
        panel.add_at(
            Button("Start", width=180, height=48, name="Start Button"),
            30,
            10,
        )
        panel.add_at(
            Toggle("Music", value=True, name="Music Toggle"),
            30,
            65,
        )
        panel.add_at(
            Slider(0, 10, 7.5, name="Volume Slider"),
            30,
            95,
        )
        panel.add_at(
            TextInput(
                "Player",
                color=(20, 30, 40, 255),
                text_color=(220, 230, 240, 255),
                caret_color=(255, 120, 80, 255),
                name="Name Input",
            ),
            30,
            125,
        )
        scene.gui.register(
            "Pause",
            UIView(
                CanvasLayout(),
                transparent=True,
                modal=True,
                pauses_below=True,
                pauses_scene=True,
                identifier="pause-view",
                name="Pause",
            ),
        )
        panel.add_at(
            ScrollView(
                Label("Scrollable", width=100, height=300),
                width=100,
                height=80,
                scroll_speed=24,
                name="Items Scroll",
            ),
            150,
            125,
        )

        with TemporaryDirectory() as directory:
            filename = Path(directory) / "gui.scene.json"
            SceneSerializer(self.registry).save(scene, filename)
            restored = SceneSerializer(self.registry).load(
                self.game,
                filename,
            )

        restored_view = restored.gui.default
        self.assertEqual(restored_view.name, "HUD")
        assert isinstance(restored_view.root, CanvasLayout)
        self.assertEqual(
            [widget.name for widget in restored_view.root.children],
            ["Backdrop"],
        )
        restored_panel = restored_view.root.children[0]
        self.assertIsInstance(restored_panel, Panel)
        assert isinstance(restored_panel, Panel)
        self.assertEqual(
            [widget.name for widget in restored_panel.children],
            [
                "Score Label",
                "Start Button",
                "Music Toggle",
                "Volume Slider",
                "Name Input",
                "Items Scroll",
            ],
        )
        self.assertEqual(
            restored_panel.position_of(restored_panel.children[1]),
            (30, 10),
        )
        restored_toggle = restored_panel.children[2]
        restored_slider = restored_panel.children[3]
        restored_input = restored_panel.children[4]
        self.assertIsInstance(restored_toggle, Toggle)
        self.assertIsInstance(restored_slider, Slider)
        self.assertIsInstance(restored_input, TextInput)
        assert isinstance(restored_toggle, Toggle)
        assert isinstance(restored_slider, Slider)
        assert isinstance(restored_input, TextInput)
        self.assertTrue(restored_toggle.value)
        self.assertEqual(
            (
                restored_slider.minimum,
                restored_slider.maximum,
                restored_slider.value,
            ),
            (0, 10, 7.5),
        )
        self.assertEqual(restored_input.text, "Player")
        self.assertEqual(restored_input.color, (20, 30, 40, 255))
        restored_score = restored_panel.children[0]
        restored_scroll = restored_panel.children[5]
        self.assertIsInstance(restored_score, Label)
        self.assertIsInstance(restored_scroll, ScrollView)
        assert isinstance(restored_score, Label)
        assert isinstance(restored_scroll, ScrollView)
        self.assertEqual(restored_score.align, "left")
        self.assertTrue(restored_score.multiline)
        self.assertEqual(restored_score.offset_x, 8)
        self.assertEqual(restored_score.scale_x, 1.2)
        self.assertEqual(restored_score.opacity, 180)
        self.assertEqual(restored_scroll.scroll_speed, 24)
        restored_pause = restored.gui.get("Pause")
        self.assertEqual(restored_pause.id, "pause-view")
        self.assertTrue(restored_pause.transparent)
        self.assertTrue(restored_pause.modal)
        self.assertTrue(restored_pause.pauses_below)
        self.assertTrue(restored_pause.pauses_scene)

    def test_font_assets_round_trip_for_text_and_gui(self) -> None:
        scene = self.game.scenes.current
        with TemporaryDirectory() as directory:
            root = Path(directory)
            relative_font = Path("fonts/Inter.ttf")
            font_file = root / relative_font
            font_file.parent.mkdir()
            shutil.copyfile(
                Path(
                    "g_editor/assets/fonts/Inter/static/"
                    "Inter_18pt-Regular.ttf"
                ),
                font_file,
            )
            font = self.game.resources.load_font(font_file)
            scene.add(create_text(
                "World",
                10,
                20,
                font=font,
                alignment="right",
            ))
            assert isinstance(scene.gui.default.root, CanvasLayout)
            scene.gui.default.root.add_at(
                Label("HUD", font=font),
                0,
                0,
            )
            scene.gui.default.root.add_at(
                Button("Start", style=ButtonStyle(font=font)),
                0,
                40,
            )
            filename = root / "font.scene.json"

            SceneSerializer(self.registry).save(scene, filename)
            payload = json.loads(filename.read_text(encoding="utf-8"))
            restored = SceneSerializer(self.registry).load(
                self.game,
                filename,
            )

        text_font = restored.objects[0].require_component(
            TextRenderer
        ).font
        text_alignment = restored.objects[0].require_component(
            TextRenderer
        ).alignment
        assert isinstance(restored.gui.default.root, CanvasLayout)
        label = restored.gui.default.root.children[0]
        button = restored.gui.default.root.children[1]
        assert isinstance(label, Label)
        assert isinstance(button, Button)
        world_layer = next(
            layer for layer in payload["layers"] if layer["type"] == "object"
        )
        ui_layer = payload["ui_views"][0]
        self.assertEqual(
            world_layer["objects"][0]["components"][0]["properties"]["font"],
            relative_font.as_posix(),
        )
        self.assertEqual(
            ui_layer["elements"][0]["font"],
            relative_font.as_posix(),
        )
        self.assertEqual(text_font, font)
        self.assertEqual(text_alignment, "right")
        self.assertEqual(label.font, font)
        self.assertEqual(button.style.font, font)

    def test_text_alignment_round_trips_and_defaults_for_older_scenes(
        self,
    ) -> None:
        scene = self.game.scenes.current
        scene.add(create_text(
            "Score",
            10,
            20,
            alignment="right",
        ))
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(scene, asset_root=".")

        restored = serializer.restore(
            self.game,
            document,
            asset_root=".",
        )

        self.assertEqual(
            restored.objects[0].require_component(TextRenderer).alignment,
            "right",
        )
        components = document.objects[0]["components"]
        assert isinstance(components, list)
        component = components[0]
        assert isinstance(component, dict)
        properties = component["properties"]
        assert isinstance(properties, dict)
        properties.pop("alignment")

        restored_legacy = serializer.restore(
            self.game,
            document,
            asset_root=".",
        )

        self.assertEqual(
            restored_legacy.objects[0]
            .require_component(TextRenderer)
            .alignment,
            "center",
        )

    def test_round_trip_preserves_editor_style_sprite_record(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "hero.png"
            pyglet.image.SolidColorImagePattern(
                (20, 40, 60, 255)
            ).create_image(30, 42).save(str(image_path))
            self.game.scenes.current.add(
                create_sprite(Image(image_path), 12, 18)
            )
            filename = root / "sprite.scene.json"

            SceneSerializer(self.registry).save(
                self.game.scenes.current,
                filename,
                asset_root=root,
            )
            restored = SceneSerializer(self.registry).load(
                self.game,
                filename,
                asset_root=root,
            )

        loaded = restored.objects[0]
        self.assertIs(type(loaded), GameObject)
        renderer = loaded.require_component(SpriteRenderer)
        self.assertIsInstance(renderer.image, Image)
        assert isinstance(renderer.image, Image)
        self.assertEqual((renderer.image.width, renderer.image.height), (30, 42))

    def test_removed_builtin_object_types_are_rejected_without_conversion(self) -> None:
        scene = self.game.scenes.current
        parent = scene.add(GameObject(10, 20))
        child = GameObject(3, 4)
        parent.add_child(child)
        serializer = SceneSerializer(self.registry)
        raw = serializer.capture(scene, asset_root=".").to_dict()
        for type_name in (
            "g.sprite", "g.rect", "g.circle", "g.text", "g.line",
            "g.triangle", "g.polygon", "g.area",
        ):
            for nested in (False, True):
                with self.subTest(type_name=type_name, nested=nested):
                    data = deepcopy(raw)
                    root = data["layers"][0]["objects"][0]
                    record = root["children"][0] if nested else root
                    record["type"] = type_name
                    document = SceneDocument.from_dict(data)
                    self.assertEqual(record["type"], type_name)
                    with self.assertRaisesRegex(SceneFormatError, "unregistered game object type"):
                        serializer.restore(self.game, document, asset_root=".")

    def test_removed_visual_renderer_does_not_leave_stale_alias_after_load(self) -> None:
        scene = self.game.scenes.current
        visual = scene.add(create_rect(12, 18, (255, 255, 255), 20, 10))
        renderer = visual.require_component(RectRenderer)

        self.assertFalse(hasattr(visual, "renderer"))
        visual.remove_component(renderer)
        self.assertIsNone(visual.get_component(RectRenderer))
        self.assertFalse(hasattr(visual, "renderer"))

        serializer = SceneSerializer(self.registry)
        document = serializer.capture(scene, asset_root=".")
        restored = serializer.restore(self.game, document, asset_root=".")
        loaded = restored.objects[0]

        self.assertIs(type(loaded), GameObject)
        self.assertIsNone(loaded.get_component(RectRenderer))
        self.assertFalse(hasattr(loaded, "renderer"))

    def test_round_trip_preserves_image_less_sprite(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sprite = GameObject(4, 8)
            renderer = sprite.add_component(SpriteRenderer())
            self.game.scenes.current.add(sprite)
            renderer.sync()
            self.assertFalse(renderer.drawable.visible)
            filename = root / "empty-sprite.scene.json"

            SceneSerializer(self.registry).save(
                self.game.scenes.current,
                filename,
                asset_root=root,
            )
            data = json.loads(filename.read_text())
            restored = SceneSerializer(self.registry).load(
                self.game,
                filename,
                asset_root=root,
            )

        stored = data["layers"][0]["objects"][0]["components"][0]
        self.assertEqual(stored["type"], "g.sprite_renderer")
        self.assertIsNone(stored["properties"]["image"])
        loaded = restored.objects[0].get_component(SpriteRenderer)
        assert loaded is not None
        self.assertIsNone(loaded.image)

    def test_scene_manager_load_registers_scene(self) -> None:
        with TemporaryDirectory() as directory:
            filename = Path(directory) / "empty.scene.json"
            SceneSerializer(self.registry).save(self.game.scenes.current, filename)
            loaded = self.game.scenes.load(
                "loaded", str(filename), registry=self.registry
            )
        self.assertIs(self.game.scenes.get("loaded"), loaded)

    def test_round_trip_preserves_gui_layout_containers(self) -> None:
        scene = self.game.scenes.current
        view = scene.gui.default
        scene.gui.rename(view, "Menu")
        assert isinstance(view.root, CanvasLayout)
        column = VBox(
            spacing=9,
            padding=(10, 11, 12, 13),
            alignment="start",
            width=360,
            height=280,
            name="Menu Column",
        )
        row = column.add(
            HBox(
                Button("Back"),
                Toggle("Hints", value=True),
                spacing=6,
                alignment="end",
                name="Action Row",
            )
        )
        grid = column.add(
            GridLayout(
                Label("A"),
                Label("B"),
                columns=2,
                row_spacing=4,
                column_spacing=5,
                name="Choice Grid",
            )
        )
        stack = column.add(
            StackLayout(
                Panel(width=100, height=40),
                Label("Overlay"),
                padding=3,
                name="Overlay Stack",
            )
        )
        anchor = column.add(
            AnchorLayout(
                Button("Corner"),
                horizontal="right",
                vertical="bottom",
                margin=(1, 2, 3, 4),
                name="Corner Anchor",
            )
        )
        view.root.add_at(column, 25, 35)

        with TemporaryDirectory() as directory:
            filename = Path(directory) / "layouts.scene.json"
            SceneSerializer(self.registry).save(scene, filename)
            restored = SceneSerializer(self.registry).load(
                self.game,
                filename,
            )

        restored_view = restored.gui.default
        self.assertEqual(restored_view.name, "Menu")
        assert isinstance(restored_view.root, CanvasLayout)
        restored_column = restored_view.root.children[0]
        self.assertIsInstance(restored_column, VBox)
        assert isinstance(restored_column, VBox)
        self.assertEqual(restored_column.spacing, 9)
        self.assertEqual(restored_column.padding, (10, 11, 12, 13))
        self.assertEqual(restored_column.alignment, "start")
        self.assertIsInstance(restored_column.children[0], HBox)
        self.assertIsInstance(restored_column.children[1], GridLayout)
        self.assertIsInstance(restored_column.children[2], StackLayout)
        self.assertIsInstance(restored_column.children[3], AnchorLayout)
        restored_row = restored_column.children[0]
        restored_grid = restored_column.children[1]
        restored_anchor = restored_column.children[3]
        assert isinstance(restored_row, HBox)
        assert isinstance(restored_grid, GridLayout)
        assert isinstance(restored_anchor, AnchorLayout)
        self.assertEqual([child.name for child in restored_row.children], [
            "Button",
            "Toggle",
        ])
        self.assertEqual(restored_grid.columns, 2)
        self.assertEqual(restored_anchor.horizontal, "right")
        self.assertEqual(restored_anchor.anchor_margin, (1, 2, 3, 4))
        self.assertIs(row.parent, column)
        self.assertIs(grid.parent, column)
        self.assertIs(stack.parent, column)
        self.assertIs(anchor.parent, column)

    def test_document_rejects_unknown_versions(self) -> None:
        with self.assertRaisesRegex(SceneFormatError, "unsupported scene version"):
            SceneDocument.from_dict(
                {
                    "format": "g.scene",
                    "version": 99,
                    "settings": {},
                    "objects": [],
                }
            )

    def test_document_writes_readable_json_atomically(self) -> None:
        document = SceneDocument({"background": [0, 0, 0, 255]}, [])
        with TemporaryDirectory() as directory:
            filename = Path(directory) / "scene.json"
            document.write(filename)
            raw = json.loads(filename.read_text())
            restored = SceneDocument.read(filename)
        self.assertEqual(raw["format"], "g.scene")
        self.assertEqual(raw["version"], 1)
        self.assertIn("layers", raw)
        self.assertNotIn("objects", raw)
        self.assertEqual(restored, document)

    def test_document_discards_legacy_layer_lock_state(self) -> None:
        document = SceneDocument({"background": [0, 0, 0, 255]}, [])
        raw = document.to_dict()
        layers = raw["layers"]
        assert isinstance(layers, list)
        layer = layers[0]
        assert isinstance(layer, dict)
        layer["locked"] = True

        restored = SceneDocument.from_dict(raw)

        self.assertNotIn("locked", restored.layers[0])

    def test_round_trip_preserves_layers_and_z_index(self) -> None:
        scene = self.game.scenes.current
        background = scene.layers.add(
            "Background",
            visible=False,
        )
        scene.default_layer = background
        game_object = scene.add(
            create_rect(10, 20, (255, 255, 255), 5, 5),
            layer=background,
            z_index=-4,
        )
        game_object.properties = {
            "team": "player",
            "damage": 3,
            "spawn": Vec2(10, 20),
        }
        game_object.render_behind_parent = True

        document = SceneSerializer(self.registry).capture(
            scene,
            asset_root=".",
        )
        restored = SceneSerializer(self.registry).restore(
            self.game,
            document,
            asset_root=".",
        )

        loaded_layer = restored.layers["Background"]
        loaded_object = restored.objects[0]
        self.assertIs(restored.default_layer, loaded_layer)
        self.assertFalse(loaded_layer.visible)
        self.assertIs(loaded_object.layer, loaded_layer)
        self.assertEqual(loaded_object.z_index, -4)
        self.assertTrue(loaded_object.render_behind_parent)
        self.assertEqual(
            loaded_object.properties,
            {
                "team": "player",
                "damage": 3,
                "spawn": Vec2(10, 20),
            },
        )
        scene.remove(game_object)

    def test_script_properties_reject_non_object_scene_values(self) -> None:
        scene = self.game.scenes.current
        scene.add(GameObject())
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(scene, asset_root=".")
        document.objects[0]["script_properties"] = []

        with self.assertRaisesRegex(
            SceneFormatError,
            "script_properties must be an object",
        ):
            serializer.restore(self.game, document, asset_root=".")

    def test_render_behind_parent_defaults_to_false_for_older_scenes(self) -> None:
        scene = self.game.scenes.current
        scene.add(GameObject())
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(scene, asset_root=".")
        document.objects[0].pop("render_behind_parent")

        restored = serializer.restore(self.game, document, asset_root=".")

        self.assertFalse(restored.objects[0].render_behind_parent)

    def test_render_behind_parent_rejects_non_boolean_scene_values(self) -> None:
        scene = self.game.scenes.current
        scene.add(GameObject())
        serializer = SceneSerializer(self.registry)
        document = serializer.capture(scene, asset_root=".")
        document.objects[0]["render_behind_parent"] = "false"

        with self.assertRaisesRegex(SceneFormatError, "must be a boolean"):
            serializer.restore(self.game, document, asset_root=".")

    def test_unregistered_components_fail_explicitly(self) -> None:
        self.game.add(GameObject()).add_component(Script())
        with self.assertRaisesRegex(SceneFormatError, "not registered"):
            SceneSerializer(self.registry).capture(
                self.game.scenes.current,
                asset_root=".",
            )

    def test_script_added_to_live_object_becomes_ready_once(self) -> None:
        obj = self.game.add(GameObject())
        script = obj.add_component(TrackingScript())
        self.assertEqual(script.ready_count, 1)
        self.game.scenes.current._prepare_subtree(obj)
        self.assertEqual(script.ready_count, 1)
        self.game._update(0.25)
        self.assertEqual(script.updates, [0.25])

    def test_restored_ready_callbacks_see_later_objects_layers_and_camera(self) -> None:
        class LookupScript(Script):
            def on_ready(self) -> None:
                self.target = self.scene.require_child("PlayerFellArea")
                self.nested = self.target.require_child("Nested")
                self.target_scene = self.nested.require_scene()
                self.camera_position = self.scene.camera.position.copy()

        self.registry.register_component("tests.lookup_ready", LookupScript)
        document = SceneDocument(
            settings={"camera": {"position": {"$vec2": [12, 34]}}},
            layers=[
                {"type": "object", "name": "Players", "id": "players", "default": True,
                 "objects": [{"id": "player", "type": "g.game_object", "name": "Player",
                              "components": [{"type": "tests.lookup_ready", "properties": {}}]}]},
                {"type": "object", "name": "Triggers", "id": "triggers",
                 "objects": [{"id": "area", "type": "g.game_object", "name": "PlayerFellArea",
                              "children": [{"id": "nested", "type": "g.game_object", "name": "Nested"}]}]},
            ],
        )
        scene = SceneSerializer(self.registry).restore(self.game, document, asset_root=".")
        script = scene.require_child("Player").require_component(LookupScript)
        self.assertIs(script.target, scene.require_child("PlayerFellArea"))
        self.assertIs(script.target_scene, scene)
        self.assertEqual(script.camera_position, Vec2(12, 34))
        self.assertTrue(script._ready)

    def test_preview_attachment_rendering_and_component_additions_do_not_run_ready(self) -> None:
        document = SceneDocument(settings={}, objects=[
            {"id": "player", "type": "g.game_object",
             "components": [{"type": "tests.tracking_script", "properties": {}}]},
        ])
        scene = SceneSerializer(self.registry).restore(
            self.game, document, asset_root=".", initialize_components=False,
        )
        obj = scene.objects[0]
        script = obj.require_component(TrackingScript)
        extra = obj.add_component(TrackingScript())
        child = GameObject()
        child_script = child.add_component(TrackingScript())
        obj.add_child(child)
        scene._draw()
        scene._draw()
        for component in (script, extra, child_script):
            self.assertEqual(component.ready_count, 0)
            self.assertFalse(component._ready)
        scene._initialize_components()
        scene._draw()
        for component in (script, extra, child_script):
            self.assertEqual(component.ready_count, 1)
        live_script = obj.add_component(TrackingScript())
        self.assertEqual(live_script.ready_count, 1)

    def test_registered_code_first_object_keeps_class_and_fields(self) -> None:
        obj = self.game.add(NamedObject())
        obj.label = "checkpoint"

        with TemporaryDirectory() as directory:
            filename = Path(directory) / "code-first.scene.json"
            SceneSerializer(self.registry).save(
                self.game.scenes.current, filename
            )
            restored = SceneSerializer(self.registry).load(
                self.game, filename
            )

        self.assertIsInstance(restored.objects[0], NamedObject)
        loaded = restored.objects[0]
        assert isinstance(loaded, NamedObject)
        self.assertEqual(loaded.label, "checkpoint")


if __name__ == "__main__":
    unittest.main()
