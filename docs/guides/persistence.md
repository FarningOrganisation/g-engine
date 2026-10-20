# Persistence and exporting

## Save and load authored scenes

```python
# Assume game exists.
game.scenes.current.save("scenes/level.scene.json", asset_root=".")
game.scenes.load("level", "scenes/level.scene.json", asset_root=".")
game.scenes.switch("level")
```

Scenes store settings, layers, object hierarchies, registered components,
GUI, tiles, stable IDs, and portable resource paths. They do not serialize
arbitrary Python code. Custom types must be registered by trusted project
modules before restoring a scene.

Prefab instances reuse template data with stable IDs and overrides. Keep
prefabs and scenes under version control along with their resource files.

## Project configuration

`g.project.json` contains the display settings, script modules, export options,
and optional networking configuration. The project folder is the asset root.

```python
from g import Game

game = Game.from_project("g.project.json")
game.scenes.load("level", "level.scene.json")
game.scenes.switch("level")
game.run(title="My Game")
```

`Game.from_project` reads configuration and imports scripts. It does not
replace the explicit scene-loading step shown above.

## Compatibility

The current scene and project formats have explicit version fields, but
prototype schemas can still change without migrations. Keep a backup before
upgrading the engine. Do not use scene serialization as an assumed universal
save-game API; checkpoint and authored-scene behavior have different goals.

Use [SceneDocument and SceneSerializer](../api/persistence.md) and
[ProjectConfig](../api/project.md) as the source of truth for the current
format and supported values.

## Export a desktop application

From the project directory, install the export extra from the engine checkout
or the appropriate Git revision. Then configure a startup scene in the
project/editor export settings and run:

```bash
python -m g.export g.project.json
```

PyInstaller packages the project into a desktop application. Build on the
target operating system; this is not a general cross-compilation service.
The editor also exposes export configuration and progress.

Test the exported application from another working directory and confirm
images, fonts, audio, registered scripts, and scene transitions work.
OS code signing, notarization, and installer creation are separate deployment
steps.

When networking is enabled, export produces separate client and server
applications. Exporting a server does not deploy or host it.
