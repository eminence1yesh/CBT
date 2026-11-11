# Exit Exam Trainer

An offline MCQ trainer for Merchant Navy Exit Exam and CDC computer based test
preparation. Runs entirely on your own computer: no internet, no accounts, no
cloud, no API keys. Your question bank and your progress never leave the machine.

The bank shipped with it holds **7,595 questions** drawn from `questions.csv`,
already split into 76 sets of 100.

---

## Getting started

### Windows

Double-click **`run.bat`**.

The first run creates a private Python environment and installs Flask, which
takes a minute or two. After that it starts in a couple of seconds. Your
browser opens at `http://127.0.0.1:5000` automatically.

If Windows says Python is not installed, get it from
[python.org/downloads](https://www.python.org/downloads/) and tick
**"Add Python to PATH"** during setup.

### macOS / Linux

```bash
chmod +x run.sh     # first time only
./run.sh
```

Then open `http://127.0.0.1:5000`.

### Manual start

```bash
pip install -r requirements.txt
python app.py
```

To stop the app, close the terminal window or press `Ctrl+C` in it.

---

## Using it

### Keyboard

The app is built to be driven from the keyboard, because that is roughly three
times faster than clicking when you are working through hundreds of questions.

| Key | Does |
| --- | --- |
| `1` `2` `3` `4` | Choose an option |
| `Enter` | Next question |
| `Backspace` | Previous question |
| `Space` | Reveal the answer |
| `S` | Skip |
| `B` | Bookmark |
| `Esc` | Leave the set and go back to setup |

### Modes

**Practice** — marks each answer the moment you choose it and shows the correct
answer. The question locks once answered so you cannot fish for the right one.

**Exam** — timed at one minute per question, so a full set of 100 gives you 100
minutes. No feedback until you press *Submit set*. You can change answers freely
until then. When the clock hits zero it submits for you.

**Flashcard** — question only, no options. `Space` reveals the answer, `Enter`
moves on. Nothing is scored.

**Revision** — practice restricted to questions you have previously got wrong.

### Choosing what to work on

Filter by subject, by state (not yet attempted / previously wrong / bookmarked /
already attempted), or by searching the question and answer text. The count under
the filters updates as you type, and the *Set* dropdown re-slices to match. Turn
shuffling on for questions, options, or both.

### Progress

Everything is saved as you go: current position, bookmarks, wrong answers,
per-question timings. Close the browser mid-set, or lose power, and the
**Resume last session** button on the setup screen picks up where you stopped.
Progress is written to SQLite in write-ahead-log mode, so an unexpected shutdown
costs you at most the question you were on.

### Statistics and export

The Statistics screen shows accuracy, average time per question, how much of the
bank you have covered, and a per-subject table with weak subjects in red and
strong ones in green.

Export the performance report as CSV or Excel, and your wrong questions or
bookmarks as CSV. For a PDF, use **Save as PDF** — it opens your browser's print
dialogue, where you choose "Save as PDF" as the destination. This avoids adding a
PDF library the app would otherwise never use.

---

## Adding more questions

Use **Import questions** on the setup screen, or the command line:

```bash
python import_bank.py my_new_bank.xlsx
python import_bank.py bank1.csv bank2.json notes.txt
```

Accepted formats:

| Format | Expected shape |
| --- | --- |
| **CSV / TSV** | A question column and an answer column. Delimiter is detected. |
| **Excel** | `.xlsx` or `.xls`, first sheet, header row on line 1. |
| **JSON** | An array of objects, or `{"questions": [...]}`. |
| **TXT** | `Question:` / `Answer:` labels, or plain alternating lines. |

Column names are matched loosely — `question`, `Q`, `ques`, `prompt` all work for
the question, and `answer`, `A`, `ans`, `correct answer` for the answer. Optional
`explanation` and `topic` columns are used if present; otherwise the subject is
inferred from keywords. If no recognisable names are found, the first two columns
are used.

Duplicates are detected by normalised question text and skipped, so re-importing
the same file is harmless.

### About your original CSV

`questions.csv` had two defects from whatever converted it out of a PDF:

1. Every `Question` cell had the **next row's answer** appended to it.
2. Page furniture was baked in — `of 850 Page 3 Version 1.0.11 , 2014 ...`.

`parser.py` strips both automatically, along with leading question numbers and
stray trailing digits, then drops rows too short to be real and removes
duplicates. That takes 9,346 raw rows down to 7,595 clean ones. The original
file is untouched — cleaning happens on read, every time.

---

## How the options are generated

This was the hard part. Three wrong options have to be believable, on-topic, and
never accidentally correct — with no internet and no model to call.

`distractors.py` tries three approaches in order.

**1. Typed generators.** If the answer has a recognisable shape, distractors are
built directly from it:

| Answer shape | Approach | Example |
| --- | --- | --- |
| Percentage | Nearby values, clamped to 0–100 | `21%` → 19.5%, 22.5%, 24% |
| Small whole number | Neighbours, not multiples | `Class 9` → 7, 11, 12 |
| Year | ±2, ±4, ±6 | `1983` → 1977, 1981, 1989 |
| Quantity with a unit | Scaled, unit and precision preserved | `1,500 kg` → 375 / 1,125 / 2,250 kg |
| Yes / No | The counterpart plus two conditional statements | `No` → Yes; Yes, but only with the Master's written permission; Only outside a special area |
| True / False | The counterpart plus two qualified statements | |
| Abbreviation expansion | One word swapped for a plausible neighbour | `Safety of Life at Sea` → Safety of **Lives** at Sea, **Security** of Life at Sea, Safety of **Living** at Sea |

**2. Bank mining.** For everything else — and that is most questions — the engine
pulls real answers from other questions in your own bank. An inverted token index
narrows thousands of candidates down to a few hundred, which are then scored on
vocabulary shared with the correct answer, vocabulary shared with the question
(this is what keeps them on-topic), whether they come from the same subject, and
similarity of length. Real exam wording beats anything synthesised, and it means
distractors sound exactly like the rest of your bank.

Candidates are rejected if they overlap the correct answer too heavily, if they
are a subset or superset of it — "Bipolar transistor" must not appear when the
answer is "Transistor" — or if they are so much shorter or longer that the odd
one out gives itself away.

**3. Same-subject fallback.** If the first two come up short, any answer of
comparable length from the same subject fills the gap.

Generation is deterministic: the same question always produces the same options,
so a cached set and a freshly built one never disagree. Everything is generated
once at import and cached in SQLite. Nothing is ever regenerated unless you ask
for it:

```bash
python import_bank.py --rebuild
```

All 7,595 questions were checked: every one has four unique options with a valid
correct index. Full generation takes about 12 seconds; after that a set of 100
loads in roughly 13 milliseconds.

### The honest limitation

Because distractors are mined from your own bank, occasional near-synonyms slip
through. One real example: where the correct answer was *"FSE and virtual loss of
GM"*, the engine offered *"Excessive free surface effect resulting in loss of
vessel stability"* as a distractor. They share almost no vocabulary, so no rule
catches it, but they mean much the same thing.

Bookmark those when you meet them. Catching them properly needs semantics, not
rules — which is what the next section is for.

---

## Plugging in a local AI model later

The architecture already allows for it. `builder.build()` takes any object with
this method:

```python
def build_options(self, question: str, answer: str, topic: str) -> tuple[list[str], int]:
    """Return (four options, index of the correct one)."""
```

Write a class that calls Ollama or llama.cpp on `localhost` and hand it over:

```python
import builder
from my_llm_engine import OllamaEngine

builder.rebuild_all_with(OllamaEngine(model="llama3"))   # your own wrapper
```

Nothing else in the app changes. Options are cached the same way, the quiz screen
does not know or care where they came from, and you can rebuild only the subjects
you are unhappy with by clearing their cached options first.

---

## Project layout

```
exit-exam-trainer/
├── app.py              Flask server and JSON API
├── config.py           All tunable values live here
├── parser.py           File reading, cleaning, subject tagging
├── distractors.py      Option generation engine
├── builder.py          Generates and caches options in bulk
├── database.py         SQLite schema, progress, statistics
├── import_bank.py      Command-line import and rebuild
├── requirements.txt
├── run.bat / run.sh    Launchers
├── data/
│   ├── questions.csv   Your original bank, untouched
│   └── questions.db    SQLite database (questions, options, progress)
├── uploads/            Imported files are kept here
├── static/
│   ├── style.css
│   └── app.js
└── templates/
    └── index.html
```

### Database schema

```sql
questions(id, qhash UNIQUE, question, answer, explanation, topic,
          options_json, correct_index, created_at)

progress(question_id PK, attempts, correct_count, wrong_count, skipped,
         last_result, bookmarked, total_time_ms, updated_at)

state(key PK, value, updated_at)     -- resume data
meta(key PK, value)                  -- import bookkeeping
```

To start completely fresh, delete `data/questions.db` and restart — it rebuilds
from `data/questions.csv` in about 15 seconds. To clear only your answers and
keep the bank, use **Reset all progress** on the Statistics screen.

---

## Configuration

Edit `config.py`:

| Setting | Default | What it does |
| --- | --- | --- |
| `SET_SIZE` | 100 | Questions per set |
| `EXAM_SECONDS_PER_Q` | 60 | Exam-mode time allowance per question |
| `PORT` | 5000 | Change if something else uses this port |
| `MAX_UPLOAD_MB` | 64 | Import size ceiling |
| `MIN_QUESTION_LEN` | 12 | Rows shorter than this are treated as junk |

---

## If something goes wrong

**Port 5000 is already in use** — change `PORT` in `config.py`. On macOS this is
often AirPlay Receiver, which you can turn off in System Settings → General →
AirDrop & Handoff.

**"python is not recognised"** (Windows) — Python is not on your PATH. Reinstall
from python.org with **"Add Python to PATH"** ticked.

**Import says no questions were found** — the file needs a question column and an
answer column. Open it and check the header row. If the columns have unusual
names, rename them to `Question` and `Answer`.

**The page is blank** — check the terminal window for an error, and confirm you
are on `http://127.0.0.1:5000` and not `https`.

**Progress looks wrong** — Statistics → Reset all progress clears every answer,
bookmark and timing while leaving the questions alone.

The app catches its own errors rather than crashing: a corrupt import file, a bad
row, a failed autosave, or a malformed request all produce a message and carry on.

---

## Ideas for later

- Spaced repetition, so questions you keep missing resurface on a schedule
- A local model for distractors (see above) to catch the near-synonym problem
- Confidence tagging — mark answers as guessed, and track guessed-but-correct
- Per-set history so you can see whether set 12 is improving over time
- Subject weighting, so weak subjects appear more often in shuffled sets
