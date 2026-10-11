"""Stage 5 — semantic matching between tender text and capability profiles.

Two interchangeable backends:

* ``lexical`` (default, zero cost, deterministic): TF-IDF cosine over
  unigrams+bigrams with synonym expansion. IDF is computed over the capability
  profiles, so words shared by every capability carry little weight.
* ``ollama``: dense embeddings from a *local* Ollama embedding model
  (e.g. ``embeddinggemma``). Vectors are cached by text hash + model, so
  unchanged text is never re-embedded. Ollama Cloud's pricing table lists no
  embedding models (checked 2026-10-10), so embeddings are not sent to paid
  cloud inference by default.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Callable

from .knowledge import KnowledgeBase
from .textnorm import expand_synonyms, ngrams, normalize, tokens


def _cosine_sparse(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _cosine_dense(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class LexicalSemanticScorer:
    name = "lexical-tfidf"

    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self.syn = kb.global_.synonyms
        docs = {c.id: Counter(ngrams(tokens(expand_synonyms(normalize(c.profile_text()), self.syn))))
                for c in kb.capabilities}
        n = len(docs)
        df: Counter[str] = Counter()
        for counts in docs.values():
            df.update(counts.keys())
        self.idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}
        self.cap_vectors = {cid: self._weight(c) for cid, c in docs.items()}

    def _weight(self, counts: Counter[str]) -> dict[str, float]:
        return {t: (1 + math.log(c)) * self.idf[t] for t, c in counts.items() if t in self.idf}

    def vectorize(self, text: str) -> dict[str, float]:
        return self._weight(Counter(ngrams(tokens(expand_synonyms(normalize(text), self.syn)))))

    def score(self, text: str) -> dict[str, float]:
        v = self.vectorize(text)
        return {cid: round(_cosine_sparse(v, cv), 4) for cid, cv in self.cap_vectors.items()}


class EmbeddingSemanticScorer:
    """Dense-embedding scorer with a pluggable cache (get/put by text hash)."""

    name = "ollama-embeddings"

    def __init__(
        self,
        kb: KnowledgeBase,
        embed: Callable[[list[str]], list[list[float]]],
        model: str,
        cache_get: Callable[[str, str], list[float] | None] | None = None,
        cache_put: Callable[[str, str, list[float]], None] | None = None,
    ) -> None:
        self.kb, self.embed, self.model = kb, embed, model
        self.cache_get, self.cache_put = cache_get, cache_put
        self.cap_vectors = dict(zip([c.id for c in kb.capabilities],
                                    self._vectors([c.profile_text() for c in kb.capabilities]), strict=True))

    def _vectors(self, texts: list[str]) -> list[list[float]]:
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        out: list[list[float] | None] = [self.cache_get(k, self.model) if self.cache_get else None for k in keys]
        missing = [i for i, v in enumerate(out) if v is None]
        if missing:
            fresh = self.embed([texts[i] for i in missing])
            if len(fresh) != len(missing):
                raise ValueError("embedding provider returned an unexpected number of vectors")
            for i, vec in zip(missing, fresh, strict=True):
                out[i] = vec
                if self.cache_put:
                    self.cache_put(keys[i], self.model, vec)
        return [v or [] for v in out]

    def score(self, text: str) -> dict[str, float]:
        vec = self._vectors([text])[0]
        return {cid: round(_cosine_dense(vec, cv), 4) for cid, cv in self.cap_vectors.items()}
