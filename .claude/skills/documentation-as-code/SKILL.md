---
name: documentation-as-code
description: README standards, API docs, architecture decision records (ADRs), inline doc conventions. Use when formalizing documentation standards or writing technical docs.
---

# Documentation as Code

Treat documentation with the same rigor, version control, and automation as source code. Document to communicate *context, decisions, and usage*, not to echo code syntax.

## 1. The README Standard

Every repository or major sub-module MUST have a `README.md` addressing:
- **Title and One-Liner**: What this is and who it's for.
- **Getting Started**: 
  - Exact prerequisites (OS, packages).
  - Explicit, copy-pasteable installation steps.
- **Minimal Example**: A fast "Hello World" or equivalent usage snippet that proves the system works.
- **Architecture Overview**: High-level map of where critical components live.
- **Development Setup**: How to run tests and linters.

## 2. Architecture Decision Records (ADRs)

For significant technical decisions (e.g., "Why did we choose a microservice architecture instead of a monolith?"), do not rely on Slack history or pull request comments. Create an ADR in a `docs/architecture/decisions/` directory.

### ADR Format snippet:
- **Title**: [Short description of decision]
- **Status**: [Proposed | Accepted | Superseded]
- **Context**: [What forced this decision? What were the constraints?]
- **Decision**: [What we decided doing.]
- **Consequences**: [Positive and negative ramifications of this choice.]

## 3. Inline Documentation & Docstrings

- Use standard formats (e.g., Google or NumPy style) for all docstrings.
- **Comments explain WHY, code explains WHAT/HOW.**
  - *Bad*: `# Loop over workers`
  - *Good*: `# We shuffle workers here to prevent bias towards IDs evaluated earlier in the loop`
- If you find yourself writing a huge comment explaining a complex block of code, consider refactoring the code to be clearer or extracting it into a named function.

## 4. Self-Documenting Data Structures

- For configuration blobs or parameter dictionaries, do not document the structure solely in a comment.
- Use formal typing, `dataclasses`, or `Pydantic` models. The code structure itself becomes the strict documentation for what keys exist and what types they require.

## 5. Artifact generation

- For Python projects, aim for docs that can be automatically extracted using Sphinx, MkDocs, or pdoc.
- Keep source markdown files simple. Avoid overly complex HTML tables or styling that hinders readability in raw text.
