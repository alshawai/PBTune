---
name: find-bugs
description: Find bugs, security vulnerabilities, and code quality issues. Use when asked to review changes, find bugs, perform security review, or audit code.
---

# Find Bugs

Review changes on this branch for bugs, security vulnerabilities, and code quality issues.

## Phase 1: Complete Input Gathering

1. If reviewing a diff, get the FULL diff against the target branch.
2. If output is truncated, read each changed file individually until you have seen every changed line
3. List all files modified in this review context before proceeding

## Phase 2: Attack Surface Mapping

For each changed file, identify and list:

* All user inputs (files, arguments, configs, request params)
* All database queries
* All system commands and subprocess calls
* All authentication/authorization checks
* All external/network calls
* All cryptographic or serialization operations

## Phase 3: Security Checklist (check EVERY item for EVERY file)

* [ ] **Injection**: SQL, command, template injection
* [ ] **Execution Risks (Python specific)**: Use of `subprocess.Popen(..., shell=True)`, `eval()`, `exec()` on untrusted input?
* [ ] **Deserialization Risks (Python specific)**: Use of `pickle.load()` instead of `json`, or `yaml.load()` instead of `yaml.safe_load()`?
* [ ] **Path Traversal**: Unsafe file access, concatenating paths without validation?
* [ ] **XSS**: All outputs in templates properly escaped?
* [ ] **Authentication/Authorization**: Access control verified, not just auth?
* [ ] **Race conditions**: TOCTOU in any read-then-write patterns?
* [ ] **Cryptography**: Secure random, proper algorithms, no secrets in logs/code?
* [ ] **Information disclosure**: Error messages printing sensitive data, timing attacks?
* [ ] **DoS**: Unbounded operations, missing timeouts, resource exhaustion?
* [ ] **Business logic**: Edge cases, state machine violations, numeric overflow?

## Phase 4: Verification

For each potential issue:

* Check if it's already handled elsewhere in the changed code
* Search for existing tests covering the scenario
* Read surrounding context to verify the issue is real

## Phase 5: Pre-Conclusion Audit

Before finalizing, you MUST:

1. List every file you reviewed and confirm you read it completely
2. List every checklist item and note whether you found issues or confirmed it's clean
3. List any areas you could NOT fully verify and why
4. Only then provide your final findings

## Output Format

**Prioritize**: security vulnerabilities > bugs > code quality

**Skip**: stylistic/formatting issues

For each issue:

* **File:Line** - Brief description
* **Severity**: Critical/High/Medium/Low
* **Problem**: What's wrong
* **Evidence**: Why this is real (not already fixed, no existing test, etc.)
* **Fix**: Concrete suggestion
* **References**: OWASP, PEPs, or other standards if applicable

If you find nothing significant, say so - don't invent issues.

Do not make changes - just report findings. The user will decide what to address.
