# AMP Status Bot

Posts a self-updating status message to a Discord channel showing every game server
managed by your CubeCoders AMP (ADS controller) instance - players online, memory,
and CPU when running, or "Offline" when not.

## What it looks like

One Discord embed. A "System Overview" section up top (running count, total
players, an estimated host CPU/memory load, and a 🟢/🟡/🔴 capacity indicator),
followed by one field per server using a colored circle emoji (🔵 online, 🔴 offline):

```
Instances Running: 2/5
Players Online: 3
Host CPU: ~35%
Host Memory: 6.0/23.4 GB (26%)
Capacity: 🟢 Room to host another server

🔵 Palworld
Players: 3/8
Memory: 40%
CPU: 25%

🔴 Rust
Offline

Updated: 3:18 PM
```

The capacity indicator is based on total memory used by *running* instances vs.
the host's installed RAM (from AMP's `Platform.InstalledRAMMB`), not a hardcoded
"one server at a time" rule - so it adapts if your headroom or hosting habits
change. Thresholds are tunable in `config.json`:
- `capacity_warn_percent` (default 70) - 🟡 above this
- `capacity_full_percent` (default 90) - 🔴 above this

The CPU figure is a rough estimate (sum of each running instance's reported CPU
percent) rather than true host-wide CPU load, since AMP doesn't expose that
directly - treat it as a ballpark, not the number the capacity indicator relies on.

## Setup

1. **Create a Discord bot**
   - https://discord.com/developers/applications -> New Application -> Bot -> Reset Token, copy it
   - Under "Privileged Gateway Intents" you don't need any of the privileged ones for this bot
   - Invite it to your server with `bot` and `applications.commands` scopes, and
     `Send Messages` / `Embed Links` / `View Channel` permissions

2. **Create a dedicated AMP API user**
   - In AMP, go to Users -> create a new user (e.g. `discord-bot-api-user`) with a strong password
   - Give it read-only permissions on ADSModule/GetInstances and on each instance you want shown
     (don't reuse your personal admin login)
   - Confirm the AMP web UI's API is reachable at `https://your-amp-host:port/API` in a browser -
     that's also AMP's built-in API explorer, useful if fields below need adjusting for your version

3. **Configure the bot**
   ```
   cd amp-discord-bot
   cp config.example.json config.json
   ```
   Edit `config.json`:
   - `discord_token`: your bot token
   - `channel_id`: right-click the channel in Discord (Developer Mode on) -> Copy Channel ID
   - `amp.url` / `amp.username` / `amp.password`: your AMP API credentials
   - `update_interval_seconds`: 30-60 is reasonable; AMP's own metrics only refresh every
     few seconds so there's no benefit going much faster
   - `instance_filter`: leave as `[]` to show every instance, or list specific instance
     names to show only those
   - `debug_dump_raw`: set `true` temporarily if metrics show as "n/a" - it prints the raw
     AMP API response to your console so you can see the exact key names your AMP version uses

4. **Install and run**
   ```
   pip install -r requirements.txt
   python bot.py
   ```
   On first run it posts a new status message in the configured channel and starts editing
   it in place every `update_interval_seconds`. The message ID is saved in `state.json` so
   restarts keep editing the same message instead of spamming new ones.

## Slash commands

- `/setchannel #channel` - move the status message to a different channel (needs Manage Server)
- `/ampstatus` - force an immediate refresh

## If metrics don't parse correctly

AMP's exact JSON field names for CPU/memory/player-count have shifted slightly between
versions over the years. `amp_client.py` searches metric keys by substring (`"cpu"`,
`"memory"`, `"user"/"player"/"active"`) rather than an exact name, which covers most
versions, but if you see "n/a":

1. Set `"debug_dump_raw": true` in `config.json` and restart the bot (it's read once at
   startup, so a running process won't pick up the change)
2. Run `python bot.py` (or `journalctl -u amp-status-bot -f` if running as a service), watch
   for `=== RAW GetInstances ===`
3. Find the real metric key names in your output and adjust the keyword lists passed to
   `find_metric(...)` calls in `bot.py`'s `build_embeds()` function

## Notes

- The bot needs to stay running continuously (e.g. as a systemd service, in a screen/tmux
  session, or as another AMP-managed generic instance) for the updates to keep happening.
- AMP sessions expire; `amp_client.py` automatically re-logs-in if a call comes back
  unauthorized, so you shouldn't need to restart the bot for that.