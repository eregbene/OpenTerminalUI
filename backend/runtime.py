from __future__ import annotations

import sys

SUPPORTED_PYTHON_MAJOR_MINOR = (3, 11)


def assert_supported_python() -> None:
    current = sys.version_info[:2]
    if current != SUPPORTED_PYTHON_MAJOR_MINOR:
        expected = ".".join(str(part) for part in SUPPORTED_PYTHON_MAJOR_MINOR)
        actual = ".".join(str(part) for part in current)
        raise RuntimeError(
            f"Bensim Trading backend supports Python {expected}. "
            f"Detected Python {actual}. Use Docker or a Python {expected} virtual environment."
        )
