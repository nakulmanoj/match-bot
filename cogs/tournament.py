"""Tournament management cog for creating, starting, and viewing tournaments."""

import math

import discord
from discord import app_commands
from discord.ext import commands

from core.models import TournamentStatus, TournamentFormat, MatchStatus, RoundStatus
from utils.permissions import is_admin
from utils import embeds


class TournamentCog(commands.Cog):
    """Cog for managing tournament lifecycle — creation, starting, and info."""

    def __init__(self, bot: "TournamentBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="create_tournament",
        description="Create a new tournament for this server.",
    )
    @app_commands.describe(
        name="Name of the tournament",
        num_teams="Number of teams (must be a power of 2)",
        team_size="Number of players per team (minimum 1)",
    )
    @is_admin()
    async def create_tournament(
        self,
        interaction: discord.Interaction,
        name: str,
        num_teams: int,
        team_size: int,
    ) -> None:
        """Create a new tournament with the specified parameters.

        Validates that no active tournament exists, num_teams is a power of 2,
        and team_size is at least 1. Creates team slots automatically.
        """
        guild_id = str(interaction.guild_id)

        # Check for existing active tournament
        existing = await self.bot.db.get_active_tournament(guild_id)
        if existing:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "A tournament is already active in this server. "
                    "Complete or cancel it before creating a new one."
                ),
                ephemeral=True,
            )
            return

        # Validate num_teams is a power of 2
        if num_teams < 2 or (num_teams & (num_teams - 1)) != 0:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"`num_teams` must be a power of 2 (e.g. 2, 4, 8, 16). "
                    f"Got **{num_teams}**."
                ),
                ephemeral=True,
            )
            return

        # Validate team_size
        if team_size < 1:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "`team_size` must be at least **1**."
                ),
                ephemeral=True,
            )
            return

        # Create the tournament
        tournament = await self.bot.db.create_tournament(
            guild_id=guild_id,
            name=name,
            num_teams=num_teams,
            team_size=team_size,
        )

        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="🏆 Tournament Created",
                description=(
                    f"**{name}**\n\n"
                    f"**Teams:** {num_teams}\n"
                    f"**Team Size:** {team_size} player(s)\n"
                    f"**Status:** Setup\n\n"
                    f"Use `/add_member` to register teams and players, "
                    f"then `/start_tournament` when all {num_teams} teams are filled."
                ),
            )
        )

    @app_commands.command(
        name="start_tournament",
        description="Start the current tournament with the chosen format.",
    )
    @app_commands.describe(format="The tournament format to use")
    @app_commands.choices(
        format=[app_commands.Choice(name="Knockout", value="knockout")]
    )
    @is_admin()
    async def start_tournament(
        self,
        interaction: discord.Interaction,
        format: app_commands.Choice[str],
    ) -> None:
        """Start the tournament after verifying all teams are filled.

        Generates the first round bracket based on team seedings.
        """
        guild_id = str(interaction.guild_id)

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        if tournament.status != TournamentStatus.SETUP:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Tournament has already been started or completed."
                ),
                ephemeral=True,
            )
            return

        # Verify all teams are full
        teams = await self.bot.db.get_teams(tournament.id)
        incomplete_teams: list[str] = []

        if len(teams) < tournament.num_teams:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Tournament requires **{tournament.num_teams}** teams to start "
                    f"(currently **{len(teams)}** registered).\n"
                    f"Use `/add_member` to register the remaining teams."
                ),
                ephemeral=True,
            )
            return

        for team in teams:
            player_count = await self.bot.db.get_player_count(team.id)
            if player_count != tournament.team_size:
                incomplete_teams.append(
                    f"**{team.name}** ({player_count}/{tournament.team_size})"
                )

        if incomplete_teams:
            team_list = "\n".join(incomplete_teams)
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"The following teams are incomplete:\n{team_list}\n\n"
                    f"All teams must have exactly **{tournament.team_size}** "
                    f"player(s) before starting."
                ),
                ephemeral=True,
            )
            return

        # Ensure 1v1 teams don't retain legacy "Team X" names
        if tournament.team_size == 1:
            for team in teams:
                if team.name.startswith("Team "):
                    players = await self.bot.db.get_players_by_team(team.id)
                    if players:
                        member = interaction.guild.get_member(int(players[0].discord_id)) if interaction.guild else None
                        new_name = member.display_name if member else players[0].ign
                        await self.bot.db.update_team_name(team.id, new_name)
            teams = await self.bot.db.get_teams(tournament.id)

        # Update tournament format and status
        tournament_format = TournamentFormat.KNOCKOUT
        await self.bot.db.update_tournament(
            tournament.id,
            format=tournament_format,
            status=TournamentStatus.IN_PROGRESS,
        )

        # Generate first round bracket
        from core.bracket_engine import generate_first_round

        sorted_teams = sorted(teams, key=lambda t: t.seed)
        first_round = await generate_first_round(self.bot.db, tournament.id, sorted_teams)

        # Build data for the bracket embed
        matches = await self.bot.db.get_matches(first_round.id)
        teams_dict = {team.id: team for team in teams}
        legs_dict: dict[int, list] = {}
        for match in matches:
            legs = await self.bot.db.get_legs(match.id)
            legs_dict[match.id] = legs

        await interaction.response.send_message(
            embed=embeds.bracket_embed(
                round_obj=first_round,
                matches=matches,
                teams=teams_dict,
                legs=legs_dict,
                tournament_name=tournament.name,
            )
        )

    @app_commands.command(
        name="tournament_info",
        description="View information about the current tournament.",
    )
    async def tournament_info(self, interaction: discord.Interaction) -> None:
        """Display current tournament information for this guild."""
        guild_id = str(interaction.guild_id)

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        teams = await self.bot.db.get_teams(tournament.id)
        team_summaries: list[str] = []

        for team in sorted(teams, key=lambda t: t.seed):
            player_count = await self.bot.db.get_player_count(team.id)
            team_summaries.append(
                f"**{team.name}** (Seed #{team.seed}) — "
                f"{player_count}/{tournament.team_size} players"
            )

        embed = discord.Embed(
            title=f"🏆 {tournament.name}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Status", value=tournament.status.value, inline=True)
        embed.add_field(name="Teams", value=f"{len(teams)}/{tournament.num_teams}", inline=True)
        embed.add_field(
            name="Team Size", value=str(tournament.team_size), inline=True
        )
        if tournament.format:
            embed.add_field(
                name="Format", value=tournament.format.value, inline=True
            )

        if not team_summaries:
            embed.add_field(name="Registered Teams", value="No teams.", inline=False)
        elif len(team_summaries) <= 16:
            embed.add_field(name="Registered Teams", value="\n".join(team_summaries), inline=False)
        else:
            # Chunk teams into fields of 16 so we never exceed Discord's 1024-character field limit
            chunk_size = 16
            for i in range(0, len(team_summaries), chunk_size):
                chunk = team_summaries[i : i + chunk_size]
                field_title = f"Registered Teams ({i + 1}–{min(i + chunk_size, len(team_summaries))})"
                embed.add_field(name=field_title, value="\n".join(chunk), inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="cancel_tournament",
        description="Cancel and delete the currently active tournament.",
    )
    @is_admin()
    async def cancel_tournament(self, interaction: discord.Interaction) -> None:
        """Cancel and delete the currently active tournament for this server."""
        guild_id = str(interaction.guild_id)

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found to cancel."),
                ephemeral=True,
            )
            return

        name = tournament.name
        await self.bot.db.delete_tournament(tournament.id)

        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="Tournament Cancelled",
                description=(
                    f"Tournament **{name}** has been cancelled and removed. "
                    f"You can now create a new tournament with `/create_tournament`."
                ),
            )
        )


async def setup(bot: "TournamentBot") -> None:
    """Register the TournamentCog with the bot."""
    await bot.add_cog(TournamentCog(bot))
