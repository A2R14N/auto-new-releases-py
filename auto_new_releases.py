#!/usr/bin/env python3
"""
Auto New Releases - Spotify Release Tracker
A terminal-based tool to automatically track new releases from your favorite artists.
"""

import os
import sys
import json
import time
import hashlib
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple
from dataclasses import dataclass, field, asdict
from functools import wraps

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

# ============================================
# CONSTANTS & CONFIGURATION
# ============================================

APP_NAME = "Auto New Releases"
APP_VERSION = "1.0.0"
CONFIG_DIR = Path.home() / ".config" / "auto-new-releases"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_FILE = CONFIG_DIR / "cache.json"
TOKEN_CACHE = CONFIG_DIR / ".spotify_token_cache"

# Spotify API Credentials - Users should set these as environment variables
# or they will be prompted to enter them
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


# ============================================
# UTILITY FUNCTIONS
# ============================================

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
        import re
        match = re.search(rf'{resource_type}/([a-zA-Z0-9]+)', input_str)
        return match.group(1) if match else None
    
    # Handle URIs: spotify:artist:123...
    if input_str.startswith(f"spotify:{resource_type}:"):
        parts = input_str.split(":")
        return parts[2] if len(parts) >= 3 else None
    
    # Assume it's just an ID
    import re
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


# ============================================
# DATA CLASSES
# ============================================

@dataclass
class Artist:
    """Represents a tracked artist."""
    uri: str
    name: str
    image: Optional[str] = None
    followers: Optional[int] = None
    last_follower_update: Optional[float] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Artist':
        """Create Artist from dict, handling both camelCase and snake_case keys."""
        # Map camelCase keys to snake_case
        key_mapping = {
            'lastFollowerUpdate': 'last_follower_update',
            'lastfollowerupdate': 'last_follower_update',
        }
        
        # Normalize keys
        normalized = {}
        for key, value in data.items():
            # Convert camelCase to snake_case if needed
            normalized_key = key_mapping.get(key, key)
            normalized[normalized_key] = value
        
        # Only pass known fields
        valid_fields = {'uri', 'name', 'image', 'followers', 'last_follower_update'}
        filtered = {k: v for k, v in normalized.items() if k in valid_fields}
        
        return cls(**filtered)


@dataclass
class Profile:
    """Represents a user profile with settings and tracked artists."""
    id: str
    name: str
    artists: List[Artist] = field(default_factory=list)
    playlist_uri: str = ""
    playlist_name: str = ""
    check_interval: int = DEFAULT_VALUES["CHECK_INTERVAL"]
    last_check: Optional[float] = None
    tracked_releases: Dict[str, float] = field(default_factory=dict)
    tracked_tracks: Dict[str, float] = field(default_factory=dict)
    days_to_check: int = DEFAULT_VALUES["DAYS_TO_CHECK"]
    sort_by_date: bool = True
    skip_remixes: bool = False
    skip_low_popularity: bool = False
    min_popularity: int = DEFAULT_VALUES["MIN_POPULARITY"]
    skip_long_albums: bool = False
    max_songs: int = DEFAULT_VALUES["MAX_SONGS"]
    limit_songs_per_album: bool = False
    max_songs_per_album: int = DEFAULT_VALUES["MAX_SONGS_PER_ALBUM"]
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        data['artists'] = [a if isinstance(a, dict) else a.to_dict() for a in self.artists]
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Profile':
        """Create Profile from dict, handling both camelCase and snake_case keys."""
        # Map camelCase keys to snake_case
        key_mapping = {
            'playlistUri': 'playlist_uri',
            'playlistName': 'playlist_name',
            'checkInterval': 'check_interval',
            'lastCheck': 'last_check',
            'trackedReleases': 'tracked_releases',
            'trackedTracks': 'tracked_tracks',
            'daysToCheck': 'days_to_check',
            'sortByDate': 'sort_by_date',
            'skipRemixes': 'skip_remixes',
            'skipLowPopularity': 'skip_low_popularity',
            'minPopularity': 'min_popularity',
            'skipLongAlbums': 'skip_long_albums',
            'maxSongs': 'max_songs',
            'limitSongsPerAlbum': 'limit_songs_per_album',
            'maxSongsPerAlbum': 'max_songs_per_album',
        }

        # Normalize keys
        normalized = {}
        for key, value in data.items():
            normalized_key = key_mapping.get(key, key)
            normalized[normalized_key] = value

        # Extract and convert artists
        artists_data = normalized.pop('artists', [])
        artists = []
        for a in artists_data:
            if isinstance(a, dict):
                artists.append(Artist.from_dict(a))
            elif isinstance(a, Artist):
                artists.append(a)

        # Only pass known fields to avoid errors
        valid_fields = {
            'id', 'name', 'playlist_uri', 'playlist_name', 'check_interval',
            'last_check', 'tracked_releases', 'tracked_tracks', 'days_to_check', 
            'sort_by_date', 'skip_remixes', 'skip_low_popularity', 'min_popularity',
            'skip_long_albums', 'max_songs', 'limit_songs_per_album',
            'max_songs_per_album'
        }
        filtered = {k: v for k, v in normalized.items() if k in valid_fields}

        return cls(artists=artists, **filtered)


