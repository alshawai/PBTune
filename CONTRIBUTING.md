# Contributing to PBT PostgreSQL Tuning

See also: [Documentation Index](./docs/README.md)

Thank you for your interest in contributing to this academic research project! This document outlines guidelines for collaboration and contributions.

## 🎓 Project Status

This is an **academic research project** under active development by the Data-Vanta Research Group. The codebase is shared for:

- **Reproducibility**: Enabling verification of published results
- **Education**: Teaching evolutionary optimization and database systems
- **Research Extension**: Facilitating further academic research

## 📋 Types of Contributions

### ✅ Welcome Contributions

We appreciate the following types of contributions:

#### 1. Bug Reports
- **Issues**: Report bugs via [GitHub Issues](https://github.com/Data-Vanta/ai-database-optimization/issues)
- **Include**: Reproduction steps, environment details, error messages, expected vs. actual behavior
- **Label**: Use `bug` label

#### 2. Documentation Improvements
- **Typo fixes**: Spelling, grammar, formatting
- **Clarifications**: Improved explanations, additional examples
- **Missing docs**: Undocumented features or edge cases
- **Label**: Use `documentation` label

#### 3. Performance Benchmarks
- **Share results**: Your PBT optimization results on different hardware/workloads
- **Comparative studies**: Comparisons with other tuning methods
- **Label**: Use `benchmarks` label

#### 4. Research Extensions
- **Collaborations**: Contact maintainers for research partnerships
- **Novel applications**: New workload types, database systems, optimization objectives
- **Academic papers**: Cite this work and share your publications with us

### ⚠️ Limited Acceptance

The following require prior discussion:

#### 1. Core Algorithm Changes
- Changes to PBT algorithm implementation
- Evolution strategies (exploit/explore)
- Convergence criteria

**Why**: These are research decisions requiring theoretical justification.

**Process**: Open a discussion issue first, including:
- Academic references supporting the change
- Theoretical analysis or proof
- Experimental validation

#### 2. New Features
- New workload types
- Additional optimization objectives
- Alternative sampling strategies

**Process**: Open a feature request issue with:
- Use case and motivation
- Design proposal
- Compatibility considerations

### ❌ Not Accepting

- **Production-focused changes**: This is research software, not production-grade
- **Commercial features**: See [Commercial Licensing](./LICENSE)
- **Unsolicited major refactors**: Coordinate with maintainers first

## 🔬 Research Collaboration

### For Academic Researchers

If you're interested in:
- Extending this work for your research
- Reproducing results for comparison
- Building upon this implementation

**Contact us:**
1. Open a GitHub issue with `collaboration` label
2. Include: Your affiliation, research interests, proposed extension
3. Email: imalwaysforlife@gmail.com for sensitive matters
4. We'll discuss collaboration terms and co-authorship

### Citation Requirements

All research using this software **must cite**:
- The original PBT paper (Jaderberg et al., 2017)
- This implementation (see [README.md](./README.md#citation))

Failure to provide proper attribution violates the [license](./LICENSE).

## 📝 How to Contribute

### Step 1: Fork and Clone

```bash
git clone https://github.com/YOUR-USERNAME/ai-database-optimization.git
cd ai-database-optimization
```

### Step 2: Create a Branch

```bash
git checkout -b fix/your-bug-fix
# or
git checkout -b docs/your-documentation-improvement
```

Branch naming conventions:
- `fix/`: Bug fixes
- `docs/`: Documentation changes
- `bench/`: Performance benchmarks
- `research/`: Research extensions (coordinate with maintainers first)

### Step 3: Make Changes

- Follow existing code style (PEP 8 for Python)
- Add docstrings for new functions/classes
- Update documentation if needed
- Keep commits focused and atomic

### Step 4: Test Your Changes

```bash
# Install dev dependencies (if not already installed)
pip install -r requirements-dev.txt

# Deterministic local validation (same gates as CI)
make lint
make typecheck
make test

# One-command full gate
make check-all
```

**Recommended**: Use project-local Python from `.venv` for all commands.

```bash
.venv/bin/python -m src.tuner.main --help
```

### Local Validation Command Matrix

```bash
# Install all runtime + dev tools
make install-dev

# Lint (strict gate)
make lint

# Type checks (evaluation + utils + scripts)
make typecheck

# Unit tests
make test

# All gates
make check-all
```

If `make` is unavailable on your platform, run the equivalent commands directly:

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy src/evaluation src/utils src/scripts
.venv/bin/python -m pytest -q tests/unit
```

### Step 5: Commit

```bash
git add .
git commit -m "Fix: Brief description of the change

Detailed explanation if needed. Reference issue #123 if applicable."
```

Commit message format:
- **Fix**: Bug fixes
- **Docs**: Documentation changes
- **Bench**: Benchmark results
- **Refactor**: Code restructuring (no functional change)
- **Research**: Research extensions

### Step 6: Push and Create Pull Request

```bash
git push origin fix/your-bug-fix
```

Create a pull request on GitHub with:
- **Clear title**: Brief description of the change
- **Description**: What, why, and how
- **Testing**: How you verified the change works
- **Related issues**: Link to relevant issues

### Step 7: Code Review

- Maintainers will review your PR
- Address feedback promptly
- Be patient—this is academic research, not commercial software
- Be respectful and constructive

## 📐 Code Style Guidelines

### Python Code

- **PEP 8**: Follow Python style guide
- **Type hints**: Use type annotations for function signatures
- **Docstrings**: NumPy/Google style docstrings
- **Line length**: 100 characters max (120 for docstrings)

Example:

```python
def compute_score(
    metrics: PerformanceMetrics,
    config: MetricConfig
) -> float:
    """
    Compute composite performance score from metrics.
    
    Parameters
    ----------
    metrics : PerformanceMetrics
        Raw performance measurements
    config : MetricConfig
        Metric weighting configuration
    
    Returns
    -------
    float
        Composite score (higher is better)
    
    Notes
    -----
    Uses workload-specific weighting: OLTP prioritizes latency,
    OLAP prioritizes throughput.
    """
    # Implementation here
    pass
```

### Documentation

- **Markdown**: Use proper formatting (headers, lists, code blocks)
- **Links**: Use relative links for internal docs
- **References**: Cite academic papers in IEEE/ACM format
- **Examples**: Include runnable code examples where applicable

## 🧪 Testing Status

Current baseline quality gates:

- **Unit tests**: `tests/unit` (run with `make test`)
- **Linting**: `ruff` strict checks (run with `make lint`)
- **Type checks**: `mypy` on `src/evaluation`, `src/utils`, and `src/scripts` (run with `make typecheck`)
- **CI**: Pull-request workflow at `.github/workflows/ci.yml` runs all gates

### Common Failure Triage

- **Docker daemon not running**:
    Start Docker before running workflows that require containerized evaluation. If you are only validating unit tests and static checks, run `make check-all`.
- **Missing PostgreSQL binaries (`pg_ctl`, `initdb`)**:
    Install PostgreSQL server tools and ensure binaries are available in `PATH`.
- **Missing Python dev tools (`pytest`, `ruff`, `mypy`)**:
    Reinstall with `make install-dev` or `pip install -r requirements-dev.txt`.
- **Fixture/data path issues**:
    Confirm expected repository paths exist (`workloads/`, `data/expert_defined_knobs/`) and run commands from the repository root.

## 📚 Documentation Standards

All contributions should maintain documentation consistency:

### Code Documentation

- **Module docstrings**: Explain purpose, architecture, key classes
- **Class docstrings**: Attributes, responsibilities, usage examples
- **Function docstrings**: Parameters, return values, exceptions, notes

### User Documentation

- **README.md**: High-level overview, quick start, examples
- **docs/*.md**: Detailed technical documentation
- **CHANGELOG.md**: Track major changes (future)

## 🐛 Bug Report Template

When reporting bugs, include:

```markdown
**Description**: Brief summary of the bug

**Steps to Reproduce**:
1. Run command: `python -m src.tuner.main ...`
2. Observe behavior: ...

**Expected Behavior**: What should happen

**Actual Behavior**: What actually happened

**Environment**:
- OS: Windows 11 / Ubuntu 22.04 / macOS Sonoma
- Python: 3.11.5
- PostgreSQL: 14.10
- Hardware: CPU, RAM, Storage type

**Error Messages**:
[Paste full error traceback here]

**Additional Context**: Any other relevant information
```

## 💡 Feature Request Template

```markdown
**Problem**: What problem does this solve?

**Proposed Solution**: How should it work?

**Alternatives Considered**: Other approaches you've thought about

**Research References**: Academic papers supporting this feature

**Use Cases**: Concrete examples of when this would be useful

**Compatibility**: Impact on existing functionality
```

## 🤝 Code of Conduct

### Be Respectful

- Treat all contributors with respect and professionalism
- Value diverse perspectives and experiences
- Accept constructive criticism gracefully

### Be Constructive

- Provide evidence-based feedback (cite papers, show data)
- Offer solutions, not just complaints
- Focus on improving the research, not personal attacks

### Be Patient

- Maintainers are academic researchers with other responsibilities
- Response times may vary (days to weeks, not hours)
- Complex questions require time for thorough answers

### Be Honest

- Disclose conflicts of interest
- Admit when you don't know something
- Give credit where credit is due

## 📧 Contact

- **GitHub Issues**: Primary communication channel
- **Email**: [Ebrahim ElShawa](mailto:imalwaysforlife@gmail.com) (for sensitive matters)
- **Discussions**: Use GitHub Discussions for open-ended questions

## 📄 License

By contributing, you agree that your contributions will be licensed under the [Academic Research License](./LICENSE).

---

Thank you for contributing to advancing the state-of-the-art in autonomous database systems! 🚀
