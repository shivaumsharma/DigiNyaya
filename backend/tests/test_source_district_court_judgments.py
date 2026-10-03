"""Unit tests for scripts.source_district_court_judgments -- the pure,
deterministic logic only (case-type filtering, tar member matching/
ordering, case acceptance/rejection). Deliberately does NOT test
load_metadata/download_complex_tar (network-dependent, no need to mock
boto3 for logic that's just "try, catch, return None") or main() (would
need heavy LLM/S3 mocking for an integration-level test that adds little
over the unit tests below).

Regression coverage for two real bugs found during manual review (see the
same fixes' own comments in source_district_court_judgments.py):
  1. extract_case_text() used to match case_no via raw substring
     containment (`case_no in m.name`), which could match a DIFFERENT
     case whose number happens to contain this one as a substring.
  2. Multi-part order files were sorted lexicographically by filename,
     which puts "..._10.pdf" before "..._2.pdf" for any case with 10+
     parts -- wrong chronological order.

Run with (from backend/):
    python -m unittest tests.test_source_district_court_judgments -v
"""
from __future__ import annotations

import io
import os
import re
import sys
import tarfile
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import source_district_court_judgments as m  # noqa: E402


def _make_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


class TestCivilCodesForState(unittest.TestCase):
    def test_unknown_state_gets_only_the_base_codes(self):
        codes = m._civil_codes_for_state("999")
        self.assertEqual(codes, m.CIVIL_CASE_TYPE_CODES)

    def test_known_state_gets_base_codes_plus_its_own_addition(self):
        codes = m._civil_codes_for_state("29")  # Telangana -> adds "OS"
        self.assertIn("OS", codes)
        self.assertTrue(m.CIVIL_CASE_TYPE_CODES.issubset(codes))


class TestPartNumber(unittest.TestCase):
    def test_extracts_trailing_numeric_suffix(self):
        member = tarfile.TarInfo(name="orders_2022_555_7.pdf")
        self.assertEqual(m._part_number(member), 7)

    def test_no_trailing_number_defaults_to_zero(self):
        member = tarfile.TarInfo(name="orders_no_suffix.pdf")
        self.assertEqual(m._part_number(member), 0)

    def test_ten_sorts_after_two_not_before(self):
        # Regression: a plain string sort puts "_10.pdf" before "_2.pdf".
        names = [f"orders_2022_555_{i}.pdf" for i in (1, 2, 10, 11, 3)]
        members = [tarfile.TarInfo(name=n) for n in names]
        members.sort(key=m._part_number)
        self.assertEqual([mm.name for mm in members], [
            "orders_2022_555_1.pdf", "orders_2022_555_2.pdf", "orders_2022_555_3.pdf",
            "orders_2022_555_10.pdf", "orders_2022_555_11.pdf",
        ])


class TestExtractCaseText(unittest.TestCase):
    def setUp(self):
        self.tar_path = Path(__file__).parent / "_scratch_test.tar"
        self.addCleanup(lambda: self.tar_path.unlink(missing_ok=True))

    def test_does_not_match_a_case_whose_number_is_a_substring_of_another(self):
        # Regression: case_no="100" must not match the "1005" case's file.
        _make_tar(self.tar_path, {
            "orders_2021_100_1.pdf": b"case 100's real order",
            "orders_2021_1005_1.pdf": b"a different case's order",
        })
        with tarfile.open(self.tar_path) as tar:
            members = [mm for mm in tar.getmembers() if "100" in re.split(r"[_.]", mm.name)]
        self.assertEqual([mm.name for mm in members], ["orders_2021_100_1.pdf"])

    def test_returns_none_when_no_member_matches(self):
        _make_tar(self.tar_path, {"orders_2021_999_1.pdf": b"unrelated"})
        result = m.extract_case_text(self.tar_path, "555")
        self.assertIsNone(result)

    def test_returns_none_when_all_matches_are_unparseable(self):
        # Not valid PDF bytes -- extract_document should fail cleanly on
        # each, and the function should report "nothing extractable"
        # rather than raising.
        _make_tar(self.tar_path, {"orders_2021_555_1.pdf": b"not a real pdf"})
        result = m.extract_case_text(self.tar_path, "555")
        self.assertIsNone(result)


class TestBuildCase(unittest.TestCase):
    def _row(self, **overrides) -> dict:
        row = {
            "cino": "TSHC0100001232024", "case_type": "OS", "case_no": "12332024",
            "complex_name": "Test Complex", "district_name": "Test District",
            "state_name": "Telangana", "reg_year": 2024,
        }
        row.update(overrides)
        return row

    def _fields(self, **overrides) -> dict:
        fields = {
            "is_genuine_civil_dispute": True, "is_final_merits_decision": True,
            "category": "property_neighbor_disputes",
            "case_description": "A dispute over a boundary wall between neighbours.",
            "expected_outcome": "The court ordered the wall removed within 30 days.",
            "cited_precedent": None,
        }
        fields.update(overrides)
        return fields

    def test_rejects_when_not_genuine_civil_dispute(self):
        self.assertIsNone(m.build_case(self._row(), self._fields(is_genuine_civil_dispute=False)))

    def test_rejects_when_not_final_merits_decision(self):
        self.assertIsNone(m.build_case(self._row(), self._fields(is_final_merits_decision=False)))

    def test_rejects_unrecognised_category(self):
        self.assertIsNone(m.build_case(self._row(), self._fields(category="not_a_real_category")))

    def test_rejects_missing_description_or_outcome(self):
        self.assertIsNone(m.build_case(self._row(), self._fields(case_description="")))
        self.assertIsNone(m.build_case(self._row(), self._fields(expected_outcome="")))

    def test_accepts_and_builds_expected_shape(self):
        case = m.build_case(self._row(), self._fields())
        self.assertIsNotNone(case)
        self.assertEqual(case["case_id"], "DC-EVAL-TSHC0100001232024")
        self.assertEqual(case["category"], "property_neighbor_disputes")
        self.assertEqual(case["source"]["docid"], "TSHC0100001232024")
        self.assertEqual(case["source"]["title"], "OS 12332024")
        self.assertEqual(case["source"]["year"], 2024)
        self.assertFalse(case["verified"])
        self.assertTrue(case["sourced_via"].startswith("ecourts_district_court"))

    def test_null_string_cited_precedent_is_normalised_to_none(self):
        case = m.build_case(self._row(), self._fields(cited_precedent="null"))
        self.assertIsNone(case["cited_precedent"])


if __name__ == "__main__":
    unittest.main()
