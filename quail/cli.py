"""
Command-line interface for QuAIL

This module provides entry points for the pip-installed package.
"""

import os
import sys
from pathlib import Path


def manage():
    """
    Entry point for Django management commands.
    Equivalent to: python manage.py <command>
    
    Usage: quail-manage <command> [options]
    """
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quail.settings')
    
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    
    execute_from_command_line(sys.argv)


def install():
    """
    Entry point for the installation script.
    
    Usage: quail-install
    """
    # Import and run the installation script
    install_script = Path(__file__).parent.parent / 'install.py'
    
    if not install_script.exists():
        print("Error: install.py not found in package")
        sys.exit(1)
    
    # Execute the install script
    with open(install_script) as f:
        code = compile(f.read(), install_script, 'exec')
        exec(code, {'__name__': '__main__', '__file__': str(install_script)})


if __name__ == '__main__':
    manage()
