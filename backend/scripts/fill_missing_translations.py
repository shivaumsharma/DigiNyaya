"""Fill in ONLY the keys en.json has that a target locale file is missing --
the incremental companion to scripts/translate_ui_strings.py (which
regenerates a locale file from scratch, discarding any prior manual
touch-ups). Meant for the common case: en.json grows a few new keys as
features ship, and every other locale file quietly falls behind until
someone runs the full regenerator (or, until now, nobody does).

Reuses translate_ui_strings.py's placeholder-protection logic rather than
duplicating it -- see that file's docstring for why the {{var}}-preservation
dance is needed at all.

Usage (from the backend folder, with a real SARVAM_API_KEY in the environment):
    python scripts/fill_missing_translations.py               # all locale files with a gap
    python scripts/fill_missing_translations.py hi-IN ta-IN    # just these
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ".")

from app.language.gateway import get_language_gateway  # noqa: E402
from scripts.translate_ui_strings import flatten, translate_key, unflatten  # noqa: E402

I18N_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"
EN_PATH = I18N_DIR / "en.json"

# All locales SUPPORTED_UI_LANGUAGES (frontend/src/i18n/LanguageContext.jsx)
# ships a file for, besides English itself.
ALL_LOCALES = ["hi-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "mr-IN", "bn-IN", "gu-IN", "pa-IN", "od-IN"]


def main() -> None:
    targets = sys.argv[1:] or ALL_LOCALES
    flat_en = flatten(json.loads(EN_PATH.read_text(encoding="utf-8")))

    gw = get_language_gateway()
    if not gw.enabled:
        print("Language gateway is disabled (LANGUAGE_GATEWAY_ENABLED=false) -- aborting.")
        return

    for target_lang in targets:
        out_path = I18N_DIR / f"{target_lang.split('-')[0]}.json"
        if not out_path.exists():
            print(f"SKIP {target_lang}: no existing {out_path.name} to fill in (use translate_ui_strings.py for a new locale).")
            continue

        flat_existing = flatten(json.loads(out_path.read_text(encoding="utf-8")))
        missing = {k: v for k, v in flat_en.items() if k not in flat_existing}
        if not missing:
            print(f"{target_lang}: nothing missing.")
            continue

        print(f"{target_lang}: filling {len(missing)} missing key(s)...")
        english_fallbacks: list[str] = []
        for key, value in missing.items():
            localized, strategy = translate_key(gw, value, target_lang)
            flat_existing[key] = localized
            if strategy == "english-fallback":
                english_fallbacks.append(key)
            time.sleep(0.3)

        if english_fallbacks:
            print(f"  WARNING: {len(english_fallbacks)} key(s) fell back to English: {', '.join(english_fallbacks)}")

        out = unflatten(flat_existing)
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  Wrote {out_path}")


if __name__ == "__main__":
    main()
