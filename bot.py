"""
Discord bot that posts a live-updating AMP server status message.

Format: one Discord embed for "Host Status" (instances running, players online,
host CPU/memory, allocated memory), followed by a separate embed per game server -
titled with the server's name, colored blue if online / red if offline, with a
Players/Memory/CPU code block (or "Offline" plus its allocated memory if off).
All embeds are attached to a single message. A footer on the last embed shows
the last-updated time.

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

from amp_client import (
    AMPClient,
    is_running,
    find_metric,
    format_metric_percent,
    format_metric_fraction,
    format_metric_memory_gb,
    format_metric_max_gb,
    metric_raw,
)

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

# Discord embeds auto-size to their content width (up to a max), so a short
# "Offline" body renders a noticeably narrower box than a longer one. Padding
# every code block line out to a fixed width with trailing spaces (invisible
# inside a monospace block) forces all embeds toward the same rendered width.
EMBED_PAD_WIDTH = CONFIG.get("embed_pad_width_chars", 34)


def pad_block(text: str, width: int = EMBED_PAD_WIDTH) -> str:
    return "\n".join(line.ljust(width) for line in text.split("\n"))


def build_overview_text(snapshot) -> str:
    instances = snapshot.instances
    host = snapshot.host
    instance_filter = CONFIG.get("instance_filter") or []
    relevant = [i for i in instances if not instance_filter or
                (i.get("FriendlyName") or i.get("InstanceName")) in instance_filter]

    running = [i for i in relevant if is_running(i)]
    total_players = 0
    total_mem_mb = 0.0
    total_cpu_pct = 0.0
    have_player_data = False
    have_mem_data = False
    have_cpu_data = False

    for inst in running:
        players = metric_raw(find_metric(inst, "user", "player", "active"))
        if players is not None:
            total_players += players
            have_player_data = True
        mem = metric_raw(find_metric(inst, "memory", "ram"))
        if mem is not None:
            total_mem_mb += mem
            have_mem_data = True
        cpu_metric = find_metric(inst, "cpu")
        cpu_pct = cpu_metric.get("Percent") if cpu_metric else None
        if cpu_pct is not None:
            total_cpu_pct += cpu_pct
            have_cpu_data = True

    lines = [f"Instances Running: {len(running)}/{len(relevant)}"]
    lines.append(f"Players Online: {total_players}" if have_player_data else "Players Online: n/a")
    lines.append(f"Host CPU: ~{round(total_cpu_pct)}%" if have_cpu_data else "Host CPU: n/a")

    if have_mem_data and host.installed_ram_mb:
        used_gb = total_mem_mb / 1024
        total_gb = host.installed_ram_mb / 1024
        pct = (total_mem_mb / host.installed_ram_mb) * 100
        lines.append(f"Host Memory: {used_gb:.1f}/{total_gb:.1f} GB ({round(pct)}%)")
    else:
        lines.append("Host Memory: n/a")

    # Allocated Memory: sum of each server's configured MAX memory (regardless of
    # whether it's currently running) vs. total host RAM - i.e. what would be used
    # if every server ran at once, not what's actually in use right now.
    total_allocated_mb = 0.0
    have_allocated_data = False
    for inst in relevant:
        mem_metric = find_metric(inst, "memory", "ram")
        cap = (mem_metric or {}).get("MaxValue")
        if cap:
            total_allocated_mb += cap
            have_allocated_data = True

    if have_allocated_data and host.installed_ram_mb:
        alloc_gb = total_allocated_mb / 1024
        total_gb = host.installed_ram_mb / 1024
        pct = (total_allocated_mb / host.installed_ram_mb) * 100
        lines.append(f"Allocated Memory: {alloc_gb:.1f}/{total_gb:.1f} GB ({round(pct)}%)")
    else:
        lines.append("Allocated Memory: n/a")

    return "\n".join(lines)


def build_embeds(snapshot) -> list[discord.Embed]:
    host_embed = discord.Embed(
        title="Host Status",
        description=f"```\n{pad_block(build_overview_text(snapshot))}\n```",
        color=discord.Color.blue(),
    )

    instance_filter = CONFIG.get("instance_filter") or []
    relevant = [
        inst for inst in snapshot.instances
        if not instance_filter
        or (inst.get("FriendlyName") or inst.get("InstanceName")) in instance_filter
    ]

    if not relevant:
        host_embed.add_field(
            name="Servers",
            value=(
                "No instances found. Check `instance_filter` in config.json and "
                "confirm your API user can see instances."
            ),
            inline=False,
        )
        return [host_embed]

    embeds = [host_embed]
    MAX_SERVER_EMBEDS = 9  # Discord allows 10 embeds/message total; host embed takes 1

    if len(relevant) > MAX_SERVER_EMBEDS:
        log.warning(
            "%d instances found but Discord only allows %d server embeds per message; "
            "showing the first %d. Narrow `instance_filter` in config.json to fit.",
            len(relevant), MAX_SERVER_EMBEDS, MAX_SERVER_EMBEDS,
        )
        relevant = relevant[:MAX_SERVER_EMBEDS]

    for inst in relevant:
        name = inst.get("FriendlyName") or inst.get("InstanceName") or "Unknown Instance"
        running = is_running(inst)
        color = discord.Color.blue() if running else discord.Color.red()

        if running:
            players_metric = find_metric(inst, "user", "player", "active")
            memory_metric = find_metric(inst, "memory", "ram")
            cpu_metric = find_metric(inst, "cpu")

            players = format_metric_fraction(players_metric) or "?/?"
            memory = format_metric_memory_gb(memory_metric) or format_metric_percent(memory_metric) or "n/a"
            cpu = format_metric_percent(cpu_metric) or "n/a"

            body = f"Players: {players}\nMemory: {memory}\nCPU: {cpu}"
        else:
            memory_metric = find_metric(inst, "memory", "ram")
            allocated = format_metric_max_gb(memory_metric)
            body = "Offline\nMemory: " + (allocated if allocated else "n/a")

        embeds.append(discord.Embed(title=name, description=f"```\n{pad_block(body)}\n```", color=color))

    return embeds


def build_offline_host_embed() -> discord.Embed:
    """Shown when the AMP API call itself failed - we have no data on the host
    or any server, so there's nothing to show except that the host is unreachable."""
    return discord.Embed(
        title="Host Status",
        description=f"```\n{pad_block('Offline')}\n```",
        color=discord.Color.red(),
    )


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
        snapshot = await amp.get_snapshot(debug_dump_raw=CONFIG.get("debug_dump_raw", False))
        embeds = build_embeds(snapshot)
    except Exception as exc:
        log.error("Failed to fetch AMP instances: %s", exc)
        embeds = [build_offline_host_embed()]

    timestamp = datetime.now().strftime("%-I:%M %p")
    embeds[-1].set_footer(text=f"Updated: {timestamp}")

    message_id = state.get("message_id")
    message = None
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            message = None

    if message is None:
        message = await channel.send(embeds=embeds)
        state["message_id"] = message.id
        state["channel_id"] = channel.id
        save_state(state)
    else:
        await message.edit(embeds=embeds)


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