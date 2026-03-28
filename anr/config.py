"""
Configuration management: loading, saving, and accessing config.json.
"""

import json
from typing import Optional

from .constants import CONFIG_FILE, ensure_config_dir, generate_id, print_warning
from .models import Artist, Profile, Config


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
