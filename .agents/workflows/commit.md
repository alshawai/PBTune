---
description: Smart commit with pre-flight quality checks. Combines bug detection, self-review, and conventional commit formatting into a single safe workflow.
---

# /commit — Smart Commit Workflow

**Skills used:** `git-conventional-commits`, `find-bugs`, `code-review-checklist`

## Steps

1. **Check the current branch.** Run `git branch --show-current`. If on `main` or `master`, create a feature branch first — do not ask the user, just derive an appropriate name from the staged changes and create it.

2. **Review what's staged.** Run `git diff --cached --stat` to see what files are staged. If nothing is staged, run `git status` and ask the user what to stage, or suggest `git add -A` if the changes look intentional.

3. **Pre-commit bug scan.** Read the `find-bugs` skill. Apply its Phase 2 (Attack Surface Mapping) and Phase 3 (Security Checklist) specifically to the staged diff (`git diff --cached`). Focus only on **Critical** and **High** severity issues. If any are found, report them to the user and ask whether to proceed or fix first.

4. **Quick self-review.** Read the `code-review-checklist` skill. Scan the staged diff for the "Identifying Problems" checklist items (runtime errors, performance, side effects, backwards compatibility). Report anything found as a brief summary — not a full review, just a heads-up.

5. **Compose the commit message.** Read the `git-conventional-commits` skill. Based on the staged changes:
   - Determine the correct commit type (`feat`, `fix`, `refactor`, `test`, `docs`, etc.)
   - Infer an appropriate scope from the changed files/modules
   - Write a subject line (imperative, capitalized, no period, ≤70 chars)
   - Write a body explaining **what** and **why**
   - Add `Co-Authored-By` footer if the agent generated the changes

6. **Present and confirm.** Show the user the proposed commit message and a summary of any issues found in steps 3–4. Ask for confirmation before committing.

// turbo
7. **Commit.** Execute the commit using the multi-`-m` flag pattern (no escaped `\n`). Then show the result with `git log -1 --oneline`.
