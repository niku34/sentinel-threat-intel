"""Claim type classifier for Veriff.

Classifies a claim into one of NEWS / ENTERTAINMENT / SCIENTIFIC / RELIGIOUS
using Tamil + English keyword sets. The classifier is intentionally rule-based
and cheap so it can gate the downstream verdict engine without an LLM hop.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

CLAIM_TYPES = ("NEWS", "ENTERTAINMENT", "SCIENTIFIC", "RELIGIOUS")


# Keyword sets. Match on lowercased whole-word for ASCII, substring for Tamil.
ENGLISH_KEYWORDS: Dict[str, List[str]] = {
    "ENTERTAINMENT": [
        "actor", "actress", "cinema", "film", "movie", "trailer", "teaser",
        "song", "album", "concert", "singer", "director", "producer",
        "kollywood", "bollywood", "tollywood", "mollywood",
        "celebrity", "celeb", "gossip", "dating", "spotted", "paparazzi",
        "award", "awards", "oscar", "grammy", "filmfare", "vijay award",
        "wedding", "engagement", "affair", "romance", "girlfriend", "boyfriend",
        "star", "hero", "heroine", "actor said", "netflix", "amazon prime",
        "hotstar", "disney+", "instagram", "reel", "vacation", "holiday",
        "maldives", "goa", "party", "premiere", "box office", "collection",
        "flop", "hit", "blockbuster", "sequel", "biopic", "birthday",
        "vijay", "ajith", "rajini", "kamal", "dhanush", "suriya", "vikram",
        "trisha", "nayanthara", "samantha", "keerthy", "anushka", "deepika",
        "shah rukh", "salman", "aamir",
    ],
    "SCIENTIFIC": [
        "vaccine", "vaccination", "covid", "coronavirus", "cancer", "diabetes",
        "cure", "cures", "medicine", "medical", "doctor", "hospital",
        "who", "icmr", "aiims", "research", "study", "trial", "clinical",
        "gene", "dna", "vaccine causes", "5g", "microchip", "immunity",
        "virus", "bacteria", "antibiotic", "drug", "fda", "cdsco",
        "climate", "warming", "temperature", "carbon", "emissions",
        "nasa", "isro", "moon", "mars", "chandrayaan",
    ],
    "RELIGIOUS": [
        "temple", "church", "mosque", "gurdwara", "shrine",
        "hindu", "muslim", "christian", "sikh", "jain", "buddhist",
        "god", "goddess", "prayer", "pooja", "puja", "festival",
        "diwali", "pongal", "ramadan", "eid", "christmas", "easter",
        "swami", "guru", "priest", "imam", "pastor", "bishop",
        "murugan", "ganesha", "shiva", "vishnu", "amman", "ayyappa",
        "sabarimala", "tirupati", "madurai meenakshi", "rameshwaram",
        "ram mandir", "kaaba", "vatican", "religious", "spiritual",
    ],
    "NEWS": [
        "election", "vote", "polling", "booth", "evm", "candidate",
        "party", "bjp", "congress", "dmk", "aiadmk", "tvk", "dmdk", "pmk",
        "minister", "cm", "pm", "governor", "president", "parliament",
        "assembly", "mla", "mp", "cabinet", "modi", "stalin", "rahul",
        "police", "arrest", "arrested", "crime", "murder", "rape", "assault",
        "protest", "rally", "strike", "bandh", "shutdown", "curfew",
        "attack", "bomb", "blast", "shooting", "encounter",
        "flood", "cyclone", "earthquake", "tsunami", "landslide",
        "disaster", "rescue", "casualty", "casualties", "dead", "injured",
        "cert-in", "cyber", "hack", "breach", "leak", "leaked",
        "pib", "government", "ministry", "budget", "gst", "tax",
        "supreme court", "high court", "verdict", "judgment", "petition",
        "border", "china", "pakistan", "military", "army", "navy", "airforce",
    ],
}

# Tamil keyword sets — used as substrings against the raw claim.
TAMIL_KEYWORDS: Dict[str, List[str]] = {
    "ENTERTAINMENT": [
        "நடிகர்", "நடிகை", "இயக்குனர்", "இசையமைப்பாளர்",
        "திரைப்படம்", "படம்", "பாடல்", "டீசர்", "டிரெய்லர்",
        "விஜய்", "அஜித்", "ரஜினி", "கமல்", "தனுஷ்", "சூர்யா", "விக்ரம்",
        "திரிஷா", "நயன்தாரா", "சமந்தா", "கீர்த்தி", "அனுஷ்கா",
        "காதல்", "காதலி", "காதலன்", "திருமணம்", "நிச்சயதார்த்தம்",
        "காணப்பட்டார்", "சந்தித்தனர்", "விடுமுறை", "விருது",
        "மாலத்தீவு", "மாலத்தீவில்", "கோவா", "பாலிவுட்", "கோலிவுட்",
        "பிறந்தநாள்", "நடிகர் சங்கம்", "சினிமா", "கிசுகிசு",
    ],
    "SCIENTIFIC": [
        "தடுப்பூசி", "கொரோனா", "கோவிட்", "புற்றுநோய்", "நீரிழிவு",
        "மருத்துவம்", "மருந்து", "மருத்துவர்", "மருத்துவமனை",
        "ஆய்வு", "ஆராய்ச்சி", "விஞ்ஞானி", "விஞ்ஞானம்",
        "நோய்", "தொற்று", "வைரஸ்", "பாக்டீரியா",
        "இஸ்ரோ", "நாசா", "சந்திரயான்", "செவ்வாய்",
    ],
    "RELIGIOUS": [
        "கோவில்", "கோயில்", "ஆலயம்", "மசூதி", "தேவாலயம்",
        "இந்து", "முஸ்லிம்", "கிறிஸ்தவ", "சீக்கிய",
        "கடவுள்", "தெய்வம்", "பூஜை", "பிரார்த்தனை",
        "தீபாவளி", "பொங்கல்", "ரமலான்", "ரம்ஜான்", "கிறிஸ்துமஸ்",
        "முருகன்", "விநாயகர்", "சிவன்", "விஷ்ணு", "அம்மன்", "ஐயப்பன்",
        "சபரிமலை", "திருப்பதி", "மதுரை மீனாட்சி", "ராமேஸ்வரம்",
        "சாமியார்", "சுவாமி", "பாதிரியார்", "இமாம்",
    ],
    "NEWS": [
        "தேர்தல்", "வாக்கு", "வாக்குச்சாவடி", "வேட்பாளர்", "கட்சி",
        "பாஜக", "காங்கிரஸ்", "திமுக", "அதிமுக", "தவெக", "தமிழக",
        "அமைச்சர்", "முதலமைச்சர்", "முதல்வர்", "பிரதமர்", "ஆளுநர்",
        "காவல்துறை", "போலீஸ்", "கைது", "குற்றம்", "கொலை", "பாலியல்",
        "போராட்டம்", "ஊர்வலம்", "வேலைநிறுத்தம்", "பந்த்",
        "தாக்குதல்", "வெடிகுண்டு", "வெடிப்பு",
        "வெள்ளம்", "புயல்", "நிலநடுக்கம்", "சுனாமி", "நிலச்சரிவு",
        "பேரிடர்", "காயம்", "மரணம்", "இறந்தார்", "காயமடைந்தார்",
        "அரசு", "அமைச்சகம்", "பட்ஜெட்", "வரி",
        "உச்சநீதிமன்றம்", "உயர்நீதிமன்றம்", "தீர்ப்பு",
        "ராணுவம்", "எல்லை", "சீனா", "பாகிஸ்தான்",
        "சென்னை", "மதுரை", "கோயம்புத்தூர்", "திருச்சி",
    ],
}


# Priority when a claim matches multiple categories.
# Entertainment wins over News when both hit — the whole point of the
# classifier is to stop gossip masquerading as hard news.
_PRIORITY = ("ENTERTAINMENT", "RELIGIOUS", "SCIENTIFIC", "NEWS")


@dataclass
class ClaimClassification:
    claim_type: str
    confidence: float
    matched_terms: Dict[str, List[str]] = field(default_factory=dict)
    scores: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "claim_type": self.claim_type,
            "classifier_confidence": round(self.confidence, 3),
            "matched_terms": self.matched_terms,
            "scores": self.scores,
        }


def _has_tamil(text: str) -> bool:
    return any("஀" <= ch <= "௿" for ch in text)


def _normalize_ascii(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"[^\w\s]", " ", normalized.lower())


def _count_english(text_lower: str, keywords: List[str]) -> List[str]:
    hits: List[str] = []
    for kw in keywords:
        kw_lower = kw.lower()
        if " " in kw_lower:
            if kw_lower in text_lower:
                hits.append(kw)
        else:
            if re.search(rf"\b{re.escape(kw_lower)}\b", text_lower):
                hits.append(kw)
    return hits


def _count_tamil(raw: str, keywords: List[str]) -> List[str]:
    return [kw for kw in keywords if kw in raw]


def classify_claim(claim_text: str) -> ClaimClassification:
    """Return the best-matching claim type for the given text.

    Uses substring / word matching against Tamil + English keyword sets.
    Falls back to NEWS with low confidence when nothing matches — this keeps
    the pipeline moving but the downstream verdict engine still needs DB
    matches before it will call anything REAL.
    """
    if not claim_text or not claim_text.strip():
        return ClaimClassification(claim_type="NEWS", confidence=0.0)

    raw = claim_text
    ascii_lower = _normalize_ascii(raw)
    tamil_present = _has_tamil(raw)

    scores: Dict[str, int] = {}
    matched: Dict[str, List[str]] = {}
    for ctype in CLAIM_TYPES:
        english_hits = _count_english(ascii_lower, ENGLISH_KEYWORDS[ctype])
        tamil_hits = _count_tamil(raw, TAMIL_KEYWORDS[ctype]) if tamil_present else []
        hits = english_hits + tamil_hits
        scores[ctype] = len(hits)
        if hits:
            matched[ctype] = hits

    # Pick winner: highest score; break ties by priority order.
    max_score = max(scores.values())
    if max_score == 0:
        return ClaimClassification(
            claim_type="NEWS",
            confidence=0.1,
            matched_terms={},
            scores=scores,
        )

    candidates = [t for t in _PRIORITY if scores[t] == max_score]
    winner = candidates[0]

    # Confidence: hit ratio scaled, capped at 0.95.
    total_hits = sum(scores.values())
    confidence = min(0.95, 0.4 + 0.55 * (scores[winner] / max(1, total_hits)))

    return ClaimClassification(
        claim_type=winner,
        confidence=confidence,
        matched_terms=matched,
        scores=scores,
    )


def is_entertainment(claim_type: str) -> bool:
    return claim_type == "ENTERTAINMENT"


def is_news(claim_type: str) -> bool:
    return claim_type == "NEWS"


if __name__ == "__main__":
    import json
    samples: Tuple[str, ...] = (
        "விஜய் திரிஷா மாலத்தீவில் சந்தித்தனர்",
        "Chennai protest footage today",
        "PIB debunked election fraud claim",
        "New COVID vaccine causes side effects",
        "Sabarimala temple opens for pilgrims",
    )
    for s in samples:
        result = classify_claim(s)
        print(s)
        print(" ", json.dumps(result.to_dict(), ensure_ascii=False))
