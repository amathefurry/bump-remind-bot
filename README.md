# DISBOARD Bump Reminder Bot

A lightweight, reliable Discord bot built in Python that tracks [DISBOARD](https://disboard.org/) bumps and automatically reminds your server when the 2-hour cooldown has expired. 

Unlike simple timer bots, this bot uses a lightweight SQLite database to ensure reminders are never lost—even if the bot restarts or encounters temporary network errors.

## Features
- Persistent reminders: Survives bot restarts and crashes.
- Slash commands: Fully supports modern Discord application commands (`/configure`, `/bumpstatus`).
- Smart error handling: Automatically handles deleted channels, deleted roles, and missing permissions.
- Zero spam: Overwrites pending reminders if a server is bumped early, preventing duplicate pings.

## Prerequisites
- Python 3.10 or higher
- A Discord bot token (Create one at the [Discord Developer Portal](https://discord.com/developers/applications))

## Installation and setup

1. Clone the repository
   ```bash
   git clone https://github.com/amathefurry/bump-remind-bot.git
   cd bump-remind-bot
   ```
2. Create a virtual environment (recommended)
   ```bash
   python -m venv .venv
   source .venv/bin/activate # On Windows: .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure the environment

   Copy the example environment file and add your token:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and paste your Discord bot token.
5. Run the bot:
   ```
   python main.py
   ```

## Usage

Once the bot is invited to your server, an administrator must configure it:
1. Type `/configure` in your server.
2. Select the role you want the bot to ping (e.g., `@Bumpers`)
3. (Optional) Select the channel where the reminder should be sent. If left blank, it uses the channel you ran the command in.
4. When someone types `/bump` and the official DISBOARD bot confirms it, this bot will react with a ⏰ and schedule the ping!

Use `/bumpstatus` at any time to check how much time is left on the cooldown.

## License

This project is licensed under the [Zero-Clause BSD License](https://opensource.org/license/0bsd). Essentially, you can do whatever you want with this.
