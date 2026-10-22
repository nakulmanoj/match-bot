"""Data models for the tournament system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TournamentStatus(Enum):
    SETUP = "setup"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TournamentFormat(Enum):
    KNOCKOUT = "knockout"
    LEAGUE = "league"  # Future
    GROUP_KNOCKOUT = "group_knockout"  # Future


class RoundStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class MatchStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class Tournament:
    id: int
    name: str
    guild_id: str
    format: TournamentFormat
    team_size: int
    num_teams: int
    status: TournamentStatus
    created_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def from_row(cls, row: dict) -> Tournament:
        return cls(
            id=row["id"],
            name=row["name"],
            guild_id=row["guild_id"],
            format=TournamentFormat(row["format"]),
            team_size=row["team_size"],
            num_teams=row["num_teams"],
            status=TournamentStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


@dataclass
class Team:
    id: int
    tournament_id: int
    name: str
    seed: int

    @classmethod
    def from_row(cls, row: dict) -> Team:
        return cls(
            id=row["id"],
            tournament_id=row["tournament_id"],
            name=row["name"],
            seed=row["seed"],
        )


@dataclass
class Player:
    id: int
    team_id: int
    discord_id: str
    ign: str  # In-game name

    @classmethod
    def from_row(cls, row: dict) -> Player:
        return cls(
            id=row["id"],
            team_id=row["team_id"],
            discord_id=row["discord_id"],
            ign=row["ign"],
        )


@dataclass
class Round:
    id: int
    tournament_id: int
    round_number: int  # Sequential: 1 = first round played
    label: str  # "R64", "R32", "R16", "QF", "SF", "F"
    status: RoundStatus

    @classmethod
    def from_row(cls, row: dict) -> Round:
        return cls(
            id=row["id"],
            tournament_id=row["tournament_id"],
            round_number=row["round_number"],
            label=row["label"],
            status=RoundStatus(row["status"]),
        )


@dataclass
class Match:
    id: int
    round_id: int
    match_number: int  # Position within the round
    home_team_id: int
    away_team_id: int
    status: MatchStatus
    winner_team_id: Optional[int] = None

    @classmethod
    def from_row(cls, row: dict) -> Match:
        return cls(
            id=row["id"],
            round_id=row["round_id"],
            match_number=row["match_number"],
            home_team_id=row["home_team_id"],
            away_team_id=row["away_team_id"],
            status=MatchStatus(row["status"]),
            winner_team_id=row["winner_team_id"],
        )


@dataclass
class MatchLeg:
    """Represents one leg of a match (home or away).

    Each match has 2 legs. The aggregate score across both legs determines
    the match winner.
    """

    id: int
    match_id: int
    leg_number: int  # 1 or 2
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    screenshot_url: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict | any) -> MatchLeg:
        return cls(
            id=row["id"],
            match_id=row["match_id"],
            leg_number=row["leg_number"],
            home_score=row["home_score"],
            away_score=row["away_score"],
            screenshot_url=row["screenshot_url"] if "screenshot_url" in row.keys() else None,
        )


@dataclass
class OCRResult:
    """Result from processing a single screenshot."""

    left_name: str
    right_name: str
    left_score: int
    right_score: int
    confidence: float  # 0.0 - 1.0


@dataclass
class MatchResult:
    """Aggregated result from both legs of a match."""

    home_aggregate: int
    away_aggregate: int
    leg1: OCRResult
    leg2: OCRResult
    winner_team_id: Optional[int] = None
