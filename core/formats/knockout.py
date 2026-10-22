"""Knockout (single-elimination) tournament format implementation."""

from __future__ import annotations

from core.formats.base import BaseTournamentFormat
from core.models import Round, Match, Team
from core.database import Database

import random

# Maps the number of matches in a round to a human-readable label.
_ROUND_LABELS: dict[int, str] = {
    32: "Round of 64",
    16: "Round of 32",
    8: "Round of 16",
    4: "Quarter Finals",
    2: "Semi Finals",
    1: "Final",
}


class KnockoutFormat(BaseTournamentFormat):
    """Single-elimination bracket format with randomized first round matchmaking."""

    def __init__(self, db: Database, tournament_id: int) -> None:
        super().__init__(db, tournament_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_first_round(self, teams: list[Team]) -> Round:
        """Create the first round of fixtures with randomized matchmaking.

        Parameters
        ----------
        teams:
            List of teams registered for the tournament. The list length
            **must** be even so every team has an opponent.

        Returns
        -------
        Round
            The newly created first round, persisted via the database layer.
        """
        if len(teams) < 2:
            raise ValueError("At least 2 teams are required to generate a bracket.")
        if len(teams) % 2 != 0:
            raise ValueError("Number of teams must be even for a knockout bracket.")

        round_number = 1
        label = self.get_round_label(num_teams=len(teams), round_number=round_number)

        first_round = await self.db.create_round(
            tournament_id=self.tournament_id,
            round_number=round_number,
            label=label,
        )

        # Random matchmaking: shuffle teams before pairing
        shuffled_teams = list(teams)
        random.shuffle(shuffled_teams)
        matches = [(shuffled_teams[i], shuffled_teams[i + 1]) for i in range(0, len(shuffled_teams), 2)]

        for match_number, (team_a, team_b) in enumerate(matches, start=1):
            await self.db.create_match(
                round_id=first_round.id,
                match_number=match_number,
                home_team_id=team_a.id,
                away_team_id=team_b.id,
            )

        return first_round

    async def generate_next_round(self, current_round: Round) -> Round | None:
        """Create the next round from current round winners.

        Winners are paired sequentially: winner of match 1 faces winner
        of match 2, winner of match 3 faces winner of match 4, etc.

        Parameters
        ----------
        current_round:
            The round whose matches have all been decided.

        Returns
        -------
        Round | None
            The newly created next round, or ``None`` if the tournament
            is over (only one winner remains).
        """
        winner_team_ids: list[int] = await self.db.get_round_winners(current_round.id)

        if len(winner_team_ids) <= 1:
            # Tournament is over — the sole winner is the champion.
            return None

        # Fetch actual Team objects
        winners: list[Team] = []
        for tid in winner_team_ids:
            team = await self.db.get_team(tid)
            if team:
                winners.append(team)

        if len(winners) <= 1:
            return None

        next_round_number = current_round.round_number + 1
        label = self.get_round_label(
            num_teams=len(winners),
            round_number=next_round_number,
        )

        next_round = await self.db.create_round(
            tournament_id=self.tournament_id,
            round_number=next_round_number,
            label=label,
        )

        # Pair winners sequentially (M1 winner vs M2 winner, …).
        for match_number, i in enumerate(range(0, len(winners), 2), start=1):
            await self.db.create_match(
                round_id=next_round.id,
                match_number=match_number,
                home_team_id=winners[i].id,
                away_team_id=winners[i + 1].id,
            )

        return next_round

    def get_round_label(self, num_teams: int, round_number: int) -> str:
        """Return a display-friendly label for a round.

        Parameters
        ----------
        num_teams:
            The number of teams competing **in this round** (not the
            total tournament size).
        round_number:
            The sequential round number (1-based).

        Returns
        -------
        str
            A label such as ``'Quarter Finals'`` or ``'Round 3'``.
        """
        num_matches = num_teams // 2
        return _ROUND_LABELS.get(num_matches, f"Round {round_number}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pair_by_seed(teams: list[Team]) -> list[tuple[Team, Team]]:
        """Pair teams using standard bracket seeding.

        Given teams sorted by seed ``[1, 2, 3, …, N]``, produce pairs
        ``[(1, N), (2, N-1), (3, N-2), …]``.

        Returns
        -------
        list[tuple[Team, Team]]
            Ordered list of ``(higher_seed, lower_seed)`` pairings.
        """
        n = len(teams)
        pairs: list[tuple[Team, Team]] = []
        for i in range(n // 2):
            pairs.append((teams[i], teams[n - 1 - i]))
        return pairs
