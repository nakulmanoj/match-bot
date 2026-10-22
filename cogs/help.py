"""Help command cog showing all available commands categorized for users and admins."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class HelpCog(commands.Cog):
    """Cog providing interactive help and command listing."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="help",
        description="Display all available tournament bot commands and how to use them.",
    )
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Show categorized command list."""
        embed = discord.Embed(
            title="📖 FC Mobile Tournament Bot — Command Guide",
            description=(
                "Welcome to the FC Mobile Tournament Bot! Below is the list of all commands "
                "available to everyone, as well as admin management commands."
            ),
            color=discord.Color.blue(),
        )

        # General & Registration commands
        embed.add_field(
            name="👥 Team & Player Registration (Everyone)",
            value=(
                "`/add_member player ign [team_name]`\n"
                "↳ Add a player with their Discord mention and In-Game Name (defaults to player name in 1v1).\n\n"
                "`/view_team team`\n"
                "↳ View a specific team's roster.\n\n"
                "`/rename_team current_name new_name`\n"
                "↳ Rename a team in the tournament.\n\n"
                "`/view_all_teams`\n"
                "↳ View all teams and their registered players.\n\n"
                "`/tournament_info`\n"
                "↳ Check active tournament name, team count, capacity, and status."
            ),
            inline=False,
        )

        # Bracket & Matches
        embed.add_field(
            name="⚔️ Bracket & Matches (Everyone)",
            value=(
                "`/brackets` or `/view_bracket`\n"
                "↳ Visualise the tournament bracket with a rendered graphical diagram, fixtures, and scores."
            ),
            inline=False,
        )

        # Result Submission Flow
        embed.add_field(
            name="📸 Submitting Match Results (Everyone)",
            value=(
                "Post a message in chat mentioning players from each team with screenshots attached:\n"
                "```\n@TeamA_Player vs @TeamB_Player\n[Screenshot 1, Screenshot 2, Screenshot 3, Screenshot 4...]\n```\n"
                "• Upload as many match screenshots as played (2 for 1v1, 4 or more for 2v2/teams).\n"
                "• The bot reads names & scores using OCR and attributes each game to the respective team.\n"
                "• Aggregates all game scores and announces the winning team.\n"
                "• If confidence is low, react with ✅ to confirm or ❌ to reject."
            ),
            inline=False,
        )

        # Admin Commands
        embed.add_field(
            name="🛡️ Tournament Admin Commands (Admins Only)",
            value=(
                "`/create_tournament name num_teams team_size`\n"
                "↳ Create a new tournament (team count must be a power of 2, e.g., 4, 8, 16, 32, 64).\n\n"
                "`/cancel_tournament`\n"
                "↳ Cancel and delete the active tournament.\n\n"
                "`/start_tournament format`\n"
                "↳ Starts the tournament and seeds the opening round once all teams are filled.\n\n"
                "`/next_round`\n"
                "↳ Advances the bracket to the next round once all current matches finish.\n\n"
                "`/remove_member player`\n"
                "↳ Remove a player from a team roster during setup.\n\n"
                "`/manual_result match_id leg1_home leg1_away leg2_home leg2_away`\n"
                "↳ Manually enter scores for both legs if screenshots fail.\n\n"
                "`/update_result match_id leg home_score away_score`\n"
                "↳ Update/correct a single leg score.\n\n"
                "`/force_winner match_id team`\n"
                "↳ Force a winner by team name or player for walkovers or disqualifications.\n\n"
                "`/remove_result match_id`\n"
                "↳ Clear a match result and reset it to pending.\n\n"
                "`/add_admin user` | `/remove_admin user` | `/list_admins`\n"
                "↳ Appoint or revoke tournament admins without needing Discord role IDs."
            ),
            inline=False,
        )

        embed.set_footer(
            text="FC Mobile Tournament Assistant • Contact an admin if you encounter any issues."
        )

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Register HelpCog."""
    await bot.add_cog(HelpCog(bot))
