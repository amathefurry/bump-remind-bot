"""
The bot watches for successful DISBOARD bumps and schedules a reminder
for the configured channel and role after the bump cooldown expires.

Configuration and pending reminders are stored in SQLite, allowing the
bot to recover pending reminders after a restart.

Environment variables:
    BOT_TOKEN: Discord bot token.

Database:
    bump_bot.db
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import override

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Keep logging centralized so the bot produces useful information without
# scattering print() calls throughout the application.
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("BumpBot")

# Load vars from .env into process environment.
_ = load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    logger.critical("BOT_TOKEN was not found in the .env file. Exiting.")
    raise SystemExit(1)

# Discord user ID of the official DISBOARD bot.
DISBOARD_BOT_ID = 302050872383242240

# DISBOARD's bump cooldown is two hours.
BUMP_COOLDOWN_SECONDS = 7200

# SQLite database used for persistent configuration and reminders.
DB_PATH = Path("bump_bot.db")

# How frequently the background worker checks for expired reminders.
REMINDER_CHECK_INTERVAL_SECONDS = 10


def _db_connect() -> sqlite3.Connection:
    """
    Open a connection to the SQLite database.

    A new connection is created for each operation. This is intentional:
    SQLite connections should not be shared between asynchronous tasks or
    threads unless their threading behavior is carefully managed.

    The timeout gives another SQLite operation a little time to release the
    database lock instead of immediately raising "database is locked".
    """
    connection = sqlite3.connect(DB_PATH, timeout=10)

    # Enable Write-Ahead Logging (WAL) for better concurrent read/write performance.
    connection.execute("PRAGMA journal_mode=WAL;")

    # Return rows that can be access by column name as well as position.
    connection.row_factory = sqlite3.Row
    return connection


def _db_init() -> None:
    """
    Create the database schema if it does not already exist.

    guild_configs:
        Stores the notification channel and role configured for each guild.

    reminders:
        Stores pending bump reminders. Only one pending reminder is kept
        for each guild.
    """
    with _db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_configs (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                role_id INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                remind_at REAL NOT NULL
            )
        """)

        # The reminder worker primarily searches by remind_at, so an index
        # makes that query efficient even if the database grows.
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reminders_remind_at
            ON reminders (remind_at)
            """
        )

        conn.commit()

    logger.info("SQLite database initialized: %s", DB_PATH)


def _db_set_config(guild_id: int, channel_id: int, role_id: int) -> None:
    """
    Create or update the bump configuration for a guild.

    The guild_id is the primary key, so configuring a guild again simply
    replaces its previous channel and role.
    """

    with _db_connect() as conn:
        conn.execute(
            """
            INSERT INTO guild_configs (
                guild_id,
                channel_id,
                role_id
            )
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                role_id = excluded.role_id
        """,
            (guild_id, channel_id, role_id),
        )

        conn.commit()


def _db_get_config(guild_id: int) -> tuple[int, int] | None:
    """
    Retrieve a guild's configured channel and role.

    Returns:
        (channel_id, role_id), or None if the guild is not configured.
    """
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT channel_id, role_id FROM guild_configs WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()

        if row is None:
            return None

        return (int(row["channel_id"]), int(row["role_id"]))


def _db_add_reminder(
    guild_id: int, channel_id: int, role_id: int, remind_at: float
) -> None:
    """
    Schedule a new reminder for a guild.

    A guild can only have one pending reminder. If another bump is detected
    before the previous reminder fires, the old reminder is replaced.

    This prevents multiple reminders from accumulating if DISBOARD sends
    duplicate events or a server is bumped again before the previous timer
    expires.
    """

    with _db_connect() as conn:
        # Remove any existing pending reminders for this guild to prevent duplicate spam
        conn.execute(
            """
            DELETE FROM reminders
            WHERE guild_id = ?
            """,
            (guild_id,),
        )

        conn.execute(
            """
            INSERT INTO reminders (
                guild_id,
                channel_id,
                role_id,
                remind_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                guild_id,
                channel_id,
                role_id,
                remind_at,
            ),
        )

        conn.commit()


