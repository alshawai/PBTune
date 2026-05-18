# Copilot Instructions

This project uses `.claude/skills/` as well as the **Graphify** tool for all AI agent conventions.

## graphify

For any question about this repo's architecture, structure, components, or how to add/modify/find
code, your **first tool call must be** to read `graphify-out/GRAPH_REPORT.md` (if it exists).

Triggers: "how do I…", "where is…", "what does … do", "add/modify a <component>",
"explain the architecture", or anything that depends on how files or classes relate.

After reading the report (and `graphify-out/wiki/index.md` for deep questions), answer from the
graph. Only read source files when (a) modifying/debugging specific code, (b) the graph lacks
the needed detail, or (c) the graph is missing or stale.

Type `/graphify` in Copilot Chat to build or update the graph.

---

When working on this project, read and follow:
1. `CLAUDE.md` at the project root for project overview and conventions
2. `.claude/skills/*/SKILL.md` for domain-specific patterns

Key skills to be aware of:
- `CLAUDE.md` — project overview, development commands, architecture
- `pbt-algorithm-patterns` — Population-Based Training algorithm structure
- `postgresql-knob-tuning` — PostgreSQL configuration best practices
- `research-project-management` — research documentation and organization
- `statistical-analysis` — best practices for analyzing experimental results
- `python-project-patterns` — types, docstrings, imports
- `error-handling-patterns` — exception handling
- `pytest-testing-patterns` — test structure
