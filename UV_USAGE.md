# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

# Development with uv

This project now uses [uv](https://docs.astral.sh/uv/) for Python package and environment management.

## Setup

### Install uv

Follow the [official installation guide](https://docs.astral.sh/uv/getting-started/installation/):

```bash
# On macOS and Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# On Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Create and activate environment

```bash
# Create virtual environment and install dependencies
uv sync

# Activate the environment (optional, uv run handles this automatically)
source .venv/bin/activate  # On macOS/Linux
.venv\Scripts\activate     # On Windows
```

## Usage

### Running commands

Use `uv run` to execute commands within the project environment:

```bash
# Run Python
uv run python -c "import custom_components.rental_control"

# Run pytest
uv run pytest

# Run pytest with coverage
uv run pytest --cov=custom_components.rental_control

# Run any Python script
uv run python my_script.py
```

### Managing dependencies

```bash
# Add a dependency
uv add package-name

# Add a dev dependency
uv add --group dev package-name

# Add a test dependency
uv add --optional test package-name

# Update dependencies
uv sync

# Update a specific package
uv lock --upgrade-package package-name
```

### Testing

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_file.py

# Run with coverage
uv run pytest --cov=custom_components.rental_control

# Run with verbose output
uv run pytest -v
```

## Migration from requirements.txt

The project has been migrated from the traditional `requirements*.txt` and `setup.cfg` format to `pyproject.toml`. The old files are preserved for reference but are no longer used:

- `requirements.txt` → `[project.dependencies]` in `pyproject.toml`
- `requirements_dev.txt` → `[dependency-groups.dev]` in `pyproject.toml`
- `requirements_test.txt` → `[project.optional-dependencies.test]` in `pyproject.toml`
- `setup.cfg` → Various `[tool.*]` sections in `pyproject.toml`
- `mypy.ini` → `[tool.mypy]` in `pyproject.toml`

## Benefits of uv

- **Fast**: 10-100x faster than pip
- **Reliable**: Reproducible builds with uv.lock
- **Simple**: Single tool for package and environment management
- **Compatible**: Works with existing Python tools and workflows

## CI/CD Integration

For GitHub Actions:

```yaml
- name: Install uv
  uses: astral-sh/setup-uv@v5

- name: Install dependencies
  run: uv sync

- name: Run tests
  run: uv run pytest
```
