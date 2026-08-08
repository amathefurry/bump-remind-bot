import discord
import asyncio
from discord import app_commands


try:
    with open("token.txt", encoding="utf-8") as f: 
        TOKEN = f.readline().strip()
except FileNotFoundError:
    print(
        "ERROR: NO token.txt FILE FOUND. "
        "PLEASE CREATE A TXT FILE CALLED 'token.txt' AND ADD YOUR BOT TOKEN INSIDE OF IT"
    )
    raise SystemExit(1)


try:
    with open("role.txt", encoding="utf-8") as f: # REPLACE WITH A PER-SERVER SOLUTION
        pingrole = f.readline().strip()
except FileNotFoundError:
    pingrole = ""

    with open("role.txt", "w", encoding="utf-8") as f:
        f.write("")


intents = discord.Intents.all()


class MyClient(discord.Client):
    def __init__(
        self,
        *,
        intents: discord.Intents,
        allowed_mentions: discord.AllowedMentions,
    ):
        super().__init__(
            intents=intents,
            allowed_mentions=allowed_mentions,
        )

        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

    async def on_message(self, message):
        global pingrole

        if message.author.id == 302050872383242240:
            if (
                message.embeds
                and message.embeds[0].description
                and "Bump done!" in message.embeds[0].description
            ):
                await asyncio.sleep(7199)

                await message.channel.send(
                    pingrole,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )

        if message.author.id == 1155392571569356880:
            if message.content == "testing":
                await asyncio.sleep(3)

                await message.channel.send(
                    pingrole,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )


allowed_mentions = discord.AllowedMentions(
    everyone=True,
    users=True,
    roles=True,
    replied_user=True,
)


client = MyClient(
    intents=intents,
    allowed_mentions=allowed_mentions,
)


@client.event
async def on_ready():
    await client.tree.sync()
    print(f"Logged in as {client.user}")


@client.tree.command(name="configure", description="configure the bot")
async def configure(interaction: discord.Interaction, role: discord.Role):
    global pingrole

    pingrole = role.mention

    with open("role.txt", "w", encoding="utf-8") as f:
        f.write(pingrole)

    await interaction.response.send_message(
        content=(
            f"{pingrole} from now on you will get pinged whenever "
            "bumping is possible. Thank you for bumping our server."
        ),
        allowed_mentions=discord.AllowedMentions(roles=True),
    )


client.run(TOKEN)