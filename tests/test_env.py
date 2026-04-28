from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_weather_tool.env import load_project_env


class EnvLoaderTests(unittest.TestCase):
    def test_load_project_env_reads_simple_key_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                'ETHERSCAN_API_KEY="test-key"\nCHAIN_NOTE=polygon\n',
                encoding="utf-8",
            )

            old_key = os.environ.pop("ETHERSCAN_API_KEY", None)
            old_note = os.environ.pop("CHAIN_NOTE", None)
            try:
                load_project_env(root)
                self.assertEqual(os.environ.get("ETHERSCAN_API_KEY"), "test-key")
                self.assertEqual(os.environ.get("CHAIN_NOTE"), "polygon")
            finally:
                if old_key is None:
                    os.environ.pop("ETHERSCAN_API_KEY", None)
                else:
                    os.environ["ETHERSCAN_API_KEY"] = old_key
                if old_note is None:
                    os.environ.pop("CHAIN_NOTE", None)
                else:
                    os.environ["CHAIN_NOTE"] = old_note

    def test_load_project_env_does_not_override_existing_value_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text("ETHERSCAN_API_KEY=file-value\n", encoding="utf-8")

            old_key = os.environ.get("ETHERSCAN_API_KEY")
            os.environ["ETHERSCAN_API_KEY"] = "existing-value"
            try:
                load_project_env(root)
                self.assertEqual(os.environ.get("ETHERSCAN_API_KEY"), "existing-value")
                load_project_env(root, override=True)
                self.assertEqual(os.environ.get("ETHERSCAN_API_KEY"), "file-value")
            finally:
                if old_key is None:
                    os.environ.pop("ETHERSCAN_API_KEY", None)
                else:
                    os.environ["ETHERSCAN_API_KEY"] = old_key


if __name__ == "__main__":
    unittest.main()
