"""Team management cog for adding/removing members and viewing rosters."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core.models import TournamentStatus
from utils.permissions import is_admin
from utils import embeds


class TeamsCog(commands.Cog):
    """Cog for managing team rosters — adding, removing, and viewing players."""

    def __init__(self, bot: "TournamentBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="add_member",
        description="Add a player to a team.",
    )
    @app_commands.describe(
        player="The Discord member to add",
        ign="The player's in-game name",
        team_name="The team name (defaults to player's name if omitted)",
    )
    async def add_member(
        self,
        interaction: discord.Interaction,
        player: discord.Member,
        ign: str,
        team_name: str | None = None,
    ) -> None:
        """Add a Discord member to a team by team name.

        Validates the tournament is in SETUP, the team isn't full,
        and the player isn't already registered.
        """
        guild_id = interaction.guild_id

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
                    "Players can only be added during the **Setup** phase."
                ),
                ephemeral=True,
            )
            return

        # Check if player is already registered anywhere in this tournament
        existing_player = await self.bot.db.get_player_by_discord_id(
            tournament.id, str(player.id)
        )
        if existing_player:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"{player.mention} is already registered in this tournament."
                ),
                ephemeral=True,
            )
            return

        resolved_team_name = team_name.strip() if team_name and team_name.strip() else player.display_name

        # Look up if team already exists
        target_team = await self.bot.db.get_team_by_name(tournament.id, resolved_team_name)

        if target_team:
            player_count = await self.bot.db.get_player_count(target_team.id)
            if player_count >= tournament.team_size:
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        f"**{target_team.name}** is already full "
                        f"({player_count}/{tournament.team_size})."
                    ),
                    ephemeral=True,
                )
                return
        else:
            # Check tournament capacity
            all_teams = await self.bot.db.get_teams(tournament.id)
            if len(all_teams) >= tournament.num_teams:
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        f"Tournament is already at capacity "
                        f"({len(all_teams)}/{tournament.num_teams} teams registered)."
                    ),
                    ephemeral=True,
                )
                return

            target_team = await self.bot.db.create_team(
                tournament_id=tournament.id,
                name=resolved_team_name,
                seed=len(all_teams) + 1,
            )

        # Add the player
        await self.bot.db.add_player(target_team.id, str(player.id), ign.strip())

        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="✅ Player Added",
                description=(
                    f"{player.mention} (IGN: **{ign.strip()}**) has joined "
                    f"**{target_team.name}**."
                ),
            )
        )

    @app_commands.command(
        name="remove_member",
        description="Remove a player from the tournament.",
    )
    @app_commands.describe(player="The Discord member to remove")
    @is_admin()
    async def remove_member(
        self,
        interaction: discord.Interaction,
        player: discord.Member,
    ) -> None:
        """Remove a registered player from the tournament (admin only)."""
        guild_id = interaction.guild_id

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
        if tournament.status != TournamentStatus.SETUP:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Players can only be removed during the **Setup** phase. "
                    "Use `/force_winner` to disqualify or award walkovers once in progress."
                ),
                ephemeral=True,
            )
            return

        existing_player = await self.bot.db.get_player_by_discord_id(
            tournament.id, str(player.id)
        )
        if not existing_player:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"{player.mention} is not registered in this tournament."
                ),
                ephemeral=True,
            )
            return

        team_id = existing_player.team_id
        await self.bot.db.remove_player(existing_player.id)

        # If team is now empty, delete team slot
        remaining = await self.bot.db.get_players(team_id)
        if not remaining:
            await self.bot.db.delete_team(team_id)

        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="🗑️ Player Removed",
                description=f"{player.mention} has been removed from the tournament.",
            )
        )

    @app_commands.command(
        name="view_team",
        description="View a team's roster.",
    )
    @app_commands.describe(team="The team name or player")
    async def view_team(
        self,
        interaction: discord.Interaction,
        team: str,
    ) -> None:
        """Display the roster of a team by its name."""
        guild_id = interaction.guild_id

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        teams = await self.bot.db.get_teams(tournament.id)
        target = team.strip().lower()

        matched_team = None
        for t in teams:
            if t.name.lower() == target:
                matched_team = t
                break
            # Check players inside team
            players = await self.bot.db.get_players(t.id)
            if any(target in (p.ign.lower(), p.discord_id, f"<@{p.discord_id}>") for p in players):
                matched_team = t
                break

        if not matched_team:
            # Fuzzy fallback
            matched_team = next((t for t in teams if target in t.name.lower()), None)

        if not matched_team:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Team **{team}** does not exist."
                ),
                ephemeral=True,
            )
            return

        players = await self.bot.db.get_players(matched_team.id)

        await interaction.response.send_message(
            embed=embeds.team_roster(
                team=matched_team,
                players=players,
                team_size=tournament.team_size,
            )
        )

    @app_commands.command(
        name="rename_team",
        description="Rename a team in the tournament.",
    )
    @app_commands.describe(
        current_name="The current team name",
        new_name="The new team name",
    )
    @is_admin()
    async def rename_team(
        self,
        interaction: discord.Interaction,
        current_name: str,
        new_name: str,
    ) -> None:
        """Rename a team."""
        tournament = await self.bot.db.get_active_tournament(interaction.guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        team = await self.bot.db.get_team_by_name(tournament.id, current_name)
        if not team:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Team **{current_name}** not found."),
                ephemeral=True,
            )
            return

        clean_new_name = new_name.strip()
        existing_target = await self.bot.db.get_team_by_name(tournament.id, clean_new_name)
        if existing_target and existing_target.id != team.id:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"A team named **{clean_new_name}** already exists."),
                ephemeral=True,
            )
            return

        await self.bot.db.update_team_name(team.id, clean_new_name)

        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="✏️ Team Renamed",
                description=f"**{team.name}** has been renamed to **{clean_new_name}**.",
            )
        )

    @app_commands.command(
        name="view_all_teams",
        description="View a summary of all teams and their players.",
    )
    async def view_all_teams(self, interaction: discord.Interaction) -> None:
        """Display a summary of every team and its current roster."""
        guild_id = interaction.guild_id

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        teams = await self.bot.db.get_teams(tournament.id)

        embed = discord.Embed(
            title=f"📋 All Teams — {tournament.name}",
            color=discord.Color.blue(),
        )

        if not teams:
            embed.description = "*No teams registered yet. Use `/add_member` to register.*"
            await interaction.response.send_message(embed=embed)
            return

        for team in teams:
            players = await self.bot.db.get_players(team.id)
            if players:
                roster = "\n".join(
                    f"• <@{p.discord_id}> — {p.ign}" for p in players
                )
            else:
                roster = "*No players yet*"

            player_count = len(players)
            embed.add_field(
                name=f"{team.name} — {player_count}/{tournament.team_size}",
                value=roster,
                inline=False,
            )

        await interaction.response.send_message(embed=embed)


async def setup(bot: "TournamentBot") -> None:
    """Register the TeamsCog with the bot."""
    await bot.add_cog(TeamsCog(bot))
