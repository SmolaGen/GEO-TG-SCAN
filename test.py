import os
import unittest

def load_tests(loader, tests, pattern):
    this_dir = os.path.dirname(__file__)
    pkg_tests = loader.discover(start_dir=os.path.join(this_dir, "python_backend", "tests"), pattern="test_*.py")
    tests.addTests(pkg_tests)
    return tests
