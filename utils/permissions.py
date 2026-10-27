"""Permission check utilities for admin commands."""

from __future__ import annotations

import discord
from discord import app_commands
import config


async def check_is_admin(interaction: discord.Interaction) -> bool:
    """Return True if user is a tournament admin, False otherwise."""
    if not interaction.guild:
        return False

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if member is None and interaction.guild:
        try:
            member = await interaction.guild.fetch_member(interaction.user.id)
        except Exception:
            pass

    if member is None:
        return False

    # Server owner is always admin
    if interaction.guild.owner_id == interaction.user.id:
        return True

    # Server administrator permission
    if member.guild_permissions.administrator:
        return True

    # Check database registered admins
    if hasattr(interaction.client, "db"):
        is_db_admin = await interaction.client.db.is_admin(
            str(interaction.guild_id), str(interaction.user.id)
        )
        if is_db_admin:
            return True

    # Optional fallback: check configured admin role
    admin_role_id = getattr(config, "ADMIN_ROLE_ID", 0)
    if admin_role_id and any(r.id == admin_role_id for r in member.roles):
        return True

    return False


def is_admin():
    """Decorator that checks if the user is authorized as a tournament admin."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if await check_is_admin(interaction):
            return True

        raise app_commands.CheckFailure(
            "You do not have tournament admin permissions."
        )

    return app_commands.check(predicate)

