"""Bracket viewing and round progression cog."""

import discord
from discord import app_commands
from discord.ext import commands

from core.models import TournamentStatus, TournamentFormat, MatchStatus, RoundStatus
from utils.permissions import is_admin
from utils import embeds


class BracketCog(commands.Cog):
    """Cog for viewing tournament brackets and advancing rounds."""

    def __init__(self, bot: "TournamentBot") -> None:
        self.bot = bot

    async def _display_bracket(self, interaction: discord.Interaction) -> None:
        """Internal helper to render and send the tournament bracket (embeds + image)."""
        guild_id = interaction.guild_id

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        rounds = await self.bot.db.get_rounds(tournament.id)
        if not rounds:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "No rounds have been generated yet. Start the tournament first."
                ),
                ephemeral=True,
            )
            return

        # Defer interaction since image generation might take a fraction of a second
        await interaction.response.defer()

        # Build data structures for each round
        all_teams = await self.bot.db.get_teams(tournament.id)
        teams_dict = {team.id: team for team in all_teams}

        round_data: list[dict] = []
        for round_obj in rounds:
            matches = await self.bot.db.get_matches(round_obj.id)
            legs_dict: dict[int, list] = {}
            for match in matches:
                legs = await self.bot.db.get_legs(match.id)
                legs_dict[match.id] = legs
            round_data.append(
                {
                    "round": round_obj,
                    "matches": matches,
                    "teams": teams_dict,
                    "legs": legs_dict,
                }
            )

        # Generate visual bracket image in memory
        from utils.bracket_visualizer import render_bracket_image
        import asyncio

        image_buf = await asyncio.to_thread(
            render_bracket_image, tournament.name, round_data
        )
        file = discord.File(image_buf, filename="bracket.png")

        bracket_embeds = embeds.full_bracket_embed(
            round_data=round_data,
            tournament_name=tournament.name,
        )

        if not bracket_embeds:
            await interaction.followup.send(file=file)
            return

        # Attach the image to the first embed or send with the followups
        first_embed = bracket_embeds[0]
        first_embed.set_image(url="attachment://bracket.png")

        await interaction.followup.send(embed=first_embed, file=file)
        for embed_obj in bracket_embeds[1:]:
            await interaction.followup.send(embed=embed_obj)

    @app_commands.command(
        name="view_bracket",
        description="View the tournament bracket with match scores and visual diagram.",
    )
    async def view_bracket(self, interaction: discord.Interaction) -> None:
        """Display the complete tournament bracket across all rounds."""
        await self._display_bracket(interaction)

    @app_commands.command(
        name="brackets",
        description="Visualise the full tournament bracket diagram and fixtures.",
    )
    async def brackets(self, interaction: discord.Interaction) -> None:
        """Visualise the tournament bracket with a rendered bracket image."""
        await self._display_bracket(interaction)

    @app_commands.command(
        name="next_round",
        description="Advance the tournament to the next round.",
    )
    @is_admin()
    async def next_round(self, interaction: discord.Interaction) -> None:
        """Advance the tournament to the next round after all matches complete.

        Checks that every match in the current round has been completed.
        If the bracket is exhausted, declares the tournament winner.
        """
        guild_id = interaction.guild_id

        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active tournament found."),
                ephemeral=True,
            )
            return

        if tournament.status != TournamentStatus.IN_PROGRESS:
            await interaction.response.send_message(
                embed=embeds.error_embed("Tournament is not currently in progress."),
                ephemeral=True,
            )
            return

        current_round = await self.bot.db.get_current_round(tournament.id)
        if not current_round:
            await interaction.response.send_message(
                embed=embeds.error_embed("No active round found."),
                ephemeral=True,
            )
            return

        # Check if all matches are completed and have a winner decided
        matches = await self.bot.db.get_matches(current_round.id)
        pending = [
            m for m in matches if m.status != MatchStatus.COMPLETED or m.winner_team_id is None
        ]
        if pending:
            pending_list = "\n".join(
                f"• Match **#{m.id}**" + (" *(Tied — use `/force_winner`)*" if m.status == MatchStatus.COMPLETED and not m.winner_team_id else "")
                for m in pending
            )
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Not all matches have an established winner in the current round.\n\n"
                    f"**Pending matches:**\n{pending_list}"
                ),
                ephemeral=True,
            )
            return

        # Mark current round as completed
        await self.bot.db.update_round_status(
            current_round.id, RoundStatus.COMPLETED
        )

        # Generate the next round
        from core.bracket_engine import generate_next_round

        next_round_obj = await generate_next_round(
            self.bot.db, tournament.id, current_round
        )

        if next_round_obj is None:
            # Tournament is over — determine the winner
            await self.bot.db.update_tournament(
                tournament.id, status=TournamentStatus.COMPLETED
            )

            # The winner is the winner of the last match in the final round
            final_matches = await self.bot.db.get_matches(current_round.id)
            if final_matches and final_matches[0].winner_team_id:
                all_teams = await self.bot.db.get_teams(tournament.id)
                teams_dict = {t.id: t for t in all_teams}
                winner = teams_dict.get(final_matches[0].winner_team_id)
                winner_name = winner.name if winner else "Unknown"

                await interaction.response.send_message(
                    embed=embeds.success_embed(
                        title="🏆 Tournament Complete!",
                        description=(
                            f"**{tournament.name}** has concluded!\n\n"
                            f"🥇 Winner: **{winner_name}**\n\n"
                            f"Congratulations!"
                        ),
                    )
                )
            else:
                await interaction.response.send_message(
                    embed=embeds.success_embed(
                        title="🏆 Tournament Complete!",
                        description=f"**{tournament.name}** has concluded!",
                    )
                )
            return

        # Display the new round bracket
        all_teams = await self.bot.db.get_teams(tournament.id)
        teams_dict = {t.id: t for t in all_teams}
        matches = await self.bot.db.get_matches(next_round_obj.id)
        legs_dict: dict[int, list] = {}
        for match in matches:
            legs = await self.bot.db.get_legs(match.id)
            legs_dict[match.id] = legs

        await interaction.response.send_message(
            embed=embeds.bracket_embed(
                round_obj=next_round_obj,
                matches=matches,
                teams=teams_dict,
                legs=legs_dict,
                tournament_name=tournament.name,
            )
        )


async def setup(bot: "TournamentBot") -> None:
    """Register the BracketCog with the bot."""
    await bot.add_cog(BracketCog(bot))
