#!/usr/bin/env python3
"""Simple test runner script for Coolhand Python package."""

import sys
import os

# Add src to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Import pytest and run tests
try:
    import pytest

    # Run tests with verbose output
    exit_code = pytest.main([
        'tests/',
        '-v',
        '--tb=short',
        '--disable-warnings'
    ])

    sys.exit(exit_code)

except ImportError:
    print("pytest not found. Installing...")
    os.system(f"{sys.executable} -m pip install pytest pytest-asyncio pytest-mock")

    import pytest
    exit_code = pytest.main([
        'tests/',
        '-v',
        '--tb=short',
        '--disable-warnings'
    ])

    sys.exit(exit_code)

except Exception as e:
    print(f"Error running tests: {e}")
    sys.exit(1)