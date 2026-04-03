---
description: Systematic refactoring workflow that ensures test coverage before changes, applies structured patterns, validates after, and commits cleanly. Use when cleaning up code, reducing technical debt, or restructuring modules.
---

# /refactor — Systematic Refactoring

**Skills used:** `refactoring-patterns`, `python-project-patterns`, `pytest-testing-patterns`, `code-review-checklist`, `error-handling-patterns`, `git-conventional-commits`

## Steps

1. **Identify the target.** Ask the user what to refactor, or analyze the specified file/module. Read the `refactoring-patterns` skill and scan for code smells:
   - Long methods (>50 lines)
   - Parameter creep (>4 arguments)
   - Complex conditionals (nested if/elif chains)
   - Duplicated code blocks
   - Magic numbers/strings
   - Hard dependencies / global state
   Present a prioritized list of refactoring opportunities.

2. **Verify test coverage.** Read the `pytest-testing-patterns` skill. Before changing anything:
   - Check if tests exist for the code being refactored
   - If not, write tests that capture the current behavior first
   - Run the tests to confirm they pass: `python -m pytest <test_file> -v`
   - This is the safety net — never skip this step

3. **Plan the refactoring.** For each identified smell, propose a specific refactoring:
   - Long method → **Extract Function** (name it by what it does)
   - Parameter creep → **Introduce Parameter Object** (dataclass)
   - Complex conditionals → **Replace with Polymorphism** or dispatch dict
   - Duplicated code → **Extract Shared Logic** into a helper
   - Magic values → **Replace with Constants/Enums**
   - Hard dependencies → **Dependency Injection**
   Present the plan to the user for approval before proceeding.

4. **Apply refactoring in small steps.** For each refactoring:
   a. Make one structural change
   b. Run the test suite to verify no behavior changed
   c. If tests fail, revert and investigate
   d. If tests pass, move to the next change

5. **Polish the result.** Read the `python-project-patterns` skill and `error-handling-patterns` skill. Ensure the refactored code:
   - Has complete type hints on all changed functions
   - Has updated docstrings reflecting new structure
   - Uses the domain exception hierarchy (not bare `except:`)
   - Follows import conventions

6. **Self-review.** Read the `code-review-checklist` skill. Review your own changes:
   - Any runtime errors introduced?
   - Any performance regressions?
   - Any unintended side effects?
   - Is the refactored code genuinely clearer, or just different?

7. **Commit.** Read the `git-conventional-commits` skill. Commit with type `refactor`:
   ```
   refactor(scope): Brief description of structural change

   What was restructured and why. No behavior change.
   ```
   Commit each logical refactoring separately if multiple were applied.
