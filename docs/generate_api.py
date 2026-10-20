"""Generate reference pages for the root package's explicit public exports.

This runs during MkDocs builds without importing the rendering runtime.
"""

import ast
from collections import defaultdict
from pathlib import Path

import mkdocs_gen_files


root = Path(__file__).resolve().parents[1]
tree = ast.parse((root / "g" / "__init__.py").read_text(encoding="utf-8"))
sources: dict[str, str] = {}
exports: list[str] = []
for node in tree.body:
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            sources[alias.asname or alias.name] = node.module or "colors"
    elif isinstance(node, ast.Assign):
        if any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            exports = ast.literal_eval(node.value)

if not exports:
    raise RuntimeError("g.__all__ must list the documented public API")

groups: dict[str, list[str]] = defaultdict(list)
for name in exports:
    if name == "__version__":
        continue
    groups[sources[name]].append(name)

titles = {
    "behaviours": "Behaviours and animation",
    "components": "Components and renderers",
    "curves": "Splines",
    "gui": "GUI",
    "input": "Input",
    "inspector": "Inspector and network fields",
    "math": "Vector math",
    "navigation": "Navigation",
    "network": "Experimental networking",
    "nodes": "Nodes and game objects",
    "objects": "Object factories",
    "particles": "Particle definitions",
    "persistence": "Scene persistence",
    "prefab": "Prefabs",
    "presentation": "Presentation and scaling",
    "project": "Project configuration",
    "rendering": "Scene rendering",
    "resources": "Resources",
    "scene": "Scenes and layers",
    "shader": "Shaders and post-processing",
    "tilemap": "Tile maps",
    "timing": "Timers",
}

with mkdocs_gen_files.open("api/index.md", "w") as page:
    page.write(
        "# Public API reference\n\n"
        "These pages are generated from `g.__all__` and the source "
        "docstrings. Prefer `from g import Game, Scene` and other root "
        "exports in game code. Private names are implementation details.\n\n"
        "All API pages describe the version in this checkout. During the "
        "alpha period, APIs and file formats may change without migrations. "
        "Networking remains experimental.\n\n"
    )
    for source in sorted(groups):
        title = titles.get(source, source.replace("_", " ").title())
        page.write(f"- [{title}]({source}.md)\n")

with mkdocs_gen_files.open("api/SUMMARY.md", "w") as summary:
    summary.write("- [API reference](index.md)\n")
    for source in sorted(groups):
        title = titles.get(source, source.replace("_", " ").title())
        filename = f"api/{source}.md"
        summary.write(f"- [{title}]({source}.md)\n")
        with mkdocs_gen_files.open(filename, "w") as page:
            page.write(f"# {title}\n\n")
            for name in groups[source]:
                page.write(f"::: g.{name}\n\n")
        mkdocs_gen_files.set_edit_path(filename, "g/__init__.py")
