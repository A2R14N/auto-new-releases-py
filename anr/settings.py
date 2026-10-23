"""Direct settings choices, backed by the existing profile configuration."""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    field: str
    label: str
    description: str
    options: Tuple
    value_format: str = ''
    zero_label: str = ''
    flag: str = ''
    global_setting: bool = False
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    custom_label: str = ''
    custom_help: str = ''

    def current(self, profile, config):
        owner = config if self.global_setting else profile
        if self.flag and not getattr(owner, self.flag):
            return 0
        return getattr(owner, self.field)

    def format(self, value):
        if self.zero_label and value == 0:
            return self.zero_label
        if self.value_format:
            return self.value_format.format(value=value)
        label = next(label for option, label in self.options if option == value)
        return label + (' · all profiles' if self.global_setting else '')

    def apply(self, profile, config, value):
        """Validate before mutation; zero disables a filter without losing its limit."""
        if self.minimum is not None:
            if type(value) is not int or not self.minimum <= value <= self.maximum:
                raise ValueError(f'Enter a whole number from {self.minimum} to {self.maximum}.')
        elif not any(type(value) is type(option) and value == option for option, _ in self.options):
            raise ValueError('Choose one of the options shown.')
        owner = config if self.global_setting else profile
        updates = {self.field: value}
        if self.flag:
            updates = {self.flag: value > 0}
            if value > 0:
                updates[self.field] = value
        changed = any(getattr(owner, field) != new_value for field, new_value in updates.items())
        for field, new_value in updates.items():
            setattr(owner, field, new_value)
        return changed


SETTINGS = (
    Setting('1', 'Checking', 'check_interval', 'Automatic check interval',
        'How often automatic checks run for this profile. ANR must be running in daemon '
        'mode (anr daemon); changing this does not start it. Manual checks remain available.',
        ((6, 'Every 6 hours'), (12, 'Every 12 hours'), (24, 'Every 24 hours')),
        value_format='Every {value} hours', minimum=1, maximum=168,
        custom_label='Hours between checks', custom_help='1–168 hours. Enter keeps the current value; b goes back.'),
    Setting('2', 'Checking', 'days_to_check', 'Release window',
        'Only releases dated within this window are considered. All dates removes the date '
        'cutoff. Songs already in the playlist or tracking history are still excluded.',
        ((7, 'Last 7 days'), (30, 'Last 30 days'), (90, 'Last 90 days'), (0, 'All dates')),
        value_format='Last {value} days', zero_label='All dates', minimum=0, maximum=365,
        custom_label='Days to look back', custom_help='0 = all dates · 1–365 = last N days. Enter keeps the value; b goes back.'),
    Setting('3', 'Playlist', 'sort_by_date', 'Playlist order',
        'Newest first sorts the playlist by album release date after new songs are added. '
        'Keep current order leaves the existing order alone.',
        ((True, 'Newest first'), (False, 'Keep current order'))),
    Setting('4', 'Filters', 'skip_remixes', 'Remixes & other versions',
        'Exclude versions filters track and release names for remixes, live recordings, '
        'acoustic cuts, remasters, covers and related keywords. Name matching can also '
        'exclude a song whose title contains those words.',
        ((False, 'Include all'), (True, 'Exclude versions'))),
    Setting('5', 'Filters', 'skip_similar_duplicates', 'Similar songs',
        'Exact Spotify track duplicates are always excluded. This extra filter also skips '
        'songs with the same title and primary artist even when their Spotify track IDs '
        'differ. It can treat different recordings as the same song.',
        ((False, 'Allow different recordings'), (True, 'Skip matching names'))),
    Setting('6', 'Filters', 'max_songs_per_album', 'Songs per album',
        'All songs adds every song that passes your other filters. Top N ranks eligible '
        'songs by Spotify popularity and adds up to that many from each release. '
        'Unavailable ranking data leaves the release pending.',
        ((0, 'All songs'), (3, 'Top 3 songs'), (5, 'Top 5 songs')),
        value_format='Top {value} songs', zero_label='All songs', flag='limit_songs_per_album',
        minimum=0, maximum=50, custom_label='Maximum songs to add per album',
        custom_help='0 = all eligible songs · 1–50 = top N songs. Enter keeps the value; b goes back.'),
    Setting('7', 'Filters', 'max_songs', 'Album size',
        'A release above this size is skipped entirely. This differs from Songs per album, '
        'which limits how many songs are selected from an accepted release. For example, '
        'a 24-track album is skipped at a size limit of 20, even with Songs per album set to Top 5.',
        ((0, 'Any size'), (20, 'Up to 20 tracks'), (40, 'Up to 40 tracks')),
        value_format='Up to {value} tracks', zero_label='Any size', flag='skip_long_albums',
        minimum=0, maximum=200, custom_label='Largest release to include',
        custom_help='0 = any size · 1–200 = maximum tracks in a release. Enter keeps the value; b goes back.'),
    Setting('8', 'Filters', 'min_popularity', 'Album popularity',
        'This filter uses the album popularity score, not artist followers or individual '
        'song scores. With a minimum enabled, unavailable scores leave the release pending '
        'rather than silently skipping it.',
        ((0, 'Any score'), (30, 'At least 30/100'), (50, 'At least 50/100')),
        value_format='At least {value}/100', zero_label='Any score', flag='skip_low_popularity',
        minimum=0, maximum=100, custom_label='Minimum album popularity',
        custom_help='0 = no popularity filter · 1–100 = minimum score. Enter keeps the value; b goes back.'),
    Setting('9', 'App', 'spotify_notifications', 'Spotify notifications',
        'Show activity and results inside Spotify desktop through the Spicetify bridge. '
        'This preference applies to every profile. These are Spotify popups, not '
        'operating-system notifications.',
        ((False, 'Off'), (True, 'On')), global_setting=True),
)

SETTING_BY_KEY = {setting.key: setting for setting in SETTINGS}
SETTING_GROUPS = ('Checking', 'Playlist', 'Filters', 'App')
