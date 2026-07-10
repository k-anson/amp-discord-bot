# AMP Status Bot

Posts a self-updating status message to a Discord channel showing every game server
managed by your CubeCoders AMP (ADS controller) instance - players online, memory,
and CPU when running, or "Offline" when not.

## What it looks like

One "Host Status" embed, followed by a separate embed per game server - titled
with the server's name, colored blue if running / red if offline, no emoji needed
since the embed's side color already carries that signal:

```
Host Status
Instances Running: 1/4
Players Online: 0
Host CPU: ~2%
Host Memory: 1.8/23.5 GB (8%)
Allocated Memory: 16.0/23.5 GB (68%)
```
```
AbioticFactor                (blue)
Players: 0/6
Memory: 1.0/8.0 GB (13%)
CPU: 2%
```
```
Palworld                     (red)
Offline
Memory: 12.0 GB
```

- **Host Memory** is what's actually in use right now by running servers.
- **Allocated Memory** is the sum of every server's *configured max* memory
  (whether it's running or not) vs. the host's total RAM - i.e. what usage would
  look like if everything ran at once. No color coding or warnings, just the numbers.
- An **offline** server's embed shows its configured memory allocation (the max it's
  set to use) since there's no current usage to report - just "Offline" plus that number.
- The **Host Status** embed itself is blue whenever the bot successfully reached AMP.
  If the AMP API call fails outright (host down, network issue, bad credentials, etc.),
  the bot posts a single red "Host Status: Offline" embed instead - there's no server
  data to show at that point, so the per-server embeds are dropped until the next
  successful poll brings them back.

**Caveat on Allocated Memory / offline memory display:** both rely on AMP reporting
a `MaxValue` for a server's memory metric. Some AMP versions/instances report an
empty `Metrics: {}` while a server is stopped, in which case that server won't show
a memory line at all (falls back to "n/a") and silently won't count toward Allocated
Memory. Turn on `debug_dump_raw` and check one of your offline instances' `Metrics`
field to see whether this applies to your setup.

**Discord's 10-embeds-per-message limit:** the host embed takes one slot, leaving
9 for servers. If `instance_filter` (or your instance count) exceeds that, the bot
logs a warning and only shows the first 9 - narrow `instance_filter` in `config.json`
if you have more than 9 servers you want tracked.

## A note on embed width

Discord doesn't offer a way to set an embed's width directly - it auto-sizes each
embed's box to fit its own content, up to a fixed max. That means a short body like
"Offline" renders a visibly narrower box next to a longer one like a running
server's Players/Memory/CPU block. To keep them looking consistent, every code
block's lines get padded out with trailing spaces (invisible inside a monospace
block) to a fixed width before sending - `embed_pad_width_chars` in `config.json`
(default 34) controls that width. If your embeds still look uneven, try bumping
this up a bit; there's no documented exact pixel threshold from Discord, so this
is tuned by eye rather than a guaranteed API behavior.

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