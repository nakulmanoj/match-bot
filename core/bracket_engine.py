"""Bracket engine facade — dispatches to the correct tournament format."""

from __future__ import annotations

from core.database import Database
from core.models import TournamentFormat, Round, Team
from core.formats.base import BaseTournamentFormat
from core.formats.knockout import KnockoutFormat


def get_format(db: Database, tournament_id: int, fmt: TournamentFormat) -> BaseTournamentFormat:
    """Factory that returns the correct format handler.

    Parameters
    ----------
    db:
        The database layer used by the format handler.
    tournament_id:
        Primary key of the tournament being managed.
    fmt:
        The tournament format variant to instantiate.

    Raises
    ------
    ValueError
        If *fmt* does not map to an implemented format class.
    """
    formats: dict[TournamentFormat, type[BaseTournamentFormat]] = {
        TournamentFormat.KNOCKOUT: KnockoutFormat,
    }
    cls = formats.get(fmt)
    if cls is None:
        raise ValueError(f"Tournament format '{fmt.value}' is not yet implemented.")
    return cls(db, tournament_id)


async def generate_first_round(
    db: Database,
    tournament_id: int,
    teams_or_fmt: list[Team] | TournamentFormat,
    teams: list[Team] | None = None,
) -> Round:
    """Create the opening round for a tournament.

    Supports both:
      generate_first_round(db, tid, fmt, teams)
      generate_first_round(db, tid, teams) # defaults to KNOCKOUT
    """
    if isinstance(teams_or_fmt, list):
        fmt = TournamentFormat.KNOCKOUT
        actual_teams = teams_or_fmt
    else:
        fmt = teams_or_fmt
        actual_teams = teams or []

    handler = get_format(db, tournament_id, fmt)
    return await handler.generate_first_round(actual_teams)


async def generate_next_round(
    db: Database,
    tournament_id: int,
    round_or_fmt: Round | TournamentFormat,
    current_round: Round | None = None,
) -> Round | None:
    """Advance the tournament by one round.

    Supports both:
      generate_next_round(db, tid, fmt, current_round)
      generate_next_round(db, tid, current_round) # defaults to KNOCKOUT
    """
    if isinstance(round_or_fmt, Round):
        fmt = TournamentFormat.KNOCKOUT
        actual_round = round_or_fmt
    else:
        fmt = round_or_fmt
        actual_round = current_round  # type: ignore[assignment]

    assert actual_round is not None
    handler = get_format(db, tournament_id, fmt)
    return await handler.generate_next_round(actual_round)
