"""Text normalisation shared by rule matching and lexical semantics."""

from __future__ import annotations

import re
from functools import lru_cache

_PUNCT = re.compile(r"[^a-z0-9&/+\- ]+")
_SPACES = re.compile(r"\s+")

STOPWORDS = frozenset(
    """a an and are as at be by for from has have in into is it its of on or that the this to was were will with
    within under upon over per via etc i ii iii iv no nos sh s/h name work works tender tenders bid bids e-tender
    etender notice nit inviting invitation online open two cover period one year years supply supplying providing
    provision installation installing commissioning testing commission including include incl various different
    other others all new existing basis based required requirement requirements selection agency agencies vendor
    vendors service services the of at lot item items rate contract contracts procurement purchase limited ltd
    pvt private india indian government govt department dept office division unit units office centre center
    corporation co company board ministry national state district call 1st 2nd 3rd 2024 2025 2026 2027 2026-27
    2025-26 2027-28 m/s etc. design development maintenance operation annual mode""".split()
)


@lru_cache(maxsize=4096)
def normalize(text: str) -> str:
    t = (text or "").lower().replace("\u2013", "-").replace("\u2014", "-")
    t = _PUNCT.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


def expand_synonyms(text_norm: str, synonyms: dict[str, str]) -> str:
    """Append expansions of abbreviations found in the text (never removes text)."""
    extra = []
    padded = f" {text_norm} "
    for short, long in synonyms.items():
        s = normalize(short)
        if s and f" {s} " in padded:
            extra.append(normalize(long))
    return text_norm + (" " + " ".join(extra) if extra else "")


@lru_cache(maxsize=8192)
def phrase_regex(phrase: str) -> re.Pattern[str]:
    """Whole-phrase match, tolerant of a plural on the last word (camera → cameras)."""
    p = normalize(phrase)
    last = p.rsplit(" ", 1)[-1]
    plural = r"(?:s|es)?" if len(last) >= 4 and last.isalpha() else ""  # never pluralise acronyms (mes ≠ mess)
    return re.compile(r"(?<![a-z0-9])" + re.escape(p) + plural + r"(?![a-z0-9])")


@lru_cache(maxsize=65536)
def stem(word: str) -> str:
    """Very light English suffix stripping (no external dependency)."""
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith(("sses", "xes", "ches", "shes")):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    return w


def tokens(text_norm: str) -> list[str]:
    return [stem(t) for t in re.split(r"[\s/]+", text_norm)
            if len(t) > 1 and t not in STOPWORDS and not t.isdigit()]


def ngrams(toks: list[str]) -> list[str]:
    return toks + [f"{a} {b}" for a, b in zip(toks, toks[1:], strict=False)]
