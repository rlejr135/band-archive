"""Deprecated compatibility wrapper for the safe M4A repair command.

It never generates 720p/480p files. With no flags it is a read-only dry run.
Use ``--enqueue`` only after reviewing the dry-run summary.
"""

from repair_media_processing import main


if __name__ == '__main__':
    raise SystemExit(main())
