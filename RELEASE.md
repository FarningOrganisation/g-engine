# First Release Plan

This document tracks the work required for the first public release of `g`.
The target is **`0.1.0-alpha`**: a useful, installable, and documented preview
whose APIs and serialized formats may still evolve.

## Release goals

The first release should:

- Be installable as `g-engine` from a Git repository or built distribution.
- Provide a coherent beginner-facing API for creating complete 2D games.
- Make the supported public API discoverable through documentation and
  docstrings.
- Load, edit, validate, and export representative projects reliably.
- Run on supported Python versions and desktop platforms.
- Clearly identify experimental APIs and known limitations.

The first release does not need every feature expected from a mature game
engine. Reliability, documentation, and a clear scope take priority over
adding more engine systems.

## Supported release scope

The release should present the following existing systems as its core feature
set:

- Game, scene, layer, game-object, component, and script APIs
- Scene-time timers and behaviours
- Sprites, text, shapes, custom fonts, particles, and trails
- Cameras and world-space or screen-space rendering
- Pymunk-backed physics, collision callbacks, and spatial queries
- Tile maps, tilesets, tile metadata, collision, and navigation
- Keyboard and mouse input
- GUI views, widgets, layouts, focus, and transitions
- Resource loading and caching
- Scene persistence, prefabs, stable object IDs, and references
- Project configuration and PyInstaller-based desktop exports
- The optional visual scene editor
- Per-renderer shaders and multi-pass scene post-processing
- Opt-in ambient, point, and spot lighting with polygonal hard shadows
- Experimental networking, clearly marked as experimental

## Release blockers

### 1. Define and document the public API

- [ ] Review the names exported by `g.__all__`.
- [ ] Remove accidental exports or mark experimental APIs explicitly.
- [ ] Define the compatibility policy for `0.x` releases.
- [ ] Define how deprecated APIs will be announced and removed.
- [x] Add docstrings to every supported exported class and function.
- [x] Document important public properties, callbacks, and lifecycle methods.
- [ ] Include parameters, return values, exceptions, ownership, and lifecycle
      behavior where relevant.
- [ ] Prefer short examples for beginner-facing APIs.

Public API documentation should be completed before attempting to document
internal implementation classes.

### 2. Create the documentation website

Use:

- MkDocs
- Material for MkDocs
- mkdocstrings-python
- GitHub Pages

Suggested documentation structure:

```text
docs/
├── index.md
├── getting-started/
│   ├── installation.md
│   ├── first-game.md
│   ├── project-structure.md
│   └── editor.md
├── guides/
│   ├── game-and-scenes.md
│   ├── objects-and-components.md
│   ├── resources.md
│   ├── input.md
│   ├── physics.md
│   ├── tile-maps.md
│   ├── gui.md
│   ├── shaders.md
│   ├── persistence.md
│   └── exporting.md
├── api/
│   ├── game.md
│   ├── scene.md
│   ├── objects.md
│   ├── components.md
│   ├── physics.md
│   ├── gui.md
│   ├── resources.md
│   └── editor.md
└── reference/
    ├── scene-format.md
    ├── project-format.md
    └── compatibility.md
```

Tasks:

- [x] Add MkDocs configuration and documentation dependencies.
- [x] Create the documentation navigation.
- [x] Convert relevant README sections into focused guides.
- [ ] Adapt suitable documents from `g/doc/` for public documentation.
- [x] Generate API reference pages from docstrings.
- [x] Add links between guides and API reference pages.
- [x] Add a strict documentation build that rejects broken references.
- [x] Add a GitHub Actions workflow that publishes `main` to GitHub Pages.
- [x] Build documentation for pull requests without deploying it.

The first site is implemented in `docs/` with its configuration in
`mkdocs.yml`. GitHub Pages still needs to be enabled in the published
repository's settings; adding the workflow does not enable hosting by itself.

### 3. Stabilize project and scene persistence

- [ ] Declare the current project and scene schemas as the `0.1` schemas.
- [ ] Document both serialized formats.
- [ ] Define how schema migrations will work.
- [ ] Add fixture tests for representative project, scene, prefab, GUI, tile,
      font, and script data.
- [ ] Ensure unsupported versions produce actionable errors.
- [ ] Decide which editor-only fields are stable serialized data.
- [ ] Keep scene persistence separate from future save-game state.

### 4. Add complete project validation

Validation should run before preview and export and should report actionable
errors for:

- [ ] Missing images, fonts, audio, scenes, prefabs, and shader files
- [ ] Invalid script modules
- [ ] Unsupported serialized object or component types
- [ ] Broken object and prefab references
- [ ] Invalid startup scenes
- [ ] Invalid project display or export settings
- [ ] Assets outside the project root
- [ ] Platform-specific export limitations

Where practical, validation should collect multiple independent errors instead
of stopping at the first one. It should be available from both the editor and
a command-line entry point.

