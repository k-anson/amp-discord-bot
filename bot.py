"""
Discord bot that posts a live-updating AMP server status message.

Format: a single Discord embed with one field per game server. Each field's
name is a colored circle emoji (🔵 online / 🔴 offline) plus the server name,
and its value is a code block with Players/Memory/CPU (or "Offline"). A footer
on the embed shows the last-updated time.

Setup: copy config.example.json to config.json and fill in your values, then
`pip install -r requirements.txt` and `python bot.py`.
"""

import json
import logging
import os
import sys
import discord
from discord import app_commands
from discord.ext import tasks
from datetime import datetime

from amp_client import AMPClient, is_running, find_metric, format_metric_percent, format_metric_fraction

CONFIG_PATH = "config.json"
STATE_PATH = "state.json"

with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

logging.basicConfig(
    level=getattr(logging, CONFIG.get("log_level", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bot")

def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


intents = discord.Intents.default()
client_bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(client_bot)

amp = AMPClient(
    base_url=CONFIG["amp"]["url"],
    username=CONFIG["amp"]["username"],
    password=CONFIG["amp"]["password"],
    verify_ssl=CONFIG["amp"].get("verify_ssl", True),
)

state = load_state()


def build_embed(instances: list[dict]) -> discord.Embed:
    embed = discord.Embed(title="Server Status", color=discord.Color.dark_theme())
    instance_filter = CONFIG.get("instance_filter") or []
    any_added = False

    for inst in instances:
        name = inst.get("FriendlyName") or inst.get("InstanceName") or "Unknown Instance"
        if instance_filter and name not in instance_filter:
            continue

        running = is_running(inst)
        dot = "🔵" if running else "🔴"

        if running:
            players_metric = find_metric(inst, "user", "player", "active")
            memory_metric = find_metric(inst, "memory", "ram")
            cpu_metric = find_metric(inst, "cpu")

            players = format_metric_fraction(players_metric) or "?/?"
            memory = format_metric_percent(memory_metric) or "n/a"
            cpu = format_metric_percent(cpu_metric) or "n/a"

            body = f"Players: {players}\nMemory: {memory}\nCPU: {cpu}"
        else:
            body = "Offline"

        embed.add_field(name=f"{dot} {name}", value=f"```\n{body}\n```", inline=False)
        any_added = True

    if not any_added:
        embed.description = (
            "No instances found. Check `instance_filter` in config.json and "
            "confirm your API user can see instances."
        )

    return embed


async def get_target_channel() -> discord.TextChannel | None:
    channel_id = state.get("channel_id") or CONFIG.get("channel_id")
    if not channel_id:
        return None
    channel = client_bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await client_bot.fetch_channel(int(channel_id))
        except discord.NotFound:
            return None
    return channel


@tasks.loop(seconds=CONFIG.get("update_interval_seconds", 45))
async def update_status_message():
    channel = await get_target_channel()
    if channel is None:
        return

    try:
        instances = await amp.get_instances(debug_dump_raw=CONFIG.get("debug_dump_raw", False))
    except Exception as exc:
        log.error("Failed to fetch AMP instances: %s", exc)
        return

    embed = build_embed(instances)
    timestamp = datetime.now().strftime("%-I:%M %p")
    embed.set_footer(text=f"Updated: {timestamp}")

    message_id = state.get("message_id")
    message = None
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            message = None

    if message is None:
        message = await channel.send(embed=embed)
        state["message_id"] = message.id
        state["channel_id"] = channel.id
        save_state(state)
    else:
        await message.edit(embed=embed)


@update_status_message.before_loop
async def before_update():
    await client_bot.wait_until_ready()
    await amp.login()


@tree.command(name="setchannel", description="Set (or move) the channel this bot posts AMP status updates in")
@app_commands.checks.has_permissions(manage_guild=True)
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    state["channel_id"] = channel.id
    state["message_id"] = None
    save_state(state)
    await interaction.response.send_message(f"Will post AMP status updates in {channel.mention}.", ephemeral=True)


@tree.command(name="ampstatus", description="Force an immediate AMP status refresh")
async def ampstatus(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await update_status_message()
    await interaction.followup.send("Refreshed.", ephemeral=True)


@client_bot.event
async def on_ready():
    await tree.sync()
    log.info("Logged in as %s", client_bot.user)
    if not update_status_message.is_running():
        update_status_message.start()


if __name__ == "__main__":
    # log_handler=None: we already configured logging via basicConfig above,
    # so let discord.py's own log records flow through that instead of also
    # setting up its own separate handler/file.
    client_bot.run(CONFIG["discord_token"], log_handler=None)