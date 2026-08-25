import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anr.config import ConfigManager
from anr.constants import atomic_write_json
from anr.models import Config, Profile


class PersistenceTests(unittest.TestCase):
    def test_atomic_write_retains_previous_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            atomic_write_json(target, {"value": 1})
            atomic_write_json(target, {"value": 2}, backup=True)

            self.assertEqual({"value": 2}, json.loads(target.read_text("utf-8")))
            backup = target.with_suffix(".json.bak")
            self.assertEqual({"value": 1}, json.loads(backup.read_text("utf-8")))
            self.assertEqual([], list(target.parent.glob("*.tmp")))

    def test_corrupt_config_recovers_from_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            target.write_text("{broken", encoding="utf-8")
            backup = target.with_suffix(".json.bak")
            expected = Config(
                profiles=[Profile(id="safe", name="Recovered")],
                active_profile_id="safe",
            )
            backup.write_text(json.dumps(expected.to_dict()), encoding="utf-8")

            with patch("anr.config.CONFIG_FILE", target), patch(
                "anr.config.ensure_config_dir", return_value=None
            ):
                manager = ConfigManager()

            self.assertEqual("Recovered", manager.get_active_profile().name)
            restored = json.loads(target.read_text("utf-8"))
            self.assertEqual("safe", restored["active_profile_id"])


if __name__ == "__main__":
    unittest.main()
