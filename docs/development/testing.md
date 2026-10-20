# Testing and compatibility

Run focused tests for a feature:

```bash
python run_tests.py tests.test_timing tests.test_resources
```

Run the complete regression suite before a release:

```bash
python run_tests.py --full
```

The repository uses `unittest`. Graphics-related tests may require a working
display/OpenGL environment or the appropriate platform headless setup; a
successful documentation build alone does not certify rendering behavior.

## Public API contract

The root `g` exports are the starting public surface. Underscore-prefixed
names and editor implementation modules are not a stability promise.
The package is currently alpha; APIs and serialized formats may change before
a stable compatibility policy is adopted.

The first release targets desktop Python. The package metadata requires
Python 3.11 or later and declares 3.11, 3.12, and 3.13 classifiers.
Actual release support should be backed by CI and installation/export smoke
tests on each advertised platform.

## Documentation changes

- Keep public docstrings and guides aligned with the implementation.
- Run the strict site build to validate API references and internal links.
- Run API documentation coverage tests after adding root exports.
- Treat docstring-only changes as behavior-preserving: signatures and
  executable statements should not change.
