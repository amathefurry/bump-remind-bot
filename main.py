import discord
import time
import asyncio
from time import sleep
from discord.ext import commands
from discord import app_commands
from discord.ext import tasks

# REPLACE WITH A PER-SERVER SOLUTION
try:
    with open("token.txt") as f:
        TOKEN = f.readline().strip()
        f.close()
except:
    print(f"ERROR: NO token.txt FILE FOUND. PLEASE CREATE A TXT FILE CALLED 'token.txt' AND ADD YOUR BOT TOKEN INSIDE OF IT")

try:
    with open("role.txt") as f:
        pingrole = f.readline().strip()
        f.close()
except:
    with open("role.txt", "w", encoding="utf-8") as f:
        f.write("")
        f.close()

intents = discord.Intents.all()

class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents, allowed_mentions: discord.AllowedMentions( everyone=True, users=True, roles=True, replied_user=True)):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        
    async def setup_hook(self):
        await self.tree.sync()

    async def on_message(self, message): 
        if message.author == 302050872383242240: 
            if 'Bump done!' in message.embeds[0].description:
                await asyncio.sleep(7199)
                await message.channel.send(f'{pingrole}')
        if message.author == 1155392571569356880: 
            if message.content == "testing":
                await asyncio.sleep(3)
                await message.channel.send(f'{pingrole}')

client = MyClient(intents=intents, allowed_mentions=all)

@client.event
async def on_ready(): 
    await client.tree.sync()
    print(f'Logged in as {client.user}')


@client.tree.command(name="configure", description="configure the bot")
async def configure(interaction: discord.Interaction, role: discord.Role):
    pingrole = role.mention
    await interaction.response.send_message(content=f"{pingrole} from now on you will get pinged whenever bumping is possible. Thank you for bumping our server.")
    with open("role.txt", "w", encoding="utf-8") as f:
            f.write(pingrole)
            f.close()



client.run(TOKEN)