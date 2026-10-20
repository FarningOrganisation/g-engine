import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from g_editor.asset_references import find_asset_references
from g_editor.state import new_object_record, new_scene_document
from g import ProjectConfig


class AssetReferenceTests(unittest.TestCase):
    def test_finds_open_scene_files_on_disk_and_scripts(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            image = root / "art" / "hero.png"
            image.parent.mkdir()
            image.write_bytes(b"")
            (root / "art" / "unused.png").write_bytes(b"")
            script = root / "art" / "logic.py"
            script.write_text("", encoding="utf-8")
            project = ProjectConfig.create(root).with_module("art.logic")
            project.write()
            (root / "level.prefab.json").write_text(
                json.dumps({"a": ["art/hero.png", {"b": "art/hero.png"}]}),
                encoding="utf-8",
            )
            document = new_scene_document()
            record = new_object_record(
                "sprite", image="art/hero.png", image_size=(8, 8)
            )
            record["name"] = "Hero"
            document.objects.append(record)
            open_scene = root / "main.scene.json"
            open_scene.write_text(
                json.dumps({"image": "art/unused.png"}),
                encoding="utf-8",
            )

            self.assertEqual(
                find_asset_references(
                    root,
                    image,
                    document=document,
                    document_path=open_scene,
                    project=project,
                ),
                (
                    "main.scene.json (open scene): Hero",
                    "level.prefab.json (2 references)",
                ),
            )
            self.assertEqual(
                find_asset_references(
                    root,
                    root / "art",
                    document=document,
                    document_path=open_scene,
                    project=project,
                ),
                (
                    "main.scene.json (open scene): Hero",
                    "level.prefab.json (2 references)",
                    "g.project.json: registered script art.logic",
                ),
            )
            # The open document replaces its stale file on disk.
            self.assertEqual(
                find_asset_references(
                    root,
                    root / "art" / "unused.png",
                    document=document,
                    document_path=open_scene,
                ),
                (),
            )


if __name__ == "__main__":
    unittest.main()
