import os
from dotenv import load_dotenv

load_dotenv()

def _safe_int(val: str | None, default: int = 0) -> int:
    if not val:
        return default
    try:
        return int(val.strip())
    except (ValueError, TypeError):
        return default


BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ROLE_ID: int = _safe_int(os.getenv("ADMIN_ROLE_ID"), 0)
OCR_CONFIDENCE_THRESHOLD: int = _safe_int(os.getenv("OCR_CONFIDENCE_THRESHOLD"), 70)
FUZZY_MATCH_THRESHOLD: int = _safe_int(os.getenv("FUZZY_MATCH_THRESHOLD"), 65)

DATABASE_PATH: str = os.getenv("DATABASE_PATH", "tournament.db")

# EasyOCR languages
OCR_LANGUAGES: list[str] = ["en"]

# Cog extensions to load
EXTENSIONS: list[str] = [
    "cogs.help",
    "cogs.tournament",
    "cogs.teams",
    "cogs.bracket",
    "cogs.results",
    "cogs.admin",
]
