"""
Playlist operations: PlaylistTrack, PlaylistOperations,
PlaylistSelector, PlaylistBackup, PlaylistRestorer.
"""

import json
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Set, Tuple, Callable

import spotipy

from .constants import (
    CONFIG_DIR, API_LIMITS,
    RICH_AVAILABLE, console,
    print_success, print_error, print_warning, print_info,
    parse_spotify_uri, format_duration, ensure_config_dir,
)
from .api import SpotifyAPI, create_progress_context

# Import BridgeAPI lazily to avoid circular imports at module-load time
def _is_bridge(api) -> bool:
    """Return True if *api* is a BridgeAPI instance."""
    try:
        from .bridge_api import BridgeAPI
        return isinstance(api, BridgeAPI)
    except ImportError:
        return False


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

    def get_playlist_details(self, playlist_uri: str, skip_cache: bool = False) -> Optional[Dict]:
        """Get playlist metadata."""
        return self.api.get_playlist(playlist_uri, skip_cache=skip_cache)

    def get_playlist_tracks(
        self,
        playlist_uri: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> List[PlaylistTrack]:
        """Get all tracks from a playlist."""
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id:
            return []

        # ---- Bridge path: use the bridge's paginated fetch -----------------
        if _is_bridge(self.api):
            try:
                raw_items = self.api._call(
                    "get_playlist_tracks", {"playlist_id": playlist_id}
                ) or []
                tracks = []
                for item in raw_items:
                    track = PlaylistTrack.from_playlist_item(item)
                    if track:
                        tracks.append(track)
                if progress_callback:
                    progress_callback(len(tracks), len(tracks))
                return tracks
            except Exception as e:
                self.api._handle_api_error(e, f"Get playlist tracks for {playlist_id}")
                return []

        # ---- Standard spotipy path -----------------------------------------
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
        """Get set of all track URIs in a playlist."""
        tracks = self.get_playlist_tracks(playlist_uri)
        return {track.uri for track in tracks}

    def add_tracks(
        self,
        playlist_uri: str,
        track_uris: List[str],
        position: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[int, int]:
        """Add tracks to a playlist in batches."""
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id or not track_uris:
            return (0, 0)

        added = 0
        failed = 0
        batch_size = API_LIMITS["PLAYLIST_BATCH_SIZE"]
        total = len(track_uris)

        # ---- Bridge path ---------------------------------------------------
        if _is_bridge(self.api):
            try:
                self.api.add_tracks_to_playlist(playlist_id, track_uris)
                added = total
            except Exception:
                failed = total
            if progress_callback:
                progress_callback(total, total)
            self.api.clear_cache(f"playlist:{playlist_id}")
            return (added, failed)

        # ---- Standard spotipy path -----------------------------------------
        for i in range(0, total, batch_size):
            batch = track_uris[i:i + batch_size]

            try:
                self.api._rate_limit()

                if position is not None:
                    self.client.playlist_add_items(playlist_id, batch, position=position + i)
                else:
                    self.client.playlist_add_items(playlist_id, batch)

                added += len(batch)

            except Exception as e:
                print_error(f"Failed to add batch: {e}")
                failed += len(batch)

            if progress_callback:
                progress_callback(added + failed, total)

        self.api.clear_cache(f"playlist:{playlist_id}")
        return (added, failed)

    def add_tracks_at_top(
        self,
        playlist_uri: str,
        track_uris: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[int, int]:
        """Add tracks to the top of a playlist."""
        return self.add_tracks(playlist_uri, track_uris, position=0, progress_callback=progress_callback)

    def remove_tracks(
        self,
        playlist_uri: str,
        track_uris: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[int, int]:
        """Remove tracks from a playlist."""
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id:
            return 0, len(track_uris)

        clean_uris = []
        for uri in track_uris:
            if isinstance(uri, dict):
                clean_uri = uri.get('uri', '')
            elif isinstance(uri, str):
                clean_uri = uri
            else:
                continue

            if clean_uri and clean_uri.startswith('spotify:track:'):
                clean_uris.append(clean_uri)

        if not clean_uris:
            return 0, len(track_uris)

        removed = 0
        failed = 0
        total = len(clean_uris)
        batch_size = 100

        for i in range(0, total, batch_size):
            batch = clean_uris[i:i + batch_size]

            if progress_callback:
                progress_callback(i, total)

            try:
                success = self.api.remove_playlist_tracks(playlist_id, batch)
                if success:
                    removed += len(batch)
                else:
                    failed += len(batch)
            except Exception as e:
                print_error(f"Failed to remove batch: {e}")
                failed += len(batch)

        if progress_callback:
            progress_callback(total, total)

        return removed, failed

    def remove_tracks_by_artist(
        self,
        playlist_uri: str,
        artist_uri: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> int:
        """Remove all tracks by a specific artist from playlist."""
        artist_id = parse_spotify_uri(artist_uri, "artist")
        if not artist_id:
            return 0

        if progress_callback:
            progress_callback("Scanning playlist...", 0, 0)

        tracks = self.get_playlist_tracks(playlist_uri)

        tracks_to_remove = []
        for track in tracks:
            track_data = self.api.get_track(track.uri)
            if track_data:
                track_artist_uris = [a.get('uri', '') for a in track_data.get('artists', [])]
                if f"spotify:artist:{artist_id}" in track_artist_uris:
                    tracks_to_remove.append(track.uri)

        if not tracks_to_remove:
            return 0

        if progress_callback:
            progress_callback(f"Removing {len(tracks_to_remove)} tracks...", 0, len(tracks_to_remove))

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
        """Remove all tracks from a playlist."""
        tracks = self.get_playlist_tracks(playlist_uri)
        if not tracks:
            return 0

        track_uris = [t.uri for t in tracks]
        removed, _ = self.remove_tracks(playlist_uri, track_uris, progress_callback)
        return removed

    def replace_all_tracks(
        self,
        playlist_uri: str,
        track_uris: List[str],
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> bool:
        """Replace all tracks in a playlist with new tracks."""
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id:
            return False

        try:
            if progress_callback:
                progress_callback("Replacing tracks...", 0, len(track_uris))

            # ---- Bridge path: clear then add -------------------------------
            if _is_bridge(self.api):
                success = self.api.replace_playlist_tracks(playlist_id, track_uris)
                self.api.clear_cache(f"playlist:{playlist_id}")
                return success

            # ---- Standard spotipy path -------------------------------------
            first_batch = track_uris[:API_LIMITS["PLAYLIST_BATCH_SIZE"]]

            self.api._rate_limit()
            self.client.playlist_replace_items(playlist_id, first_batch)

            if len(track_uris) > API_LIMITS["PLAYLIST_BATCH_SIZE"]:
                remaining = track_uris[API_LIMITS["PLAYLIST_BATCH_SIZE"]:]

                def add_progress(current, total):
                    if progress_callback:
                        done = len(first_batch) + current
                        progress_callback("Adding tracks...", done, len(track_uris))

                self.add_tracks(playlist_uri, remaining, progress_callback=add_progress)

            self.api.clear_cache(f"playlist:{playlist_id}")
            return True

        except Exception as e:
            self.api._handle_api_error(e, "Replace playlist tracks")
            return False

    def create_playlist(self, name: str, description: str = "", public: bool = False) -> Optional[Dict]:
        """Create a new playlist."""
        return self.api.create_playlist(name, description, public)

    def duplicate_playlist(
        self,
        source_uri: str,
        new_name: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ) -> Optional[Dict]:
        """Duplicate a playlist with all its tracks."""
        if progress_callback:
            progress_callback("Getting source playlist...", 0, 0)

        source = self.get_playlist_details(source_uri)
        if not source:
            print_error("Could not fetch source playlist")
            return None

        if progress_callback:
            progress_callback("Creating new playlist...", 0, 0)

        description = f"Duplicated from \"{source.get('name', 'Unknown')}\""
        new_playlist = self.create_playlist(new_name, description)
        if not new_playlist:
            print_error("Could not create new playlist")
            return None

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
        """Update playlist name, description, or visibility."""
        playlist_id = parse_spotify_uri(playlist_uri, "playlist")
        if not playlist_id:
            return False

        try:
            # ---- Bridge path -----------------------------------------------
            if _is_bridge(self.api):
                self.api._call(
                    "update_playlist_details",
                    {
                        "playlist_id": playlist_id,
                        "name": name,
                        "description": description,
                        "public": public,
                    },
                )
                self.api.clear_cache(f"playlist:{playlist_id}")
                return True

            # ---- Standard spotipy path -------------------------------------
            self.api._rate_limit()
            self.client.playlist_change_details(
                playlist_id,
                name=name,
                description=description,
                public=public
            )
            self.api.clear_cache(f"playlist:{playlist_id}")
            return True

        except Exception as e:
            self.api._handle_api_error(e, "Update playlist details")
            return False

    def analyze_playlist(
        self,
        playlist_uri: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Dict:
        """Analyze a playlist and return statistics."""
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
        uri_counts = {}
        name_artist_counts = {}

        for track in tracks:
            artists.update(track.artists)
            albums.add(track.album_name)
            total_duration += track.duration_ms
            total_popularity += track.popularity

            uri_counts[track.uri] = uri_counts.get(track.uri, 0) + 1
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
            'similar_duplicates': similar_duplicates - exact_duplicates,
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
            from rich.table import Table
            table = Table(title="Your Playlists", show_header=True, header_style="bold cyan")
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
        """Interactive playlist selection."""
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
                from rich.prompt import Prompt
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
                return self._manual_playlist_entry(choice)
            print_error("Invalid input")
            return None

    def _manual_playlist_entry(self, initial_value: str = "") -> Optional[Dict]:
        """Manual playlist URI/URL entry."""
        if RICH_AVAILABLE:
            from rich.prompt import Prompt
            uri = Prompt.ask(
                "Enter playlist URL or URI",
                default=initial_value
            ) if not initial_value else initial_value
        else:
            uri = initial_value or input("Enter playlist URL or URI: ").strip()

        if not uri:
            return None

        playlist_id = parse_spotify_uri(uri, "playlist")
        if not playlist_id:
            print_error("Invalid playlist URL/URI")
            return None

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


class PlaylistBackup:
    """Manages playlist backups for recovery from failed operations."""

    BACKUP_FILE = CONFIG_DIR / "playlist_backup.json"
    MAX_AGE_HOURS = 24

    @classmethod
    def create(cls, playlist_uri: str, playlist_name: str, track_uris: List[str], operation: str) -> bool:
        """Create a backup before destructive operation."""
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

            age_hours = (time.time() - backup.get('created_at', 0)) / 3600
            if age_hours > cls.MAX_AGE_HOURS:
                cls.discard()
                return None

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
        """Check for pending backup and offer to restore."""
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
            from rich.prompt import Prompt
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
        """Restore playlist from backup."""
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
