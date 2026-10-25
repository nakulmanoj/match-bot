"""Discord embed builders for consistent bot responses."""

from __future__ import annotations

from typing import Optional

import discord

from core.models import Tournament, Team, Player, Round, Match, MatchLeg


def tournament_info(tournament: Tournament, teams: list[Team]) -> discord.Embed:
    """Build an embed showing tournament overview."""
    status_emoji = {
        "setup": "🔧",
        "in_progress": "⚔️",
        "completed": "🏆",
    }
    emoji = status_emoji.get(tournament.status.value, "📋")

    embed = discord.Embed(
        title=f"{emoji} {tournament.name}",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Format", value=tournament.format.value.replace("_", " ").title(), inline=True)
    embed.add_field(name="Teams", value=str(tournament.num_teams), inline=True)
    embed.add_field(name="Team Size", value=str(tournament.team_size), inline=True)
    embed.add_field(name="Status", value=tournament.status.value.replace("_", " ").title(), inline=True)

    if teams:
        filled = sum(1 for _ in teams)  # All created teams
        embed.add_field(name="Teams Created", value=f"{filled}/{tournament.num_teams}", inline=True)

    embed.set_footer(text=f"Tournament ID: {tournament.id}")
    return embed


def team_roster(team: Team, players: list[Player], team_size: Optional[int] = None) -> discord.Embed:
    """Build an embed showing a team's roster."""
    embed = discord.Embed(
        title=f"📋 {team.name}",
        color=discord.Color.green(),
    )
    if team_size is not None:
        embed.add_field(name="Capacity", value=f"{len(players)}/{team_size}", inline=True)

    if players:
        roster_lines = []
        for i, p in enumerate(players, 1):
            roster_lines.append(f"`{i}.` <@{p.discord_id}> — **{p.ign}**")
        embed.add_field(name="Players", value="\n".join(roster_lines), inline=False)
    else:
        embed.add_field(name="Players", value="*No players yet*", inline=False)

    embed.set_footer(text=f"Team ID: {team.id}")
    return embed


def bracket_embed(
    tournament: Optional[Tournament] = None,
    current_round: Optional[Round] = None,
    matches: Optional[list[Match]] = None,
    teams: Optional[dict[int, Team]] = None,
    legs: Optional[dict[int, list[MatchLeg]]] = None,
    *,
    round_obj: Optional[Round] = None,
    tournament_name: Optional[str] = None,
) -> discord.Embed:
    """Build an embed showing the current round's bracket."""
    rnd = round_obj if round_obj is not None else current_round
    t_name = tournament_name if tournament_name is not None else (tournament.name if tournament else "Tournament")
    round_label = rnd.label if rnd else "Round"
    matches_list = matches or []
    teams_map = teams or {}
    legs_map = legs or {}

    embed = discord.Embed(
        title=f"⚔️ {t_name} — {round_label}",
        color=discord.Color.gold(),
    )

    # If round has > 20 matches (e.g. Round of 64 has 32 matches), group them
    # so we never exceed Discord's strict 25-field limit per embed.
    chunk_size = 1
    if len(matches_list) > 40:
        chunk_size = 3
    elif len(matches_list) > 20:
        chunk_size = 2

    for i in range(0, len(matches_list), chunk_size):
        chunk = matches_list[i : i + chunk_size]
        if len(chunk) == 1:
            match = chunk[0]
            home = teams_map.get(match.home_team_id)
            away = teams_map.get(match.away_team_id)
            home_name = home.name if home else "TBD"
            away_name = away.name if away else "TBD"

            match_legs = legs_map.get(match.id, [])

            if match.status.value == "completed":
                leg_scores = []
                for leg in match_legs:
                    if leg.home_score is not None and leg.away_score is not None:
                        leg_scores.append(f"{leg.home_score}-{leg.away_score}")
                score_str = " / ".join(leg_scores) if leg_scores else "N/A"

                agg_home = sum(l.home_score for l in match_legs if l.home_score is not None)
                agg_away = sum(l.away_score for l in match_legs if l.away_score is not None)

                winner_marker = ""
                if match.winner_team_id == match.home_team_id:
                    winner_marker = f" ✅ **{home_name}** advances"
                elif match.winner_team_id == match.away_team_id:
                    winner_marker = f" ✅ **{away_name}** advances"

                value = f"**{home_name}** vs **{away_name}**\nLegs: {score_str} (Agg: {agg_home}-{agg_away}){winner_marker}"
            else:
                status_icon = "⏳" if match.status.value == "pending" else "🔴"
                value = f"**{home_name}** vs **{away_name}** {status_icon}"

            embed.add_field(
                name=f"Match #{match.match_number}  •  Match ID: {match.id}",
                value=value,
                inline=False,
            )
        else:
            first_num = chunk[0].match_number
            last_num = chunk[-1].match_number
            field_name = f"Matches #{first_num}–#{last_num}"
            lines = []
            for match in chunk:
                home = teams_map.get(match.home_team_id)
                away = teams_map.get(match.away_team_id)
                home_name = home.name if home else "TBD"
                away_name = away.name if away else "TBD"

                match_legs = legs_map.get(match.id, [])

                if match.status.value == "completed":
                    agg_home = sum(l.home_score for l in match_legs if l.home_score is not None)
                    agg_away = sum(l.away_score for l in match_legs if l.away_score is not None)
                    winner_marker = ""
                    if match.winner_team_id == match.home_team_id:
                        winner_marker = f" ✅ **{home_name}**"
                    elif match.winner_team_id == match.away_team_id:
                        winner_marker = f" ✅ **{away_name}**"
                    lines.append(f"**#{match.match_number}** (ID: {match.id}): **{home_name}** {agg_home}-{agg_away} **{away_name}**{winner_marker}")
                else:
                    status_icon = "⏳" if match.status.value == "pending" else "🔴"
                    lines.append(f"**#{match.match_number}** (ID: {match.id}): **{home_name}** vs **{away_name}** {status_icon}")

            embed.add_field(name=field_name, value="\n".join(lines), inline=False)

    return embed


def full_bracket_embed(
    tournament: Optional[Tournament] = None,
    rounds: Optional[list[Round]] = None,
    all_matches: Optional[dict[int, list[Match]]] = None,
    teams: Optional[dict[int, Team]] = None,
    all_legs: Optional[dict[int, list[MatchLeg]]] = None,
    *,
    round_data: Optional[list[dict]] = None,
    tournament_name: Optional[str] = None,
) -> list[discord.Embed]:
    """Build embeds showing the full tournament bracket across all rounds."""
    embeds_list = []
    t_name = tournament_name if tournament_name is not None else (tournament.name if tournament else "Tournament")

    if round_data is not None:
        for data in round_data:
            rnd = data.get("round")
            m_list = data.get("matches", [])
            t_map = data.get("teams", {})
            l_map = data.get("legs", {})
            embed = bracket_embed(
                round_obj=rnd,
                matches=m_list,
                teams=t_map,
                legs=l_map,
                tournament_name=t_name,
            )
            if rnd:
                status_text = rnd.status.value.replace("_", " ").title()
                embed.set_footer(text=f"Round {rnd.round_number} • {status_text}")
            embeds_list.append(embed)
        return embeds_list

    if rounds:
        for rnd in rounds:
            matches = (all_matches or {}).get(rnd.id, [])
            embed = bracket_embed(
                tournament=tournament,
                current_round=rnd,
                matches=matches,
                teams=teams or {},
                legs=all_legs or {},
                tournament_name=t_name,
            )
            status_text = rnd.status.value.replace("_", " ").title()
            embed.set_footer(text=f"Round {rnd.round_number} • {status_text}")
            embeds_list.append(embed)

    return embeds_list


def result_confirmation(
    match: Match,
    home_team: Team,
    away_team: Team,
    legs: list[MatchLeg],
    agg_home: int,
    agg_away: int,
    winner_team: Team,
) -> discord.Embed:
    """Build an embed confirming a recorded result."""
    embed = discord.Embed(
        title="✅ Result Recorded",
        color=discord.Color.green(),
    )

    leg_lines = []
    for leg in legs:
        if leg.home_score is not None and leg.away_score is not None:
            leg_lines.append(f"Leg {leg.leg_number}: **{home_team.name}** {leg.home_score} - {leg.away_score} **{away_team.name}**")

    embed.add_field(
        name="Scores",
        value="\n".join(leg_lines),
        inline=False,
    )
    embed.add_field(
        name="Aggregate",
        value=f"**{home_team.name}** {agg_home} - {agg_away} **{away_team.name}**",
        inline=False,
    )
    embed.add_field(
        name="Winner",
        value=f"🏆 **{winner_team.name}**",
        inline=False,
    )
    embed.set_footer(text=f"Match ID: {match.id}")
    return embed


def ocr_preview(
    left_name: str,
    right_name: str,
    left_score: int,
    right_score: int,
    confidence: float,
    leg_number: int,
) -> discord.Embed:
    """Build an embed showing OCR extraction preview."""
    color = discord.Color.green() if confidence >= 0.7 else discord.Color.orange()
    embed = discord.Embed(
        title=f"🔍 OCR Result — Leg {leg_number}",
        color=color,
    )
    embed.add_field(name="Left Player", value=f"**{left_name}**", inline=True)
    embed.add_field(name="Score", value=f"**{left_score} - {right_score}**", inline=True)
    embed.add_field(name="Right Player", value=f"**{right_name}**", inline=True)
    embed.add_field(name="Confidence", value=f"{confidence:.0%}", inline=False)
    return embed


def error_embed(
    title_or_desc: Optional[str] = None,
    description: Optional[str] = None,
    *,
    title: Optional[str] = None,
) -> discord.Embed:
    """Build a red error embed."""
    # Determine the actual title and description
    if title is not None:
        actual_title = title
        actual_desc = description or title_or_desc or ""
    elif description is not None:
        actual_title = title_or_desc or "Error"
        actual_desc = description
    else:
        actual_title = "Error"
        actual_desc = title_or_desc or ""

    return discord.Embed(
        title=f"❌ {actual_title}",
        description=actual_desc,
        color=discord.Color.red(),
    )


def success_embed(
    title_or_desc: Optional[str] = None,
    description: Optional[str] = None,
    *,
    title: Optional[str] = None,
) -> discord.Embed:
    """Build a green success embed."""
    # Determine the actual title and description
    if title is not None:
        actual_title = title
        actual_desc = description or title_or_desc or ""
    elif description is not None:
        actual_title = title_or_desc or "Success"
        actual_desc = description
    else:
        actual_title = "Success"
        actual_desc = title_or_desc or ""

    return discord.Embed(
        title=f"✅ {actual_title}",
        description=actual_desc,
        color=discord.Color.green(),
    )
