"""
기존 영상 파일들을 다중 화질 및 라디오 모드 지원을 위해 트랜스코딩하는 마이그레이션 스크립트.

사용법:
    # 모든 영상(file_type='video') 중 트랜스코딩되지 않은 항목 처리
    python migrate_video_qualities.py
    
    # 이미 완료된 항목도 강제로 다시 트랜스코딩
    python migrate_video_qualities.py --force
"""

import sys
import os
import logging

# Ensure the backend directory is in the path if running from this directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from extensions import db
from models import Media
from transcoder import _transcode_video

def migrate_videos(app, force=False):
    # Set up logging to both console and a file
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('migration.log')
        ]
    )
    logger = logging.getLogger(__name__)
    
    with app.app_context():
        query = Media.query.filter_by(file_type='video')
        if not force:
            # 'completed'가 아닌 항목들만 조회 (NULL이거나 'pending', 'failed' 등)
            query = query.filter(Media.transcoding_status != 'completed')
        
        videos = query.all()
        total = len(videos)
        logger.info(f"트랜스코딩 작업을 시작합니다. 대상 영상 수: {total}")
        
        success_count = 0
        fail_count = 0
        
        for i, media in enumerate(videos, 1):
            logger.info(f"[{i}/{total}] 처리 중: {media.filename} (ID: {media.id}, 현재 상태: {media.transcoding_status})")
            try:
                # 상태 업데이트
                media.transcoding_status = 'processing'
                db.session.commit()
                
                # 트랜스코딩 수행 (FFmpeg 사용)
                _transcode_video(media.filename)
                
                # 성공 시 상태 업데이트
                media.transcoding_status = 'completed'
                db.session.commit()
                logger.info(f"  => [성공] {media.filename} 트랜스코딩 완료")
                success_count += 1
            except Exception as e:
                # 실패 시 롤백 및 상태 업데이트
                db.session.rollback()
                media.transcoding_status = 'failed'
                db.session.commit()
                logger.error(f"  => [실패] {media.filename} 처리 중 오류 발생: {e}")
                fail_count += 1
        
        logger.info("-" * 40)
        logger.info(f"마이그레이션 완료!")
        logger.info(f"전체: {total}, 성공: {success_count}, 실패: {fail_count}")
        logger.info("-" * 40)

if __name__ == '__main__':
    # Flask 앱 생성 (환경 변수에 따른 Config 자동 선택)
    app = create_app()
    
    force_retranscode = '--force' in sys.argv
    if force_retranscode:
        confirm = input("이미 완료된 영상들을 포함하여 모든 영상을 다시 트랜스코딩하시겠습니까? (y/n): ")
        if confirm.lower() != 'y':
            print("작업을 취소합니다.")
            sys.exit(0)
            
    migrate_videos(app, force=force_retranscode)
