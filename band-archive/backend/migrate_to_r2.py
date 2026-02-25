"""
기존 Fly.io Volume 파일들을 Cloudflare R2로 마이그레이션하는 일회성 스크립트.

사용법:
    # Fly.io SSH 접속 후 실행
    fly ssh console
    cd /app
    python migrate_to_r2.py          # 마이그레이션 실행
    python migrate_to_r2.py --verify # 검증만 실행
"""

import os
import sys
import mimetypes

from app import create_app
from models import Media, PracticeLog, PersonalLog
from storage import storage


def guess_content_type(filename):
    ct, _ = mimetypes.guess_type(filename)
    if filename.lower().endswith('.m4a'):
        return 'audio/mp4'
    return ct or 'application/octet-stream'


def migrate(app):
    upload_folder = os.getenv('UPLOAD_FOLDER', '/data/uploads')
    personal_logs_folder = os.path.join(upload_folder, 'personal_logs')

    success = 0
    skipped = 0
    failed = 0
    missing = 0

    with app.app_context():
        # 1. Media 파일 (곡 미디어 + 악보)
        print("=== Media 파일 마이그레이션 ===")
        for media in Media.query.all():
            key = f'media/{media.filename}'
            local_path = os.path.join(upload_folder, media.filename)

            if not os.path.exists(local_path):
                print(f"  [MISSING] {local_path}")
                missing += 1
                continue

            if storage.exists(key):
                print(f"  [SKIP] {key} (이미 존재)")
                skipped += 1
                continue

            try:
                ct = guess_content_type(media.filename)
                with open(local_path, 'rb') as f:
                    storage.upload(key, f, content_type=ct)
                print(f"  [OK] {key} ({os.path.getsize(local_path)} bytes)")
                success += 1
            except Exception as e:
                print(f"  [FAIL] {key}: {e}")
                failed += 1

        # 2. PracticeLog 녹음 파일
        print("\n=== PracticeLog 녹음 마이그레이션 ===")
        for log in PracticeLog.query.filter(PracticeLog.recording.isnot(None)).all():
            key = f'recordings/{log.recording}'
            local_path = os.path.join(upload_folder, log.recording)

            if not os.path.exists(local_path):
                print(f"  [MISSING] {local_path}")
                missing += 1
                continue

            if storage.exists(key):
                print(f"  [SKIP] {key} (이미 존재)")
                skipped += 1
                continue

            try:
                ct = guess_content_type(log.recording)
                with open(local_path, 'rb') as f:
                    storage.upload(key, f, content_type=ct)
                print(f"  [OK] {key} ({os.path.getsize(local_path)} bytes)")
                success += 1
            except Exception as e:
                print(f"  [FAIL] {key}: {e}")
                failed += 1

        # 3. PersonalLog 파일
        print("\n=== PersonalLog 파일 마이그레이션 ===")
        for log in PersonalLog.query.all():
            key = f'personal_logs/{log.filename}'
            local_path = os.path.join(personal_logs_folder, log.filename)

            if not os.path.exists(local_path):
                print(f"  [MISSING] {local_path}")
                missing += 1
                continue

            if storage.exists(key):
                print(f"  [SKIP] {key} (이미 존재)")
                skipped += 1
                continue

            try:
                ct = guess_content_type(log.filename)
                with open(local_path, 'rb') as f:
                    storage.upload(key, f, content_type=ct)
                print(f"  [OK] {key} ({os.path.getsize(local_path)} bytes)")
                success += 1
            except Exception as e:
                print(f"  [FAIL] {key}: {e}")
                failed += 1

    print(f"\n=== 결과 ===")
    print(f"  성공: {success}")
    print(f"  스킵 (이미 존재): {skipped}")
    print(f"  실패: {failed}")
    print(f"  로컬 파일 없음: {missing}")

    return failed == 0


def verify(app):
    """R2에 모든 파일이 존재하는지 검증"""
    ok = 0
    missing = 0

    with app.app_context():
        print("=== 검증: Media ===")
        for media in Media.query.all():
            key = f'media/{media.filename}'
            if storage.exists(key):
                ok += 1
            else:
                print(f"  [MISSING] {key}")
                missing += 1

        print("=== 검증: PracticeLog 녹음 ===")
        for log in PracticeLog.query.filter(PracticeLog.recording.isnot(None)).all():
            key = f'recordings/{log.recording}'
            if storage.exists(key):
                ok += 1
            else:
                print(f"  [MISSING] {key}")
                missing += 1

        print("=== 검증: PersonalLog ===")
        for log in PersonalLog.query.all():
            key = f'personal_logs/{log.filename}'
            if storage.exists(key):
                ok += 1
            else:
                print(f"  [MISSING] {key}")
                missing += 1

    print(f"\n=== 검증 결과 ===")
    print(f"  존재: {ok}")
    print(f"  누락: {missing}")
    return missing == 0


if __name__ == '__main__':
    app = create_app()

    if '--verify' in sys.argv:
        result = verify(app)
    else:
        result = migrate(app)

    sys.exit(0 if result else 1)
