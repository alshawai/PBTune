# PBTune

**Evolutionary Auto-Tuning for PostgreSQL using Population-Based Training**

---

PBTune applies [Population-Based Training (PBT)](https://arxiv.org/abs/1711.09846), an evolutionary algorithm originally developed by DeepMind, to automatically optimize PostgreSQL database configuration.

## Quick Start

```bash
git clone https://github.com/alshawai/PBTune
cd PBTune
./scripts/bootstrap.sh
python -m src.tuners pbt --tier minimal --config rapid
```

## Documentation Structure

This documentation follows the [Diátaxis](https://diataxis.fr/) framework:

| Section | Purpose |
|---------|---------|
| [Getting Started](getting-started/setup.md) | Installation and first run |
| [Architecture](architecture/overview.md) | How and why PBTune is designed this way |
| [Guides](guides/adding-knobs.md) | Step-by-step how-to guides |
| [Reference](reference/cli.md) | CLI flags, schemas, and specifications |

## Links

- [GitHub Repository](https://github.com/alshawai/PBTune)
- [Contributing Guide](https://github.com/alshawai/PBTune/blob/main/CONTRIBUTING.md)
- [Changelog](https://github.com/alshawai/PBTune/blob/main/CHANGELOG.md)
