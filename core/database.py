"""Database layer for the tournament system.

Uses aiosqlite for async SQLite operations. All database access goes through
the Database class which manages connection lifecycle and provides typed
query methods.
"""

from __future__ import annotations

import logging
import aiosqlite
from typing import Optional

log = logging.getLogger(__name__)

from core.models import (
    Tournament,
    TournamentStatus,
    TournamentFormat,
    Team,
    Player,
    Round,
    RoundStatus,
    Match,
    MatchStatus,
    MatchLeg,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tournaments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'knockout',
    team_size INTEGER NOT NULL DEFAULT 1,
    num_teams INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'setup',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    seed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    discord_id TEXT NOT NULL,
    ign TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    match_number INTEGER NOT NULL,
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    status TEXT NOT NULL DEFAULT 'pending',
    winner_team_id INTEGER REFERENCES teams(id)
);

CREATE TABLE IF NOT EXISTS match_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    leg_number INTEGER NOT NULL,
    home_score INTEGER,
    away_score INTEGER,
    screenshot_url TEXT,
    UNIQUE(match_id, leg_number)
);
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    discord_id TEXT NOT NULL,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(guild_id, discord_id)
);
"""


class Database:
    """Async database interface for tournament data."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Open connection and initialize schema."""
        import os
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path, timeout=30.0)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.commit()

        # Check and migrate legacy match_legs table if CHECK constraint exists
        async with self._conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='match_legs'") as cur:
            row = await cur.fetchone()
            if row and "CHECK" in row[0].upper():
                log.info("Migrating legacy match_legs table to remove CHECK constraint...")
                await self._conn.execute("PRAGMA foreign_keys = OFF")
                await self._conn.executescript("""
                    CREATE TABLE IF NOT EXISTS match_legs_migrated (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                        leg_number INTEGER NOT NULL,
                        home_score INTEGER,
                        away_score INTEGER,
                        screenshot_url TEXT,
                        UNIQUE(match_id, leg_number)
                    );
                    INSERT OR IGNORE INTO match_legs_migrated SELECT id, match_id, leg_number, home_score, away_score, screenshot_url FROM match_legs;
                    DROP TABLE match_legs;
                    ALTER TABLE match_legs_migrated RENAME TO match_legs;
                """)
                await self._conn.execute("PRAGMA foreign_keys = ON")
                await self._conn.commit()
                log.info("match_legs table migration complete.")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not connected. Call connect() first."
        return self._conn

    # ── Tournament ──────────────────────────────────────────────

    async def create_tournament(
        self,
        name: str,
        guild_id: str,
        num_teams: int,
        team_size: int = 1,
        fmt: TournamentFormat = TournamentFormat.KNOCKOUT,
    ) -> Tournament:
        cursor = await self.conn.execute(
            """INSERT INTO tournaments (name, guild_id, format, team_size, num_teams, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, guild_id, fmt.value, team_size, num_teams, TournamentStatus.SETUP.value),
        )
        await self.conn.commit()
        return await self.get_tournament(cursor.lastrowid)  # type: ignore[arg-type]

    async def get_tournament(self, tournament_id: int) -> Optional[Tournament]:
        async with self.conn.execute(
            "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return Tournament.from_row(row) if row else None

    async def get_active_tournament(self, guild_id: str) -> Optional[Tournament]:
        """Get the active (non-completed) tournament for a guild."""
        async with self.conn.execute(
            """SELECT * FROM tournaments
               WHERE guild_id = ? AND status != ?
               ORDER BY created_at DESC LIMIT 1""",
            (guild_id, TournamentStatus.COMPLETED.value),
        ) as cursor:
            row = await cursor.fetchone()
            return Tournament.from_row(row) if row else None

    async def update_tournament_status(
        self, tournament_id: int, status: TournamentStatus
    ) -> None:
        await self.conn.execute(
            "UPDATE tournaments SET status = ? WHERE id = ?",
            (status.value, tournament_id),
        )
        await self.conn.commit()

    async def delete_tournament(self, tournament_id: int) -> bool:
        """Completely delete a tournament and all associated teams, rounds, and matches."""
        cursor = await self.conn.execute(
            "DELETE FROM tournaments WHERE id = ?", (tournament_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def update_tournament_format(
        self, tournament_id: int, fmt: TournamentFormat
    ) -> None:
        await self.conn.execute(
            "UPDATE tournaments SET format = ? WHERE id = ?",
            (fmt.value, tournament_id),
        )
        await self.conn.commit()

    async def update_tournament(
        self,
        tournament_id: int,
        format: Optional[TournamentFormat] = None,
        status: Optional[TournamentStatus] = None,
    ) -> None:
        """Update format and/or status for a tournament."""
        updates = []
        params = []
        if format is not None:
            updates.append("format = ?")
            params.append(format.value)
        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        if not updates:
            return
        params.append(tournament_id)
        sql = f"UPDATE tournaments SET {', '.join(updates)} WHERE id = ?"
        await self.conn.execute(sql, tuple(params))
        await self.conn.commit()

    # ── Teams ───────────────────────────────────────────────────

    async def create_team(
        self, tournament_id: int, name: str, seed: int = 0
    ) -> Team:
        cursor = await self.conn.execute(
            "INSERT INTO teams (tournament_id, name, seed) VALUES (?, ?, ?)",
            (tournament_id, name, seed),
        )
        await self.conn.commit()
        return await self.get_team(cursor.lastrowid)  # type: ignore[arg-type]

    async def get_team(self, team_id: int) -> Optional[Team]:
        async with self.conn.execute(
            "SELECT * FROM teams WHERE id = ?", (team_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return Team.from_row(row) if row else None

    async def get_teams(self, tournament_id: int) -> list[Team]:
        async with self.conn.execute(
            "SELECT * FROM teams WHERE tournament_id = ? ORDER BY seed",
            (tournament_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [Team.from_row(r) for r in rows]

    async def get_team_by_name(
        self, tournament_id: int, name: str
    ) -> Optional[Team]:
        async with self.conn.execute(
            "SELECT * FROM teams WHERE tournament_id = ? AND LOWER(name) = LOWER(?)",
            (tournament_id, name.strip()),
        ) as cursor:
            row = await cursor.fetchone()
            return Team.from_row(row) if row else None

    async def update_team_name(self, team_id: int, new_name: str) -> None:
        """Update a team's name."""
        await self.conn.execute(
            "UPDATE teams SET name = ? WHERE id = ?",
            (new_name.strip(), team_id),
        )
        await self.conn.commit()

    async def delete_team(self, team_id: int) -> None:
        """Delete a team and its players."""
        await self.conn.execute("DELETE FROM players WHERE team_id = ?", (team_id,))
        await self.conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        await self.conn.commit()

    # ── Players ─────────────────────────────────────────────────

    async def add_player(
        self, team_id: int, discord_id: str, ign: str
    ) -> Player:
        cursor = await self.conn.execute(
            "INSERT INTO players (team_id, discord_id, ign) VALUES (?, ?, ?)",
            (team_id, discord_id, ign),
        )
        await self.conn.commit()
        return await self.get_player(cursor.lastrowid)  # type: ignore[arg-type]

    async def get_player(self, player_id: int) -> Optional[Player]:
        async with self.conn.execute(
            "SELECT * FROM players WHERE id = ?", (player_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return Player.from_row(row) if row else None

    async def get_player_by_discord_id(
        self, tournament_id: int, discord_id: str
    ) -> Optional[Player]:
        async with self.conn.execute(
            """SELECT p.* FROM players p
               JOIN teams t ON p.team_id = t.id
               WHERE t.tournament_id = ? AND p.discord_id = ?""",
            (tournament_id, discord_id),
        ) as cursor:
            row = await cursor.fetchone()
            return Player.from_row(row) if row else None

    async def get_players_by_team(self, team_id: int) -> list[Player]:
        async with self.conn.execute(
            "SELECT * FROM players WHERE team_id = ?", (team_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [Player.from_row(r) for r in rows]

    async def get_all_players(self, tournament_id: int) -> list[Player]:
        async with self.conn.execute(
            """SELECT p.* FROM players p
               JOIN teams t ON p.team_id = t.id
               WHERE t.tournament_id = ?""",
            (tournament_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [Player.from_row(r) for r in rows]

    async def remove_player(self, player_id: int) -> None:
        await self.conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
        await self.conn.commit()

    async def get_player_count(self, team_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) as cnt FROM players WHERE team_id = ?", (team_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["cnt"] if row else 0

    # ── Rounds ──────────────────────────────────────────────────

    async def create_round(
        self, tournament_id: int, round_number: int, label: str
    ) -> Round:
        cursor = await self.conn.execute(
            """INSERT INTO rounds (tournament_id, round_number, label, status)
               VALUES (?, ?, ?, ?)""",
            (tournament_id, round_number, label, RoundStatus.IN_PROGRESS.value),
        )
        await self.conn.commit()
        return await self.get_round(cursor.lastrowid)  # type: ignore[arg-type]

    async def get_round(self, round_id: int) -> Optional[Round]:
        async with self.conn.execute(
            "SELECT * FROM rounds WHERE id = ?", (round_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return Round.from_row(row) if row else None

    async def get_current_round(self, tournament_id: int) -> Optional[Round]:
        """Get the latest in-progress round."""
        async with self.conn.execute(
            """SELECT * FROM rounds
               WHERE tournament_id = ? AND status = ?
               ORDER BY round_number DESC LIMIT 1""",
            (tournament_id, RoundStatus.IN_PROGRESS.value),
        ) as cursor:
            row = await cursor.fetchone()
            return Round.from_row(row) if row else None

    async def get_rounds(self, tournament_id: int) -> list[Round]:
        async with self.conn.execute(
            "SELECT * FROM rounds WHERE tournament_id = ? ORDER BY round_number",
            (tournament_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [Round.from_row(r) for r in rows]

    async def update_round_status(
        self, round_id: int, status: RoundStatus
    ) -> None:
        await self.conn.execute(
            "UPDATE rounds SET status = ? WHERE id = ?",
            (status.value, round_id),
        )
        await self.conn.commit()

    # ── Matches ─────────────────────────────────────────────────

    async def create_match(
        self,
        round_id: int,
        match_number: int,
        home_team_id: int,
        away_team_id: int,
    ) -> Match:
        cursor = await self.conn.execute(
            """INSERT INTO matches (round_id, match_number, home_team_id, away_team_id, status)
               VALUES (?, ?, ?, ?, ?)""",
            (round_id, match_number, home_team_id, away_team_id, MatchStatus.PENDING.value),
        )
        await self.conn.commit()
        # Create the two leg placeholders
        match_id = cursor.lastrowid
        await self.conn.execute(
            "INSERT INTO match_legs (match_id, leg_number) VALUES (?, 1)",
            (match_id,),
        )
        await self.conn.execute(
            "INSERT INTO match_legs (match_id, leg_number) VALUES (?, 2)",
            (match_id,),
        )
        await self.conn.commit()
        return await self.get_match(match_id)  # type: ignore[arg-type]

    async def get_match(self, match_id: int) -> Optional[Match]:
        async with self.conn.execute(
            "SELECT * FROM matches WHERE id = ?", (match_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return Match.from_row(row) if row else None

    async def get_matches_by_round(self, round_id: int) -> list[Match]:
        async with self.conn.execute(
            "SELECT * FROM matches WHERE round_id = ? ORDER BY match_number",
            (round_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [Match.from_row(r) for r in rows]

    async def find_match_between_teams(
        self, tournament_id: int, team1_id: int, team2_id: int
    ) -> Optional[Match]:
        """Find a pending/in-progress match between two teams in the current round."""
        current_round = await self.get_current_round(tournament_id)
        if not current_round:
            return None
        async with self.conn.execute(
            """SELECT * FROM matches
               WHERE round_id = ? AND status != ?
               AND ((home_team_id = ? AND away_team_id = ?)
                    OR (home_team_id = ? AND away_team_id = ?))""",
            (
                current_round.id,
                MatchStatus.COMPLETED.value,
                team1_id,
                team2_id,
                team2_id,
                team1_id,
            ),
        ) as cursor:
            row = await cursor.fetchone()
            return Match.from_row(row) if row else None

    async def update_match_status(
        self, match_id: int, status: MatchStatus
    ) -> None:
        await self.conn.execute(
            "UPDATE matches SET status = ? WHERE id = ?",
            (status.value, match_id),
        )
        await self.conn.commit()

    async def set_match_winner(
        self, match_id: int, winner_team_id: int
    ) -> None:
        await self.conn.execute(
            "UPDATE matches SET winner_team_id = ?, status = ? WHERE id = ?",
            (winner_team_id, MatchStatus.COMPLETED.value, match_id),
        )
        await self.conn.commit()

    async def clear_match_result(self, match_id: int) -> None:
        """Remove result from a match, resetting it to pending."""
        await self.conn.execute(
            "UPDATE matches SET winner_team_id = NULL, status = ? WHERE id = ?",
            (MatchStatus.PENDING.value, match_id),
        )
        await self.conn.execute(
            "UPDATE match_legs SET home_score = NULL, away_score = NULL WHERE match_id = ?",
            (match_id,),
        )
        await self.conn.commit()

    # ── Match Legs ──────────────────────────────────────────────

    async def get_match_legs(self, match_id: int) -> list[MatchLeg]:
        async with self.conn.execute(
            "SELECT * FROM match_legs WHERE match_id = ? ORDER BY leg_number",
            (match_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [MatchLeg.from_row(r) for r in rows]

    async def update_leg_score(
        self,
        match_id: int,
        leg_number: int,
        home_score: int,
        away_score: int,
        screenshot_url: Optional[str] = None,
    ) -> None:
        await self.conn.execute(
            """INSERT INTO match_legs (match_id, leg_number, home_score, away_score, screenshot_url)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(match_id, leg_number) DO UPDATE SET
                   home_score = excluded.home_score,
                   away_score = excluded.away_score,
                   screenshot_url = excluded.screenshot_url""",
            (match_id, leg_number, home_score, away_score, screenshot_url),
        )
        await self.conn.commit()

    async def are_all_matches_completed(self, round_id: int) -> bool:
        async with self.conn.execute(
            """SELECT COUNT(*) as cnt FROM matches
               WHERE round_id = ? AND status != ?""",
            (round_id, MatchStatus.COMPLETED.value),
        ) as cursor:
            row = await cursor.fetchone()
            return row["cnt"] == 0 if row else False

    async def get_round_winners(self, round_id: int) -> list[int]:
        """Get winner team IDs for all matches in a round, ordered by match number."""
        async with self.conn.execute(
            """SELECT winner_team_id FROM matches
               WHERE round_id = ? AND winner_team_id IS NOT NULL
               ORDER BY match_number""",
            (round_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [r["winner_team_id"] for r in rows]

    # ── Convenience aliases ─────────────────────────────────────

    async def get_players(self, team_id: int) -> list[Player]:
        """Alias for get_players_by_team."""
        return await self.get_players_by_team(team_id)

    async def get_matches(self, round_id: int) -> list[Match]:
        """Alias for get_matches_by_round."""
        return await self.get_matches_by_round(round_id)

    async def get_legs(self, match_id: int) -> list[MatchLeg]:
        """Alias for get_match_legs."""
        return await self.get_match_legs(match_id)

    # ── Bot / Tournament Admins ────────────────────────────────

    async def add_admin(self, guild_id: str, discord_id: str, added_by: str) -> bool:
        """Add a bot admin for the guild. Returns True if newly added, False if already exists."""
        try:
            await self.conn.execute(
                """INSERT INTO admins (guild_id, discord_id, added_by)
                   VALUES (?, ?, ?)""",
                (guild_id, discord_id, added_by),
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def remove_admin(self, guild_id: str, discord_id: str) -> bool:
        """Remove an admin from the guild."""
        cursor = await self.conn.execute(
            "DELETE FROM admins WHERE guild_id = ? AND discord_id = ?",
            (guild_id, discord_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def is_admin(self, guild_id: str, discord_id: str) -> bool:
        """Check if a user is registered as an admin for this guild."""
        async with self.conn.execute(
            "SELECT 1 FROM admins WHERE guild_id = ? AND discord_id = ?",
            (guild_id, discord_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None

    async def get_admins(self, guild_id: str) -> list[str]:
        """Get list of admin discord_ids for a guild."""
        async with self.conn.execute(
            "SELECT discord_id FROM admins WHERE guild_id = ? ORDER BY created_at",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [r["discord_id"] for r in rows]


