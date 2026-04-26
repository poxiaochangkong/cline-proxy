"""
Port management - detect and allocate available ports.

Supports:
- Checking whether a specific port is available.
- Automatically finding a free port when preferred port is occupied or not specified.
- CLI output to inform the user which port was assigned.
"""

import socket
import logging

logger = logging.getLogger("cline-proxy")


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check whether the given port is available on the specified host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(preferred: int = 0, host: str = "127.0.0.1") -> int:
    """
    Find an available port.

    Strategy:
      1. If preferred > 0, try that port first.
      2. If preferred == 0 or port is taken, ask the OS for a free port.
      3. If both steps fail, raise RuntimeError.

    Returns the port number.
    """
    if preferred > 0:
        if is_port_available(preferred, host):
            logger.info("Using configured port: %d", preferred)
            return preferred
        logger.warning(
            "Configured port %d is already in use, searching for free port...",
            preferred,
        )

    # Let the OS assign a free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, 0))
            port = s.getsockname()[1]
            logger.info("Assigned free port: %d", port)
            return port
        except OSError as e:
            raise RuntimeError(f"Could not find any available port: {e}") from e
