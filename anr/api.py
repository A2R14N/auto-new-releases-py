"""
Spotify API wrapper: SpotifyAPIError, SpotifyAPI,
ArtistSearcher, ReleaseFetcher, ProgressDisplay.
"""

import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable

import spotipy
from spotipy.exceptions import SpotifyException

from .constants import (
    API_LIMITS, RICH_AVAILABLE, console,
    print_info, print_error, print_warning,
    parse_spotify_uri, parse_release_date, format_number,
)
from .auth import SpotifyAuthManager
from .models import Artist


class SpotifyAPIError(Exception):
    """Custom exception for Spotify API errors."""
    pass


class SpotifyAPI:
    """
    Wrapper around Spotipy client with helper methods,
    error handling, caching, and rate limiting.
    """

    def __init__(self, spotify_auth: SpotifyAuthManager):
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
                del self._cache[key]
                del self._cache_ttl[key]
        return None

    def _set_cached(self, key: str, value: Any):
        """Set value in cache with timestamp."""
        self._cache[key] = value
        self._cache_ttl[key] = time.time()

        # Limit cache size
        if len(self._cache) > 500:
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
                retry_after = int(e.headers.get('Retry-After', 5))
                raise SpotifyAPIError(f"{context}: Rate limited. Try again in {retry_after} seconds.")
            else:
                raise SpotifyAPIError(f"{context}: Spotify error {e.http_status} - {e.msg}")
        else:
            raise SpotifyAPIError(f"{context}: {str(e)}")

    # USER OPERATIONS
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

    # ARTIST OPERATIONS
    def search_artists(self, query: str, limit: int = 10) -> List[Dict]:
        """Search for artists by name."""
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
        """Get artist details by URI/ID."""
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
        """Get multiple artists in batch."""
        if not artist_ids:
            return []

        results = []
        for i in range(0, len(artist_ids), 50):
            batch = artist_ids[i:i+50]
            try:
                self._rate_limit()
                response = self.client.artists(batch)
                results.extend(response.get('artists', []))
            except Exception as e:
                self._handle_api_error(e, "Get multiple artists")

        return [a for a in results if a]

    def get_artist_albums(
        self,
        artist_uri: str,
        include_groups: str = "album,single",
        limit: int = 50
    ) -> List[Dict]:
        """Get all albums/singles from an artist (paginated)."""
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

                if response.get('next'):
                    offset += len(items)
                else:
                    break

            return all_albums

        except Exception as e:
            self._handle_api_error(e, f"Get artist albums for {artist_id}")
            return []

    def get_artist_top_tracks(self, artist_uri: str, country: str = "US") -> List[Dict]:
        """Get artist's top tracks."""
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
        """Convert Spotify artist dict to Artist model."""
        return Artist(
            uri=artist_data.get('uri', ''),
            name=artist_data.get('name', 'Unknown'),
            image=artist_data.get('images', [{}])[0].get('url') if artist_data.get('images') else None,
            followers=artist_data.get('followers', {}).get('total'),
            last_follower_update=time.time()
        )

    # ALBUM OPERATIONS
    def get_album(self, album_uri: str) -> Optional[Dict]:
        """Get album details by URI/ID."""
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
        """Get all tracks from an album (paginated)."""
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

    # TRACK OPERATIONS
    def get_track(self, track_uri: str) -> Optional[Dict]:
        """Get track details by URI/ID."""
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
        """Get multiple tracks in batch."""
        if not track_uris:
            return []

        track_ids = [parse_spotify_uri(uri, "track") for uri in track_uris]
        track_ids = [tid for tid in track_ids if tid]

        if not track_ids:
            return []

        results = []
        for i in range(0, len(track_ids), API_LIMITS["TRACKS_PER_REQUEST"]):
            batch = track_ids[i:i + API_LIMITS["TRACKS_PER_REQUEST"]]
            try:
                self._rate_limit()
                response = self.client.tracks(batch)
                results.extend(response.get('tracks', []))
            except Exception as e:
                self._handle_api_error(e, "Get multiple tracks")

        return [t for t in results if t]

    def get_tracks_audio_features(self, track_uris: List[str]) -> List[Dict]:
        """Get audio features for multiple tracks."""
        if not track_uris:
            return []

        track_ids = [parse_spotify_uri(uri, "track") for uri in track_uris]
        track_ids = [tid for tid in track_ids if tid]

        if not track_ids:
            return []

        results = []
        for i in range(0, len(track_ids), 100):
            batch = track_ids[i:i+100]
            try:
                self._rate_limit()
                response = self.client.audio_features(batch)
                results.extend(response)
            except Exception as e:
                self._handle_api_error(e, "Get audio features")

        return [f for f in results if f]

    # PLAYLIST OPERATIONS (Basic)
    def remove_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """Remove tracks from a playlist."""
        if not track_uris:
            return True

        tracks = []
        for uri in track_uris:
            if isinstance(uri, dict):
                uri_str = uri.get('uri', '')
            elif isinstance(uri, str):
                uri_str = uri
            else:
                continue

            if uri_str:
                tracks.append({"uri": uri_str})

        if not tracks:
            return True

        try:
            self._rate_limit()
            self.client.playlist_remove_all_occurrences_of_items(
                playlist_id,
                [t["uri"] for t in tracks]
            )
            return True
        except Exception as e:
            self._handle_api_error(e, f"Remove tracks from playlist {playlist_id}")
            return False

    def get_user_playlists(self, limit: int = 50) -> List[Dict]:
        """Get current user's playlists (paginated)."""
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

                own_playlists = [p for p in items if p.get('owner', {}).get('id') == user_id]
                all_playlists.extend(own_playlists)

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
        """Get playlist details."""
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
        """Create a new playlist."""
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

