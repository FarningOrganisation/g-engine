import contextlib
import io
import unittest
from unittest.mock import patch

import run_tests


class TestRunnerTests(unittest.TestCase):
    def test_default_loads_only_core_modules(self) -> None:
        suite = run_tests.build_suite([])
        modules = {test.__class__.__module__ for test in run_tests.iter_cases(suite)}
        self.assertEqual(modules, set(run_tests.CORE_MODULES))

    def test_feature_file_module_class_and_method_selectors(self) -> None:
        for selector in (
            str(run_tests.TESTS / "test_trail_renderer.py"),
            "test_trail_renderer",
            "tests.test_trail_renderer",
            "test_trail_renderer.TrailRendererTests",
            "test_trail_renderer.TrailRendererTests.test_segments_expire_and_clear",
        ):
            with self.subTest(selector=selector):
                suite = run_tests.build_suite([selector])
                cases = list(run_tests.iter_cases(suite))
                self.assertGreater(len(cases), 0)
                self.assertTrue(all(
                    test.id().startswith("test_trail_renderer.TrailRendererTests.")
                    for test in cases
                ))
                if selector.endswith("test_segments_expire_and_clear"):
                    self.assertEqual(len(cases), 1)

    def test_core_plus_feature_deduplicates_overlapping_selections(self) -> None:
        core = run_tests.build_suite([])
        combined = run_tests.build_suite(
            ["test_vec2", "test_trail_renderer", "test_trail_renderer"], core=True,
        )
        feature = run_tests.build_suite(["test_trail_renderer"])
        self.assertEqual(
            combined.countTestCases(), core.countTestCases() + feature.countTestCases(),
        )

    def test_full_discovers_core_feature_and_runner_tests(self) -> None:
        suite = run_tests.build_suite([], full=True)
        modules = {test.__class__.__module__ for test in run_tests.iter_cases(suite)}
        self.assertTrue(set(run_tests.CORE_MODULES) <= modules)
        self.assertIn("test_trail_renderer", modules)
        self.assertIn("test_test_runner", modules)
        self.assertGreater(suite.countTestCases(), run_tests.build_suite([]).countTestCases())

    def test_invalid_selection_fails_instead_of_succeeding_empty(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_tests.main(["test_does_not_exist"]), 1)
        with self.assertRaisesRegex(ValueError, "inside"):
            run_tests.build_suite(["missing.py"])
        with patch.object(unittest.TestLoader, "loadTestsFromNames", return_value=unittest.TestSuite()):
            with self.assertRaisesRegex(ValueError, "No tests selected"):
                run_tests.build_suite(["test_vec2"])

    def test_full_rejects_selectors_and_core(self) -> None:
        for selectors, core in ((["test_vec2"], False), ([], True)):
            with self.subTest(selectors=selectors, core=core):
                with self.assertRaisesRegex(ValueError, "cannot be combined"):
                    run_tests.build_suite(selectors, core=core, full=True)

    def test_cli_reports_timings_and_propagates_failure(self) -> None:
        class Passing(unittest.TestCase):
            def runTest(self) -> None:
                pass

        class Failing(unittest.TestCase):
            def runTest(self) -> None:
                self.fail("intentional runner regression check")

        for case, expected in ((Passing(), 0), (Failing(), 1)):
            with self.subTest(expected=expected):
                output = io.StringIO()
                with (
                    patch.object(run_tests, "build_suite", return_value=unittest.TestSuite([case])),
                    contextlib.redirect_stderr(output),
                ):
                    self.assertEqual(run_tests.main([]), expected)
                self.assertIn("Slowest tests", output.getvalue())

    def test_durations_can_be_disabled_and_negative_values_are_rejected(self) -> None:
        output = io.StringIO()
        suite = unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
        with (
            patch.object(run_tests, "build_suite", return_value=suite),
            contextlib.redirect_stderr(output),
        ):
            self.assertEqual(run_tests.main(["--durations", "0"]), 0)
        self.assertNotIn("Slowest tests", output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                run_tests.main(["--durations", "-1"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
