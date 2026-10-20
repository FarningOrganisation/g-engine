from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile


class ProjectTrustStore:
    def __init__(self, filename: str | Path | None = None) -> None:
        self.path = (
            Path(filename).expanduser().resolve()
            if filename is not None
            else Path.home() / ".g-editor" / "trusted-projects.json"
        )

    def is_trusted(self, project_root: str | Path) -> bool:
        return str(Path(project_root).resolve()) in self._read()

    def trust(self, project_root: str | Path) -> None:
        projects = self._read()
        projects.add(str(Path(project_root).resolve()))
        self._write(projects)

    def revoke(self, project_root: str | Path) -> None:
        projects = self._read()
        projects.discard(str(Path(project_root).resolve()))
        self._write(projects)

    def _read(self) -> set[str]:
        if not self.path.is_file():
            return set()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"could not read project trust store: {error}"
            ) from error
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("trusted_projects"), list)
            or not all(
                isinstance(item, str)
                for item in raw["trusted_projects"]
            )
        ):
            raise ValueError("project trust store is invalid")
        return set(raw["trusted_projects"])

    def _write(self, projects: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            {"trusted_projects": sorted(projects)},
            indent=2,
            ensure_ascii=False,
        )
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        temporary_path.replace(self.path)
