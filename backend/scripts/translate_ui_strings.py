"""Generate frontend/src/i18n/hi.json from frontend/src/i18n/en.json.

One-off/dev-time tool: translates the static UI-chrome string dictionary via
the existing LanguageGateway (Sarvam Mayura translation, same code path used
for localizing agent output). Not a runtime API -- run manually whenever
en.json gains/changes keys, then hand-review the diff.

Usage (from the backend folder):
    python scripts/translate_ui_strings.py [target_lang]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

# Windows consoles often default stdout to cp1252, which can't encode most
# Indic scripts -- reconfigure so the mismatch/failure warnings below (which
# print the actual translated strings) don't crash the script.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ".")

from app.language.gateway import get_language_gateway  # noqa: E402

EN_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "en.json"
DEFAULT_TARGET = "hi-IN"

# The brand wordmark must never be transliterated/translated (see project
# memory "diginyaya_language_switcher" -- Sarvam has been observed garbling
# it into a phonetic spelling per-script).
BRAND = "DigiNyaya"

PLACEHOLDER_RE = re.compile(r"\{\{\w+\}\}")

# Sarvam was observed mangling symbolic placeholder tokens in several ways:
# dropping {{n}}/{{pct}} entirely, translating the variable *name* inside
# the braces (e.g. {{model}} -> {{மாதிரி}}), or silently swallowing an
# "@@0@@"-style marker with no surrounding lexical content. Actual numerals,
# by contrast, survive MT reliably (verified against hi.json: literal "18",
# "72" etc. come through untouched) -- so {{var}} and the brand name are
# swapped for distinct three-digit numbers before translating and restored
# verbatim afterward. Numbers start at 900 to avoid colliding with any real
# number already present in the UI strings (e.g. "72 hours", "20000").
TOKEN_BASE = 900


def protect(text: str) -> tuple[str, list[str]]:
    """Replace every {{var}} and the brand name with a distinct numeric
    token; return the protected text plus the list of originals to restore,
    indexed by (token - TOKEN_BASE).
    """
    originals: list[str] = []

    def _sub_placeholder(match: re.Match[str]) -> str:
        originals.append(match.group(0))
        return str(TOKEN_BASE + len(originals) - 1)

    protected = PLACEHOLDER_RE.sub(_sub_placeholder, text)

    if BRAND in protected:
        originals.append(BRAND)
        protected = protected.replace(BRAND, str(TOKEN_BASE + len(originals) - 1))

    return protected, originals


def restore(text: str, originals: list[str]) -> str:
    for index, original in enumerate(originals):
        text = text.replace(str(TOKEN_BASE + index), original)
    return text


def check_placeholders(original: str, translated: str) -> bool:
    """Return True if every {{var}} in *original* also appears in *translated*."""
    original_vars = set(PLACEHOLDER_RE.findall(original))
    translated_vars = set(PLACEHOLDER_RE.findall(translated))
    return original_vars.issubset(translated_vars)


def translate_key(gw, value: str, target_lang: str) -> tuple[str, str]:
    """Translate one UI string, working around Sarvam's placeholder quirks.

    Tries, in order, until one preserves every {{var}}:
      1. Numeral-token protection (protect/restore above) -- correct for
         nearly all strings, since real numerals survive MT reliably.
      2. Raw passthrough of the literal {{var}} braces -- numeral tokens
         backfire on very short, low-context strings (e.g. "Tier {{n}}"),
         where Sarvam "helpfully" spells the number out as a word instead
         of leaving it as a digit; the literal mustache form happened to
         survive fine there in the existing Hindi dictionary.
      3. English passthrough -- last resort so a broken interpolation
         token never reaches the frontend; better to show one untranslated
         label than crash `t()` silently producing "{{n}}" literally.

    Returns (localized_text, strategy_used).
    """
    protected_value, originals = protect(value)
    result = gw.to_user_language(protected_value, target_lang)
    if result.was_translated:
        localized = restore(result.localized_text, originals)
        if check_placeholders(value, localized):
            return localized, "numeral-token"

    raw_result = gw.to_user_language(value, target_lang)
    if raw_result.was_translated and check_placeholders(value, raw_result.localized_text):
        return raw_result.localized_text, "raw-mustache"

    return value, "english-fallback"


def flatten(node: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def unflatten(flat: dict[str, str]) -> dict:
    root: dict = {}
    for path, value in flat.items():
        parts = path.split(".")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return root


def main() -> None:
    target_lang = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET
    out_path = EN_PATH.with_name(f"{target_lang.split('-')[0]}.json")

    en = json.loads(EN_PATH.read_text(encoding="utf-8"))
    flat_en = flatten(en)
    print(f"Translating {len(flat_en)} UI strings -> {target_lang} ...")

    gw = get_language_gateway()
    if not gw.enabled:
        print("Language gateway is disabled (LANGUAGE_GATEWAY_ENABLED=false) -- aborting.")
        return

    # One request at a time with a small pause between them -- Sarvam seems
    # to reject some requests when a full batch fires back-to-back with no
    # spacing (observed as retries exhausted on ~5-10% of a 148-string burst).
    flat_out: dict[str, str] = {}
    english_fallbacks: list[str] = []
    raw_mustache_used: list[str] = []
    for key, value in flat_en.items():
        localized, strategy = translate_key(gw, value, target_lang)
        flat_out[key] = localized
        if strategy == "english-fallback":
            english_fallbacks.append(key)
        elif strategy == "raw-mustache":
            raw_mustache_used.append(key)
        time.sleep(0.3)  # translate_key may issue a 2nd API call on fallback

    if raw_mustache_used:
        print(
            f"NOTE: {len(raw_mustache_used)} key(s) needed the raw-mustache fallback "
            "(numeral-token translation garbled the placeholder):"
        )
        for key in raw_mustache_used:
            print(f"  - {key}")

    if english_fallbacks:
        print(
            f"WARNING: {len(english_fallbacks)} key(s) fell back to English "
            "(no strategy preserved the {placeholder}, or translation failed):"
        )
        for key in english_fallbacks:
            print(f"  - {key}")

    out = unflatten(flat_out)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
