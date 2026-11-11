"""
parser.py
---------
Reads a question bank from CSV / XLSX / JSON / TXT, cleans it, tags each
question with a subject, and returns a list of {"question", "answer", "topic"}.

Every reader is defensive: a malformed row is skipped, never fatal.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

import config

# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

# Page furniture left behind by PDF-to-CSV conversions, e.g.
# "of  850 Page 12 Version 1.0.11 , 2014 (c) ... SHIPPING EXAMS 2020"
_FOOTER_PATTERNS = [
    re.compile(r"\s*(?:of\s+\d+\s+)?Page\s+\d+\s+Version[\s\S]*$", re.I),
    re.compile(r"\s*instagram\s*I'?d\s*:?-?\s*\S+\s*$", re.I),
    re.compile(r"\s*\u00a9\s*[A-Z][A-Za-z.\s]{0,40}[\s\S]*$"),
    re.compile(r"\s*SHIPPING\s+EXAMS\s+\d{4}[\s\S]*$", re.I),
    re.compile(r"\s*\d{3,5}\s*\+\s*MC\s*$", re.I),
]

_LEADING_NUMBER = re.compile(r"^\s*(?:Q(?:uestion)?\s*)?\d{1,5}\s*[.)\-:]\s+", re.I)
_TRAILING_STRAY = re.compile(r"(?<=[?.:])\s+\d{1,3}\s*$")   # "... cover? 8"
_WHITESPACE = re.compile(r"\s+")


def scrub(text: str) -> str:
    """Strip converter artefacts and normalise whitespace."""
    if text is None:
        return ""
    t = str(text).replace("\u00a0", " ")
    for pat in _FOOTER_PATTERNS:
        t = pat.sub("", t)
    t = _WHITESPACE.sub(" ", t).strip()
    t = _LEADING_NUMBER.sub("", t)
    t = _TRAILING_STRAY.sub("", t)
    return t.strip(" .\u2013-")


def _strip_bleed(question: str, next_answer: str) -> str:
    """
    Some exported banks append the *next* row's answer to the end of the
    question cell. If we can see that suffix, remove it.
    """
    if not next_answer or len(next_answer) < 3:
        return question
    if question.endswith(next_answer):
        return question[: -len(next_answer)].strip()
    return question


# --------------------------------------------------------------------------
# Subject tagging
# --------------------------------------------------------------------------

TOPIC_KEYWORDS = {
    "COLREG": ["colreg", "rule of the road", "give-way", "stand-on", "overtaking",
               "head-on", "sidelight", "masthead light", "not under command",
               "restricted in her ability", "sound signal", "steering and sailing"],
    "Navigation": ["gyro", "compass", "chart", "bearing", "position line", "gps",
                   "radar", "arpa", "ecdis", "latitude", "longitude", "great circle",
                   "sextant", "buoy", "iala", "passage plan", "tide", "echo sounder",
                   "rhumb", "azimuth", "deviation", "variation"],
    "Meteorology": ["weather", "wind", "cyclone", "depression", "anticyclone",
                    "fog", "barometer", "isobar", "front", "swell", "beaufort",
                    "tropical revolving", "visibility", "monsoon", "humidity"],
    "Ship Stability": ["stability", "gm", "metacentr", "free surface", "trim",
                       "list", "draft", "draught", "displacement", "righting",
                       "loll", "kg ", "bm ", "tpc", "hydrostatic", "load line"],
    "Cargo": ["cargo", "hold", "hatch", "lashing", "dunnage", "imsbc", "imdg",
              "tanker", "ullage", "stowage", "container", "bulk", "loading",
              "discharg", "crude oil washing", "cow ", "inert gas", "lng", "lpg",
              "grain", "bill of lading", "manifold"],
    "Fire Safety": ["fire", "foam", "extinguish", "co2", "sprinkler", "smoke",
                    "flammable", "flash point", "combust", "fixed installation",
                    "fire main", "detector"],
    "Life Saving": ["lifeboat", "liferaft", "lifejacket", "life-saving", "lsa",
                    "immersion suit", "epirb", "sart", "abandon ship", "muster",
                    "rescue boat", "davit", "line-throwing", "survival craft"],
    "SOLAS": ["solas", "safety of life at sea", "safety construction",
              "safety equipment certificate"],
    "MARPOL": ["marpol", "pollution", "oily water", "sewage", "garbage",
               "annex i", "annex ii", "annex iv", "annex v", "annex vi",
               "sox", "nox", "ballast water", "sopep", "orb", "oil record"],
    "ISM / ISPS": ["ism code", "safety management", "sms", "isps", "ship security",
                   "ssp", "ssas", "security level", "dpa", "designated person",
                   "audit", "non-conformity", "port state control", "psc"],
    "GMDSS": ["gmdss", "dsc", "vhf", "mf/hf", "inmarsat", "navtex", "distress alert",
              "radio", "sea area a1", "sea area a2", "mmsi", "sat-c"],
    "Engine": ["engine", "boiler", "pump", "compressor", "diesel", "turbo",
               "lubric", "cooling water", "purifier", "generator", "shaft",
               "valve", "bearing", "fuel oil", "refrigerat", "freon", "crankcase"],
    "Electrical": ["voltage", "current", "circuit", "transistor", "diode",
                   "resistor", "capacitor", "insulation", "alternator", "battery",
                   "switchboard", "earth fault", "megger"],
    "Medical": ["first aid", "casualty", "cpr", "bleeding", "burn", "fracture",
                "resuscitat", "medical", "unconscious", "poison", "hypothermia"],
    "Watchkeeping": ["watch", "bridge", "lookout", "handover", "master's standing",
                     "stcw", "rest hours", "log book", "pilot"],
    "Anchoring & Mooring": ["anchor", "moor", "berth", "windlass", "rope", "wire",
                            "cable", "tug", "bollard", "capstan", "let go"],
    "Enclosed Space": ["enclosed space", "confined space", "permit to work",
                       "oxygen content", "gas free", "entry permit"],
}

_TOPIC_ORDER = list(TOPIC_KEYWORDS)


def classify(question: str, answer: str) -> str:
    """Assign a subject using keyword hits. Falls back to 'General'."""
    blob = f"{question} {answer}".lower()
    best, best_hits = "General", 0
    for topic in _TOPIC_ORDER:
        hits = sum(1 for kw in TOPIC_KEYWORDS[topic] if kw in blob)
        if hits > best_hits:
            best, best_hits = topic, hits
    return best


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------

_Q_KEYS = ("question", "questions", "q", "ques", "prompt")
_A_KEYS = ("answer", "answers", "a", "ans", "correct", "correct answer", "correct_answer")
_E_KEYS = ("explanation", "explain", "note", "notes", "reason")
_T_KEYS = ("topic", "subject", "category", "section")


def _pick(header_map: dict, names) -> str | None:
    for n in names:
        if n in header_map:
            return header_map[n]
    return None


def _rows_from_records(records: list[dict]) -> list[dict]:
    """Normalise a list of dict-rows with unknown column names."""
    if not records:
        return []
    header_map = {str(k).strip().lower(): k for k in records[0].keys()}
    qk = _pick(header_map, _Q_KEYS)
    ak = _pick(header_map, _A_KEYS)
    ek = _pick(header_map, _E_KEYS)
    tk = _pick(header_map, _T_KEYS)

    if qk is None or ak is None:          # fall back to first two columns
        keys = list(records[0].keys())
        if len(keys) < 2:
            return []
        qk, ak = keys[0], keys[1]

    out = []
    for r in records:
        out.append({
            "question": "" if r.get(qk) is None else str(r.get(qk)),
            "answer": "" if r.get(ak) is None else str(r.get(ak)),
            "explanation": str(r.get(ek) or "") if ek else "",
            "topic": str(r.get(tk) or "") if tk else "",
        })
    return out


def _read_csv(path: Path) -> list[dict]:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                sample = fh.read(8192)
                fh.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                except csv.Error:
                    dialect = csv.excel
                return _rows_from_records(list(csv.DictReader(fh, dialect=dialect)))
        except UnicodeDecodeError:
            continue
        except Exception as exc:                       # corrupt file
            print(f"[parser] CSV read failed: {exc}", file=sys.stderr)
            return []
    return []


def _read_xlsx(path: Path) -> list[dict]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("[parser] openpyxl not installed; cannot read Excel.", file=sys.stderr)
        return []
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            return []
        header = [str(h).strip() if h is not None else f"col{i}"
                  for i, h in enumerate(header)]
        records = [dict(zip(header, r)) for r in rows]
        wb.close()
        return _rows_from_records(records)
    except Exception as exc:
        print(f"[parser] Excel read failed: {exc}", file=sys.stderr)
        return []


def _read_json(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        print(f"[parser] JSON read failed: {exc}", file=sys.stderr)
        return []
    if isinstance(data, dict):
        for key in ("questions", "data", "items", "bank"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    return _rows_from_records([d for d in data if isinstance(d, dict)])


_TXT_Q = re.compile(r"^\s*(?:Q(?:uestion)?\s*\d*\s*[:.)-]|\d+\s*[.)])\s*(.+)$", re.I)
_TXT_A = re.compile(r"^\s*(?:A(?:ns(?:wer)?)?\s*\d*\s*[:.)-])\s*(.+)$", re.I)


def _read_txt(path: Path) -> list[dict]:
    """
    Accepts either explicit 'Question:' / 'Answer:' labels or plain
    alternating lines (question, answer, blank, question, answer ...).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f"[parser] TXT read failed: {exc}", file=sys.stderr)
        return []

    out, pending_q, pending_a = [], None, None
    labelled = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        mq, ma = _TXT_Q.match(line), _TXT_A.match(line)
        if mq:
            labelled = True
            if pending_q and pending_a:
                out.append({"question": pending_q, "answer": pending_a,
                            "explanation": "", "topic": ""})
            pending_q, pending_a = mq.group(1), None
        elif ma:
            labelled = True
            pending_a = ma.group(1)
        elif labelled:
            if pending_a is not None:
                pending_a += " " + line
            elif pending_q is not None:
                pending_q += " " + line
        else:                                    # unlabelled alternating lines
            if pending_q is None:
                pending_q = line
            else:
                out.append({"question": pending_q, "answer": line,
                            "explanation": "", "topic": ""})
                pending_q = None

    if pending_q and pending_a:
        out.append({"question": pending_q, "answer": pending_a,
                    "explanation": "", "topic": ""})
    return out


