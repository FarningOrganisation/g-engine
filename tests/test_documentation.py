import ast
import inspect
from pathlib import Path
import re
import textwrap
import unittest
from unittest.mock import patch

import g


class PublicDocumentationTests(unittest.TestCase):
    def test_exported_classes_and_functions_have_explicit_docstrings(self) -> None:
        for name in g.__all__:
            value = getattr(g, name)
            if not (inspect.isclass(value) or inspect.isfunction(value)):
                continue
            with self.subTest(symbol=name):
                source = textwrap.dedent(inspect.getsource(value))
                definition = ast.parse(source).body[0]
                assert isinstance(definition, (ast.ClassDef, ast.FunctionDef))
                self.assertTrue(
                    ast.get_docstring(definition),
                    f"g.{name} needs an explicit public API docstring",
                )
                if isinstance(definition, ast.ClassDef):
                    for member in definition.body:
                        if (
                            isinstance(
                                member, (ast.FunctionDef, ast.AsyncFunctionDef)
                            )
                            and not member.name.startswith("_")
                        ):
                            with self.subTest(member=member.name):
                                self.assertTrue(
                                    ast.get_docstring(member),
                                    f"g.{name}.{member.name} needs a docstring",
                                )

    def test_first_game_tutorial_runs_without_starting_event_loop(self) -> None:
        filename = (
            Path(__file__).resolve().parents[1]
            / "docs/getting-started/first-game.md"
        )
        example = re.search(
            r"```python\n(.*?)\n```",
            filename.read_text(encoding="utf-8"),
            re.DOTALL,
        )
        assert example is not None
        namespace: dict[str, object] = {}
        with patch.object(g.Game, "run", autospec=True) as run:
            exec(compile(example.group(1), str(filename), "exec"), namespace)
        run.assert_called_once()
        update = namespace["update"]
        assert callable(update)
        update(1 / 60)

    def test_authored_python_examples_have_valid_syntax(self) -> None:
        docs = Path(__file__).resolve().parents[1] / "docs"
        for filename in docs.rglob("*.md"):
            python_lines: list[str] | None = None
            for line_number, line in enumerate(
                filename.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line == "```python":
                    python_lines = []
                elif line == "```" and python_lines is not None:
                    with self.subTest(
                        file=str(filename.relative_to(docs)),
                        ending_line=line_number,
                    ):
                        ast.parse("\n".join(python_lines))
                    python_lines = None
                elif python_lines is not None:
                    python_lines.append(line)


if __name__ == "__main__":
    unittest.main()
