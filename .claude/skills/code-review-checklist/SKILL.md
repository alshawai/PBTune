---
name: code-review-checklist
description: Perform code reviews. Use when reviewing pull requests, examining code changes, or providing feedback on code quality. Covers security, performance, testing, scientific reproducibility, and design review.
---

# Code Review Checklist

Follow these guidelines when reviewing code.

## Review Checklist

### Identifying Problems

Look for these issues in code changes:

- **Runtime errors**: Potential exceptions, null pointer issues, out-of-bounds access
- **Performance**: Unbounded O(n²) operations, N+1 queries, unnecessary allocations
- **Side effects**: Unintended behavioral changes affecting other components
- **Backwards compatibility**: Breaking API changes without migration path
- **Security vulnerabilities**: Injection, XSS, access control gaps, secrets exposure

### Scientific & Analytical Assessment
For projects involving experiments, calculations, or machine learning:
- **Reproducibility**: Are seeds set explicitly? Are experimental configs saved along with results?
- **Numeric stability**: Look for division by zero risks, large value/small value floating point issues, log(0).
- **Type safety**: Are complex data structures appropriately documented or typed (e.g., pandas DataFrame schemas, tensor shapes)?
- **Evaluation fairness**: Do benchmarks or comparisons use a level playing field without data leakage?

### Design Assessment

- Do component interactions make logical sense?
- Does the change align with existing project architecture?
- Are there conflicts with current requirements or goals?

### Test Coverage

Every significant PR should have appropriate test coverage:

- Functional tests for business logic
- Integration tests for component interactions
- Are edge cases (e.g., extreme values, empty lists) handled in testing?
- `pytest` specific: Are tests using `conftest.py` fixtures optimally instead of repeating setup?

Verify tests cover actual requirements and edge cases. Avoid excessive branching or looping in test code.

## Feedback Guidelines

### Tone

- Be polite and empathetic
- Provide actionable suggestions, not vague criticism
- Phrase as questions when uncertain: "Have you considered...?"

### Approval

- Approve when only minor issues remain
- Don't block PRs for stylistic preferences
- Remember: the goal is risk reduction, not perfect code

## Common Patterns to Flag

### Database

```python
# Bad: SQL injection risk
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
# Good: Parameterized query
cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
```

```python
# Bad: Catching all database errors generically
try:
    execute_query()
except Exception:
    pass
# Good: Handling specific driver errors
try:
    execute_query()
except sqlalchemy.exc.OperationalError:
    handle_connection_issue()
```

### Python Data Science / ML

```python
# Bad: Modifying DataFrame while chaining (SettingWithCopyWarning risk)
df[df['val'] > 0]['new_col'] = df['val'] * 2
# Good: Using vectorized assignments or .loc
df.loc[df['val'] > 0, 'new_col'] = df['val'] * 2
```

### Python Core Patterns

```python
# Bad: Mutable default arguments
def append_to(element, target=[]):
    target.append(element)
    return target

# Good: None as default
def append_to(element, target=None):
    if target is None:
        target = []
    target.append(element)
    return target
```

```python
# Bad: Catch-all exception ignoring errors
try:
    do_something()
except Exception:
    pass

# Good: Specific exception
try:
    do_something()
except ValueError:
    pass
```
