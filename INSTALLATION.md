# QuAIL Installation & Distribution Guide

## For Users: Installing QuAIL

### Method 1: Install from Source (Editable Mode)

This is the recommended method for development and testing.

```bash
# Clone or navigate to the repository
cd /path/to/quail

# Create and activate conda environment
conda create -n ent python=3.12
conda activate ent

# Install in editable mode
pip install -e .

# Run the automated setup
quail-install
```

### Method 2: Install from PyPI (When Published)

Once QuAIL is published to PyPI:

```bash
pip install quail-server
quail-install
```

### Method 3: Install from Git Repository

```bash
pip install git+https://github.com/miinukim/quail.git
quail-install
```

## After Installation

Once installed via pip, you can use these commands:

- `quail-manage` - Django management commands (equivalent to `python manage.py`)
- `quail-install` - Run the automated installation script

### Examples:

```bash
# Run database migrations
quail-manage migrate

# Create a superuser
quail-manage createsuperuser

# Start the development server
quail-manage runserver

# Run any Django management command
quail-manage <command> [options]
```

## For Developers: Building and Distributing

### Building the Package

1. **Install build tools**:
   ```bash
   pip install build twine
   ```

2. **Build the distribution packages**:
   ```bash
   python -m build
   ```
   
   This creates two files in `dist/`:
   - `quail_server-0.1.0.tar.gz` (source distribution)
   - `quail_server-0.1.0-py3-none-any.whl` (wheel distribution)

3. **Check the distribution**:
   ```bash
   twine check dist/*
   ```

### Testing the Package Locally

Before publishing, test the package installation:

```bash
# Install from the local wheel
pip install dist/quail_server-0.1.0-py3-none-any.whl

# Or install from the source distribution
pip install dist/quail_server-0.1.0.tar.gz
```

### Publishing to PyPI

#### Test PyPI (Recommended First)

1. **Create an account** at [test.pypi.org](https://test.pypi.org)

2. **Upload to Test PyPI**:
   ```bash
   twine upload --repository testpypi dist/*
   ```

3. **Test installation from Test PyPI**:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ quail-server
   ```

#### Production PyPI

1. **Create an account** at [pypi.org](https://pypi.org)

2. **Upload to PyPI**:
   ```bash
   twine upload dist/*
   ```

3. **Install from PyPI**:
   ```bash
   pip install quail-server
   ```

## Package Structure

The QuAIL package includes:

```
quail-server/
├── pyproject.toml          # Package metadata and dependencies
├── setup.py                # Legacy setup file (defers to pyproject.toml)
├── MANIFEST.in             # Specifies non-Python files to include
├── LICENSE                 # MIT License
├── README.md              # Main documentation
├── requirements.txt        # Direct dependencies (for reference)
├── quail/                  # Main Django project
│   ├── __init__.py
│   ├── cli.py             # CLI entry points
│   ├── settings.py
│   └── ...
├── expmanager/            # Experiment management app
├── orchestrator/          # Execution engine app
├── visualizer/            # Real-time interface app
└── core/                  # Shared utilities
```

## Version Management

To update the version:

1. Edit `pyproject.toml` and update the `version` field
2. Commit the change
3. Tag the release:
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```
4. Build and publish the new version

## Dependencies

All dependencies are specified in `pyproject.toml`:

- **Runtime dependencies**: Listed under `[project.dependencies]`
- **Optional dependencies**: Listed under `[project.optional-dependencies]`

The `requirements.txt` file is maintained for backward compatibility and direct installation.

## Troubleshooting

### Import Errors After Installation

If you get import errors, ensure you're in the correct environment:

```bash
conda activate ent
which python  # Should point to your conda environment
```

### Command Not Found: quail-manage

The CLI commands are installed as entry points. If they're not found:

```bash
# Reinstall in editable mode
pip install -e .

# Or check if the scripts directory is in PATH
echo $PATH
```

### Package Not Found

If `pip install -e .` fails:

```bash
# Ensure you're in the package root directory
ls pyproject.toml  # This file should exist

# Try upgrading pip
pip install --upgrade pip setuptools wheel
```

## Additional Resources

- **Django Documentation**: https://docs.djangoproject.com/
- **Python Packaging Guide**: https://packaging.python.org/
- **Setuptools Documentation**: https://setuptools.pypa.io/
