"""Install and run 9front virtual machines with transparent QEMU commands."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("p9qemu")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.1.0"

from p9qemu.cli import main

__all__ = ["__version__", "main"]
