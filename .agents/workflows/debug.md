---
description: Systematic debugging workflow using hypothesis-driven investigation, targeted testing, and proper error handling fixes. Use when tracking down a bug, investigating unexpected behavior, or diagnosing test failures.
---

# /debug — Systematic Debugging

**Skills used:** `find-bugs`, `error-handling-patterns`, `pytest-testing-patterns`, `python-project-patterns`

## Steps

1. **Reproduce the problem.** Before investigating, establish a reliable reproduction:
   - Get the exact error message, traceback, and command that triggered it
   - Reproduce it yourself — if you can't reproduce it, you can't verify the fix
   - Reduce to the minimal reproduction case (smallest input, fewest steps)

2. **Form a hypothesis.** Based on the error and traceback, form 1–3 hypotheses about the root cause. Rank them by likelihood. Do NOT start changing code yet.

   Common hypothesis categories:
   - **Data issue**: Unexpected input (null, wrong type, out of range)
   - **State issue**: Race condition, stale cache, mutation of shared state
   - **Logic issue**: Off-by-one, wrong comparison operator, missing edge case
   - **Environment issue**: Missing dependency, wrong version, config mismatch

3. **Investigate the most likely hypothesis.** Read the code around the error location:
   - Read the function where the error occurs (full function, not just the line)
   - Read the caller that passes data to this function
   - Check: What are the actual values at the crash point? Add targeted logging or use a debugger.

4. **Scan for related issues.** Read the `find-bugs` skill. Apply the security checklist and attack surface mapping to the area around the bug. Often a bug appears because the code has broader issues (missing validation, bare excepts hiding earlier failures, etc.).

5. **Fix the root cause, not the symptom.** Read the `error-handling-patterns` skill. When applying the fix:
   - Fix the actual root cause, not just the crash point
   - Add proper validation at boundaries (fail fast)
   - Use specific exception types, not bare `except:`
   - Preserve tracebacks with `raise from`
   - Add logging at the appropriate level (`logger.warning` for recoverable, `logger.error` for failures)

6. **Write a regression test.** Read the `pytest-testing-patterns` skill. Before considering the bug fixed:
   - Write a test that fails WITHOUT the fix (proving the bug existed)
   - Apply the fix and verify the test passes
   - Name the test descriptively: `test_<function>_<bug_scenario>_<expected_behavior>`
   - Add the minimal reproduction case as a parametrized test input

// turbo
7. **Verify.** Run the full relevant test suite: `python -m pytest <test_dir> -v`. Confirm:
   - The new regression test passes
   - No existing tests broke
   - The original reproduction case no longer triggers the error

8. **Document.** Add a brief inline comment near the fix explaining WHY the bug occurred (not just what the fix does). This prevents future developers from "cleaning up" the fix by removing it.
