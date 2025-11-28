#!/usr/bin/env python3
"""
QuAIL Installation Script

This script automates the installation and setup of the QuAIL system.
It handles:
- Python dependency installation
- Database migrations
- Redis availability check
- Directory structure creation
- Configuration validation
"""

import sys
import os
import subprocess
import platform
from pathlib import Path


class Colors:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def print_header(message):
    """Print a formatted header message"""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{message.center(60)}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}\n")


def print_step(step_num, message):
    """Print a formatted step message"""
    print(f"{Colors.OKBLUE}[{step_num}] {message}{Colors.ENDC}")


def print_success(message):
    """Print a success message"""
    print(f"{Colors.OKGREEN}✓ {message}{Colors.ENDC}")


def print_warning(message):
    """Print a warning message"""
    print(f"{Colors.WARNING}⚠ {message}{Colors.ENDC}")


def print_error(message):
    """Print an error message"""
    print(f"{Colors.FAIL}✗ {message}{Colors.ENDC}")


def check_python_version():
    """Check if Python version meets requirements"""
    print_step(1, "Checking Python version...")
    
    version_info = sys.version_info
    if version_info.major < 3 or (version_info.major == 3 and version_info.minor < 10):
        print_error(f"Python 3.10+ required. Current version: {sys.version}")
        return False
    
    print_success(f"Python {version_info.major}.{version_info.minor}.{version_info.micro}")
    return True


def check_redis():
    """Check if Redis is available"""
    print_step(2, "Checking Redis availability...")
    
    try:
        result = subprocess.run(
            ['redis-cli', 'ping'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and 'PONG' in result.stdout:
            print_success("Redis is running")
            return True
        else:
            print_warning("Redis is not running")
            print(f"   Please start Redis with: {Colors.BOLD}redis-server{Colors.ENDC}")
            return False
            
    except FileNotFoundError:
        print_warning("Redis CLI not found")
        print(f"   Please install Redis for your system:")
        
        system = platform.system()
        if system == "Linux":
            print(f"   Ubuntu/Debian: {Colors.BOLD}sudo apt-get install redis-server{Colors.ENDC}")
            print(f"   Fedora/RHEL:   {Colors.BOLD}sudo dnf install redis{Colors.ENDC}")
            print(f"   Arch:          {Colors.BOLD}sudo pacman -S redis{Colors.ENDC}")
        elif system == "Darwin":
            print(f"   macOS:         {Colors.BOLD}brew install redis{Colors.ENDC}")
        
        return False
    except subprocess.TimeoutExpired:
        print_warning("Redis check timed out")
        return False


def install_dependencies():
    """Install Python dependencies from requirements.txt"""
    print_step(3, "Installing Python dependencies...")
    
    requirements_file = Path(__file__).parent / 'requirements.txt'
    
    if not requirements_file.exists():
        print_error("requirements.txt not found!")
        return False
    
    try:
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-r', str(requirements_file)],
            check=True
        )
        print_success("Dependencies installed successfully")
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to install dependencies: {e}")
        return False


def create_directories():
    """Create necessary directories"""
    print_step(4, "Creating project directories...")
    
    base_dir = Path(__file__).parent
    directories = [
        base_dir / 'logs',
        base_dir / 'data',
    ]
    
    for directory in directories:
        directory.mkdir(exist_ok=True)
        print_success(f"Created/verified: {directory.name}/")
    
    return True


def run_migrations():
    """Run Django database migrations"""
    print_step(5, "Running database migrations...")
    
    manage_py = Path(__file__).parent / 'manage.py'
    
    if not manage_py.exists():
        print_error("manage.py not found!")
        return False
    
    try:
        subprocess.run(
            [sys.executable, str(manage_py), 'migrate'],
            check=True
        )
        print_success("Migrations completed successfully")
        return True
        
    except subprocess.CalledProcessError as e:
        print_error(f"Migration failed: {e}")
        return False


def create_superuser_prompt():
    """Prompt user to create a Django superuser"""
    print_step(6, "Django superuser setup (optional)...")
    
    response = input(f"{Colors.OKCYAN}Do you want to create a Django superuser now? (y/N): {Colors.ENDC}").strip().lower()
    
    if response in ['y', 'yes']:
        manage_py = Path(__file__).parent / 'manage.py'
        try:
            subprocess.run(
                [sys.executable, str(manage_py), 'createsuperuser'],
                check=True
            )
            print_success("Superuser created successfully")
        except subprocess.CalledProcessError:
            print_warning("Superuser creation skipped or failed")
    else:
        print(f"   You can create a superuser later with: {Colors.BOLD}python manage.py createsuperuser{Colors.ENDC}")


def print_next_steps():
    """Print instructions for running the server"""
    print_header("Installation Complete!")
    
    print(f"{Colors.OKGREEN}QuAIL has been successfully installed!{Colors.ENDC}\n")
    
    print(f"{Colors.BOLD}Next Steps:{Colors.ENDC}\n")
    
    print(f"{Colors.OKCYAN}1. Start Redis (if not already running):{Colors.ENDC}")
    print(f"   {Colors.BOLD}redis-server{Colors.ENDC}\n")
    
    print(f"{Colors.OKCYAN}2. Start Django Web Server:{Colors.ENDC}")
    print(f"   {Colors.BOLD}python manage.py runserver{Colors.ENDC}\n")
    
    print(f"{Colors.OKCYAN}3. Start Celery Worker (in separate terminal):{Colors.ENDC}")
    print(f"   {Colors.BOLD}celery -A quail worker -l info -c 1{Colors.ENDC}\n")
    
    print(f"{Colors.OKCYAN}4. Start InstrumentServer (in separate terminal):{Colors.ENDC}")
    print(f"   {Colors.BOLD}instrumentserver-server{Colors.ENDC}\n")
    
    print(f"{Colors.WARNING}Note: The InstrumentServer is an external dependency.{Colors.ENDC}")
    print(f"{Colors.WARNING}      Consult its documentation for installation.{Colors.ENDC}\n")
    
    print(f"{Colors.BOLD}Documentation:{Colors.ENDC}")
    print(f"   Syntax Guide:       docs/syntax_guide.md")
    print(f"   System Architecture: docs/SYSTEM_ARCHITECTURE.md\n")


def main():
    """Main installation routine"""
    print_header("QuAIL Installation Script")
    
    print(f"{Colors.BOLD}QuIQCL Automated Instrument Linking system{Colors.ENDC}")
    print("This script will install and configure QuAIL on your system.\n")
    
    # Track installation status
    all_checks_passed = True
    
    # Step 1: Check Python version
    if not check_python_version():
        print_error("Installation aborted: Python version requirement not met")
        sys.exit(1)
    
    # Step 2: Check Redis (warning only, not fatal)
    redis_available = check_redis()
    if not redis_available:
        all_checks_passed = False
    
    # Step 3: Install dependencies
    if not install_dependencies():
        print_error("Installation aborted: Dependency installation failed")
        sys.exit(1)
    
    # Step 4: Create directories
    if not create_directories():
        print_error("Installation aborted: Directory creation failed")
        sys.exit(1)
    
    # Step 5: Run migrations
    if not run_migrations():
        print_error("Installation aborted: Database migration failed")
        sys.exit(1)
    
    # Step 6: Optional superuser creation
    create_superuser_prompt()
    
    # Print next steps
    print_next_steps()
    
    # Final status
    if not all_checks_passed:
        print_warning("Installation completed with warnings. Please review the messages above.")
        sys.exit(0)
    else:
        print_success("Installation completed successfully!")
        sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.WARNING}Installation cancelled by user{Colors.ENDC}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
