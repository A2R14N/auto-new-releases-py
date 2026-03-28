"""
install_bridge.py
~~~~~~~~~~~~~~~~~
Copies anr-bridge.js to the Spicetify extensions folder and prints
instructions to enable it.

Usage:
    python install_bridge.py
"""

import os
import shutil
import sys
from pathlib import Path


def find_spicetify_extensions() -> Path:
    """Locate the Spicetify extensions directory."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA environment variable not set (Windows only)")

    # Standard location
    candidate = Path(appdata) / "spicetify" / "Extensions"
    if candidate.exists():
        return candidate

    # Custom config root via environment variable
    custom = os.environ.get("SPICETIFY_CONFIG")
    if custom:
        candidate2 = Path(custom) / "Extensions"
        if candidate2.exists():
            return candidate2
        # Create it if spicetify config root exists
        if Path(custom).exists():
            candidate2.mkdir(parents=True, exist_ok=True)
            return candidate2

    raise FileNotFoundError(
        "Could not find Spicetify extensions folder.\n"
        "Expected: %APPDATA%\\spicetify\\Extensions\\\n"
        "Make sure Spicetify is installed: https://spicetify.app/"
    )


def main():
    script_dir = Path(__file__).parent
    source = script_dir / "anr-bridge.js"

    if not source.exists():
        print(f"[ERROR] Could not find {source}")
        sys.exit(1)

    try:
        dest_dir = find_spicetify_extensions()
    except (RuntimeError, FileNotFoundError) as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    dest = dest_dir / "anr-bridge.js"
    shutil.copy2(source, dest)
    print(f"[OK] Copied {source.name} → {dest}")
    print()
    print("Next steps:")
    print("  1. Enable the extension:")
    print("       spicetify config extensions anr-bridge.js")
    print("  2. Apply changes to Spotify:")
    print("       spicetify apply")
    print("  3. Open Spotify — you should see a '🎸 ANR Bridge active' toast")
    print("  4. Run auto-new-releases normally:")
    print("       python auto_new_releases.py")
    print()
    print("The bridge is active as long as Spotify is open.")
    print("ANR will auto-detect the bridge and skip OAuth entirely.")


if __name__ == "__main__":
    main()
