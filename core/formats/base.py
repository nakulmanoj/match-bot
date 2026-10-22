"""Abstract base class for tournament format plugins."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.database import Database
    from core.models import Round, Match, Team


class BaseTournamentFormat(abc.ABC):
    """Interface that all tournament formats must implement."""

    def __init__(self, db: Database, tournament_id: int) -> None:
        self.db = db
        self.tournament_id = tournament_id

    @abc.abstractmethod
    async def generate_first_round(self, teams: list[Team]) -> Round:
        """Create the first round of fixtures."""
        ...

    @abc.abstractmethod
    async def generate_next_round(self, current_round: Round) -> Round | None:
        """Create the next round from current round winners. Returns None if tournament is over."""
        ...

    @abc.abstractmethod
    def get_round_label(self, num_teams: int, round_number: int) -> str:
        """Get the display label for a round (e.g., 'QF', 'SF', 'F')."""
        ...