def _db_get_due_reminders(
    now_timestamp: float,
) -> list[dict[str, int | float]]:
    """
    Retrieve reminders whose scheduled time has arrived.

    IMPORTANT:
    This function does NOT delete the reminders.

    A reminder should only be removed after the bot has successfully sent
    it. Otherwise, a temporary Discord API failure could permanently lose
    the reminder.
    """

    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT id, guild_id, channel_id, role_id, remind_at
            FROM reminders
            WHERE remind_at <= ?
            ORDER BY remind_at ASC
            """,
            (now_timestamp,),
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "guild_id": int(row["guild_id"]),
            "channel_id": int(row["channel_id"]),
            "role_id": int(row["role_id"]),
            "remind_at": float(row["remind_at"]),
        }
        for row in rows
    ]


def _db_delete_reminder(reminder_id: int) -> None:
    """
    Remove a reminder after it has been successfully handled.

    This is deliberately separate from _db_get_due_reminders() so that
    failed Discord requests do not cause reminders to disappear.
    """

    with _db_connect() as conn:
        conn.execute(
            """
            DELETE FROM reminders
            WHERE id = ?
            """,
            (reminder_id,),
        )
        conn.commit()


def _db_get_next_reminder(guild_id: int) -> float | None:
    """
    Return the scheduled time of the next reminder for a guild.

    Returns None when there is no pending reminder.
    """

    with _db_connect() as conn:
        row = conn.execute(
            """
            SELECT remind_at
            FROM reminders
            WHERE guild_id = ?
            ORDER BY remind_at ASC
            LIMIT 1
            """,
            (guild_id,),
        ).fetchone()

    if row is None:
        return None

    return float(row["remind_at"])


class BumpBot(commands.Bot):
    """
    Discord client responsible for detecting bumps and sending reminders.
    """

    def __init__(self):
        # Only enable the intents the bot actually needs.
        #
        # message_content is required because the bot examines the
        # description of DISBOARD's bump message.
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            # This bot primarily uses application commands, but a prefix is
            # still required by commands.Bot.
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions(
                # The bot should never accidentally ping @everyone or users.
                everyone=False,
                users=False,
                roles=True,
                replied_user=False,
            ),
        )

    @override
    async def setup_hook(self) -> None:
        """
        Perform asynchronous initialization before the bot becomes ready.
        """

        # SQLite is synchronous. Run it in a worker thread so database I/O
        # does not block Discord.py's asyncio event loop.
        await asyncio.to_thread(_db_init)

        # Start the persistent reminder worker.
        _ = self.check_reminders.start()

        # Synchronize slash commands once during startup.
        _ = await self.tree.sync()

        logger.info("Application command tree synchronized successfully.")

    async def on_ready(self) -> None:
        """Log successful Discord authentication."""
        if self.user is None:
            return

        logger.info(
            "Logged in as %s (ID: %s)",
            self.user,
            self.user.id,
        )

    async def on_message(self, message: discord.Message) -> None:
        """
        Process incoming messages and detect successful DISBOARD bumps.

        The handler deliberately does not use asyncio.sleep(). The reminder
        is stored in SQLite instead, allowing the bot to continue processing
        messages normally and recover the reminder after a restart.
        """

        # Bump reminders only make sense inside guilds.
        if message.guild is None:
            return

        # Ignore messages generated by this bot.
        if self.user is not None and message.author.id == self.user.id:
            return

        # We only care about messages sent by DISBOARD.
        if message.author.id != DISBOARD_BOT_ID:
            return

        # A successful bump is reported through an embed.
        if not message.embeds:
            return

        embed = message.embeds[0]
        description = embed.description or ""

        # Ignore other DISBOARD messages.
        if "Bump done!" not in description:
            return

        guild_id = message.guild.id

        # Look up this server's configured notification settings.
        config = await asyncio.to_thread(
            _db_get_config,
            guild_id,
        )

        if config is None:
            logger.info(
                "Successful bump detected in guild %s, "
                "but the guild has no reminder configuration.",
                guild_id,
            )
            return

        channel_id, role_id = config

        # Use wall-clock time because the timestamp is persisted in SQLite
        # and must remain meaningful after the process exits and restarts.
        remind_at = time.time() + BUMP_COOLDOWN_SECONDS

        await asyncio.to_thread(
            _db_add_reminder,
            guild_id,
            channel_id,
            role_id,
            remind_at,
        )

        # Reacting to the bump gives administrators a visual confirmation
        # that the bot successfully detected and scheduled the reminder.
        try:
            await message.add_reaction("⏰")
        except discord.HTTPException:
            # Failing to add the reaction should not affect the reminder.
            pass

        logger.info(
            "Scheduled bump reminder: guild=%s channel=%s role=%s at=%s",
            guild_id,
            channel_id,
            role_id,
            int(remind_at),
        )

    @tasks.loop(seconds=REMINDER_CHECK_INTERVAL_SECONDS)
    async def check_reminders(self) -> None:
        """
        Find and process reminders whose scheduled time has arrived.

        The worker runs every ten seconds. This means the reminder can be
        delayed by up to roughly ten seconds, which is negligible compared
        with a two-hour bump cooldown.
        """
        now = time.time()
        reminders = await asyncio.to_thread(
            _db_get_due_reminders,
            now,
        )

        for reminder in reminders:
            await self._process_reminder(reminder)

    async def _process_reminder(
        self,
        reminder: dict[str, int | float],
    ) -> None:
        """
        Deliver one reminder.

        The reminder remains in SQLite if a temporary Discord error occurs.
        This allows a later worker iteration to retry it.
        """
        reminder_id = int(reminder["id"])
        guild_id = int(reminder["guild_id"])
        channel_id = int(reminder["channel_id"])
        role_id = int(reminder["role_id"])

        guild = self.get_guild(guild_id)
        if guild is None:
            try:
                guild = await self.fetch_guild(guild_id)
            except discord.NotFound:
                # The bot is no longer in this guild, so this reminder can
                # never be delivered.
                logger.warning(
                    "Guild %s no longer exists or is inaccessible. "
                    "Discarding reminder %s.",
                    guild_id,
                    reminder_id,
                )

                await asyncio.to_thread(
                    _db_delete_reminder,
                    reminder_id,
                )

                return

            except discord.HTTPException as error:
                # Temporary API failure. Keep the reminder for a retry.
                logger.error(
                    "Failed to fetch guild %s: %s",
                    guild_id,
                    error,
                )
                return

        role = guild.get_role(role_id)

        if role is None:
            # A deleted role can never be pinged, so retrying this reminder
            # forever would only fill the logs.
            logger.warning(
                "Configured role %s no longer exists in guild %s. "
                "Discarding reminder %s.",
                role_id,
                guild_id,
                reminder_id,
            )

            await asyncio.to_thread(
                _db_delete_reminder,
                reminder_id,
            )

            return

        channel = self.get_channel(channel_id)

        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.NotFound:
                # The channel has been deleted. There is nowhere to send
                # this reminder.
                logger.warning(
                    "Channel %s no longer exists in guild %s. Discarding reminder %s.",
                    channel_id,
                    guild_id,
                    reminder_id,
                )

                await asyncio.to_thread(
                    _db_delete_reminder,
                    reminder_id,
                )

                return

            except discord.Forbidden:
                # The bot may temporarily lack access. Keep the reminder
                # and try again later.

                logger.warning(
                    "Access denied when fetching channel %s in guild %s. Will retry.",
                    channel_id,
                    guild_id,
                )

                return

            except discord.HTTPException as error:
                logger.error(
                    "Failed to fetch channel %s: %s",
                    channel_id,
                    error,
                )

                return

        # A Discord channel must be messageable for .send() to be valid.
        if not isinstance(channel, discord.abc.Messageable):
            logger.error(
                "Configured channel %s in guild %s is not messageable.",
                channel_id,
                guild_id,
            )

            await asyncio.to_thread(
                _db_delete_reminder,
                reminder_id,
            )

            return

        # Send the reminder

        try:
            await channel.send(
                content=(
                    f"{role.mention} 🚀 "
                    "**It's time to bump the server again!** "
                    "Use `/bump`."
                ),
                allowed_mentions=discord.AllowedMentions(
                    roles=True,
                ),
            )

        except discord.Forbidden:
            # Keep the reminder. The administrator may fix the bot's channel
            # permissions later, at which point the worker can retry.
            logger.error(
                "Missing permission to send messages in channel %s "
                "of guild %s. Will retry.",
                channel_id,
                guild_id,
            )
            return

        except discord.HTTPException as error:
            # Keep the reminder for temporary Discord API failures.
            logger.error(
                "Failed to send reminder %s: %s",
                reminder_id,
                error,
            )
            return

        # Only remove the reminder after Discord accepted the message.
        await asyncio.to_thread(
            _db_delete_reminder,
            reminder_id,
        )
        logger.info(
            "Sent bump reminder: guild=%s channel=%s role=%s",
            guild_id,
            channel_id,
            role_id,
        )

    @check_reminders.before_loop
    async def before_check_reminders(self) -> None:
        """Wait until Discord is ready before starting the worker."""
        await self.wait_until_ready()

    async def close(self) -> None:
        """
        Shut down the bot and its background worker cleanly.
        """
        if self.check_reminders.is_running():
            self.check_reminders.cancel()

        await super().close()


bot = BumpBot()


# /configure
@bot.tree.command(
    name="configure",
    description="Configure the role and channel used for bump reminders.",
)
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    role="The role to ping when bumping is available.",
    channel="The channel where the reminder should be sent.",
)
async def configure(
    interaction: discord.Interaction,
    role: discord.Role,
    channel: discord.TextChannel | None = None,
) -> None:
    """
    Configure bump reminders for the current guild.

    The command requires the Manage Server permission. If no channel is supplied,
    the command's current text channel is used.
    """

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message(
            "This command must be used inside a server.",
            ephemeral=True,
        )
        return

    # When the channel argument is omitted, use the channel in which the
    # command was executed.
    if channel is None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Please specify a text channel.",
                ephemeral=True,
            )
            return

        target_channel = interaction.channel
    else:
        target_channel = channel

    # Store only Discord IDs in the database. Mentions are generated when
    # needed rather than being persisted as text.
    await asyncio.to_thread(
        _db_set_config,
        guild_id,
        target_channel.id,
        role.id,
    )

    await interaction.response.send_message(
        content=(
            "**Configuration saved!**\n"
            f"• **Ping role:** {role.mention}\n"
            f"• **Reminder channel:** {target_channel.mention}"
        ),
        # The role mention above is displayed as text but must not actually
        # ping the role in the configuration confirmation.
        allowed_mentions=discord.AllowedMentions(roles=False),
        ephemeral=True,
    )

    logger.info(
        "Updated configuration: guild=%s channel=%s role=%s",
        guild_id,
        target_channel.id,
        role.id,
    )


# /bumpstatus
@bot.tree.command(
    name="bumpstatus",
    description="Check when the next bump reminder is scheduled.",
)
async def bumpstatus(
    interaction: discord.Interaction,
) -> None:
    """
    Show the remaining time until the next scheduled bump reminder.
    """

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message(
            "This command must be used inside a server.",
            ephemeral=True,
        )
        return

    remind_at = await asyncio.to_thread(_db_get_next_reminder, guild_id)

    if remind_at is None:
        await interaction.response.send_message(
            "There is no active bump cooldown. Bump the server using DISBOARD!",
            ephemeral=True,
        )
        return

    remaining_seconds = int(remind_at - time.time())
    if remaining_seconds <= 0:
        _ = await interaction.response.send_message(
            "The bump timer has expired! You can bump right now.", ephemeral=True
        )

    # Discord renders this as a localized relative timestamp, e.g.
    # "in 1 hour" or "in 42 minutes".
    discord_timestamp = f"<t:{int(remind_at)}:R>"
    await interaction.response.send_message(
        f"The next bump reminder will be sent {discord_timestamp}.",
        ephemeral=True,
    )


if __name__ == "__main__":
    bot.run(TOKEN)
