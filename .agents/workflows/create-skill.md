---
description: How to create or update an AI agent skill using the SKILL.md format
---

# Create or Update Agent Skill

This workflow guides you through creating or modifying an Agent Skills-format skill for this project. Skills are stored in `.claude/skills/` and follow the universal SKILL.md standard compatible with Claude Code, Gemini Antigravity, and other AI agents.

## Prerequisites

- Understand what domain the skill covers
- Have reference material or codebase context available
- Know the trigger scenarios (when should the agent use this skill?)

## Steps

1. **Read the skill-creator reference** to understand best practices:

   ```
   View file: ~/.claude/skills/skill-creator/SKILL.md
   ```

2. **Capture intent** — Answer these questions:
   - What should this skill enable the agent to do?
   - When should this skill trigger? (what user phrases/contexts)
   - What's the expected behavior/output format?
   - What reference files does it need?

3. **Create the skill directory:**

   ```bash
   mkdir -p .claude/skills/<skill-name>/references
   ```

4. **Draft the SKILL.md** with this template:

   ```markdown
   ---
   name: <skill-name>
   description: <Clear, "pushy" description of what the skill does and when to trigger it>
   ---

   # Instructions

   [Procedural instructions for the agent to follow]

   ## Key Rules

   - [Rule 1 with rationale]
   - [Rule 2 with rationale]

   ## Reference Files

   - Read `references/<file>.md` when [specific condition]

   ## Examples

   - [Example usage patterns]
   ```

5. **Create reference files** in `references/` for detailed domain knowledge that would bloat the main SKILL.md (>500 lines).

6. **Test the skill** by asking the agent a question that should trigger it. Verify:
   - Does the agent find and load the skill?
   - Does the agent follow the instructions correctly?
   - Are the outputs consistent with expectations?

7. **Iterate** — Refine the skill based on test results. Focus on:
   - Generalizing instructions (avoid overfitting to examples)
   - Explaining "why" instead of rigid MUSTs
   - Keeping SKILL.md lean (<500 lines)
   - Moving detailed references to `references/`

8. **Commit the skill** to version control:
   ```bash
   git add .claude/skills/<skill-name>/
   git commit -m "feat(skills): add <skill-name> agent skill"
   ```

## Naming Conventions

- Skill names: `kebab-case` (e.g., `data-pipeline-patterns`)
- Directories: match the skill name exactly
- Reference files: descriptive kebab-case (e.g., `data-cleaning.md`)

## Skill Locations

- **Project-specific skills:** `.claude/skills/` (committed to git, shared with collaborators)
- **Personal global skills:** `~/.claude/skills/` (available across all projects, not committed)
