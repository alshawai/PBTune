---
name: pytest-testing-patterns
description: Testing best practices and conventions using pytest. Use when generating tests, configuring conftest.py, mocking, evaluating testing practices, or dealing with large test suites.
---

# Pytest Testing Patterns

Follow these practices when writing, configuring, or reviewing `pytest` test suites. 

## Test Structure & Naming

1. **Test functions:** `test_<function_name>_<scenario>_<expected_behavior>`
   - Good: `test_normalize_metrics_negative_values_handled()`
   - Bad: `test_normalize_1()`
2. **Arrange-Act-Assert:** Use the AAA pattern clearly separated by blank lines within tests.
3. **Class grouping:** Group related tests into classes `Test<Component>` when they share setup logic or focus on the same class behavior.

## Fixtures & `conftest.py`

- Place shared fixtures in `conftest.py` at the appropriate directory level.
- Avoid repetitive setup code in individual tests. Leverage fixtures instead.
- Use the appropriate scope (`function`, `class`, `module`, `session`):
  - `session`: Resource-intensive setup (e.g., heavy mocking, shared caches).
  - `function`: Mutable state, or anything modified by tests.
- Always use `yield` instead of `return` in fixtures that require teardown logic.

## Parametrization

- Use `@pytest.mark.parametrize` for testing identical logic against multiple inputs.
- Prefer parametrization over writing loops inside test cases.
- Use `pytest.param("value", id="specific_case")` to provide readable identifiers for parameters.

## Mocking Strategies

- Use `unittest.mock` (specifically `patch` and `MagicMock`) for isolating behavior.
- Use `autospec=True` in `@patch` whenever possible to ensure mock signatures match the real objects.
- Prefer mocking at the invocation boundary (where it's imported) rather than where it's defined.
- Be extremely cautious when mocking standard library operations like `subprocess` or `datetime`; consider robust alternatives like `freezegun` for time if possible, or specialized fixtures for executing mock commands.

## Database Testing
For projects that interact with databases:
- Do not rely on fixed/existing system databases. Use dynamic test container instances (e.g., `testcontainers`) or isolated schema/tables.
- Reset the database state cleanly between tests via transaction rollbacks to prevent cross-test contamination.
- Test both the happy path and common database failure modes (e.g., connection errors, deadlocks, query timeouts).

## Testing Algorithmic / Stateless Logic

- *Stateless Functions*: For pure algorithms (e.g., sorting, ranking, scoring), tests should mock inputs and assert correct outputs without needing actual system/network interaction.
- Test for standard invariants:
  - Is the output size consistent with the input size?
  - Are values bounded within the expected range?
  - Are edge cases (empty inputs, boundary values) handled gracefully?

## Coverage & Quality

- Tests should prioritize execution speed. Mark slow tests (e.g., ones actually hitting databases) with `@pytest.mark.slow`.
- Coverage metrics are important, but do not chase code coverage at the expense of meaningful assertions.
- Do not test `builtins` or external libraries directly. Test how *our* code interacts with them.
