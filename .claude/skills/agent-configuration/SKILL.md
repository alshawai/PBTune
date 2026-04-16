---
name: agent-configuration
description: How to structure .claude/, CLAUDE.md, agent modes, instruction files, and skill directories for maximum AI agent effectiveness. Use when setting up a new project for AI-assisted development, configuring agent behavior, or organizing skills and instructions across multiple agents.
---

# Agent Configuration

Follow these practices when configuring AI coding agents (Claude Code, GitHub Copilot, Gemini) for a project.

## 1. Directory Structure

```
project-root/
├── CLAUDE.md                    # Root context file for Claude Code
├── .claude/
│   └── skills/                  # Project-level skills (SKILL.md format)
│       └── my-skill/
│           ├── SKILL.md         # Main instructions (YAML frontmatter + markdown)
│           └── references/      # Supporting docs loaded on demand
├── .github/
│   ├── copilot-instructions.md  # GitHub Copilot custom instructions
│   ├── agents/                  # Copilot agent modes
│   └── instructions/            # Copilot instruction files
├── .agents/
│   └── workflows/               # Gemini Antigravity workflows
└── .cursorrules                 # Cursor AI rules (if applicable)
```

### Personal vs Project Skills
- **`~/.claude/skills/`** — Personal skills available across all projects. General-purpose patterns (testing, commits, code review).
- **`<project>/.claude/skills/`** — Project-specific skills. Domain knowledge unique to this codebase (architecture patterns, data models, deployment procedures).

## 2. CLAUDE.md Best Practices

The `CLAUDE.md` file at the project root provides always-on context to Claude Code.

- **Keep it concise**: Claude reads this every conversation. Under 200 lines is ideal.
- **Include**:
  - Project overview (1–2 sentences)
  - Tech stack and key dependencies
  - Build/test/lint commands
  - Directory structure overview
  - Critical conventions (naming, error handling, testing patterns)
  - Links to important documentation
- **Exclude**:
  - Full API documentation (put in skills or references)
  - Lengthy tutorials (link to external docs)
  - Information that changes frequently (use dynamic sources instead)

## 3. Skill Authoring (SKILL.md)

### Frontmatter
```yaml
---
name: skill-name
description: What this skill does and WHEN to trigger it. Be specific and slightly "pushy" to ensure the agent uses it when relevant.
---
```

### Body Guidelines
- Keep under 500 lines. If longer, split into a main SKILL.md with `references/` files.
- Use imperative voice ("Do X", "Check Y").
- Explain the *why* behind instructions, not just the *what*. Agents perform better when they understand the reasoning.
- Include concrete examples with input/output pairs.
- Avoid excessive MUST/NEVER/ALWAYS — explain reasoning instead.

### Progressive Disclosure
1. **Name + Description** (~100 words) — always in agent context
2. **SKILL.md body** (<500 lines) — loaded when skill triggers
3. **references/** — loaded on demand for deep details

## 4. Multi-Agent Harmony

When using multiple AI agents on the same project:

- **Universal format**: The `SKILL.md` format with YAML frontmatter is understood by Claude Code, Gemini CLI, and Cursor. Prefer it as the single source of truth.
- **Agent-specific configs**: Keep `.github/copilot-instructions.md` and `.cursorrules` as thin wrappers that point to the shared skills, not as duplicate sources.
- **Avoid conflicts**: Don't give contradictory instructions across agent config files. If Claude Code says "use `ruff`" but Copilot instructions say "use `flake8`", confusion ensues.

## 5. Instruction Files vs Skills

| Aspect | Instruction Files | Skills |
|--------|------------------|--------|
| **When loaded** | Always attached to every conversation | On-demand, when description matches |
| **Best for** | Global conventions, formatting rules | Domain-specific workflows, complex procedures |
| **Length** | Short (<50 lines) | Medium (<500 lines, with references) |
| **Example** | "Always use Google-style docstrings" | "How to run and analyze benchmark experiments" |

## 6. Iterating on Agent Configuration

- **Start minimal**: Begin with a concise CLAUDE.md and a few critical skills. Add more as patterns emerge from actual agent interactions.
- **Track what fails**: When the agent makes repeated mistakes, that's a signal to add a skill or instruction addressing that pattern.
- **Version control everything**: All agent configuration lives in the repo alongside the code it supports.
