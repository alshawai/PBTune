---
name: Bug Report
about: Report a bug or unexpected behavior
title: '[BUG] '
labels: bug
assignees: ''
---

## Bug Description

A clear and concise description of what the bug is.

## Steps to Reproduce

1. Run command: `python -m src.tuners pbt ...`
2. Configure: ...
3. Observe: ...

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened.

## Environment

- **OS**: [e.g., Windows 11, Ubuntu 22.04, macOS Sonoma]
- **Python**: [e.g., 3.11.5]
- **PostgreSQL**: [e.g., 14.10]
- **Hardware**: [e.g., 8-core CPU, 32GB RAM, NVMe SSD]

## Error Messages

```
Paste full error traceback here
```

## Logs

Attach the relevant `pbt_tuning_*.html` from `results/{olap,oltp/<workload>}/pbt_runs/<tier>/` or relevant log excerpts.

## Additional Context

Any other relevant information (custom configurations, workload files, etc.).

## Checklist

- [ ] I have searched existing issues to ensure this is not a duplicate
- [ ] I have included complete reproduction steps
- [ ] I have attached relevant logs or error messages
- [ ] I have specified my environment details
