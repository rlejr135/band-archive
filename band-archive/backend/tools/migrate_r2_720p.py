"""Create verified immutable 720p R2 objects for song videos.

This program intentionally has no Flask, SQLAlchemy, or /data dependency. It
is safe in a disposable Fly machine: it discovers candidates through the
production read API, writes only new ``media/transcoded/720/...`` objects, and
records work in a private R2 manifest. It never overwrites or deletes originals.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
from urllib.parse import urlsplit


PROFILE = 'anything-r2-720p-h264-aac-v2'
PREFIX = 'private/migrations/r2-720p'
DEFAULT_API_URL = 'https://band-archive.fly.dev'
MANIFEST_SCHEMA = 2
CHUNK = 1024 * 1024
MIN_SCRATCH_BYTES = 5 * 1024 ** 3
CONTAINERS = {'.mp4': 'mp4', '.mov': 'mov', '.m4v': 'mp4'}


class MigrationError(RuntimeError):
    """Secret-safe reason code suitable for a private manifest."""

    def __init__(self, code='migration_error'):
        self.code = code
        super().__init__(code)


class Blocked(MigrationError):
    pass


class DestinationExists(MigrationError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(error):
    return error.code if isinstance(error, MigrationError) else 'unexpected_error'


def event(item, state, reason=''):
    # Object keys, presigned URLs, headers, and exception text must not reach
    # terminal logs. Media ID and a stable reason code are enough to operate.
    suffix = f' reason={reason}' if reason else ''
    print(f'media={item.get("media_id", "?")} state={state}{suffix}')


def get_etag(head):
    value = head.get('ETag') or head.get('etag')
    return value.strip('"') if isinstance(value, str) else None


def metadata(head, name):
    values = head.get('Metadata') or head.get('metadata') or {}
    return values.get(name) or values.get(name.lower())


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(CHUNK), b''):
            hasher.update(block)
    return hasher.hexdigest()


def _safe_filename(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        raise Blocked('invalid_filename')
    if '\x00' in value or '/' in value or '\\' in value or value != Path(value).name:
        raise Blocked('invalid_filename')
    suffix = Path(value).suffix.lower()
    if suffix not in CONTAINERS:
        raise Blocked('unsupported_video_container')
    return value


def source_key_for_filename(filename):
    return f'media/{_safe_filename(filename)}'


def _destination_etag_token(etag):
    if not isinstance(etag, str) or not etag:
        raise Blocked('source_metadata_missing')
    token = re.sub(r'[^A-Za-z0-9._-]', '-', etag.strip('"'))
    if not token or len(token) > 200:
        raise Blocked('invalid_source_etag')
    return token


def destination_key(media_id, source_etag, filename):
    if not isinstance(media_id, int) or isinstance(media_id, bool) or media_id < 1:
        raise Blocked('invalid_media_id')
    suffix = Path(_safe_filename(filename)).suffix.lower()
    return f'media/transcoded/720/{media_id}/{_destination_etag_token(source_etag)}{suffix}'


def _default_get(url, timeout, headers):
    try:
        import requests
    except ImportError as exc:
        raise MigrationError('requests_unavailable') from exc
    return requests.get(url, timeout=timeout, headers=headers)


def _inventory_url(api_url, allow_test_url=False):
    parsed = urlsplit(api_url)
    if not allow_test_url and (parsed.scheme != 'https' or parsed.hostname != 'band-archive.fly.dev'):
        raise MigrationError('api_url_not_allowed')
    if not parsed.scheme or not parsed.netloc:
        raise MigrationError('api_url_invalid')
    return f'{api_url.rstrip("/")}/internal/migrations/r2-720p/inventory'


def fetch_targets(api_url=DEFAULT_API_URL, timeout=20, request_get=None, migration_token=None):
    """Return authenticated inventory video targets without URL-like fields."""
    request_get = request_get or _default_get
    token = migration_token if migration_token is not None else os.getenv('R2_MIGRATION_TOKEN')
    if not token:
        raise MigrationError('migration_token_missing')
    url = _inventory_url(api_url, allow_test_url=request_get is not _default_get)
    try:
        response = request_get(url=url, timeout=timeout, headers={
            'Accept': 'application/json', 'X-Migration-Token': token,
        })
        if hasattr(response, 'raise_for_status'):
            response.raise_for_status()
        elif getattr(response, 'status_code', 500) >= 400:
            raise MigrationError('api_http_error')
        payload = response.json()
    except MigrationError:
        raise
    except Exception as exc:
        # Request exception text can include a URL/query, so do not expose it.
        raise MigrationError('api_request_failed') from exc
    if not isinstance(payload, list):
        raise MigrationError('api_schema_invalid')

    targets, seen_media, seen_keys = [], set(), set()
    for media in payload:
        if not isinstance(media, dict):
            raise MigrationError('api_schema_invalid')
        if media.get('file_type') != 'video':
            continue
        media_id = media.get('id')
        if not isinstance(media_id, int) or isinstance(media_id, bool) or media_id < 1:
            raise MigrationError('api_invalid_media_id')
        filename = _safe_filename(media.get('storage_filename'))
        key = source_key_for_filename(filename)
        if media_id in seen_media:
            raise MigrationError('api_duplicate_media_id')
        if key in seen_keys:
            raise MigrationError('api_duplicate_source_key')
        seen_media.add(media_id)
        seen_keys.add(key)
        targets.append({'media_id': media_id, 'source_key': key, 'filename': filename})
    return targets


def ffprobe_command(target):
    return ['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(target)]


def probe(target):
    try:
        output = subprocess.run(ffprobe_command(target), check=True, text=True, capture_output=True).stdout
        return json.loads(output)
    except Exception as exc:
        raise MigrationError('ffprobe_failed') from exc


def stream(data, kind):
    return next((item for item in data.get('streams', []) if item.get('codec_type') == kind), None)


def dimensions(video):
    width, height = int(video.get('width') or 0), int(video.get('height') or 0)
    tags, side_data = video.get('tags') or {}, video.get('side_data_list') or []
    rotation = next((item.get('rotation') for item in side_data if 'rotation' in item), tags.get('rotate', 0))
    try:
        rotation = int(rotation) % 360
    except (TypeError, ValueError):
        rotation = 0
    return (height, width) if rotation in (90, 270) else (width, height)


def duration(data):
    try:
        return float((data.get('format') or {}).get('duration'))
    except (TypeError, ValueError):
        return None


def validate(source, output):
    source_video, output_video = stream(source, 'video'), stream(output, 'video')
    if not source_video or not output_video:
        raise MigrationError('video_stream_missing')
    width, height = dimensions(output_video)
    source_width, source_height = dimensions(source_video)
    if output_video.get('codec_name') != 'h264':
        raise MigrationError('output_video_not_h264')
    if not width or not height or width > 1280 or height > 720 or width % 2 or height % 2:
        raise MigrationError('invalid_output_dimensions')
    if width > source_width or height > source_height or abs(width / height - source_width / source_height) > .03:
        raise MigrationError('output_geometry_invalid')
    if stream(source, 'audio') and (not stream(output, 'audio') or stream(output, 'audio').get('codec_name') != 'aac'):
        raise MigrationError('output_audio_not_aac')
    source_duration, output_duration = duration(source), duration(output)
    if source_duration is not None and output_duration is not None and abs(source_duration - output_duration) > max(.5, source_duration * .02):
        raise MigrationError('output_duration_mismatch')


def ffmpeg_command(source, output, container):
    scale = "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
    return ['ffmpeg', '-hide_banner', '-y', '-i', str(source), '-map', '0:v:0', '-map', '0:a?', '-vf', scale,
            '-c:v', 'libx264', '-crf', '27', '-preset', 'medium', '-maxrate', '2500k', '-bufsize', '5000k',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', '-f', container, str(output)]


def transcode(source, output, container):
    try:
        subprocess.run(ffmpeg_command(source, output, container), check=True, capture_output=True)
    except Exception as exc:
        raise MigrationError('ffmpeg_failed') from exc
    if not Path(output).is_file() or not Path(output).stat().st_size:
        raise MigrationError('empty_transcode_output')


class R2:
    """Minimal direct S3v4 client; credentials are read but never emitted."""

    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    @classmethod
    def configured(cls, env=None, client_factory=None, config_factory=None):
        env = os.environ if env is None else env
        required = ('S3_ENDPOINT_URL', 'S3_BUCKET_NAME', 'S3_ACCESS_KEY', 'S3_SECRET_KEY')
        if any(not env.get(name) for name in required):
            raise MigrationError('r2_config_missing')
        try:
            if client_factory is None:
                import boto3
                client_factory = boto3.client
            if config_factory is None:
                from botocore.config import Config
                config_factory = Config
            client = client_factory('s3', endpoint_url=env['S3_ENDPOINT_URL'],
                                    aws_access_key_id=env['S3_ACCESS_KEY'],
                                    aws_secret_access_key=env['S3_SECRET_KEY'],
                                    config=config_factory(signature_version='s3v4'))
        except ImportError as exc:
            raise MigrationError('boto3_unavailable') from exc
        except Exception as exc:
            raise MigrationError('r2_client_init_failed') from exc
        return cls(client, env['S3_BUCKET_NAME'])

    def head(self, key):
        return self.client.head_object(Bucket=self.bucket, Key=key)

    def download(self, key, path):
        with Path(path).open('wb') as handle:
            self.client.download_fileobj(self.bucket, key, handle)

    def upload_new(self, key, path, content_type, values):
        # Conditional creation prevents a retry/parallel runner from ever
        # overwriting an immutable derivative key.
        try:
            with Path(path).open('rb') as handle:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=handle,
                                       ContentType=content_type, Metadata=values, IfNoneMatch='*')
        except Exception as exc:
            text = str(exc).lower()
            if 'precondition' in text or '412' in text or 'already exists' in text:
                raise DestinationExists('destination_key_exists') from exc
            raise MigrationError('r2_upload_failed') from exc

    def put_manifest(self, key, value):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=value, ContentType='application/json')

    def get_manifest(self, key):
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key)['Body']
            return body.read()
        except Exception as exc:
            raise MigrationError('remote_manifest_unreadable') from exc

    def stream_hash(self, key):
        body = self.client.get_object(Bucket=self.bucket, Key=key)['Body']
        hasher, total = hashlib.sha256(), 0
        for block in iter(lambda: body.read(CHUNK), b''):
            hasher.update(block)
            total += len(block)
        return hasher.hexdigest(), total

    def signed_get(self, key):
        return self.client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket, 'Key': key}, ExpiresIn=300,
        )


def default_manifest():
    return Path(__file__).parent / '.state' / 'r2-720p-manifest.json'


def default_remote_manifest(run_id):
    return f'{PREFIX}/{run_id}/manifest.json'


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding='utf-8')
    os.replace(temporary, path)


def load_bytes(value):
    try:
        data = json.loads(value.decode('utf-8') if isinstance(value, bytes) else value)
    except Exception as exc:
        raise MigrationError('manifest_unreadable') from exc
    if data.get('schema') != MANIFEST_SCHEMA or data.get('profile') != PROFILE or not isinstance(data.get('items'), list):
        raise MigrationError('manifest_schema_invalid')
    return data


def load(path):
    try:
        return load_bytes(Path(path).read_text(encoding='utf-8'))
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError('manifest_unreadable') from exc


class Runner:
    def __init__(self, r2, path, apply=False, continue_on_error=False, remote_manifest_key=None, remote_probe=False,
                 min_scratch_bytes=MIN_SCRATCH_BYTES):
        self.r2, self.path, self.apply = r2, Path(path), apply
        self.continue_on_error = continue_on_error
        self.remote_manifest_key = remote_manifest_key
        self.remote_probe = remote_probe
        self.min_scratch_bytes = min_scratch_bytes
        self.data = None

    def persist(self, remote=False):
        self.data['updated_at'] = now()
        save(self.path, self.data)
        if remote:
            self.r2.put_manifest(self.data['remote_manifest_key'], json.dumps(self.data, sort_keys=True).encode('utf-8'))

    def plan(self, targets, run_id=None):
        run_id = run_id or f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}'
        remote_key = self.remote_manifest_key or default_remote_manifest(run_id)
        self.data = {'schema': MANIFEST_SCHEMA, 'profile': PROFILE, 'run_id': run_id,
                     'remote_manifest_key': remote_key, 'items': []}
        for target in targets:
            item = {'media_id': target['media_id'], 'source_key': target['source_key'], 'filename': target['filename'],
                    'state': 'planned', 'source': {}, 'output': {}}
            try:
                head = self.r2.head(item['source_key'])
                etag, size = get_etag(head), int(head.get('ContentLength', -1))
                item['source'] = {'etag': etag, 'size': size, 'content_type': head.get('ContentType') or 'application/octet-stream'}
                item['output']['key'] = destination_key(item['media_id'], etag, item['filename'])
                if not etag or size < 1:
                    item.update(state='blocked', reason='source_metadata_missing')
            except Exception as exc:
                item.update(state='blocked', reason=safe_error(exc))
            self.data['items'].append(item)
        self.persist()
        return self.data

    def resume(self):
        if not self.remote_manifest_key:
            raise MigrationError('remote_manifest_key_required')
        self.data = load_bytes(self.r2.get_manifest(self.remote_manifest_key))
        if self.data.get('remote_manifest_key') != self.remote_manifest_key:
            raise MigrationError('remote_manifest_key_mismatch')
        changed = False
        for item in self.data['items']:
            if item.get('state') in {'upload_started', 'source_downloaded', 'interrupted', 'failed'}:
                self._recover_upload_started(item)
                changed = True
            elif item.get('state') == 'copy_started':
                # v2 never writes to source_key; refuse legacy in-place work.
                item.update(state='blocked', reason='legacy_in_place_state_refused')
                changed = True
        if changed:
            self.persist(remote=True)
        else:
            save(self.path, self.data)
        return self.data

    def same_source(self, item, head):
        source = item['source']
        return get_etag(head) == source.get('etag') and int(head.get('ContentLength', -1)) == source.get('size')

    def _destination_matches(self, item):
        output = item.get('output') or {}
        if not output.get('key') or not output.get('sha256') or not output.get('size'):
            return False
        try:
            head = self.r2.head(output['key'])
            if int(head.get('ContentLength', -1)) != output['size']:
                return False
            if metadata(head, 'migration-profile') != PROFILE or metadata(head, 'source-etag') != item['source'].get('etag'):
                return False
            actual, size = self.r2.stream_hash(output['key'])
            return actual == output['sha256'] and size == output['size']
        except Exception:
            return False

    def _recover_upload_started(self, item):
        if self._destination_matches(item):
            item.update(state='completed', completed_at=now(), recovery='destination_verified_after_interruption')
            return
        if not item.get('source', {}).get('sha256'):
            item.update(state='blocked', reason='recovery_source_hash_missing')
            return
        try:
            head = self.r2.head(item['source_key'])
            actual, size = self.r2.stream_hash(item['source_key'])
            if self.same_source(item, head) and actual == item['source']['sha256'] and size == item['source']['size']:
                item.update(state='planned', reason='source_intact_after_interruption')
            else:
                item.update(state='blocked', reason='recovery_state_ambiguous')
        except Exception as exc:
            item.update(state='blocked', reason=safe_error(exc))

    def verify_output(self, item):
        output = item['output']
        head = self.r2.head(output['key'])
        if int(head.get('ContentLength', -1)) != output['size']:
            raise MigrationError('r2_head_verification_failed')
        if (metadata(head, 'migration-profile') != PROFILE or
                metadata(head, 'source-etag') != item['source']['etag'] or
                metadata(head, 'sha256') != output['sha256']):
            raise MigrationError('r2_metadata_verification_failed')
        if (head.get('ContentType') or '') != output['content_type']:
            raise MigrationError('content_type_not_preserved')
        actual, size = self.r2.stream_hash(output['key'])
        if actual != output['sha256'] or size != output['size']:
            raise MigrationError('r2_sha256_mismatch')

    def ensure_scratch(self):
        if self.min_scratch_bytes and shutil.disk_usage(tempfile.gettempdir()).free < self.min_scratch_bytes:
            raise Blocked('scratch_space_insufficient')

    def apply_item(self, item):
        container = CONTAINERS.get(Path(item['filename']).suffix.lower())
        if not container:
            raise Blocked('unsupported_video_container')
        source_head = self.r2.head(item['source_key'])
        if not self.same_source(item, source_head):
            raise Blocked('source_changed_before_download')
        self.ensure_scratch()
        with tempfile.TemporaryDirectory(prefix='anything-r2-720p-') as workdir:
            workdir = Path(workdir)
            source = workdir / ('source' + Path(item['filename']).suffix.lower())
            output = workdir / ('output' + Path(item['filename']).suffix.lower())
            self.r2.download(item['source_key'], source)
            item['source']['sha256'] = digest(source)
            # Persist before transcode/new-object write so scratch loss can
            # prove original integrity without touching that original object.
            item.update(state='source_downloaded')
            self.persist(remote=True)
            if source.stat().st_size != item['source']['size'] or not self.same_source(item, self.r2.head(item['source_key'])):
                raise Blocked('source_changed_during_download')
            source_probe = probe(source)
            transcode(source, output, container)
            output_probe = probe(output)
            validate(source_probe, output_probe)
            output_sha, output_size = digest(output), output.stat().st_size
            if output_size >= item['source']['size']:
                raise Blocked('output_not_smaller')
            content_type = source_head.get('ContentType') or mimetypes.guess_type(item['filename'])[0] or 'video/mp4'
            item['output'].update({'sha256': output_sha, 'size': output_size, 'content_type': content_type,
                                   'profile': PROFILE})
            try:
                self.r2.head(item['output']['key'])
            except Exception:
                pass
            else:
                if self._destination_matches(item):
                    item.update(state='completed', completed_at=now(), recovery='destination_already_verified')
                    self.persist(remote=True)
                    return
                raise Blocked('destination_key_collision')
            if not self.same_source(item, self.r2.head(item['source_key'])):
                raise Blocked('source_changed_before_upload')
            item.update(state='upload_started')
            self.persist(remote=True)
            values = {'migration-profile': PROFILE, 'source-etag': item['source']['etag'],
                      'source-sha256': item['source']['sha256'], 'sha256': output_sha}
            try:
                self.r2.upload_new(item['output']['key'], output, content_type, values)
            except DestinationExists:
                if self._destination_matches(item):
                    item.update(state='completed', completed_at=now(), recovery='conditional_create_race_verified')
                    self.persist(remote=True)
                    return
                raise Blocked('destination_key_collision')
            self.verify_output(item)
            if self.remote_probe:
                # Presigned URL is ephemeral: never store or print it.
                validate(source_probe, probe(self.r2.signed_get(item['output']['key'])))
            item.update(state='completed', completed_at=now())
            self.persist(remote=True)
            event(item, 'completed')

    def run(self, canary=None):
        if not self.apply:
            for item in self.data['items']:
                event(item, item['state'], item.get('reason', ''))
            return int(any(item['state'] == 'blocked' for item in self.data['items']))
        self.persist(remote=True)
        failed = any(item['state'] == 'blocked' for item in self.data['items'])
        # Plan always has every target; canary only limits this invocation.
        candidates = [item for item in self.data['items'] if item['state'] == 'planned'][:canary]
        for item in candidates:
            try:
                self.apply_item(item)
            except KeyboardInterrupt:
                item.update(state='interrupted', reason='keyboard_interrupt')
                self.persist(remote=True)
                return 130
            except Blocked as exc:
                item.update(state='blocked', reason=safe_error(exc))
                self.persist(remote=True)
                event(item, 'blocked', item['reason'])
                failed = True
                if not self.continue_on_error:
                    break
            except Exception as exc:
                item.update(state='failed', reason=safe_error(exc))
                self.persist(remote=True)
                event(item, 'failed', item['reason'])
                failed = True
                if not self.continue_on_error:
                    break
        nonterminal = {'planned', 'source_downloaded', 'upload_started', 'interrupted'}
        return int(failed or any(item['state'] in {'blocked', 'failed'} | nonterminal for item in self.data['items']))


def parse(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-url', default=DEFAULT_API_URL)
    parser.add_argument('--api-timeout', type=int, default=20)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--canary', type=int)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--run-id')
    parser.add_argument('--remote-manifest-key')
    parser.add_argument('--manifest', type=Path, default=default_manifest())
    parser.add_argument('--continue-on-error', action='store_true')
    parser.add_argument('--remote-probe', action='store_true')
    args = parser.parse_args(argv)
    if args.canary is not None and args.canary < 1:
        parser.error('--canary must be positive')
    if args.api_timeout < 1:
        parser.error('--api-timeout must be positive')
    if args.resume and not (args.remote_manifest_key or args.run_id):
        parser.error('--resume requires --remote-manifest-key or --run-id')
    if args.resume and not args.remote_manifest_key:
        args.remote_manifest_key = default_remote_manifest(args.run_id)
    return args


def main(argv=None):
    args = parse(argv)
    r2 = R2.configured()
    runner = Runner(r2, args.manifest, apply=args.apply, continue_on_error=args.continue_on_error,
                    remote_manifest_key=args.remote_manifest_key, remote_probe=args.remote_probe)
    if args.resume:
        runner.resume()
    else:
        # Do not slice here: one-off rootfs loss must not hide candidates.
        runner.plan(fetch_targets(args.api_url, args.api_timeout), run_id=args.run_id)
    return runner.run(args.canary)


if __name__ == '__main__':
    raise SystemExit(main())
