"""Team management cog for adding/removing members and viewing rosters."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core.models import TournamentStatus
from utils.permissions import is_admin, check_is_admin
from utils import embeds


class TeamsCog(commands.Cog):
    """Cog for managing team rosters — adding, removing, and viewing players."""

    def __init__(self, bot: "TournamentBot") -> None:
        self.bot = bot

    @app_commands.command(
        name="add_member",
        description="Add a player to a team (or substitute in an ongoing tournament).",
    )
    @app_commands.describe(
        player="The Discord member to add",
        ign="The player's in-game name",
        team_name="The team name (required if tournament is in progress)",
    )
    async def add_member(
        self,
        interaction: discord.Interaction,
        player: discord.Member,
        ign: str,
        team_name: str | None = None,
    ) -> None:
        """Add a Discord member to a team by team name.

        Works both in SETUP and IN_PROGRESS phases.
        """
        guild_id = interaction.guild_id

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        if tournament.status not in (TournamentStatus.SETUP, TournamentStatus.IN_PROGRESS):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Tournament is currently in **{tournament.status.value}** status and not accepting new players."
                ),
                ephemeral=True,
            )
            return

        # If tournament is IN_PROGRESS, only tournament admins can make roster adjustments
        if tournament.status == TournamentStatus.IN_PROGRESS:
            if not await check_is_admin(interaction):
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        "Only tournament admins can add or substitute players while a tournament is in progress."
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

        all_teams = await self.bot.db.get_teams(tournament.id)
        target_team = None

        if tournament.status == TournamentStatus.IN_PROGRESS:
            # When in progress, bracket is fixed. Player must join an EXISTING team with an open spot.
            if team_name and team_name.strip():
                query = team_name.strip().lower()
                for t in all_teams:
                    if t.name.lower() == query or t.name.lower().replace(" (vacant)", "") == query:
                        target_team = t
                        break
                    # Also allow searching by seed number e.g. "3" or "team 3"
                    if query in (str(t.seed), f"team {t.seed}"):
                        target_team = t
                        break
            else:
                # If team_name omitted, check if there is an under-filled or vacant team
                vacant_teams = []
                for t in all_teams:
                    cnt = await self.bot.db.get_player_count(t.id)
                    if cnt < tournament.team_size:
                        vacant_teams.append(t)

                if len(vacant_teams) == 1:
                    target_team = vacant_teams[0]
                elif len(vacant_teams) == 0:
                    await interaction.response.send_message(
                        embed=embeds.error_embed(
                            "All teams are currently full! Use `/remove_member` or `/replace_member` first."
                        ),
                        ephemeral=True,
                    )
                    return
                else:
                    vacant_names = ", ".join(f"`{t.name}`" for t in vacant_teams)
                    await interaction.response.send_message(
                        embed=embeds.error_embed(
                            f"Multiple teams have open spots: {vacant_names}.\n"
                            f"Please specify `team_name` when using `/add_member`."
                        ),
                        ephemeral=True,
                    )
                    return

            if not target_team:
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        f"Could not find an existing team matching **{team_name}**.\n"
                        f"Use `/tournament_info` or `/view_bracket` to check current team names."
                    ),
                    ephemeral=True,
                )
                return

            player_count = await self.bot.db.get_player_count(target_team.id)
            if player_count >= tournament.team_size:
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        f"**{target_team.name}** is already full ({player_count}/{tournament.team_size})."
                    ),
                    ephemeral=True,
                )
                return

            # Add the player
            await self.bot.db.add_player(target_team.id, str(player.id), ign.strip())

            # Update team name if 1v1 or if marked (Vacant)
            if tournament.team_size == 1:
                await self.bot.db.update_team_name(target_team.id, player.display_name)
                display_team_name = player.display_name
            else:
                if target_team.name.endswith(" (Vacant)"):
                    cleaned = target_team.name.replace(" (Vacant)", "")
                    await self.bot.db.update_team_name(target_team.id, cleaned)
                    display_team_name = cleaned
                else:
                    display_team_name = target_team.name

            await interaction.response.send_message(
                embed=embeds.success_embed(
                    title="✅ Player Added to Ongoing Tournament",
                    description=(
                        f"{player.mention} (IGN: **{ign.strip()}**) has joined "
                        f"**{display_team_name}**.\n\n"
                        f"Bracket fixtures, `/submit_result`, `@mentions`, and OCR will now recognize {player.mention} everywhere."
                    ),
                )
            )
            return

        # Setup phase:
        resolved_team_name = team_name.strip() if team_name and team_name.strip() else player.display_name
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
            return

        if tournament.status not in (TournamentStatus.SETUP, TournamentStatus.IN_PROGRESS):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Tournament is in **{tournament.status.value}** status and not accepting changes."
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
        team = await self.bot.db.get_team(team_id)
        team_name = team.name if team else "Team"

        await self.bot.db.remove_player(existing_player.id)

        # Check remaining players
        remaining = await self.bot.db.get_players(team_id)

        if tournament.status == TournamentStatus.SETUP:
            # In setup phase, empty teams are cleaned up
            if not remaining:
                await self.bot.db.delete_team(team_id)
            desc = f"{player.mention} has been removed from **{team_name}**."
        else:
            # In progress phase, NEVER delete team slot (needed for bracket fixtures)
            if tournament.team_size == 1:
                vacant_name = f"Team {team.seed} (Vacant)" if team else "Vacant"
                await self.bot.db.update_team_name(team_id, vacant_name)
            elif not remaining and team:
                if not team.name.endswith(" (Vacant)"):
                    await self.bot.db.update_team_name(team_id, f"{team.name} (Vacant)")

            desc = (
                f"{player.mention} has been removed from **{team_name}**.\n\n"
                f"*(Tournament is in progress. The team slot remains in the bracket. "
                f"You can add a replacement using `/add_member` or `/replace_member`.)*"
            )

        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="🗑️ Player Removed",
                description=desc,
            )
        )

    @app_commands.command(
        name="replace_member",
        description="Substitute a player in the tournament with a new player.",
    )
    @app_commands.describe(
        old_player="The registered member being replaced",
        new_player="The new Discord member joining the team",
        new_ign="The in-game name of the new player",
    )
    @is_admin()
    async def replace_member(
        self,
        interaction: discord.Interaction,
        old_player: discord.Member,
        new_player: discord.Member,
        new_ign: str,
    ) -> None:
        """Atomically substitute a player with a new player."""
        guild_id = interaction.guild_id

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        if tournament.status not in (TournamentStatus.SETUP, TournamentStatus.IN_PROGRESS):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Tournament is in **{tournament.status.value}** status and not accepting changes."
                ),
                ephemeral=True,
            )
            return

        # Check old player
        existing_old = await self.bot.db.get_player_by_discord_id(
            tournament.id, str(old_player.id)
        )
        if not existing_old:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"{old_player.mention} is not registered in this tournament."
                ),
                ephemeral=True,
            )
            return

        # Check new player not already registered
        existing_new = await self.bot.db.get_player_by_discord_id(
            tournament.id, str(new_player.id)
        )
        if existing_new:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"{new_player.mention} is already registered in this tournament."
                ),
                ephemeral=True,
            )
            return

        team = await self.bot.db.get_team(existing_old.team_id)
        if not team:
            await interaction.response.send_message(
                embed=embeds.error_embed("Team not found."),
                ephemeral=True,
            )
            return

        # Replace player
        await self.bot.db.remove_player(existing_old.id)
        await self.bot.db.add_player(team.id, str(new_player.id), new_ign.strip())

        # Update team name if 1v1
        if tournament.team_size == 1:
            await self.bot.db.update_team_name(team.id, new_player.display_name)
            display_name = new_player.display_name
        else:
            if team.name.endswith(" (Vacant)"):
                cleaned = team.name.replace(" (Vacant)", "")
                await self.bot.db.update_team_name(team.id, cleaned)
                display_name = cleaned
            else:
                display_name = team.name

        await interaction.response.send_message(
            embed=embeds.success_embed(
                title="🔄 Player Substituted",
                description=(
                    f"{old_player.mention} has been replaced by {new_player.mention} "
                    f"(IGN: **{new_ign.strip()}**) on **{display_name}**.\n\n"
                    f"Bracket fixtures, `/submit_result`, `@mentions`, and OCR will now recognize {new_player.mention} everywhere."
                ),
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
