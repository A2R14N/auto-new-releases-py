"""
Constants, paths, defaults, and shared utility functions.
"""

import os
import re
import sys
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

# Third-party imports
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    from spotipy.exceptions import SpotifyException
except ImportError:
    print("Error: spotipy is required. Install with: pip install spotipy")
    sys.exit(1)

# Optional: rich for better terminal output
try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich.panel import Panel
    from rich.text import Text
    from rich import print as rprint
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

# CONSTANTS & CONFIGURATION
APP_NAME = "Auto New Releases"
APP_VERSION = "1.0.0"
CONFIG_DIR = Path.home() / ".config" / "auto-new-releases"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_FILE = CONFIG_DIR / "cache.json"
TOKEN_CACHE = CONFIG_DIR / ".spotify_token_cache"

# Spotify API Credentials - set as environment variables or prompted at startup
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

# API Limits
API_LIMITS = {
    "TRACKS_PER_REQUEST": 50,
    "PLAYLIST_BATCH_SIZE": 100,
    "ARTIST_ALBUMS_LIMIT": 50,
    "PLAYLIST_TRACKS_LIMIT": 100,
    "SEARCH_LIMIT": 10,
}

# Default values
DEFAULT_VALUES = {
    "CHECK_INTERVAL": 24,  # hours
    "DAYS_TO_CHECK": 30,
    "MIN_POPULARITY": 30,
    "MAX_SONGS": 20,
    "MAX_SONGS_PER_ALBUM": 5,
}

# Remix/variant keywords to filter out
REMIX_KEYWORDS = [
    "remix", "reverb", "remaster", "acoustic", "live", "cover",
    "instrumental", "karaoke", "radio edit", "extended", "demo",
    "alternate", "slowed", "sped up", "speed up", "8d audio",
    "nightcore", "reprise", "session", "stripped", "piano",
    "guitar", "acustic", "unplugged", "orchestral", "version",
    "pian", "chitara", "editie", "concert", "versiune",
]

# Spotify API scopes needed
SPOTIFY_SCOPES = [
    "user-library-read",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
    "user-read-private",
]

# UTILITY FUNCTIONS
def ensure_config_dir():
    """Ensure the configuration directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def generate_id() -> str:
    """Generate a unique ID for profiles."""
    return hashlib.md5(f"{time.time()}{os.urandom(8).hex()}".encode()).hexdigest()[:12]


def print_colored(text: str, color: str = "white", bold: bool = False):
    """Print colored text, using rich if available, otherwise plain."""
    if RICH_AVAILABLE:
        style = f"bold {color}" if bold else color
        console.print(text, style=style)
    else:
        print(text)


def print_success(text: str):
    """Print success message."""
    print_colored(f"✓ {text}", "green", bold=True)


def print_error(text: str):
    """Print error message."""
    print_colored(f"✗ {text}", "red", bold=True)


def print_warning(text: str):
    """Print warning message."""
    print_colored(f"⚠ {text}", "yellow")


def print_info(text: str):
    """Print info message."""
    print_colored(f"ℹ {text}", "blue")


def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def format_duration(ms: int) -> str:
    """Format milliseconds to human readable duration."""
    hours = ms // 3600000
    minutes = (ms % 3600000) // 60000
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_number(num: int) -> str:
    """Format large numbers with K/M suffixes."""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)


def parse_spotify_uri(input_str: str, resource_type: str) -> Optional[str]:
    """
    Parse Spotify URI/URL and extract the ID.

    Args:
        input_str: Spotify URI, URL, or ID
        resource_type: 'artist', 'playlist', 'track', or 'album'

    Returns:
        The extracted ID or None if invalid
    """
    if not input_str:
        return None

    input_str = input_str.strip()

    # Handle URLs: https://open.spotify.com/artist/123...
    if "open.spotify.com" in input_str:
        match = re.search(rf'{resource_type}/([a-zA-Z0-9]+)', input_str)
        return match.group(1) if match else None

    # Handle URIs: spotify:artist:123...
    if input_str.startswith(f"spotify:{resource_type}:"):
        parts = input_str.split(":")
        return parts[2] if len(parts) >= 3 else None

    # Assume it's just an ID
    if re.match(r'^[a-zA-Z0-9]+$', input_str):
        return input_str

    return None


def normalize_spotify_uri(input_str: str, resource_type: str) -> Optional[str]:
    """Convert any Spotify input to standard URI format."""
    id_ = parse_spotify_uri(input_str, resource_type)
    return f"spotify:{resource_type}:{id_}" if id_ else None


def parse_release_date(date_string: str) -> Optional[datetime]:
    """Parse Spotify's various date formats."""
    if not date_string:
        return None

    try:
        if len(date_string) == 4:  # Year only
            return datetime.strptime(date_string, "%Y")
        elif len(date_string) == 7:  # Year-Month
            return datetime.strptime(date_string, "%Y-%m")
        else:  # Full date
            return datetime.strptime(date_string, "%Y-%m-%d")
    except ValueError:
        return None


def days_since(date: datetime) -> float:
    """Calculate days since a given date."""
    return (datetime.now() - date).total_seconds() / (60 * 60 * 24)