# ARTIST SEARCH & SELECTION HELPERS
class ArtistSearcher:
    """Helper class for searching and selecting artists."""

    def __init__(self, spotify_api: SpotifyAPI):
        self.api = spotify_api

    def search_and_display(self, query: str) -> List[Dict]:
        """Search for artists and display results."""
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
            from rich.table import Table
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
        """Let user select an artist from search results."""
        if not artists:
            return None

        try:
            if RICH_AVAILABLE:
                from rich.prompt import IntPrompt
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
        """Run interactive artist search loop."""
        while True:
            if RICH_AVAILABLE:
                from rich.prompt import Prompt, Confirm
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

                if RICH_AVAILABLE:
                    from rich.prompt import Confirm
                    if not Confirm.ask("Search again?", default=True):
                        return None
                else:
                    if input("Search again? [Y/n]: ").lower() == 'n':
                        return None


# RELEASE FETCHER
class ReleaseFetcher:
    """Fetches and processes new releases from artists."""

    def __init__(self, spotify_api: SpotifyAPI):
        self.api = spotify_api

    def get_artist_releases(
        self,
        artist_uri: str,
        include_groups: str = "album,single"
    ) -> List[Dict]:
        """Get all releases from an artist."""
        return self.api.get_artist_albums(artist_uri, include_groups=include_groups)

    def get_release_details(self, release_uri: str) -> Optional[Dict]:
        """Get detailed info about a release including tracks."""
        return self.api.get_album(release_uri)

    def filter_releases_by_date(
        self,
        releases: List[Dict],
        days_back: int = 30
    ) -> List[Dict]:
        """Filter releases to only those within date range."""
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
        """Get new releases for an artist that haven't been tracked yet."""
        tracked = tracked_releases or {}

        all_releases = self.get_artist_releases(artist.uri)
        recent_releases = self.filter_releases_by_date(all_releases, days_back)
        new_releases = [r for r in recent_releases if r.get('uri') not in tracked]

        return new_releases

    def display_releases(self, releases: List[Dict], title: str = "Releases"):
        """Display releases in a formatted table."""
        if not releases:
            print_info("No releases to display")
            return

        if RICH_AVAILABLE:
            from rich.table import Table
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

# PROGRESS DISPLAY HELPERS
class ProgressDisplay:
    """Helper for displaying progress during long operations."""

    def __init__(self):
        self._progress = None
        self._task_id = None

    def start(self, description: str, total: int = 100):
        """Start a progress display."""
        if RICH_AVAILABLE:
            from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
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
    """Context manager for progress display."""
    class ProgressContext:
        def __init__(self):
            self.display = ProgressDisplay()

        def __enter__(self):
            self.display.start(description, total)
            return self.display

        def __exit__(self, *args):
            self.display.stop()

    return ProgressContext()
