import hashlib
import json
from pathlib import Path
import unittest


class ValidatedFixture(unittest.TestCase):
    def test_fixture_is_the_one_checked_by_the_official_validator(self):
        root = Path(__file__).resolve().parents[1]
        record = json.loads((root / "docs/comparison-results.json").read_text())
        digest = hashlib.sha256((root / "examples/report.sarif").read_bytes()).hexdigest()
        self.assertEqual(digest, record["fixture_sha256"])
        check = next(c for c in record["checks"] if c["check"] == "sdk-validation")
        self.assertEqual(check["exit_code"], 0)
