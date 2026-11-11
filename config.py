"""Central configuration. No hardcoded values elsewhere in the app."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"

DB_PATH = DATA_DIR / "questions.db"

# Question bank shipped with the app (imported automatically on first run).
SEED_BANK = DATA_DIR / "questions.csv"

# Quiz structure
SET_SIZE = 100            # questions per set
OPTION_COUNT = 4          # A / B / C / D
EXAM_SECONDS_PER_Q = 60   # exam-mode timer allowance per question

# Server
HOST = "127.0.0.1"
PORT = 5000
DEBUG = False

# Import limits
MAX_UPLOAD_MB = 64
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".txt"}

# Minimum lengths for a row to count as a usable question
MIN_QUESTION_LEN = 12
MIN_ANSWER_LEN = 1

for _d in (DATA_DIR, UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)
