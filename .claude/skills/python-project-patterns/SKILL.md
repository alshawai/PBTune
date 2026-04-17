---
name: python-project-patterns
description: Guidelines for Python project structure, types, formatting, and docstrings. Use when starting new python components, analyzing architecture, or reviewing code quality.
---

# Python Project Patterns

Follow these practices when writing or modifying Python code.

## Type Hinting (PEP 484)

- **Mandatory**: All new functions, methods, and classes MUST use type hints for all arguments and return values (unless the project's existing files explicitly omit them, in which case match the existing standard, but push for types).
- Prefer standard library collections for typing in modern Python (`list`, `dict` instead of `typing.Dict` starting Python 3.9).
- Use `Optional[Type]` (or `Type | None`) when `None` is a valid value.
- Use `Any` only as an absolute last resort.
- Example:
  ```python
  def calculate_discounts(items: list[OrderItem], rate: float = 0.10) -> dict[str, float]:
      pass
  ```

## Docstrings & Comments

- Use **Google style** docstrings for functions and classes.
- Clearly document:
  - `Args:`
  - `Returns:`
  - `Raises:` (if the function explicitly raises exceptions)
- Do not repeat the code in comments. Comments should explain the *why*, not the *what*.
- Include docstrings on all module files (`"""Module description."""` at the top).

## Formatting & Linting

- Assume the project uses consistent formatting. When making changes, match the surrounding style (or use standard tools like `black` or `ruff`).
- Stick to standard 88-100 character line limits.
- Organize imports using `isort` conventions:
  1. Standard library imports
  2. Third-party imports (e.g., `numpy`, `pandas`, `requests`)
  3. Local application imports
- Avoid wildcard imports (`from module import *`).

## Project Structure & Packaging

- Keep source code organized modularly (e.g., under a `src/` directory).
- Use `__init__.py` to explicitly export public APIs using `__all__ = [...]`.
- Keep environment configuration out of code (use `.env` files or specific configuration loaders).
- Dependencies should be cleanly listed in `requirements.txt` or `pyproject.toml`.

## Error Handling

- Raise specific exceptions (e.g., `ValueError`, `KeyError`) instead of generic `Exception`.
- Create custom exception classes for domain-specific errors (e.g., `InvalidOrderError`, `ExternalServiceError`).
- When catching exceptions to log and re-raise, use `raise from exc` to preserve the stack trace.

## Data Structures & Performance

- Python's `dict` and `list` are very fast, but for numerical/matrix operations or large arrays, use NumPy/Pandas.
- Use `dataclasses` (`from dataclasses import dataclass`) for simple data containers instead of generic dictionaries.
- Example: When dealing with tuning data or configuration parameters, use type-safe configuration dataclasses rather than loose key-value runtime dictionaries.
