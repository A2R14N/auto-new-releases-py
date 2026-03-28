"""
Data models: Artist, Profile, Config.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict

from .constants import DEFAULT_VALUES


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
        key_mapping = {
            'lastFollowerUpdate': 'last_follower_update',
            'lastfollowerupdate': 'last_follower_update',
        }
        normalized = {}
        for key, value in data.items():
            normalized_key = key_mapping.get(key, key)
            normalized[normalized_key] = value

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
    skip_similar_duplicates: bool = False

    def to_dict(self) -> Dict:
        data = asdict(self)
        data['artists'] = [a if isinstance(a, dict) else a.to_dict() for a in self.artists]
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> 'Profile':
        """Create Profile from dict, handling both camelCase and snake_case keys."""
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
            'skipSimilarDuplicates': 'skip_similar_duplicates',
        }

        normalized = {}
        for key, value in data.items():
            normalized_key = key_mapping.get(key, key)
            normalized[normalized_key] = value

        artists_data = normalized.pop('artists', [])
        artists = []
        for a in artists_data:
            if isinstance(a, dict):
                artists.append(Artist.from_dict(a))
            elif isinstance(a, Artist):
                artists.append(a)

        valid_fields = {
            'id', 'name', 'playlist_uri', 'playlist_name', 'check_interval',
            'last_check', 'tracked_releases', 'tracked_tracks', 'days_to_check',
            'sort_by_date', 'skip_remixes', 'skip_low_popularity', 'min_popularity',
            'skip_long_albums', 'max_songs', 'limit_songs_per_album',
            'max_songs_per_album', 'skip_similar_duplicates'
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
