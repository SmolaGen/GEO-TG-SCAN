import os
import stat
import unittest
import tempfile
import json
from python_backend.config import AppConfig, DetectorConfig

class TestConfigPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_config_save_permissions_0600(self):
        """Verifies data/config.json is saved with 0600 permissions and directory with 0700"""
        cfg = AppConfig()
        cfg.data_dir = self.temp_dir
        cfg.detector.max_credible_speed_kmh = 320.0
        cfg.save_detector_config_to_file()

        config_file = cfg.get_config_json_path()
        self.assertTrue(os.path.isfile(config_file))

        # Check file permissions on posix systems
        if os.name == "posix":
            file_mode = stat.S_IMODE(os.stat(config_file).st_mode)
            self.assertEqual(file_mode, 0o600, f"Expected 0600 permissions on config.json, got {oct(file_mode)}")

            dir_mode = stat.S_IMODE(os.stat(self.temp_dir).st_mode)
            self.assertEqual(dir_mode, 0o700, f"Expected 0700 permissions on data dir, got {oct(dir_mode)}")

        # Check content and loading
        new_cfg = AppConfig()
        new_cfg.data_dir = self.temp_dir
        new_cfg.load_detector_config_from_file()
        self.assertEqual(new_cfg.detector.max_credible_speed_kmh, 320.0)

if __name__ == "__main__":
    unittest.main()
