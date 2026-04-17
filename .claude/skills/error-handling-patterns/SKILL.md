---
name: error-handling-patterns
description: Exception hierarchies, retry patterns, graceful degradation, structured error logging. Use when standardizing error handling or improving robustness.
---

# Error Handling Patterns

Follow these practices to ensure robust software behavior in the face of inevitable runtime faults.

## 1. Exception Hierarchies

Do not rely solely on built-in generic exceptions (`Exception`, `ValueError`, `RuntimeError`) for complex systems.

- **Define Domain Errors**: Create a base exception class for the project or module, and subclass it for specific failure modes.
  ```python
  class PaymentServiceError(Exception):
      """Base exception for payment processing failures."""
      pass

  class PaymentTimeoutError(PaymentServiceError):
      """Raised when the payment gateway does not respond in time."""
      pass
  ```
- This allows consumers to catch `PaymentServiceError` to handle all domain errors, while ignoring `MemoryError` or `KeyboardInterrupt`.

## 2. Catching Exceptions Safely

- **Avoid Bare Excepts**: NEVER use a bare `except:`. It catches asynchronous generic events like `SystemExit` or `KeyboardInterrupt`.
- **Catch the Narrowest Exception Possible**: If you expect a `KeyError`, catch `KeyError`, not `Exception`.
- **Implicit vs Explicit**: If an operation is expected to fail occasionally (e.g., trying to read a cache), use `dict.get()` or bounds checks instead of intentionally causing and catching `KeyError`. Keep exceptions for *exceptional* state.

## 3. Retries and Exponential Backoff

For transient failures (like network endpoints, deadlocks, temporary DB unreachability):
- Do not immediately fail. Implement a retry loop.
- Use libraries like `tenacity` or `backoff` rather than writing custom `time.sleep()` loops.
- Always apply **jitter** along with exponential backoff to avoid the "thundering herd" problem where multiple retrying workers slam a recovering system simultaneously.

## 4. Error Preservations and Nesting

- When catching a low-level exception (e.g., `requests.ConnectionError`) and wrapping it in a domain exception (e.g., `ServiceUnavailableError`), use `raise from`.
  ```python
  try:
      response = fetch_data(...)
  except requests.ConnectionError as ex:
      raise ServiceUnavailableError("Failed to reach upstream API") from ex
  ```
  This preserves the original traceback for debugging.

## 5. Graceful Degradation / Failing Fast

- **Fail Fast (Initialization)**: Validate parameters and connections immediately on startup. If a required configuration is invalid, raise a loud error immediately rather than crashing 2 hours into a run.
- **Graceful Degradation (Runtime)**: In long-running tasks (like an overnight ML training pipeline), if a non-critical component fails (e.g., logging metrics to a remote dashboard), catch the error, log a warning locally, and continue the core workload. Do not crash the entire process.

## 6. Structured Error Logging

- Don't just `print("Error happened")`. Use the `logging` module.
- Use `logger.exception("Failed to connect")` inside an `except` block. This automatically captures and logs the stack trace alongside your message.
- Include context in logs (e.g., `logger.error("Failed to process order", extra={"order_id": order.id, "user": order.user})`).
