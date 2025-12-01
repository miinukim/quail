# QuAIL Quick Start

## Installation (5 minutes)

```bash
# Navigate to QuAIL directory
cd /path/to/quail

# Install QuAIL
pip install -e .

# Run automated setup
quail-install
```

## Running QuAIL (3 terminals required)

### Terminal 1: Redis
```bash
redis-server
```

### Terminal 2: Django Server
```bash
cd /path/to/quail
quail-manage runserver
# Or: python manage.py runserver
```

### Terminal 3: Celery Worker
```bash
cd /path/to/quail
celery -A quail worker -l info -c 1
```

### Terminal 4 (Optional): InstrumentServer
```bash
# See instrumentserver documentation
instrumentserver-server
```

## Quick Commands Reference

```bash
# Django management
quail-manage migrate              # Run migrations
quail-manage createsuperuser      # Create admin user
quail-manage runserver            # Start dev server
quail-manage shell               # Django shell

# Development
pip install -e .                 # Install in editable mode
quail-install                    # Run setup wizard

# Building package (for distribution)
python -m build                  # Build distribution packages
twine check dist/*              # Verify package
```

## Access Points

- **API Server**: http://localhost:8000/
- **Admin Panel**: http://localhost:8000/admin/
- **API Endpoints**: http://localhost:8000/api/
- **WebSocket**: ws://localhost:8000/ws/experiment/{run_id}/

## Next Steps

1. **Create an experiment script** with QuAIL syntax
2. **Submit a run** via API
3. **Monitor in real-time** via WebSocket
4. **View results** in HDF5 files in `data/` directory

See [docs/syntax_guide.md](docs/syntax_guide.md) for the experiment scripting language.
