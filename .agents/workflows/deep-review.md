---
description: Comprehensive multi-pass code review combining security analysis, quality patterns, refactoring opportunities, and Python best practices. Use when reviewing a branch, a PR, or a set of changed files thoroughly.
---

# /deep-review — Multi-Pass Code Review

**Skills used:** `find-bugs`, `code-review-checklist`, `refactoring-patterns`, `python-project-patterns`, `error-handling-patterns`

## Steps

1. **Gather the diff.** Determine the review scope:
   - If the user specifies files, review those files.
   - If on a feature branch, diff against main: `git diff $(git merge-base HEAD main)...HEAD`
   - Otherwise, ask the user what to review.
   List all changed files before proceeding.

2. **Pass 1 — Security & Bugs.** Read the `find-bugs` skill. Execute the full 5-phase methodology (Input Gathering → Attack Surface Mapping → Security Checklist → Verification → Pre-Conclusion Audit) against the changed code. Record all findings with severity levels.

3. **Pass 2 — Code Quality.** Read the `code-review-checklist` skill. Evaluate the changes for:
   - Runtime errors, performance issues, side effects
   - Scientific assessment (if applicable): reproducibility, numeric stability, type safety
   - Design assessment: does this fit the existing architecture?
   - Test coverage: are new behaviors tested?

4. **Pass 3 — Python Patterns.** Read the `python-project-patterns` skill. Check the changed code for:
   - Type hints on all new/modified functions
   - Google-style docstrings on public APIs
   - Import organization (stdlib → third-party → local)
   - Appropriate use of dataclasses vs raw dicts

5. **Pass 4 — Error Handling.** Read the `error-handling-patterns` skill. Check for:
   - Bare `except:` clauses
   - Generic exception catching where specific types should be used
   - Missing `raise from` when wrapping exceptions
   - Fail-fast validation at boundaries
   - Appropriate use of logging vs print

6. **Pass 5 — Refactoring Opportunities.** Read the `refactoring-patterns` skill. Identify code smells in the changed code:
   - Long methods that should be extracted
   - Parameter creep (functions with 5+ arguments)
   - Complex conditionals that could use polymorphism or dispatch dicts
   - Duplicated code across the diff
   - Magic numbers or strings

7. **Synthesize the report.** Combine all findings into a single prioritized report:
   ```
   ## 🔴 Critical (must fix)
   ## 🟡 Important (should fix)
   ## 🟢 Suggestions (nice to have)
   ## 💡 Refactoring Opportunities
   ```
   De-duplicate findings that appeared in multiple passes. For each issue, include the file, line, problem, and a concrete fix suggestion.
