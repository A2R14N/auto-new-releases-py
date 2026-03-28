"""
Import and export functionality for profiles, artists, and configuration data.
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any

from .constants import (
    APP_NAME, APP_VERSION, RICH_AVAILABLE, console,
    print_success, print_error, print_warning, print_info,
    DEFAULT_VALUES, generate_id,
)
from .models import Artist, Profile
from .config import ConfigManager


class ExportFormat(Enum):
    JSON = "json"


class ImportMode(Enum):
    SKIP = "skip"
    REPLACE = "replace"
    RENAME = "rename"


@dataclass
class ExportResult:
    success: bool
    file_path: str = ""
    profiles_exported: int = 0
    artists_exported: int = 0
    error_message: str = ""


@dataclass
class ImportResult:
    success: bool
    profiles_imported: int = 0
    profiles_skipped: int = 0
    profiles_replaced: int = 0
    artists_imported: int = 0
    error_message: str = ""
    warnings: List[str] = field(default_factory=list)


@dataclass
class ExportMetadata:
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


class ProfileExporter:
    """Handles exporting profiles to files."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager

    def export_profile(self, profile: Profile, file_path: str, include_tracked: bool = True) -> ExportResult:
        result = ExportResult(success=False)

        try:
            profile_data = profile.to_dict()
            if not include_tracked:
                profile_data['tracked_releases'] = {}

            export_data = {
                **ExportMetadata().to_dict(),
                'type': 'single',
                'profile': profile_data,
            }

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

    def export_all_profiles(self, file_path: str, include_tracked: bool = True) -> ExportResult:
        result = ExportResult(success=False)

        try:
            profiles = self.config_manager.config.profiles
            profiles_data = []
            total_artists = 0

            for profile in profiles:
                profile_data = profile.to_dict()
                if not include_tracked:
                    profile_data['tracked_releases'] = {}
                profiles_data.append(profile_data)
                total_artists += len(profile.artists)

            export_data = {
                **ExportMetadata().to_dict(),
                'type': 'all',
                'active_profile_id': self.config_manager.config.active_profile_id,
                'profile_count': len(profiles),
                'profiles': profiles_data,
            }

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

    def export_artists_only(self, profile: Profile, file_path: str) -> ExportResult:
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
        if not file_path.endswith('.json'):
            file_path += '.json'
        return file_path

    def generate_filename(self, profile_name: Optional[str] = None, export_type: str = 'profile') -> str:
        date_str = datetime.now().strftime("%Y%m%d")
        if profile_name:
            safe_name = "".join(
                c if c.isalnum() or c in '-_' else '-'
                for c in profile_name.lower()
            ).strip('-')
            return f"anr-{export_type}-{safe_name}-{date_str}.json"
        else:
            return f"anr-{export_type}-all-{date_str}.json"


