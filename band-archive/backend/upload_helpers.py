"""Small, storage-agnostic helpers for legacy multipart form uploads."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class PreparedUpload:
    filename: str
    original_filename: str
    content_type: str
    file_size: int


def prepare_upload(file, filename, content_type):
    """Capture form-upload metadata and rewind the stream for storage upload."""
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    return PreparedUpload(
        filename=filename,
        original_filename=file.filename,
        content_type=content_type,
        file_size=file_size,
    )
