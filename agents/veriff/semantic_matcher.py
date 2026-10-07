"""Semantic claim matching for Veriff.

Hybrid matcher: when the sentence-transformers model is available we score
headlines by embedding cosine similarity (real semantic matching across
Tamil + English mixed input); otherwise we fall back to the lightweight
entity extractor + difflib.SequenceMatcher scoring so the pipeline still
works with no NLP models installed.

Semantic tiers (all-MiniLM-L6-v2 cosine similarity):
- exact_match  (sim >= 0.85)  -> score 1.0
- partial      (sim >= 0.75)  -> score 0.7
- keyword_only (sim >= 0.60)  -> score 0.4
- no_match     (sim < 0.60)   -> 0.0

difflib fallback tiers (SequenceMatcher ratio):
- exact_match  (ratio >= 0.70)  -> score 1.0
- partial      (ratio >= 0.45)  -> score 0.6
- keyword_only (>=2 shared tokens present) -> score 0.3
- no_match     -> 0.0
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, List, Optional

try:  # embeddings are optional; difflib is the fallback path.
    from .embeddings import cosine, embed, embed_many, get_model
except Exception:  # noqa: BLE001
    get_model = lambda: None  # type: ignore
    embed = lambda text: None  # type: ignore
    embed_many = lambda texts: None  # type: ignore
    cosine = lambda a, b: 0.0  # type: ignore

# Semantic similarity tier thresholds.
SEM_EXACT = 0.85
SEM_PARTIAL = 0.75
SEM_KEYWORD = 0.60

TAMIL_RANGE = ("஀", "௿")

ENGLISH_STOPWORDS = frozenset(
    {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "and", "or", "but", "if", "so", "of", "in", "on", "at", "to", "from",
        "for", "with", "by", "as", "that", "this", "these", "those",
        "it", "its", "he", "she", "they", "them", "his", "her", "their",
        "we", "us", "our", "you", "your", "i", "me", "my",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "can", "could", "should", "shall", "may", "might",
        "not", "no", "yes", "very", "just", "than", "then", "there", "here",
        "what", "which", "who", "whom", "when", "where", "why", "how",
        "today", "yesterday", "tomorrow", "now",
        "news", "claim", "video", "photo", "image", "footage",
    }
)

TAMIL_STOPWORDS = frozenset(
    {
        "ஒரு", "இது", "அது", "அந்த", "இந்த", "இங்கே", "அங்கே",
        "என்ற", "என்று", "எனக்கு", "என்னை", "நான்", "நாம்", "நாங்கள்",
        "மேலும்", "ஆனால்", "மற்றும்", "அல்லது",
        "செய்தி", "காணொளி", "படம்",
    }
)


@dataclass
class ClaimEntities:
    tokens: List[str]
    tamil_tokens: List[str]
    english_tokens: List[str]
    raw: str = ""  # normalised full claim text, for embedding-based matching

    @property
    def all_tokens(self) -> List[str]:
        return self.tokens

    def canonical(self) -> str:
        return " ".join(self.tokens)


def _has_tamil_char(text: str) -> bool:
    return any(TAMIL_RANGE[0] <= ch <= TAMIL_RANGE[1] for ch in text)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def extract_entities(claim_text: str) -> ClaimEntities:
    """Extract salient tokens (who / what / where / when candidates).

    We deliberately keep this simple:
    - Tamil script runs of 2+ chars are kept as-is (proper nouns, verbs).
    - ASCII words with 3+ chars that aren't stopwords are kept lower-cased.
    - Numbers (years, dates) are kept.
    """
    text = _normalize(claim_text)
    if not text:
        return ClaimEntities(tokens=[], tamil_tokens=[], english_tokens=[], raw="")

    tamil_tokens: List[str] = []
    english_tokens: List[str] = []

    # Tamil word tokens.
    for match in re.finditer(r"[஀-௿]+", text):
        tok = match.group(0)
        if len(tok) >= 2 and tok not in TAMIL_STOPWORDS:
            tamil_tokens.append(tok)

    # ASCII word / number tokens.
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9']+|\d{2,}", text):
        tok = match.group(0).lower().strip("'")
        if len(tok) >= 3 and tok not in ENGLISH_STOPWORDS:
            english_tokens.append(tok)

    # Preserve first-seen order while deduping.
    seen: set = set()
    ordered: List[str] = []
    for t in tamil_tokens + english_tokens:
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    return ClaimEntities(
        tokens=ordered,
        tamil_tokens=tamil_tokens,
        english_tokens=english_tokens,
        raw=text,
    )


def sequence_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def token_overlap(entities: ClaimEntities, headline: str) -> int:
    headline_norm = _normalize(headline).lower()
    count = 0
    for tok in entities.tokens:
        if tok.lower() in headline_norm:
            count += 1
    return count


def _long_token_overlap(entities: ClaimEntities, headline: str) -> int:
    """Count only long (>=6 char) topical tokens overlapping the headline.

    A single long topical token (e.g. "election", "protest") is weak but
    non-zero evidence — worth a keyword_only tier when it's the only signal.
    """
    hl = _normalize(headline).lower()
    return sum(1 for tok in entities.tokens if len(tok) >= 6 and tok.lower() in hl)


def score_headline(entities: ClaimEntities, headline: str) -> dict:
    """Score how strongly the given fact-check headline matches the claim.

    Returns a dict:
      {tier: "exact"|"partial"|"keyword_only"|"none", score: float, ratio: float, overlap: int}
    """
    if not headline or not entities.tokens:
        return {"tier": "none", "score": 0.0, "ratio": 0.0, "overlap": 0}

    canon = entities.canonical()
    ratio = sequence_ratio(canon, headline)
    overlap = token_overlap(entities, headline)
    long_overlap = _long_token_overlap(entities, headline)

    if ratio >= 0.70:
        return {"tier": "exact", "score": 1.0, "ratio": round(ratio, 3), "overlap": overlap}
    if ratio >= 0.45:
        return {"tier": "partial", "score": 0.6, "ratio": round(ratio, 3), "overlap": overlap}
    if overlap >= 2:
        return {"tier": "keyword_only", "score": 0.3, "ratio": round(ratio, 3), "overlap": overlap}
    if long_overlap >= 1:
        return {"tier": "keyword_only", "score": 0.3, "ratio": round(ratio, 3), "overlap": overlap}
    return {"tier": "none", "score": 0.0, "ratio": round(ratio, 3), "overlap": overlap}


def _semantic_tier(similarity: float, overlap: int) -> dict:
    """Map an embedding cosine similarity to a match tier + score."""
    sim = round(float(similarity), 3)
    if sim >= SEM_EXACT:
        return {"tier": "exact", "score": 1.0, "ratio": sim, "similarity": sim, "overlap": overlap}
    if sim >= SEM_PARTIAL:
        return {"tier": "partial", "score": 0.7, "ratio": sim, "similarity": sim, "overlap": overlap}
    if sim >= SEM_KEYWORD:
        return {
            "tier": "keyword_only", "score": 0.4, "ratio": sim, "similarity": sim, "overlap": overlap,
        }
    return {"tier": "none", "score": 0.0, "ratio": sim, "similarity": sim, "overlap": overlap}


def semantic_similarity(claim_text: str, headline: str) -> float:
    """Cosine similarity between claim and headline embeddings (0.0 if no model)."""
    a = embed(claim_text)
    b = embed(headline)
    if a is None or b is None:
        return 0.0
    return cosine(a, b)


def best_match(entities: ClaimEntities, candidates: Iterable[dict]) -> Optional[dict]:
    """Return the highest-scoring candidate (with scoring metadata attached).

    Uses embedding cosine similarity when the model is available (authoritative),
    and falls back to difflib/token scoring only when the model can't run.
    """
    cand_list = list(candidates)
    if not cand_list:
        return None

    # Embedding path — authoritative when the model + vectors are available.
    claim_text = entities.raw or entities.canonical()
    if claim_text and get_model() is not None:
        claim_vec = embed(claim_text)
        if claim_vec is not None:
            headlines = [(c.get("headline", "") or "") for c in cand_list]
            head_vecs = embed_many(headlines)
            if head_vecs is not None:
                best: Optional[dict] = None
                best_score = -1.0
                for cand, headline, hv in zip(cand_list, headlines, head_vecs):
                    if not headline:
                        continue
                    sim = cosine(claim_vec, hv)
                    overlap = token_overlap(entities, headline)
                    scoring = _semantic_tier(sim, overlap)
                    if scoring["score"] > best_score:
                        best_score = scoring["score"]
                        best = {**cand, **scoring}
                if best is not None and best_score > 0.0:
                    return best
                # Model ran and found no semantic match → authoritative no-match.
                return None

    # difflib fallback (model unavailable).
    best_fb: Optional[dict] = None
    best_score_fb = 0.0
    for cand in cand_list:
        headline = cand.get("headline", "") or ""
        scoring = score_headline(entities, headline)
        if scoring["score"] > best_score_fb:
            best_score_fb = scoring["score"]
            best_fb = {**cand, **scoring}
    return best_fb


if __name__ == "__main__":
    import json
    claim = "PIB debunked election fraud claim"
    ents = extract_entities(claim)
    print("entities:", ents)
    fake_hits = [
        {"headline": "PIB Fact Check: Viral election fraud video is old", "url": "x"},
        {"headline": "Cricket world cup schedule announced", "url": "y"},
    ]
    print(json.dumps(best_match(ents, fake_hits), ensure_ascii=False, indent=2))
