import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import pyglet

from g import (
    Game,
    GameObject,
    Image,
    SceneFormatError,
    SceneSerializer,
    SpriteAnimation,
    SpriteAnimationFrame,
    SpriteAnimator,
    SpriteRenderer,
    SpriteSheet,
    SpriteSheetRegion,
)
from g.persistence import JsonValue


class SpriteSheetGridTests(unittest.TestCase):
    def test_grid_uses_top_left_coordinates_and_row_major_iteration(self) -> None:
        grid = SpriteSheet(Image.solid(70, 38, (255, 255, 255))).grid(
            16,
            margin=2,
            spacing=2,
        )

        self.assertEqual((grid.columns, grid.rows, len(grid)), (3, 2, 6))
        self.assertEqual(
            [(region.x, region.y) for region in grid],
            [(2, 2), (20, 2), (38, 2), (2, 20), (20, 20), (38, 20)],
        )
        self.assertIs(grid[1, 0].sheet, grid.image)
        self.assertEqual((grid[-1].width, grid[-1].height), (16, 16))
        self.assertEqual(len(grid[1:3]), 2)

    def test_grid_can_limit_columns_and_rows(self) -> None:
        grid = SpriteSheet(Image.solid(64, 64, (255, 255, 255))).grid(
            16,
            columns=2,
            rows=3,
        )
        self.assertEqual((grid.columns, grid.rows, len(grid)), (2, 3, 6))
        with self.assertRaises(IndexError):
            _ = grid[2, 0]

    def test_grid_rejects_invalid_geometry(self) -> None:
        sheet = SpriteSheet(Image.solid(16, 16, (255, 255, 255)))
        for arguments in (
            (0,),
            (17,),
            (8, 8),
        ):
            with self.subTest(arguments=arguments):
                if arguments == (8, 8):
                    with self.assertRaises(ValueError):
                        sheet.grid(*arguments, margin=5)
                else:
                    with self.assertRaises(ValueError):
                        sheet.grid(*arguments)
        with self.assertRaises(TypeError):
            sheet.grid(8.0)  # type: ignore[arg-type]

    def test_regions_work_without_a_presentation_context(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sheet.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(16, 8).save(str(path))
            image = Image(path, presentation_enabled=False)
            region = SpriteSheet(image).grid(8)[1, 0]
            self.assertEqual((region.width, region.height), (8, 8))
            self.assertIsNone(region._source)


class SpriteSheetPersistenceTests(unittest.TestCase):
    def test_renderer_and_animator_regions_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "sheet.png"
            pyglet.image.SolidColorImagePattern(
                (255, 255, 255, 255)
            ).create_image(16, 8).save(str(path))
            game = Game(32, 32)
            game.resources.asset_root = root
            grid = SpriteSheet(game.resources.load_image("sheet.png")).grid(8)
            owner = game.add(GameObject())
            owner.add_component(SpriteRenderer(grid[0, 0]))
            owner.add_component(SpriteAnimator({
                "walk": SpriteAnimation((
                    SpriteAnimationFrame(grid[0, 0], 0.1),
                    SpriteAnimationFrame(grid[1, 0], 0.2),
                )),
            }, "walk"))

            serializer = SceneSerializer()
            document = serializer.capture(
                game.scenes.current,
                asset_root=root,
            )
            components = document.objects[0]["components"]
            assert isinstance(components, list)
            animator_record = next(
                component
                for component in components
                if isinstance(component, dict)
                and component.get("type") == "g.sprite_animator"
            )
            properties = animator_record["properties"]
            assert isinstance(properties, dict)
            states = properties["states"]
            assert isinstance(states, list)
            state = states[0]
            assert isinstance(state, dict)
            frames = state["frames"]
            assert isinstance(frames, list)
            assert all(isinstance(frame, dict) for frame in frames)
            self.assertEqual(
                [
                    frame["region"]
                    for frame in frames
                    if isinstance(frame, dict)
                ],
                [[0, 0, 8, 8], [8, 0, 8, 8]],
            )

            restored = serializer.restore(game, document, asset_root=root)
            animator = restored.objects[0].require_component(SpriteAnimator)
            images = [
                frame.image
                for frame in animator.states["walk"].frames
            ]
            self.assertTrue(
                all(isinstance(image, SpriteSheetRegion) for image in images)
            )
            self.assertEqual(
                [(image.x, image.y) for image in images if isinstance(image, SpriteSheetRegion)],
                [(0, 0), (8, 0)],
            )

            invalid_frame = frames[0]
            assert isinstance(invalid_frame, dict)
            invalid_frame["region"] = cast(
                JsonValue,
                [12, 0, 8, 8],
            )
            with self.assertRaisesRegex(
                SceneFormatError,
                "exceeds its image",
            ):
                serializer.restore(game, document, asset_root=root)


if __name__ == "__main__":
    unittest.main()