@dataclass
class Config:
    """Main configuration container."""
    profiles: List[Profile] = field(default_factory=list)
    active_profile_id: Optional[str] = None
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    
    def to_dict(self) -> Dict:
        return {
            'profiles': [p.to_dict() for p in self.profiles],
            'active_profile_id': self.active_profile_id,
            'spotify_client_id': self.spotify_client_id,
            'spotify_client_secret': self.spotify_client_secret,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Config':
        profiles = [Profile.from_dict(p) for p in data.get('profiles', [])]
        return cls(
            profiles=profiles,
            active_profile_id=data.get('active_profile_id'),
            spotify_client_id=data.get('spotify_client_id', ''),
            spotify_client_secret=data.get('spotify_client_secret', ''),
        )


# ============================================
# CONFIGURATION MANAGEMENT
# ============================================

class ConfigManager:
    """Handles loading and saving configuration."""
    
    def __init__(self):
        ensure_config_dir()
        self.config: Config = self._load()
    
    def _load(self) -> Config:
        """Load configuration from file."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return Config.from_dict(data)
            except (json.JSONDecodeError, KeyError) as e:
                print_warning(f"Config file corrupted, creating new: {e}")
        
        return self._create_default()
    
    def _create_default(self) -> Config:
        """Create default configuration with one profile."""
        profile_id = generate_id()
        default_profile = Profile(
            id=profile_id,
            name="Default Profile"
        )
        config = Config(
            profiles=[default_profile],
            active_profile_id=profile_id
        )
        self.save(config)
        return config
    
    def save(self, config: Optional[Config] = None):
        """Save configuration to file."""
        if config:
            self.config = config
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.config.to_dict(), f, indent=2)
    
    def get_active_profile(self) -> Profile:
        """Get the currently active profile."""
        for profile in self.config.profiles:
            if profile.id == self.config.active_profile_id:
                return profile
        
        # Fallback to first profile
        if self.config.profiles:
            return self.config.profiles[0]
        
        # Create new profile if none exist
        return self._create_default().profiles[0]
    
    def get_profile_by_id(self, profile_id: str) -> Optional[Profile]:
        """Get a profile by its ID."""
        for profile in self.config.profiles:
            if profile.id == profile_id:
                return profile
        return None


# ============================================
# SPOTIFY AUTHENTICATION
# ============================================

class SpotifyAuth:
    """Handles Spotify authentication."""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.sp: Optional[spotipy.Spotify] = None
        self._current_user: Optional[Dict] = None
    
    def get_credentials(self) -> Tuple[str, str]:
        """Get Spotify API credentials, prompting if needed."""
        client_id = self.config_manager.config.spotify_client_id or SPOTIFY_CLIENT_ID
        client_secret = self.config_manager.config.spotify_client_secret or SPOTIFY_CLIENT_SECRET
        
        if not client_id or not client_secret:
            print_info("Spotify API credentials not found.")
            print("\nTo use this application, you need Spotify API credentials.")
            print("1. Go to https://developer.spotify.com/dashboard")
            print("2. Create a new application")
            print("3. Add 'http://127.0.0.1:8888/callback' as a Redirect URI")
            print("4. Copy the Client ID and Client Secret\n")
            
            if RICH_AVAILABLE:
                client_id = Prompt.ask("Enter your Spotify Client ID")
                client_secret = Prompt.ask("Enter your Spotify Client Secret")
            else:
                client_id = input("Enter your Spotify Client ID: ").strip()
                client_secret = input("Enter your Spotify Client Secret: ").strip()
            
            # Save credentials
            self.config_manager.config.spotify_client_id = client_id
            self.config_manager.config.spotify_client_secret = client_secret
            self.config_manager.save()
            
            print_success("Credentials saved!")
        
        return client_id, client_secret
    
    def authenticate(self) -> bool:
        """Authenticate with Spotify and create client."""
        try:
            client_id, client_secret = self.get_credentials()
            
            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope=" ".join(SPOTIFY_SCOPES),
                cache_path=str(TOKEN_CACHE),
                open_browser=True
            )
            
            self.sp = spotipy.Spotify(auth_manager=auth_manager)
            
            # Test the connection
            self._current_user = self.sp.current_user()
            print_success(f"Authenticated as: {self._current_user['display_name']}")
            return True
            
        except SpotifyException as e:
            print_error(f"Spotify authentication failed: {e}")
            return False
        except Exception as e:
            print_error(f"Authentication error: {e}")
            return False
    
    def get_client(self) -> Optional[spotipy.Spotify]:
        """Get the authenticated Spotify client."""
        if not self.sp:
            self.authenticate()
        return self.sp
    
    def get_current_user(self) -> Optional[Dict]:
        """Get current user info (cached)."""
        if not self._current_user and self.sp:
            self._current_user = self.sp.current_user()
        return self._current_user


# ============================================
# GLOBAL INSTANCES (initialized in main)
# ============================================

config_manager: Optional[ConfigManager] = None
spotify_auth: Optional[SpotifyAuth] = None


def init_globals():
    """Initialize global instances."""
    global config_manager, spotify_auth
    config_manager = ConfigManager()
    spotify_auth = SpotifyAuth(config_manager)
    return spotify_auth.authenticate()


# End of Part 1
# ============================================
# PART 2: PROFILE MANAGEMENT
# ============================================

"""
Profile management functions for creating, deleting, renaming,
switching, and listing profiles.
"""

from typing import Optional, List, Tuple


class ProfileManager:
    """Manages user profiles - create, delete, rename, switch, list."""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
    
    @property
    def config(self) -> Config:
        return self.config_manager.config
    
    @property
    def profiles(self) -> List[Profile]:
        return self.config.profiles
    
    @property
    def active_profile(self) -> Profile:
        return self.config_manager.get_active_profile()
    
    def save(self):
        """Save current configuration."""
        self.config_manager.save()
    
    # ============================================
    # PROFILE CRUD OPERATIONS
    # ============================================
    
    def create_profile(self, name: str) -> Profile:
        """
        Create a new profile.
        
        Args:
            name: Name for the new profile
            
        Returns:
            The newly created profile
        """
        profile_id = generate_id()
        new_profile = Profile(
            id=profile_id,
            name=name.strip()
        )
        
        self.config.profiles.append(new_profile)
        self.config.active_profile_id = profile_id
        self.save()
        
        print_success(f"Created profile: {name}")
        return new_profile
    
    def delete_profile(self, profile_id: str) -> bool:
        """
        Delete a profile by ID.
        
        Args:
            profile_id: ID of the profile to delete
            
        Returns:
            True if deleted, False otherwise
        """
        if len(self.profiles) <= 1:
            print_error("Cannot delete the last profile!")
            return False
        
        profile = self.config_manager.get_profile_by_id(profile_id)
        if not profile:
            print_error("Profile not found!")
            return False
        
        profile_name = profile.name
        self.config.profiles = [p for p in self.profiles if p.id != profile_id]
        
        # Switch to first available profile if active was deleted
        if self.config.active_profile_id == profile_id:
            self.config.active_profile_id = self.profiles[0].id
        
        self.save()
        print_success(f"Deleted profile: {profile_name}")
        return True
    
    def rename_profile(self, profile_id: str, new_name: str) -> bool:
        """
        Rename a profile.
        
        Args:
            profile_id: ID of the profile to rename
            new_name: New name for the profile
            
        Returns:
            True if renamed, False otherwise
        """
        profile = self.config_manager.get_profile_by_id(profile_id)
        if not profile:
            print_error("Profile not found!")
            return False
        
        old_name = profile.name
        profile.name = new_name.strip()
        self.save()
        
        print_success(f"Renamed '{old_name}' to '{new_name}'")
        return True
    
    def switch_profile(self, profile_id: str) -> bool:
        """
        Switch to a different profile.
        
        Args:
            profile_id: ID of the profile to switch to
            
        Returns:
            True if switched, False otherwise
        """
        profile = self.config_manager.get_profile_by_id(profile_id)
        if not profile:
            print_error("Profile not found!")
            return False
        
        if self.config.active_profile_id == profile_id:
            print_info(f"Already on profile: {profile.name}")
            return True
        
        self.config.active_profile_id = profile_id
        self.save()
        
        print_success(f"Switched to profile: {profile.name}")
        return True
    
    def duplicate_profile(self, profile_id: str, new_name: str) -> Optional[Profile]:
        """
        Duplicate an existing profile.
        
        Args:
            profile_id: ID of the profile to duplicate
            new_name: Name for the duplicated profile
            
        Returns:
            The new profile or None if failed
        """
        source_profile = self.config_manager.get_profile_by_id(profile_id)
        if not source_profile:
            print_error("Source profile not found!")
            return None
        
        new_profile_id = generate_id()
        
        # Deep copy the profile
        new_profile = Profile(
            id=new_profile_id,
            name=new_name.strip(),
            artists=[Artist.from_dict(a.to_dict()) for a in source_profile.artists],
            playlist_uri=source_profile.playlist_uri,
            playlist_name=source_profile.playlist_name,
            check_interval=source_profile.check_interval,
            last_check=None,  # Reset last check
            tracked_releases={},  # Reset tracked releases
            days_to_check=source_profile.days_to_check,
            sort_by_date=source_profile.sort_by_date,
            skip_remixes=source_profile.skip_remixes,
            skip_low_popularity=source_profile.skip_low_popularity,
            min_popularity=source_profile.min_popularity,
            skip_long_albums=source_profile.skip_long_albums,
            max_songs=source_profile.max_songs,
            limit_songs_per_album=source_profile.limit_songs_per_album,
            max_songs_per_album=source_profile.max_songs_per_album,
        )
        
        self.config.profiles.append(new_profile)
        self.config.active_profile_id = new_profile_id
        self.save()
        
        print_success(f"Duplicated '{source_profile.name}' as '{new_name}'")
        return new_profile
    
    # ============================================
    # PROFILE LISTING & DISPLAY
    # ============================================
    
    def list_profiles(self) -> List[Tuple[str, str, int, str, bool]]:
        """
        Get list of all profiles with their info.
        
        Returns:
            List of tuples: (id, name, artist_count, playlist_name, is_active)
        """
        result = []
        for profile in self.profiles:
            result.append((
                profile.id,
                profile.name,
                len(profile.artists),
                profile.playlist_name or "Not set",
                profile.id == self.config.active_profile_id
            ))
        return result
    
    def display_profiles(self):
        """Display all profiles in a formatted table."""
        profiles = self.list_profiles()
        
        if RICH_AVAILABLE:
            table = Table(title="Profiles", show_header=True, header_style="bold cyan")
            table.add_column("#", style="dim", width=3)
            table.add_column("Name", style="white")
            table.add_column("Artists", justify="right")
            table.add_column("Playlist", style="dim")
            table.add_column("Status", justify="center")
            
            for idx, (pid, name, artists, playlist, is_active) in enumerate(profiles, 1):
                status = "[bold green]● Active[/]" if is_active else "[dim]○[/]"
                table.add_row(
                    str(idx),
                    name,
                    str(artists),
                    playlist[:30] + "..." if len(playlist) > 30 else playlist,
                    status
                )
            
            console.print(table)
        else:
            print("\n=== Profiles ===")
            for idx, (pid, name, artists, playlist, is_active) in enumerate(profiles, 1):
                status = "[ACTIVE]" if is_active else ""
                print(f"  {idx}. {name} - {artists} artists - {playlist} {status}")
            print()
    
    def display_profile_details(self, profile: Optional[Profile] = None):
        """Display detailed information about a profile."""
        if profile is None:
            profile = self.active_profile
        
        is_active = profile.id == self.config.active_profile_id
        last_check = "Never" if not profile.last_check else \
            datetime.fromtimestamp(profile.last_check).strftime("%Y-%m-%d %H:%M")
        
        if RICH_AVAILABLE:
            # Create panel with profile details
            details = [
                f"[bold]Name:[/] {profile.name}",
                f"[bold]Status:[/] {'[green]Active[/]' if is_active else '[dim]Inactive[/]'}",
                f"[bold]Artists:[/] {len(profile.artists)}",
                f"[bold]Playlist:[/] {profile.playlist_name or '[red]Not configured[/]'}",
                f"[bold]Last Check:[/] {last_check}",
                f"[bold]Tracked Releases:[/] {len(profile.tracked_releases)}",
                f"[bold]Tracked Tracks:[/] {len(profile.tracked_tracks)}",
                "",
                "[bold cyan]Settings:[/]",
                f"  Check Interval: {profile.check_interval}h",
                f"  Days to Check: {profile.days_to_check if profile.days_to_check > 0 else 'All time'}",
                f"  Sort by Date: {'Yes' if profile.sort_by_date else 'No'}",
                f"  Skip Remixes: {'Yes' if profile.skip_remixes else 'No'}",
                f"  Skip Low Popularity: {'Yes' if profile.skip_low_popularity else 'No'}"
                    + (f" (<{profile.min_popularity})" if profile.skip_low_popularity else ""),
                f"  Skip Long Albums: {'Yes' if profile.skip_long_albums else 'No'}"
                    + (f" (>{profile.max_songs} tracks)" if profile.skip_long_albums else ""),
                f"  Limit per Album: {'Yes' if profile.limit_songs_per_album else 'No'}"
                    + (f" ({profile.max_songs_per_album} max)" if profile.limit_songs_per_album else ""),
            ]
            
            panel = Panel(
                "\n".join(details),
                title=f"[bold]Profile: {profile.name}[/]",
                border_style="cyan"
            )
            console.print(panel)
        else:
            print(f"\n=== Profile: {profile.name} ===")
            print(f"  Status: {'Active' if is_active else 'Inactive'}")
            print(f"  Artists: {len(profile.artists)}")
            print(f"  Playlist: {profile.playlist_name or 'Not configured'}")
            print(f"  Last Check: {last_check}")
            print(f"  Tracked Releases: {len(profile.tracked_releases)}")
            print("\n  Settings:")
            print(f"    Check Interval: {profile.check_interval}h")
            print(f"    Days to Check: {profile.days_to_check if profile.days_to_check > 0 else 'All time'}")
            print(f"    Sort by Date: {'Yes' if profile.sort_by_date else 'No'}")
            print(f"    Skip Remixes: {'Yes' if profile.skip_remixes else 'No'}")
            print(f"    Skip Low Popularity: {'Yes' if profile.skip_low_popularity else 'No'}")
            print(f"    Skip Long Albums: {'Yes' if profile.skip_long_albums else 'No'}")
            print(f"    Limit per Album: {'Yes' if profile.limit_songs_per_album else 'No'}")
            print()
    
    # ============================================
    # PROFILE SETTINGS
    # ============================================
    
    def update_profile_setting(self, profile: Profile, setting: str, value: any) -> bool:
        """
        Update a profile setting.
        
        Args:
            profile: Profile to update
            setting: Setting name
            value: New value
            
        Returns:
            True if updated successfully
        """
        valid_settings = {
            'check_interval': int,
            'days_to_check': int,
            'sort_by_date': bool,
            'skip_remixes': bool,
            'skip_low_popularity': bool,
            'min_popularity': int,
            'skip_long_albums': bool,
            'max_songs': int,
            'limit_songs_per_album': bool,
            'max_songs_per_album': int,
            'playlist_uri': str,
            'playlist_name': str,
        }
        
        if setting not in valid_settings:
            print_error(f"Unknown setting: {setting}")
            return False
        
        expected_type = valid_settings[setting]
        try:
            if expected_type == bool and isinstance(value, str):
                value = value.lower() in ('true', 'yes', '1', 'on')
            else:
                value = expected_type(value)
            
            setattr(profile, setting, value)
            self.save()
            print_success(f"Updated {setting} = {value}")
            return True
        except (ValueError, TypeError) as e:
            print_error(f"Invalid value for {setting}: {e}")
            return False
    
    def reset_tracked_releases(self, profile: Optional[Profile] = None, mode: str = "all") -> bool:
        """
        Reset tracked data for a profile.

        Args:
            profile: Profile to reset (defaults to active)
            mode: "all" | "tracks" | "releases"

        Returns:
            True if reset successfully
        """
        if profile is None:
            profile = self.active_profile

        release_count = len(profile.tracked_releases)
        track_count = len(getattr(profile, 'tracked_tracks', {}))

        if mode == "all":
            profile.tracked_releases = {}
            profile.tracked_tracks = {}
            self.save()
            print_success(f"Cleared {release_count} releases and {track_count} tracks from '{profile.name}'")

        elif mode == "tracks":
            profile.tracked_tracks = {}
            self.save()
            print_success(f"Cleared {track_count} tracked tracks from '{profile.name}'")
            print_info("Previously filtered songs can now be re-added")

        elif mode == "releases":
            profile.tracked_releases = {}
            self.save()
            print_success(f"Cleared {release_count} tracked releases from '{profile.name}'")
            print_info("Albums will be re-scanned, but already-tracked songs won't be re-added")

        return True
    
    # ============================================
    # ARTIST MANAGEMENT IN PROFILE
    # ============================================
    
    def add_artist_to_profile(self, profile: Profile, artist: Artist) -> bool:
        """
        Add an artist to a profile.
        
        Args:
            profile: Profile to add artist to
            artist: Artist to add
            
        Returns:
            True if added, False if already exists
        """
        # Check if already exists
        for existing in profile.artists:
            if existing.uri == artist.uri:
                print_warning(f"Artist '{artist.name}' is already tracked")
                return False
        
        profile.artists.append(artist)
        self.save()
        print_success(f"Added '{artist.name}' to tracking")
        return True
    
    def remove_artist_from_profile(self, profile: Profile, artist_uri: str) -> bool:
        """
        Remove an artist from a profile.
        
        Args:
            profile: Profile to remove artist from
            artist_uri: URI of the artist to remove
            
        Returns:
            True if removed, False if not found
        """
        for i, artist in enumerate(profile.artists):
            if artist.uri == artist_uri:
                removed = profile.artists.pop(i)
                self.save()
                print_success(f"Removed '{removed.name}' from tracking")
                return True
        
        print_error("Artist not found in profile")
        return False
    
    def clear_all_artists(self, profile: Profile) -> int:
        """
        Remove all artists from a profile.
        
        Args:
            profile: Profile to clear
            
        Returns:
            Number of artists removed
        """
        count = len(profile.artists)
        profile.artists = []
        self.save()
        print_success(f"Removed {count} artists from '{profile.name}'")
        return count
    
    def list_artists(self, profile: Optional[Profile] = None) -> List[Artist]:
        """
        Get list of artists in a profile.
        
        Args:
            profile: Profile to list artists from (defaults to active)
            
        Returns:
            List of Artist objects
        """
        if profile is None:
            profile = self.active_profile
        return profile.artists
    
    def display_artists(self, profile: Optional[Profile] = None):
        """Display artists in a profile as a formatted table."""
        if profile is None:
            profile = self.active_profile
        
        artists = profile.artists
        
        if not artists:
            print_info(f"No artists tracked in '{profile.name}'")
            return
        
        if RICH_AVAILABLE:
            table = Table(
                title=f"Tracked Artists ({len(artists)})",
                show_header=True,
                header_style="bold cyan"
            )
            table.add_column("#", style="dim", width=4)
            table.add_column("Name", style="white")
            table.add_column("Followers", justify="right")
            table.add_column("URI", style="dim")
            
            for idx, artist in enumerate(artists, 1):
                followers = format_number(artist.followers) if artist.followers else "Unknown"
                table.add_row(
                    str(idx),
                    artist.name,
                    followers,
                    artist.uri.split(":")[-1][:12] + "..."
                )
            
            console.print(table)
        else:
            print(f"\n=== Tracked Artists ({len(artists)}) ===")
            for idx, artist in enumerate(artists, 1):
                followers = format_number(artist.followers) if artist.followers else "Unknown"
                print(f"  {idx}. {artist.name} ({followers} followers)")
            print()
    
    def find_artist_by_index(self, profile: Profile, index: int) -> Optional[Artist]:
        """
        Find an artist by their display index (1-based).
        
        Args:
            profile: Profile to search in
            index: 1-based index
            
        Returns:
            Artist or None
        """
        if 1 <= index <= len(profile.artists):
            return profile.artists[index - 1]
        return None
    
    def find_artist_by_name(self, profile: Profile, name: str) -> Optional[Artist]:
        """
        Find an artist by name (case-insensitive partial match).
        
        Args:
            profile: Profile to search in
            name: Name to search for
            
        Returns:
            First matching artist or None
        """
        name_lower = name.lower()
        for artist in profile.artists:
            if name_lower in artist.name.lower():
                return artist
        return None


# ============================================
# INTERACTIVE PROFILE MENU
# ============================================

class ProfileMenu:
    """Interactive menu for profile management."""
    
    def __init__(self, profile_manager: ProfileManager):
        self.pm = profile_manager
    
    def prompt(self, message: str, default: str = "") -> str:
        """Prompt user for input."""
        if RICH_AVAILABLE:
            return Prompt.ask(message, default=default) if default else Prompt.ask(message)
        else:
            prompt_text = f"{message} [{default}]: " if default else f"{message}: "
            result = input(prompt_text).strip()
            return result if result else default
    
    def confirm(self, message: str, default: bool = False) -> bool:
        """Prompt user for confirmation."""
        if RICH_AVAILABLE:
            return Confirm.ask(message, default=default)
        else:
            suffix = " [Y/n]: " if default else " [y/N]: "
            result = input(message + suffix).strip().lower()
            if not result:
                return default
            return result in ('y', 'yes')
    
    def prompt_int(self, message: str, default: int = 0, min_val: int = None, max_val: int = None) -> int:
        """Prompt user for integer input."""
        while True:
            if RICH_AVAILABLE:
                try:
                    value = IntPrompt.ask(message, default=default)
                except KeyboardInterrupt:
                    return default
            else:
                try:
                    prompt_text = f"{message} [{default}]: "
                    result = input(prompt_text).strip()
                    value = int(result) if result else default
                except ValueError:
                    print_error("Please enter a valid number")
                    continue
            
            if min_val is not None and value < min_val:
                print_error(f"Value must be at least {min_val}")
                continue
            if max_val is not None and value > max_val:
                print_error(f"Value must be at most {max_val}")
                continue
            
            return value
    
    def select_profile(self, prompt_message: str = "Select profile") -> Optional[Profile]:
        """
        Let user select a profile from a numbered list.
        
        Returns:
            Selected profile or None if cancelled
        """
        profiles = self.pm.list_profiles()
        
        if not profiles:
            print_error("No profiles available")
            return None
        
        self.pm.display_profiles()
        
        try:
            choice = self.prompt_int(
                f"{prompt_message} (1-{len(profiles)}, 0 to cancel)",
                default=0,
                min_val=0,
                max_val=len(profiles)
            )
            
            if choice == 0:
                return None
            
            profile_id = profiles[choice - 1][0]
            return self.pm.config_manager.get_profile_by_id(profile_id)
            
        except (ValueError, IndexError):
            print_error("Invalid selection")
            return None
    
    def run_create_profile(self):
        """Interactive profile creation."""
        name = self.prompt("Enter profile name")
        if not name:
            print_warning("Cancelled")
            return
        
        self.pm.create_profile(name)
    
    def run_delete_profile(self):
        """Interactive profile deletion."""
        profile = self.select_profile("Select profile to delete")
        if not profile:
            return
        
        if not self.confirm(f"Are you sure you want to delete '{profile.name}'?"):
            print_warning("Cancelled")
            return
        
        self.pm.delete_profile(profile.id)
    
    def run_rename_profile(self):
        """Interactive profile renaming."""
        profile = self.select_profile("Select profile to rename")
        if not profile:
            return
        
        new_name = self.prompt("Enter new name", default=profile.name)
        if not new_name or new_name == profile.name:
            print_warning("Cancelled")
            return
        
        self.pm.rename_profile(profile.id, new_name)
    
    def run_switch_profile(self):
        """Interactive profile switching."""
        profile = self.select_profile("Select profile to switch to")
        if not profile:
            return
        
        self.pm.switch_profile(profile.id)
    
    def run_duplicate_profile(self):
        """Interactive profile duplication."""
        profile = self.select_profile("Select profile to duplicate")
        if not profile:
            return
        
        new_name = self.prompt("Enter name for the copy", default=f"{profile.name} (Copy)")
        if not new_name:
            print_warning("Cancelled")
            return
        
        self.pm.duplicate_profile(profile.id, new_name)
    
    def run_edit_settings(self):
        """Interactive settings editor for active profile."""
        profile = self.pm.active_profile
        
        print_info(f"Editing settings for: {profile.name}")
        print("Press Enter to keep current value\n")
        
        # Check interval
        profile.check_interval = self.prompt_int(
            f"Check interval in hours (current: {profile.check_interval})",
            default=profile.check_interval,
            min_val=1,
            max_val=168
        )
        
        # Days to check
        profile.days_to_check = self.prompt_int(
            f"Days to look back, 0=all time (current: {profile.days_to_check})",
            default=profile.days_to_check,
            min_val=0,
            max_val=365
        )
        
        # Boolean settings
        profile.sort_by_date = self.confirm(
            "Sort playlist by release date after adding?",
            default=profile.sort_by_date
        )
        
        profile.skip_remixes = self.confirm(
            "Skip remixes and variants?",
            default=profile.skip_remixes
        )
        
        profile.skip_low_popularity = self.confirm(
            "Skip low popularity releases?",
            default=profile.skip_low_popularity
        )
        
        if profile.skip_low_popularity:
            profile.min_popularity = self.prompt_int(
                f"Minimum popularity (0-100, current: {profile.min_popularity})",
                default=profile.min_popularity,
                min_val=0,
                max_val=100
            )
        
        profile.skip_long_albums = self.confirm(
            "Skip albums with too many tracks?",
            default=profile.skip_long_albums
        )
        
        if profile.skip_long_albums:
            profile.max_songs = self.prompt_int(
                f"Maximum tracks per album (current: {profile.max_songs})",
                default=profile.max_songs,
                min_val=1,
                max_val=200
            )
        
        profile.limit_songs_per_album = self.confirm(
            "Limit songs added per album?",
            default=profile.limit_songs_per_album
        )
        
        if profile.limit_songs_per_album:
            profile.max_songs_per_album = self.prompt_int(
                f"Max songs per album (current: {profile.max_songs_per_album})",
                default=profile.max_songs_per_album,
                min_val=1,
                max_val=50
            )
        
        self.pm.save()
        print_success("Settings saved!")
    
    def run_reset_tracked(self):
        """Interactive reset of tracked releases."""
        profile = self.pm.active_profile
        count = len(profile.tracked_releases)
        
        if count == 0:
            print_info("No tracked releases to reset")
            return
        
        if self.confirm(f"Reset {count} tracked releases for '{profile.name}'?"):
            self.pm.reset_tracked_releases(profile)


# End of Part 2
# ============================================
# PART 3: SPOTIFY API HELPERS
# ============================================

"""
Spotify API wrapper functions for artist search, details, 
releases, and general API operations.
"""

from typing import Optional, List, Dict, Any, Generator
import time


class SpotifyAPIError(Exception):
    """Custom exception for Spotify API errors."""
    pass


class SpotifyAPI:
    """
    Wrapper around Spotipy client with helper methods,
    error handling, caching, and rate limiting.
    """
    
    def __init__(self, spotify_auth: SpotifyAuth):
        self.auth = spotify_auth
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, float] = {}
        self._cache_max_age = 300  # 5 minutes default cache TTL
        self._rate_limit_delay = 0.1  # Delay between API calls
        self._last_request_time = 0
    
    @property
    def client(self) -> spotipy.Spotify:
        """Get the authenticated Spotify client."""
        sp = self.auth.get_client()
        if not sp:
            raise SpotifyAPIError("Not authenticated with Spotify")
        return sp
    
    def _rate_limit(self):
        """Apply rate limiting between API calls."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._rate_limit_delay:
            time.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = time.time()
    
    def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""
        if key in self._cache:
            if time.time() - self._cache_ttl.get(key, 0) < self._cache_max_age:
                return self._cache[key]
            else:
                # Expired, remove from cache
                del self._cache[key]
                del self._cache_ttl[key]
        return None
    
    def _set_cached(self, key: str, value: Any):
        """Set value in cache with timestamp."""
        self._cache[key] = value
        self._cache_ttl[key] = time.time()
        
        # Limit cache size
        if len(self._cache) > 500:
            # Remove oldest entries
            oldest_keys = sorted(self._cache_ttl.keys(), key=lambda k: self._cache_ttl[k])[:100]
            for k in oldest_keys:
                del self._cache[k]
                del self._cache_ttl[k]
    
    def clear_cache(self, key: Optional[str] = None):
        """Clear cache, optionally for specific key."""
        if key:
            self._cache.pop(key, None)
            self._cache_ttl.pop(key, None)
        else:
            self._cache.clear()
            self._cache_ttl.clear()
    
    def _handle_api_error(self, e: Exception, context: str = "API call"):
        """Handle and re-raise API errors with context."""
        if isinstance(e, SpotifyException):
            if e.http_status == 401:
                raise SpotifyAPIError(f"{context}: Authentication expired. Please re-authenticate.")
            elif e.http_status == 403:
                raise SpotifyAPIError(f"{context}: Access forbidden. Check your permissions.")
            elif e.http_status == 404:
                raise SpotifyAPIError(f"{context}: Resource not found.")
            elif e.http_status == 429:
                # Rate limited - wait and could retry
                retry_after = int(e.headers.get('Retry-After', 5))
                raise SpotifyAPIError(f"{context}: Rate limited. Try again in {retry_after} seconds.")
            else:
                raise SpotifyAPIError(f"{context}: Spotify error {e.http_status} - {e.msg}")
        else:
            raise SpotifyAPIError(f"{context}: {str(e)}")
    
    # ============================================
    # USER OPERATIONS
    # ============================================
    
    def get_current_user(self) -> Dict:
        """Get current user's profile."""
        cache_key = "current_user"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            self._rate_limit()
            user = self.client.current_user()
            self._set_cached(cache_key, user)
            return user
        except Exception as e:
            self._handle_api_error(e, "Get current user")
    
    def get_current_user_id(self) -> str:
        """Get current user's Spotify ID."""
        return self.get_current_user()['id']
    
    # ============================================
    # ARTIST OPERATIONS
    # ============================================
    
    def search_artists(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Search for artists by name.
        
        Args:
            query: Search query
            limit: Maximum results to return
            
        Returns:
            List of artist dictionaries
        """
        if not query or len(query.strip()) < 2:
            return []
        
        try:
            self._rate_limit()
            results = self.client.search(
                q=query.strip(),
                type='artist',
                limit=min(limit, API_LIMITS["SEARCH_LIMIT"])
            )
            return results.get('artists', {}).get('items', [])
        except Exception as e:
            self._handle_api_error(e, "Search artists")
            return []
    
    def get_artist(self, artist_uri: str) -> Optional[Dict]:
        """
        Get artist details by URI/ID.
        
        Args:
            artist_uri: Spotify artist URI, URL, or ID
            
        Returns:
            Artist dictionary or None
        """
        artist_id = parse_spotify_uri(artist_uri, "artist")
        if not artist_id:
            print_error(f"Invalid artist URI: {artist_uri}")
            return None
        
        cache_key = f"artist:{artist_id}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            self._rate_limit()
            artist = self.client.artist(artist_id)
            self._set_cached(cache_key, artist)
            return artist
        except Exception as e:
            self._handle_api_error(e, f"Get artist {artist_id}")
            return None
    
    def get_multiple_artists(self, artist_ids: List[str]) -> List[Dict]:
        """
        Get multiple artists in batch.
        
        Args:
            artist_ids: List of artist IDs
            
        Returns:
            List of artist dictionaries
        """
        if not artist_ids:
            return []
        
        # Spotify allows max 50 artists per request
        results = []
        for i in range(0, len(artist_ids), 50):
            batch = artist_ids[i:i+50]
            try:
                self._rate_limit()
                response = self.client.artists(batch)
                results.extend(response.get('artists', []))
            except Exception as e:
                self._handle_api_error(e, "Get multiple artists")
        
        return [a for a in results if a]  # Filter None values
    
    def get_artist_albums(
        self,
        artist_uri: str,
        include_groups: str = "album,single",
        limit: int = 50
    ) -> List[Dict]:
        """
        Get all albums/singles from an artist.
        
        Args:
            artist_uri: Artist URI/ID
            include_groups: Types to include (album, single, appears_on, compilation)
            limit: Max per request (will paginate for all)
            
        Returns:
            List of album dictionaries
        """
        artist_id = parse_spotify_uri(artist_uri, "artist")
        if not artist_id:
            return []
        
        all_albums = []
        offset = 0
        
        try:
            while True:
                self._rate_limit()
                response = self.client.artist_albums(
                    artist_id,
                    include_groups=include_groups,
                    limit=min(limit, API_LIMITS["ARTIST_ALBUMS_LIMIT"]),
                    offset=offset
                )
                
                items = response.get('items', [])
                if not items:
                    break
                
                all_albums.extend(items)
                
                # Check if there are more pages
                if response.get('next'):
                    offset += len(items)
                else:
                    break
            
            return all_albums
            
        except Exception as e:
            self._handle_api_error(e, f"Get artist albums for {artist_id}")
            return []
    
    def get_artist_top_tracks(self, artist_uri: str, country: str = "US") -> List[Dict]:
        """
        Get artist's top tracks.
        
        Args:
            artist_uri: Artist URI/ID
            country: Country code for top tracks
            
        Returns:
            List of track dictionaries
        """
        artist_id = parse_spotify_uri(artist_uri, "artist")
        if not artist_id:
            return []
        
        try:
            self._rate_limit()
            response = self.client.artist_top_tracks(artist_id, country=country)
            return response.get('tracks', [])
        except Exception as e:
            self._handle_api_error(e, f"Get top tracks for {artist_id}")
            return []
    
    def artist_to_model(self, artist_data: Dict) -> Artist:
        """
        Convert Spotify artist dict to Artist model.
        
        Args:
            artist_data: Raw Spotify artist dictionary
            
        Returns:
            Artist dataclass instance
        """
        return Artist(
            uri=artist_data.get('uri', ''),
            name=artist_data.get('name', 'Unknown'),
            image=artist_data.get('images', [{}])[0].get('url') if artist_data.get('images') else None,
            followers=artist_data.get('followers', {}).get('total'),
            last_follower_update=time.time()
        )
    
    # ============================================
    # ALBUM OPERATIONS
    # ============================================
    
    def get_album(self, album_uri: str) -> Optional[Dict]:
        """
        Get album details by URI/ID.
        
        Args:
            album_uri: Spotify album URI, URL, or ID
            
        Returns:
            Album dictionary or None
        """
        album_id = parse_spotify_uri(album_uri, "album")
        if not album_id:
            return None
        
        cache_key = f"album:{album_id}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            self._rate_limit()
            album = self.client.album(album_id)
            self._set_cached(cache_key, album)
            return album
        except Exception as e:
            self._handle_api_error(e, f"Get album {album_id}")
            return None
    
    def get_album_tracks(self, album_uri: str) -> List[Dict]:
        """
        Get all tracks from an album.
        
        Args:
            album_uri: Album URI/ID
            
        Returns:
            List of track dictionaries
        """
        album_id = parse_spotify_uri(album_uri, "album")
        if not album_id:
            return []
        
        all_tracks = []
        offset = 0
        
        try:
            while True:
                self._rate_limit()
                response = self.client.album_tracks(album_id, limit=50, offset=offset)
                
                items = response.get('items', [])
                if not items:
                    break
                
                all_tracks.extend(items)
                
                if response.get('next'):
                    offset += len(items)
                else:
                    break
            
            return all_tracks
            
        except Exception as e:
            self._handle_api_error(e, f"Get album tracks for {album_id}")
            return []
    
    # ============================================
    # TRACK OPERATIONS
    # ============================================
    
    def get_track(self, track_uri: str) -> Optional[Dict]:
        """
        Get track details by URI/ID.
        
        Args:
            track_uri: Track URI/ID
            
        Returns:
            Track dictionary or None
        """
        track_id = parse_spotify_uri(track_uri, "track")
        if not track_id:
            return None
        
        try:
            self._rate_limit()
            return self.client.track(track_id)
        except Exception as e:
            self._handle_api_error(e, f"Get track {track_id}")
            return None
    
    def get_multiple_tracks(self, track_uris: List[str]) -> List[Dict]:
        """
        Get multiple tracks in batch.
        
        Args:
            track_uris: List of track URIs/IDs
            
        Returns:
            List of track dictionaries
        """
        if not track_uris:
            return []
        
        # Parse URIs to IDs
        track_ids = []
        for uri in track_uris:
            tid = parse_spotify_uri(uri, "track")
            if tid:
                track_ids.append(tid)
        
        if not track_ids:
            return []
        
        # Spotify allows max 50 tracks per request
        results = []
        for i in range(0, len(track_ids), API_LIMITS["TRACKS_PER_REQUEST"]):
            batch = track_ids[i:i + API_LIMITS["TRACKS_PER_REQUEST"]]
            try:
                self._rate_limit()
                response = self.client.tracks(batch)
                results.extend(response.get('tracks', []))
            except Exception as e:
                self._handle_api_error(e, "Get multiple tracks")
        
        return [t for t in results if t]  # Filter None values
    
    def get_tracks_audio_features(self, track_uris: List[str]) -> List[Dict]:
        """
        Get audio features for multiple tracks.
        
        Args:
            track_uris: List of track URIs/IDs
            
        Returns:
            List of audio feature dictionaries
        """
        if not track_uris:
            return []
        
        track_ids = [parse_spotify_uri(uri, "track") for uri in track_uris]
        track_ids = [tid for tid in track_ids if tid]
        
        if not track_ids:
            return []
        
        results = []
        for i in range(0, len(track_ids), 100):  # Max 100 per request for audio features
            batch = track_ids[i:i+100]
            try:
                self._rate_limit()
                response = self.client.audio_features(batch)
                results.extend(response)
            except Exception as e:
                self._handle_api_error(e, "Get audio features")
        
        return [f for f in results if f]
    
    # ============================================
    # PLAYLIST OPERATIONS (Basic - more in Part 4)
    # ============================================
    
    def get_user_playlists(self, limit: int = 50) -> List[Dict]:
        """
        Get current user's playlists.
        
        Args:
            limit: Maximum playlists to fetch (0 = all)
            
        Returns:
            List of playlist dictionaries
        """
        all_playlists = []
        offset = 0
        user_id = self.get_current_user_id()
        
        try:
            while True:
                self._rate_limit()
                response = self.client.current_user_playlists(limit=50, offset=offset)
                
                items = response.get('items', [])
                if not items:
                    break
                
                # Filter to only user's own playlists
                own_playlists = [p for p in items if p.get('owner', {}).get('id') == user_id]
                all_playlists.extend(own_playlists)
                
                # Check limits and pagination
                if limit > 0 and len(all_playlists) >= limit:
                    all_playlists = all_playlists[:limit]
                    break
                
                if response.get('next'):
                    offset += len(items)
                else:
                    break
            
            return all_playlists
            
        except Exception as e:
            self._handle_api_error(e, "Get user playlists")
            return []
    
    def get_playlist(self, playlist_uri: str, skip_cache: bool = False) -> Optional[Dict]:
        """
        Get playlist details.
        
        Args:
            playlist_uri: Playlist URI/ID
            skip_cache: Force fresh fetch
            
        Returns:
            Playlist dictionary or None
        """
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id:
            return None
        
        cache_key = f"playlist:{playlist_id}"
        if not skip_cache:
            cached = self._get_cached(cache_key)
            if cached:
                return cached
        
        try:
            self._rate_limit()
            playlist = self.client.playlist(
                playlist_id,
                fields="id,name,description,owner,tracks(total),uri,images"
            )
            self._set_cached(cache_key, playlist)
            return playlist
        except Exception as e:
            self._handle_api_error(e, f"Get playlist {playlist_id}")
            return None
    
    def create_playlist(
        self,
        name: str,
        description: str = "",
        public: bool = False
    ) -> Optional[Dict]:
        """
        Create a new playlist.
        
        Args:
            name: Playlist name
            description: Playlist description
            public: Whether playlist is public
            
        Returns:
            Created playlist dictionary or None
        """
        try:
            user_id = self.get_current_user_id()
            self._rate_limit()
            playlist = self.client.user_playlist_create(
                user_id,
                name,
                public=public,
                description=description
            )
            return playlist
        except Exception as e:
            self._handle_api_error(e, "Create playlist")
            return None


# ============================================
# ARTIST SEARCH & SELECTION HELPERS
# ============================================

class ArtistSearcher:
    """Helper class for searching and selecting artists."""
    
    def __init__(self, spotify_api: SpotifyAPI):
        self.api = spotify_api
    
    def search_and_display(self, query: str) -> List[Dict]:
        """
        Search for artists and display results.
        
        Args:
            query: Search query
            
        Returns:
            List of found artists
        """
        print_info(f"Searching for: {query}")
        
        artists = self.api.search_artists(query)
        
        if not artists:
            print_warning("No artists found")
            return []
        
        self._display_search_results(artists)
        return artists
    
    def _display_search_results(self, artists: List[Dict]):
        """Display search results in a formatted list."""
        if RICH_AVAILABLE:
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("#", style="dim", width=3)
            table.add_column("Name", style="white")
            table.add_column("Followers", justify="right")
            table.add_column("Genres", style="dim")
            
            for idx, artist in enumerate(artists, 1):
                followers = format_number(artist.get('followers', {}).get('total', 0))
                genres = ", ".join(artist.get('genres', [])[:3]) or "Unknown"
                table.add_row(
                    str(idx),
                    artist['name'],
                    followers,
                    genres[:40] + "..." if len(genres) > 40 else genres
                )
            
            console.print(table)
        else:
            print("\n=== Search Results ===")
            for idx, artist in enumerate(artists, 1):
                followers = format_number(artist.get('followers', {}).get('total', 0))
                genres = ", ".join(artist.get('genres', [])[:2]) or "Unknown"
                print(f"  {idx}. {artist['name']} ({followers} followers) - {genres}")
            print()
    
    def select_artist(self, artists: List[Dict]) -> Optional[Dict]:
        """
        Let user select an artist from search results.
        
        Args:
            artists: List of artist dicts from search
            
        Returns:
            Selected artist dict or None
        """
        if not artists:
            return None
        
        try:
            if RICH_AVAILABLE:
                choice = IntPrompt.ask(
                    f"Select artist (1-{len(artists)}, 0 to cancel)",
                    default=0
                )
            else:
                choice = int(input(f"Select artist (1-{len(artists)}, 0 to cancel): ") or "0")
            
            if choice == 0:
                return None
            
            if 1 <= choice <= len(artists):
                return artists[choice - 1]
            
            print_error("Invalid selection")
            return None
            
        except (ValueError, KeyboardInterrupt):
            return None
    
    def interactive_search(self) -> Optional[Artist]:
        """
        Run interactive artist search loop.
        
        Returns:
            Selected Artist model or None
        """
        while True:
            if RICH_AVAILABLE:
                query = Prompt.ask("\nEnter artist name to search (or 'q' to quit)")
            else:
                query = input("\nEnter artist name to search (or 'q' to quit): ").strip()
            
            if not query or query.lower() == 'q':
                return None
            
            artists = self.search_and_display(query)
            
            if artists:
                selected = self.select_artist(artists)
                if selected:
                    return self.api.artist_to_model(selected)
                
                # Ask if they want to search again
                if RICH_AVAILABLE:
                    if not Confirm.ask("Search again?", default=True):
                        return None
                else:
                    if input("Search again? [Y/n]: ").lower() == 'n':
                        return None


# ============================================
# RELEASE FETCHER
# ============================================

class ReleaseFetcher:
    """Fetches and processes new releases from artists."""
    
    def __init__(self, spotify_api: SpotifyAPI):
        self.api = spotify_api
    
    def get_artist_releases(
        self,
        artist_uri: str,
        include_groups: str = "album,single"
    ) -> List[Dict]:
        """
        Get all releases from an artist.
        
        Args:
            artist_uri: Artist URI
            include_groups: Types to include
            
        Returns:
            List of release (album) dictionaries
        """
        return self.api.get_artist_albums(artist_uri, include_groups=include_groups)
    
    def get_release_details(self, release_uri: str) -> Optional[Dict]:
        """
        Get detailed info about a release including tracks.
        
        Args:
            release_uri: Album URI
            
        Returns:
            Album dictionary with full details
        """
        return self.api.get_album(release_uri)
    
    def filter_releases_by_date(
        self,
        releases: List[Dict],
        days_back: int = 30
    ) -> List[Dict]:
        """
        Filter releases to only those within date range.
        
        Args:
            releases: List of release dicts
            days_back: Number of days to look back (0 = all)
            
        Returns:
            Filtered list of releases
        """
        if days_back == 0:
            return releases
        
        filtered = []
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        for release in releases:
            release_date = parse_release_date(release.get('release_date', ''))
            if release_date and release_date >= cutoff_date:
                filtered.append(release)
        
        return filtered
    
    def get_new_releases_for_artist(
        self,
        artist: Artist,
        days_back: int = 30,
        tracked_releases: Dict[str, float] = None
    ) -> List[Dict]:
        """
        Get new releases for an artist that haven't been tracked yet.
        
        Args:
            artist: Artist model
            days_back: Days to look back
            tracked_releases: Dict of already tracked release URIs
            
        Returns:
            List of new release dicts
        """
        tracked = tracked_releases or {}
        
        # Get all releases
        all_releases = self.get_artist_releases(artist.uri)
        
        # Filter by date
        recent_releases = self.filter_releases_by_date(all_releases, days_back)
        
        # Filter out already tracked
        new_releases = [
            r for r in recent_releases 
            if r.get('uri') not in tracked
        ]
        
        return new_releases
    
    def display_releases(self, releases: List[Dict], title: str = "Releases"):
        """Display releases in a formatted table."""
        if not releases:
            print_info("No releases to display")
            return
        
        if RICH_AVAILABLE:
            table = Table(title=title, show_header=True, header_style="bold cyan")
            table.add_column("#", style="dim", width=3)
            table.add_column("Name", style="white")
            table.add_column("Type", style="dim")
            table.add_column("Tracks", justify="right")
            table.add_column("Release Date")
            
            for idx, release in enumerate(releases, 1):
                table.add_row(
                    str(idx),
                    release.get('name', 'Unknown')[:40],
                    release.get('album_type', 'unknown').title(),
                    str(release.get('total_tracks', '?')),
                    release.get('release_date', 'Unknown')
                )
            
            console.print(table)
        else:
            print(f"\n=== {title} ===")
            for idx, release in enumerate(releases, 1):
                print(
                    f"  {idx}. {release.get('name', 'Unknown')} "
                    f"({release.get('album_type', 'unknown')}) - "
                    f"{release.get('total_tracks', '?')} tracks - "
                    f"{release.get('release_date', 'Unknown')}"
                )
            print()


# ============================================
# PROGRESS DISPLAY HELPERS
# ============================================

class ProgressDisplay:
    """Helper for displaying progress during long operations."""
    
    def __init__(self):
        self._progress = None
        self._task_id = None
    
    def start(self, description: str, total: int = 100):
        """Start a progress display."""
        if RICH_AVAILABLE:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("[dim]{task.fields[status]}"),
                console=console
            )
            self._progress.start()
            self._task_id = self._progress.add_task(
                description,
                total=total,
                status=""
            )
        else:
            print(f"\n{description}...")
    
    def update(self, advance: int = 1, status: str = ""):
        """Update progress."""
        if RICH_AVAILABLE and self._progress and self._task_id is not None:
            self._progress.update(self._task_id, advance=advance, status=status)
        elif status:
            print(f"  {status}")
    
    def set_description(self, description: str):
        """Update the description."""
        if RICH_AVAILABLE and self._progress and self._task_id is not None:
            self._progress.update(self._task_id, description=description)
        else:
            print(f"  {description}")
    
    def stop(self):
        """Stop the progress display."""
        if RICH_AVAILABLE and self._progress:
            self._progress.stop()
            self._progress = None
            self._task_id = None


def create_progress_context(description: str, total: int = 100):
    """
    Context manager for progress display.
    
    Usage:
        with create_progress_context("Processing", 100) as progress:
            for item in items:
                progress.update(1, f"Processing {item}")
    """
    class ProgressContext:
        def __init__(self):
            self.display = ProgressDisplay()
        
        def __enter__(self):
            self.display.start(description, total)
            return self.display
        
        def __exit__(self, *args):
            self.display.stop()
    
    return ProgressContext()


# ============================================
# GLOBAL API INSTANCE (initialized later)
# ============================================

spotify_api: Optional[SpotifyAPI] = None
artist_searcher: Optional[ArtistSearcher] = None
release_fetcher: Optional[ReleaseFetcher] = None


def init_api(spotify_auth: SpotifyAuth):
    """Initialize global API instances."""
    global spotify_api, artist_searcher, release_fetcher
    
    spotify_api = SpotifyAPI(spotify_auth)
    artist_searcher = ArtistSearcher(spotify_api)
    release_fetcher = ReleaseFetcher(spotify_api)
    
    return spotify_api


# End of Part 3
# ============================================
# PART 4: PLAYLIST OPERATIONS
# ============================================

"""
Playlist operations including get tracks, add/remove tracks,
playlist creation, and playlist selection helpers.
"""

from typing import Optional, List, Dict, Set, Tuple, Callable
from dataclasses import dataclass
import time


@dataclass
class PlaylistTrack:
    """Represents a track in a playlist with metadata."""
    uri: str
    name: str
    artists: List[str]
    album_name: str
    album_uri: str
    release_date: Optional[str] = None
    duration_ms: int = 0
    popularity: int = 0
    added_at: Optional[str] = None
    
    @classmethod
    def from_playlist_item(cls, item: Dict) -> Optional['PlaylistTrack']:
        """Create from Spotify playlist item."""
        track = item.get('track')
        if not track or not track.get('uri'):
            return None
        
        return cls(
            uri=track['uri'],
            name=track.get('name', 'Unknown'),
            artists=[a.get('name', 'Unknown') for a in track.get('artists', [])],
            album_name=track.get('album', {}).get('name', 'Unknown'),
            album_uri=track.get('album', {}).get('uri', ''),
            release_date=track.get('album', {}).get('release_date'),
            duration_ms=track.get('duration_ms', 0),
            popularity=track.get('popularity', 0),
            added_at=item.get('added_at')
        )
    
    @property
    def artist_string(self) -> str:
        """Get comma-separated artist names."""
        return ", ".join(self.artists)
    
    @property
    def primary_artist(self) -> str:
        """Get the primary (first) artist."""
        return self.artists[0] if self.artists else "Unknown"


class PlaylistOperations:
    """Handles all playlist-related operations."""
    
    def __init__(self, spotify_api: SpotifyAPI):
        self.api = spotify_api
    
    @property
    def client(self) -> spotipy.Spotify:
        return self.api.client
    
    # ============================================
    # GET PLAYLIST INFORMATION
    # ============================================
    
    def get_playlist_details(
        self,
        playlist_uri: str,
        skip_cache: bool = False
    ) -> Optional[Dict]:
        """
        Get playlist metadata.
        
        Args:
            playlist_uri: Playlist URI/ID
            skip_cache: Force fresh fetch
            
        Returns:
            Playlist dictionary or None
        """
        return self.api.get_playlist(playlist_uri, skip_cache=skip_cache)
    
    def get_playlist_tracks(
        self,
        playlist_uri: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> List[PlaylistTrack]:
        """
        Get all tracks from a playlist.
        
        Args:
            playlist_uri: Playlist URI/ID
            progress_callback: Optional callback(current, total)
            
        Returns:
            List of PlaylistTrack objects
        """
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id:
            return []
        
        all_tracks = []
        offset = 0
        total = None
        
        try:
            while True:
                self.api._rate_limit()
                response = self.client.playlist_tracks(
                    playlist_id,
                    limit=API_LIMITS["PLAYLIST_TRACKS_LIMIT"],
                    offset=offset,
                    fields="items(added_at,track(uri,name,artists,album,duration_ms,popularity)),total,next"
                )
                
                if total is None:
                    total = response.get('total', 0)
                
                items = response.get('items', [])
                if not items:
                    break
                
                for item in items:
                    track = PlaylistTrack.from_playlist_item(item)
                    if track:
                        all_tracks.append(track)
                
                if progress_callback:
                    progress_callback(len(all_tracks), total)
                
                if response.get('next'):
                    offset += len(items)
                else:
                    break
            
            return all_tracks
            
        except Exception as e:
            self.api._handle_api_error(e, f"Get playlist tracks for {playlist_id}")
            return []
    
    def get_playlist_track_uris(self, playlist_uri: str) -> Set[str]:
        """
        Get set of all track URIs in a playlist.
        
        Args:
            playlist_uri: Playlist URI/ID
            
        Returns:
            Set of track URIs
        """
        tracks = self.get_playlist_tracks(playlist_uri)
        return {track.uri for track in tracks}
    
    # ============================================
    # ADD TRACKS TO PLAYLIST
    # ============================================
    
    def add_tracks(
        self,
        playlist_uri: str,
        track_uris: List[str],
        position: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[int, int]:
        """
        Add tracks to a playlist in batches.
        
        Args:
            playlist_uri: Playlist URI/ID
            track_uris: List of track URIs to add
            position: Optional position to insert at
            progress_callback: Optional callback(current, total)
            
        Returns:
            Tuple of (added_count, failed_count)
        """
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id or not track_uris:
            return (0, 0)
        
        added = 0
        failed = 0
        batch_size = API_LIMITS["PLAYLIST_BATCH_SIZE"]
        total = len(track_uris)
        
        # Process in batches
        for i in range(0, total, batch_size):
            batch = track_uris[i:i + batch_size]
            
            try:
                self.api._rate_limit()
                
                if position is not None:
                    # Insert at specific position
                    self.client.playlist_add_items(
                        playlist_id,
                        batch,
                        position=position + i
                    )
                else:
                    # Append to end
                    self.client.playlist_add_items(playlist_id, batch)
                
                added += len(batch)
                
            except Exception as e:
                print_error(f"Failed to add batch: {e}")
                failed += len(batch)
            
            if progress_callback:
                progress_callback(added + failed, total)
        
        # Clear playlist cache
        self.api.clear_cache(f"playlist:{playlist_id}")
        
        return (added, failed)
    
    def add_tracks_at_top(
        self,
        playlist_uri: str,
        track_uris: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[int, int]:
        """
        Add tracks to the top of a playlist.
        
        Args:
            playlist_uri: Playlist URI/ID
            track_uris: List of track URIs
            progress_callback: Optional callback
            
        Returns:
            Tuple of (added_count, failed_count)
        """
        return self.add_tracks(playlist_uri, track_uris, position=0, progress_callback=progress_callback)
    
    # ============================================
    # REMOVE TRACKS FROM PLAYLIST
    # ============================================
    
    def remove_tracks(
        self,
        playlist_uri: str,
        track_uris: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[int, int]:
        """
        Remove tracks from a playlist in batches.
        
        Args:
            playlist_uri: Playlist URI/ID
            track_uris: List of track URIs to remove
            progress_callback: Optional callback(current, total)
            
        Returns:
            Tuple of (removed_count, failed_count)
        """
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id or not track_uris:
            return (0, 0)
        
        removed = 0
        failed = 0
        batch_size = API_LIMITS["PLAYLIST_BATCH_SIZE"]
        total = len(track_uris)
        
        # Format tracks for removal
        for i in range(0, total, batch_size):
            batch = track_uris[i:i + batch_size]
            tracks_to_remove = [{"uri": uri} for uri in batch]
            
            try:
                self.api._rate_limit()
                self.client.playlist_remove_all_occurrences_of_items(
                    playlist_id,
                    tracks_to_remove
                )
                removed += len(batch)
                
            except Exception as e:
                print_error(f"Failed to remove batch: {e}")
                failed += len(batch)
            
            if progress_callback:
                progress_callback(removed + failed, total)
        
        # Clear playlist cache
        self.api.clear_cache(f"playlist:{playlist_id}")
        
        return (removed, failed)
    
    def remove_tracks_by_artist(
        self,
        playlist_uri: str,
        artist_uri: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> int:
        """
        Remove all tracks by a specific artist from playlist.
        
        Args:
            playlist_uri: Playlist URI
            artist_uri: Artist URI to remove
            progress_callback: Optional callback(status, current, total)
            
        Returns:
            Number of tracks removed
        """
        artist_id = parse_spotify_uri(artist_uri, "artist")
        if not artist_id:
            return 0
        
        if progress_callback:
            progress_callback("Scanning playlist...", 0, 0)
        
        # Get all playlist tracks
        tracks = self.get_playlist_tracks(playlist_uri)
        
        # Find tracks by this artist
        tracks_to_remove = []
        for track in tracks:
            # Check if artist is in this track
            for artist_name in track.artists:
                # We need to check by URI, so get track details
                track_data = self.api.get_track(track.uri)
                if track_data:
                    track_artist_uris = [a.get('uri', '') for a in track_data.get('artists', [])]
                    if f"spotify:artist:{artist_id}" in track_artist_uris:
                        tracks_to_remove.append(track.uri)
                        break
        
        if not tracks_to_remove:
            return 0
        
        if progress_callback:
            progress_callback(f"Removing {len(tracks_to_remove)} tracks...", 0, len(tracks_to_remove))
        
        # Remove the tracks
        def remove_progress(current, total):
            if progress_callback:
                progress_callback("Removing tracks...", current, total)
        
        removed, _ = self.remove_tracks(playlist_uri, tracks_to_remove, remove_progress)
        
        return removed
    
    def clear_playlist(
        self,
        playlist_uri: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> int:
        """
        Remove all tracks from a playlist.
        
        Args:
            playlist_uri: Playlist URI
            progress_callback: Optional callback
            
        Returns:
            Number of tracks removed
        """
        tracks = self.get_playlist_tracks(playlist_uri)
        if not tracks:
            return 0
        
        track_uris = [t.uri for t in tracks]
        removed, _ = self.remove_tracks(playlist_uri, track_uris, progress_callback)
        
        return removed
    
    # ============================================
    # REPLACE PLAYLIST TRACKS
    # ============================================
    
    def replace_all_tracks(
        self,
        playlist_uri: str,
        track_uris: List[str],
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> bool:
        """
        Replace all tracks in a playlist with new tracks.
        
        Args:
            playlist_uri: Playlist URI
            track_uris: New track URIs
            progress_callback: Optional callback(status, current, total)
            
        Returns:
            True if successful
        """
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id:
            return False
        
        try:
            # First batch can use replace
            if progress_callback:
                progress_callback("Replacing tracks...", 0, len(track_uris))
            
            first_batch = track_uris[:API_LIMITS["PLAYLIST_BATCH_SIZE"]]
            
            self.api._rate_limit()
            self.client.playlist_replace_items(playlist_id, first_batch)
            
            # Add remaining tracks
            if len(track_uris) > API_LIMITS["PLAYLIST_BATCH_SIZE"]:
                remaining = track_uris[API_LIMITS["PLAYLIST_BATCH_SIZE"]:]
                
                def add_progress(current, total):
                    if progress_callback:
                        done = len(first_batch) + current
                        progress_callback("Adding tracks...", done, len(track_uris))
                
                self.add_tracks(playlist_uri, remaining, progress_callback=add_progress)
            
            # Clear cache
            self.api.clear_cache(f"playlist:{playlist_id}")
            
            return True
            
        except Exception as e:
            self.api._handle_api_error(e, "Replace playlist tracks")
            return False
    
    # ============================================
    # PLAYLIST CREATION & MANAGEMENT
    # ============================================
    
    def create_playlist(
        self,
        name: str,
        description: str = "",
        public: bool = False
    ) -> Optional[Dict]:
        """
        Create a new playlist.
        
        Args:
            name: Playlist name
            description: Playlist description
            public: Whether public
            
        Returns:
            Created playlist dict or None
        """
        return self.api.create_playlist(name, description, public)
    
    def duplicate_playlist(
        self,
        source_uri: str,
        new_name: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> Optional[Dict]:
        """
        Duplicate a playlist with all its tracks.
        
        Args:
            source_uri: Source playlist URI
            new_name: Name for the new playlist
            progress_callback: Optional callback
            
        Returns:
            New playlist dict or None
        """
        if progress_callback:
            progress_callback("Getting source playlist...", 0, 0)
        
        # Get source details
        source = self.get_playlist_details(source_uri)
        if not source:
            print_error("Could not fetch source playlist")
            return None
        
        # Create new playlist
        if progress_callback:
            progress_callback("Creating new playlist...", 0, 0)
        
        description = f"Duplicated from \"{source.get('name', 'Unknown')}\""
        new_playlist = self.create_playlist(new_name, description)
        if not new_playlist:
            print_error("Could not create new playlist")
            return None
        
        # Get source tracks
        if progress_callback:
            progress_callback("Fetching tracks...", 0, 0)
        
        def track_progress(current, total):
            if progress_callback:
                progress_callback("Fetching tracks...", current, total)
        
        tracks = self.get_playlist_tracks(source_uri, track_progress)
        track_uris = [t.uri for t in tracks]
        
        if track_uris:
            if progress_callback:
                progress_callback("Adding tracks...", 0, len(track_uris))
            
            def add_progress(current, total):
                if progress_callback:
                    progress_callback("Adding tracks...", current, total)
            
            self.add_tracks(new_playlist['uri'], track_uris, progress_callback=add_progress)
        
        return new_playlist
    
    def update_playlist_details(
        self,
        playlist_uri: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        public: Optional[bool] = None
    ) -> bool:
        """
        Update playlist name, description, or visibility.
        
        Args:
            playlist_uri: Playlist URI
            name: New name (optional)
            description: New description (optional)
            public: New visibility (optional)
            
        Returns:
            True if successful
        """
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id:
            return False
        
        try:
            self.api._rate_limit()
            self.client.playlist_change_details(
                playlist_id,
                name=name,
                description=description,
                public=public
            )
            
            # Clear cache
            self.api.clear_cache(f"playlist:{playlist_id}")
            
            return True
            
        except Exception as e:
            self.api._handle_api_error(e, "Update playlist details")
            return False
    
    # ============================================
    # PLAYLIST ANALYSIS
    # ============================================
    
    def analyze_playlist(
        self,
        playlist_uri: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Dict:
        """
        Analyze a playlist and return statistics.
        
        Args:
            playlist_uri: Playlist URI
            progress_callback: Optional status callback
            
        Returns:
            Dictionary with analysis results
        """
        if progress_callback:
            progress_callback("Fetching tracks...")
        
        tracks = self.get_playlist_tracks(playlist_uri)
        
        if not tracks:
            return {
                'total_tracks': 0,
                'unique_artists': 0,
                'unique_albums': 0,
                'total_duration_ms': 0,
                'total_duration_formatted': '0m',
                'exact_duplicates': 0,
                'similar_duplicates': 0,
                'avg_popularity': 0,
            }
        
        artists = set()
        albums = set()
        total_duration = 0
        total_popularity = 0
        
        # Track duplicates
        uri_counts = {}
        name_artist_counts = {}
        
        for track in tracks:
            artists.update(track.artists)
            albums.add(track.album_name)
            total_duration += track.duration_ms
            total_popularity += track.popularity
            
            # Count URI occurrences
            uri_counts[track.uri] = uri_counts.get(track.uri, 0) + 1
            
            # Count name+artist occurrences
            key = f"{track.name.lower()}|||{track.primary_artist.lower()}"
            name_artist_counts[key] = name_artist_counts.get(key, 0) + 1
        
        exact_duplicates = sum(count - 1 for count in uri_counts.values() if count > 1)
        similar_duplicates = sum(count - 1 for count in name_artist_counts.values() if count > 1)
        
        return {
            'total_tracks': len(tracks),
            'unique_artists': len(artists),
            'unique_albums': len(albums),
            'total_duration_ms': total_duration,
            'total_duration_formatted': format_duration(total_duration),
            'exact_duplicates': exact_duplicates,
            'similar_duplicates': similar_duplicates - exact_duplicates,  # Similar but not exact
            'avg_popularity': total_popularity // len(tracks) if tracks else 0,
        }
    
    def display_playlist_stats(self, playlist_uri: str):
        """Display playlist statistics."""
        details = self.get_playlist_details(playlist_uri)
        stats = self.analyze_playlist(playlist_uri)
        
        name = details.get('name', 'Unknown') if details else 'Unknown'
        
        if RICH_AVAILABLE:
            from rich.panel import Panel
            
            lines = [
                f"[bold]Tracks:[/] {stats['total_tracks']}",
                f"[bold]Duration:[/] {stats['total_duration_formatted']}",
                f"[bold]Unique Artists:[/] {stats['unique_artists']}",
                f"[bold]Unique Albums:[/] {stats['unique_albums']}",
                f"[bold]Avg Popularity:[/] {stats['avg_popularity']}/100",
                "",
                f"[bold]Exact Duplicates:[/] {stats['exact_duplicates']}",
                f"[bold]Similar Tracks:[/] {stats['similar_duplicates']}",
            ]
            
            panel = Panel(
                "\n".join(lines),
                title=f"[bold cyan]Playlist: {name}[/]",
                border_style="cyan"
            )
            console.print(panel)
        else:
            print(f"\n=== Playlist: {name} ===")
            print(f"  Tracks: {stats['total_tracks']}")
            print(f"  Duration: {stats['total_duration_formatted']}")
            print(f"  Unique Artists: {stats['unique_artists']}")
            print(f"  Unique Albums: {stats['unique_albums']}")
            print(f"  Avg Popularity: {stats['avg_popularity']}/100")
            print(f"  Exact Duplicates: {stats['exact_duplicates']}")
            print(f"  Similar Tracks: {stats['similar_duplicates']}")
            print()


# ============================================
# PLAYLIST SELECTOR
# ============================================

class PlaylistSelector:
    """Helper for selecting playlists interactively."""
    
    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops
        self._cached_playlists: Optional[List[Dict]] = None
    
    def get_user_playlists(self, force_refresh: bool = False) -> List[Dict]:
        """Get user's playlists with caching."""
        if self._cached_playlists is None or force_refresh:
            self._cached_playlists = self.api.get_user_playlists(limit=50)
        return self._cached_playlists
    
    def display_playlists(self, playlists: Optional[List[Dict]] = None):
        """Display playlists in a formatted table."""
        if playlists is None:
            playlists = self.get_user_playlists()
        
        if not playlists:
            print_warning("No playlists found")
            return
        
        if RICH_AVAILABLE:
            table = Table(
                title="Your Playlists",
                show_header=True,
                header_style="bold cyan"
            )
            table.add_column("#", style="dim", width=4)
            table.add_column("Name", style="white")
            table.add_column("Tracks", justify="right")
            table.add_column("URI", style="dim")
            
            for idx, playlist in enumerate(playlists, 1):
                table.add_row(
                    str(idx),
                    playlist.get('name', 'Unknown')[:40],
                    str(playlist.get('tracks', {}).get('total', 0)),
                    playlist.get('id', '')[:12] + "..."
                )
            
            console.print(table)
        else:
            print("\n=== Your Playlists ===")
            for idx, playlist in enumerate(playlists, 1):
                tracks = playlist.get('tracks', {}).get('total', 0)
                print(f"  {idx}. {playlist.get('name', 'Unknown')} ({tracks} tracks)")
            print()
    
    def select_playlist(
        self,
        prompt_message: str = "Select playlist",
        allow_manual: bool = True
    ) -> Optional[Dict]:
        """
        Interactive playlist selection.
        
        Args:
            prompt_message: Prompt to display
            allow_manual: Allow manual URI entry
            
        Returns:
            Selected playlist dict or None
        """
        playlists = self.get_user_playlists()
        
        if not playlists:
            print_warning("No playlists found in your library")
            if allow_manual:
                return self._manual_playlist_entry()
            return None
        
        self.display_playlists(playlists)
        
        max_choice = len(playlists)
        if allow_manual:
            print_info(f"Enter 1-{max_choice} to select, 'm' for manual entry, or 0 to cancel")
        
        try:
            if RICH_AVAILABLE:
                choice = Prompt.ask(prompt_message)
            else:
                choice = input(f"{prompt_message}: ").strip()
            
            if choice.lower() == 'm' and allow_manual:
                return self._manual_playlist_entry()
            
            choice_num = int(choice)
            
            if choice_num == 0:
                return None
            
            if 1 <= choice_num <= max_choice:
                return playlists[choice_num - 1]
            
            print_error("Invalid selection")
            return None
            
        except ValueError:
            if allow_manual and choice:
                # Try as URI
                return self._manual_playlist_entry(choice)
            print_error("Invalid input")
            return None
    
    def _manual_playlist_entry(self, initial_value: str = "") -> Optional[Dict]:
        """Manual playlist URI/URL entry."""
        if RICH_AVAILABLE:
            uri = Prompt.ask(
                "Enter playlist URL or URI",
                default=initial_value
            ) if not initial_value else initial_value
        else:
            if initial_value:
                uri = initial_value
            else:
                uri = input("Enter playlist URL or URI: ").strip()
        
        if not uri:
            return None
        
        # Normalize and validate
        playlist_id = parse_spotify_uri(uri, "playlist")
        if not playlist_id:
            print_error("Invalid playlist URL/URI")
            return None
        
        # Fetch details
        playlist = self.ops.get_playlist_details(f"spotify:playlist:{playlist_id}")
        if not playlist:
            print_error("Could not find playlist")
            return None
        
        print_success(f"Found playlist: {playlist.get('name', 'Unknown')}")
        return playlist
    
    def search_playlists(self, query: str) -> List[Dict]:
        """Filter cached playlists by name."""
        playlists = self.get_user_playlists()
        query_lower = query.lower()
        return [p for p in playlists if query_lower in p.get('name', '').lower()]


# ============================================
# PLAYLIST BACKUP MANAGER
# ============================================

class PlaylistBackup:
    """Manages playlist backups for recovery from failed operations."""
    
    BACKUP_FILE = CONFIG_DIR / "playlist_backup.json"
    MAX_AGE_HOURS = 24
    
    @classmethod
    def create(
        cls,
        playlist_uri: str,
        playlist_name: str,
        track_uris: List[str],
        operation: str
    ) -> bool:
        """
        Create a backup before destructive operation.
        
        Args:
            playlist_uri: Playlist URI
            playlist_name: Playlist name
            track_uris: Current track URIs
            operation: Operation being performed
            
        Returns:
            True if backup created
        """
        try:
            ensure_config_dir()
            
            backup = {
                'playlist_uri': playlist_uri,
                'playlist_name': playlist_name,
                'track_uris': track_uris,
                'track_count': len(track_uris),
                'operation': operation,
                'created_at': time.time(),
                'status': 'in_progress'
            }
            
            with open(cls.BACKUP_FILE, 'w', encoding='utf-8') as f:
                json.dump(backup, f, indent=2)
            
            return True
            
        except Exception as e:
            print_error(f"Failed to create backup: {e}")
            return False
    
    @classmethod
    def get_pending(cls) -> Optional[Dict]:
        """Get pending backup if exists and not expired."""
        if not cls.BACKUP_FILE.exists():
            return None
        
        try:
            with open(cls.BACKUP_FILE, 'r', encoding='utf-8') as f:
                backup = json.load(f)
            
            # Check age
            age_hours = (time.time() - backup.get('created_at', 0)) / 3600
            if age_hours > cls.MAX_AGE_HOURS:
                cls.discard()
                return None
            
            # Check status
            if backup.get('status') != 'in_progress':
                return None
            
            return backup
            
        except Exception:
            return None
    
    @classmethod
    def complete(cls):
        """Mark operation as complete and remove backup."""
        if cls.BACKUP_FILE.exists():
            cls.BACKUP_FILE.unlink()
    
    @classmethod
    def discard(cls):
        """Discard backup without restoring."""
        if cls.BACKUP_FILE.exists():
            cls.BACKUP_FILE.unlink()
    
    @classmethod
    def get_age_string(cls, backup: Dict) -> str:
        """Get human-readable age of backup."""
        if not backup or 'created_at' not in backup:
            return "unknown"
        
        seconds = time.time() - backup['created_at']
        
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            return f"{int(seconds / 60)} minutes ago"
        elif seconds < 86400:
            return f"{int(seconds / 3600)} hours ago"
        else:
            return f"{int(seconds / 86400)} days ago"


class PlaylistRestorer:
    """Handles restoring playlists from backup."""
    
    def __init__(self, playlist_ops: PlaylistOperations):
        self.ops = playlist_ops
    
    def check_and_offer_restore(self) -> bool:
        """
        Check for pending backup and offer to restore.
        
        Returns:
            True if restored or discarded, False if no backup
        """
        backup = PlaylistBackup.get_pending()
        if not backup:
            return False
        
        age = PlaylistBackup.get_age_string(backup)
        
        print_warning(f"\nInterrupted operation detected!")
        print(f"  Playlist: {backup.get('playlist_name', 'Unknown')}")
        print(f"  Operation: {backup.get('operation', 'Unknown')}")
        print(f"  Tracks: {backup.get('track_count', 0)}")
        print(f"  Created: {age}")
        print()
        
        if RICH_AVAILABLE:
            choice = Prompt.ask(
                "What would you like to do?",
                choices=["restore", "discard", "ignore"],
                default="restore"
            )
        else:
            print("Options: [r]estore, [d]iscard, [i]gnore")
            choice = input("Choice [r]: ").strip().lower() or 'r'
            choice = {'r': 'restore', 'd': 'discard', 'i': 'ignore'}.get(choice, 'ignore')
        
        if choice == 'restore':
            return self.restore(backup)
        elif choice == 'discard':
            PlaylistBackup.discard()
            print_success("Backup discarded")
            return True
        else:
            print_info("Backup kept for later")
            return False
    
    def restore(self, backup: Dict) -> bool:
        """
        Restore playlist from backup.
        
        Args:
            backup: Backup dictionary
            
        Returns:
            True if successful
        """
        playlist_uri = backup.get('playlist_uri')
        track_uris = backup.get('track_uris', [])
        
        if not playlist_uri or not track_uris:
            print_error("Invalid backup data")
            return False
        
        print_info(f"Restoring {len(track_uris)} tracks...")
        
        with create_progress_context("Restoring playlist", len(track_uris)) as progress:
            def update_progress(status, current, total):
                progress.update(1, status)
            
            success = self.ops.replace_all_tracks(
                playlist_uri,
                track_uris,
                progress_callback=update_progress
            )
        
        if success:
            PlaylistBackup.complete()
            print_success(f"Restored {len(track_uris)} tracks!")
            return True
        else:
            print_error("Restore failed - backup kept for retry")
            return False


# ============================================
# GLOBAL PLAYLIST INSTANCES (initialized later)
# ============================================

playlist_ops: Optional[PlaylistOperations] = None
playlist_selector: Optional[PlaylistSelector] = None
playlist_restorer: Optional[PlaylistRestorer] = None


def init_playlist_ops(spotify_api: SpotifyAPI):
    """Initialize global playlist operation instances."""
    global playlist_ops, playlist_selector, playlist_restorer
    
    playlist_ops = PlaylistOperations(spotify_api)
    playlist_selector = PlaylistSelector(spotify_api, playlist_ops)
    playlist_restorer = PlaylistRestorer(playlist_ops)
    
    return playlist_ops


# End of Part 4
# ============================================
# PART 5: TRACK FILTERING AND PROCESSING
# ============================================

"""
Track filtering, duplicate detection, remix detection,
and track processing logic for new releases.
"""

from typing import Optional, List, Dict, Set, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
import re


class FilterReason(Enum):
    """Reasons why a track was filtered out."""
    DUPLICATE = "duplicate"
    REMIX = "remix"
    LOW_POPULARITY = "low_popularity"
    ALREADY_TRACKED = "already_tracked"
    LONG_ALBUM = "long_album"
    ALBUM_LIMIT = "album_limit"


@dataclass
class FilteredTrack:
    """Represents a track that was filtered out."""
    uri: str
    name: str
    reason: FilterReason
    details: str = ""


@dataclass
class FilterStats:
    """Statistics from filtering operations."""
    total_input: int = 0
    passed: int = 0
    filtered_duplicates: int = 0
    filtered_remixes: int = 0
    filtered_low_popularity: int = 0
    filtered_long_albums: int = 0
    filtered_album_limit: int = 0
    filtered_already_tracked: int = 0
    filtered_tracks: List[FilteredTrack] = field(default_factory=list)
    
    @property
    def total_filtered(self) -> int:
        return (
            self.filtered_duplicates +
            self.filtered_remixes +
            self.filtered_low_popularity +
            self.filtered_long_albums +
            self.filtered_album_limit +
            self.filtered_already_tracked
        )
    
    def add_filtered(self, track_uri: str, track_name: str, reason: FilterReason, details: str = ""):
        """Record a filtered track."""
        self.filtered_tracks.append(FilteredTrack(track_uri, track_name, reason, details))
        
        if reason == FilterReason.DUPLICATE:
            self.filtered_duplicates += 1
        elif reason == FilterReason.REMIX:
            self.filtered_remixes += 1
        elif reason == FilterReason.LOW_POPULARITY:
            self.filtered_low_popularity += 1
        elif reason == FilterReason.LONG_ALBUM:
            self.filtered_long_albums += 1
        elif reason == FilterReason.ALBUM_LIMIT:
            self.filtered_album_limit += 1
        elif reason == FilterReason.ALREADY_TRACKED:
            self.filtered_already_tracked += 1
    
    def summary(self) -> str:
        """Get summary string of filtering results."""
        parts = []
        if self.filtered_duplicates:
            parts.append(f"{self.filtered_duplicates} duplicates")
        if self.filtered_remixes:
            parts.append(f"{self.filtered_remixes} remixes")
        if self.filtered_low_popularity:
            parts.append(f"{self.filtered_low_popularity} low popularity")
        if self.filtered_long_albums:
            parts.append(f"{self.filtered_long_albums} long albums")
        if self.filtered_album_limit:
            parts.append(f"{self.filtered_album_limit} album limit")
        if self.filtered_already_tracked:
            parts.append(f"{self.filtered_already_tracked} already tracked")
        
        if parts:
            return f"Filtered: {', '.join(parts)}"
        return "No tracks filtered"
    
    def display(self):
        """Display filter statistics."""
        if RICH_AVAILABLE:
            from rich.table import Table
            
            table = Table(title="Filter Statistics", show_header=True, header_style="bold cyan")
            table.add_column("Category", style="white")
            table.add_column("Count", justify="right")
            
            table.add_row("Total Input", str(self.total_input))
            table.add_row("[green]Passed[/]", f"[green]{self.passed}[/]")
            table.add_row("[red]Total Filtered[/]", f"[red]{self.total_filtered}[/]")
            table.add_row("", "")
            table.add_row("  Duplicates", str(self.filtered_duplicates))
            table.add_row("  Remixes/Variants", str(self.filtered_remixes))
            table.add_row("  Low Popularity", str(self.filtered_low_popularity))
            table.add_row("  Long Albums", str(self.filtered_long_albums))
            table.add_row("  Album Limit", str(self.filtered_album_limit))
            table.add_row("  Already Tracked", str(self.filtered_already_tracked))
            
            console.print(table)
        else:
            print("\n=== Filter Statistics ===")
            print(f"  Total Input: {self.total_input}")
            print(f"  Passed: {self.passed}")
            print(f"  Total Filtered: {self.total_filtered}")
            print(f"    - Duplicates: {self.filtered_duplicates}")
            print(f"    - Remixes/Variants: {self.filtered_remixes}")
            print(f"    - Low Popularity: {self.filtered_low_popularity}")
            print(f"    - Long Albums: {self.filtered_long_albums}")
            print(f"    - Album Limit: {self.filtered_album_limit}")
            print(f"    - Already Tracked: {self.filtered_already_tracked}")
            print()


# ============================================
# REMIX / VARIANT DETECTION
# ============================================

class RemixDetector:
    """Detects remix and variant tracks."""
    
    # Compile patterns for efficiency
    _keyword_pattern: Optional[re.Pattern] = None
    
    @classmethod
    def _get_pattern(cls) -> re.Pattern:
        """Get compiled regex pattern for remix keywords."""
        if cls._keyword_pattern is None:
            # Escape and join keywords
            escaped = [re.escape(kw) for kw in REMIX_KEYWORDS]
            pattern = r'\b(' + '|'.join(escaped) + r')\b'
            cls._keyword_pattern = re.compile(pattern, re.IGNORECASE)
        return cls._keyword_pattern
    
    @classmethod
    def is_remix_or_variant(cls, track_name: str, album_name: str = "") -> bool:
        """
        Check if a track is a remix or variant.
        
        Args:
            track_name: Name of the track
            album_name: Name of the album (optional)
            
        Returns:
            True if remix/variant detected
        """
        combined = f"{track_name} {album_name}".lower()
        pattern = cls._get_pattern()
        return bool(pattern.search(combined))
    
    @classmethod
    def get_matched_keywords(cls, track_name: str, album_name: str = "") -> List[str]:
        """
        Get list of matched remix keywords.
        
        Args:
            track_name: Name of the track
            album_name: Name of the album
            
        Returns:
            List of matched keywords
        """
        combined = f"{track_name} {album_name}".lower()
        pattern = cls._get_pattern()
        matches = pattern.findall(combined)
        return list(set(matches))


# ============================================
# DUPLICATE DETECTION
# ============================================

@dataclass
class DuplicateInfo:
    """Information about a duplicate track."""
    uri: str
    name: str
    artist: str
    original_index: int
    duplicate_index: int
    match_type: str  # 'exact' or 'similar'


class DuplicateDetector:
    """Detects duplicate tracks in playlists."""
    
    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalize track name for comparison."""
        # Remove common suffixes and normalize
        name = name.lower().strip()
        # Remove content in parentheses/brackets for comparison
        name = re.sub(r'\([^)]*\)', '', name)
        name = re.sub(r'\[[^\]]*\]', '', name)
        # Remove extra whitespace
        name = ' '.join(name.split())
        return name
    
    @classmethod
    def find_duplicates(
        cls,
        tracks: List[PlaylistTrack],
        by_name_and_artist: bool = False
    ) -> Tuple[List[DuplicateInfo], Set[str]]:
        """
        Find duplicate tracks in a list.
        
        Args:
            tracks: List of PlaylistTrack objects
            by_name_and_artist: If True, also match by name+artist (not just URI)
            
        Returns:
            Tuple of (list of duplicates, set of unique URIs)
        """
        duplicates = []
        unique_uris = set()
        
        # Track first occurrence
        uri_first_seen: Dict[str, int] = {}
        name_artist_first_seen: Dict[str, int] = {}
        
        for idx, track in enumerate(tracks):
            # Check exact URI match
            if track.uri in uri_first_seen:
                duplicates.append(DuplicateInfo(
                    uri=track.uri,
                    name=track.name,
                    artist=track.primary_artist,
                    original_index=uri_first_seen[track.uri],
                    duplicate_index=idx,
                    match_type='exact'
                ))
                continue
            
            # Check name+artist match if enabled
            if by_name_and_artist:
                key = f"{cls.normalize_name(track.name)}|||{track.primary_artist.lower()}"
                if key in name_artist_first_seen:
                    duplicates.append(DuplicateInfo(
                        uri=track.uri,
                        name=track.name,
                        artist=track.primary_artist,
                        original_index=name_artist_first_seen[key],
                        duplicate_index=idx,
                        match_type='similar'
                    ))
                    continue
                name_artist_first_seen[key] = idx
            
            # First occurrence
            uri_first_seen[track.uri] = idx
            unique_uris.add(track.uri)
        
        return duplicates, unique_uris
    
    @classmethod
    def display_duplicates(cls, duplicates: List[DuplicateInfo]):
        """Display found duplicates."""
        if not duplicates:
            print_success("No duplicates found!")
            return
        
        exact = [d for d in duplicates if d.match_type == 'exact']
        similar = [d for d in duplicates if d.match_type == 'similar']
        
        if RICH_AVAILABLE:
            if exact:
                table = Table(title=f"Exact Duplicates ({len(exact)})", show_header=True, header_style="bold red")
                table.add_column("#", style="dim", width=4)
                table.add_column("Track", style="white")
                table.add_column("Artist")
                table.add_column("Positions", style="dim")
                
                for idx, dup in enumerate(exact[:20], 1):
                    table.add_row(
                        str(idx),
                        dup.name[:40],
                        dup.artist[:20],
                        f"{dup.original_index + 1} & {dup.duplicate_index + 1}"
                    )
                
                if len(exact) > 20:
                    table.add_row("...", f"and {len(exact) - 20} more", "", "")
                
                console.print(table)
            
            if similar:
                table = Table(title=f"Similar Duplicates ({len(similar)})", show_header=True, header_style="bold yellow")
                table.add_column("#", style="dim", width=4)
                table.add_column("Track", style="white")
                table.add_column("Artist")
                table.add_column("Positions", style="dim")
                
                for idx, dup in enumerate(similar[:20], 1):
                    table.add_row(
                        str(idx),
                        dup.name[:40],
                        dup.artist[:20],
                        f"{dup.original_index + 1} & {dup.duplicate_index + 1}"
                    )
                
                if len(similar) > 20:
                    table.add_row("...", f"and {len(similar) - 20} more", "", "")
                
                console.print(table)
        else:
            if exact:
                print(f"\n=== Exact Duplicates ({len(exact)}) ===")
                for idx, dup in enumerate(exact[:20], 1):
                    print(f"  {idx}. {dup.name} by {dup.artist} (positions {dup.original_index + 1} & {dup.duplicate_index + 1})")
                if len(exact) > 20:
                    print(f"  ... and {len(exact) - 20} more")
            
            if similar:
                print(f"\n=== Similar Duplicates ({len(similar)}) ===")
                for idx, dup in enumerate(similar[:20], 1):
                    print(f"  {idx}. {dup.name} by {dup.artist} (positions {dup.original_index + 1} & {dup.duplicate_index + 1})")
                if len(similar) > 20:
                    print(f"  ... and {len(similar) - 20} more")
            
            print()


# ============================================
# TRACK FILTER
# ============================================

@dataclass
class FilterConfig:
    """Configuration for track filtering."""
    skip_duplicates: bool = True
    existing_uris: Set[str] = field(default_factory=set)
    
    skip_remixes: bool = False
    
    skip_low_popularity: bool = False
    min_popularity: int = 30
    
    skip_long_albums: bool = False
    max_album_tracks: int = 20
    
    limit_per_album: bool = False
    max_per_album: int = 5
    
    tracked_releases: Dict[str, float] = field(default_factory=dict)


class TrackFilter:
    """Filters tracks based on various criteria."""
    
    def __init__(self, config: FilterConfig, spotify_api: Optional[SpotifyAPI] = None):
        self.config = config
        self.api = spotify_api
        self.stats = FilterStats()
        self._album_track_counts: Dict[str, int] = {}
    
    def reset_stats(self):
        """Reset filtering statistics."""
        self.stats = FilterStats()
        self._album_track_counts = {}
    
    def filter_track(
        self,
        track_uri: str,
        track_name: str,
        album_name: str = "",
        album_uri: str = "",
        popularity: Optional[int] = None
    ) -> bool:
        """
        Check if a single track should be included.
        
        Args:
            track_uri: Track URI
            track_name: Track name
            album_name: Album name
            album_uri: Album URI
            popularity: Track popularity (0-100)
            
        Returns:
            True if track should be included
        """
        self.stats.total_input += 1
        
        # Check duplicates
        if self.config.skip_duplicates and track_uri in self.config.existing_uris:
            self.stats.add_filtered(track_uri, track_name, FilterReason.DUPLICATE)
            return False
        
        # Check remixes
        if self.config.skip_remixes and RemixDetector.is_remix_or_variant(track_name, album_name):
            keywords = RemixDetector.get_matched_keywords(track_name, album_name)
            self.stats.add_filtered(
                track_uri, track_name, FilterReason.REMIX,
                f"Keywords: {', '.join(keywords)}"
            )
            return False
        
        # Check popularity
        if self.config.skip_low_popularity and popularity is not None:
            if popularity < self.config.min_popularity:
                self.stats.add_filtered(
                    track_uri, track_name, FilterReason.LOW_POPULARITY,
                    f"Popularity: {popularity} < {self.config.min_popularity}"
                )
                return False
        
        # Check album limit
        if self.config.limit_per_album and album_uri:
            current_count = self._album_track_counts.get(album_uri, 0)
            if current_count >= self.config.max_per_album:
                self.stats.add_filtered(
                    track_uri, track_name, FilterReason.ALBUM_LIMIT,
                    f"Album already has {current_count} tracks"
                )
                return False
            self._album_track_counts[album_uri] = current_count + 1
        
        # Track passed
        self.stats.passed += 1
        self.config.existing_uris.add(track_uri)
        return True
    
    def filter_tracks(
        self,
        tracks: List[Dict],
        album_name: str = "",
        album_uri: str = ""
    ) -> List[Dict]:
        """
        Filter a list of tracks.
        
        Args:
            tracks: List of track dictionaries
            album_name: Album name for all tracks
            album_uri: Album URI
            
        Returns:
            Filtered list of tracks
        """
        result = []
        
        for track in tracks:
            track_uri = track.get('uri', '')
            track_name = track.get('name', '')
            popularity = track.get('popularity')
            
            if self.filter_track(track_uri, track_name, album_name, album_uri, popularity):
                result.append(track)
        
        return result
    
    def check_album_skip(
        self,
        album_total_tracks: int,
        album_popularity: Optional[int] = None
    ) -> Tuple[bool, str]:
        """
        Check if an entire album should be skipped.
        
        Args:
            album_total_tracks: Number of tracks in album
            album_popularity: Album popularity
            
        Returns:
            Tuple of (should_skip, reason)
        """
        # Check if album is too long
        if self.config.skip_long_albums:
            if album_total_tracks > self.config.max_album_tracks:
                return True, f"Too many tracks ({album_total_tracks} > {self.config.max_album_tracks})"
        
        # Check album popularity
        if self.config.skip_low_popularity and album_popularity is not None:
            if album_popularity < self.config.min_popularity:
                return True, f"Low popularity ({album_popularity} < {self.config.min_popularity})"
        
        return False, ""


# ============================================
# TRACK PROCESSOR
# ============================================

@dataclass
class ProcessedRelease:
    """Represents a processed release with tracks to add."""
    release_uri: str
    release_name: str
    release_type: str
    release_date: str
    artist_name: str
    tracks: List[Dict]
    skipped: bool = False
    skip_reason: str = ""


class TrackProcessor:
    """Processes releases and extracts tracks with filtering."""
    
    def __init__(
        self,
        spotify_api: SpotifyAPI,
        profile: Profile,
        existing_uris: Set[str]
    ):
        self.api = spotify_api
        self.profile = profile
        
        # Set up filter config from profile
        self.filter_config = FilterConfig(
            skip_duplicates=True,
            existing_uris=existing_uris.copy(),
            skip_remixes=profile.skip_remixes,
            skip_low_popularity=profile.skip_low_popularity,
            min_popularity=profile.min_popularity,
            skip_long_albums=profile.skip_long_albums,
            max_album_tracks=profile.max_songs,
            limit_per_album=profile.limit_songs_per_album,
            max_per_album=profile.max_songs_per_album,
            tracked_releases=profile.tracked_releases
        )
        
        self.track_filter = TrackFilter(self.filter_config, spotify_api)
    
    @property
    def stats(self) -> FilterStats:
        return self.track_filter.stats
    
    def process_release(
        self,
        release: Dict,
        artist_name: str
    ) -> ProcessedRelease:
        """
        Process a single release and extract tracks.
        
        Args:
            release: Release (album) dictionary
            artist_name: Artist name
            
        Returns:
            ProcessedRelease object
        """
        release_uri = release.get('uri', '')
        release_name = release.get('name', 'Unknown')
        release_type = release.get('album_type', 'unknown')
        release_date = release.get('release_date', '')
        total_tracks = release.get('total_tracks', 0)
        
        result = ProcessedRelease(
            release_uri=release_uri,
            release_name=release_name,
            release_type=release_type,
            release_date=release_date,
            artist_name=artist_name,
            tracks=[]
        )
        
        # Check if already tracked
        if release_uri in self.profile.tracked_releases:
            result.skipped = True
            result.skip_reason = "Already tracked"
            return result
        
        # Get full album details
        album_details = self.api.get_album(release_uri)
        if not album_details:
            result.skipped = True
            result.skip_reason = "Could not fetch album details"
            return result
        
        album_popularity = album_details.get('popularity')
        
        # Check if entire album should be skipped
        should_skip, skip_reason = self.track_filter.check_album_skip(
            total_tracks,
            album_popularity
        )
        
        if should_skip:
            result.skipped = True
            result.skip_reason = skip_reason
            
            if "Too many tracks" in skip_reason:
                self.stats.filtered_long_albums += 1
            elif "Low popularity" in skip_reason:
                self.stats.filtered_low_popularity += 1
            
            return result
        
        # Get and filter tracks
        album_tracks = album_details.get('tracks', {}).get('items', [])
        
        filtered_tracks = self.track_filter.filter_tracks(
            album_tracks,
            album_name=release_name,
            album_uri=release_uri
        )
        
        result.tracks = filtered_tracks
        return result
    
    def process_releases(
        self,
        releases: List[Dict],
        artist_name: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> List[ProcessedRelease]:
        """
        Process multiple releases.
        
        Args:
            releases: List of release dictionaries
            artist_name: Artist name
            progress_callback: Optional progress callback
            
        Returns:
            List of ProcessedRelease objects
        """
        results = []
        total = len(releases)
        
        for idx, release in enumerate(releases):
            processed = self.process_release(release, artist_name)
            results.append(processed)
            
            if progress_callback:
                progress_callback(idx + 1, total)
        
        return results
    
    def get_tracks_to_add(self, processed_releases: List[ProcessedRelease]) -> List[str]:
        """
        Extract track URIs from processed releases.
        
        Args:
            processed_releases: List of ProcessedRelease objects
            
        Returns:
            List of track URIs to add
        """
        track_uris = []
        
        for release in processed_releases:
            if not release.skipped:
                for track in release.tracks:
                    track_uris.append(track.get('uri'))
        
        return [uri for uri in track_uris if uri]


# ============================================
# POPULARITY SORTER
# ============================================

class PopularitySorter:
    """Sorts and limits tracks by popularity."""
    
    def __init__(self, spotify_api: SpotifyAPI):
        self.api = spotify_api
    
    def get_top_tracks_by_popularity(
        self,
        track_uris: List[str],
        limit: int
    ) -> List[str]:
        """
        Get top N tracks by popularity.
        
        Args:
            track_uris: List of track URIs
            limit: Maximum number to return
            
        Returns:
            Sorted list of top track URIs
        """
        if len(track_uris) <= limit:
            return track_uris
        
        # Fetch track details
        tracks = self.api.get_multiple_tracks(track_uris)
        
        if not tracks:
            return track_uris[:limit]
        
        # Sort by popularity
        tracks_sorted = sorted(
            tracks,
            key=lambda t: t.get('popularity', 0),
            reverse=True
        )
        
        # Return top N URIs
        return [t.get('uri') for t in tracks_sorted[:limit] if t.get('uri')]
    
    def sort_tracks_by_popularity(
        self,
        track_uris: List[str],
        ascending: bool = False
    ) -> List[str]:
        """
        Sort tracks by popularity.
        
        Args:
            track_uris: List of track URIs
            ascending: If True, sort least popular first
            
        Returns:
            Sorted list of track URIs
        """
        if not track_uris:
            return []
        
        tracks = self.api.get_multiple_tracks(track_uris)
        
        if not tracks:
            return track_uris
        
        tracks_sorted = sorted(
            tracks,
            key=lambda t: t.get('popularity', 0),
            reverse=not ascending
        )
        
        return [t.get('uri') for t in tracks_sorted if t.get('uri')]


# ============================================
# RELEASE DATE FILTER
# ============================================

class ReleaseDateFilter:
    """Filters releases by date."""
    
    @staticmethod
    def filter_by_days(
        releases: List[Dict],
        days_back: int
    ) -> List[Dict]:
        """
        Filter releases to those within N days.
        
        Args:
            releases: List of release dictionaries
            days_back: Days to look back (0 = no limit)
            
        Returns:
            Filtered releases
        """
        if days_back == 0:
            return releases
        
        cutoff = datetime.now() - timedelta(days=days_back)
        result = []
        
        for release in releases:
            release_date = parse_release_date(release.get('release_date', ''))
            if release_date and release_date >= cutoff:
                result.append(release)
        
        return result
    
    @staticmethod
    def sort_by_release_date(
        releases: List[Dict],
        newest_first: bool = True
    ) -> List[Dict]:
        """
        Sort releases by release date.
        
        Args:
            releases: List of release dictionaries
            newest_first: If True, newest first
            
        Returns:
            Sorted releases
        """
        def get_date(release):
            date = parse_release_date(release.get('release_date', ''))
            return date or datetime.min
        
        return sorted(releases, key=get_date, reverse=newest_first)
    
    @staticmethod
    def group_by_date(releases: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Group releases by release date.
        
        Args:
            releases: List of release dictionaries
            
        Returns:
            Dictionary mapping dates to releases
        """
        groups: Dict[str, List[Dict]] = {}
        
        for release in releases:
            date_str = release.get('release_date', 'Unknown')
            if date_str not in groups:
                groups[date_str] = []
            groups[date_str].append(release)
        
        return groups


# ============================================
# TRACK COLLECTION HELPER
# ============================================

@dataclass
class TrackCollection:
    """Collection of tracks with metadata."""
    tracks: List[str] = field(default_factory=list)
    sources: Dict[str, List[str]] = field(default_factory=dict)  # artist_name -> [track_uris]
    
    def add_from_artist(self, artist_name: str, track_uris: List[str]):
        """Add tracks from an artist."""
        self.tracks.extend(track_uris)
        if artist_name not in self.sources:
            self.sources[artist_name] = []
        self.sources[artist_name].extend(track_uris)
    
    def __len__(self) -> int:
        return len(self.tracks)
    
    @property
    def artist_counts(self) -> Dict[str, int]:
        """Get count of tracks per artist."""
        return {name: len(uris) for name, uris in self.sources.items()}
    
    def summary(self) -> str:
        """Get summary of collection."""
        artist_parts = [f"{name}: {count}" for name, count in self.artist_counts.items()]
        return f"{len(self.tracks)} tracks from {len(self.sources)} artists ({', '.join(artist_parts[:5])}{'...' if len(artist_parts) > 5 else ''})"


# ============================================
# CONVENIENCE FUNCTIONS
# ============================================

def create_filter_from_profile(profile: Profile, existing_uris: Set[str]) -> TrackFilter:
    """
    Create a TrackFilter from a profile.
    
    Args:
        profile: Profile with settings
        existing_uris: Set of existing track URIs
        
    Returns:
        Configured TrackFilter
    """
    config = FilterConfig(
        skip_duplicates=True,
        existing_uris=existing_uris,
        skip_remixes=profile.skip_remixes,
        skip_low_popularity=profile.skip_low_popularity,
        min_popularity=profile.min_popularity,
        skip_long_albums=profile.skip_long_albums,
        max_album_tracks=profile.max_songs,
        limit_per_album=profile.limit_songs_per_album,
        max_per_album=profile.max_songs_per_album,
        tracked_releases=profile.tracked_releases
    )
    
    return TrackFilter(config)


def quick_filter_tracks(
    tracks: List[Dict],
    existing_uris: Set[str],
    skip_remixes: bool = False,
    album_name: str = ""
) -> Tuple[List[Dict], int]:
    """
    Quick track filtering without full profile.
    
    Args:
        tracks: Tracks to filter
        existing_uris: Existing URIs to skip
        skip_remixes: Whether to skip remixes
        album_name: Album name for remix detection
        
    Returns:
        Tuple of (filtered_tracks, filtered_count)
    """
    result = []
    filtered = 0
    
    for track in tracks:
        uri = track.get('uri', '')
        name = track.get('name', '')
        
        if uri in existing_uris:
            filtered += 1
            continue
        
        if skip_remixes and RemixDetector.is_remix_or_variant(name, album_name):
            filtered += 1
            continue
        
        result.append(track)
        existing_uris.add(uri)
    
    return result, filtered


# End of Part 5
# ============================================
# PART 6: NEW RELEASE CHECKING CORE LOGIC
# ============================================

"""
Core logic for checking new releases from tracked artists,
processing them, and adding to playlists.
"""

from typing import Optional, List, Dict, Set, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
import time


class CheckStatus(Enum):
    """Status of a release check operation."""
    SUCCESS = "success"
    PARTIAL = "partial"
    NO_NEW = "no_new"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class ArtistCheckResult:
    """Result of checking a single artist."""
    artist: Artist
    status: CheckStatus
    releases_found: int = 0
    releases_processed: int = 0
    tracks_added: int = 0
    tracks_filtered: int = 0
    error_message: str = ""
    new_releases: List[Dict] = field(default_factory=list)


@dataclass
class ProfileCheckResult:
    """Result of checking an entire profile."""
    profile_name: str
    status: CheckStatus
    total_artists: int = 0
    artists_checked: int = 0
    artists_with_new: int = 0
    total_releases: int = 0
    total_tracks_added: int = 0
    total_tracks_filtered: int = 0
    artist_results: List[ArtistCheckResult] = field(default_factory=list)
    filter_stats: Optional[FilterStats] = None
    error_message: str = ""
    duration_seconds: float = 0
    
    def summary(self) -> str:
        """Get summary of check results."""
        if self.status == CheckStatus.ERROR:
            return f"Error: {self.error_message}"
        
        if self.total_tracks_added == 0:
            return f"No new releases found for {self.artists_checked} artists"
        
        parts = [f"Added {self.total_tracks_added} tracks"]
        parts.append(f"from {self.artists_with_new} artists")
        
        if self.total_tracks_filtered > 0:
            parts.append(f"({self.total_tracks_filtered} filtered)")
        
        return " ".join(parts)
    
    def display(self):
        """Display detailed results."""
        if RICH_AVAILABLE:
            from rich.panel import Panel
            from rich.table import Table
            
            # Status color
            status_colors = {
                CheckStatus.SUCCESS: "green",
                CheckStatus.PARTIAL: "yellow",
                CheckStatus.NO_NEW: "blue",
                CheckStatus.ERROR: "red",
                CheckStatus.SKIPPED: "dim",
            }
            color = status_colors.get(self.status, "white")
            
            # Summary panel
            lines = [
                f"[bold]Status:[/] [{color}]{self.status.value.upper()}[/]",
                f"[bold]Duration:[/] {self.duration_seconds:.1f}s",
                "",
                f"[bold]Artists Checked:[/] {self.artists_checked}/{self.total_artists}",
                f"[bold]Artists with New Releases:[/] {self.artists_with_new}",
                f"[bold]Total Releases Found:[/] {self.total_releases}",
                "",
                f"[bold green]Tracks Added:[/] {self.total_tracks_added}",
                f"[bold yellow]Tracks Filtered:[/] {self.total_tracks_filtered}",
            ]
            
            if self.error_message:
                lines.append(f"\n[bold red]Error:[/] {self.error_message}")
            
            panel = Panel(
                "\n".join(lines),
                title=f"[bold]Check Results: {self.profile_name}[/]",
                border_style=color
            )
            console.print(panel)
            
            # Artist breakdown if we have results
            if self.artist_results and any(r.tracks_added > 0 for r in self.artist_results):
                table = Table(title="Artist Breakdown", show_header=True, header_style="bold cyan")
                table.add_column("Artist", style="white")
                table.add_column("Releases", justify="right")
                table.add_column("Added", justify="right", style="green")
                table.add_column("Filtered", justify="right", style="yellow")
                
                for result in self.artist_results:
                    if result.tracks_added > 0 or result.releases_found > 0:
                        table.add_row(
                            result.artist.name[:30],
                            str(result.releases_found),
                            str(result.tracks_added),
                            str(result.tracks_filtered)
                        )
                
                console.print(table)
        else:
            print(f"\n=== Check Results: {self.profile_name} ===")
            print(f"  Status: {self.status.value.upper()}")
            print(f"  Duration: {self.duration_seconds:.1f}s")
            print(f"  Artists Checked: {self.artists_checked}/{self.total_artists}")
            print(f"  Artists with New: {self.artists_with_new}")
            print(f"  Releases Found: {self.total_releases}")
            print(f"  Tracks Added: {self.total_tracks_added}")
            print(f"  Tracks Filtered: {self.total_tracks_filtered}")
            
            if self.error_message:
                print(f"  Error: {self.error_message}")
            
            if self.artist_results and any(r.tracks_added > 0 for r in self.artist_results):
                print("\n  Artist Breakdown:")
                for result in self.artist_results:
                    if result.tracks_added > 0:
                        print(f"    {result.artist.name}: +{result.tracks_added} tracks")
            print()


# ============================================
# PROGRESS CALLBACK TYPES
# ============================================

@dataclass
class CheckProgress:
    """Progress information for release checking."""
    phase: str  # 'init', 'artist', 'processing', 'adding', 'sorting', 'done'
    artist_current: int = 0
    artist_total: int = 0
    artist_name: str = ""
    release_current: int = 0
    release_total: int = 0
    tracks_found: int = 0
    message: str = ""


ProgressCallback = Callable[[CheckProgress], None]


# ============================================
# RELEASE CHECKER
# ============================================

class ReleaseChecker:
    """
    Main class for checking new releases from tracked artists.
    """
    
    def __init__(
        self,
        spotify_api: SpotifyAPI,
        playlist_ops: PlaylistOperations,
        config_manager: ConfigManager
    ):
        self.api = spotify_api
        self.playlist_ops = playlist_ops
        self.config_manager = config_manager
        self.release_fetcher = ReleaseFetcher(spotify_api)
    
    def check_artist(
        self,
        artist: Artist,
        profile: Profile,
        existing_uris: Set[str],
        track_processor: TrackProcessor,
        progress_callback: Optional[ProgressCallback] = None
    ) -> ArtistCheckResult:
        """
        Check a single artist for new releases.
        
        Args:
            artist: Artist to check
            profile: Profile with settings
            existing_uris: Set of existing track URIs in playlist
            track_processor: TrackProcessor for filtering
            progress_callback: Optional progress callback
            
        Returns:
            ArtistCheckResult
        """
        result = ArtistCheckResult(artist=artist, status=CheckStatus.SUCCESS)
        
        try:
            # Get artist's releases
            all_releases = self.release_fetcher.get_artist_releases(artist.uri)
            
            # Filter by date
            recent_releases = ReleaseDateFilter.filter_by_days(
                all_releases,
                profile.days_to_check
            )
            
            # Filter out already tracked
            new_releases = [
                r for r in recent_releases
                if r.get('uri') not in profile.tracked_releases
            ]
            
            result.releases_found = len(new_releases)
            result.new_releases = new_releases
            
            if not new_releases:
                result.status = CheckStatus.NO_NEW
                return result
            
            # Process each release
            tracks_to_add = []
            
            for idx, release in enumerate(new_releases):
                if progress_callback:
                    progress_callback(CheckProgress(
                        phase='processing',
                        release_current=idx + 1,
                        release_total=len(new_releases),
                        message=f"Processing: {release.get('name', 'Unknown')[:30]}"
                    ))
                
                processed = track_processor.process_release(release, artist.name)
                
                if not processed.skipped and processed.tracks:
                    track_uris = [t.get('uri') for t in processed.tracks if t.get('uri')]
                    tracks_to_add.extend(track_uris)
                    
                    # Mark release as tracked
                    profile.tracked_releases[release.get('uri')] = time.time()
                
                result.releases_processed += 1
            
            result.tracks_added = len(tracks_to_add)
            result.tracks_filtered = track_processor.stats.total_filtered
            
            if tracks_to_add:
                result.status = CheckStatus.SUCCESS
            else:
                result.status = CheckStatus.NO_NEW
            
            # Add tracks to existing URIs for dedup
            existing_uris.update(tracks_to_add)
            
            return result
            
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.error_message = str(e)
            return result
    
    def check_profile(
        self,
        profile: Profile,
        progress_callback: Optional[ProgressCallback] = None,
        silent: bool = False
    ) -> ProfileCheckResult:
        """Check all artists in a profile for new releases."""
        start_time = time.time()
    
        result = ProfileCheckResult(
            profile_name=profile.name,
            status=CheckStatus.SUCCESS,
            total_artists=len(profile.artists)
        )
    
        # Validate profile
        if not profile.playlist_uri:
            result.status = CheckStatus.ERROR
            result.error_message = "No playlist configured"
            return result
    
        if not profile.artists:
            result.status = CheckStatus.ERROR
            result.error_message = "No artists to check"
            return result
    
        try:
            # Get existing playlist tracks
            if progress_callback:
                progress_callback(CheckProgress(
                    phase='init',
                    message='Fetching existing playlist tracks...'
                ))
    
            existing_tracks = self.playlist_ops.get_playlist_tracks(profile.playlist_uri)
            existing_uris = {t.uri for t in existing_tracks}
    
            if not silent:
                print_info(f"Playlist has {len(existing_uris)} existing tracks")
    
            # Ensure tracked_tracks exists
            if not hasattr(profile, 'tracked_tracks') or profile.tracked_tracks is None:
                profile.tracked_tracks = {}
    
            # Collect all new tracks to add
            all_new_tracks: List[str] = []
    
            # Check each artist
            for idx, artist in enumerate(profile.artists):
                if progress_callback:
                    progress_callback(CheckProgress(
                        phase='artist',
                        artist_current=idx + 1,
                        artist_total=len(profile.artists),
                        artist_name=artist.name,
                        message=f"Checking: {artist.name}"
                    ))
    
                if not silent:
                    print_info(f"[{idx + 1}/{len(profile.artists)}] Checking {artist.name}...")
    
                # Get artist's releases
                all_releases = self.release_fetcher.get_artist_releases(artist.uri)
    
                # Filter by date
                recent_releases = ReleaseDateFilter.filter_by_days(
                    all_releases,
                    profile.days_to_check
                )
    
                # Filter out already tracked releases
                new_releases = [
                    r for r in recent_releases
                    if r.get('uri') not in profile.tracked_releases
                ]
    
                result.total_releases += len(new_releases)
    
                if not new_releases:
                    result.artists_checked += 1
                    continue
                
                result.artists_with_new += 1
                artist_tracks_added = 0
                artist_tracks_filtered = 0
    
                # Process each new release
                for release in new_releases:
                    release_uri = release.get('uri')
                    release_name = release.get('name', 'Unknown')
    
                    if not silent:
                        print_info(f"  Release: {release_name}")
    
                    # Get album details
                    album_details = self.api.get_album(release_uri)
                    if not album_details:
                        continue
                    
                    album_tracks = album_details.get('tracks', {}).get('items', [])
                    album_popularity = album_details.get('popularity', 0)
    
                    # Check album-level filters
                    if profile.skip_long_albums and len(album_tracks) > profile.max_songs:
                        if not silent:
                            print_warning(f"    Skipping album: too many tracks ({len(album_tracks)} > {profile.max_songs})")
                        result.total_tracks_filtered += len(album_tracks)
                        artist_tracks_filtered += len(album_tracks)
                        profile.tracked_releases[release_uri] = time.time()
                        continue
                    
                    if profile.skip_low_popularity and album_popularity < profile.min_popularity:
                        if not silent:
                            print_warning(f"    Skipping album: low popularity ({album_popularity} < {profile.min_popularity})")
                        result.total_tracks_filtered += len(album_tracks)
                        artist_tracks_filtered += len(album_tracks)
                        profile.tracked_releases[release_uri] = time.time()
                        continue
                    
                    # Process tracks from this release
                    tracks_to_add = []
    
                    for track in album_tracks:
                        track_uri = track.get('uri')
                        track_name = track.get('name', '')
    
                        if not track_uri:
                            continue
                        
                        # Skip if already in playlist - DON'T mark as tracked
                        # (so it can be re-added if user removes it)
                        if track_uri in existing_uris:
                            if not silent:
                                print(f"    [in playlist] {track_name}")
                            artist_tracks_filtered += 1
                            result.total_tracks_filtered += 1
                            continue
                        
                        # Skip if we intentionally skipped this track before
                        if track_uri in profile.tracked_tracks:
                            if not silent:
                                print(f"    [previously processed] {track_name}")
                            artist_tracks_filtered += 1
                            result.total_tracks_filtered += 1
                            continue
                        
                        # Skip remixes if enabled - mark as tracked (intentional skip)
                        if profile.skip_remixes:
                            if RemixDetector.is_remix_or_variant(track_name, release_name):
                                if not silent:
                                    print(f"    [remix/variant] {track_name}")
                                profile.tracked_tracks[track_uri] = time.time()
                                artist_tracks_filtered += 1
                                result.total_tracks_filtered += 1
                                continue
                        
                        # This track is a candidate for adding
                        tracks_to_add.append(track_uri)
                        if not silent:
                            print_success(f"    [adding] {track_name}")
    
                    # Apply per-album limit if enabled
                    if profile.limit_songs_per_album and len(tracks_to_add) > profile.max_songs_per_album:
                        track_details = self.api.get_multiple_tracks(tracks_to_add)
                        if track_details:
                            sorted_tracks = sorted(
                                track_details,
                                key=lambda t: t.get('popularity', 0),
                                reverse=True
                            )
                            # Keep only top N
                            kept_uris = [
                                t.get('uri') for t in sorted_tracks[:profile.max_songs_per_album]
                                if t.get('uri')
                            ]
                            # Mark skipped tracks as tracked (intentional skip due to limit)
                            for t in sorted_tracks[profile.max_songs_per_album:]:
                                uri = t.get('uri')
                                if uri:
                                    profile.tracked_tracks[uri] = time.time()
                                    if not silent:
                                        print_warning(f"    [album limit] {t.get('name', 'Unknown')}")
                            
                            filtered_count = len(tracks_to_add) - len(kept_uris)
                            tracks_to_add = kept_uris
                            artist_tracks_filtered += filtered_count
                            result.total_tracks_filtered += filtered_count
    
                    # Mark tracks we're adding as tracked
                    for track_uri in tracks_to_add:
                        profile.tracked_tracks[track_uri] = time.time()
    
                    # Add to our collection
                    all_new_tracks.extend(tracks_to_add)
                    artist_tracks_added += len(tracks_to_add)
    
                    # Prevent duplicates within this check run
                    existing_uris.update(tracks_to_add)
    
                    # Mark release as tracked
                    profile.tracked_releases[release_uri] = time.time()
    
                # Store artist result
                result.artist_results.append(ArtistCheckResult(
                    artist=artist,
                    status=CheckStatus.SUCCESS,
                    releases_found=len(new_releases),
                    tracks_added=artist_tracks_added,
                    tracks_filtered=artist_tracks_filtered
                ))
    
                result.artists_checked += 1
    
            # Add tracks to playlist
            result.total_tracks_added = len(all_new_tracks)
    
            if all_new_tracks:
                if progress_callback:
                    progress_callback(CheckProgress(
                        phase='adding',
                        tracks_found=len(all_new_tracks),
                        message=f'Adding {len(all_new_tracks)} tracks to playlist...'
                    ))
    
                if not silent:
                    print_info(f"Adding {len(all_new_tracks)} new tracks to playlist...")
    
                added, failed = self.playlist_ops.add_tracks(
                    profile.playlist_uri,
                    all_new_tracks
                )
    
                if failed > 0:
                    result.status = CheckStatus.PARTIAL
                    result.error_message = f"Failed to add {failed} tracks"
                    result.total_tracks_added = added
    
                # Sort if enabled
                if profile.sort_by_date and added > 0:
                    if progress_callback:
                        progress_callback(CheckProgress(
                            phase='sorting',
                            message='Sorting playlist by release date...'
                        ))
    
                    if not silent:
                        print_info("Sorting playlist by release date...")
    
                    time.sleep(2)
    
                    if playlist_tools:
                        playlist_tools.sorter.sort_by_release_date(profile.playlist_uri)
            else:
                result.status = CheckStatus.NO_NEW
    
            # Update last check time and SAVE
            profile.last_check = time.time()
            self.config_manager.save()
    
            result.duration_seconds = time.time() - start_time
    
            if progress_callback:
                progress_callback(CheckProgress(
                    phase='done',
                    tracks_found=result.total_tracks_added,
                    message='Check complete!'
                ))
    
            return result
    
        except Exception as e:
            # Save even on error to preserve tracked data
            self.config_manager.save()
            
            result.status = CheckStatus.ERROR
            result.error_message = str(e)
            result.duration_seconds = time.time() - start_time
            import traceback
            traceback.print_exc()
            return result
    
    def check_all_profiles(
        self,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        silent: bool = False
    ) -> List[ProfileCheckResult]:
        """
        Check all profiles for new releases.
        
        Args:
            progress_callback: Optional callback(profile_name, current, total)
            silent: If True, suppress output
            
        Returns:
            List of ProfileCheckResult
        """
        profiles = self.config_manager.config.profiles
        results = []
        
        for idx, profile in enumerate(profiles):
            if progress_callback:
                progress_callback(profile.name, idx + 1, len(profiles))
            
            if not silent:
                print_info(f"\n{'='*50}")
                print_info(f"Checking profile: {profile.name} ({idx + 1}/{len(profiles)})")
                print_info(f"{'='*50}")
            
            result = self.check_profile(profile, silent=silent)
            results.append(result)
            
            if not silent:
                result.display()
        
        return results


# ============================================
# SIMPLIFIED CHECK FUNCTIONS
# ============================================

def check_current_profile(
    silent: bool = False,
    progress_callback: Optional[ProgressCallback] = None
) -> ProfileCheckResult:
    """
    Check the current active profile for new releases.
    
    Args:
        silent: If True, suppress output
        progress_callback: Optional progress callback
        
    Returns:
        ProfileCheckResult
    """
    if not config_manager or not spotify_api or not playlist_ops:
        raise RuntimeError("Global instances not initialized")
    
    checker = ReleaseChecker(spotify_api, playlist_ops, config_manager)
    profile = config_manager.get_active_profile()
    
    if not silent:
        print_info(f"Checking profile: {profile.name}")
        print_info(f"Artists: {len(profile.artists)}")
        print_info(f"Days back: {profile.days_to_check if profile.days_to_check > 0 else 'All time'}")
        print()
    
    result = checker.check_profile(profile, progress_callback, silent)
    
    if not silent:
        print()
        result.display()
    
    return result


def check_all_profiles(silent: bool = False) -> List[ProfileCheckResult]:
    """
    Check all profiles for new releases.
    
    Args:
        silent: If True, suppress output
        
    Returns:
        List of ProfileCheckResult
    """
    if not config_manager or not spotify_api or not playlist_ops:
        raise RuntimeError("Global instances not initialized")
    
    checker = ReleaseChecker(spotify_api, playlist_ops, config_manager)
    
    def progress(name, current, total):
        if not silent:
            print_info(f"Profile {current}/{total}: {name}")
    
    return checker.check_all_profiles(progress, silent)


# ============================================
# QUICK CHECK (NO OUTPUT)
# ============================================

def quick_check(profile: Optional[Profile] = None) -> Tuple[int, int]:
    """
    Quick check without detailed output.
    
    Args:
        profile: Profile to check (defaults to active)
        
    Returns:
        Tuple of (tracks_added, tracks_filtered)
    """
    if not config_manager or not spotify_api or not playlist_ops:
        raise RuntimeError("Global instances not initialized")
    
    if profile is None:
        profile = config_manager.get_active_profile()
    
    checker = ReleaseChecker(spotify_api, playlist_ops, config_manager)
    result = checker.check_profile(profile, silent=True)
    
    return (result.total_tracks_added, result.total_tracks_filtered)


# ============================================
# INTERACTIVE CHECK
# ============================================

class InteractiveChecker:
    """Interactive release checker with terminal UI."""
    
    def __init__(
        self,
        spotify_api: SpotifyAPI,
        playlist_ops: PlaylistOperations,
        config_manager: ConfigManager
    ):
        self.checker = ReleaseChecker(spotify_api, playlist_ops, config_manager)
        self.config_manager = config_manager
    
    def run_check(self, profile: Optional[Profile] = None) -> ProfileCheckResult:
        """
        Run an interactive check with progress display.
        
        Args:
            profile: Profile to check (defaults to active)
            
        Returns:
            ProfileCheckResult
        """
        if profile is None:
            profile = self.config_manager.get_active_profile()
        
        # Validate
        if not profile.playlist_uri:
            print_error("No playlist configured for this profile!")
            print_info("Use the playlist menu to set a target playlist first.")
            return ProfileCheckResult(
                profile_name=profile.name,
                status=CheckStatus.ERROR,
                error_message="No playlist configured"
            )
        
        if not profile.artists:
            print_error("No artists tracked in this profile!")
            print_info("Use the artists menu to add artists first.")
            return ProfileCheckResult(
                profile_name=profile.name,
                status=CheckStatus.ERROR,
                error_message="No artists"
            )
        
        # Display pre-check info
        print()
        print_info(f"Profile: {profile.name}")
        print_info(f"Artists: {len(profile.artists)}")
        print_info(f"Playlist: {profile.playlist_name}")
        print_info(f"Days to check: {profile.days_to_check if profile.days_to_check > 0 else 'All time'}")
        print()
        
        # Confirm
        if RICH_AVAILABLE:
            if not Confirm.ask("Start check?", default=True):
                print_warning("Cancelled")
                return ProfileCheckResult(
                    profile_name=profile.name,
                    status=CheckStatus.SKIPPED
                )
        else:
            if input("Start check? [Y/n]: ").lower() == 'n':
                print_warning("Cancelled")
                return ProfileCheckResult(
                    profile_name=profile.name,
                    status=CheckStatus.SKIPPED
                )
        
        print()
        
        # Run with progress
        if RICH_AVAILABLE:
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("[dim]{task.fields[status]}"),
                console=console
            ) as progress:
                task = progress.add_task(
                    "Checking releases...",
                    total=len(profile.artists),
                    status=""
                )
                
                def update_progress(p: CheckProgress):
                    if p.phase == 'artist':
                        progress.update(
                            task,
                            completed=p.artist_current,
                            description=f"Checking {p.artist_name}...",
                            status=f"{p.artist_current}/{p.artist_total}"
                        )
                    elif p.phase == 'adding':
                        progress.update(
                            task,
                            description="Adding tracks...",
                            status=p.message
                        )
                    elif p.phase == 'sorting':
                        progress.update(
                            task,
                            description="Sorting playlist...",
                            status=""
                        )
                
                result = self.checker.check_profile(profile, update_progress, silent=True)
        else:
            def simple_progress(p: CheckProgress):
                if p.phase == 'artist':
                    print(f"  [{p.artist_current}/{p.artist_total}] {p.artist_name}")
                elif p.phase == 'adding':
                    print(f"  {p.message}")
            
            result = self.checker.check_profile(profile, simple_progress, silent=True)
        
        print()
        result.display()
        
        return result
    
    def run_check_all(self) -> List[ProfileCheckResult]:
        """
        Run check on all profiles interactively.
        
        Returns:
            List of ProfileCheckResult
        """
        profiles = self.config_manager.config.profiles
        
        if not profiles:
            print_error("No profiles configured!")
            return []
        
        # Confirm
        print_info(f"This will check {len(profiles)} profiles:")
        for p in profiles:
            artists = len(p.artists)
            playlist = p.playlist_name or "No playlist"
            print(f"  - {p.name}: {artists} artists -> {playlist}")
        print()
        
        if RICH_AVAILABLE:
            if not Confirm.ask("Check all profiles?", default=True):
                print_warning("Cancelled")
                return []
        else:
            if input("Check all profiles? [Y/n]: ").lower() == 'n':
                print_warning("Cancelled")
                return []
        
        print()
        
        results = []
        total_added = 0
        
        for idx, profile in enumerate(profiles):
            print_info(f"\n{'='*50}")
            print_info(f"Profile {idx + 1}/{len(profiles)}: {profile.name}")
            print_info(f"{'='*50}")
            
            if not profile.playlist_uri:
                print_warning("Skipping - no playlist configured")
                results.append(ProfileCheckResult(
                    profile_name=profile.name,
                    status=CheckStatus.SKIPPED,
                    error_message="No playlist"
                ))
                continue
            
            if not profile.artists:
                print_warning("Skipping - no artists")
                results.append(ProfileCheckResult(
                    profile_name=profile.name,
                    status=CheckStatus.SKIPPED,
                    error_message="No artists"
                ))
                continue
            
            result = self.checker.check_profile(profile, silent=False)
            results.append(result)
            total_added += result.total_tracks_added
            
            # Brief pause between profiles
            if idx < len(profiles) - 1:
                time.sleep(1)
        
        # Summary
        print()
        print_info(f"{'='*50}")
        print_info("SUMMARY")
        print_info(f"{'='*50}")
        
        successful = sum(1 for r in results if r.status in [CheckStatus.SUCCESS, CheckStatus.PARTIAL])
        with_new = sum(1 for r in results if r.total_tracks_added > 0)
        
        print_success(f"Checked {len(results)} profiles")
        print_success(f"Profiles with new releases: {with_new}")
        print_success(f"Total tracks added: {total_added}")
        
        return results


# ============================================
# SCHEDULED CHECK
# ============================================

class ScheduledChecker:
    """Handles scheduled/automatic release checking."""
    
    def __init__(
        self,
        spotify_api: SpotifyAPI,
        playlist_ops: PlaylistOperations,
        config_manager: ConfigManager
    ):
        self.checker = ReleaseChecker(spotify_api, playlist_ops, config_manager)
        self.config_manager = config_manager
    
    def get_profiles_due(self) -> List[Profile]:
        """
        Get profiles that are due for a check.
        
        Returns:
            List of profiles that need checking
        """
        due = []
        now = time.time()
        
        for profile in self.config_manager.config.profiles:
            if not profile.playlist_uri or not profile.artists:
                continue
            
            interval_seconds = profile.check_interval * 3600  # hours to seconds
            
            if profile.last_check is None:
                due.append(profile)
            elif now - profile.last_check >= interval_seconds:
                due.append(profile)
        
        return due
    
    def check_due_profiles(self, silent: bool = True) -> List[ProfileCheckResult]:
        """
        Check all profiles that are due.
        
        Args:
            silent: If True, minimal output
            
        Returns:
            List of results
        """
        due = self.get_profiles_due()
        
        if not due:
            if not silent:
                print_info("No profiles due for checking")
            return []
        
        if not silent:
            print_info(f"Checking {len(due)} due profiles...")
        
        results = []
        
        for profile in due:
            if not silent:
                print_info(f"Checking: {profile.name}")
            
            result = self.checker.check_profile(profile, silent=silent)
            results.append(result)
            
            if not silent and result.total_tracks_added > 0:
                print_success(f"  Added {result.total_tracks_added} tracks")
        
        return results
    
    def display_schedule(self):
        """Display check schedule for all profiles."""
        profiles = self.config_manager.config.profiles
        now = time.time()
        
        if RICH_AVAILABLE:
            from rich.table import Table
            
            table = Table(title="Check Schedule", show_header=True, header_style="bold cyan")
            table.add_column("Profile", style="white")
            table.add_column("Interval", justify="right")
            table.add_column("Last Check")
            table.add_column("Next Check")
            table.add_column("Status")
            
            for profile in profiles:
                interval_str = f"{profile.check_interval}h"
                
                if profile.last_check:
                    last_dt = datetime.fromtimestamp(profile.last_check)
                    last_str = last_dt.strftime("%Y-%m-%d %H:%M")
                    
                    next_check = profile.last_check + (profile.check_interval * 3600)
                    next_dt = datetime.fromtimestamp(next_check)
                    
                    if next_check <= now:
                        next_str = "[bold red]Due now[/]"
                        status = "[yellow]●[/]"
                    else:
                        next_str = next_dt.strftime("%Y-%m-%d %H:%M")
                        status = "[green]●[/]"
                else:
                    last_str = "[dim]Never[/]"
                    next_str = "[bold red]Due now[/]"
                    status = "[yellow]●[/]"
                
                if not profile.playlist_uri:
                    status = "[red]○[/]"
                    next_str = "[dim]No playlist[/]"
                elif not profile.artists:
                    status = "[red]○[/]"
                    next_str = "[dim]No artists[/]"
                
                table.add_row(
                    profile.name,
                    interval_str,
                    last_str,
                    next_str,
                    status
                )
            
            console.print(table)
        else:
            print("\n=== Check Schedule ===")
            for profile in profiles:
                if profile.last_check:
                    last_dt = datetime.fromtimestamp(profile.last_check)
                    last_str = last_dt.strftime("%Y-%m-%d %H:%M")
                else:
                    last_str = "Never"
                
                status = "Ready" if profile.playlist_uri and profile.artists else "Not configured"
                
                print(f"  {profile.name}:")
                print(f"    Interval: {profile.check_interval}h")
                print(f"    Last check: {last_str}")
                print(f"    Status: {status}")
            print()


# ============================================
# GLOBAL CHECKER INSTANCE
# ============================================

release_checker: Optional[ReleaseChecker] = None
interactive_checker: Optional[InteractiveChecker] = None
scheduled_checker: Optional[ScheduledChecker] = None


def init_checkers(
    spotify_api: SpotifyAPI,
    playlist_ops: PlaylistOperations,
    config_manager: ConfigManager
):
    """Initialize global checker instances."""
    global release_checker, interactive_checker, scheduled_checker
    
    release_checker = ReleaseChecker(spotify_api, playlist_ops, config_manager)
    interactive_checker = InteractiveChecker(spotify_api, playlist_ops, config_manager)
    scheduled_checker = ScheduledChecker(spotify_api, playlist_ops, config_manager)
    
    return release_checker


# End of Part 6
# ============================================
# PART 7: PLAYLIST TOOLS
# ============================================

"""
Playlist tools including sorting, deduplication, 
duplication, and other playlist management operations.
"""

from typing import Optional, List, Dict, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
import time


class SortCriteria(Enum):
    """Criteria for sorting playlists."""
    RELEASE_DATE = "release_date"
    POPULARITY = "popularity"
    TRACK_NAME = "name"
    ARTIST_NAME = "artist"
    ALBUM_NAME = "album"
    DURATION = "duration"
    DATE_ADDED = "added_at"


class SortOrder(Enum):
    """Sort order."""
    ASCENDING = "asc"
    DESCENDING = "desc"


@dataclass
class SortResult:
    """Result of a sort operation."""
    success: bool
    tracks_sorted: int
    sort_criteria: SortCriteria
    sort_order: SortOrder
    duration_seconds: float = 0
    error_message: str = ""


@dataclass
class DedupeResult:
    """Result of a deduplication operation."""
    success: bool
    duplicates_found: int
    duplicates_removed: int
    exact_duplicates: int
    similar_duplicates: int
    duration_seconds: float = 0
    error_message: str = ""
    removed_tracks: List[DuplicateInfo] = None
    
    def __post_init__(self):
        if self.removed_tracks is None:
            self.removed_tracks = []


@dataclass
class DuplicatePlaylistResult:
    """Result of duplicating a playlist."""
    success: bool
    source_name: str
    new_name: str
    new_uri: str = ""
    tracks_copied: int = 0
    duration_seconds: float = 0
    error_message: str = ""


# ============================================
# PLAYLIST SORTER
# ============================================

class PlaylistSorter:
    """Handles playlist sorting operations."""
    
    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops
    
    def sort_playlist(
        self,
        playlist_uri: str,
        criteria: SortCriteria = SortCriteria.RELEASE_DATE,
        order: SortOrder = SortOrder.DESCENDING,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        create_backup: bool = True
    ) -> SortResult:
        """
        Sort a playlist by the specified criteria.
        
        Args:
            playlist_uri: Playlist URI
            criteria: Sort criteria
            order: Sort order (asc/desc)
            progress_callback: Optional callback(status, current, total)
            create_backup: Whether to create backup before sorting
            
        Returns:
            SortResult
        """
        start_time = time.time()
        result = SortResult(
            success=False,
            tracks_sorted=0,
            sort_criteria=criteria,
            sort_order=order
        )
        
        try:
            # Get playlist details
            playlist = self.ops.get_playlist_details(playlist_uri)
            if not playlist:
                result.error_message = "Could not fetch playlist"
                return result
            
            playlist_name = playlist.get('name', 'Unknown')
            
            if progress_callback:
                progress_callback("Fetching tracks...", 0, 0)
            
            # Get all tracks
            tracks = self.ops.get_playlist_tracks(playlist_uri)
            
            if not tracks:
                result.error_message = "Playlist is empty"
                return result
            
            # Create backup if requested
            if create_backup:
                if progress_callback:
                    progress_callback("Creating backup...", 0, 0)
                
                track_uris = [t.uri for t in tracks]
                PlaylistBackup.create(
                    playlist_uri,
                    playlist_name,
                    track_uris,
                    f"sort_by_{criteria.value}"
                )
            
            if progress_callback:
                progress_callback("Fetching track details...", 0, len(tracks))
            
            # Fetch full track details for sorting
            track_uris = [t.uri for t in tracks]
            full_tracks = self.api.get_multiple_tracks(track_uris)
            
            if not full_tracks:
                result.error_message = "Could not fetch track details"
                return result
            
            # Create mapping of URI to full track data
            track_map = {t.get('uri'): t for t in full_tracks if t}
            
            # Build sortable list with original playlist data
            sortable = []
            for pt in tracks:
                full = track_map.get(pt.uri, {})
                sortable.append({
                    'uri': pt.uri,
                    'name': pt.name,
                    'artists': pt.artists,
                    'album_name': pt.album_name,
                    'release_date': full.get('album', {}).get('release_date', ''),
                    'popularity': full.get('popularity', 0),
                    'duration_ms': full.get('duration_ms', 0),
                    'added_at': pt.added_at or '',
                })
            
            if progress_callback:
                progress_callback("Sorting tracks...", 0, 0)
            
            # Sort based on criteria
            reverse = (order == SortOrder.DESCENDING)
            
            if criteria == SortCriteria.RELEASE_DATE:
                def sort_key(t):
                    date = parse_release_date(t.get('release_date', ''))
                    return date or datetime.min
                sortable.sort(key=sort_key, reverse=reverse)
                
            elif criteria == SortCriteria.POPULARITY:
                sortable.sort(key=lambda t: t.get('popularity', 0), reverse=reverse)
                
            elif criteria == SortCriteria.TRACK_NAME:
                sortable.sort(key=lambda t: t.get('name', '').lower(), reverse=reverse)
                
            elif criteria == SortCriteria.ARTIST_NAME:
                sortable.sort(
                    key=lambda t: t.get('artists', [''])[0].lower() if t.get('artists') else '',
                    reverse=reverse
                )
                
            elif criteria == SortCriteria.ALBUM_NAME:
                sortable.sort(key=lambda t: t.get('album_name', '').lower(), reverse=reverse)
                
            elif criteria == SortCriteria.DURATION:
                sortable.sort(key=lambda t: t.get('duration_ms', 0), reverse=reverse)
                
            elif criteria == SortCriteria.DATE_ADDED:
                sortable.sort(key=lambda t: t.get('added_at', ''), reverse=reverse)
            
            # Get sorted URIs
            sorted_uris = [t['uri'] for t in sortable]
            
            # Replace playlist contents
            if progress_callback:
                progress_callback("Replacing tracks...", 0, len(sorted_uris))
            
            def replace_progress(status, current, total):
                if progress_callback:
                    progress_callback(status, current, total)
            
            success = self.ops.replace_all_tracks(
                playlist_uri,
                sorted_uris,
                replace_progress
            )
            
            if success:
                result.success = True
                result.tracks_sorted = len(sorted_uris)
                PlaylistBackup.complete()  # Clear backup on success
            else:
                result.error_message = "Failed to replace tracks"
            
            result.duration_seconds = time.time() - start_time
            return result
            
        except Exception as e:
            result.error_message = str(e)
            result.duration_seconds = time.time() - start_time
            return result
    
    def sort_by_release_date(
        self,
        playlist_uri: str,
        newest_first: bool = True,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> SortResult:
        """
        Convenience method to sort by release date.
        
        Args:
            playlist_uri: Playlist URI
            newest_first: If True, newest first
            progress_callback: Optional callback
            
        Returns:
            SortResult
        """
        order = SortOrder.DESCENDING if newest_first else SortOrder.ASCENDING
        return self.sort_playlist(
            playlist_uri,
            SortCriteria.RELEASE_DATE,
            order,
            progress_callback
        )
    
    def display_sort_options(self):
        """Display available sort options."""
        print("\nSort Options:")
        print("  1. Release Date (newest first)")
        print("  2. Release Date (oldest first)")
        print("  3. Popularity (most popular first)")
        print("  4. Popularity (least popular first)")
        print("  5. Track Name (A-Z)")
        print("  6. Track Name (Z-A)")
        print("  7. Artist Name (A-Z)")
        print("  8. Artist Name (Z-A)")
        print("  9. Album Name (A-Z)")
        print(" 10. Duration (longest first)")
        print(" 11. Duration (shortest first)")
        print(" 12. Date Added (newest first)")
        print("  0. Cancel")
        print()
    
    def get_sort_from_choice(self, choice: int) -> Optional[Tuple[SortCriteria, SortOrder]]:
        """
        Get sort criteria and order from menu choice.
        
        Args:
            choice: Menu choice (1-12)
            
        Returns:
            Tuple of (criteria, order) or None if cancelled
        """
        options = {
            1: (SortCriteria.RELEASE_DATE, SortOrder.DESCENDING),
            2: (SortCriteria.RELEASE_DATE, SortOrder.ASCENDING),
            3: (SortCriteria.POPULARITY, SortOrder.DESCENDING),
            4: (SortCriteria.POPULARITY, SortOrder.ASCENDING),
            5: (SortCriteria.TRACK_NAME, SortOrder.ASCENDING),
            6: (SortCriteria.TRACK_NAME, SortOrder.DESCENDING),
            7: (SortCriteria.ARTIST_NAME, SortOrder.ASCENDING),
            8: (SortCriteria.ARTIST_NAME, SortOrder.DESCENDING),
            9: (SortCriteria.ALBUM_NAME, SortOrder.ASCENDING),
            10: (SortCriteria.DURATION, SortOrder.DESCENDING),
            11: (SortCriteria.DURATION, SortOrder.ASCENDING),
            12: (SortCriteria.DATE_ADDED, SortOrder.DESCENDING),
        }
        return options.get(choice)


# ============================================
# PLAYLIST DEDUPLICATOR
# ============================================

class PlaylistDeduplicator:
    """Handles playlist deduplication."""
    
    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops
    
    def find_duplicates(
        self,
        playlist_uri: str,
        include_similar: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Tuple[List[DuplicateInfo], List[PlaylistTrack]]:
        """
        Find duplicates in a playlist.
        
        Args:
            playlist_uri: Playlist URI
            include_similar: If True, also find similar (name+artist) matches
            progress_callback: Optional status callback
            
        Returns:
            Tuple of (duplicates list, all tracks)
        """
        if progress_callback:
            progress_callback("Fetching tracks...")
        
        tracks = self.ops.get_playlist_tracks(playlist_uri)
        
        if not tracks:
            return [], []
        
        if progress_callback:
            progress_callback(f"Analyzing {len(tracks)} tracks...")
        
        duplicates, _ = DuplicateDetector.find_duplicates(tracks, include_similar)
        
        return duplicates, tracks
    
    def preview_duplicates(
        self,
        playlist_uri: str,
        include_similar: bool = False
    ) -> List[DuplicateInfo]:
        """
        Preview duplicates without removing them.
        
        Args:
            playlist_uri: Playlist URI
            include_similar: Include similar matches
            
        Returns:
            List of duplicates found
        """
        print_info("Scanning playlist for duplicates...")
        
        duplicates, tracks = self.find_duplicates(playlist_uri, include_similar)
        
        if not duplicates:
            print_success("No duplicates found!")
            return []
        
        exact = [d for d in duplicates if d.match_type == 'exact']
        similar = [d for d in duplicates if d.match_type == 'similar']
        
        print()
        print_info(f"Found {len(duplicates)} duplicates in {len(tracks)} tracks:")
        print_info(f"  - Exact duplicates: {len(exact)}")
        print_info(f"  - Similar tracks: {len(similar)}")
        print()
        
        DuplicateDetector.display_duplicates(duplicates)
        
        return duplicates
    
    def remove_duplicates(
        self,
        playlist_uri: str,
        include_similar: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        create_backup: bool = True
    ) -> DedupeResult:
        """
        Remove duplicates from a playlist.
        
        Args:
            playlist_uri: Playlist URI
            include_similar: Remove similar matches too
            progress_callback: Optional callback(status, current, total)
            create_backup: Whether to create backup first
            
        Returns:
            DedupeResult
        """
        start_time = time.time()
        result = DedupeResult(
            success=False,
            duplicates_found=0,
            duplicates_removed=0,
            exact_duplicates=0,
            similar_duplicates=0
        )
        
        try:
            # Get playlist details
            playlist = self.ops.get_playlist_details(playlist_uri)
            if not playlist:
                result.error_message = "Could not fetch playlist"
                return result
            
            playlist_name = playlist.get('name', 'Unknown')
            
            if progress_callback:
                progress_callback("Finding duplicates...", 0, 0)
            
            # Find duplicates
            duplicates, tracks = self.find_duplicates(
                playlist_uri,
                include_similar,
                lambda s: progress_callback(s, 0, 0) if progress_callback else None
            )
            
            if not duplicates:
                result.success = True
                result.duration_seconds = time.time() - start_time
                return result
            
            result.duplicates_found = len(duplicates)
            result.exact_duplicates = len([d for d in duplicates if d.match_type == 'exact'])
            result.similar_duplicates = len([d for d in duplicates if d.match_type == 'similar'])
            
            # Create backup if requested
            if create_backup:
                if progress_callback:
                    progress_callback("Creating backup...", 0, 0)
                
                track_uris = [t.uri for t in tracks]
                PlaylistBackup.create(
                    playlist_uri,
                    playlist_name,
                    track_uris,
                    "deduplicate"
                )
            
            # Get URIs to remove (the duplicate occurrences, not the originals)
            uris_to_remove = [d.uri for d in duplicates]
            
            if progress_callback:
                progress_callback("Removing duplicates...", 0, len(uris_to_remove))
            
            # Remove duplicates
            def remove_progress(current, total):
                if progress_callback:
                    progress_callback("Removing...", current, total)
            
            removed, failed = self.ops.remove_tracks(
                playlist_uri,
                uris_to_remove,
                remove_progress
            )
            
            result.duplicates_removed = removed
            result.removed_tracks = duplicates
            result.success = True
            
            if failed > 0:
                result.error_message = f"Failed to remove {failed} tracks"
            else:
                PlaylistBackup.complete()  # Clear backup on full success
            
            result.duration_seconds = time.time() - start_time
            return result
            
        except Exception as e:
            result.error_message = str(e)
            result.duration_seconds = time.time() - start_time
            return result
    
    def display_result(self, result: DedupeResult):
        """Display deduplication result."""
        if RICH_AVAILABLE:
            from rich.panel import Panel
            
            if result.success and result.duplicates_removed > 0:
                color = "green"
                status = "SUCCESS"
            elif result.success and result.duplicates_found == 0:
                color = "blue"
                status = "NO DUPLICATES"
            else:
                color = "red"
                status = "ERROR"
            
            lines = [
                f"[bold]Status:[/] [{color}]{status}[/]",
                f"[bold]Duration:[/] {result.duration_seconds:.1f}s",
                "",
                f"[bold]Duplicates Found:[/] {result.duplicates_found}",
                f"  - Exact: {result.exact_duplicates}",
                f"  - Similar: {result.similar_duplicates}",
                "",
                f"[bold green]Removed:[/] {result.duplicates_removed}",
            ]
            
            if result.error_message:
                lines.append(f"\n[bold red]Error:[/] {result.error_message}")
            
            panel = Panel(
                "\n".join(lines),
                title="[bold]Deduplication Results[/]",
                border_style=color
            )
            console.print(panel)
        else:
            print("\n=== Deduplication Results ===")
            print(f"  Duplicates Found: {result.duplicates_found}")
            print(f"    - Exact: {result.exact_duplicates}")
            print(f"    - Similar: {result.similar_duplicates}")
            print(f"  Removed: {result.duplicates_removed}")
            if result.error_message:
                print(f"  Error: {result.error_message}")
            print(f"  Duration: {result.duration_seconds:.1f}s")
            print()


# ============================================
# PLAYLIST DUPLICATOR
# ============================================

class PlaylistDuplicator:
    """Handles playlist duplication."""
    
    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops
    
    def duplicate(
        self,
        source_uri: str,
        new_name: Optional[str] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> DuplicatePlaylistResult:
        """
        Duplicate a playlist.
        
        Args:
            source_uri: Source playlist URI
            new_name: Name for new playlist (auto-generated if None)
            progress_callback: Optional callback(status, current, total)
            
        Returns:
            DuplicatePlaylistResult
        """
        start_time = time.time()
        result = DuplicatePlaylistResult(
            success=False,
            source_name="",
            new_name=""
        )
        
        try:
            if progress_callback:
                progress_callback("Fetching source playlist...", 0, 0)
            
            # Get source details
            source = self.ops.get_playlist_details(source_uri)
            if not source:
                result.error_message = "Could not fetch source playlist"
                return result
            
            result.source_name = source.get('name', 'Unknown')
            result.new_name = new_name or f"{result.source_name} (Copy)"
            
            if progress_callback:
                progress_callback("Creating new playlist...", 0, 0)
            
            # Create new playlist
            new_playlist = self.ops.create_playlist(
                result.new_name,
                description=f"Duplicated from \"{result.source_name}\""
            )
            
            if not new_playlist:
                result.error_message = "Could not create new playlist"
                return result
            
            result.new_uri = new_playlist.get('uri', '')
            
            if progress_callback:
                progress_callback("Fetching tracks...", 0, 0)
            
            # Get source tracks
            tracks = self.ops.get_playlist_tracks(source_uri)
            
            if tracks:
                track_uris = [t.uri for t in tracks]
                
                if progress_callback:
                    progress_callback("Copying tracks...", 0, len(track_uris))
                
                def add_progress(current, total):
                    if progress_callback:
                        progress_callback("Copying...", current, total)
                
                added, failed = self.ops.add_tracks(
                    result.new_uri,
                    track_uris,
                    progress_callback=add_progress
                )
                
                result.tracks_copied = added
                
                if failed > 0:
                    result.error_message = f"Failed to copy {failed} tracks"
            
            result.success = True
            result.duration_seconds = time.time() - start_time
            return result
            
        except Exception as e:
            result.error_message = str(e)
            result.duration_seconds = time.time() - start_time
            return result
    
    def display_result(self, result: DuplicatePlaylistResult):
        """Display duplication result."""
        if result.success:
            print_success(f"Created \"{result.new_name}\"")
            print_info(f"  Copied {result.tracks_copied} tracks from \"{result.source_name}\"")
            print_info(f"  Duration: {result.duration_seconds:.1f}s")
            
            if result.error_message:
                print_warning(f"  Warning: {result.error_message}")
        else:
            print_error(f"Failed to duplicate playlist")
            print_error(f"  Error: {result.error_message}")


# ============================================
# PLAYLIST ANALYZER
# ============================================

class PlaylistAnalyzer:
    """Analyzes playlists and provides insights."""
    
    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops
    
    def analyze(
        self,
        playlist_uri: str,
        detailed: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Dict:
        """
        Analyze a playlist.
        
        Args:
            playlist_uri: Playlist URI
            detailed: If True, include more detailed analysis
            progress_callback: Optional status callback
            
        Returns:
            Dictionary with analysis results
        """
        if progress_callback:
            progress_callback("Fetching playlist...")
        
        # Basic stats from playlist ops
        stats = self.ops.analyze_playlist(playlist_uri, progress_callback)
        
        if not stats or stats.get('total_tracks', 0) == 0:
            return {'error': 'Could not analyze playlist'}
        
        if detailed and stats.get('total_tracks', 0) > 0:
            if progress_callback:
                progress_callback("Fetching detailed data...")
            
            tracks = self.ops.get_playlist_tracks(playlist_uri)
            
            # Calculate additional stats
            if tracks:
                # Artist frequency
                artist_counts = {}
                for track in tracks:
                    for artist in track.artists:
                        artist_counts[artist] = artist_counts.get(artist, 0) + 1
                
                stats['top_artists'] = sorted(
                    artist_counts.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:10]
                
                # Release year distribution
                year_counts = {}
                for track in tracks:
                    if track.release_date:
                        year = track.release_date[:4]
                        year_counts[year] = year_counts.get(year, 0) + 1
                
                stats['year_distribution'] = sorted(
                    year_counts.items(),
                    key=lambda x: x[0],
                    reverse=True
                )
                
                # Popularity distribution
                pop_ranges = {'0-20': 0, '21-40': 0, '41-60': 0, '61-80': 0, '81-100': 0}
                for track in tracks:
                    pop = track.popularity
                    if pop <= 20:
                        pop_ranges['0-20'] += 1
                    elif pop <= 40:
                        pop_ranges['21-40'] += 1
                    elif pop <= 60:
                        pop_ranges['41-60'] += 1
                    elif pop <= 80:
                        pop_ranges['61-80'] += 1
                    else:
                        pop_ranges['81-100'] += 1
                
                stats['popularity_distribution'] = pop_ranges
        
        return stats
    
    def display_analysis(self, playlist_uri: str, detailed: bool = False):
        """Display playlist analysis."""
        print_info("Analyzing playlist...")
        
        stats = self.analyze(playlist_uri, detailed)
        
        if 'error' in stats:
            print_error(stats['error'])
            return
        
        playlist = self.ops.get_playlist_details(playlist_uri)
        name = playlist.get('name', 'Unknown') if playlist else 'Unknown'
        
        if RICH_AVAILABLE:
            from rich.panel import Panel
            from rich.table import Table
            from rich.columns import Columns
            
            # Main stats panel
            lines = [
                f"[bold]Total Tracks:[/] {stats.get('total_tracks', 0)}",
                f"[bold]Duration:[/] {stats.get('total_duration_formatted', 'N/A')}",
                f"[bold]Unique Artists:[/] {stats.get('unique_artists', 0)}",
                f"[bold]Unique Albums:[/] {stats.get('unique_albums', 0)}",
                f"[bold]Avg Popularity:[/] {stats.get('avg_popularity', 0)}/100",
                "",
                f"[bold]Exact Duplicates:[/] {stats.get('exact_duplicates', 0)}",
                f"[bold]Similar Tracks:[/] {stats.get('similar_duplicates', 0)}",
            ]
            
            panel = Panel(
                "\n".join(lines),
                title=f"[bold cyan]Playlist Analysis: {name}[/]",
                border_style="cyan"
            )
            console.print(panel)
            
            if detailed:
                # Top artists table
                if 'top_artists' in stats and stats['top_artists']:
                    table = Table(title="Top Artists", show_header=True, header_style="bold")
                    table.add_column("Artist", style="white")
                    table.add_column("Tracks", justify="right")
                    
                    for artist, count in stats['top_artists'][:10]:
                        table.add_row(artist[:30], str(count))
                    
                    console.print(table)
                
                # Year distribution
                if 'year_distribution' in stats and stats['year_distribution']:
                    table = Table(title="Release Years", show_header=True, header_style="bold")
                    table.add_column("Year", style="white")
                    table.add_column("Tracks", justify="right")
                    
                    for year, count in stats['year_distribution'][:10]:
                        table.add_row(year, str(count))
                    
                    console.print(table)
                
                # Popularity distribution
                if 'popularity_distribution' in stats:
                    table = Table(title="Popularity Distribution", show_header=True, header_style="bold")
                    table.add_column("Range", style="white")
                    table.add_column("Tracks", justify="right")
                    
                    for range_name, count in stats['popularity_distribution'].items():
                        table.add_row(range_name, str(count))
                    
                    console.print(table)
        else:
            print(f"\n=== Playlist Analysis: {name} ===")
            print(f"  Total Tracks: {stats.get('total_tracks', 0)}")
            print(f"  Duration: {stats.get('total_duration_formatted', 'N/A')}")
            print(f"  Unique Artists: {stats.get('unique_artists', 0)}")
            print(f"  Unique Albums: {stats.get('unique_albums', 0)}")
            print(f"  Avg Popularity: {stats.get('avg_popularity', 0)}/100")
            print(f"  Exact Duplicates: {stats.get('exact_duplicates', 0)}")
            print(f"  Similar Tracks: {stats.get('similar_duplicates', 0)}")
            
            if detailed:
                if 'top_artists' in stats:
                    print("\n  Top Artists:")
                    for artist, count in stats['top_artists'][:5]:
                        print(f"    {artist}: {count} tracks")
                
                if 'year_distribution' in stats:
                    print("\n  Release Years:")
                    for year, count in stats['year_distribution'][:5]:
                        print(f"    {year}: {count} tracks")
            print()


# ============================================
# ARTIST TRACK REMOVER
# ============================================

class ArtistTrackRemover:
    """Removes all tracks by a specific artist from a playlist."""
    
    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops
    
    def remove_artist_tracks(
        self,
        playlist_uri: str,
        artist_uri: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        create_backup: bool = True
    ) -> Tuple[int, str]:
        """
        Remove all tracks by an artist from a playlist.
        
        Args:
            playlist_uri: Playlist URI
            artist_uri: Artist URI
            progress_callback: Optional callback
            create_backup: Whether to create backup first
            
        Returns:
            Tuple of (tracks_removed, artist_name)
        """
        # Get artist info
        artist = self.api.get_artist(artist_uri)
        if not artist:
            print_error("Could not find artist")
            return 0, ""
        
        artist_name = artist.get('name', 'Unknown')
        artist_id = parse_spotify_uri(artist_uri, "artist")
        
        if progress_callback:
            progress_callback("Scanning playlist...", 0, 0)
        
        # Get playlist tracks
        tracks = self.ops.get_playlist_tracks(playlist_uri)
        
        if not tracks:
            print_info("Playlist is empty")
            return 0, artist_name
        
        if progress_callback:
            progress_callback("Finding artist tracks...", 0, len(tracks))
        
        # Find tracks by this artist
        tracks_to_remove = []
        
        for idx, track in enumerate(tracks):
            # Get full track details to check artist URIs
            track_data = self.api.get_track(track.uri)
            if track_data:
                track_artist_ids = [
                    parse_spotify_uri(a.get('uri', ''), 'artist')
                    for a in track_data.get('artists', [])
                ]
                
                if artist_id in track_artist_ids:
                    tracks_to_remove.append(track.uri)
            
            if progress_callback and idx % 10 == 0:
                progress_callback("Finding artist tracks...", idx, len(tracks))
        
        if not tracks_to_remove:
            print_info(f"No tracks by {artist_name} found in playlist")
            return 0, artist_name
        
        # Create backup if requested
        if create_backup:
            playlist = self.ops.get_playlist_details(playlist_uri)
            PlaylistBackup.create(
                playlist_uri,
                playlist.get('name', 'Unknown') if playlist else 'Unknown',
                [t.uri for t in tracks],
                f"remove_artist_{artist_name}"
            )
        
        if progress_callback:
            progress_callback("Removing tracks...", 0, len(tracks_to_remove))
        
        # Remove tracks
        def remove_progress(current, total):
            if progress_callback:
                progress_callback("Removing...", current, total)
        
        removed, failed = self.ops.remove_tracks(
            playlist_uri,
            tracks_to_remove,
            remove_progress
        )
        
        if removed > 0 and failed == 0:
            PlaylistBackup.complete()
        
        return removed, artist_name


# ============================================
# PLAYLIST TOOLS MANAGER
# ============================================

class PlaylistTools:
    """Central manager for all playlist tools."""
    
    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops
        
        self.sorter = PlaylistSorter(spotify_api, playlist_ops)
        self.deduplicator = PlaylistDeduplicator(spotify_api, playlist_ops)
        self.duplicator = PlaylistDuplicator(spotify_api, playlist_ops)
        self.analyzer = PlaylistAnalyzer(spotify_api, playlist_ops)
        self.artist_remover = ArtistTrackRemover(spotify_api, playlist_ops)
    
    def sort(
        self,
        playlist_uri: str,
        criteria: SortCriteria = SortCriteria.RELEASE_DATE,
        order: SortOrder = SortOrder.DESCENDING
    ) -> SortResult:
        """Sort a playlist."""
        return self.sorter.sort_playlist(playlist_uri, criteria, order)
    
    def deduplicate(
        self,
        playlist_uri: str,
        include_similar: bool = False
    ) -> DedupeResult:
        """Remove duplicates from a playlist."""
        return self.deduplicator.remove_duplicates(playlist_uri, include_similar)
    
    def duplicate(
        self,
        source_uri: str,
        new_name: Optional[str] = None
    ) -> DuplicatePlaylistResult:
        """Duplicate a playlist."""
        return self.duplicator.duplicate(source_uri, new_name)
    
    def analyze(self, playlist_uri: str, detailed: bool = False) -> Dict:
        """Analyze a playlist."""
        return self.analyzer.analyze(playlist_uri, detailed)
    
    def remove_artist(self, playlist_uri: str, artist_uri: str) -> Tuple[int, str]:
        """Remove all tracks by an artist."""
        return self.artist_remover.remove_artist_tracks(playlist_uri, artist_uri)


# ============================================
# GLOBAL TOOLS INSTANCE
# ============================================

playlist_tools: Optional[PlaylistTools] = None


def init_playlist_tools(spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
    """Initialize global playlist tools instance."""
    global playlist_tools
    playlist_tools = PlaylistTools(spotify_api, playlist_ops)
    return playlist_tools


# ============================================
# CONVENIENCE FUNCTIONS
# ============================================

def sort_playlist_by_date(playlist_uri: str, newest_first: bool = True) -> bool:
    """
    Sort a playlist by release date.
    
    Args:
        playlist_uri: Playlist URI
        newest_first: If True, newest first
        
    Returns:
        True if successful
    """
    if not playlist_tools:
        raise RuntimeError("Playlist tools not initialized")
    
    result = playlist_tools.sorter.sort_by_release_date(playlist_uri, newest_first)
    return result.success


def quick_dedupe(playlist_uri: str) -> int:
    """
    Quick deduplication of a playlist.
    
    Args:
        playlist_uri: Playlist URI
        
    Returns:
        Number of duplicates removed
    """
    if not playlist_tools:
        raise RuntimeError("Playlist tools not initialized")
    
    result = playlist_tools.deduplicate(playlist_uri)
    return result.duplicates_removed


# End of Part 7
# ============================================
# PART 8: IMPORT/EXPORT FUNCTIONALITY
# ============================================

"""
Import and export functionality for profiles, artists,
and configuration data.
"""

from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import json
import time


class ExportFormat(Enum):
    """Supported export formats."""
    JSON = "json"
    # Future: CSV, etc.


class ImportMode(Enum):
    """How to handle duplicate profiles during import."""
    SKIP = "skip"          # Keep existing, skip duplicates
    REPLACE = "replace"    # Replace existing with imported
    RENAME = "rename"      # Import with new name


@dataclass
class ExportResult:
    """Result of an export operation."""
    success: bool
    file_path: str = ""
    profiles_exported: int = 0
    artists_exported: int = 0
    error_message: str = ""


@dataclass
class ImportResult:
    """Result of an import operation."""
    success: bool
    profiles_imported: int = 0
    profiles_skipped: int = 0
    profiles_replaced: int = 0
    artists_imported: int = 0
    error_message: str = ""
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


# ============================================
# EXPORT DATA STRUCTURES
# ============================================

@dataclass
class ExportMetadata:
    """Metadata for exported files."""
    version: str = "2.0"
    export_date: str = ""
    app_name: str = APP_NAME
    app_version: str = APP_VERSION
    
    def to_dict(self) -> Dict:
        return {
            'version': self.version,
            'export_date': self.export_date or datetime.now().isoformat(),
            'app_name': self.app_name,
            'app_version': self.app_version,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ExportMetadata':
        return cls(
            version=data.get('version', '1.0'),
            export_date=data.get('export_date', ''),
            app_name=data.get('app_name', ''),
            app_version=data.get('app_version', ''),
        )


# ============================================
# PROFILE EXPORTER
# ============================================

class ProfileExporter:
    """Handles exporting profiles to files."""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
    
    def export_profile(
        self,
        profile: Profile,
        file_path: str,
        include_tracked: bool = True
    ) -> ExportResult:
        """
        Export a single profile to a file.
        
        Args:
            profile: Profile to export
            file_path: Output file path
            include_tracked: Include tracked releases history
            
        Returns:
            ExportResult
        """
        result = ExportResult(success=False)
        
        try:
            # Prepare profile data
            profile_data = profile.to_dict()
            
            # Optionally remove tracked releases
            if not include_tracked:
                profile_data['tracked_releases'] = {}
            
            # Build export structure
            export_data = {
                **ExportMetadata().to_dict(),
                'type': 'single',
                'profile': profile_data,
            }
            
            # Write to file
            file_path = self._ensure_extension(file_path)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            
            result.success = True
            result.file_path = file_path
            result.profiles_exported = 1
            result.artists_exported = len(profile.artists)
            
            return result
            
        except Exception as e:
            result.error_message = str(e)
            return result
    
    def export_all_profiles(
        self,
        file_path: str,
        include_tracked: bool = True
    ) -> ExportResult:
        """
        Export all profiles to a single file.
        
        Args:
            file_path: Output file path
            include_tracked: Include tracked releases history
            
        Returns:
            ExportResult
        """
        result = ExportResult(success=False)
        
        try:
            profiles = self.config_manager.config.profiles
            
            # Prepare profiles data
            profiles_data = []
            total_artists = 0
            
            for profile in profiles:
                profile_data = profile.to_dict()
                
                if not include_tracked:
                    profile_data['tracked_releases'] = {}
                
                profiles_data.append(profile_data)
                total_artists += len(profile.artists)
            
            # Build export structure
            export_data = {
                **ExportMetadata().to_dict(),
                'type': 'all',
                'active_profile_id': self.config_manager.config.active_profile_id,
                'profile_count': len(profiles),
                'profiles': profiles_data,
            }
            
            # Write to file
            file_path = self._ensure_extension(file_path)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            
            result.success = True
            result.file_path = file_path
            result.profiles_exported = len(profiles)
            result.artists_exported = total_artists
            
            return result
            
        except Exception as e:
            result.error_message = str(e)
            return result
    
    def export_artists_only(
        self,
        profile: Profile,
        file_path: str
    ) -> ExportResult:
        """
        Export only the artists list from a profile.
        
        Args:
            profile: Profile to export artists from
            file_path: Output file path
            
        Returns:
            ExportResult
        """
        result = ExportResult(success=False)
        
        try:
            artists_data = [a.to_dict() for a in profile.artists]
            
            export_data = {
                **ExportMetadata().to_dict(),
                'type': 'artists',
                'source_profile': profile.name,
                'artist_count': len(artists_data),
                'artists': artists_data,
            }
            
            file_path = self._ensure_extension(file_path)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            
            result.success = True
            result.file_path = file_path
            result.artists_exported = len(artists_data)
            
            return result
            
        except Exception as e:
            result.error_message = str(e)
            return result
    
    def _ensure_extension(self, file_path: str) -> str:
        """Ensure file has .json extension."""
        if not file_path.endswith('.json'):
            file_path += '.json'
        return file_path
    
    def generate_filename(
        self,
        profile_name: Optional[str] = None,
        export_type: str = 'profile'
    ) -> str:
        """
        Generate a default filename for export.
        
        Args:
            profile_name: Profile name (optional)
            export_type: Type of export
            
        Returns:
            Suggested filename
        """
        date_str = datetime.now().strftime("%Y%m%d")
        
        if profile_name:
            # Sanitize profile name
            safe_name = "".join(
                c if c.isalnum() or c in '-_' else '-'
                for c in profile_name.lower()
            ).strip('-')
            return f"anr-{export_type}-{safe_name}-{date_str}.json"
        else:
            return f"anr-{export_type}-all-{date_str}.json"


# ============================================
# PROFILE IMPORTER
# ============================================

class ProfileImporter:
    """Handles importing profiles from files."""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager

    def _camel_to_snake(self, name: str) -> str:
        """Convert camelCase to snake_case."""
        import re
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    
    def _normalize_keys(self, data) -> Any:
        """Convert all dict keys from camelCase to snake_case recursively."""
        if isinstance(data, dict):
            return {self._camel_to_snake(k): self._normalize_keys(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._normalize_keys(item) for item in data]
        return data
    
    def import_from_file(
        self,
        file_path: str,
        mode: ImportMode = ImportMode.SKIP,
        new_name: Optional[str] = None
    ) -> ImportResult:
        """
        Import profiles from a file.
        
        Args:
            file_path: Path to import file
            mode: How to handle duplicates
            new_name: Optional name for single profile import
            
        Returns:
            ImportResult
        """
        result = ImportResult(success=False)
        
        try:
            # Read file
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            data = self._normalize_keys(data)
            
            # Detect format and type
            version = data.get('version', '1.0')
            export_type = data.get('type', self._detect_type(data))
            
            print_info(f"Detected format: v{version}, type: {export_type}")
            
            # Handle different types
            if export_type == 'all':
                return self._import_all_profiles(data, mode)
            elif export_type == 'single':
                return self._import_single_profile(data, mode, new_name)
            elif export_type == 'artists':
                return self._import_artists(data)
            else:
                # Legacy format detection
                if 'profile' in data:
                    return self._import_single_profile(data, mode, new_name)
                elif 'profiles' in data:
                    return self._import_all_profiles(data, mode)
                elif 'artists' in data:
                    return self._import_artists(data)
                else:
                    result.error_message = "Unknown file format"
                    return result
                    
        except json.JSONDecodeError as e:
            result.error_message = f"Invalid JSON: {e}"
            return result
        except FileNotFoundError:
            result.error_message = f"File not found: {file_path}"
            return result
        except Exception as e:
            result.error_message = str(e)
            return result
    
    def _detect_type(self, data: Dict) -> str:
        """Detect export type from data structure."""
        if 'profiles' in data and isinstance(data['profiles'], list):
            return 'all'
        elif 'profile' in data:
            return 'single'
        elif 'artists' in data and 'profile' not in data:
            return 'artists'
        return 'unknown'
    
    def _import_single_profile(
        self,
        data: Dict,
        mode: ImportMode,
        new_name: Optional[str]
    ) -> ImportResult:
        """Import a single profile."""
        result = ImportResult(success=False)
        
        profile_data = data.get('profile', data)
        
        if not profile_data.get('artists') and not profile_data.get('name'):
            result.error_message = "Invalid profile data"
            return result
        
        # Check for existing profile with same name
        existing_names = {p.name.lower(): p for p in self.config_manager.config.profiles}
        profile_name = new_name or profile_data.get('name', 'Imported Profile')
        
        if profile_name.lower() in existing_names:
            if mode == ImportMode.SKIP:
                result.success = True
                result.profiles_skipped = 1
                result.warnings.append(f"Profile '{profile_name}' already exists, skipped")
                return result
            elif mode == ImportMode.REPLACE:
                existing = existing_names[profile_name.lower()]
                self._update_profile(existing, profile_data)
                result.success = True
                result.profiles_replaced = 1
                result.artists_imported = len(existing.artists)
                return result
            elif mode == ImportMode.RENAME:
                profile_name = self._generate_unique_name(profile_name)
        
        # Create new profile
        new_profile = self._create_profile_from_data(profile_data, profile_name)
        
        self.config_manager.config.profiles.append(new_profile)
        self.config_manager.config.active_profile_id = new_profile.id
        self.config_manager.save()
        
        result.success = True
        result.profiles_imported = 1
        result.artists_imported = len(new_profile.artists)
        
        return result
    
    def _import_all_profiles(
        self,
        data: Dict,
        mode: ImportMode
    ) -> ImportResult:
        """Import multiple profiles."""
        result = ImportResult(success=False)
        
        profiles_data = data.get('profiles', [])
        
        if not profiles_data:
            result.error_message = "No profiles found in file"
            return result
        
        existing_names = {p.name.lower(): p for p in self.config_manager.config.profiles}
        
        for profile_data in profiles_data:
            if not profile_data.get('name'):
                result.warnings.append("Skipped profile with no name")
                continue
            
            profile_name = profile_data.get('name')
            
            if profile_name.lower() in existing_names:
                if mode == ImportMode.SKIP:
                    result.profiles_skipped += 1
                    continue
                elif mode == ImportMode.REPLACE:
                    existing = existing_names[profile_name.lower()]
                    self._update_profile(existing, profile_data)
                    result.profiles_replaced += 1
                    result.artists_imported += len(existing.artists)
                    continue
                elif mode == ImportMode.RENAME:
                    profile_name = self._generate_unique_name(profile_name)
            
            # Create new profile
            new_profile = self._create_profile_from_data(profile_data, profile_name)
            self.config_manager.config.profiles.append(new_profile)
            result.profiles_imported += 1
            result.artists_imported += len(new_profile.artists)
            
            # Update existing names map
            existing_names[profile_name.lower()] = new_profile
        
        self.config_manager.save()
        result.success = True
        
        return result
    
    def _import_artists(self, data: Dict) -> ImportResult:
        """Import artists into the active profile."""
        result = ImportResult(success=False)
        
        artists_data = data.get('artists', [])
        
        if not artists_data:
            result.error_message = "No artists found in file"
            return result
        
        profile = self.config_manager.get_active_profile()
        existing_uris = {a.uri for a in profile.artists}
        
        added = 0
        skipped = 0
        
        for artist_data in artists_data:
            uri = artist_data.get('uri', '')
            
            if uri in existing_uris:
                skipped += 1
                continue
            
            artist = Artist.from_dict(artist_data)
            profile.artists.append(artist)
            existing_uris.add(uri)
            added += 1
        
        self.config_manager.save()
        
        result.success = True
        result.artists_imported = added
        
        if skipped > 0:
            result.warnings.append(f"Skipped {skipped} artists (already tracked)")
        
        return result
    
    def _create_profile_from_data(
        self,
        data: Dict,
        name: str
    ) -> Profile:
        """Create a Profile from imported data."""
        # Generate new ID
        profile_id = generate_id()
        
        # Parse artists
        artists = []
        for artist_data in data.get('artists', []):
            if isinstance(artist_data, dict):
                artists.append(Artist.from_dict(artist_data))
        
        # Create profile with defaults for missing fields
        return Profile(
            id=profile_id,
            name=name,
            artists=artists,
            playlist_uri=data.get('playlist_uri', ''),
            playlist_name=data.get('playlist_name', ''),
            check_interval=data.get('check_interval', DEFAULT_VALUES['CHECK_INTERVAL']),
            last_check=None,  # Reset last check
            tracked_releases=data.get('tracked_releases', {}),
            days_to_check=data.get('days_to_check', DEFAULT_VALUES['DAYS_TO_CHECK']),
            sort_by_date=data.get('sort_by_date', True),
            skip_remixes=data.get('skip_remixes', False),
            skip_low_popularity=data.get('skip_low_popularity', False),
            min_popularity=data.get('min_popularity', DEFAULT_VALUES['MIN_POPULARITY']),
            skip_long_albums=data.get('skip_long_albums', False),
            max_songs=data.get('max_songs', DEFAULT_VALUES['MAX_SONGS']),
            limit_songs_per_album=data.get('limit_songs_per_album', False),
            max_songs_per_album=data.get('max_songs_per_album', DEFAULT_VALUES['MAX_SONGS_PER_ALBUM']),
        )
    
    def _update_profile(self, profile: Profile, data: Dict):
        """Update an existing profile with imported data."""
        # Update artists
        profile.artists = []
        for artist_data in data.get('artists', []):
            if isinstance(artist_data, dict):
                profile.artists.append(Artist.from_dict(artist_data))
        
        # Update settings
        profile.playlist_uri = data.get('playlist_uri', profile.playlist_uri)
        profile.playlist_name = data.get('playlist_name', profile.playlist_name)
        profile.check_interval = data.get('check_interval', profile.check_interval)
        profile.days_to_check = data.get('days_to_check', profile.days_to_check)
        profile.sort_by_date = data.get('sort_by_date', profile.sort_by_date)
        profile.skip_remixes = data.get('skip_remixes', profile.skip_remixes)
        profile.skip_low_popularity = data.get('skip_low_popularity', profile.skip_low_popularity)
        profile.min_popularity = data.get('min_popularity', profile.min_popularity)
        profile.skip_long_albums = data.get('skip_long_albums', profile.skip_long_albums)
        profile.max_songs = data.get('max_songs', profile.max_songs)
        profile.limit_songs_per_album = data.get('limit_songs_per_album', profile.limit_songs_per_album)
        profile.max_songs_per_album = data.get('max_songs_per_album', profile.max_songs_per_album)
        
        # Optionally update tracked releases
        if data.get('tracked_releases'):
            profile.tracked_releases = data.get('tracked_releases', {})
    
    def _generate_unique_name(self, base_name: str) -> str:
        """Generate a unique profile name."""
        existing_names = {p.name.lower() for p in self.config_manager.config.profiles}
        
        new_name = f"{base_name} (imported)"
        counter = 1
        
        while new_name.lower() in existing_names:
            counter += 1
            new_name = f"{base_name} (imported {counter})"
        
        return new_name


# ============================================
# IMPORT/EXPORT MANAGER
# ============================================

class ImportExportManager:
    """Central manager for import/export operations."""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.exporter = ProfileExporter(config_manager)
        self.importer = ProfileImporter(config_manager)
    
    # ============================================
    # EXPORT METHODS
    # ============================================
    
    def export_current_profile(
        self,
        file_path: Optional[str] = None,
        include_tracked: bool = True
    ) -> ExportResult:
        """Export the current active profile."""
        profile = self.config_manager.get_active_profile()
        
        if not file_path:
            file_path = self.exporter.generate_filename(profile.name, 'profile')
        
        return self.exporter.export_profile(profile, file_path, include_tracked)
    
    def export_all(
        self,
        file_path: Optional[str] = None,
        include_tracked: bool = True
    ) -> ExportResult:
        """Export all profiles."""
        if not file_path:
            file_path = self.exporter.generate_filename(None, 'all-profiles')
        
        return self.exporter.export_all_profiles(file_path, include_tracked)
    
    def export_artists(
        self,
        file_path: Optional[str] = None
    ) -> ExportResult:
        """Export artists from current profile."""
        profile = self.config_manager.get_active_profile()
        
        if not file_path:
            file_path = self.exporter.generate_filename(profile.name, 'artists')
        
        return self.exporter.export_artists_only(profile, file_path)
    
    # ============================================
    # IMPORT METHODS
    # ============================================
    
    def import_file(
        self,
        file_path: str,
        mode: ImportMode = ImportMode.SKIP
    ) -> ImportResult:
        """Import from a file."""
        return self.importer.import_from_file(file_path, mode)
    
    def preview_import(self, file_path: str) -> Dict:
        """
        Preview what would be imported without actually importing.
        
        Args:
            file_path: Path to import file
            
        Returns:
            Dictionary with preview information
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            preview = {
                'valid': True,
                'version': data.get('version', '1.0'),
                'export_date': data.get('export_date', 'Unknown'),
                'type': data.get('type', 'unknown'),
                'profiles': [],
                'total_artists': 0,
            }
            
            if 'profiles' in data:
                for p in data['profiles']:
                    artist_count = len(p.get('artists', []))
                    preview['profiles'].append({
                        'name': p.get('name', 'Unknown'),
                        'artists': artist_count,
                        'playlist': p.get('playlist_name', 'Not set'),
                    })
                    preview['total_artists'] += artist_count
            elif 'profile' in data:
                p = data['profile']
                artist_count = len(p.get('artists', []))
                preview['profiles'].append({
                    'name': p.get('name', 'Unknown'),
                    'artists': artist_count,
                    'playlist': p.get('playlist_name', 'Not set'),
                })
                preview['total_artists'] = artist_count
            elif 'artists' in data:
                preview['type'] = 'artists'
                preview['total_artists'] = len(data['artists'])
            
            return preview
            
        except Exception as e:
            return {
                'valid': False,
                'error': str(e),
            }
    
    def display_preview(self, file_path: str):
        """Display import preview."""
        preview = self.preview_import(file_path)
        
        if not preview.get('valid'):
            print_error(f"Invalid file: {preview.get('error', 'Unknown error')}")
            return
        
        if RICH_AVAILABLE:
            from rich.panel import Panel
            from rich.table import Table
            
            lines = [
                f"[bold]File:[/] {Path(file_path).name}",
                f"[bold]Format Version:[/] {preview['version']}",
                f"[bold]Export Date:[/] {preview['export_date']}",
                f"[bold]Type:[/] {preview['type']}",
                f"[bold]Total Artists:[/] {preview['total_artists']}",
            ]
            
            panel = Panel(
                "\n".join(lines),
                title="[bold cyan]Import Preview[/]",
                border_style="cyan"
            )
            console.print(panel)
            
            if preview['profiles']:
                table = Table(title="Profiles in File", show_header=True, header_style="bold")
                table.add_column("Name", style="white")
                table.add_column("Artists", justify="right")
                table.add_column("Playlist")
                
                for p in preview['profiles']:
                    table.add_row(
                        p['name'],
                        str(p['artists']),
                        p['playlist'][:30] if p['playlist'] else 'Not set'
                    )
                
                console.print(table)
        else:
            print(f"\n=== Import Preview ===")
            print(f"  File: {Path(file_path).name}")
            print(f"  Format Version: {preview['version']}")
            print(f"  Export Date: {preview['export_date']}")
            print(f"  Type: {preview['type']}")
            print(f"  Total Artists: {preview['total_artists']}")
            
            if preview['profiles']:
                print("\n  Profiles:")
                for p in preview['profiles']:
                    print(f"    - {p['name']}: {p['artists']} artists")
            print()


# ============================================
# INTERACTIVE IMPORT/EXPORT
# ============================================

class ImportExportMenu:
    """Interactive menu for import/export operations."""
    
    def __init__(self, import_export_manager: ImportExportManager):
        self.manager = import_export_manager
    
    def prompt(self, message: str, default: str = "") -> str:
        """Prompt for input."""
        if RICH_AVAILABLE:
            return Prompt.ask(message, default=default) if default else Prompt.ask(message)
        else:
            prompt_text = f"{message} [{default}]: " if default else f"{message}: "
            result = input(prompt_text).strip()
            return result if result else default
    
    def confirm(self, message: str, default: bool = False) -> bool:
        """Prompt for confirmation."""
        if RICH_AVAILABLE:
            return Confirm.ask(message, default=default)
        else:
            suffix = " [Y/n]: " if default else " [y/N]: "
            result = input(message + suffix).strip().lower()
            if not result:
                return default
            return result in ('y', 'yes')
    
    def run_export_current(self):
        """Interactive export of current profile."""
        profile = self.manager.config_manager.get_active_profile()
        
        print_info(f"Exporting profile: {profile.name}")
        print_info(f"  Artists: {len(profile.artists)}")
        print_info(f"  Tracked releases: {len(profile.tracked_releases)}")
        print()
        
        # Ask about tracked releases
        include_tracked = self.confirm(
            "Include tracked releases history? (larger file)",
            default=True
        )
        
        # Get filename
        default_name = self.manager.exporter.generate_filename(profile.name, 'profile')
        file_path = self.prompt("Output filename", default=default_name)
        
        if not file_path:
            print_warning("Cancelled")
            return
        
        # Export
        result = self.manager.export_current_profile(file_path, include_tracked)
        
        if result.success:
            print_success(f"Exported to: {result.file_path}")
            print_info(f"  Profile: {profile.name}")
            print_info(f"  Artists: {result.artists_exported}")
        else:
            print_error(f"Export failed: {result.error_message}")
    
    def run_export_all(self):
        """Interactive export of all profiles."""
        profiles = self.manager.config_manager.config.profiles
        total_artists = sum(len(p.artists) for p in profiles)
        
        print_info(f"Exporting {len(profiles)} profiles")
        print_info(f"  Total artists: {total_artists}")
        print()
        
        # Ask about tracked releases
        include_tracked = self.confirm(
            "Include tracked releases history? (larger file)",
            default=False
        )
        
        # Get filename
        default_name = self.manager.exporter.generate_filename(None, 'all-profiles')
        file_path = self.prompt("Output filename", default=default_name)
        
        if not file_path:
            print_warning("Cancelled")
            return
        
        # Export
        result = self.manager.export_all(file_path, include_tracked)
        
        if result.success:
            print_success(f"Exported to: {result.file_path}")
            print_info(f"  Profiles: {result.profiles_exported}")
            print_info(f"  Artists: {result.artists_exported}")
        else:
            print_error(f"Export failed: {result.error_message}")
    
    def run_export_artists(self):
        """Interactive export of artists only."""
        profile = self.manager.config_manager.get_active_profile()
        
        if not profile.artists:
            print_warning("No artists to export")
            return
        
        print_info(f"Exporting {len(profile.artists)} artists from: {profile.name}")
        print()
        
        # Get filename
        default_name = self.manager.exporter.generate_filename(profile.name, 'artists')
        file_path = self.prompt("Output filename", default=default_name)
        
        if not file_path:
            print_warning("Cancelled")
            return
        
        # Export
        result = self.manager.export_artists(file_path)
        
        if result.success:
            print_success(f"Exported to: {result.file_path}")
            print_info(f"  Artists: {result.artists_exported}")
        else:
            print_error(f"Export failed: {result.error_message}")
    
    def run_import(self):
        """Interactive import."""
        file_path = self.prompt("Enter file path to import")
        
        if not file_path:
            print_warning("Cancelled")
            return
        
        # Check file exists
        if not Path(file_path).exists():
            print_error(f"File not found: {file_path}")
            return
        
        # Show preview
        self.manager.display_preview(file_path)
        
        # Ask for import mode
        print("\nHow should duplicate profiles be handled?")
        print("  1. Skip - Keep existing, don't import duplicates")
        print("  2. Replace - Overwrite existing with imported")
        print("  3. Rename - Import with new name")
        print("  0. Cancel")
        print()
        
        try:
            if RICH_AVAILABLE:
                choice = IntPrompt.ask("Choice", default=1)
            else:
                choice = int(input("Choice [1]: ").strip() or "1")
        except ValueError:
            choice = 1
        
        if choice == 0:
            print_warning("Cancelled")
            return
        
        mode_map = {
            1: ImportMode.SKIP,
            2: ImportMode.REPLACE,
            3: ImportMode.RENAME,
        }
        mode = mode_map.get(choice, ImportMode.SKIP)
        
        # Confirm
        if not self.confirm("Proceed with import?", default=True):
            print_warning("Cancelled")
            return
        
        # Import
        result = self.manager.import_file(file_path, mode)
        
        if result.success:
            print_success("Import complete!")
            if result.profiles_imported:
                print_info(f"  Profiles imported: {result.profiles_imported}")
            if result.profiles_replaced:
                print_info(f"  Profiles replaced: {result.profiles_replaced}")
            if result.profiles_skipped:
                print_info(f"  Profiles skipped: {result.profiles_skipped}")
            if result.artists_imported:
                print_info(f"  Artists imported: {result.artists_imported}")
            
            for warning in result.warnings:
                print_warning(f"  {warning}")
        else:
            print_error(f"Import failed: {result.error_message}")


# ============================================
# GLOBAL INSTANCE
# ============================================

import_export_manager: Optional[ImportExportManager] = None
import_export_menu: Optional[ImportExportMenu] = None


def init_import_export(config_manager: ConfigManager):
    """Initialize global import/export instances."""
    global import_export_manager, import_export_menu
    
    import_export_manager = ImportExportManager(config_manager)
    import_export_menu = ImportExportMenu(import_export_manager)
    
    return import_export_manager


# ============================================
# CONVENIENCE FUNCTIONS
# ============================================

def export_profile(
    profile: Optional[Profile] = None,
    file_path: Optional[str] = None,
    include_tracked: bool = True
) -> str:
    """
    Quick export of a profile.
    
    Args:
        profile: Profile to export (defaults to active)
        file_path: Output path (auto-generated if None)
        include_tracked: Include tracked releases
        
    Returns:
        Path to exported file
    """
    if not import_export_manager:
        raise RuntimeError("Import/export not initialized")
    
    if profile is None:
        result = import_export_manager.export_current_profile(file_path, include_tracked)
    else:
        if not file_path:
            file_path = import_export_manager.exporter.generate_filename(profile.name, 'profile')
        result = import_export_manager.exporter.export_profile(profile, file_path, include_tracked)
    
    if result.success:
        return result.file_path
    else:
        raise RuntimeError(f"Export failed: {result.error_message}")


def import_profile(file_path: str, mode: str = 'skip') -> int:
    """
    Quick import of profiles.
    
    Args:
        file_path: Path to import file
        mode: 'skip', 'replace', or 'rename'
        
    Returns:
        Number of profiles imported
    """
    if not import_export_manager:
        raise RuntimeError("Import/export not initialized")
    
    mode_map = {
        'skip': ImportMode.SKIP,
        'replace': ImportMode.REPLACE,
        'rename': ImportMode.RENAME,
    }
    import_mode = mode_map.get(mode.lower(), ImportMode.SKIP)
    
    result = import_export_manager.import_file(file_path, import_mode)
    
    if result.success:
        return result.profiles_imported + result.profiles_replaced
    else:
        raise RuntimeError(f"Import failed: {result.error_message}")


# End of Part 8
# ============================================
# PART 9: TERMINAL UI AND MENU SYSTEM
# ============================================

"""
Terminal user interface with menus, navigation,
and interactive commands.
"""

from typing import Optional, List, Dict, Callable, Any
from dataclasses import dataclass
from enum import Enum
import os
import sys


# ============================================
# MENU ITEM DEFINITIONS
# ============================================

@dataclass
class MenuItem:
    """Represents a menu item."""
    key: str
    label: str
    action: Optional[Callable] = None
    submenu: Optional['Menu'] = None
    enabled: bool = True
    description: str = ""
    
    def is_separator(self) -> bool:
        """Check if this item is a separator."""
        return self.key == "" or self.label == "---"


class Menu:
    """Represents a menu with items."""
    
    def __init__(
        self,
        title: str,
        items: List[MenuItem] = None,
        back_label: str = "Back",
        show_back: bool = True
    ):
        self.title = title
        self.items = items or []
        self.back_label = back_label
        self.show_back = show_back
    
    def add_item(self, item: MenuItem):
        """Add an item to the menu."""
        self.items.append(item)
    
    def add_separator(self):
        """Add a visual separator."""
        self.items.append(MenuItem(key="", label="---", enabled=True))
    
    def display(self):
        """Display the menu."""
        if RICH_AVAILABLE:
            from rich.panel import Panel
            from rich.text import Text
            
            lines = []
            for item in self.items:
                if item.is_separator():
                    # Separator line
                    lines.append("[dim]" + "─" * 40 + "[/]")
                else:
                    # Regular menu item
                    if item.enabled:
                        # Enabled item - bright colors
                        key_style = "bold cyan"
                        label_style = "bold white"
                        desc_style = "dim"
                    else:
                        # Disabled item - grayed out
                        key_style = "dim"
                        label_style = "dim"
                        desc_style = "dim"
                    
                    # Build the line
                    line = f"  [{key_style}]{item.key:>2}[/]  [{label_style}]{item.label}[/]"
                    if item.description:
                        line += f"  [{desc_style}]{item.description}[/]"
                    lines.append(line)
            
            # Add back option if enabled
            if self.show_back:
                lines.append("")
                lines.append(f"  [bold cyan] 0[/]  [bold white]{self.back_label}[/]")
            
            # Create and display panel
            panel = Panel(
                "\n".join(lines),
                title=f"[bold]{self.title}[/]",
                border_style="cyan",
                padding=(1, 2)
            )
            console.print(panel)
        else:
            # Fallback for non-rich display
            print(f"\n{'='*60}")
            print(f"  {self.title}")
            print(f"{'='*60}")
            
            for item in self.items:
                if item.is_separator():
                    print(f"  {'-'*50}")
                else:
                    status = "" if item.enabled else " [disabled]"
                    desc = f" - {item.description}" if item.description else ""
                    print(f"  {item.key:>2}. {item.label}{desc}{status}")
            
            if self.show_back:
                print()
                print(f"   0. {self.back_label}")
            
            print()
    
    def get_choice(self) -> Optional[str]:
        """Get user's menu choice."""
        try:
            if RICH_AVAILABLE:
                choice = Prompt.ask("Choice").strip().lower()
            else:
                choice = input("Choice: ").strip().lower()
            return choice
        except (KeyboardInterrupt, EOFError):
            return "0"
    
    def find_item(self, key: str) -> Optional[MenuItem]:
        """Find menu item by key."""
        for item in self.items:
            if item.key.lower() == key.lower():
                return item
        return None
    
    def run(self) -> Optional[str]:
        """Display menu and get valid choice."""
        self.display()
        return self.get_choice()


# ============================================
# APPLICATION STATE
# ============================================

class AppState:
    """Holds application state for the UI."""
    
    def __init__(self):
        self.running = True
        self.current_menu = "main"
        self.message: Optional[str] = None
        self.message_type: str = "info"  # info, success, error, warning
    
    def set_message(self, message: str, msg_type: str = "info"):
        """Set a message to display."""
        self.message = message
        self.message_type = msg_type
    
    def clear_message(self):
        """Clear the current message."""
        self.message = None
    
    def show_message(self):
        """Display the current message if any."""
        if not self.message:
            return
        
        if self.message_type == "success":
            print_success(self.message)
        elif self.message_type == "error":
            print_error(self.message)
        elif self.message_type == "warning":
            print_warning(self.message)
        else:
            print_info(self.message)
        
        print()
        self.clear_message()


# ============================================
# HEADER DISPLAY
# ============================================

def display_header():
    """Display the application header."""
    if RICH_AVAILABLE:
        from rich.panel import Panel
        from rich.text import Text
        
        header = Text()
        header.append("♪ ", style="bold green")
        header.append(APP_NAME, style="bold white")
        header.append(f" v{APP_VERSION}", style="dim")
        
        console.print(Panel(header, border_style="green"))
    else:
        print()
        print(f"{'='*50}")
        print(f"  ♪ {APP_NAME} v{APP_VERSION}")
        print(f"{'='*50}")
        print()


def display_status_bar(config_manager: ConfigManager):
    """Display current status information."""
    profile = config_manager.get_active_profile()
    
    artists = len(profile.artists)
    playlist = profile.playlist_name or "Not set"
    last_check = "Never"
    
    if profile.last_check:
        last_dt = datetime.fromtimestamp(profile.last_check)
        last_check = last_dt.strftime("%Y-%m-%d %H:%M")
    
    if RICH_AVAILABLE:
        from rich.table import Table
        
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="bold cyan")
        table.add_column(style="white")
        table.add_column(style="bold cyan")
        table.add_column(style="white")
        
        table.add_row(
            "Profile:", profile.name,
            "Artists:", str(artists)
        )
        table.add_row(
            "Playlist:", playlist[:30] + "..." if len(playlist) > 30 else playlist,
            "Last Check:", last_check
        )
        
        console.print(table)
        print()
    else:
        print(f"  Profile: {profile.name} | Artists: {artists}")
        print(f"  Playlist: {playlist[:40]} | Last Check: {last_check}")
        print()


# ============================================
# MAIN MENU BUILDER
# ============================================

class MenuBuilder:
    """Builds the application menus."""
    
    def __init__(
        self,
        config_manager: ConfigManager,
        profile_manager: ProfileManager,
        app_state: AppState
    ):
        self.config_manager = config_manager
        self.profile_manager = profile_manager
        self.app_state = app_state
    
    def build_main_menu(self) -> Menu:
        """Build the main menu."""
        profile = self.config_manager.get_active_profile()
        has_playlist = bool(profile.playlist_uri)
        has_artists = len(profile.artists) > 0
        
        menu = Menu("Main Menu", show_back=False)
        
        menu.add_item(MenuItem(
            key="1",
            label="Check for New Releases",
            description="Scan tracked artists",
            enabled=has_playlist and has_artists
        ))
        
        menu.add_item(MenuItem(
            key="2",
            label="Check All Profiles",
            description=f"Scan all {len(self.config_manager.config.profiles)} profiles"
        ))
        
        menu.add_separator()
        
        menu.add_item(MenuItem(
            key="3",
            label="Artists",
            description=f"{len(profile.artists)} tracked"
        ))
        
        menu.add_item(MenuItem(
            key="4",
            label="Playlist",
            description=profile.playlist_name or "Not configured"
        ))
        
        menu.add_item(MenuItem(
            key="5",
            label="Playlist Tools",
            description="Sort, dedupe, analyze",
            enabled=has_playlist
        ))
        
        menu.add_separator()
        
        menu.add_item(MenuItem(
            key="6",
            label="Profiles",
            description=f"{len(self.config_manager.config.profiles)} profiles"
        ))
        
        menu.add_item(MenuItem(
            key="7",
            label="Settings",
            description="Configure options"
        ))
        
        menu.add_item(MenuItem(
            key="8",
            label="Import/Export",
            description="Backup & restore"
        ))
        
        menu.add_separator()
        
        menu.add_item(MenuItem(
            key="s",
            label="Schedule Status",
            description="View check schedule"
        ))
        
        menu.add_item(MenuItem(
            key="q",
            label="Quit",
            description="Exit application"
        ))
        
        return menu
    
    def build_artists_menu(self) -> Menu:
        """Build the artists management menu."""
        profile = self.config_manager.get_active_profile()
        has_artists = len(profile.artists) > 0
        has_playlist = bool(profile.playlist_uri)
        
        menu = Menu(f"Artists ({len(profile.artists)})")
        
        menu.add_item(MenuItem(
            key="1",
            label="View All Artists",
            enabled=has_artists
        ))
        
        menu.add_item(MenuItem(
            key="2",
            label="Search & Add Artist"
        ))
        
        menu.add_item(MenuItem(
            key="3",
            label="Add by URI/URL",
            description="Direct Spotify link"
        ))
        
        menu.add_item(MenuItem(
            key="4",
            label="Import from Playlist",
            description="Pull artists from a playlist",
            enabled=has_playlist
        ))
        
        menu.add_separator()
        
        menu.add_item(MenuItem(
            key="5",
            label="Remove Artist",
            enabled=has_artists
        ))
        
        menu.add_item(MenuItem(
            key="6",
            label="Refresh Artist Data",
            description="Update followers, images",
            enabled=has_artists
        ))
        
        menu.add_item(MenuItem(
            key="7",
            label="Clear All Artists",
            enabled=has_artists
        ))
        
        return menu
    
    def build_playlist_menu(self) -> Menu:
        """Build the playlist configuration menu."""
        profile = self.config_manager.get_active_profile()
        has_playlist = bool(profile.playlist_uri)
        
        menu = Menu("Playlist Configuration")
        
        menu.add_item(MenuItem(
            key="1",
            label="Select from My Playlists"
        ))
        
        menu.add_item(MenuItem(
            key="2",
            label="Enter Playlist URL/URI"
        ))
        
        menu.add_item(MenuItem(
            key="3",
            label="Create New Playlist"
        ))
        
        if has_playlist:
            menu.add_separator()
            
            menu.add_item(MenuItem(
                key="4",
                label="View Current Playlist Info"
            ))
            
            menu.add_item(MenuItem(
                key="5",
                label="Clear Playlist Setting"
            ))
        
        return menu
    
    def build_playlist_tools_menu(self) -> Menu:
        """Build the playlist tools menu."""
        menu = Menu("Playlist Tools")
        
        menu.add_item(MenuItem(
            key="1",
            label="Sort Playlist",
            description="By date, popularity, name..."
        ))
        
        menu.add_item(MenuItem(
            key="2",
            label="Remove Duplicates",
            description="Find & remove duplicate tracks"
        ))
        
        menu.add_item(MenuItem(
            key="3",
            label="Preview Duplicates",
            description="See duplicates without removing"
        ))
        
        menu.add_separator()
        
        menu.add_item(MenuItem(
            key="4",
            label="Analyze Playlist",
            description="View statistics"
        ))
        
        menu.add_item(MenuItem(
            key="5",
            label="Duplicate Playlist",
            description="Create a copy"
        ))
        
        menu.add_item(MenuItem(
            key="6",
            label="Remove Artist Tracks",
            description="Remove all tracks by an artist"
        ))
        
        return menu
    
    def build_profiles_menu(self) -> Menu:
        """Build the profiles management menu."""
        profiles = self.config_manager.config.profiles
        
        menu = Menu(f"Profiles ({len(profiles)})")
        
        menu.add_item(MenuItem(
            key="1",
            label="View All Profiles"
        ))
        
        menu.add_item(MenuItem(
            key="2",
            label="Switch Profile"
        ))
        
        menu.add_item(MenuItem(
            key="3",
            label="Create New Profile"
        ))
        
        menu.add_separator()
        
        menu.add_item(MenuItem(
            key="4",
            label="Rename Profile"
        ))
        
        menu.add_item(MenuItem(
            key="5",
            label="Duplicate Profile"
        ))
        
        menu.add_item(MenuItem(
            key="6",
            label="Delete Profile",
            enabled=len(profiles) > 1
        ))
        
        return menu
    
    def build_settings_menu(self) -> Menu:
        """Build the settings menu."""
        profile = self.config_manager.get_active_profile()
        
        menu = Menu("Settings")
        
        menu.add_item(MenuItem(
            key="1",
            label="Edit All Settings",
            description="Guided setup"
        ))
        
        menu.add_separator()
        
        menu.add_item(MenuItem(
            key="2",
            label=f"Check Interval: {profile.check_interval}h"
        ))
        
        days = profile.days_to_check if profile.days_to_check > 0 else "All"
        menu.add_item(MenuItem(
            key="3",
            label=f"Days to Check: {days}"
        ))
        
        menu.add_item(MenuItem(
            key="4",
            label=f"Sort by Date: {'Yes' if profile.sort_by_date else 'No'}"
        ))
        
        menu.add_item(MenuItem(
            key="5",
            label=f"Skip Remixes: {'Yes' if profile.skip_remixes else 'No'}"
        ))
        
        menu.add_item(MenuItem(
            key="6",
            label=f"Skip Low Popularity: {'Yes' if profile.skip_low_popularity else 'No'}"
        ))
        
        menu.add_item(MenuItem(
            key="7",
            label=f"Skip Long Albums: {'Yes' if profile.skip_long_albums else 'No'}"
        ))
        
        menu.add_item(MenuItem(
            key="8",
            label=f"Limit per Album: {'Yes' if profile.limit_songs_per_album else 'No'}"
        ))
        
        menu.add_separator()

        tracked_tracks = len(getattr(profile, 'tracked_tracks', {}))

        menu.add_item(MenuItem(
            key="r",
            label="Reset All Tracking",
            description=f"{len(profile.tracked_releases)} releases, {tracked_tracks} tracks"
        ))

        menu.add_item(MenuItem(
            key="t",
            label="Reset Tracked Tracks Only",
            description=f"{tracked_tracks} tracks (re-add filtered songs)"
        ))

        return menu
    
    def build_import_export_menu(self) -> Menu:
        """Build the import/export menu."""
        menu = Menu("Import / Export")
        
        menu.add_item(MenuItem(
            key="1",
            label="Export Current Profile"
        ))
        
        menu.add_item(MenuItem(
            key="2",
            label="Export All Profiles"
        ))
        
        menu.add_item(MenuItem(
            key="3",
            label="Export Artists Only"
        ))
        
        menu.add_separator()
        
        menu.add_item(MenuItem(
            key="4",
            label="Import from File"
        ))
        
        menu.add_item(MenuItem(
            key="5",
            label="Preview Import File"
        ))
        
        return menu


# ============================================
# MENU HANDLERS
# ============================================

class MenuHandler:
    """Handles menu actions and navigation."""
    
    def __init__(
        self,
        config_manager: ConfigManager,
        profile_manager: ProfileManager,
        profile_menu: ProfileMenu,
        app_state: AppState
    ):
        self.config_manager = config_manager
        self.profile_manager = profile_manager
        self.profile_menu = profile_menu
        self.app_state = app_state
    
    def prompt(self, message: str, default: str = "") -> str:
        """Prompt for input."""
        if RICH_AVAILABLE:
            return Prompt.ask(message, default=default) if default else Prompt.ask(message)
        else:
            prompt_text = f"{message} [{default}]: " if default else f"{message}: "
            result = input(prompt_text).strip()
            return result if result else default
    
    def confirm(self, message: str, default: bool = False) -> bool:
        """Confirm action."""
        if RICH_AVAILABLE:
            return Confirm.ask(message, default=default)
        else:
            suffix = " [Y/n]: " if default else " [y/N]: "
            result = input(message + suffix).strip().lower()
            return result in ('y', 'yes') if result else default
    
    def wait_for_key(self):
        """Wait for user to press Enter."""
        input("\nPress Enter to continue...")
    
    # ============================================
    # MAIN MENU HANDLERS
    # ============================================
    
    def handle_check_releases(self):
        """Handle check for new releases."""
        if interactive_checker:
            interactive_checker.run_check()
            self.wait_for_key()
    
    def handle_check_all(self):
        """Handle check all profiles."""
        if interactive_checker:
            interactive_checker.run_check_all()
            self.wait_for_key()
    
    def handle_schedule_status(self):
        """Show schedule status."""
        if scheduled_checker:
            scheduled_checker.display_schedule()
            self.wait_for_key()
    
    # ============================================
    # ARTISTS MENU HANDLERS
    # ============================================
    
    def handle_view_artists(self):
        """View all artists."""
        self.profile_manager.display_artists()
        self.wait_for_key()
    
    def handle_search_artist(self):
        """Search and add artist."""
        if artist_searcher:
            artist = artist_searcher.interactive_search()
            if artist:
                profile = self.config_manager.get_active_profile()
                self.profile_manager.add_artist_to_profile(profile, artist)
    
    def handle_add_artist_uri(self):
        """Add artist by URI/URL."""
        uri = self.prompt("Enter Spotify artist URL or URI")
        
        if not uri:
            return
        
        artist_id = parse_spotify_uri(uri, "artist")
        if not artist_id:
            print_error("Invalid artist URL/URI")
            return
        
        if spotify_api:
            print_info("Fetching artist info...")
            artist_data = spotify_api.get_artist(f"spotify:artist:{artist_id}")
            
            if artist_data:
                artist = spotify_api.artist_to_model(artist_data)
                profile = self.config_manager.get_active_profile()
                self.profile_manager.add_artist_to_profile(profile, artist)
            else:
                print_error("Could not find artist")
    
    def handle_import_from_playlist(self):
        """Import artists from a playlist."""
        if not playlist_selector or not spotify_api:
            return
        
        print_info("Select playlist to import artists from:")
        playlist = playlist_selector.select_playlist()
        
        if not playlist:
            return
        
        print_info(f"Scanning playlist: {playlist.get('name', 'Unknown')}...")
        
        # Get playlist tracks
        tracks = playlist_ops.get_playlist_tracks(playlist.get('uri', ''))
        
        if not tracks:
            print_warning("No tracks in playlist")
            return
        
        # Extract unique artists
        artist_uris = set()
        for track in tracks:
            # Get primary artist only
            if track.artists:
                # We need to fetch artist URI - construct it from name lookup
                pass
        
        # For simplicity, let's use a different approach
        print_info(f"Found {len(tracks)} tracks, extracting artists...")
        
        seen_artists = {}
        for track in tracks:
            track_data = spotify_api.get_track(track.uri)
            if track_data:
                for artist in track_data.get('artists', [])[:1]:  # Primary only
                    artist_uri = artist.get('uri')
                    if artist_uri and artist_uri not in seen_artists:
                        seen_artists[artist_uri] = artist.get('name', 'Unknown')
        
        if not seen_artists:
            print_warning("No artists found")
            return
        
        print_info(f"Found {len(seen_artists)} unique artists")
        
        # Check existing
        profile = self.config_manager.get_active_profile()
        existing_uris = {a.uri for a in profile.artists}
        
        new_artists = {uri: name for uri, name in seen_artists.items() if uri not in existing_uris}
        
        if not new_artists:
            print_info("All artists already tracked")
            return
        
        print_info(f"{len(new_artists)} new artists to add:")
        for name in list(new_artists.values())[:10]:
            print(f"  - {name}")
        if len(new_artists) > 10:
            print(f"  ... and {len(new_artists) - 10} more")
        
        if not self.confirm(f"Add {len(new_artists)} artists?", default=True):
            return
        
        # Add artists
        added = 0
        for uri, name in new_artists.items():
            artist_data = spotify_api.get_artist(uri)
            if artist_data:
                artist = spotify_api.artist_to_model(artist_data)
                profile.artists.append(artist)
                added += 1
        
        self.config_manager.save()
        print_success(f"Added {added} artists")
    
    def handle_remove_artist(self):
        """Remove an artist."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.artists:
            print_warning("No artists to remove")
            return
        
        self.profile_manager.display_artists()
        
        try:
            if RICH_AVAILABLE:
                choice = IntPrompt.ask(f"Enter artist number to remove (1-{len(profile.artists)}, 0 to cancel)")
            else:
                choice = int(input(f"Enter artist number (1-{len(profile.artists)}, 0 to cancel): ") or "0")
            
            if choice == 0:
                return
            
            artist = self.profile_manager.find_artist_by_index(profile, choice)
            if artist:
                if self.confirm(f"Remove '{artist.name}'?"):
                    self.profile_manager.remove_artist_from_profile(profile, artist.uri)
            else:
                print_error("Invalid selection")
                
        except ValueError:
            print_error("Invalid input")
    
    def handle_refresh_artists(self):
        """Refresh artist data."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.artists:
            print_warning("No artists to refresh")
            return
        
        if not self.confirm(f"Refresh {len(profile.artists)} artists?"):
            return
        
        print_info("Refreshing artist data...")
        
        updated = 0
        for i, artist in enumerate(profile.artists):
            print(f"  [{i+1}/{len(profile.artists)}] {artist.name}")
            
            if spotify_api:
                data = spotify_api.get_artist(artist.uri)
                if data:
                    profile.artists[i] = spotify_api.artist_to_model(data)
                    updated += 1
        
        self.config_manager.save()
        print_success(f"Updated {updated} artists")
    
    def handle_clear_artists(self):
        """Clear all artists."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.artists:
            print_warning("No artists to clear")
            return
        
        if self.confirm(f"Remove all {len(profile.artists)} artists?"):
            self.profile_manager.clear_all_artists(profile)
    
    # ============================================
    # PLAYLIST MENU HANDLERS
    # ============================================
    
    def handle_select_playlist(self):
        """Select playlist from list."""
        if playlist_selector:
            playlist = playlist_selector.select_playlist()
            if playlist:
                profile = self.config_manager.get_active_profile()
                profile.playlist_uri = playlist.get('uri', '')
                profile.playlist_name = playlist.get('name', '')
                self.config_manager.save()
                print_success(f"Set playlist to: {profile.playlist_name}")
    
    def handle_enter_playlist_uri(self):
        """Enter playlist URI manually."""
        uri = self.prompt("Enter Spotify playlist URL or URI")
        
        if not uri:
            return
        
        playlist_id = parse_spotify_uri(uri, "playlist")
        if not playlist_id:
            print_error("Invalid playlist URL/URI")
            return
        
        if playlist_ops:
            print_info("Fetching playlist info...")
            playlist = playlist_ops.get_playlist_details(f"spotify:playlist:{playlist_id}")
            
            if playlist:
                profile = self.config_manager.get_active_profile()
                profile.playlist_uri = playlist.get('uri', '')
                profile.playlist_name = playlist.get('name', '')
                self.config_manager.save()
                print_success(f"Set playlist to: {profile.playlist_name}")
            else:
                print_error("Could not find playlist")
    
    def handle_create_playlist(self):
        """Create new playlist."""
        name = self.prompt("Enter playlist name")
        
        if not name:
            return
        
        description = self.prompt("Description (optional)", default="")
        
        if playlist_ops:
            print_info("Creating playlist...")
            playlist = playlist_ops.create_playlist(name, description)
            
            if playlist:
                profile = self.config_manager.get_active_profile()
                profile.playlist_uri = playlist.get('uri', '')
                profile.playlist_name = playlist.get('name', '')
                self.config_manager.save()
                print_success(f"Created and set playlist: {profile.playlist_name}")
            else:
                print_error("Failed to create playlist")
    
    def handle_view_playlist_info(self):
        """View current playlist info."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.playlist_uri:
            print_warning("No playlist configured")
            return
        
        if playlist_ops:
            playlist_ops.display_playlist_stats(profile.playlist_uri)
            self.wait_for_key()
    
    def handle_clear_playlist(self):
        """Clear playlist setting."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.playlist_uri:
            print_warning("No playlist configured")
            return
        
        if self.confirm(f"Clear playlist setting '{profile.playlist_name}'?"):
            profile.playlist_uri = ""
            profile.playlist_name = ""
            self.config_manager.save()
            print_success("Playlist setting cleared")
    
    # ============================================
    # PLAYLIST TOOLS HANDLERS
    # ============================================
    
    def handle_sort_playlist(self):
        """Sort playlist."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.playlist_uri or not playlist_tools:
            return
        
        playlist_tools.sorter.display_sort_options()
        
        try:
            if RICH_AVAILABLE:
                choice = IntPrompt.ask("Select sort option", default=1)
            else:
                choice = int(input("Select sort option [1]: ") or "1")
            
            if choice == 0:
                return
            
            sort_config = playlist_tools.sorter.get_sort_from_choice(choice)
            if not sort_config:
                print_error("Invalid choice")
                return
            
            criteria, order = sort_config
            
            if not self.confirm(f"Sort playlist by {criteria.value}?"):
                return
            
            print_info("Sorting playlist...")
            result = playlist_tools.sort(profile.playlist_uri, criteria, order)
            
            if result.success:
                print_success(f"Sorted {result.tracks_sorted} tracks")
            else:
                print_error(f"Sort failed: {result.error_message}")
                
        except ValueError:
            print_error("Invalid input")
        
        self.wait_for_key()
    
    def handle_dedupe_playlist(self):
        """Remove duplicates."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.playlist_uri or not playlist_tools:
            return
        
        include_similar = self.confirm("Include similar tracks (same name + artist)?", default=False)
        
        if not self.confirm("Remove duplicates?"):
            return
        
        print_info("Removing duplicates...")
        result = playlist_tools.deduplicate(profile.playlist_uri, include_similar)
        playlist_tools.deduplicator.display_result(result)
        self.wait_for_key()
    
    def handle_preview_duplicates(self):
        """Preview duplicates."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.playlist_uri or not playlist_tools:
            return
        
        include_similar = self.confirm("Include similar tracks?", default=False)
        playlist_tools.deduplicator.preview_duplicates(profile.playlist_uri, include_similar)
        self.wait_for_key()
    
    def handle_analyze_playlist(self):
        """Analyze playlist."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.playlist_uri or not playlist_tools:
            return
        
        detailed = self.confirm("Include detailed analysis?", default=True)
        playlist_tools.analyzer.display_analysis(profile.playlist_uri, detailed)
        self.wait_for_key()
    
    def handle_duplicate_playlist(self):
        """Duplicate playlist."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.playlist_uri or not playlist_tools:
            return
        
        new_name = self.prompt("Name for copy", default=f"{profile.playlist_name} (Copy)")
        
        if not new_name:
            return
        
        print_info("Duplicating playlist...")
        result = playlist_tools.duplicate(profile.playlist_uri, new_name)
        playlist_tools.duplicator.display_result(result)
        self.wait_for_key()
    
    def handle_remove_artist_tracks(self):
        """Remove all tracks by an artist."""
        profile = self.config_manager.get_active_profile()
        
        if not profile.playlist_uri or not playlist_tools:
            return
        
        # Search for artist
        if artist_searcher:
            artist = artist_searcher.interactive_search()
            if not artist:
                return
            
            if not self.confirm(f"Remove all tracks by '{artist.name}' from playlist?"):
                return
            
            print_info("Removing tracks...")
            removed, name = playlist_tools.remove_artist(profile.playlist_uri, artist.uri)
            
            if removed > 0:
                print_success(f"Removed {removed} tracks by {name}")
            else:
                print_info(f"No tracks by {name} found")
            
            self.wait_for_key()
    
    # ============================================
    # SETTINGS HANDLERS
    # ============================================
    
    def handle_edit_settings(self):
        """Edit all settings."""
        self.profile_menu.run_edit_settings()
    
    def handle_toggle_setting(self, setting: str):
        """Toggle a boolean setting."""
        profile = self.config_manager.get_active_profile()
        
        current = getattr(profile, setting, False)
        new_value = not current
        setattr(profile, setting, new_value)
        self.config_manager.save()
        
        print_success(f"{setting}: {'Yes' if new_value else 'No'}")
    
    def handle_change_interval(self):
        """Change check interval."""
        profile = self.config_manager.get_active_profile()
        
        try:
            if RICH_AVAILABLE:
                value = IntPrompt.ask(f"Check interval (hours)", default=profile.check_interval)
            else:
                value = int(input(f"Check interval (hours) [{profile.check_interval}]: ") or str(profile.check_interval))
            
            if 1 <= value <= 168:
                profile.check_interval = value
                self.config_manager.save()
                print_success(f"Check interval: {value}h")
            else:
                print_error("Value must be 1-168")
        except ValueError:
            print_error("Invalid number")
    
    def handle_change_days(self):
        """Change days to check."""
        profile = self.config_manager.get_active_profile()
        
        try:
            if RICH_AVAILABLE:
                value = IntPrompt.ask(f"Days to check (0=all)", default=profile.days_to_check)
            else:
                value = int(input(f"Days to check (0=all) [{profile.days_to_check}]: ") or str(profile.days_to_check))
            
            if 0 <= value <= 365:
                profile.days_to_check = value
                self.config_manager.save()
                print_success(f"Days to check: {value if value > 0 else 'All time'}")
            else:
                print_error("Value must be 0-365")
        except ValueError:
            print_error("Invalid number")
    
    # ============================================
    # SETTINGS HANDLERS
    # ============================================
    
    def handle_edit_settings(self):
        """Edit all settings."""
        self.profile_menu.run_edit_settings()
    
    def handle_toggle_setting(self, setting: str):
        """Toggle a boolean setting."""
        profile = self.config_manager.get_active_profile()
        
        current = getattr(profile, setting, False)
        new_value = not current
        setattr(profile, setting, new_value)
        self.config_manager.save()
        
        print_success(f"{setting}: {'Yes' if new_value else 'No'}")
    
    def handle_change_interval(self):
        """Change check interval."""
        profile = self.config_manager.get_active_profile()
        
        try:
            if RICH_AVAILABLE:
                value = IntPrompt.ask(f"Check interval (hours)", default=profile.check_interval)
            else:
                value = int(input(f"Check interval (hours) [{profile.check_interval}]: ") or str(profile.check_interval))
            
            if 1 <= value <= 168:
                profile.check_interval = value
                self.config_manager.save()
                print_success(f"Check interval: {value}h")
            else:
                print_error("Value must be 1-168")
        except ValueError:
            print_error("Invalid number")
    
    def handle_change_days(self):
        """Change days to check."""
        profile = self.config_manager.get_active_profile()
        
        try:
            if RICH_AVAILABLE:
                value = IntPrompt.ask(f"Days to check (0=all)", default=profile.days_to_check)
            else:
                value = int(input(f"Days to check (0=all) [{profile.days_to_check}]: ") or str(profile.days_to_check))
            
            if 0 <= value <= 365:
                profile.days_to_check = value
                self.config_manager.save()
                print_success(f"Days to check: {value if value > 0 else 'All time'}")
            else:
                print_error("Value must be 0-365")
        except ValueError:
            print_error("Invalid number")
    
    def handle_reset_tracked(self):
        """Reset all tracked releases and tracks."""
        profile = self.config_manager.get_active_profile()
        release_count = len(profile.tracked_releases)
        track_count = len(getattr(profile, 'tracked_tracks', {}))
        
        if release_count == 0 and track_count == 0:
            print_info("Nothing to reset")
            return
        
        print_info(f"This will reset:")
        print_info(f"  - {release_count} tracked releases")
        print_info(f"  - {track_count} tracked tracks")
        print_warning("All songs will be re-scanned on next check!")
        
        if self.confirm(f"Reset all tracking for '{profile.name}'?"):
            self.profile_manager.reset_tracked_releases(profile, mode="all")
    
    def handle_reset_tracks_only(self):
        """Reset only tracked tracks (allows re-adding filtered songs)."""
        profile = self.config_manager.get_active_profile()
        track_count = len(getattr(profile, 'tracked_tracks', {}))
        
        if track_count == 0:
            print_info("No tracked tracks to reset")
            return
        
        print_info(f"This will reset {track_count} tracked tracks")
        print_info("Previously filtered/removed songs can be re-added")
        print_info("Release history will be kept (won't re-scan old albums)")
        
        if self.confirm(f"Reset tracked tracks for '{profile.name}'?"):
            self.profile_manager.reset_tracked_releases(profile, mode="tracks")


# ============================================
# MAIN APPLICATION UI
# ============================================

class ApplicationUI:
    """Main application user interface."""
    
    def __init__(
        self,
        config_manager: ConfigManager,
        profile_manager: ProfileManager
    ):
        self.config_manager = config_manager
        self.profile_manager = profile_manager
        self.profile_menu = ProfileMenu(profile_manager)
        self.app_state = AppState()
        
        self.menu_builder = MenuBuilder(config_manager, profile_manager, self.app_state)
        self.handler = MenuHandler(
            config_manager,
            profile_manager,
            self.profile_menu,
            self.app_state
        )
    
    def run(self):
        """Run the main application loop."""
        clear_screen()
        display_header()
        
        # Check for interrupted operations
        if playlist_restorer:
            playlist_restorer.check_and_offer_restore()
        
        while self.app_state.running:
            self.run_main_menu()
    
    def run_main_menu(self):
        """Run the main menu loop."""
        clear_screen()
        display_header()
        display_status_bar(self.config_manager)
        self.app_state.show_message()
        
        menu = self.menu_builder.build_main_menu()
        menu.display()
        
        choice = menu.get_choice()
        
        if choice == 'q':
            self.app_state.running = False
        elif choice == '1':
            self.handler.handle_check_releases()
        elif choice == '2':
            self.handler.handle_check_all()
        elif choice == '3':
            self.run_artists_menu()
        elif choice == '4':
            self.run_playlist_menu()
        elif choice == '5':
            self.run_playlist_tools_menu()
        elif choice == '6':
            self.run_profiles_menu()
        elif choice == '7':
            self.run_settings_menu()
        elif choice == '8':
            self.run_import_export_menu()
        elif choice == 's':
            self.handler.handle_schedule_status()
    
    def run_artists_menu(self):
        """Run the artists submenu."""
        while True:
            clear_screen()
            display_header()
            
            menu = self.menu_builder.build_artists_menu()
            menu.display()
            
            choice = menu.get_choice()
            
            if choice == '0':
                break
            elif choice == '1':
                self.handler.handle_view_artists()
            elif choice == '2':
                self.handler.handle_search_artist()
            elif choice == '3':
                self.handler.handle_add_artist_uri()
            elif choice == '4':
                self.handler.handle_import_from_playlist()
            elif choice == '5':
                self.handler.handle_remove_artist()
            elif choice == '6':
                self.handler.handle_refresh_artists()
            elif choice == '7':
                self.handler.handle_clear_artists()
    
    def run_playlist_menu(self):
        """Run the playlist submenu."""
        while True:
            clear_screen()
            display_header()
            
            menu = self.menu_builder.build_playlist_menu()
            menu.display()
            
            choice = menu.get_choice()
            
            if choice == '0':
                break
            elif choice == '1':
                self.handler.handle_select_playlist()
            elif choice == '2':
                self.handler.handle_enter_playlist_uri()
            elif choice == '3':
                self.handler.handle_create_playlist()
            elif choice == '4':
                self.handler.handle_view_playlist_info()
            elif choice == '5':
                self.handler.handle_clear_playlist()
    
    def run_playlist_tools_menu(self):
        """Run the playlist tools submenu."""
        while True:
            clear_screen()
            display_header()
            
            menu = self.menu_builder.build_playlist_tools_menu()
            menu.display()
            
            choice = menu.get_choice()
            
            if choice == '0':
                break
            elif choice == '1':
                self.handler.handle_sort_playlist()
            elif choice == '2':
                self.handler.handle_dedupe_playlist()
            elif choice == '3':
                self.handler.handle_preview_duplicates()
            elif choice == '4':
                self.handler.handle_analyze_playlist()
            elif choice == '5':
                self.handler.handle_duplicate_playlist()
            elif choice == '6':
                self.handler.handle_remove_artist_tracks()
    
    def run_profiles_menu(self):
        """Run the profiles submenu."""
        while True:
            clear_screen()
            display_header()
            
            menu = self.menu_builder.build_profiles_menu()
            menu.display()
            
            choice = menu.get_choice()
            
            if choice == '0':
                break
            elif choice == '1':
                self.profile_manager.display_profiles()
                self.handler.wait_for_key()
            elif choice == '2':
                self.profile_menu.run_switch_profile()
            elif choice == '3':
                self.profile_menu.run_create_profile()
            elif choice == '4':
                self.profile_menu.run_rename_profile()
            elif choice == '5':
                self.profile_menu.run_duplicate_profile()
            elif choice == '6':
                self.profile_menu.run_delete_profile()
    
    def run_settings_menu(self):
        """Run the settings submenu."""
        profile = self.config_manager.get_active_profile()
        
        while True:
            clear_screen()
            display_header()
            
            menu = self.menu_builder.build_settings_menu()
            menu.display()
            
            choice = menu.get_choice()
            
            if choice == '0':
                break
            elif choice == '1':
                self.handler.handle_edit_settings()
            elif choice == '2':
                self.handler.handle_change_interval()
            elif choice == '3':
                self.handler.handle_change_days()
            elif choice == '4':
                self.handler.handle_toggle_setting('sort_by_date')
            elif choice == '5':
                self.handler.handle_toggle_setting('skip_remixes')
            elif choice == '6':
                self.handler.handle_toggle_setting('skip_low_popularity')
            elif choice == '7':
                self.handler.handle_toggle_setting('skip_long_albums')
            elif choice == '8':
                self.handler.handle_toggle_setting('limit_songs_per_album')
            elif choice == 'r':
                self.handler.handle_reset_tracked()
            elif choice == 't':
                self.handler.handle_reset_tracks_only()
    
    def run_import_export_menu(self):
        """Run the import/export submenu."""
        while True:
            clear_screen()
            display_header()
            
            menu = self.menu_builder.build_import_export_menu()
            menu.display()
            
            choice = menu.get_choice()
            
            if choice == '0':
                break
            elif choice == '1':
                if import_export_menu:
                    import_export_menu.run_export_current()
                    self.handler.wait_for_key()
            elif choice == '2':
                if import_export_menu:
                    import_export_menu.run_export_all()
                    self.handler.wait_for_key()
            elif choice == '3':
                if import_export_menu:
                    import_export_menu.run_export_artists()
                    self.handler.wait_for_key()
            elif choice == '4':
                if import_export_menu:
                    import_export_menu.run_import()
                    self.handler.wait_for_key()
            elif choice == '5':
                if import_export_manager:
                    file_path = self.handler.prompt("Enter file path")
                    if file_path:
                        import_export_manager.display_preview(file_path)
                        self.handler.wait_for_key()


# End of Part 9
# ============================================
# PART 10: MAIN ENTRY POINT, CLI & SCHEDULER
# ============================================

"""
Main entry point, command-line interface, argument parsing,
daemon mode, and scheduled checking functionality.
"""

import argparse
import signal
import threading
from typing import Optional, List
import time
import sys
import os


# ============================================
# CLI ARGUMENT PARSER
# ============================================

def create_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="auto-new-releases",
        description=f"{APP_NAME} - Automatically track new releases from your favorite artists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                      Start interactive mode
  %(prog)s check                Check current profile for new releases
  %(prog)s check --all          Check all profiles
  %(prog)s check --profile "My Profile"  Check specific profile
  %(prog)s daemon               Run in background, checking on schedule
  %(prog)s artists list         List tracked artists
  %(prog)s artists add "Artist Name"    Search and add artist
  %(prog)s export               Export current profile
  %(prog)s import backup.json   Import profiles from file
        """
    )
    
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"%(prog)s {APP_VERSION}"
    )
    
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress non-essential output"
    )
    
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output"
    )
    
    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Check command
    check_parser = subparsers.add_parser("check", help="Check for new releases")
    check_parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Check all profiles"
    )
    check_parser.add_argument(
        "--profile", "-p",
        type=str,
        help="Profile name to check"
    )
    check_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be added without adding"
    )
    
    # Daemon command
    daemon_parser = subparsers.add_parser("daemon", help="Run in daemon mode")
    daemon_parser.add_argument(
        "--interval", "-i",
        type=int,
        default=0,
        help="Override check interval (minutes, 0 = use profile settings)"
    )
    daemon_parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit (for cron jobs)"
    )
    
    # Artists command
    artists_parser = subparsers.add_parser("artists", help="Manage artists")
    artists_sub = artists_parser.add_subparsers(dest="artists_command")
    
    artists_sub.add_parser("list", help="List tracked artists")
    
    artists_add = artists_sub.add_parser("add", help="Add an artist")
    artists_add.add_argument("query", type=str, help="Artist name or Spotify URI")
    
    artists_remove = artists_sub.add_parser("remove", help="Remove an artist")
    artists_remove.add_argument("query", type=str, help="Artist name or number")
    
    artists_sub.add_parser("refresh", help="Refresh artist data")
    
    # Playlist command
    playlist_parser = subparsers.add_parser("playlist", help="Manage playlist")
    playlist_sub = playlist_parser.add_subparsers(dest="playlist_command")
    
    playlist_sub.add_parser("info", help="Show playlist info")
    
    playlist_set = playlist_sub.add_parser("set", help="Set target playlist")
    playlist_set.add_argument("uri", type=str, help="Playlist URI or URL")
    
    playlist_sub.add_parser("sort", help="Sort playlist by release date")
    playlist_sub.add_parser("dedupe", help="Remove duplicate tracks")
    playlist_sub.add_parser("analyze", help="Analyze playlist")
    
    # Profile command
    profile_parser = subparsers.add_parser("profile", help="Manage profiles")
    profile_sub = profile_parser.add_subparsers(dest="profile_command")
    
    profile_sub.add_parser("list", help="List all profiles")
    profile_sub.add_parser("show", help="Show current profile details")
    
    profile_switch = profile_sub.add_parser("switch", help="Switch to a profile")
    profile_switch.add_argument("name", type=str, help="Profile name")
    
    profile_create = profile_sub.add_parser("create", help="Create new profile")
    profile_create.add_argument("name", type=str, help="Profile name")
    
    profile_delete = profile_sub.add_parser("delete", help="Delete a profile")
    profile_delete.add_argument("name", type=str, help="Profile name")
    
    # Export command
    export_parser = subparsers.add_parser("export", help="Export profiles")
    export_parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Export all profiles"
    )
    export_parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output file path"
    )
    export_parser.add_argument(
        "--no-history",
        action="store_true",
        help="Exclude tracked releases history"
    )
    
    # Import command
    import_parser = subparsers.add_parser("import", help="Import profiles")
    import_parser.add_argument("file", type=str, help="File to import")
    import_parser.add_argument(
        "--mode", "-m",
        choices=["skip", "replace", "rename"],
        default="skip",
        help="How to handle duplicates"
    )
    
    # Status command
    subparsers.add_parser("status", help="Show status and schedule")
    
    # Config command
    config_parser = subparsers.add_parser("config", help="Show/edit configuration")
    config_parser.add_argument(
        "--show",
        action="store_true",
        help="Show current configuration"
    )
    config_parser.add_argument(
        "--reset-credentials",
        action="store_true",
        help="Reset Spotify credentials"
    )
    
    return parser


# ============================================
# CLI COMMAND HANDLERS
# ============================================

class CLIHandler:
    """Handles CLI commands."""
    
    def __init__(self, quiet: bool = False):
        self.quiet = quiet
    
    def log(self, message: str, level: str = "info"):
        """Log message unless quiet mode."""
        if self.quiet and level == "info":
            return
        
        if level == "success":
            print_success(message)
        elif level == "error":
            print_error(message)
        elif level == "warning":
            print_warning(message)
        else:
            print_info(message)
    
    # ============================================
    # CHECK COMMANDS
    # ============================================
    
    def cmd_check(self, args) -> int:
        """Handle check command."""
        if args.all:
            return self.cmd_check_all(args)
        elif args.profile:
            return self.cmd_check_profile(args.profile, args.dry_run)
        else:
            return self.cmd_check_current(args.dry_run)
    
    def cmd_check_current(self, dry_run: bool = False) -> int:
        """Check current profile."""
        profile = config_manager.get_active_profile()
        
        self.log(f"Checking profile: {profile.name}")
        self.log(f"Artists: {len(profile.artists)}")
        self.log(f"Playlist: {profile.playlist_name or 'Not set'}")
        
        if not profile.playlist_uri:
            self.log("No playlist configured!", "error")
            return 1
        
        if not profile.artists:
            self.log("No artists tracked!", "error")
            return 1
        
        if dry_run:
            self.log("Dry run - would check for new releases", "info")
            return 0
        
        result = check_current_profile(silent=self.quiet)
        
        if result.status == CheckStatus.SUCCESS:
            self.log(result.summary(), "success")
            return 0
        elif result.status == CheckStatus.NO_NEW:
            self.log("No new releases found", "info")
            return 0
        else:
            self.log(f"Check failed: {result.error_message}", "error")
            return 1
    
    def cmd_check_profile(self, profile_name: str, dry_run: bool = False) -> int:
        """Check specific profile by name."""
        # Find profile
        profile = None
        for p in config_manager.config.profiles:
            if p.name.lower() == profile_name.lower():
                profile = p
                break
        
        if not profile:
            self.log(f"Profile not found: {profile_name}", "error")
            return 1
        
        # Temporarily switch to this profile
        original_id = config_manager.config.active_profile_id
        config_manager.config.active_profile_id = profile.id
        
        try:
            result = self.cmd_check_current(dry_run)
        finally:
            config_manager.config.active_profile_id = original_id
        
        return result
    
    def cmd_check_all(self, args) -> int:
        """Check all profiles."""
        profiles = config_manager.config.profiles
        
        self.log(f"Checking {len(profiles)} profiles...")
        
        total_added = 0
        failed = 0
        
        for profile in profiles:
            self.log(f"\nProfile: {profile.name}")
            
            if not profile.playlist_uri or not profile.artists:
                self.log("  Skipping - not configured", "warning")
                continue
            
            if release_checker:
                result = release_checker.check_profile(profile, silent=True)
                
                if result.status in [CheckStatus.SUCCESS, CheckStatus.NO_NEW]:
                    if result.total_tracks_added > 0:
                        self.log(f"  Added {result.total_tracks_added} tracks", "success")
                        total_added += result.total_tracks_added
                    else:
                        self.log("  No new releases", "info")
                else:
                    self.log(f"  Failed: {result.error_message}", "error")
                    failed += 1
        
        print()
        self.log(f"Total: {total_added} tracks added", "success")
        
        if failed > 0:
            self.log(f"{failed} profiles failed", "warning")
            return 1
        
        return 0
    
    # ============================================
    # ARTISTS COMMANDS
    # ============================================
    
    def cmd_artists(self, args) -> int:
        """Handle artists commands."""
        if args.artists_command == "list":
            return self.cmd_artists_list()
        elif args.artists_command == "add":
            return self.cmd_artists_add(args.query)
        elif args.artists_command == "remove":
            return self.cmd_artists_remove(args.query)
        elif args.artists_command == "refresh":
            return self.cmd_artists_refresh()
        else:
            self.log("Use: artists list|add|remove|refresh", "info")
            return 0
    
    def cmd_artists_list(self) -> int:
        """List artists."""
        profile = config_manager.get_active_profile()
        
        if not profile.artists:
            self.log("No artists tracked", "info")
            return 0
        
        profile_manager.display_artists()
        return 0
    
    def cmd_artists_add(self, query: str) -> int:
        """Add an artist."""
        profile = config_manager.get_active_profile()
        
        # Check if it's a URI/URL
        artist_id = parse_spotify_uri(query, "artist")
        
        if artist_id:
            # Direct URI/URL
            artist_data = spotify_api.get_artist(f"spotify:artist:{artist_id}")
            if artist_data:
                artist = spotify_api.artist_to_model(artist_data)
                if profile_manager.add_artist_to_profile(profile, artist):
                    return 0
                return 1
            else:
                self.log("Artist not found", "error")
                return 1
        else:
            # Search by name
            results = spotify_api.search_artists(query)
            
            if not results:
                self.log("No artists found", "error")
                return 1
            
            # In non-interactive mode, add the first result
            artist_data = results[0]
            artist = spotify_api.artist_to_model(artist_data)
            
            self.log(f"Found: {artist.name} ({format_number(artist.followers or 0)} followers)")
            
            if profile_manager.add_artist_to_profile(profile, artist):
                return 0
            return 1
    
    def cmd_artists_remove(self, query: str) -> int:
        """Remove an artist."""
        profile = config_manager.get_active_profile()
        
        if not profile.artists:
            self.log("No artists to remove", "warning")
            return 1
        
        # Try as number first
        try:
            idx = int(query)
            artist = profile_manager.find_artist_by_index(profile, idx)
        except ValueError:
            # Try as name
            artist = profile_manager.find_artist_by_name(profile, query)
        
        if artist:
            if profile_manager.remove_artist_from_profile(profile, artist.uri):
                return 0
            return 1
        else:
            self.log("Artist not found", "error")
            return 1
    
    def cmd_artists_refresh(self) -> int:
        """Refresh artist data."""
        profile = config_manager.get_active_profile()
        
        if not profile.artists:
            self.log("No artists to refresh", "warning")
            return 0
        
        self.log(f"Refreshing {len(profile.artists)} artists...")
        
        updated = 0
        for i, artist in enumerate(profile.artists):
            if not self.quiet:
                print(f"  [{i+1}/{len(profile.artists)}] {artist.name}")
            
            data = spotify_api.get_artist(artist.uri)
            if data:
                profile.artists[i] = spotify_api.artist_to_model(data)
                updated += 1
        
        config_manager.save()
        self.log(f"Updated {updated} artists", "success")
        return 0
    
    # ============================================
    # PLAYLIST COMMANDS
    # ============================================
    
    def cmd_playlist(self, args) -> int:
        """Handle playlist commands."""
        if args.playlist_command == "info":
            return self.cmd_playlist_info()
        elif args.playlist_command == "set":
            return self.cmd_playlist_set(args.uri)
        elif args.playlist_command == "sort":
            return self.cmd_playlist_sort()
        elif args.playlist_command == "dedupe":
            return self.cmd_playlist_dedupe()
        elif args.playlist_command == "analyze":
            return self.cmd_playlist_analyze()
        else:
            self.log("Use: playlist info|set|sort|dedupe|analyze", "info")
            return 0
    
    def cmd_playlist_info(self) -> int:
        """Show playlist info."""
        profile = config_manager.get_active_profile()
        
        if not profile.playlist_uri:
            self.log("No playlist configured", "warning")
            return 1
        
        if playlist_ops:
            playlist_ops.display_playlist_stats(profile.playlist_uri)
        return 0
    
    def cmd_playlist_set(self, uri: str) -> int:
        """Set target playlist."""
        profile = config_manager.get_active_profile()
        
        playlist_id = parse_spotify_uri(uri, "playlist")
        if not playlist_id:
            self.log("Invalid playlist URI/URL", "error")
            return 1
        
        if playlist_ops:
            playlist = playlist_ops.get_playlist_details(f"spotify:playlist:{playlist_id}")
            if playlist:
                profile.playlist_uri = playlist.get('uri', '')
                profile.playlist_name = playlist.get('name', '')
                config_manager.save()
                self.log(f"Set playlist to: {profile.playlist_name}", "success")
                return 0
            else:
                self.log("Playlist not found", "error")
                return 1
        return 1
    
    def cmd_playlist_sort(self) -> int:
        """Sort playlist."""
        profile = config_manager.get_active_profile()
        
        if not profile.playlist_uri:
            self.log("No playlist configured", "error")
            return 1
        
        self.log("Sorting playlist by release date...")
        
        if playlist_tools:
            result = playlist_tools.sorter.sort_by_release_date(profile.playlist_uri)
            if result.success:
                self.log(f"Sorted {result.tracks_sorted} tracks", "success")
                return 0
            else:
                self.log(f"Sort failed: {result.error_message}", "error")
                return 1
        return 1
    
    def cmd_playlist_dedupe(self) -> int:
        """Remove duplicates."""
        profile = config_manager.get_active_profile()
        
        if not profile.playlist_uri:
            self.log("No playlist configured", "error")
            return 1
        
        self.log("Removing duplicates...")
        
        if playlist_tools:
            result = playlist_tools.deduplicate(profile.playlist_uri)
            if result.success:
                if result.duplicates_removed > 0:
                    self.log(f"Removed {result.duplicates_removed} duplicates", "success")
                else:
                    self.log("No duplicates found", "info")
                return 0
            else:
                self.log(f"Dedupe failed: {result.error_message}", "error")
                return 1
        return 1
    
    def cmd_playlist_analyze(self) -> int:
        """Analyze playlist."""
        profile = config_manager.get_active_profile()
        
        if not profile.playlist_uri:
            self.log("No playlist configured", "error")
            return 1
        
        if playlist_tools:
            playlist_tools.analyzer.display_analysis(profile.playlist_uri, detailed=True)
        return 0
    
    # ============================================
    # PROFILE COMMANDS
    # ============================================
    
    def cmd_profile(self, args) -> int:
        """Handle profile commands."""
        if args.profile_command == "list":
            return self.cmd_profile_list()
        elif args.profile_command == "show":
            return self.cmd_profile_show()
        elif args.profile_command == "switch":
            return self.cmd_profile_switch(args.name)
        elif args.profile_command == "create":
            return self.cmd_profile_create(args.name)
        elif args.profile_command == "delete":
            return self.cmd_profile_delete(args.name)
        else:
            self.log("Use: profile list|show|switch|create|delete", "info")
            return 0
    
    def cmd_profile_list(self) -> int:
        """List profiles."""
        profile_manager.display_profiles()
        return 0
    
    def cmd_profile_show(self) -> int:
        """Show current profile."""
        profile_manager.display_profile_details()
        return 0
    
    def cmd_profile_switch(self, name: str) -> int:
        """Switch to profile."""
        for p in config_manager.config.profiles:
            if p.name.lower() == name.lower():
                if profile_manager.switch_profile(p.id):
                    return 0
                return 1
        
        self.log(f"Profile not found: {name}", "error")
        return 1
    
    def cmd_profile_create(self, name: str) -> int:
        """Create profile."""
        profile_manager.create_profile(name)
        return 0
    
    def cmd_profile_delete(self, name: str) -> int:
        """Delete profile."""
        for p in config_manager.config.profiles:
            if p.name.lower() == name.lower():
                if profile_manager.delete_profile(p.id):
                    return 0
                return 1
        
        self.log(f"Profile not found: {name}", "error")
        return 1
    
    # ============================================
    # IMPORT/EXPORT COMMANDS
    # ============================================
    
    def cmd_export(self, args) -> int:
        """Handle export command."""
        include_tracked = not args.no_history
        
        if args.all:
            result = import_export_manager.export_all(args.output, include_tracked)
        else:
            result = import_export_manager.export_current_profile(args.output, include_tracked)
        
        if result.success:
            self.log(f"Exported to: {result.file_path}", "success")
            self.log(f"  Profiles: {result.profiles_exported}")
            self.log(f"  Artists: {result.artists_exported}")
            return 0
        else:
            self.log(f"Export failed: {result.error_message}", "error")
            return 1
    
    def cmd_import(self, args) -> int:
        """Handle import command."""
        mode_map = {
            "skip": ImportMode.SKIP,
            "replace": ImportMode.REPLACE,
            "rename": ImportMode.RENAME,
        }
        mode = mode_map.get(args.mode, ImportMode.SKIP)
        
        result = import_export_manager.import_file(args.file, mode)
        
        if result.success:
            self.log("Import complete!", "success")
            if result.profiles_imported:
                self.log(f"  Imported: {result.profiles_imported}")
            if result.profiles_replaced:
                self.log(f"  Replaced: {result.profiles_replaced}")
            if result.profiles_skipped:
                self.log(f"  Skipped: {result.profiles_skipped}")
            return 0
        else:
            self.log(f"Import failed: {result.error_message}", "error")
            return 1
    
    # ============================================
    # OTHER COMMANDS
    # ============================================
    
    def cmd_status(self) -> int:
        """Show status."""
        if scheduled_checker:
            scheduled_checker.display_schedule()
        return 0
    
    def cmd_config(self, args) -> int:
        """Handle config command."""
        if args.reset_credentials:
            config_manager.config.spotify_client_id = ""
            config_manager.config.spotify_client_secret = ""
            config_manager.save()
            
            # Remove token cache
            if TOKEN_CACHE.exists():
                TOKEN_CACHE.unlink()
            
            self.log("Credentials reset. Re-run to set new credentials.", "success")
            return 0
        
        # Show config
        profile = config_manager.get_active_profile()
        
        print(f"\nConfiguration Directory: {CONFIG_DIR}")
        print(f"Config File: {CONFIG_FILE}")
        print(f"Profiles: {len(config_manager.config.profiles)}")
        print(f"Active Profile: {profile.name}")
        print(f"Credentials Set: {'Yes' if config_manager.config.spotify_client_id else 'No'}")
        print()
        
        return 0


# ============================================
# DAEMON / SCHEDULER
# ============================================

class Daemon:
    """Background daemon for scheduled checking."""
    
    def __init__(self, interval_override: int = 0):
        self.interval_override = interval_override  # Minutes, 0 = use profile settings
        self.running = False
        self._stop_event = threading.Event()
    
    def start(self):
        """Start the daemon."""
        self.running = True
        self._stop_event.clear()
        
        print_info(f"{APP_NAME} daemon started")
        print_info("Press Ctrl+C to stop")
        print()
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        
        self._run_loop()
    
    def stop(self):
        """Stop the daemon."""
        self.running = False
        self._stop_event.set()
        print_info("Daemon stopping...")
    
    def _handle_signal(self, signum, frame):
        """Handle termination signals."""
        self.stop()
    
    def _run_loop(self):
        """Main daemon loop."""
        while self.running:
            # Check which profiles are due
            if scheduled_checker:
                due_profiles = scheduled_checker.get_profiles_due()
                
                if due_profiles:
                    print_info(f"Checking {len(due_profiles)} due profiles...")
                    results = scheduled_checker.check_due_profiles(silent=False)
                    
                    for result in results:
                        if result.total_tracks_added > 0:
                            print_success(f"[{result.profile_name}] Added {result.total_tracks_added} tracks")
            
            # Calculate sleep time
            if self.interval_override > 0:
                sleep_seconds = self.interval_override * 60
            else:
                # Sleep for 5 minutes, then check again
                sleep_seconds = 300
            
            # Wait with ability to interrupt
            self._stop_event.wait(sleep_seconds)
    
    def run_once(self):
        """Run a single check and exit (for cron)."""
        print_info(f"{APP_NAME} - One-time check")
        
        if scheduled_checker:
            due_profiles = scheduled_checker.get_profiles_due()
            
            if not due_profiles:
                print_info("No profiles due for checking")
                return 0
            
            print_info(f"Checking {len(due_profiles)} due profiles...")
            results = scheduled_checker.check_due_profiles(silent=False)
            
            total_added = sum(r.total_tracks_added for r in results)
            
            if total_added > 0:
                print_success(f"Total: {total_added} tracks added")
            else:
                print_info("No new releases found")
            
            return 0
        
        return 1


# ============================================
# INITIALIZATION
# ============================================

def initialize_application() -> bool:
    """Initialize all application components."""
    global config_manager, spotify_auth, spotify_api
    global profile_manager, artist_searcher, release_fetcher
    global playlist_ops, playlist_selector, playlist_restorer, playlist_tools
    global release_checker, interactive_checker, scheduled_checker
    global import_export_manager, import_export_menu
    
    try:
        # Initialize configuration
        config_manager = ConfigManager()
        
        # Initialize Spotify authentication
        spotify_auth = SpotifyAuth(config_manager)
        if not spotify_auth.authenticate():
            print_error("Failed to authenticate with Spotify")
            return False
        
        # Initialize API
        spotify_api = SpotifyAPI(spotify_auth)
        
        # Initialize profile management
        profile_manager = ProfileManager(config_manager)
        
        # Initialize API helpers
        artist_searcher = ArtistSearcher(spotify_api)
        release_fetcher = ReleaseFetcher(spotify_api)
        
        # Initialize playlist operations
        playlist_ops = PlaylistOperations(spotify_api)
        playlist_selector = PlaylistSelector(spotify_api, playlist_ops)
        playlist_restorer = PlaylistRestorer(playlist_ops)
        playlist_tools = PlaylistTools(spotify_api, playlist_ops)
        
        # Initialize checkers
        release_checker = ReleaseChecker(spotify_api, playlist_ops, config_manager)
        interactive_checker = InteractiveChecker(spotify_api, playlist_ops, config_manager)
        scheduled_checker = ScheduledChecker(spotify_api, playlist_ops, config_manager)
        
        # Initialize import/export
        import_export_manager = ImportExportManager(config_manager)
        import_export_menu = ImportExportMenu(import_export_manager)
        
        return True
        
    except Exception as e:
        print_error(f"Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================
# MAIN ENTRY POINT
# ============================================

def main() -> int:
    """Main entry point."""
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # Handle no-color flag
    global RICH_AVAILABLE
    if args.no_color:
        RICH_AVAILABLE = False
    
    # Initialize application
    if not initialize_application():
        return 1
    
    # Create CLI handler
    cli = CLIHandler(quiet=args.quiet)
    
    # Route to appropriate command
    try:
        if args.command is None:
            # Interactive mode
            app = ApplicationUI(config_manager, profile_manager)
            app.run()
            return 0
        
        elif args.command == "check":
            return cli.cmd_check(args)
        
        elif args.command == "daemon":
            daemon = Daemon(args.interval)
            if args.once:
                return daemon.run_once()
            else:
                daemon.start()
                return 0
        
        elif args.command == "artists":
            return cli.cmd_artists(args)
        
        elif args.command == "playlist":
            return cli.cmd_playlist(args)
        
        elif args.command == "profile":
            return cli.cmd_profile(args)
        
        elif args.command == "export":
            return cli.cmd_export(args)
        
        elif args.command == "import":
            return cli.cmd_import(args)
        
        elif args.command == "status":
            return cli.cmd_status()
        
        elif args.command == "config":
            return cli.cmd_config(args)
        
        else:
            parser.print_help()
            return 0
            
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 130
    except Exception as e:
        print_error(f"Error: {e}")
        if not args.quiet:
            import traceback
            traceback.print_exc()
        return 1


# ============================================
# SCRIPT ENTRY POINT
# ============================================

if __name__ == "__main__":
    sys.exit(main())


# ============================================
# END OF APPLICATION
# ============================================

"""
Installation & Usage:

1. Install dependencies:
   pip install spotipy rich

2. Set up Spotify API credentials:
   - Go to https://developer.spotify.com/dashboard
   - Create a new application
   - Add http://127.0.0.1:8888/callback as Redirect URI
   - Copy Client ID and Client Secret

3. Run the application:
   python auto_new_releases.py

4. On first run:
   - Enter your Spotify credentials when prompted
   - Authorize the application in your browser
   - Set up a profile with artists and playlist

5. Commands:
   python auto_new_releases.py              # Interactive mode
   python auto_new_releases.py check        # Check current profile
   python auto_new_releases.py check --all  # Check all profiles
   python auto_new_releases.py daemon       # Run in background
   python auto_new_releases.py --help       # Show all commands

6. For cron jobs:
   */30 * * * * /path/to/python /path/to/auto_new_releases.py daemon --once

Enjoy tracking your favorite artists' new releases!
"""