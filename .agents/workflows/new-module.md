---
description: Scaffold a new Python module with proper structure, types, tests, error handling, and documentation from the start. Use when creating a new component, service, or package within a Python project.
---

# /new-module — Python Module Scaffolding

**Skills used:** `python-project-patterns`, `pytest-testing-patterns`, `error-handling-patterns`, `documentation-as-code`

## Steps

1. **Clarify scope.** Ask the user:
   - What does this module do? (one sentence)
   - Where does it live in the project? (e.g., `src/analytics/`, `lib/utils/`)
   - What are its dependencies? (external libs, other internal modules)
   - Is it a library (imported by others) or a script (executed directly)?

2. **Create the module structure.** Read the `python-project-patterns` skill. Create:
   ```
   module_name/
   ├── __init__.py          # Public API exports via __all__
   ├── core.py              # Main logic
   ├── exceptions.py        # Domain-specific exception hierarchy
   ├── types.py             # Dataclasses / type definitions (if needed)
   └── _internal.py         # Private helpers (underscore prefix)
   ```

3. **Set up the exception hierarchy.** Read the `error-handling-patterns` skill. In `exceptions.py`, create:
   - A base exception class for this module (e.g., `class AnalyticsError(Exception)`)
   - 2–3 specific subclasses for expected failure modes
   - Ensure all exception classes have clear docstrings

4. **Write the core implementation.** Read the `python-project-patterns` skill. Ensure:
   - All functions have type hints (arguments + return)
   - Google-style docstrings with `Args:`, `Returns:`, `Raises:`
   - Imports organized: stdlib → third-party → local
   - Fail-fast validation at public function boundaries
   - `raise from` when wrapping lower-level exceptions
   - Module-level docstring explaining the module's purpose

5. **Create the test suite.** Read the `pytest-testing-patterns` skill. Create:
   ```
   tests/
   ├── conftest.py              # Shared fixtures
   └── test_module_name.py      # Tests following AAA pattern
   ```
   - Name tests: `test_<function>_<scenario>_<expected>`
   - Use `@pytest.mark.parametrize` for multi-input validation
   - Mock external dependencies with `autospec=True`
   - Include at least: happy path, edge case, error case for each public function

6. **Write the documentation.** Read the `documentation-as-code` skill. Create:
   - Module-level docstring (already done in step 4)
   - Entry in the project's README or docs index explaining what this module does
   - If the module makes a significant design decision, create an ADR

// turbo
7. **Verify.** Run `python -m pytest tests/test_module_name.py -v` to ensure all tests pass. Report the results.