### 5. Add continuous integration

Create GitHub Actions workflows that:

- [ ] Run the full test suite.
- [ ] Test Python 3.11, 3.12, and 3.13.
- [ ] Exercise Linux, Windows, and macOS where practical.
- [ ] Build the wheel and source distribution.
- [ ] Install the wheel in a clean environment.
- [ ] Verify `import g`, `import g_editor`, and the `g-editor` entry point.
- [ ] Build the documentation in strict mode.
- [ ] Run a minimal exported-game smoke test.
- [ ] Retain useful logs or build artifacts when a job fails.

Platform-specific tests may run in separate jobs where graphics or windowing
requirements make a single matrix impractical.

### 6. Provide starter projects and complete examples

- [ ] Add a minimal code-first starter project.
- [ ] Add a minimal editor and serialized-scene starter project.
- [ ] Provide one small polished game that demonstrates the recommended APIs.
- [ ] Ensure every starter can be run from a fresh installation.
- [ ] Ensure every starter can be exported.
- [ ] Document the directory structure and asset conventions.
- [ ] Keep examples focused; avoid requiring users to learn experimental
      networking for their first project.

### 7. Improve diagnostics and debugging

- [ ] Standardize runtime logging and error messages.
- [ ] Add optional debug drawing for colliders and object bounds.
- [ ] Add optional camera and navigation debug visualization.
- [ ] Add a small FPS, frame-time, object-count, and physics debug overlay.
- [ ] Ensure presentation and editor failures identify the responsible asset,
      object, component, or scene.

Only the diagnostics needed to understand common startup, loading, and export
failures are release blockers. More advanced debug visualization may move to
a later release if necessary.

### 8. Finish release metadata and repository setup

- [ ] Confirm package metadata and supported Python versions.
- [ ] Confirm the MIT license and packaged third-party font licenses.
- [ ] Add `CHANGELOG.md`.
- [ ] Add contribution instructions.
- [ ] Add a code of conduct if outside contributions will be accepted.
- [ ] Add issue and pull-request templates.
- [ ] Document supported operating systems and known limitations.
- [ ] Document the release and versioning process.
- [ ] Add repository topics, description, and documentation URL.

## First-release usability review

Before release, build at least two small games using only the documented public
API. Record every point where the user must:

- Import an internal module
- Mutate undocumented state
- Understand pyglet or Pymunk internals
- Manually repair generated scene data
- Repeat excessive setup
- Guess asset paths or lifecycle behavior

Fix release-blocking issues in the public API or document intentional advanced
escape hatches.

## Features deferred until after `0.1`

These features are valuable but should not delay the first alpha release:

- Sprite normal maps
- Built-in bloom, blur, CRT, vignette, and pixelation effects
- Shader and asset hot reloading
- Texture-atlas authoring and optimization
- Gamepad support and configurable input actions
- Music crossfading and audio buses
- Save slots and persisted user settings
- Beginner-friendly physics joints and constraints
- Render-to-texture surfaces and multiple camera viewports
- Localization and plugin discovery

## 2D lighting status

- [x] Add serializable `PointLight2D` and `SpotLight2D` components.
- [x] Add serialized scene ambient light.
- [x] Render scene color and light accumulation into separate targets.
- [x] Preserve the direct rendering path for scenes with no enabled lights.
- [x] Add explicit polygonal `ShadowOccluder2D` components and hard shadows.
- [x] Cull lights outside the camera viewport.
- [x] Support code, serialized scenes, editor fields, and runtime preview.
- [ ] Add per-renderer light layers and masks.
- [ ] Add optional sprite normal maps.
- [ ] Measure fill rate and add optional reduced-resolution light buffers.
- [ ] Add soft shadows only after hard-shadow behavior is stable.

## Release-candidate checklist

A release candidate is ready when:

- [ ] All release-blocking tasks above are complete.
- [ ] The full test suite passes on supported Python versions.
- [ ] Wheel and source distributions build successfully.
- [ ] The wheel installs and imports in a clean environment.
- [ ] The editor starts from the installed package.
- [ ] Starter projects run and export successfully.
- [ ] Documentation builds without warnings or broken references.
- [ ] GitHub Pages shows the documentation from the release commit.
- [ ] Scene and project fixtures load successfully.
- [ ] No known issue causes data loss or corrupts scene or project files.
- [ ] Known limitations are documented.
- [ ] The changelog and release notes are complete.
- [ ] The release tag matches the package version.

## Publishing `0.1.0-alpha`

1. Freeze feature development.
2. Run the complete release-candidate checklist.
3. Update the changelog and release notes.
4. Build the wheel and source distribution from a clean checkout.
5. Install and smoke-test the wheel.
6. Tag the release.
7. Publish the GitHub release and distribution artifacts.
8. Deploy the matching documentation to GitHub Pages.
9. Open the `0.2` milestone for deferred features and feedback from early
   adopters.