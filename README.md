# Auto New Releases (ANR)

A powerful, terminal-based Python tool that automatically tracks new releases from your favorite Spotify artists and seamlessly adds them to a dedicated playlist. Say goodbye to missing new drops and hello to a fully automated new release radar.

## Key Features

- **Automated Tracking**: Scan your hand-picked artists for fresh albums and singles.
- **Smart Logic Filtering**: Intelligently skip remixes, acoustic variants, long albums, or unpopular tracks.
- **Deduplication Engine**: Built-in similarity filters to avoid adding the exact same song twice (e.g. Single vs Album releases).
- **Multi-Profile Support**: Maintain independent configurations and playlists for different moods or tracking needs.
- **Dual Authentication**: Fully supports standard Spotify Developer API OAuth or a zero-configuration "Spicetify Bridge" mode.

---

## Tech Stack

- **Language**: Python 3.9+
- **API Wrapper**: `spotipy` (for standard OAuth)
- **UI Framework**: `rich` (for beautiful CLI rendering)
- **Networking**: `requests` (for Spicetify Bridge polling)
- **Bridge Support**: JavaScript (Spicetify Extension)
- **Deployment**: Local Environment / Scheduled Task (Cron / Windows Task Scheduler)

---

## Prerequisites

- Python 3.9 or higher
- A Spotify Account (Free or Premium)
- **Option A:** Spotify Desktop App with [Spicetify](https://spicetify.app/) installed (Highly Recommended for zero API limits).
- **Option B:** Spotify Developer App credentials (for headless execution).

---

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/adrian/auto-new-releases.git
cd auto-new-releases
```

### 2. Install Dependencies

You can install this repository directly as a python package:

```bash
pip install -e ".[rich]"
```

Or manually install the dependencies:

```bash
pip install spotipy rich requests
```

### 3. Authentication Setup

You must pick one of two paths to allow ANR to read/modify your Spotify library.

#### Path A: Spicetify Bridge (Recommended)
This bypasses standard Spotify API restrictions (and developer dashboard setup) by routing commands directly through the active Spicetify Desktop Client.

1. Run the bridge installation script:
```bash
python install_bridge.py
```
2. Enable the extension in Spicetify:
```bash
spicetify config extensions anr-bridge.js
spicetify apply
```
3. Open Spotify. You should see a toast indicating **"🎸 ANR Bridge active"**. ANR will automatically detect this during launch.

#### Path B: Standard OAuth (For headless/servers)
1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create an App and add `http://127.0.0.1:8888/callback` as a Redirect URI.
3. Configure the following environment variables on your system:

| Variable | Description | Example |
| -------- | ----------- | ------- |
| `SPOTIFY_CLIENT_ID` | Your developer Client ID | `abcd1234abcd1234` |
| `SPOTIFY_CLIENT_SECRET` | Your developer Client Secret | `zzzz9999zzzz9999` |
| `SPOTIFY_REDIRECT_URI` | Required Redirect URI | `http://127.0.0.1:8888/callback` |

*(Note: ANR will safely prompt you for the ID/Secret inside the app if they are missing.)*

### 4. Launch the App

Start the terminal UI and follow the on-screen wizard to create your first tracking profile:

```bash
anr
```

All tracking state and system configurations are saved to `~/.config/auto-new-releases/config.json`.

---

## Architecture

This project is structured as a modular CLI package handling local persistence, an interactive user hierarchy, and API requests.

### Directory Structure

```
├── anr/                    # Main python package
│   ├── models.py           # Core dataclasses (Profile, Artist, Config)
│   ├── checker.py          # Release evaluation and similarity deduplication logic
│   ├── playlist.py         # Spotify playlist mutating logic (API + Bridge)
│   ├── ui.py               # Interactive CLI rendering powered by rich
│   ├── tools.py            # Diagnostic tools (analyzer, sorting, deduplication)
│   ├── config.py           # Local state JSON serialization
│   └── cli.py              # Argument parsing
├── anr-bridge.js           # Spicetify extension bridging code
├── install_bridge.py       # Utility script to install the bridge
└── setup.py                # Package definition
```

### Request Lifecycle Example (Release Check)
1. `check_profile()` is invoked from `anr/checker.py`.
2. App queries the active connection channel (Spicetify Bridge OR Spotipy) for `get_playlist_tracks()` to populate `existing_signatures`.
3. App loops through your tracked artists and hits `/artists/{id}/albums` to isolate recent releases.
4. Tracks are extracted and individually piped through filters (`Skip Remixes`, `Skip Similar Duplicates`, `Min Popularity`).
5. Qualifying tracks are batched and executed via `playlist_add_items`.
6. Successful additions are timestamped in the local `cache.json` state lock to avoid re-adding previously checked songs.

### Key Components

**Similar Duplicate Deduplication Layer**
Handled in `checker.py`, this generates lowercased string-based signatures (`track_name|||primary_artist`) for tracks currently in your playlist. It runs identical parsing against upcoming release additions to intercept track variants (like Singles vs Albums) that bypass traditional `track_uri` match tracking.

**Dual-Channel API Abstraction**
The tool abstracts `api.py` so standard functions (e.g. `get_playlist`, `add_tracks`) dynamically route to `anr-bridge.js` (listening locally on port `7421`) if Spicetify is running, skipping strict API limits.

---

## Environment Variables

| Variable | Description | Default |
| -------- | ----------- | ------- |
| `SPOTIFY_CLIENT_ID` | OAuth Client ID (if not using Bridge) | Prompt in UI |
| `SPOTIFY_CLIENT_SECRET` | OAuth Client Secret | Prompt in UI |
| `SPOTIFY_REDIRECT_URI` | Re-auth callback | `http://127.0.0.1:8888/callback` |

---

## Available Scripts

When installed via `pip install -e .`, the `anr` command acts as your primary entry.

| Command | Description |
| ------- | ----------- |
| `anr` | Launch the main interactive wizard |
| `anr playlist dedupe` | Launches the interactive utility tool to remove duplicates in an existing playlist |
| `anr playlist analyze` | Runs statistical analysis on your configured playlist |
| `python install_bridge.py` | Copies JS extension to your Spicetify path |

---

## Deployment (Scheduling)

Auto New Releases is best run as a background task. Since it is a terminal utility, "Deployment" means executing it on a schedule.

### Linux / macOS (Cron)

Ensure your Spotify config has generated an active `.spotify_token_cache`. Run `crontab -e` and append:

```bash
# Run checking scripts headlessly at 2:00 AM daily
0 2 * * * /path/to/your/venv/bin/anr >> /var/log/anr.log 2>&1
```

### Windows (Task Scheduler)

1. Open Task Scheduler and **Create Basic Task...**
2. Action: **Start a program**
3. Program/script: `anr` (or the direct path to where `anr.exe` is installed by pip)
4. Add arguments: *(Leave blank if using the command above)*
5. Run daily.

*(Note: Scheduled tasks will fail if using Bridge mode but Spotify Desktop is closed. Consider using the OAuth configuration for pure headless execution).*

---

## Troubleshooting

### Spicetify Bridge Won't Connect
**Error:** CLI hangs or skips Bridge connection entirely.
**Solution:**
1. Ensure the desktop Spotify app is open.
2. Confirm the extension is running by typing `spicetify config` (ensure `anr-bridge.js` is under `extensions`).
3. Turn off any firewalls silently blocking `localhost:7421`.

### Nothing gets added to the Playlist
**Solution:**
1. Open the UI (`anr`), navigate to Settings (`6`).
2. Verify you aren't filtering *too aggressively* (e.g., `Min Popularity` effectively excludes many indie artists). 
3. Lower `Days to Check` or type `reset` in the settings menu to clear your tracked cache and force a complete resync.

### Authentication Failures
**Error:** `spotipy.exceptions.SpotifyException: http status: 401, code:-1`
**Solution:** 
Delete the `~/.config/auto-new-releases/.spotify_token_cache` file and rerun the app to force a fresh OAuth login sequence.

---

## License
MIT License – free to use, modify, and distribute.
