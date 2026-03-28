"""
Track filtering, duplicate detection, remix detection,
and track processing logic for new releases.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Dict, Set, Tuple, Callable

from .constants import (
    REMIX_KEYWORDS, RICH_AVAILABLE, console,
    print_success, parse_release_date,
)
from .models import Profile
from .api import SpotifyAPI


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


class RemixDetector:
    """Detects remix and variant tracks."""

    _keyword_pattern: Optional[re.Pattern] = None

    @classmethod
    def _get_pattern(cls) -> re.Pattern:
        """Get compiled regex pattern for remix keywords."""
        if cls._keyword_pattern is None:
            escaped = [re.escape(kw) for kw in REMIX_KEYWORDS]
            pattern = r'\b(' + '|'.join(escaped) + r')\b'
            cls._keyword_pattern = re.compile(pattern, re.IGNORECASE)
        return cls._keyword_pattern

    @classmethod
    def is_remix_or_variant(cls, track_name: str, album_name: str = "") -> bool:
        """Check if a track is a remix or variant."""
        combined = f"{track_name} {album_name}".lower()
        pattern = cls._get_pattern()
        return bool(pattern.search(combined))

    @classmethod
    def get_matched_keywords(cls, track_name: str, album_name: str = "") -> List[str]:
        """Get list of matched remix keywords."""
        combined = f"{track_name} {album_name}".lower()
        pattern = cls._get_pattern()
        matches = pattern.findall(combined)
        return list(set(matches))


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
        name = name.lower().strip()
        name = re.sub(r'\([^)]*\)', '', name)
        name = re.sub(r'\[[^\]]*\]', '', name)
        name = ' '.join(name.split())
        return name

    @classmethod
    def find_duplicates(
        cls,
        tracks,  # List[PlaylistTrack]
        by_name_and_artist: bool = False
    ) -> Tuple[List[DuplicateInfo], Set[str]]:
        """Find duplicate tracks in a list."""
        duplicates = []
        unique_uris = set()

        uri_first_seen: Dict[str, int] = {}
        name_artist_first_seen: Dict[str, int] = {}

        for idx, track in enumerate(tracks):
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
            from rich.table import Table
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
        """Check if a single track should be included."""
        self.stats.total_input += 1

        if self.config.skip_duplicates and track_uri in self.config.existing_uris:
            self.stats.add_filtered(track_uri, track_name, FilterReason.DUPLICATE)
            return False

        if self.config.skip_remixes and RemixDetector.is_remix_or_variant(track_name, album_name):
            keywords = RemixDetector.get_matched_keywords(track_name, album_name)
            self.stats.add_filtered(
                track_uri, track_name, FilterReason.REMIX,
                f"Keywords: {', '.join(keywords)}"
            )
            return False

        if self.config.skip_low_popularity and popularity is not None:
            if popularity < self.config.min_popularity:
                self.stats.add_filtered(
                    track_uri, track_name, FilterReason.LOW_POPULARITY,
                    f"Popularity: {popularity} < {self.config.min_popularity}"
                )
                return False

        if self.config.limit_per_album and album_uri:
            current_count = self._album_track_counts.get(album_uri, 0)
            if current_count >= self.config.max_per_album:
                self.stats.add_filtered(
                    track_uri, track_name, FilterReason.ALBUM_LIMIT,
                    f"Album already has {current_count} tracks"
                )
                return False
            self._album_track_counts[album_uri] = current_count + 1

        self.stats.passed += 1
        self.config.existing_uris.add(track_uri)
        return True

    def filter_tracks(
        self,
        tracks: List[Dict],
        album_name: str = "",
        album_uri: str = ""
    ) -> List[Dict]:
        """Filter a list of tracks."""
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
        """Check if an entire album should be skipped."""
        if self.config.skip_long_albums:
            if album_total_tracks > self.config.max_album_tracks:
                return True, f"Too many tracks ({album_total_tracks} > {self.config.max_album_tracks})"

        if self.config.skip_low_popularity and album_popularity is not None:
            if album_popularity < self.config.min_popularity:
                return True, f"Low popularity ({album_popularity} < {self.config.min_popularity})"

        return False, ""


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

    def process_release(self, release: Dict, artist_name: str) -> ProcessedRelease:
        """Process a single release and extract tracks."""
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

        if release_uri in self.profile.tracked_releases:
            result.skipped = True
            result.skip_reason = "Already tracked"
            return result

        album_details = self.api.get_album(release_uri)
        if not album_details:
            result.skipped = True
            result.skip_reason = "Could not fetch album details"
            return result

        album_popularity = album_details.get('popularity')

        should_skip, skip_reason = self.track_filter.check_album_skip(total_tracks, album_popularity)

        if should_skip:
            result.skipped = True
            result.skip_reason = skip_reason

            if "Too many tracks" in skip_reason:
                self.stats.filtered_long_albums += 1
            elif "Low popularity" in skip_reason:
                self.stats.filtered_low_popularity += 1

            return result

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
        """Process multiple releases."""
        results = []
        total = len(releases)

        for idx, release in enumerate(releases):
            processed = self.process_release(release, artist_name)
            results.append(processed)

            if progress_callback:
                progress_callback(idx + 1, total)

        return results

    def get_tracks_to_add(self, processed_releases: List[ProcessedRelease]) -> List[str]:
        """Extract track URIs from processed releases."""
        track_uris = []

        for release in processed_releases:
            if not release.skipped:
                for track in release.tracks:
                    track_uris.append(track.get('uri'))

        return [uri for uri in track_uris if uri]


class PopularitySorter:
    """Sorts and limits tracks by popularity."""

    def __init__(self, spotify_api: SpotifyAPI):
        self.api = spotify_api

    def get_top_tracks_by_popularity(self, track_uris: List[str], limit: int) -> List[str]:
        """Get top N tracks by popularity."""
        if len(track_uris) <= limit:
            return track_uris

        tracks = self.api.get_multiple_tracks(track_uris)

        if not tracks:
            return track_uris[:limit]

        tracks_sorted = sorted(tracks, key=lambda t: t.get('popularity', 0), reverse=True)
        return [t.get('uri') for t in tracks_sorted[:limit] if t.get('uri')]

    def sort_tracks_by_popularity(self, track_uris: List[str], ascending: bool = False) -> List[str]:
        """Sort tracks by popularity."""
        if not track_uris:
            return []

        tracks = self.api.get_multiple_tracks(track_uris)

        if not tracks:
            return track_uris

        tracks_sorted = sorted(tracks, key=lambda t: t.get('popularity', 0), reverse=not ascending)
        return [t.get('uri') for t in tracks_sorted if t.get('uri')]


class ReleaseDateFilter:
    """Filters releases by date."""

    @staticmethod
    def filter_by_days(releases: List[Dict], days_back: int) -> List[Dict]:
        """Filter releases to those within N days."""
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
    def sort_by_release_date(releases: List[Dict], newest_first: bool = True) -> List[Dict]:
        """Sort releases by release date."""
        def get_date(release):
            date = parse_release_date(release.get('release_date', ''))
            return date or datetime.min

        return sorted(releases, key=get_date, reverse=newest_first)

    @staticmethod
    def group_by_date(releases: List[Dict]) -> Dict[str, List[Dict]]:
        """Group releases by release date."""
        groups: Dict[str, List[Dict]] = {}

        for release in releases:
            date_str = release.get('release_date', 'Unknown')
            if date_str not in groups:
                groups[date_str] = []
            groups[date_str].append(release)

        return groups


@dataclass
class TrackCollection:
    """Collection of tracks with metadata."""
    tracks: List[str] = field(default_factory=list)
    sources: Dict[str, List[str]] = field(default_factory=dict)

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


def create_filter_from_profile(profile: Profile, existing_uris: Set[str]) -> TrackFilter:
    """Create a TrackFilter from a profile."""
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
    """Quick track filtering without full profile."""
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
