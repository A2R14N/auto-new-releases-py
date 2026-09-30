"""Fixed Spotify notifications, controlled by one global on/off switch."""

import logging

DEFAULT_MESSAGES = {
    "start": "ANR: Checking {profile} for new releases...",
    "sorting": "ANR: Sorting {playlist} by release date...",
    "success": "ANR: Added {added} songs to {playlist} ({duration}s).",
    "no_new": "ANR: No new releases for {profile}.",
    "partial": "ANR: {profile} finished with an issue: {error}",
    "error": "ANR: {profile} failed: {error}",
}
NOTIFICATION_DURATION_MS = 6000


def render_notification(profile, event, result=None):
    template = DEFAULT_MESSAGES.get(event, "")
    if not template:
        return ""
    values = {
        "profile": profile.name, "playlist": profile.playlist_name or profile.name,
        "added": getattr(result, "total_tracks_added", 0),
        "duration": f"{getattr(result, 'duration_seconds', 0):.1f}",
        "error": getattr(result, "error_message", "") or "Unknown error",
    }
    return template.format_map(values)


def notify_profile(api, profile, event, result=None, *, enabled=False):
    """Notification failures must never change release-check results."""
    from .bridge_api import BridgeAPI
    if not isinstance(api, BridgeAPI) or not enabled:
        return False
    try:
        message = render_notification(profile, event, result)
        if not message:
            return False
        return api.show_notification(message, event in ("partial", "error"), NOTIFICATION_DURATION_MS)
    except Exception:
        logging.getLogger(__name__).warning("Could not show ANR notification", exc_info=True)
        return False
