#!/usr/bin/env python3
"""
Mississippi DOR Order Bot - GUI Launcher
Easy-to-use interface for automated ordering
"""

import sys
import subprocess
import os
from pathlib import Path

VERSION = "2.0.0"

def check_requirements():
    """Check if required packages are installed"""
    required = ['playwright', 'dotenv', 'flask']
    missing = []

    for package in required:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)

    return missing

def install_requirements():
    """Install missing requirements"""
    print("Installing required packages...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    print("Installing browser...")
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])

def clear_cache_if_updated():
    """Clear __pycache__ automatically when code version changes (e.g. after git pull)."""
    import shutil
    base_dir = Path(__file__).parent
    version_file = base_dir / '.version'
    cache_dir = base_dir / '__pycache__'

    cached_version = None
    if version_file.exists():
        cached_version = version_file.read_text().strip()

    if cached_version != VERSION:
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            print(f"Cleared __pycache__ (version changed: {cached_version} -> {VERSION})")
        version_file.write_text(VERSION)

def main():
    print("Mississippi DOR Order Bot")
    print("-" * 30)

    clear_cache_if_updated()

    missing = check_requirements()
    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        install_requirements()

    # Try Tkinter desktop app first (works best on Windows)
    # Pass --web to force the web UI instead
    if '--web' not in sys.argv:
        try:
            from gui_bot import main as run_gui
            run_gui()
            return
        except ImportError:
            print("Tkinter not available. Starting web interface...")

    # Fall back to web UI
    from web_gui import app
    import webbrowser
    import threading

    def open_browser():
        """Wait for the server to start, then open the browser."""
        import time
        time.sleep(1.5)
        webbrowser.open('http://127.0.0.1:5050')

    print("Starting web interface at http://127.0.0.1:5050")
    threading.Thread(target=open_browser, daemon=True).start()
    # Port 5050: macOS AirPlay Receiver often binds *:5000 and responds with HTTP 403 to browsers.
    app.run(debug=False, port=5050, host='127.0.0.1')

if __name__ == "__main__":
    main()