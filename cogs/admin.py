"""Admin cog for manual result management — update, remove, force, and enter."""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.models import TournamentStatus, TournamentFormat, MatchStatus, RoundStatus
from utils.permissions import is_admin
from utils import embeds


class AdminCog(commands.Cog):
    """Cog providing admin-only commands for managing match results."""

    def __init__(self, bot: "TournamentBot") -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_winner(
        home_agg: int,
        away_agg: int,
        home_away_goals: int,
        away_home_goals: int,
        home_team_id: int,
        away_team_id: int,
    ) -> Optional[int]:
        """Determine winner by aggregate, then away-goals rule.

        Returns:
            The winning team ID, or ``None`` if the tie is unresolvable.
        """
        if home_agg > away_agg:
            return home_team_id
        if away_agg > home_agg:
            return away_team_id

        # Away-goals tiebreak
        if away_home_goals > home_away_goals:
            return away_team_id
        if home_away_goals > away_home_goals:
            return home_team_id

        return None

    async def _get_teams_dict(self, tournament_id: int) -> dict[int, object]:
        """Return a dict mapping team ID to team object for a tournament."""
        all_teams = await self.bot.db.get_teams(tournament_id)
        return {t.id: t for t in all_teams}

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @app_commands.command(
        name="update_result",
        description="Update a single leg score for a match.",
    )
    @app_commands.describe(
        match_id="The match ID to update",
        leg="Leg number (1 or 2)",
        home_score="Home team score",
        away_score="Away team score",
    )
    @is_admin()
    async def update_result(
        self,
        interaction: discord.Interaction,
        match_id: int,
        leg: int,
        home_score: int,
        away_score: int,
    ) -> None:
        """Update the score of a specific leg and recalculate the match result."""
        match = await self.bot.db.get_match(match_id)
        if not match:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Match **#{match_id}** not found."),
                ephemeral=True,
            )
            return

        if leg not in (1, 2):
            await interaction.response.send_message(
                embed=embeds.error_embed("Leg must be **1** or **2**."),
                ephemeral=True,
            )
            return

        # Update the leg score
        await self.bot.db.update_leg_score(match_id, leg, home_score, away_score)

        # Recalculate aggregate from both legs
        legs = await self.bot.db.get_legs(match_id)
        if len(legs) < 2:
            await interaction.response.send_message(
                embed=embeds.success_embed(
                    title="✅ Leg Score Updated",
                    description=(
                        f"Leg **{leg}** of match **#{match_id}** updated to "
                        f"**{home_score}** - **{away_score}**.\n\n"
                        f"Both legs are needed to determine a winner."
                    ),
                )
            )
            return

        # Sort legs by leg number
        legs_sorted = sorted(legs, key=lambda l: l.leg_number)
        leg1, leg2 = legs_sorted[0], legs_sorted[1]

        if leg1.home_score is None or leg2.home_score is None:
            await interaction.response.send_message(
                embed=embeds.success_embed(
                    title="✅ Leg Score Updated",
                    description=(
                        f"Leg **{leg}** of match **#{match_id}** updated to "
                        f"**{home_score}** - **{away_score}**.\n\n"
                        f"Waiting for both legs to determine winner."
                    ),
                )
            )
            return

        home_agg = leg1.home_score + leg2.home_score
        away_agg = leg1.away_score + leg2.away_score

        winner_team_id = self._determine_winner(
            home_agg=home_agg,
            away_agg=away_agg,
            home_away_goals=leg2.home_score,
            away_home_goals=leg1.away_score,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
        )

        if winner_team_id is not None:
            await self.bot.db.set_match_winner(match_id, winner_team_id)
            await self.bot.db.update_match_status(match_id, MatchStatus.COMPLETED)

        tournament = await self.bot.db.get_active_tournament(interaction.guild_id)
        teams_dict = await self._get_teams_dict(tournament.id) if tournament else {}

        home_players = await self.bot.db.get_players_by_team(match.home_team_id)
        away_players = await self.bot.db.get_players_by_team(match.away_team_id)
        home_display = ", ".join(f"<@{p.discord_id}>" for p in home_players) if home_players else getattr(teams_dict.get(match.home_team_id), "name", "Home")
        away_display = ", ".join(f"<@{p.discord_id}>" for p in away_players) if away_players else getattr(teams_dict.get(match.away_team_id), "name", "Away")

        if winner_team_id == match.home_team_id:
            winner_display = home_display
        elif winner_team_id == match.away_team_id:
            winner_display = away_display
        else:
            winner_display = "TBD (use `/force_winner`)"

        result_embed = discord.Embed(
            title="✅ Result Updated",
            color=discord.Color.green(),
        )
        result_embed.add_field(
            name="Leg 1",
            value=f"{home_display} **{leg1.home_score}** - **{leg1.away_score}** {away_display}",
            inline=False,
        )
        result_embed.add_field(
            name="Leg 2",
            value=f"{home_display} **{leg2.home_score}** - **{leg2.away_score}** {away_display}",
            inline=False,
        )
        result_embed.add_field(
            name="Aggregate",
            value=f"**{home_agg}** - **{away_agg}**",
            inline=True,
        )
        result_embed.add_field(
            name="Winner", value=f"🏅 **{winner_display}**", inline=True
        )

        await interaction.response.send_message(embed=result_embed)

    @app_commands.command(
        name="remove_result",
        description="Clear the result of a match.",
    )
    @app_commands.describe(match_id="The match ID to clear")
    @is_admin()
    async def remove_result(
        self,
        interaction: discord.Interaction,
        match_id: int,
    ) -> None:
        """Clear all scores and the winner for a match."""
        match = await self.bot.db.get_match(match_id)
        if not match:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Match **#{match_id}** not found."),
                ephemeral=True,
            )
            return

        await self.bot.db.clear_match_result(match_id)

        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="🗑️ Result Cleared",
                description=f"All scores and winner for match **#{match_id}** have been removed.",
            )
        )

    @app_commands.command(
        name="force_winner",
        description="Force-set the winner of a match.",
    )
    @app_commands.describe(
        match_id="The match ID",
        team="The winning team name or player",
    )
    @is_admin()
    async def force_winner(
        self,
        interaction: discord.Interaction,
        match_id: int,
        team: str,
    ) -> None:
        """Forcibly set a match winner by team name or player."""
        match = await self.bot.db.get_match(match_id)
        if not match:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Match **#{match_id}** not found."),
                ephemeral=True,
            )
            return

        tournament = await self.bot.db.get_active_tournament(interaction.guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        home_team = await self.bot.db.get_team(match.home_team_id)
        away_team = await self.bot.db.get_team(match.away_team_id)
        home_players = await self.bot.db.get_players_by_team(match.home_team_id)
        away_players = await self.bot.db.get_players_by_team(match.away_team_id)

        target = team.strip().lower()
        chosen_team = None

        if home_team and (home_team.name.lower() == target or any(target in (p.ign.lower(), p.discord_id, f"<@{p.discord_id}>") for p in home_players)):
            chosen_team = home_team
        elif away_team and (away_team.name.lower() == target or any(target in (p.ign.lower(), p.discord_id, f"<@{p.discord_id}>") for p in away_players)):
            chosen_team = away_team
        elif home_team and target in home_team.name.lower():
            chosen_team = home_team
        elif away_team and target in away_team.name.lower():
            chosen_team = away_team

        if not chosen_team:
            h_name = home_team.name if home_team else "Home"
            a_name = away_team.name if away_team else "Away"
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Team/player **{team}** not recognized for match **#{match_id}**.\n"
                    f"Participants: **{h_name}** vs **{a_name}**."
                ),
                ephemeral=True,
            )
            return

        await self.bot.db.set_match_winner(match_id, chosen_team.id)
        await self.bot.db.update_match_status(match_id, MatchStatus.COMPLETED)

        win_players = home_players if chosen_team.id == match.home_team_id else away_players
        win_display = ", ".join(f"<@{p.discord_id}>" for p in win_players) if win_players else chosen_team.name

        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="🏆 Winner Forced",
                description=(
                    f"Match **#{match_id}** winner forcibly set to "
                    f"🏅 **{win_display}**."
                ),
            )
        )

    @app_commands.command(
        name="manual_result",
        description="Manually enter both leg scores for a match.",
    )
    @app_commands.describe(
        match_id="The match ID",
        leg1_home="Leg 1 home team score",
        leg1_away="Leg 1 away team score",
        leg2_home="Leg 2 home team score",
        leg2_away="Leg 2 away team score",
    )
    @is_admin()
    async def manual_result(
        self,
        interaction: discord.Interaction,
        match_id: int,
        leg1_home: int,
        leg1_away: int,
        leg2_home: int,
        leg2_away: int,
    ) -> None:
        """Manually enter scores for both legs and determine the winner."""
        match = await self.bot.db.get_match(match_id)
        if not match:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Match **#{match_id}** not found."),
                ephemeral=True,
            )
            return

        # Update both legs
        await self.bot.db.update_leg_score(match_id, 1, leg1_home, leg1_away)
        await self.bot.db.update_leg_score(match_id, 2, leg2_home, leg2_away)

        # Calculate aggregate
        home_agg = leg1_home + leg2_home
        away_agg = leg1_away + leg2_away

        # Determine winner
        winner_team_id = self._determine_winner(
            home_agg=home_agg,
            away_agg=away_agg,
            home_away_goals=leg2_home,
            away_home_goals=leg1_away,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
        )

        tournament = await self.bot.db.get_active_tournament(interaction.guild_id)
        teams_dict = await self._get_teams_dict(tournament.id) if tournament else {}

        home_players = await self.bot.db.get_players_by_team(match.home_team_id)
        away_players = await self.bot.db.get_players_by_team(match.away_team_id)
        home_display = ", ".join(f"<@{p.discord_id}>" for p in home_players) if home_players else getattr(teams_dict.get(match.home_team_id), "name", "Home")
        away_display = ", ".join(f"<@{p.discord_id}>" for p in away_players) if away_players else getattr(teams_dict.get(match.away_team_id), "name", "Away")

        if winner_team_id == match.home_team_id:
            await self.bot.db.set_match_winner(match_id, winner_team_id)
            await self.bot.db.update_match_status(match_id, MatchStatus.COMPLETED)
            winner_display = home_display
        elif winner_team_id == match.away_team_id:
            await self.bot.db.set_match_winner(match_id, winner_team_id)
            await self.bot.db.update_match_status(match_id, MatchStatus.COMPLETED)
            winner_display = away_display
        else:
            # Tied — admin must resolve manually
            await self.bot.db.update_match_status(match_id, MatchStatus.COMPLETED)
            winner_display = "Draw — use `/force_winner` to decide"

        result_embed = discord.Embed(
            title="📊 Manual Result Recorded",
            color=discord.Color.green(),
        )
        result_embed.add_field(
            name="Leg 1 (Home)",
            value=f"{home_display} **{leg1_home}** - **{leg1_away}** {away_display}",
            inline=False,
        )
        result_embed.add_field(
            name="Leg 2 (Away)",
            value=f"{home_display} **{leg2_home}** - **{leg2_away}** {away_display}",
            inline=False,
        )
        result_embed.add_field(
            name="Aggregate",
            value=f"**{home_agg}** - **{away_agg}**",
            inline=True,
        )
        result_embed.add_field(
            name="Winner", value=f"🏅 **{winner_display}**", inline=True
        )

        await interaction.response.send_message(embed=result_embed)

    # ------------------------------------------------------------------
    # Tournament Admin Management Commands
    # ------------------------------------------------------------------

    @app_commands.command(
        name="add_admin",
        description="Add a member as a tournament admin.",
    )
    @app_commands.describe(user="The Discord user to grant tournament admin permissions")
    @is_admin()
    async def add_admin(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Add a tournament admin."""
        guild_id = str(interaction.guild_id)
        success = await self.bot.db.add_admin(
            guild_id=guild_id,
            discord_id=str(user.id),
            added_by=str(interaction.user.id),
        )

        if success:
            await interaction.response.send_message(
                embed=embeds.success_embed(
                    title="Admin Added",
                    description=f"{user.mention} is now a tournament admin.",
                )
            )
        else:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"{user.mention} is already a tournament admin."
                ),
                ephemeral=True,
            )

    @app_commands.command(
        name="remove_admin",
        description="Revoke tournament admin permissions from a member.",
    )
    @app_commands.describe(user="The Discord user to remove as tournament admin")
    @is_admin()
    async def remove_admin(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Remove a tournament admin."""
        guild_id = str(interaction.guild_id)
        removed = await self.bot.db.remove_admin(
            guild_id=guild_id,
            discord_id=str(user.id),
        )

        if removed:
            await interaction.response.send_message(
                embed=embeds.success_embed(
                    title="Admin Removed",
                    description=f"{user.mention} has been removed from tournament admins.",
                )
            )
        else:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"{user.mention} was not found in the tournament admins list."
                ),
                ephemeral=True,
            )

    @app_commands.command(
        name="list_admins",
        description="View the list of registered tournament admins.",
    )
    @is_admin()
    async def list_admins(self, interaction: discord.Interaction) -> None:
        """List all registered tournament admins."""
        guild_id = str(interaction.guild_id)
        admin_ids = await self.bot.db.get_admins(guild_id)

        embed = discord.Embed(
            title="🛡️ Tournament Admins",
            color=discord.Color.blue(),
        )

        # Show server owner
        if interaction.guild and interaction.guild.owner:
            owner_text = f"👑 {interaction.guild.owner.mention} *(Server Owner)*"
        else:
            owner_text = "Unknown"
        embed.add_field(name="Server Owner", value=owner_text, inline=False)

        # Show appointed admins
        if admin_ids:
            admin_list = "\n".join(f"• <@{aid}>" for aid in admin_ids)
        else:
            admin_list = "*No custom admins added yet. Use `/add_admin`.*"

        embed.add_field(name="Appointed Admins", value=admin_list, inline=False)
        embed.set_footer(text="Users with Administrator server permission also have access.")

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # Results Channel Restrictions
    # ------------------------------------------------------------------

    @app_commands.command(
        name="set_results_channel",
        description="Restrict match result submissions (@p1 vs @p2 & /submit_result) to a specific channel.",
    )
    @app_commands.describe(channel="The designated channel for submitting match results")
    @is_admin()
    async def set_results_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        """Set the designated results channel for this server."""
        guild_id = str(interaction.guild_id)
        await self.bot.db.set_results_channel(guild_id, str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="Results Channel Set",
                description=f"Match result submissions are now restricted to {channel.mention}.\n"
                            f"Messages sent in other channels will be redirected here.",
            )
        )

    @app_commands.command(
        name="clear_results_channel",
        description="Remove channel restrictions and allow match result submissions in any channel.",
    )
    @is_admin()
    async def clear_results_channel(self, interaction: discord.Interaction) -> None:
        """Remove the channel restriction for match results."""
        guild_id = str(interaction.guild_id)
        await self.bot.db.set_results_channel(guild_id, None)
        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="Results Channel Cleared",
                description="Match results can now be submitted in any channel where the bot has access.",
            )
        )


async def setup(bot: "TournamentBot") -> None:
    """Register the AdminCog with the bot."""
    await bot.add_cog(AdminCog(bot))
