"""
distractors.py
--------------
Turns "question + correct answer" into a four-option MCQ, entirely offline.

Strategy, in order of preference:

  1. Typed generators - numeric values, percentages, years, yes/no, true/false,
     abbreviation expansions. These produce answers that sit right next to the
     correct one, which is what makes an option believable.
  2. Bank mining - pull real answers from other questions in the same subject,
     ranked by how much vocabulary they share with this question and answer.
     Real exam wording beats anything synthesised.
  3. Generic same-topic fallback - any same-length answer from the subject.

Everything is deterministic: the same question always yields the same options,
so a cached set and a freshly generated set never disagree.

The engine is deliberately a plain class with one `generate()` method. To swap
in a local LLM later (Ollama, llama.cpp), implement the same method and hand
the object to `builder.build()` - nothing else in the app needs to change.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from collections import defaultdict

OPTION_COUNT = 4

STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or", "is",
    "are", "be", "by", "with", "that", "this", "it", "as", "from", "was",
    "were", "you", "your", "what", "which", "when", "where", "who", "how",
    "why", "shall", "should", "must", "can", "may", "will", "would", "if",
    "not", "no", "do", "does", "did", "has", "have", "had", "there", "their",
    "his", "her", "its", "all", "any", "one", "two", "into", "out", "up",
    "down", "over", "under", "than", "then", "so", "such", "about",
}

_WORD = re.compile(r"[a-z0-9]+")
# Grouped thousands first ("1,500"), then a plain number. The thousands
# branch demands at least one ",ddd" group, otherwise "1983" would match as
# "198" followed by a stray "3". The lookbehind keeps digits that are glued to
# letters out of it - the "2" in "m2" or "CO2" is not a quantity.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9.,])"
    r"(?:[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?|[-+]?\.\d+)"
    r"(?![0-9])"
)


def tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower())
            if w not in STOPWORDS and len(w) > 2}


def _seed(text: str) -> random.Random:
    """Deterministic RNG derived from the answer text."""
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return random.Random(int(h[:12], 16))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# ==========================================================================
# Typed generators
# ==========================================================================

_YES = {"yes", "yes.", "yes, it is", "affirmative"}
_NO = {"no", "no.", "no, it is not", "negative"}
_TRUE = {"true", "true.", "correct"}
_FALSE = {"false", "false.", "incorrect", "wrong"}

_CONDITIONAL = [
    "Yes, but only with the Master's written permission",
    "Yes, provided it is recorded in the official log book",
    "No, unless the vessel is in immediate danger",
    "No, except with permission from the coastal State",
    "Only outside a special area",
    "Only when the vessel is en route",
]

_TF_STATEMENTS = [
    "True, but only for vessels above 500 GT",
    "False, the requirement applies to all vessels",
    "True, provided the equipment is type-approved",
    "False, this applies only inside special areas",
]


def _fmt_like(value: float, model: str) -> str:
    """Format `value` with the same precision and grouping as `model`."""
    grouped = "," in model
    if "." in model:
        decimals = len(model.split(".")[1])
        return f"{value:{',' if grouped else ''}.{decimals}f}"
    if abs(value - round(value)) < 1e-9:
        n = int(round(value))
        return f"{n:,}" if grouped else str(n)
    return f"{value:{',' if grouped else ''}.1f}"


def _numeric_variants(answer: str) -> list[str]:
    """
    Build nearby values for an answer that is essentially a number plus a unit.
    "21%" -> 18%, 19.5%, 23%.  "37 m2" -> 28 m2, 45 m2, 55 m2.
    """
    matches = list(_NUMBER.finditer(answer))
    if len(matches) != 1:
        return []
    m = matches[0]
    raw = m.group(0)
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return []

    rng = _seed(answer)
    sign = -1.0 if value < 0 else 1.0
    value = abs(value)                       # e.g. "-5 degrees C"
    is_percent = "%" in answer
    is_year = 1850 <= value <= 2100 and abs(value - round(value)) < 1e-9 and len(raw) == 4

    if is_year:
        deltas = [-6, -4, -2, 2, 4, 6]
        candidates = [value + d for d in deltas]
    elif is_percent and 0 < value <= 100:
        steps = [-8, -5, -3, -1.5, 1.5, 3, 5, 8]
        candidates = [value + s for s in steps if 0 < value + s <= 100]
    elif value == 0:
        candidates = [1, 2, 5, 10]
    elif abs(value - round(value)) < 1e-9 and 1 <= value <= 12:
        # Small whole numbers are usually counts, classes or annex numbers.
        # Neighbours read as plausible; multiples ("Class 27") do not.
        candidates = [value - 2, value - 1, value + 1, value + 2, value + 3]
    else:
        factors = [0.25, 0.5, 0.75, 1.5, 2.0, 3.0]
        candidates = [value * f for f in factors]

    candidates = [c for c in candidates if c > 0 and abs(c - value) > 1e-9]
    rng.shuffle(candidates)

    out, seen = [], {_norm(answer)}
    for c in candidates:
        text = answer[:m.start()] + _fmt_like(sign * c, raw) + answer[m.end():]
        key = _norm(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) == OPTION_COUNT - 1:
            break
    return out


def _boolean_variants(answer: str) -> list[str]:
    low = answer.strip().lower()
    rng = _seed(answer)
    if low in _YES or low in _NO:
        counterpart = "No" if low in _YES else "Yes"
        extra = rng.sample(_CONDITIONAL, 2)
        return [counterpart] + extra
    if low in _TRUE or low in _FALSE:
        counterpart = "False" if low in _TRUE else "True"
        extra = rng.sample(_TF_STATEMENTS, 2)
        return [counterpart] + extra
    return []


_ABBREV_SWAPS = [
    ("Safety", "Security"), ("Safety", "Standards"), ("Life", "Lives"),
    ("Life", "Living"), ("Sea", "Shipping"), ("International", "Intergovernmental"),
    ("Convention", "Code"), ("Code", "Convention"), ("Maritime", "Marine"),
    ("Organization", "Organisation Authority"), ("Prevention", "Protection"),
    ("Pollution", "Contamination"), ("Management", "Monitoring"),
    ("Standards", "Statutes"), ("Training", "Testing"), ("Global", "General"),
    ("Distress", "Disaster"), ("Emergency", "Escape"), ("Certificate", "Confirmation"),
]


def _expansion_variants(question: str, answer: str) -> list[str]:
    """
    For "What does X stand for?" style questions, mutate one word of the
    correct expansion so the distractor stays in the same register.
    """
    if not re.search(r"stand\s+for|abbreviat|acronym|full\s+form|stands\s+for",
                     question, re.I):
        return []
    words = answer.split()
    if len(words) < 3:
        return []

    rng = _seed(answer)
    out, seen = [], {_norm(answer)}
    swaps = _ABBREV_SWAPS[:]
    rng.shuffle(swaps)

    for src, dst in swaps:
        for i, w in enumerate(words):
            if w.strip(",.").lower() != src.lower():
                continue
            new = words[:]
            new[i] = dst
            text = " ".join(new)
            key = _norm(text)
            if key not in seen:
                seen.add(key)
                out.append(text)
            break
        if len(out) == OPTION_COUNT - 1:
            break
    return out


# ==========================================================================
# Engine
# ==========================================================================

class DistractorEngine:
    """
    Build once from the whole bank, then call `generate()` per question.

    `records` is a list of dicts with keys: id, question, answer, topic.
    """

    def __init__(self, records: list[dict]):
        self.answers: list[str] = []
        self.topics: list[str] = []
        self.answer_tokens: list[set[str]] = []
        self.by_topic: dict[str, list[int]] = defaultdict(list)
        self.index: dict[str, list[int]] = defaultdict(list)
        self._norm_seen: dict[str, int] = {}

        for rec in records:
            ans = (rec.get("answer") or "").strip()
            if not ans:
                continue
            key = _norm(ans)
            if key in self._norm_seen:      # keep the answer pool unique
                continue
            i = len(self.answers)
            self._norm_seen[key] = i
            self.answers.append(ans)
            self.topics.append(rec.get("topic") or "General")
            tk = tokens(ans)
            self.answer_tokens.append(tk)
            self.by_topic[self.topics[-1]].append(i)
            for t in tk:
                if len(self.index[t]) < 4000:      # cap runaway common tokens
                    self.index[t].append(i)

        self.total = len(self.answers)

    # -- candidate scoring -------------------------------------------------

    def _candidates(self, q_tokens: set[str], a_tokens: set[str],
                    topic: str, limit: int = 600) -> set[int]:
        pool: set[int] = set()
        for t in list(a_tokens)[:14]:
            pool.update(self.index.get(t, ())[:120])
        if len(pool) < 60:
            for t in list(q_tokens)[:14]:
                pool.update(self.index.get(t, ())[:80])
                if len(pool) > limit:
                    break
        if len(pool) < 30:
            pool.update(self.by_topic.get(topic, ())[:limit])
        return pool

    def _mine_bank(self, question: str, answer: str, topic: str,
                   need: int) -> list[str]:
        a_tokens = tokens(answer)
        q_tokens = tokens(question)
        a_norm = _norm(answer)
        a_len = max(len(answer), 1)
        a_words = len(answer.split())

        scored = []
        for i in self._candidates(q_tokens, a_tokens, topic):
            cand = self.answers[i]
            c_norm = _norm(cand)
            if c_norm == a_norm:
                continue
            c_tokens = self.answer_tokens[i]

            # Reject near-duplicates: they might be correct too.
            if a_tokens and c_tokens:
                union = len(a_tokens | c_tokens)
                inter = len(a_tokens & c_tokens)
                jaccard = inter / union if union else 0.0
                if jaccard > 0.8:
                    continue
                # "Bipolar transistor" next to "Transistor" is a trick, not a
                # distractor: reject short options that merely qualify or
                # abbreviate the correct answer.
                if (a_tokens <= c_tokens or c_tokens <= a_tokens) \
                        and min(len(a_tokens), len(c_tokens)) <= 3:
                    continue
            else:
                jaccard = 0.0

            # Length similarity: options of wildly different length give the
            # answer away visually.
            ratio = min(len(cand), a_len) / max(len(cand), a_len)
            if ratio < 0.25:
                continue
            if a_words <= 3 and len(cand.split()) > a_words + 4:
                continue

            q_overlap = len(c_tokens & q_tokens) / (len(q_tokens) or 1)
            same_topic = 1.0 if self.topics[i] == topic else 0.0

            score = (2.4 * jaccard) + (1.6 * q_overlap) + (0.9 * same_topic) \
                    + (0.8 * ratio) - (0.0002 * abs(len(cand) - a_len))
            scored.append((score, i))

        if not scored:
            return []

        scored.sort(key=lambda s: (-s[0], s[1]))
        rng = _seed(answer)
        # Take from a slightly wider band so options are not three paraphrases
        # of the same sentence.
        band = scored[: max(need * 6, 12)]
        picked, seen_tokens = [], []
        for _, i in band:
            cand = self.answers[i]
            ct = self.answer_tokens[i]
            if any(ct and st and len(ct & st) / len(ct | st) > 0.7
                   for st in seen_tokens):
                continue
            picked.append(cand)
            seen_tokens.append(ct)
            if len(picked) == need:
                break
        if len(picked) < need:
            for _, i in band:
                cand = self.answers[i]
                if cand not in picked:
                    picked.append(cand)
                if len(picked) == need:
                    break
        rng.shuffle(picked)
        return picked[:need]

    def _fallback(self, answer: str, topic: str, need: int,
                  used: list[str]) -> list[str]:
        rng = _seed(answer + topic)
        pool = self.by_topic.get(topic) or list(range(self.total))
        if not pool:
            return []
        taken = {_norm(answer)} | {_norm(u) for u in used}
        out = []
        idxs = list(pool)
        rng.shuffle(idxs)
        for i in idxs[:400]:
            cand = self.answers[i]
            if _norm(cand) in taken:
                continue
            taken.add(_norm(cand))
            out.append(cand)
            if len(out) == need:
                break
        return out

    # -- public API --------------------------------------------------------

    def generate(self, question: str, answer: str, topic: str = "General") -> list[str]:
        """Return exactly OPTION_COUNT-1 wrong options (best effort)."""
        answer = (answer or "").strip()
        need = OPTION_COUNT - 1
        out: list[str] = []

        for typed in (_boolean_variants(answer),
                      _expansion_variants(question, answer),
                      _numeric_variants(answer)):
            if typed:
                out = typed[:need]
                break

        if len(out) < need:
            taken = {_norm(answer)} | {_norm(o) for o in out}
            for cand in self._mine_bank(question, answer, topic, need - len(out) + 3):
                if _norm(cand) in taken:
                    continue
                taken.add(_norm(cand))
                out.append(cand)
                if len(out) == need:
                    break

        if len(out) < need:
            out += self._fallback(answer, topic, need - len(out), out)

        return out[:need]

    def build_options(self, question: str, answer: str,
                      topic: str = "General") -> tuple[list[str], int]:
        """
        Return (options, correct_index) with options in a deterministic
        shuffled order, so the correct answer is not always in slot A.
        """
        wrong = self.generate(question, answer, topic)
        options = [answer] + wrong
        rng = _seed(question + answer)
        rng.shuffle(options)
        return options, options.index(answer)


def preview(records: list[dict], n: int = 5) -> None:
    """Small helper for eyeballing output quality from the command line."""
    eng = DistractorEngine(records)
    for rec in records[:n]:
        opts, ci = eng.build_options(rec["question"], rec["answer"], rec.get("topic", ""))
        print("Q:", rec["question"])
        for j, o in enumerate(opts):
            print(f"   {'ABCD'[j]}. {o}" + ("   <-- correct" if j == ci else ""))
        print()
