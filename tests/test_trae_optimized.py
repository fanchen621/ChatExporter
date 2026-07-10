import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chat_exporter.adapters.trae_optimized import TraeAdapter


class TempTraeAdapter(TraeAdapter):
    def __init__(self, root: str):
        super().__init__()
        self.encrypted_db_path = os.path.join(root, "database.db")
        self._cache_path = os.path.join(root, "key.cache")

    def _get_key_cache_path(self) -> str:
        return self._cache_path


class TraeOptimizedTests(unittest.TestCase):
    def test_parse_key_hex_accepts_common_forms(self):
        raw = "ab" * 32
        self.assertEqual(TraeAdapter._parse_key_hex(raw), bytes.fromhex(raw))
        self.assertEqual(TraeAdapter._parse_key_hex("0x" + raw), bytes.fromhex(raw))
        self.assertEqual(TraeAdapter._parse_key_hex(f'"{raw}"'), bytes.fromhex(raw))

    def test_parse_key_hex_rejects_invalid_values(self):
        self.assertIsNone(TraeAdapter._parse_key_hex(""))
        self.assertIsNone(TraeAdapter._parse_key_hex("zz" * 32))
        self.assertIsNone(TraeAdapter._parse_key_hex("ab" * 31))

    def test_memory_scan_is_explicit_by_default(self):
        adapter = TraeAdapter()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(adapter._memory_scan_enabled())
        with mock.patch.dict(os.environ, {"TRAE_ENABLE_MEMORY_SCAN": "1"}, clear=True):
            self.assertTrue(adapter._memory_scan_enabled())

    def test_v2_cache_round_trip_and_scope(self):
        key = bytes.fromhex("12" * 32)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "database.db"
            db.write_bytes(b"x" * 4096)
            adapter = TempTraeAdapter(tmp)
            adapter._save_key_cache(key)
            self.assertEqual(adapter._load_cached_key(), key)

            payload = json.loads(Path(adapter._cache_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 2)
            self.assertEqual(payload["scope"], adapter._cache_scope())

            other = TempTraeAdapter(tmp)
            other.encrypted_db_path = os.path.join(tmp, "other.db")
            Path(other.encrypted_db_path).write_bytes(b"y" * 4096)
            self.assertIsNone(other._load_cached_key())

    def test_fast_validator_rejects_wrong_key(self):
        page = b"\x00" * 4096
        validator = TraeAdapter._fast_key_validator(page)
        self.assertFalse(validator(bytes.fromhex("34" * 32)))


if __name__ == "__main__":
    unittest.main()
