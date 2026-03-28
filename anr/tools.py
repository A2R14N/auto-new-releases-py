"""
Playlist tools: sorting, deduplication, duplication, analysis,
and artist track removal.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Tuple, Callable

from .constants import (
    RICH_AVAILABLE, console,
    print_success, print_error, print_warning, print_info,
    parse_release_date,
)
from .api import SpotifyAPI
from .playlist import PlaylistOperations, PlaylistTrack, PlaylistBackup, _is_bridge
from .filters import DuplicateDetector, DuplicateInfo


class SortCriteria(Enum):
    RELEASE_DATE = "release_date"
    POPULARITY = "popularity"
    TRACK_NAME = "name"
    ARTIST_NAME = "artist"
    ALBUM_NAME = "album"
    DURATION = "duration"
    DATE_ADDED = "added_at"


class SortOrder(Enum):
    ASCENDING = "asc"
    DESCENDING = "desc"


@dataclass
class SortResult:
    success: bool
    tracks_sorted: int
    sort_criteria: SortCriteria
    sort_order: SortOrder
    duration_seconds: float = 0
    error_message: str = ""


@dataclass
class DedupeResult:
    success: bool
    duplicates_found: int
    duplicates_removed: int
    exact_duplicates: int
    similar_duplicates: int
    duration_seconds: float = 0
    error_message: str = ""
    removed_tracks: List[DuplicateInfo] = field(default_factory=list)


@dataclass
class DuplicatePlaylistResult:
    success: bool
    source_name: str
    new_name: str
    new_uri: str = ""
    tracks_copied: int = 0
    duration_seconds: float = 0
    error_message: str = ""


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
        progress_callback=None,
        create_backup: bool = True
    ) -> SortResult:
        start_time = time.time()
        result = SortResult(success=False, tracks_sorted=0, sort_criteria=criteria, sort_order=order)

        try:
            playlist = self.ops.get_playlist_details(playlist_uri)
            if not playlist:
                result.error_message = "Could not fetch playlist"
                return result

            playlist_name = playlist.get('name', 'Unknown')

            if progress_callback:
                progress_callback("Fetching tracks...", 0, 0)

            tracks = self.ops.get_playlist_tracks(playlist_uri)

            if not tracks:
                result.error_message = "Playlist is empty"
                return result

            if create_backup:
                PlaylistBackup.create(
                    playlist_uri, playlist_name,
                    [t.uri for t in tracks], f"sort_by_{criteria.value}"
                )

            # ----------------------------------------------------------------
            # Build sortable list.
            # For RELEASE_DATE + bridge: use the fast two-phase approach
            #   Phase 1: artist discography (bulk, ~455 calls for ~1800 albums)
            #   Phase 2: direct album lookup for remainder (~200 calls)
            #   Total: ~660 calls, ~19s, zero rate limit waits
            # For other criteria or non-bridge: use get_multiple_tracks.
            # ----------------------------------------------------------------
            track_map: Dict = {}
            album_date_map: Dict[str, Optional[str]] = {}

            if criteria == SortCriteria.RELEASE_DATE and _is_bridge(self.api):
                if progress_callback:
                    progress_callback("Fetching album release dates...", 0, 0)

                # Collect unique album URIs, seeding with dates already known
                unique_album_uris = []
                seen_albums = set()
                for pt in tracks:
                    if pt.album_uri and pt.album_uri not in seen_albums:
                        seen_albums.add(pt.album_uri)
                        if pt.release_date:
                            album_date_map[pt.album_uri] = pt.release_date
                        else:
                            unique_album_uris.append(pt.album_uri)

                if unique_album_uris:
                    try:
                        # Single bridge call — JS handles artist discography
                        # bulk resolution + direct album fallback internally
                        date_map = self.api.get_album_release_dates(
                            unique_album_uris, playlist_uri=playlist_uri
                        )
                        album_date_map.update(date_map)
                        print_info(
                            f"Fetched {len(date_map)}/{len(unique_album_uris)} album dates "
                            f"({len(album_date_map)} total, {len(tracks)} tracks)"
                        )
                    except Exception as e:
                        print_warning(f"Album date fetch failed ({e}), falling back to track lookup")

                if album_date_map:
                    print_info(f"Have dates for {len(album_date_map)} unique albums")

            else:
                # Non-release-date sort or non-bridge: use full track details
                track_uris = [t.uri for t in tracks]
                full_tracks = self.api.get_multiple_tracks(track_uris)
                if not full_tracks:
                    result.error_message = "Could not fetch track details"
                    return result
                track_map = {t.get('uri'): t for t in full_tracks if t}

            # Build the sortable list
            missing_date_count = 0
            sortable = []
            for pt in tracks:
                full = track_map.get(pt.uri, {})

                # Release date: album map > pt.release_date > full track data
                if album_date_map:
                    release_date = (
                        album_date_map.get(pt.album_uri)
                        or pt.release_date
                        or (full.get('album') or {}).get('release_date')
                        or ''
                    )
                else:
                    full_release_date = (full.get('album') or {}).get('release_date')
                    release_date = full_release_date or pt.release_date or ''

                if not release_date:
                    missing_date_count += 1

                sortable.append({
                    'uri': pt.uri,
                    'name': pt.name,
                    'artists': pt.artists,
                    'album_name': pt.album_name,
                    'release_date': release_date,
                    'popularity': full.get('popularity', 0),
                    'duration_ms': full.get('duration_ms', 0),
                    'added_at': pt.added_at or '',
                })

            if missing_date_count:
                print_warning(f"{missing_date_count} tracks have no release date and will sort last")

            reverse = (order == SortOrder.DESCENDING)

            if criteria == SortCriteria.RELEASE_DATE:
                sortable.sort(
                    key=lambda t: parse_release_date(t.get('release_date', '')) or datetime.min,
                    reverse=reverse
                )
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

            sorted_uris = [t['uri'] for t in sortable]
            success = self.ops.replace_all_tracks(playlist_uri, sorted_uris, progress_callback)

            if success:
                result.success = True
                result.tracks_sorted = len(sorted_uris)
                PlaylistBackup.complete()
            else:
                result.error_message = "Failed to replace tracks"

            result.duration_seconds = time.time() - start_time
            return result

        except Exception as e:
            result.error_message = str(e)
            result.duration_seconds = time.time() - start_time
            return result

    def sort_by_release_date(self, playlist_uri: str, newest_first: bool = True, progress_callback=None) -> SortResult:
        order = SortOrder.DESCENDING if newest_first else SortOrder.ASCENDING
        return self.sort_playlist(playlist_uri, SortCriteria.RELEASE_DATE, order, progress_callback)

    def display_sort_options(self):
        print("\nSort Options:")
        for i, label in [
            (1, "Release Date (newest first)"), (2, "Release Date (oldest first)"),
            (3, "Popularity (most popular first)"), (4, "Popularity (least popular first)"),
            (5, "Track Name (A-Z)"), (6, "Track Name (Z-A)"),
            (7, "Artist Name (A-Z)"), (8, "Artist Name (Z-A)"),
            (9, "Album Name (A-Z)"),
            (10, "Duration (longest first)"), (11, "Duration (shortest first)"),
            (12, "Date Added (newest first)"),
        ]:
            print(f"  {i:2}. {label}")
        print("   0. Cancel\n")

    def get_sort_from_choice(self, choice: int) -> Optional[Tuple[SortCriteria, SortOrder]]:
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


class PlaylistDeduplicator:
    """Handles playlist deduplication."""

    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops

    def find_duplicates(self, playlist_uri: str, include_similar: bool = False, progress_callback=None):
        if progress_callback:
            progress_callback("Fetching tracks...")
        tracks = self.ops.get_playlist_tracks(playlist_uri)
        if not tracks:
            return [], []
        if progress_callback:
            progress_callback(f"Analyzing {len(tracks)} tracks...")
        duplicates, _ = DuplicateDetector.find_duplicates(tracks, include_similar)
        return duplicates, tracks

    def interactive_dedupe(self, playlist_uri: str, include_similar: bool = False, create_backup: bool = True) -> DedupeResult:
        start_time = time.time()
        result = DedupeResult(success=True, duplicates_found=0, duplicates_removed=0, exact_duplicates=0, similar_duplicates=0)

        playlist = self.ops.get_playlist_details(playlist_uri)
        if not playlist:
            result.success = False
            result.error_message = "Could not fetch playlist"
            return result

        playlist_name = playlist.get('name', 'Unknown')
        print_info(f"Scanning playlist: {playlist_name}")

        duplicates, tracks = self.find_duplicates(playlist_uri, include_similar, lambda s: print_info(s))

        if not duplicates:
            print_success("No duplicates found!")
            result.duration_seconds = time.time() - start_time
            return result

        exact = [d for d in duplicates if d.match_type == 'exact']
        similar = [d for d in duplicates if d.match_type == 'similar']
        result.duplicates_found = len(duplicates)
        result.exact_duplicates = len(exact)
        result.similar_duplicates = len(similar)

        print()
        print_info(f"Found {len(duplicates)} duplicates in {len(tracks)} tracks:")
        print_info(f"  - Exact duplicates: {len(exact)}")
        if include_similar:
            print_info(f"  - Similar tracks: {len(similar)}")

        DuplicateDetector.display_duplicates(duplicates)

        if RICH_AVAILABLE:
            from rich.prompt import Confirm
            should_remove = Confirm.ask(f"Remove {len(duplicates)} duplicate(s)?", default=False)
        else:
            response = input(f"Remove {len(duplicates)} duplicate(s)? [y/N]: ").strip().lower()
            should_remove = response in ('y', 'yes')

        if not should_remove:
            print_info("No changes made")
            result.duration_seconds = time.time() - start_time
            return result

        if create_backup:
            PlaylistBackup.create(playlist_uri, playlist_name, [t.uri for t in tracks], "deduplicate")

        uris_to_remove = [d.uri for d in duplicates]
        removed, failed = self.ops.remove_tracks(playlist_uri, uris_to_remove)
        result.duplicates_removed = removed
        result.removed_tracks = duplicates

        if failed > 0:
            result.error_message = f"Failed to remove {failed} tracks"
        else:
            PlaylistBackup.complete()

        result.duration_seconds = time.time() - start_time
        if removed > 0:
            print_success(f"Removed {removed} duplicates")
        return result

    def remove_duplicates(self, playlist_uri: str, include_similar: bool = False, progress_callback=None, create_backup: bool = True) -> DedupeResult:
        start_time = time.time()
        result = DedupeResult(success=False, duplicates_found=0, duplicates_removed=0, exact_duplicates=0, similar_duplicates=0)

        try:
            playlist = self.ops.get_playlist_details(playlist_uri)
            if not playlist:
                result.error_message = "Could not fetch playlist"
                return result

            playlist_name = playlist.get('name', 'Unknown')
            duplicates, tracks = self.find_duplicates(
                playlist_uri, include_similar,
                lambda s: progress_callback(s, 0, 0) if progress_callback else None
            )

            if not duplicates:
                result.success = True
                result.duration_seconds = time.time() - start_time
                return result

            result.duplicates_found = len(duplicates)
            result.exact_duplicates = len([d for d in duplicates if d.match_type == 'exact'])
            result.similar_duplicates = len([d for d in duplicates if d.match_type == 'similar'])

            if create_backup:
                PlaylistBackup.create(playlist_uri, playlist_name, [t.uri for t in tracks], "deduplicate")

            uris_to_remove = [d.uri for d in duplicates]
            removed, failed = self.ops.remove_tracks(playlist_uri, uris_to_remove)
            result.duplicates_removed = removed
            result.removed_tracks = duplicates
            result.success = True

            if failed > 0:
                result.error_message = f"Failed to remove {failed} tracks"
            else:
                PlaylistBackup.complete()

            result.duration_seconds = time.time() - start_time
            return result

        except Exception as e:
            result.error_message = str(e)
            result.duration_seconds = time.time() - start_time
            return result

    def display_result(self, result: DedupeResult):
        if RICH_AVAILABLE:
            from rich.panel import Panel
            if result.success and result.duplicates_removed > 0:
                color, status = "green", "SUCCESS"
            elif result.success and result.duplicates_found == 0:
                color, status = "blue", "NO DUPLICATES"
            else:
                color, status = "red", "ERROR"
            lines = [
                f"[bold]Status:[/] [{color}]{status}[/]",
                f"[bold]Duplicates Found:[/] {result.duplicates_found}",
                f"[bold green]Removed:[/] {result.duplicates_removed}",
            ]
            if result.error_message:
                lines.append(f"[bold red]Error:[/] {result.error_message}")
            console.print(Panel("\n".join(lines), title="[bold]Deduplication Results[/]", border_style=color))
        else:
            print(f"\n=== Deduplication Results ===\n  Found: {result.duplicates_found}\n  Removed: {result.duplicates_removed}")
            if result.error_message:
                print(f"  Error: {result.error_message}")


class PlaylistDuplicator:
    """Handles playlist duplication."""

    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops

    def duplicate(self, source_uri: str, new_name: Optional[str] = None, progress_callback=None) -> DuplicatePlaylistResult:
        start_time = time.time()
        result = DuplicatePlaylistResult(success=False, source_name="", new_name="")

        try:
            source = self.ops.get_playlist_details(source_uri)
            if not source:
                result.error_message = "Could not fetch source playlist"
                return result

            result.source_name = source.get('name', 'Unknown')
            result.new_name = new_name or f"{result.source_name} (Copy)"

            new_playlist = self.ops.create_playlist(result.new_name, description=f"Duplicated from \"{result.source_name}\"")

            if not new_playlist:
                result.error_message = "Could not create new playlist"
                return result

            result.new_uri = new_playlist.get('uri', '')
            tracks = self.ops.get_playlist_tracks(source_uri)

            if tracks:
                added, failed = self.ops.add_tracks(result.new_uri, [t.uri for t in tracks])
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
        if result.success:
            print_success(f"Created \"{result.new_name}\"")
            print_info(f"  Copied {result.tracks_copied} tracks from \"{result.source_name}\"")
            if result.error_message:
                print_warning(f"  Warning: {result.error_message}")
        else:
            print_error(f"Failed to duplicate playlist: {result.error_message}")


class PlaylistAnalyzer:
    """Analyzes playlists and provides insights."""

    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops

    def analyze(self, playlist_uri: str, detailed: bool = False, progress_callback=None) -> Dict:
        stats = self.ops.analyze_playlist(playlist_uri, progress_callback)
        if not stats or stats.get('total_tracks', 0) == 0:
            return {'error': 'Could not analyze playlist'}

        if detailed:
            tracks = self.ops.get_playlist_tracks(playlist_uri)
            if tracks:
                artist_counts: Dict[str, int] = {}
                year_counts: Dict[str, int] = {}

                for track in tracks:
                    for artist in track.artists:
                        artist_counts[artist] = artist_counts.get(artist, 0) + 1
                    if track.release_date:
                        year = track.release_date[:4]
                        year_counts[year] = year_counts.get(year, 0) + 1

                stats['top_artists'] = sorted(artist_counts.items(), key=lambda x: x[1], reverse=True)[:10]
                stats['year_distribution'] = sorted(year_counts.items(), key=lambda x: x[0], reverse=True)

        return stats

    def display_analysis(self, playlist_uri: str, detailed: bool = False):
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
            console.print(Panel("\n".join(lines), title=f"[bold cyan]Playlist Analysis: {name}[/]", border_style="cyan"))

            if detailed and 'top_artists' in stats and stats['top_artists']:
                table = Table(title="Top Artists", show_header=True, header_style="bold")
                table.add_column("Artist", style="white")
                table.add_column("Tracks", justify="right")
                for artist, count in stats['top_artists'][:10]:
                    table.add_row(artist[:30], str(count))
                console.print(table)

            if detailed and 'year_distribution' in stats and stats['year_distribution']:
                table = Table(title="Release Years", show_header=True, header_style="bold")
                table.add_column("Year", style="white")
                table.add_column("Tracks", justify="right")
                for year, count in stats['year_distribution'][:10]:
                    table.add_row(year, str(count))
                console.print(table)
        else:
            print(f"\n=== Playlist Analysis: {name} ===")
            print(f"  Total Tracks: {stats.get('total_tracks', 0)}")
            print(f"  Unique Artists: {stats.get('unique_artists', 0)}")
            print(f"  Avg Popularity: {stats.get('avg_popularity', 0)}/100")
            if detailed and 'top_artists' in stats:
                print("\n  Top Artists:")
                for artist, count in stats['top_artists'][:5]:
                    print(f"    {artist}: {count} tracks")
            print()


class ArtistTrackRemover:
    """Removes all tracks by a specific artist from a playlist."""

    def __init__(self, spotify_api: SpotifyAPI, playlist_ops: PlaylistOperations):
        self.api = spotify_api
        self.ops = playlist_ops

    def remove_artist_tracks(self, playlist_uri: str, artist_uri: str, progress_callback=None, create_backup: bool = True) -> Tuple[int, str]:
        from .constants import parse_spotify_uri

        artist = self.api.get_artist(artist_uri)
        if not artist:
            print_error("Could not find artist")
            return 0, ""

        artist_name = artist.get('name', 'Unknown')
        artist_id = parse_spotify_uri(artist_uri, "artist")

        tracks = self.ops.get_playlist_tracks(playlist_uri)
        if not tracks:
            print_info("Playlist is empty")
            return 0, artist_name

        tracks_to_remove = []

        for idx, track in enumerate(tracks):
            track_data = self.api.get_track(track.uri)
            if track_data:
                track_artist_ids = [parse_spotify_uri(a.get('uri', ''), 'artist') for a in track_data.get('artists', [])]
                if artist_id in track_artist_ids:
                    tracks_to_remove.append(track.uri)

            if progress_callback and idx % 10 == 0:
                progress_callback("Finding artist tracks...", idx, len(tracks))

        if not tracks_to_remove:
            print_info(f"No tracks by {artist_name} found in playlist")
            return 0, artist_name

        if create_backup:
            playlist = self.ops.get_playlist_details(playlist_uri)
            PlaylistBackup.create(
                playlist_uri,
                playlist.get('name', 'Unknown') if playlist else 'Unknown',
                [t.uri for t in tracks],
                f"remove_artist_{artist_name}"
            )

        removed, failed = self.ops.remove_tracks(playlist_uri, tracks_to_remove)
        if removed > 0 and failed == 0:
            PlaylistBackup.complete()

        return removed, artist_name


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

    def sort(self, playlist_uri: str, criteria: SortCriteria = SortCriteria.RELEASE_DATE, order: SortOrder = SortOrder.DESCENDING) -> SortResult:
        return self.sorter.sort_playlist(playlist_uri, criteria, order)

    def deduplicate(self, playlist_uri: str, include_similar: bool = False) -> DedupeResult:
        return self.deduplicator.remove_duplicates(playlist_uri, include_similar)

    def interactive_dedupe(self, playlist_uri: str, include_similar: bool = False) -> DedupeResult:
        return self.deduplicator.interactive_dedupe(playlist_uri, include_similar)

    def duplicate(self, source_uri: str, new_name: Optional[str] = None) -> DuplicatePlaylistResult:
        return self.duplicator.duplicate(source_uri, new_name)

    def analyze(self, playlist_uri: str, detailed: bool = False) -> Dict:
        return self.analyzer.analyze(playlist_uri, detailed)

    def remove_artist(self, playlist_uri: str, artist_uri: str) -> Tuple[int, str]:
        return self.artist_remover.remove_artist_tracks(playlist_uri, artist_uri)