class ProfileImporter:
    """Handles importing profiles from files."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager

    def _camel_to_snake(self, name: str) -> str:
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    def _normalize_keys(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {self._camel_to_snake(k): self._normalize_keys(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._normalize_keys(item) for item in data]
        return data

    def import_from_file(self, file_path: str, mode: ImportMode = ImportMode.SKIP, new_name: Optional[str] = None) -> ImportResult:
        result = ImportResult(success=False)

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            data = self._normalize_keys(data)
            version = data.get('version', '1.0')
            export_type = data.get('type', self._detect_type(data))

            print_info(f"Detected format: v{version}, type: {export_type}")

            if export_type == 'all':
                return self._import_all_profiles(data, mode)
            elif export_type == 'single':
                return self._import_single_profile(data, mode, new_name)
            elif export_type == 'artists':
                return self._import_artists(data)
            else:
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
        if 'profiles' in data and isinstance(data['profiles'], list):
            return 'all'
        elif 'profile' in data:
            return 'single'
        elif 'artists' in data and 'profile' not in data:
            return 'artists'
        return 'unknown'

    def _import_single_profile(self, data: Dict, mode: ImportMode, new_name: Optional[str]) -> ImportResult:
        result = ImportResult(success=False)
        profile_data = data.get('profile', data)

        if not profile_data.get('artists') and not profile_data.get('name'):
            result.error_message = "Invalid profile data"
            return result

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
                self.config_manager.save()
                result.success = True
                result.profiles_replaced = 1
                result.artists_imported = len(existing.artists)
                return result
            elif mode == ImportMode.RENAME:
                profile_name = self._generate_unique_name(profile_name)

        new_profile = self._create_profile_from_data(profile_data, profile_name)
        self.config_manager.config.profiles.append(new_profile)
        self.config_manager.config.active_profile_id = new_profile.id
        self.config_manager.save()

        result.success = True
        result.profiles_imported = 1
        result.artists_imported = len(new_profile.artists)
        return result

    def _import_all_profiles(self, data: Dict, mode: ImportMode) -> ImportResult:
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

            new_profile = self._create_profile_from_data(profile_data, profile_name)
            self.config_manager.config.profiles.append(new_profile)
            result.profiles_imported += 1
            result.artists_imported += len(new_profile.artists)
            existing_names[profile_name.lower()] = new_profile

        self.config_manager.save()
        result.success = True
        return result

    def _import_artists(self, data: Dict) -> ImportResult:
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

    def _create_profile_from_data(self, data: Dict, name: str) -> Profile:
        profile_id = generate_id()
        artists = [Artist.from_dict(a) for a in data.get('artists', []) if isinstance(a, dict)]

        return Profile(
            id=profile_id,
            name=name,
            artists=artists,
            playlist_uri=data.get('playlist_uri', ''),
            playlist_name=data.get('playlist_name', ''),
            check_interval=data.get('check_interval', DEFAULT_VALUES['CHECK_INTERVAL']),
            last_check=None,
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
        profile.artists = [Artist.from_dict(a) for a in data.get('artists', []) if isinstance(a, dict)]
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
        if data.get('tracked_releases'):
            profile.tracked_releases = data.get('tracked_releases', {})

    def _generate_unique_name(self, base_name: str) -> str:
        existing_names = {p.name.lower() for p in self.config_manager.config.profiles}
        new_name = f"{base_name} (imported)"
        counter = 1
        while new_name.lower() in existing_names:
            counter += 1
            new_name = f"{base_name} (imported {counter})"
        return new_name


class ImportExportManager:
    """Central manager for import/export operations."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.exporter = ProfileExporter(config_manager)
        self.importer = ProfileImporter(config_manager)

    def export_current_profile(self, file_path: Optional[str] = None, include_tracked: bool = True) -> ExportResult:
        profile = self.config_manager.get_active_profile()
        if not file_path:
            file_path = self.exporter.generate_filename(profile.name, 'profile')
        return self.exporter.export_profile(profile, file_path, include_tracked)

    def export_all(self, file_path: Optional[str] = None, include_tracked: bool = True) -> ExportResult:
        if not file_path:
            file_path = self.exporter.generate_filename(None, 'all-profiles')
        return self.exporter.export_all_profiles(file_path, include_tracked)

    def export_artists(self, file_path: Optional[str] = None) -> ExportResult:
        profile = self.config_manager.get_active_profile()
        if not file_path:
            file_path = self.exporter.generate_filename(profile.name, 'artists')
        return self.exporter.export_artists_only(profile, file_path)

    def import_file(self, file_path: str, mode: ImportMode = ImportMode.SKIP) -> ImportResult:
        return self.importer.import_from_file(file_path, mode)

    def preview_import(self, file_path: str) -> Dict:
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
                    count = len(p.get('artists', []))
                    preview['profiles'].append({'name': p.get('name', 'Unknown'), 'artists': count, 'playlist': p.get('playlist_name', 'Not set')})
                    preview['total_artists'] += count
            elif 'profile' in data:
                p = data['profile']
                count = len(p.get('artists', []))
                preview['profiles'].append({'name': p.get('name', 'Unknown'), 'artists': count, 'playlist': p.get('playlist_name', 'Not set')})
                preview['total_artists'] = count
            elif 'artists' in data:
                preview['type'] = 'artists'
                preview['total_artists'] = len(data['artists'])

            return preview

        except Exception as e:
            return {'valid': False, 'error': str(e)}

    def display_preview(self, file_path: str):
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
            console.print(Panel("\n".join(lines), title="[bold cyan]Import Preview[/]", border_style="cyan"))

            if preview['profiles']:
                table = Table(title="Profiles in File", show_header=True, header_style="bold")
                table.add_column("Name", style="white")
                table.add_column("Artists", justify="right")
                table.add_column("Playlist")
                for p in preview['profiles']:
                    table.add_row(p['name'], str(p['artists']), (p['playlist'] or 'Not set')[:30])
                console.print(table)
        else:
            print(f"\n=== Import Preview ===")
            print(f"  File: {Path(file_path).name}")
            print(f"  Type: {preview['type']}")
            print(f"  Total Artists: {preview['total_artists']}")
            if preview['profiles']:
                print("\n  Profiles:")
                for p in preview['profiles']:
                    print(f"    - {p['name']}: {p['artists']} artists")
            print()


