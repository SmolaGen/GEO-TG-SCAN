import unittest

def load_tests(loader, standard_tests, pattern):
    import os
    start_dir = os.path.dirname(__file__)
    package_tests = loader.discover(start_dir=start_dir, pattern="test_*.py")
    return package_tests

if __name__ == "__main__":
    unittest.main()
