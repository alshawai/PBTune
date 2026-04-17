---
name: prompt-engineering
description: Prompt design patterns, chain-of-thought, few-shot, structured output, context window management. Use when crafting prompts for LLMs, designing system prompts, building agent instructions, or optimizing prompt quality.
---

# Prompt Engineering

Follow these practices when designing prompts for large language models, whether for interactive use, system prompts, agent instructions, or automated pipelines.

## 1. Core Principles

- **Be Specific**: Vague prompts produce vague outputs. Instead of "summarize this," say "summarize this in 3 bullet points, each under 20 words, focusing on the technical contributions."
- **Provide Context**: Tell the model what it is, what it's working with, and what the output should look like. Context reduces ambiguity and hallucination.
- **Separate Instructions from Data**: Use clear delimiters (e.g., `---`, XML tags, markdown headers) to distinguish the task description from the input data being processed.

## 2. Prompting Techniques

### Zero-Shot
- Give the instruction directly with no examples.
- Works well for simple, well-defined tasks.
- Example: "Classify the following review as positive or negative: ..."

### Few-Shot
- Provide 2–5 input/output examples before the actual task.
- Choose examples that cover edge cases, not just the easy path.
- Ensure examples are consistent in format — the model will mirror the pattern.

### Chain-of-Thought (CoT)
- Ask the model to "think step by step" or "show your reasoning."
- Dramatically improves performance on math, logic, and multi-step reasoning tasks.
- Can be combined with few-shot: show examples that include the reasoning steps.

### Self-Consistency
- Run the same prompt multiple times (with temperature > 0) and take the majority answer.
- Useful for tasks where the model is uncertain — the consistent answer is more likely correct.

## 3. Structured Output

When you need machine-parseable output:

- **Specify the format explicitly**: "Respond with a JSON object containing keys: `name` (string), `score` (float between 0 and 1), `tags` (array of strings)."
- **Provide a schema or example**: Show the exact JSON/YAML/CSV shape you expect.
- **Use XML tags for sections**: `<analysis>...</analysis>` and `<recommendation>...</recommendation>` help the model organize long outputs and make parsing reliable.
- **Validate output programmatically**: Always parse and validate model output in code. Don't assume it will be perfectly formatted every time.

## 4. System Prompts and Agent Instructions

- **Role definition**: Start with who the agent is and what it does. "You are a senior Python developer reviewing pull requests for security and correctness issues."
- **Behavioral constraints**: Define what the agent should and should not do. "Never modify files without explicit user approval."
- **Output format**: Specify how the agent should communicate. "When reporting issues, use the format: File:Line — Severity — Description."
- **Tone**: If relevant, guide tone. "Be direct and concise. Avoid filler phrases."

## 5. Context Window Management

- **Front-load important context**: Models pay more attention to the beginning and end of prompts. Put critical instructions at the top.
- **Trim irrelevant context**: Don't dump an entire codebase into the prompt. Include only the files and sections relevant to the task.
- **Summarize when needed**: If context is too long, summarize earlier sections and include full detail only for the most recent/relevant parts.
- **Use references, not repetition**: Instead of repeating the same instruction in multiple places, state it once clearly and reference it.

## 6. Iteration and Debugging

- **Start simple, add complexity**: Begin with a minimal prompt. If the output isn't right, add constraints, examples, or context incrementally.
- **Diagnose before patching**: When a prompt produces bad output, understand *why* before adding more instructions. Sometimes the issue is ambiguity, not missing rules.
- **Avoid prompt bloat**: Every additional instruction adds cognitive load. Remove instructions that aren't pulling their weight.
- **Test with diverse inputs**: A prompt that works for one input may fail on edge cases. Test with varied examples before deploying.

## 7. Anti-Patterns to Avoid

- **Threatening the model**: "You MUST do this or else" doesn't help. Explain *why* something matters instead.
- **Contradictory instructions**: "Be concise" + "Include all relevant details" creates tension. Resolve contradictions explicitly.
- **Over-constraining**: Too many rigid rules can make output stiff and low-quality. Give the model room to use its judgment where appropriate.
- **Ignoring the model's strengths**: Don't ask the model to do arithmetic or count characters precisely — use code for that. Use the model for reasoning, synthesis, and language tasks.
