"""Offline compatibility checks for the optional operator-imported skill request."""
import json
from pathlib import Path
import unittest

from app.main import _parse_skill_file


PACK = Path(__file__).resolve().parents[2] / "skills" / "reconhawk-quality-pack.json"


class QualitySkillPackTests(unittest.TestCase):
    def test_pack_matches_current_file_import_contract(self):
        body = json.loads(PACK.read_text(encoding="utf-8"))
        self.assertEqual(body["version"], "1.0.0")
        self.assertEqual(len(body["files"]), 8)
        names = set()
        for entry in body["files"]:
            with self.subTest(filename=entry["filename"]):
                self.assertNotIn("/", entry["filename"])
                self.assertLess(len(entry["content"]), 100_000)
                parsed = _parse_skill_file(entry["filename"], entry["content"])
                self.assertTrue(parsed["name"].startswith("RHQ v1 - "))
                self.assertLessEqual(len(parsed["name"]), 120)
                self.assertNotIn(parsed["name"], names)
                names.add(parsed["name"])
                self.assertEqual(parsed["playbook"], entry["content"])
        self.assertEqual(len(names), 8)

    def test_each_playbook_has_controls_proof_and_inconclusive_conditions(self):
        body = json.loads(PACK.read_text(encoding="utf-8"))
        required = (
            "Prerequisites:", "Bounded actions:", "Positive and negative controls:",
            "Minimum proof:", "False-positive checks:", "Stop or inconclusive:",
        )
        for entry in body["files"]:
            with self.subTest(filename=entry["filename"]):
                for section in required:
                    self.assertIn(section, entry["content"])
                self.assertIn("request", entry["content"].lower())
                self.assertIn("redact", entry["content"].lower())


if __name__ == "__main__":
    unittest.main()
