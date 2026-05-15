"""
Entry point for running fincat as a module: python -m fincat
"""

from fincat.cli.commands import app

if __name__ == "__main__":
    app()
