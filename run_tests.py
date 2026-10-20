"""Run a small core suite, selected feature tests, or the full regression suite."""

import argparse
from pathlib import Path
import sys
from time import perf_counter
import unittest
from collections.abc import Iterator, Sequence


ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"
CORE_MODULES = (
    "test_vec2",
    "test_component_lookup",
    "test_gameobject_update",
    "test_timing",
    "test_scenes",
    "test_collision_callbacks",
)


def test_name(selector: str) -> str:
    if selector.endswith(".py") or "/" in selector or "\\" in selector:
        path = Path(selector).resolve()
        if path.parent != TESTS or path.suffix != ".py" or not path.is_file():
            raise ValueError(f"Expected a Python test file directly inside {TESTS}: {selector}")
        return path.stem
    return selector.removeprefix("tests.")


def iter_cases(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from iter_cases(test)
        else:
            yield test


def build_suite(
    selectors: Sequence[str], *, core: bool = False, full: bool = False,
) -> unittest.TestSuite:
    if full and (core or selectors):
        raise ValueError("--full cannot be combined with --core or test selectors")
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    loader = unittest.TestLoader()
    if full:
        suite = loader.discover(str(TESTS), pattern="test_*.py")
    else:
        names = list(CORE_MODULES) if core or not selectors else []
        names.extend(test_name(selector) for selector in selectors)
        suite = loader.loadTestsFromNames(names)
    unique = {}
    for test in iter_cases(suite):
        unique.setdefault(test.id(), test)
    if not unique:
        raise ValueError("No tests selected")
    return unittest.TestSuite(unique.values())


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.timings: list[tuple[float, str]] = []
        self._started = 0.0

    def startTest(self, test: unittest.TestCase) -> None:
        super().startTest(test)
        self._started = perf_counter()

    def stopTest(self, test: unittest.TestCase) -> None:
        self.timings.append((perf_counter() - self._started, test.id()))
        super().stopTest(test)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tests", nargs="*",
        help="Test files or dotted module/class/method names; without selectors, run core",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--core", action="store_true", help="Include core with selected tests")
    group.add_argument("--full", action="store_true", help="Discover all tests, including slow tests")
    parser.add_argument("--durations", type=int, default=5, help="Show N slowest tests (0 disables)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-f", "--failfast", action="store_true")
    args = parser.parse_args(argv)
    if args.durations < 0:
        parser.error("--durations must be non-negative")
    try:
        suite = build_suite(args.tests, core=args.core, full=args.full)
    except ValueError as error:
        parser.error(str(error))
    mode = "full" if args.full else "core + selected" if args.core and args.tests else (
        "selected" if args.tests else "core"
    )
    print(f"Running {mode} suite ({suite.countTestCases()} tests)", file=sys.stderr)
    runner = unittest.TextTestRunner(
        verbosity=2 if args.verbose else 1,
        failfast=args.failfast,
        resultclass=TimedResult,
    )
    result = runner.run(suite)
    if args.durations:
        print("\nSlowest tests (individual test time, including setUp/tearDown):", file=sys.stderr)
        for seconds, name in sorted(result.timings, reverse=True)[:args.durations]:
            print(f"{seconds:8.3f}s  {name}", file=sys.stderr)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
