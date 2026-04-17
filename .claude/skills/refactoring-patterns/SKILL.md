---
name: refactoring-patterns
description: Extract method, replace conditional with polymorphism, introduce parameter object, dependency injection. Use when cleaning up messy code, addressing technical debt, or doing large scale refactors.
---

# Refactoring Patterns

Follow these practices when refactoring code to improve maintainability, reduce complexity, and manage technical debt without altering external behavior.

## Core Refactoring Principles

1. **Test-Driven Refactoring**: Never refactor without tests covering the behavior. Write or verify tests *before* changing the code.
2. **Small Steps**: Refactor in small, verifiable steps. Commit frequently (`git commit -m "refactor: ..."`).
3. **No New Features**: Do not mix refactoring with adding new features. Fix the structure first, commit, then add the feature.

## Common Code Smells & Solutions

### 1. Long Methods / Giant Functions
*Smell*: A function does too many things, spans hundreds of lines, or requires lots of scrolling.
*Solution*: **Extract Method / Extract Function**
- Break the logic into smaller, well-named helper functions.
- The name of the new function should explicitly state *what* it does, allowing the reader to skip reading the implementation.

### 2. Parameter Creep
*Smell*: A function signature takes an excessive number of parameters (e.g., `def run(a, b, c, d, e, f, g):`).
*Solution*: **Introduce Parameter Object / Dataclass**
- Group related parameters into a single configuration object or `dataclass`. This simplifies the signature and allows easy addition of future properties.

### 3. Complex Conditionals & Switch Statements
*Smell*: Long `if-elif-else` chains checking types or string flags (e.g., checking database versions or algorithmic strategies).
*Solution*: **Replace Conditional with Polymorphism / Strategy Pattern**
- Define a base interface and create subclasses for each condition branch.
- Alternatively, use a dictionary to map keys to specific handler functions.

### 4. Hard Dependencies and Global State
*Smell*: A component directly instantiates heavy dependencies inside its constructor (e.g., creating a highly customized database connection pool inside an evaluator object).
*Solution*: **Dependency Injection**
- Pass the instantiated dependency (or a factory interface) into the class. This makes the class vastly easier to test using mocks or lightweight instances.

### 5. Magic Strings and Numbers
*Smell*: Literal numbers like `0.95`, `5432`, or strings like `"failed"` scattered through the code.
*Solution*: **Replace with Constants or Enums**
- Define constants at the top of the file or use Python's `Enum` to give the values semantic meaning and prevent typo-based bugs.

### 6. Duplicated Code
*Smell*: The same exact code structure is repeated in three different places with minor variable changes.
*Solution*: **Pull Up Method / Extract Shared Logic**
- Move the duplicated logic into a shared utility function or pull it up into a common base class.
