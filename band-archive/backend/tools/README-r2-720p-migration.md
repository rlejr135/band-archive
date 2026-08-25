# Immutable R2 720p migration

`migrate_r2_720p.py` is dry-run-by-default and has no Flask, SQLAlchemy, or
`/data` dependency. It calls the token-gated internal inventory on `--api-url`
(default `https://band-archive.fly.dev`; production host/HTTPS are enforced),
accepts only `file_type=video` entries, and configures R2 from `S3_ENDPOINT_URL`, `S3_BUCKET_NAME`,
`S3_ACCESS_KEY`, and `S3_SECRET_KEY`. Secret values and signed URLs are never
written to manifests or logs.

The worker never overwrites or deletes `media/<original>`. It creates a verified
immutable object at `media/transcoded/720/<media-id>/<source-etag>.<ext>`, only
when strictly smaller, and saves a private R2 manifest before derivative writes.
`--canary` limits one execution, not the full plan; `--resume` downloads the
remote manifest using `--run-id` or `--remote-manifest-key`.

```powershell
# Build/publish a dedicated image containing ffmpeg/ffprobe first. The same Fly
# app must already inject S3_* and R2_MIGRATION_TOKEN into this one-off machine.
fly machine run <migration-image> --app band-archive --region nrt --vm-size performance-2x --rootfs-size 8 --restart no --rm -- python tools/migrate_r2_720p.py --apply --canary 1 --remote-probe --run-id r2-720-20260825

# Resume on a new one-off rootfs.
fly machine run <migration-image> --app band-archive --region nrt --vm-size performance-2x --rootfs-size 8 --restart no --rm -- python tools/migrate_r2_720p.py --apply --resume --run-id r2-720-20260825
```

After manifest review, run the separate finalize command on an existing app
machine with `/data` mounted. It rechecks source ETag and derivative content,
then CAS-updates only Media's 720p reference; it performs no R2 write/delete.
Run finalize during a short maintenance window with no Media rename/delete/new
upload activity; it intentionally holds a brief DB CAS lock and re-HEADs each
original immediately before commit.

```powershell
fly ssh console -a band-archive -C "python tools/finalize_r2_720p_manifest.py --finalize-manifest private/migrations/r2-720p/r2-720-20260825/manifest.json"
fly ssh console -a band-archive -C "python tools/finalize_r2_720p_manifest.py --apply --finalize-manifest private/migrations/r2-720p/r2-720-20260825/manifest.json"
```

There is intentionally **no** `delete-original` or `--prune-originals` command.
Original deletion is prohibited until the user explicitly re-approves it after
the completed 720p rollout and production API verification. Normal user-driven
Media deletion remains separate and removes that record's original, audio, and
linked 720p derivative together.
