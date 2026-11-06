"""Guide translations: every language mirrors the English source exactly, is written in the right script, and is served and
labelled correctly by the API. Translations are machine-made and unreviewed; these tests guard structure, not quality."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("DIGINYAYA_USE_LLM", "0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matters import guides  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
I18N = REPO / "frontend" / "src" / "i18n"

# Unicode block (inclusive) each language must be written in.
SCRIPT = {"hi": (0x0900, 0x097F), "mr": (0x0900, 0x097F), "bn": (0x0980, 0x09FF), "pa": (0x0A00, 0x0A7F), "gu": (0x0A80, 0x0AFF),
          "od": (0x0B00, 0x0B7F), "ta": (0x0B80, 0x0BFF), "te": (0x0C00, 0x0C7F), "kn": (0x0C80, 0x0CFF), "ml": (0x0D00, 0x0D7F)}


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for x in node:
            yield from _strings(x)
    elif isinstance(node, dict):
        for x in node.values():
            yield from _strings(x)


def _shape(node):
    """Keys and list lengths, ignoring the text itself."""
    if isinstance(node, dict):
        return {k: _shape(v) for k, v in node.items()}
    if isinstance(node, list):
        return len(node)
    return "s"


def _in_script(text: str, lang: str) -> float:
    lo, hi = SCRIPT[lang]
    letters = [c for c in text if c.isalpha()]
    return sum(lo <= ord(c) <= hi for c in letters) / len(letters) if letters else 1.0


class TranslationFiles(unittest.TestCase):
    def test_every_language_has_a_file_and_covers_every_guide(self):
        for code in guides.TRANSLATION_CODES:
            tr = guides.load_translation(code)
            self.assertIsNotNone(tr, code)
            self.assertEqual(set(tr["guides"]), {g["id"] for g in guides.GUIDES} | set(guides.COMPANION_GUIDES), code)

    def test_structure_matches_english(self):
        for code in guides.TRANSLATION_CODES:
            tr = guides.load_translation(code)
            self.assertEqual(len(tr["shared"]["documents"]), len(guides.COMMON_DOCUMENTS), code)
            self.assertEqual(len(tr["shared"]["this_tool_cannot"]), len(guides.CANNOT_DO), code)
            for g in guides.GUIDES:
                e = tr["guides"][g["id"]]
                self.assertEqual(set(e), {"label", "summary", "forum", "steps"}, (code, g["id"]))
                self.assertEqual(len(e["steps"]), len(g["steps"]), (code, g["id"]))
            for gid, g in guides.COMPANION_GUIDES.items():
                e = tr["guides"][gid]
                self.assertEqual(set(e), {"label", "summary", "forum", "steps", "documents"}, (code, gid))
                self.assertEqual(len(e["steps"]), len(g["steps"]), (code, gid))
                self.assertEqual(len(e["documents"]), len(g["documents"]), (code, gid))

    def test_no_empty_strings_and_mostly_in_the_right_script(self):
        for code in guides.TRANSLATION_CODES:
            for s in _strings(guides.load_translation(code)):
                self.assertTrue(s.strip(), code)
                self.assertGreaterEqual(_in_script(s, code), 0.6, (code, s[:60]))

    def test_no_untranslated_english_left_behind(self):
        for code in guides.TRANSLATION_CODES:
            tr = guides.load_translation(code)
            self.assertNotEqual(tr["shared"]["disclaimer"], guides.DISCLAIMER, code)
            for g in [*guides.GUIDES, *({"id": k, **v} for k, v in guides.COMPANION_GUIDES.items())]:
                self.assertNotEqual(tr["guides"][g["id"]]["forum"], g["forum"], (code, g["id"]))


class Localize(unittest.TestCase):
    def setUp(self):
        self.g = guides.source_guide("criminal_complaint")
        self.base = guides.render(self.g)

    def test_lang_code(self):
        self.assertEqual(guides.lang_code("hi-IN"), "hi")
        self.assertEqual(guides.lang_code("od-IN"), "od")
        for none in (None, "", "en-IN", "fr-FR", "xx"):
            self.assertEqual(guides.lang_code(none), "en")

    def test_english_and_unknown_return_the_base(self):
        for lang in (None, "en-IN", "fr-FR"):
            out = guides.localize("criminal_complaint", self.base, lang)
            self.assertEqual(out["steps"], self.base["steps"])
            self.assertEqual(out["language"], "en")

    def test_translated_guide_is_labelled_unreviewed(self):
        out = guides.localize("criminal_complaint", self.base, "hi-IN")
        self.assertEqual(out["language"], "hi")
        self.assertFalse(out["translation_reviewed"])
        self.assertFalse(out["reviewed"])
        self.assertEqual(len(out["steps"]), len(self.base["steps"]))
        self.assertNotEqual(out["disclaimer"], self.base["disclaimer"])

    def test_unknown_guide_falls_back_to_base(self):
        self.assertIs(guides.localize("nope", self.base, "hi-IN"), self.base)


class Api(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app

        self.client = TestClient(app)

    def test_guide_is_served_in_the_requested_language(self):
        en = self.client.get("/api/matters/tax_dispute/guide").json()
        ta = self.client.get("/api/matters/tax_dispute/guide?lang=ta-IN").json()
        self.assertEqual(en["language"], "en")
        self.assertEqual(ta["language"], "ta")
        self.assertFalse(ta["translation_reviewed"])
        self.assertNotEqual(ta["label"], en["label"])
        self.assertEqual(len(ta["steps"]), len(en["steps"]))

    def test_unsupported_language_gets_english(self):
        r = self.client.get("/api/matters/tax_dispute/guide?lang=fr-FR").json()
        self.assertEqual(r["language"], "en")

    def test_list_translates_navigator_labels_only(self):
        en = {m["id"]: m for m in self.client.get("/api/matters").json()}
        te = {m["id"]: m for m in self.client.get("/api/matters?lang=te-IN").json()}
        self.assertEqual(en.keys(), te.keys())
        self.assertNotEqual(te["criminal_complaint"]["label"], en["criminal_complaint"]["label"])
        self.assertEqual(te["money_recovery"]["label"], en["money_recovery"]["label"])


class FrontendLocales(unittest.TestCase):
    def test_matters_subtree_matches_english_in_every_language(self):
        en = json.loads((I18N / "en.json").read_text(encoding="utf-8"))["matters"]
        for code in guides.TRANSLATION_CODES:
            loc = json.loads((I18N / f"{code}.json").read_text(encoding="utf-8"))["matters"]
            self.assertEqual(_shape(loc), _shape(en), code)
            for s in _strings(loc):
                self.assertTrue(s.strip(), code)
            # brand name must survive translation
            self.assertIn("DigiNyaya", loc["sections"]["resolve"], code)


if __name__ == "__main__":
    unittest.main()
