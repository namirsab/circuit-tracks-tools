# Contributing

Thanks for your interest in contributing to circuit-tracks-tools!

## Getting started

1. Fork and clone the repo
2. Create a virtual environment and install in editable mode:

```bash
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -e ".[mcp,dev]"
```

3. Install the pre-commit hooks:

```bash
pre-commit install
```

4. Run the tests:

```bash
pytest
```

Tests mock MIDI I/O, so you don't need a Circuit Tracks connected to run them.

## Submitting changes

1. Create a branch for your change
2. Make your changes and add tests if applicable
3. Ensure all tests pass (`pytest`) and linting passes (`ruff check src/ tests/`)
4. Open a pull request with a clear description of what you changed and why

## Reporting bugs

Open a [GitHub issue](https://github.com/namirsab/circuit-tracks-tools/issues) with:

- What you expected to happen
- What actually happened
- Your Python version, OS, and Circuit Tracks firmware version
- Steps to reproduce

## Code style

- Code is linted and formatted with [ruff](https://docs.astral.sh/ruff/) — the pre-commit hook runs both automatically
- Keep it simple and readable
- Follow existing patterns in the codebase
- Add tests for new functionality
