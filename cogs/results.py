"""Result submission cog — listens for match result messages with screenshots.

Expects messages containing two @mentions separated by 'vs' and exactly two
image attachments (leg 1 and leg 2 screenshots). Performs OCR extraction,
matches players, and records scores.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

from core.models import TournamentStatus, TournamentFormat, MatchStatus, RoundStatus
from utils import embeds

logger = logging.getLogger(__name__)

# Reaction emojis for confirmation flow
CONFIRM_EMOJI = "✅"
REJECT_EMOJI = "❌"


class ResultsCog(commands.Cog):
    """Cog that processes match results from messages with screenshots."""

    def __init__(self, bot: "TournamentBot") -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_image_attachment(attachment: discord.Attachment) -> bool:
        """Check whether an attachment is an image based on content type."""
        return (
            attachment.content_type is not None
            and attachment.content_type.startswith("image/")
        )

    @staticmethod
    def _has_vs_between_mentions(content: str) -> bool:
        """Return True if the message text contains 'vs' (case-insensitive)."""
        return "vs" in content.lower()

    def _determine_scores(
        self,
        ocr_result: dict,
        left_player_team_id: int,
        home_team_id: int,
    ) -> tuple[int, int]:
        """Map OCR left/right scores to home/away based on team membership.

        Args:
            ocr_result: Dict with ``left_score`` and ``right_score`` keys.
            left_player_team_id: The team ID of the player on the left side.
            home_team_id: The team ID designated as the home team.

        Returns:
            A ``(home_score, away_score)`` tuple.
        """
        left_score: int = ocr_result["left_score"]
        right_score: int = ocr_result["right_score"]

        if left_player_team_id == home_team_id:
            return left_score, right_score
        return right_score, left_score

    @staticmethod
    def _determine_winner(
        home_agg: int,
        away_agg: int,
        home_away_goals: int,
        away_home_goals: int,
        home_team_id: int,
        away_team_id: int,
    ) -> Optional[int]:
        """Determine the winner by aggregate, falling back to away-goals rule.

        Returns:
            The winning team's ID, or ``None`` if the tie cannot be broken.
        """
        if home_agg > away_agg:
            return home_team_id
        if away_agg > home_agg:
            return away_team_id

        # Tied on aggregate — apply away-goals rule.
        # home_away_goals = goals scored by the home team in the away leg
        # away_home_goals = goals scored by the away team in the home leg
        if away_home_goals > home_away_goals:
            return away_team_id
        if home_away_goals > away_home_goals:
            return home_team_id

        # Still level — cannot decide automatically
        return None

    # ------------------------------------------------------------------
    # Listener
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Process incoming messages that look like result submissions."""
        # Ignore bots and DMs
        if message.author.bot or message.guild is None:
            return

        # Must have at least 2 mentions (can be 2, 3, 4, etc. across the two teams)
        # and at least 1 image attachment
        if len(message.mentions) < 2:
            return

        image_attachments = [
            a for a in message.attachments if self._is_image_attachment(a)
        ]
        if not image_attachments:
            return

        # Must contain 'vs' between mentions
        if not self._has_vs_between_mentions(message.content):
            return

        guild_id = message.guild.id

        # Get active tournament
        tournament = await self.bot.db.get_active_tournament(guild_id)
        if not tournament:
            return

        if tournament.status != TournamentStatus.IN_PROGRESS:
            return

        # Resolve mentioned players and determine the two distinct competing teams
        team_players_map: dict[int, list[discord.Member]] = {}
        for member in message.mentions:
            p = await self.bot.db.get_player_by_discord_id(tournament.id, str(member.id))
            if p:
                team_players_map.setdefault(p.team_id, []).append(member)

        # Must map to exactly 2 distinct teams
        distinct_team_ids = list(team_players_map.keys())
        if len(distinct_team_ids) != 2:
            # Check if user mentioned 2 players where one was not found
            if len(message.mentions) == 2 and len(distinct_team_ids) < 2:
                unregistered = [
                    m.mention
                    for m in message.mentions
                    if not await self.bot.db.get_player_by_discord_id(tournament.id, str(m.id))
                ]
                if unregistered:
                    await message.reply(
                        embed=embeds.error_embed(
                            f"{', '.join(unregistered)} is not registered in the tournament."
                        )
                    )
            return

        team_a_id, team_b_id = distinct_team_ids[0], distinct_team_ids[1]

        # Find the scheduled match between these two teams
        match = await self.bot.db.find_match_between_teams(
            tournament.id, team_a_id, team_b_id
        )
        if not match:
            await message.reply(
                embed=embeds.error_embed(
                    "No scheduled match found between these two teams."
                )
            )
            return

        if match.status == MatchStatus.COMPLETED:
            await message.reply(
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
            await message.reply(
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
            async with message.channel.typing():
                for att_bytes in attachments_bytes:
                    res = await extractor.extract(att_bytes)
                    ocr_results.append(res)
        except Exception:
            logger.exception("OCR extraction failed for match %s", match.id)
            await message.reply(
                embed=embeds.error_embed(
                    "OCR extraction failed. Please use `/manual_result` to enter "
                    "scores manually."
                )
            )
            return

        # Validate that all screenshots could be parsed
        failed_indices = [i + 1 for i, res in enumerate(ocr_results) if res is None]
        if failed_indices:
            legs_str = ", ".join(f"Screenshot #{i}" for i in failed_indices)
            await message.reply(
                embed=embeds.error_embed(
                    f"Could not extract scores from {legs_str}. "
                    "Please use `/manual_result` or re-upload clearer screenshots."
                )
            )
            return

        # Get all registered players for each team
        home_players = await self.bot.db.get_players(match.home_team_id)
        away_players = await self.bot.db.get_players(match.away_team_id)

        # Match OCR names to players for each screenshot
        all_match_scores: list[float] = []
        for i, ocr_res in enumerate(ocr_results):
            try:
                _hp, _ap, h_score, a_score = match_players_in_match(
                    ocr_res.left_name, ocr_res.right_name, home_players, away_players
                )
                all_match_scores.extend([h_score, a_score])
            except Exception:
                logger.exception("Player matching failed for match %s leg %d", match.id, i + 1)
                await message.reply(
                    embed=embeds.error_embed(
                        "Failed to match OCR names to registered players. "
                        "Please use `/manual_result` instead."
                    )
                )
                return

        # Check confidence (fuzzy match scores 0-100 and OCR confidence 0-1)
        min_match_score = min(all_match_scores) if all_match_scores else 0.0
        min_ocr_conf = min(res.confidence for res in ocr_results) * 100.0

        needs_confirmation = (
            min_match_score < config.FUZZY_MATCH_THRESHOLD
            or min_ocr_conf < config.OCR_CONFIDENCE_THRESHOLD
        )

        if needs_confirmation:
            # Post OCR preview and ask for confirmation
            preview_embed = discord.Embed(
                title=f"🔍 OCR Result Preview ({len(ocr_results)} Screenshot{'s' if len(ocr_results) > 1 else ''}) — Needs Confirmation",
                description="Please verify if the extracted details are correct.",
                color=discord.Color.orange(),
            )
            for i, res in enumerate(ocr_results, start=1):
                preview_embed.add_field(
                    name=f"Game / Leg {i}",
                    value=f"Left: **{res.left_name}** ({res.left_score}) - ({res.right_score}) **{res.right_name}**",
                    inline=False,
                )
            preview_embed.set_footer(
                text=f"Match Score: {min_match_score:.0f}% | OCR: {min_ocr_conf:.0f}% — "
                f"React {CONFIRM_EMOJI} to accept or {REJECT_EMOJI} to reject."
            )

            preview_msg = await message.reply(embed=preview_embed)
            await preview_msg.add_reaction(CONFIRM_EMOJI)
            await preview_msg.add_reaction(REJECT_EMOJI)

            def reaction_check(
                reaction: discord.Reaction, user: discord.User
            ) -> bool:
                return (
                    user.id == message.author.id
                    and reaction.message.id == preview_msg.id
                    and str(reaction.emoji) in (CONFIRM_EMOJI, REJECT_EMOJI)
                )

            try:
                reaction, _ = await self.bot.wait_for(
                    "reaction_add", check=reaction_check, timeout=60.0
                )
            except asyncio.TimeoutError:
                await preview_msg.reply(
                    embed=embeds.error_embed(
                        "Confirmation timed out. Please re-submit or use `/manual_result`."
                    )
                )
                return

            if str(reaction.emoji) == REJECT_EMOJI:
                await preview_msg.reply(
                    embed=embeds.error_embed(
                        "Result rejected. Please re-submit with clearer screenshots or use `/manual_result`."
                    )
                )
                return

        # Determine home/away scores for each screenshot:
        # If OCR left name matches a home team player better, left_score is home_score
        from rapidfuzz import fuzz
        from ocr.matcher import _normalize_name

        def _calc_similarity(ocr_name: str, ign: str) -> float:
            score_raw = fuzz.WRatio(ocr_name.strip(), ign.strip())
            score_norm = fuzz.WRatio(_normalize_name(ocr_name), _normalize_name(ign))
            return max(score_raw, score_norm)

        def get_leg_scores(ocr_res):
            score_left_to_home = max([_calc_similarity(ocr_res.left_name, p.ign) for p in home_players], default=0.0)
            score_left_to_away = max([_calc_similarity(ocr_res.left_name, p.ign) for p in away_players], default=0.0)
            if score_left_to_home >= score_left_to_away:
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

        home_players = await self.bot.db.get_players_by_team(match.home_team_id)
        away_players = await self.bot.db.get_players_by_team(match.away_team_id)

        def _get_display(team_id: int, players: list) -> str:
            if players:
                return ", ".join(f"<@{p.discord_id}>" for p in players)
            t = teams_dict.get(team_id)
            return t.name if t else "TBD"

        home_display = _get_display(match.home_team_id, home_players)
        away_display = _get_display(match.away_team_id, away_players)

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

            await message.reply(embed=result_embed)
        else:
            # Draw — cannot break tie automatically
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
            await message.reply(embed=result_embed)


async def setup(bot: "TournamentBot") -> None:
    """Register the ResultsCog with the bot."""
    await bot.add_cog(ResultsCog(bot))
