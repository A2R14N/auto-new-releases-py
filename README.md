# 🎵 Auto New Releases – Spotify Release Tracker

Auto New Releases is a **terminal-based Python tool** that automatically tracks new releases from your favorite Spotify artists and adds them to a playlist.  
It supports **multiple profiles**, **artist filtering**, **release rules**, and **playlist automation**.

---

## ✨ Features

- 🔍 Track new **albums & singles** from selected artists
- 📅 Filter releases by date (e.g. last 30 days)
- 🚫 Skip remixes, low-popularity tracks, or long albums
- 🎧 Automatically add new tracks to a Spotify playlist
- 👤 Multiple user profiles with independent settings
- 🖥️ Rich terminal UI (optional, via `rich`)
- 🔐 Secure Spotify OAuth authentication

---

## 📦 Requirements

- **Python 3.9+**
- A **Spotify account**
- Spotify Developer credentials

### Python Dependencies

```bash
pip install spotipy rich
````

---

## 🔑 Spotify API Setup

1. Go to the **Spotify Developer Dashboard**
   [https://developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Add this redirect URI:

   ```
   http://127.0.0.1:8888/callback
   ```
4. Copy your **Client ID** and **Client Secret**

You can either:

* Enter them when prompted, **or**
* Set them as environment variables:

```bash
export SPOTIFY_CLIENT_ID="your_client_id"
export SPOTIFY_CLIENT_SECRET="your_client_secret"
export SPOTIFY_REDIRECT_URI="http://127.0.0.1:8888/callback"
```

---

## 🚀 Usage

Run the script:

```bash
python auto_new_releases.py
```

On first launch:

* You’ll authenticate with Spotify
* A default profile will be created
* Config files will be stored in:

```
~/.config/auto-new-releases/
```

---

## 👤 Profiles

Each profile has its own:

* Tracked artists
* Playlist
* Filters & rules
* Release history

You can:

* Create / delete / duplicate profiles
* Switch between profiles
* Reset tracked releases

---

## ⚙️ Configuration Options

Per profile settings include:

* Check interval (hours)
* Days to look back (0 = all time)
* Sort playlist by release date
* Skip remixes & variants
* Minimum popularity filter
* Album track limits
* Max songs per album

---

## 🧠 How It Works

1. Fetches artist releases via Spotify API
2. Filters releases based on your rules
3. Detects untracked releases
4. Adds qualifying tracks to your playlist
5. Saves state to avoid duplicates

---

## 📁 Project Structure

```
auto_new_releases.py   # Main application
~/.config/
└── auto-new-releases/
    ├── config.json
    ├── cache.json
    └── .spotify_token_cache
```

---

## 🛠️ Optional Enhancements

* Add cron / scheduled execution
* Export playlists or logs
* Notification support
* Web or GUI interface

---

## 🐛 Troubleshooting

* **Authentication fails**
  → Re-check redirect URI and API credentials

* **Nothing added to playlist**
  → Verify filters (popularity, remixes, date range)

* **Rate limited**
  → Wait and retry (Spotify API limit)

---

## 📜 License

MIT License – free to use, modify, and distribute.

---

## ❤️ Credits

Built with:

* [Spotipy](https://spotipy.readthedocs.io/)
* [Rich](https://github.com/Textualize/rich)
* Spotify Web API

---

Happy listening 🎶