class ImportExportMenu:
    """Interactive menu for import/export operations."""

    def __init__(self, import_export_manager: ImportExportManager):
        self.manager = import_export_manager

    def prompt(self, message: str, default: str = "") -> str:
        if RICH_AVAILABLE:
            from rich.prompt import Prompt
            return Prompt.ask(message, default=default) if default else Prompt.ask(message)
        else:
            prompt_text = f"{message} [{default}]: " if default else f"{message}: "
            result = input(prompt_text).strip()
            return result if result else default

    def confirm(self, message: str, default: bool = False) -> bool:
        if RICH_AVAILABLE:
            from rich.prompt import Confirm
            return Confirm.ask(message, default=default)
        else:
            suffix = " [Y/n]: " if default else " [y/N]: "
            result = input(message + suffix).strip().lower()
            return result in ('y', 'yes') if result else default

    def run_export_current(self):
        profile = self.manager.config_manager.get_active_profile()

        print_info(f"Exporting profile: {profile.name}")
        print_info(f"  Artists: {len(profile.artists)}")

        include_tracked = self.confirm("Include tracked releases history? (larger file)", default=True)
        default_name = self.manager.exporter.generate_filename(profile.name, 'profile')
        file_path = self.prompt("Output filename", default=default_name)

        if not file_path:
            print_warning("Cancelled")
            return

        result = self.manager.export_current_profile(file_path, include_tracked)
        if result.success:
            print_success(f"Exported to: {result.file_path}")
            print_info(f"  Artists: {result.artists_exported}")
        else:
            print_error(f"Export failed: {result.error_message}")

    def run_export_all(self):
        profiles = self.manager.config_manager.config.profiles
        print_info(f"Exporting {len(profiles)} profiles")

        include_tracked = self.confirm("Include tracked releases history?", default=False)
        default_name = self.manager.exporter.generate_filename(None, 'all-profiles')
        file_path = self.prompt("Output filename", default=default_name)

        if not file_path:
            print_warning("Cancelled")
            return

        result = self.manager.export_all(file_path, include_tracked)
        if result.success:
            print_success(f"Exported to: {result.file_path}")
            print_info(f"  Profiles: {result.profiles_exported}, Artists: {result.artists_exported}")
        else:
            print_error(f"Export failed: {result.error_message}")

    def run_export_artists(self):
        profile = self.manager.config_manager.get_active_profile()
        if not profile.artists:
            print_warning("No artists to export")
            return

        print_info(f"Exporting {len(profile.artists)} artists from: {profile.name}")
        default_name = self.manager.exporter.generate_filename(profile.name, 'artists')
        file_path = self.prompt("Output filename", default=default_name)

        if not file_path:
            print_warning("Cancelled")
            return

        result = self.manager.export_artists(file_path)
        if result.success:
            print_success(f"Exported to: {result.file_path}")
            print_info(f"  Artists: {result.artists_exported}")
        else:
            print_error(f"Export failed: {result.error_message}")

    def run_import(self):
        file_path = self.prompt("Enter file path to import")
        if not file_path:
            print_warning("Cancelled")
            return

        if not Path(file_path).exists():
            print_error(f"File not found: {file_path}")
            return

        self.manager.display_preview(file_path)

        print("\nHow should duplicate profiles be handled?")
        print("  1. Skip - Keep existing, don't import duplicates")
        print("  2. Replace - Overwrite existing with imported")
        print("  3. Rename - Import with new name")
        print("  0. Cancel\n")

        try:
            if RICH_AVAILABLE:
                from rich.prompt import IntPrompt
                choice = IntPrompt.ask("Choice", default=1)
            else:
                choice = int(input("Choice [1]: ").strip() or "1")
        except ValueError:
            choice = 1

        if choice == 0:
            print_warning("Cancelled")
            return

        mode_map = {1: ImportMode.SKIP, 2: ImportMode.REPLACE, 3: ImportMode.RENAME}
        mode = mode_map.get(choice, ImportMode.SKIP)

        if not self.confirm("Proceed with import?", default=True):
            print_warning("Cancelled")
            return

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
