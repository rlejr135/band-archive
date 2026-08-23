"""Backward-compatible entry points for M4A-only media processing."""

from media_processing import extract_m4a_audio, start_audio_processing


def _transcode_video(filename):
    """Legacy name; extract only one M4A audio derivative."""
    return extract_m4a_audio(filename)


def transcode_video_async(app, media_id):
    """Legacy entry point using a media id, not a filename."""
    start_audio_processing(app, media_id)
