"""FC Mobile Tournament Discord Bot — Entry Point."""

from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import config
from core.database import Database
from utils import embeds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")


class TournamentBot(commands.Bot):
    """Custom bot class with database access."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # Needed to read result messages
        intents.members = True  # Needed to resolve mentions

        super().__init__(
            command_prefix="!",  # Fallback; we use slash commands primarily
            intents=intents,
        )
        self.db = Database(config.DATABASE_PATH)

    async def setup_hook(self) -> None:
        """Called when the bot starts. Load cogs and connect to DB."""
        log.info("Connecting to database...")
        await self.db.connect()

        log.info("Loading extensions...")
        for ext in config.EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info(f"  Loaded {ext}")
            except Exception as e:
                log.error(f"  Failed to load {ext}: {e}")

        # Global sync
        log.info("Syncing slash commands globally...")
        synced = await self.tree.sync()

        # Register global slash command error handler
        async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
            if isinstance(error, app_commands.CheckFailure):
                msg = str(error) or "You do not have tournament admin permissions."
                embed = embeds.error_embed(msg)
            else:
                log.error(f"Command error: {error}", exc_info=error)
                embed = embeds.error_embed(f"An unexpected error occurred: {error}")

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception as e:
                log.error(f"Failed to send error response: {e}")

        self.tree.on_error = on_tree_error

        # Start lightweight HTTP server for Hugging Face Spaces / Uptime monitoring
        port = int(os.getenv("PORT", 7860))
        await self._start_web_server(port)

        # Pre-warm EasyOCR in the background so commands don't block
        asyncio.create_task(self._warmup_ocr())

    async def _warmup_ocr(self) -> None:
        """Pre-load OCR reader in background executor so first command doesn't wait."""
        log.info("Pre-warming EasyOCR reader in background...")
        try:
            from ocr.extractor import get_extractor
            extractor = get_extractor()
            await extractor._ensure_reader()
            log.info("EasyOCR reader pre-warmed successfully!")
        except Exception as e:
            log.warning(f"Could not pre-warm EasyOCR reader: {e}")

    async def _start_web_server(self, port: int) -> None:
        """Start a lightweight aiohttp health check server."""
        from aiohttp import web

        async def health_check(request: web.Request) -> web.Response:
            return web.json_response({
                "status": "online",
                "bot": str(self.user) if self.user else "initializing",
                "guilds": len(self.guilds),
            })

        app = web.Application()
        app.router.add_get("/", health_check)
        app.router.add_get("/health", health_check)

        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, "0.0.0.0", port)
        try:
            await site.start()
            log.info(f"Health check web server running on http://0.0.0.0:{port}")
        except Exception as e:
            log.warning(f"Could not start health check web server on port {port}: {e}")

    async def on_ready(self) -> None:
        log.info(f"Bot is ready! Logged in as {self.user} (ID: {self.user.id})")  # type: ignore[union-attr]
        log.info(f"Connected to {len(self.guilds)} guild(s)")

        # Sync directly to each connected guild for instant availability
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced_guild = await self.tree.sync(guild=guild)
                log.info(f"Synced {len(synced_guild)} slash command(s) instantly to guild '{guild.name}' (ID: {guild.id})")
            except Exception as e:
                log.warning(f"Could not sync instantly to guild '{guild.name}': {e}")

    async def close(self) -> None:
        log.info("Shutting down...")
        if hasattr(self, "_web_runner") and self._web_runner:
            await self._web_runner.cleanup()
        await self.db.close()
        await super().close()


def main() -> None:
    if not config.BOT_TOKEN:
        log.error("BOT_TOKEN not set. Copy .env.example to .env and fill in your token.")
        return

    bot = TournamentBot()
    bot.run(config.BOT_TOKEN)


if __name__ == "__main__":
    main()
