# Installation

## Requirements

- Python 3.11 or newer
- A desktop environment with OpenGL support for the game window and editor
- A virtual environment for each game project

The package distribution is called `g-engine`; the Python import is `g`.
The repository currently declares Python 3.11, 3.12, and 3.13 classifiers.
Cross-platform release certification remains work in progress.

## Install from a checkout

From the repository root:

```bash
python3 -m venv .venv
```

Activate it with `source .venv/bin/activate` on macOS/Linux, or
`.venv\Scripts\Activate.ps1` in Windows PowerShell.

```bash
python -m pip install .
```

For engine development, use `python -m pip install -e .`.

## Optional tools

```bash
python -m pip install ".[editor]"
python -m pip install ".[export]"
python -m pip install ".[all]"
```

`all` installs editor and export dependencies. Documentation tooling is a
separate `docs` extra.

The installed editor runs with either:

```bash
g-editor
python -m g_editor
```

`python -m g.editor` remains a compatibility entry point.

## Install from GitHub

Once the repository is published, replace these placeholders with its real
owner, repository, and revision:

```bash
python -m pip install \
  "g-engine[editor] @ git+https://github.com/OWNER/REPOSITORY.git@REVISION"
```

This does not require a PyPI release. Pin a tag or commit for reproducible
projects rather than relying on a moving branch.
