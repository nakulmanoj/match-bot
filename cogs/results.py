"""Result submission cog — listens for match result messages with screenshots or slash commands.

Accepts match result screenshots (2, 4, 6, 8+), runs OCR extraction, matches
players to their registered FC Mobile IGNs, and records aggregate scores.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Callable, Awaitable, Any

import discord
from discord import app_commands
from discord.ext import commands

from core.models import TournamentStatus, TournamentFormat, MatchStatus, RoundStatus
from utils import embeds

logger = logging.getLogger(__name__)

# Reaction emojis for confirmation flow
CONFIRM_EMOJI = "✅"
REJECT_EMOJI = "❌"


class ResultsCog(commands.Cog):
    """Cog that processes match results from messages or slash commands."""

    def __init__(self, bot: "TournamentBot") -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_image_attachment(attachment: discord.Attachment) -> bool:
        """Check whether an attachment is an image based on content type or filename."""
        if attachment.content_type and attachment.content_type.startswith("image/"):
            return True
        filename = (attachment.filename or "").lower()
        return filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"))

    @staticmethod
    def _has_vs_between_mentions(content: str) -> bool:
        """Return True if the message text contains 'vs' (case-insensitive)."""
        return "vs" in content.lower()

    @staticmethod
    def _determine_winner(
        home_agg: int,
        away_agg: int,
        home_away_goals: int,
        away_home_goals: int,
        home_team_id: int,
        away_team_id: int,
    ) -> Optional[int]:
        """Determine the winner by aggregate, falling back to away-goals rule."""
        if home_agg > away_agg:
            return home_team_id
        if away_agg > home_agg:
            return away_team_id

        # Tied on aggregate — apply away-goals rule.
        if away_home_goals > home_away_goals:
            return away_team_id
        if home_away_goals > away_home_goals:
            return home_team_id

        return None

    # ------------------------------------------------------------------
    # Core Submission Processor
    # ------------------------------------------------------------------

    async def _handle_submission(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        author: discord.User | discord.Member,
        players: list[discord.Member],
        image_attachments: list[discord.Attachment],
        send_reply: Callable[..., Awaitable[Any]],
    ) -> None:
        """Process a match result submission from either a chat message or slash command."""
        if not image_attachments:
            await send_reply(
                embed=embeds.error_embed("Please attach at least one match result screenshot.")
            )
            return

        guild_id = str(guild.id)

        # Get active tournament
        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            await send_reply(
                embed=embeds.error_embed(
                    "No active tournament found in this server. Use `/create_tournament` to create one."
                )
            )
            return

        if tournament.status == TournamentStatus.SETUP:
            await send_reply(
                embed=embeds.error_embed(
                    "The tournament is still in **Setup** phase.\n"
                    "All teams must be filled and an admin must run `/start_tournament` before submitting match scores."
                )
            )
            return

        if tournament.status != TournamentStatus.IN_PROGRESS:
            await send_reply(
                embed=embeds.error_embed(
                    f"Tournament is currently in **{tournament.status.value}** status and not accepting match results."
                )
            )
            return

        # Resolve mentioned players and determine the two distinct competing teams
        team_players_map: dict[int, list[discord.Member]] = {}
        unregistered: list[str] = []
        for member in players:
            p = await self.bot.db.get_player_by_discord_id(tournament.id, str(member.id))
            if p:
                team_players_map.setdefault(p.team_id, []).append(member)
            else:
                unregistered.append(member.mention)

        if unregistered:
            await send_reply(
                embed=embeds.error_embed(
                    f"The following player(s) are not registered in the active tournament: {', '.join(unregistered)}\n"
                    f"Use `/add_member` to register them first."
                )
            )
            return

        # Must map to exactly 2 distinct teams
        distinct_team_ids = list(team_players_map.keys())
        if len(distinct_team_ids) != 2:
            if len(distinct_team_ids) == 1:
                await send_reply(
                    embed=embeds.error_embed(
                        "All mentioned players belong to the **same team**! Mention players from the two competing teams."
                    )
                )
            else:
                await send_reply(
                    embed=embeds.error_embed(
                        f"Mentions span across **{len(distinct_team_ids)}** teams. Match submissions must be between exactly **2** competing teams."
                    )
                )
            return

        team_a_id, team_b_id = distinct_team_ids[0], distinct_team_ids[1]

        # Find the scheduled match between these two teams
        match = await self.bot.db.find_match_between_teams(
            tournament.id, team_a_id, team_b_id
        )
        if not match:
            await send_reply(
                embed=embeds.error_embed(
                    "No scheduled match found between these two teams in the current round. "
                    "Use `/view_bracket` to check current fixtures."
                )
            )
            return

        if match.status == MatchStatus.COMPLETED:
            await send_reply(
                embed=embeds.error_embed(
                    "This match has already been completed. Use `/update_result` to modify."
                )
            )
            return

        # Download all attachments
        attachments_bytes: list[bytes] = []
        try:
            for att in image_attachments:
                data = await att.read()
                attachments_bytes.append(data)
        except discord.HTTPException:
            await send_reply(
                embed=embeds.error_embed("Failed to download one or more screenshot attachments.")
            )
            return

        # OCR extraction
        from ocr.extractor import get_extractor
        from ocr.matcher import match_players_in_match
        import config

        extractor = get_extractor()

        ocr_results = []
        try:
            for att_bytes in attachments_bytes:
                res = await extractor.extract(att_bytes)
                ocr_results.append(res)
        except Exception:
            logger.exception("OCR extraction failed for match %s", match.id)
            await send_reply(
                embed=embeds.error_embed(
                    "OCR extraction failed. Please use `/manual_result` to enter scores manually."
                )
            )
            return

        # Validate that all screenshots could be parsed
        failed_indices = [i + 1 for i, res in enumerate(ocr_results) if res is None]
        if failed_indices:
            legs_str = ", ".join(f"Screenshot #{i}" for i in failed_indices)
            await send_reply(
                embed=embeds.error_embed(
                    f"Could not extract scores from {legs_str}. "
                    "Please use `/manual_result` or re-upload clearer screenshots."
                )
            )
            return

        # Get all registered players for each team
        home_players = await self.bot.db.get_players(match.home_team_id)
        away_players = await self.bot.db.get_players(match.away_team_id)

        # Determine home/away scores for each screenshot directly using fuzzy matching
        from rapidfuzz import fuzz
        from ocr.matcher import _normalize_name

        def _calc_similarity(ocr_name: str, ign: str) -> float:
            score_raw = fuzz.WRatio(ocr_name.strip(), ign.strip())
            score_norm = fuzz.WRatio(_normalize_name(ocr_name), _normalize_name(ign))
            return max(score_raw, score_norm)

        def get_leg_scores(ocr_res):
            score_left_to_home = max([_calc_similarity(ocr_res.left_name, p.ign) for p in home_players], default=0.0)
            score_left_to_away = max([_calc_similarity(ocr_res.left_name, p.ign) for p in away_players], default=0.0)
            score_right_to_home = max([_calc_similarity(ocr_res.right_name, p.ign) for p in home_players], default=0.0)
            score_right_to_away = max([_calc_similarity(ocr_res.right_name, p.ign) for p in away_players], default=0.0)

            direct_score = score_left_to_home + score_right_to_away
            swapped_score = score_left_to_away + score_right_to_home

            if direct_score >= swapped_score:
                return ocr_res.left_score, ocr_res.right_score
            else:
                return ocr_res.right_score, ocr_res.left_score

        leg_scores_list: list[tuple[int, int]] = []
        for i, ocr_res in enumerate(ocr_results, start=1):
            h_score, a_score = get_leg_scores(ocr_res)
            leg_scores_list.append((h_score, a_score))
            await self.bot.db.update_leg_score(match.id, i, h_score, a_score)

        # Calculate totals
        home_agg = sum(h for h, _ in leg_scores_list)
        away_agg = sum(a for _, a in leg_scores_list)

        # Away goals tiebreaker (for standard 2-leg matches)
        if len(leg_scores_list) == 2:
            home_away_goals = leg_scores_list[1][0]
            away_home_goals = leg_scores_list[0][1]
        else:
            home_away_goals = 0
            away_home_goals = 0

        winner_team_id = self._determine_winner(
            home_agg=home_agg,
            away_agg=away_agg,
            home_away_goals=home_away_goals,
            away_home_goals=away_home_goals,
            home_team_id=match.home_team_id,
            away_team_id=match.away_team_id,
        )

        all_teams = await self.bot.db.get_teams(tournament.id)
        teams_dict = {t.id: t for t in all_teams}

        home_players_db = await self.bot.db.get_players_by_team(match.home_team_id)
        away_players_db = await self.bot.db.get_players_by_team(match.away_team_id)

        def _get_display(team_id: int, players_list: list) -> str:
            if players_list:
                return ", ".join(f"<@{p.discord_id}>" for p in players_list)
            t = teams_dict.get(team_id)
            return t.name if t else "TBD"

        home_display = _get_display(match.home_team_id, home_players_db)
        away_display = _get_display(match.away_team_id, away_players_db)

        if winner_team_id is not None:
            await self.bot.db.set_match_winner(match.id, winner_team_id)
            await self.bot.db.update_match_status(match.id, MatchStatus.COMPLETED)

            if winner_team_id == match.home_team_id:
                winner_display = home_display
            elif winner_team_id == match.away_team_id:
                winner_display = away_display
            else:
                t_win = teams_dict.get(winner_team_id)
                winner_display = t_win.name if t_win else "TBD"

            result_embed = discord.Embed(
                title="📊 Match Result Recorded",
                color=discord.Color.green(),
            )
            for i, (h_s, a_s) in enumerate(leg_scores_list, start=1):
                result_embed.add_field(
                    name=f"Game / Leg {i}",
                    value=f"{home_display} **{h_s}** - **{a_s}** {away_display}",
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

            await send_reply(embed=result_embed)
        else:
            # Draw
            await self.bot.db.update_match_status(match.id, MatchStatus.COMPLETED)

            result_embed = discord.Embed(
                title="⚖️ Match Ended in a Draw",
                description=(
                    f"Aggregate score is level (**{home_agg}** - **{away_agg}**).\n"
                    f"An admin must resolve this using `/force_winner match_id:{match.id} team:<name>`."
                ),
                color=discord.Color.orange(),
            )
            for i, (h_s, a_s) in enumerate(leg_scores_list, start=1):
                result_embed.add_field(
                    name=f"Game / Leg {i}",
                    value=f"{home_display} **{h_s}** - **{a_s}** {away_display}",
                    inline=False,
                )
            await send_reply(embed=result_embed)

    # ------------------------------------------------------------------
    # Message Listener (@Player1 vs @Player2 + images)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Process incoming messages that look like result submissions."""
        if message.author.bot or message.guild is None:
            return

        has_images = any(self._is_image_attachment(a) for a in message.attachments)
        has_vs = self._has_vs_between_mentions(message.content)

        if has_images or has_vs:
            logger.info(
                f"[Results] Message in '{message.guild.name}' (#{message.channel}) from {message.author}: "
                f"content='{message.content}', mentions={[m.name for m in message.mentions]}, "
                f"roles={[r.name for r in message.role_mentions]}, attachments={len(message.attachments)}"
            )

        if not has_vs or not has_images:
            return

        # Resolve mentions: support user mentions, role mentions, or single opponent tag
        mentions_list: list[discord.Member] = list(message.mentions)
        for role in message.role_mentions:
            for member in role.members:
                if member not in mentions_list:
                    mentions_list.append(member)

        if len(mentions_list) == 1 and message.author not in mentions_list:
            mentions_list.append(message.author)

        if len(mentions_list) < 2:
            await message.reply(
                embed=embeds.error_embed(
                    "To submit match results, please mention both competing players:\n"
                    "Example: `@Player1 vs @Player2`\n"
                    "*(Make sure to mention actual users, not roles!)*"
                )
            )
            return

        image_attachments = [
            a for a in message.attachments if self._is_image_attachment(a)
        ]

        await self._handle_submission(
            guild=message.guild,
            channel=message.channel,
            author=message.author,
            players=mentions_list,
            image_attachments=image_attachments,
            send_reply=message.reply,
        )

    # ------------------------------------------------------------------
    # Slash Command: /submit_result
    # ------------------------------------------------------------------

    @app_commands.command(
        name="submit_result",
        description="Submit match screenshots for automatic OCR score calculation.",
    )
    @app_commands.describe(
        opponent="The opponent player from the other team",
        screenshot1="Screenshot of match 1",
        screenshot2="Screenshot of match 2",
        screenshot3="Screenshot of match 3 (optional)",
        screenshot4="Screenshot of match 4 (optional)",
    )
    async def submit_result(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        screenshot1: discord.Attachment,
        screenshot2: Optional[discord.Attachment] = None,
        screenshot3: Optional[discord.Attachment] = None,
        screenshot4: Optional[discord.Attachment] = None,
    ) -> None:
        """Slash command alternative to submit match results."""
        if not interaction.guild:
            await interaction.response.send_message(
                embed=embeds.error_embed("This command can only be used in a server."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        attachments = [att for att in [screenshot1, screenshot2, screenshot3, screenshot4] if att is not None]

        author_member = (
            interaction.user if isinstance(interaction.user, discord.Member)
            else interaction.guild.get_member(interaction.user.id)
        )
        if not author_member:
            author_member = interaction.user

        await self._handle_submission(
            guild=interaction.guild,
            channel=interaction.channel,
            author=author_member,
            players=[author_member, opponent],
            image_attachments=attachments,
            send_reply=interaction.followup.send,
        )


async def setup(bot: "TournamentBot") -> None:
    """Register the ResultsCog with the bot."""
    await bot.add_cog(ResultsCog(bot))