_READERS = {
    ".csv": _read_csv, ".tsv": _read_csv,
    ".xlsx": _read_xlsx, ".xls": _read_xlsx,
    ".json": _read_json,
    ".txt": _read_txt,
}


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def load_bank(path) -> list[dict]:
    """
    Parse a question bank file and return cleaned, deduplicated rows.
    Returns [] rather than raising if the file is unreadable.
    """
    path = Path(path)
    if not path.exists():
        print(f"[parser] File not found: {path}", file=sys.stderr)
        return []

    reader = _READERS.get(path.suffix.lower())
    if reader is None:
        print(f"[parser] Unsupported file type: {path.suffix}", file=sys.stderr)
        return []

    raw = reader(path)
    if not raw:
        return []

    answers = [scrub(r.get("answer", "")) for r in raw]

    cleaned, seen = [], set()
    for i, row in enumerate(raw):
        try:
            q = str(row.get("question", ""))
            nxt = answers[i + 1] if i + 1 < len(answers) else ""
            q = scrub(_strip_bleed(scrub(q), nxt))
            a = answers[i]
            if len(q) < config.MIN_QUESTION_LEN or len(a) < config.MIN_ANSWER_LEN:
                continue
            key = re.sub(r"[^a-z0-9]", "", q.lower())[:160]
            if key in seen:
                continue
            seen.add(key)
            topic = scrub(row.get("topic", "")) or classify(q, a)
            cleaned.append({
                "question": q,
                "answer": a,
                "explanation": scrub(row.get("explanation", "")),
                "topic": topic,
            })
        except Exception:
            continue                              # one bad row never stops import
    return cleaned


if __name__ == "__main__":
    for p in sys.argv[1:]:
        rows = load_bank(p)
        print(f"{p}: {len(rows)} usable questions")
        for r in rows[:3]:
            print("  ", r)
