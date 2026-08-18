# Auto New Releases (ANR) 🎵

<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Spicetify Compatible](https://img.shields.io/badge/Spicetify-Extension%20Ready-f43f5e.svg?logo=spotify&logoColor=white)](https://spicetify.app/)
[![Code Style: Rich](https://img.shields.io/badge/CLI-Rich%20Aurora-38bdf8.svg)](https://github.com/Textualize/rich)
[![Architecture: Dual--Engine](https://img.shields.io/badge/Engine-Spicetify%20%7C%20Web%20API-1ed760.svg)]()

**An intelligent, terminal-driven Spotify release radar that automatically tracks your favorite artists, filters out unwanted noise, and syncs fresh drops directly into dedicated playlists.**

[Features](#-key-features) •
[Architecture](#-dual-engine-architecture) •
[Installation](#-getting-started) •
[Spicetify Bridge](#-spicetify-bridge-setup-recommended) •
[CLI Reference](#-cli-command-reference) •
[Configuration](#-configuration--filtering-engine) •
[Automation](#-automation--background-scheduling)

</div>

---

## ⚡ Overview

Streaming platforms often clutter your release radar with acoustic versions, VIP remixes, or tracks from massive deluxe reissue dumps. **Auto New Releases (ANR)** gives you complete, granular control over your music feed.

ANR monitors your hand-picked artist roster, detects new singles and albums within custom lookback windows, filters them using intelligent heuristics (remix detection, popularity thresholds, album track limits, and cross-release duplicate matching), and seamlessly batches them into your Spotify playlists.

```
┌─────────────────┐       ┌──────────────────────┐       ┌────────────────────────┐
│ Tracked Artists │ ───►  │ Filter & Dedupe Core │ ───►  │ Target Spotify Playlist│
└─────────────────┘       └──────────────────────┘       └────────────────────────┘
                                    │
                ┌───────────────────┴───────────────────┐
                ▼                                       ▼
    ┌──────────────────────┐                ┌──────────────────────┐
    │   Spicetify Bridge   │                │   Spotify Web API    │
    │  (Zero Rate Limits)  │                │ (Headless / Servers) │
    └──────────────────────┘                └──────────────────────┘
```

---

## ✨ Key Features

- **🔄 Dual-Engine Connectivity**:
  - **Spicetify Bridge Mode**: Hooks into the Spotify Desktop client via a lightweight local extension. Zero rate limits, no developer registration required.
  - **Official Web API (OAuth)**: Standard Spotify Web API PKCE/OAuth with automatic token caching for headless servers, containers, and cron jobs.
- **🧠 Intelligent Filtering & Heuristics**:
  - **Remix & Variant Detection**: Automatically detect and drop remix cuts, acoustic recordings, live bootlegs, and instrumental edits.
  - **Fuzzy Duplicate Interception**: Prevent adding single releases when they later appear on a full album, and vice-versa, using track-signature deduplication (`title + primary artist`).
  - **Popularity & Length Controls**: Filter out unranked/low-popularity drops and cap max songs per album or skip sprawling box sets.
- **👥 Multi-Profile Ecosystem**:
  - Create separate tracking profiles (e.g., *Electronic Focus*, *Indie Radar*, *Heavy Rotation*), each with dedicated artist rosters, check intervals, custom lookback windows, and distinct target playlists.
- **🛠️ Playlist Power Tools**:
  - Built-in multi-criteria playlist sorting (Release Date, Popularity, Duration, Date Added, Artist, Track Name).
  - Deep playlist deduplication and statistical health analyzers.
- **🎨 Modern Aurora Terminal UI**:
  - Powered by Rich with animated gradient progress bars, structured dashboards, status tables, and keyboard-driven shortcuts.
- **⏱️ Flexible Background Automation**:
  - Built-in daemon runner (`anr daemon`), single-pass scheduler flags (`--once`), and seamless integration with Cron, `systemd`, or Windows Task Scheduler.

---

## 🏗️ Dual-Engine Architecture

ANR dynamically selects the best communication channel based on your environment:

| Feature | 🎸 Spicetify Bridge (Port 7421) | 🌐 Spotify Web API (OAuth) |
| :--- | :--- | :--- |
| **Best For** | Desktop users, power listeners | Headless servers, Docker, VPS, Cron |
| **Rate Limits** | **None** (internal client calls) | Standard Spotify API quotas |
| **Setup Overhead** | Zero developer credentials needed | Developer app ID & secret required |
| **Client Requirement** | Spotify Desktop app open | No Spotify client needed |
| **Data Hydration** | Live Spicetify client GraphQL / internal | Standard REST endpoints |

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.9+**
- A **Spotify Account** (Free or Premium)
- *Optional (Recommended)*: [Spicetify CLI](https://spicetify.app/) for zero-config bridge mode.

---

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/A2R14N/auto-new-releases-py.git
   cd auto-new-releases-py
   ```

2. **Create and activate a virtual environment**:
   ```bash
   # Windows (PowerShell)
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install the package**:
   ```bash
   # Install with rich terminal UI support
   pip install -e ".[rich]"
   ```

---

## 🔌 Connection Setup

Choose your preferred connection method:

### Option A: Spicetify Bridge Setup *(Recommended)*

The Spicetify bridge lets ANR interact with Spotify locally without needing Spotify Developer API keys.

1. **Install the bridge extension**:
   ```bash
   python install_bridge.py
   ```
2. **Register and apply the extension in Spicetify**:
   ```bash
   spicetify config extensions anr-bridge.js
   spicetify apply
   ```
3. **Open Spotify Desktop**:
   You will see a notification toast: **`🎸 ANR Bridge active`**. ANR automatically detects the local bridge server on port `7421`.

---

### Option B: Spotify Web API OAuth *(Headless / Servers)*

For headless servers or environments without the Spotify Desktop client:

1. Visit the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create an application and add `http://127.0.0.1:8888/callback` under **Redirect URIs**.
3. Set your environment variables (or let ANR prompt you upon first start):

   ```bash
   export SPOTIFY_CLIENT_ID="your_client_id_here"
   export SPOTIFY_CLIENT_SECRET="your_client_secret_here"
   export SPOTIFY_REDIRECT_URI="http://127.0.0.1:8888/callback"
   ```

---

## 🖥️ Usage

### Interactive Terminal Mode

Launch the interactive dashboard:

```bash
anr
```

```text
╭── Auto New Releases  v2.0.0 ─────────────────────────────────────╮
│ Main Profile                                             ● Ready │
│ 142 artists · My New Releases                   2026-08-18 12:45 │
│ ──────────────────────── Actions ─────────────────────────────── │
│ > 1  Check current profile                                 Ready │
│   2  Check all profiles                               3 profiles │
│   3  Manage artists                                  142 tracked │
│   4  Manage playlist                             My New Releases │
│   5  Manage profiles                                  3 profiles │
│   6  Settings                                                    │
│ ──────────────────────────────────────────────────────────────── │
│  [S] Schedule     [7] Import / Export     [Q] Quit               │
╰──────────────────────────────────────────────────────────────────╯
```

---

## 📖 CLI Command Reference

ANR comes with a comprehensive non-interactive CLI for scripting and automation:

### Release Checking

| Command | Description |
| :--- | :--- |
| `anr check` | Check the currently active profile for new releases |
| `anr check --all` | Run checks across all configured profiles sequentially |
| `anr check --profile "Indie Radar"` | Check a specific profile by name |
| `anr check --dry-run` | Preview what tracks would be added without modifying the playlist |

### Artist Management

| Command | Description |
| :--- | :--- |
| `anr artists list` | List all tracked artists in the active profile |
| `anr artists add "Daft Punk"` | Search and add artist by name |
| `anr artists add spotify:artist:...` | Add artist directly by Spotify URI / URL |
| `anr artists remove "Artist Name"` | Remove an artist from tracking |
| `anr artists refresh` | Force refresh cached artist metadata |

### Playlist Tools

| Command | Description |
| :--- | :--- |
| `anr playlist info` | Display active playlist statistics and metadata |
| `anr playlist set <URI>` | Set the target playlist for the active profile |
| `anr playlist sort` | Sort target playlist tracks (by release date, popularity, etc.) |
| `anr playlist dedupe` | Scan and remove duplicate or variant tracks from the playlist |
| `anr playlist analyze` | Output detailed breakdown of genres, release dates, and popularity |

### Profile & Configuration

| Command | Description |
| :--- | :--- |
| `anr profile list` | Show all available tracking profiles |
| `anr profile switch "Name"` | Switch the active profile |
| `anr profile create "Name"` | Create a new tracking profile |
| `anr profile delete "Name"` | Remove an existing profile |
| `anr export -o backup.json` | Export profile configurations and history to JSON |
| `anr import backup.json` | Import profiles with conflict resolution (`skip`/`replace`/`rename`) |
| `anr config --show` | Display current local configuration state |

---

## ⚙️ Configuration & Filtering Engine

Each profile in ANR maintains independent filter rules configured via the settings menu (`anr` -> `6`):

```text
╭── Settings  ·  Main Profile ─────────────────────────────────────╮
│ [i]  Check interval                                           4h │
│ [d]  Days to check                                        7 days │
│ [s]  Sort by date                                             On │
│ [r]  Skip remixes                                             On │
│ [p]  Skip low popularity                                      On │
│ [m]  Minimum popularity                                       25 │
│ [a]  Skip long albums                                        Off │
│ [l]  Limit per album                                          On │
│ [x]  Maximum songs per album                                   3 │
│ [u]  Skip similar tracks                                      On │
│ ──────────────────────────────────────────────────────────────── │
│ Tracked  284 releases  ·  412 tracks       [reset] Clear history │
╰──────────────────────────────────────────────────────────────────╯
```

### Filtering Logic Explained

* **`Skip Remixes` (`skip_remixes`)**: Filters out tracks matching keywords such as `Remix`, `Mix`, `Edit`, `Acoustic`, `Instrumental`, `Live at`, `VIP`, `Dub`, or `Remaster`.
* **`Skip Similar Tracks` (`skip_similar_duplicates`)**: Generates normalized signatures (`clean_title|||primary_artist`). If a single is already in your playlist or history, subsequent album re-releases of that exact song will not create duplicates.
* **`Limit per Album` (`limit_songs_per_album` / `max_songs_per_album`)**: Protects your playlist from being hijacked by 25-track LP drops by picking only the top songs per album release.
* **`Minimum Popularity` (`min_popularity`)**: Rejects tracks with Spotify popularity scores below the set threshold.

---

## ⏰ Automation & Background Scheduling

### Daemon Mode

Run ANR continuously in the background. It will automatically wake up and check profiles according to their individual `check_interval`:

```bash
anr daemon
```

Override the interval on the fly:
```bash
# Check all profiles every 120 minutes
anr daemon --interval 120
```

---

### Cron Job (Linux / macOS)

Add a single-shot execution to your crontab (`crontab -e`):

```bash
# Run release check every morning at 06:00
0 6 * * * /path/to/.venv/bin/anr check --all --quiet >> /var/log/anr.log 2>&1
```

---

### Windows Task Scheduler

1. Open **Task Scheduler** and select **Create Basic Task**.
2. **Trigger**: Daily (or choose your desired frequency).
3. **Action**: Start a program.
4. **Program/Script**: Point to your virtual environment's executable:
   ```text
   C:\Users\<User>\Documents\Code\auto-new-releases\.venv\Scripts\anr.exe
   ```
5. **Add Arguments**: `check --all --quiet`

---

## 📂 Project Structure

```
auto-new-releases/
├── anr/                       # Core Python package
│   ├── api.py                 # Spotify API interface & metadata hydration
│   ├── auth.py                # OAuth token management & PKCE
│   ├── bridge_api.py          # Spicetify local HTTP bridge adapter
│   ├── bridge_server.py       # Embedded server handling bridge payloads
│   ├── checker.py             # Release fetcher, gradient progress, and logic engine
│   ├── cli.py                 # Argument parsing & headless command execution
│   ├── config.py              # Configuration storage & migration manager
│   ├── constants.py           # Palette colors, defaults, and shared constants
│   ├── filters.py             # Regex heuristics for remixes, acoustic, & duplicates
│   ├── importer.py            # JSON backup, export, and migration engine
│   ├── models.py              # Dataclasses: Profile, Artist, Track, Config
│   ├── playlist.py            # Playlist mutate, batch add, and track extraction
│   ├── profile.py             # Profile lifecycle and switching logic
│   ├── tools.py               # Playlist dedupe, sorter, and analytics tools
│   └── ui.py                  # Rich Aurora dashboard and interactive menus
├── anr-bridge.js              # Spicetify Desktop Client JavaScript extension
├── install_bridge.py          # Automated Spicetify extension installer
├── setup.py                   # Package setup and entry point definition
└── README.md                  # Documentation
```

---

## 🔧 Troubleshooting

<details>
<summary><b>1. Spicetify Bridge is not detected</b></summary>

* Verify the Spotify desktop client is actively running.
* Run `spicetify config` and ensure `anr-bridge.js` is present in the `extensions` list.
* Re-run `spicetify apply` in your terminal.
* Ensure local port `7421` is not blocked by third-party firewall software.
</details>

<details>
<summary><b>2. No new tracks are being added to the playlist</b></summary>

* Open `anr` -> `Settings [6]`.
* Check if `Minimum Popularity` is configured too high for underground or indie artists.
* Type `reset` inside the Settings menu to clear the tracked release history cache and force a complete re-scan.
* Run `anr check --dry-run` to preview candidate releases and diagnose filter matches.
</details>

<details>
<summary><b>3. OAuth 401 Unauthorized Error</b></summary>

* Delete `.spotify_token_cache` located in your project or home directory.
* Re-run `anr` to authenticate through a fresh browser session.
</details>

---

## 📄 License

This project is open-source software licensed under the [MIT License](LICENSE).
