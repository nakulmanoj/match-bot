"""Fuzzy name matching for OCR-extracted player names."""

from __future__ import annotations

import logging
from typing import Optional

from rapidfuzz import fuzz, process

from core.models import Player
import config

log = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Normalize names to handle stylized gaming characters, symbols, and OCR misreads."""
    if not name:
        return ""
    import unicodedata

    # Map common stylized characters, gaming fonts, and OCR misreads
    subs = {
        "Ł": "L",
        "ł": "L",
        "Ə": "E",
        "ə": "E",
        "€": "E",
        "Ø": "O",
        "ø": "O",
        "0": "O",
        "1": "I",
        "|": "I",
        "$": "S",
        "@": "A",
        "3": "E",
        "4": "A",
        "7": "T",
    }
    s = name.strip()
    for k, v in subs.items():
        s = s.replace(k, v)

    # Decompose any remaining diacritics/accents (e.g. é -> e)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    return "".join(c for c in s if c.isalnum())


def match_player(
    ocr_name: str,
    players: list[Player],
    threshold: int | None = None,
) -> tuple[Optional[Player], float]:
    """Match an OCR-extracted name to a registered player.

    Uses rapidfuzz ``WRatio`` with normalization for stylized font glyphs.
    """
    if threshold is None:
        threshold = config.FUZZY_MATCH_THRESHOLD

    if not players or not ocr_name.strip():
        return None, 0.0

    norm_ocr = _normalize_name(ocr_name)
    raw_ocr = ocr_name.strip()

    best_player: Optional[Player] = None
    best_score: float = 0.0

    for p in players:
        # 1. Raw similarity
        score_raw = fuzz.WRatio(raw_ocr, p.ign)
        # 2. Normalized similarity (handles 'D E M O N' -> 'DEMON', 'D € m 0' -> 'DEMO')
        norm_ign = _normalize_name(p.ign)
        score_norm = fuzz.WRatio(norm_ocr, norm_ign)

        final_score = max(score_raw, score_norm)
        if final_score > best_score:
            best_score = final_score
            best_player = p

    if best_score < threshold or best_player is None:
        log.warning(
            "No match found for OCR name '%s' (threshold=%d, best_score=%.1f)",
            ocr_name,
            threshold,
            best_score,
        )
        return None, best_score

    log.info("Matched OCR '%s' -> '%s' (score=%.1f)", ocr_name, best_player.ign, best_score)
    return best_player, best_score


def match_players_in_match(
    ocr_left: str,
    ocr_right: str,
    home_players: list[Player],
    away_players: list[Player],
) -> tuple[Optional[Player], Optional[Player], float, float]:
    """Match both OCR names to players from the two teams.

    Tries matching left name against home team first, then away team,
    and vice versa.  Picks the combination with the best total score.

    Args:
        ocr_left: OCR text for the left-side player name.
        ocr_right: OCR text for the right-side player name.
        home_players: Registered players from the home team.
        away_players: Registered players from the away team.

    Returns:
        ``(home_matched, away_matched, home_score, away_score)``
        — the return order is always *(home, away)* regardless of
        screen position.
    """
    # Try direct matching: left = home, right = away
    left_home, lh_score = match_player(ocr_left, home_players)
    right_away, ra_score = match_player(ocr_right, away_players)
    direct_total = lh_score + ra_score

    # Try swapped: left = away, right = home
    left_away, la_score = match_player(ocr_left, away_players)
    right_home, rh_score = match_player(ocr_right, home_players)
    swapped_total = la_score + rh_score

    if direct_total >= swapped_total:
        # Left player is from home team, right is away
        return left_home, right_away, lh_score, ra_score
    else:
        # Left player is from away team, right is home
        # Swap so return order is always (home_player, away_player)
        return right_home, left_away, rh_score, la_score
