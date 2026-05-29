import threading
import subprocess
import os
import tempfile
import logging
from flask import current_app
from extensions import db
from models import Media

def transcode_video_async(app, filename):
    def job():
        with app.app_context():
            try:
                # Update status to processing
                media = Media.query.filter_by(filename=filename).first()
                if media:
                    media.transcoding_status = 'processing'
                    db.session.commit()
                
                _transcode_video(filename)
                
                # Update status to completed
                if media:
                    media.transcoding_status = 'completed'
                    db.session.commit()
                logging.info(f"Transcoding completed for {filename}")
            except Exception as e:
                logging.error(f"Transcoding failed for {filename}: {e}")
                # Update status to failed
                try:
                    media = Media.query.filter_by(filename=filename).first()
                    if media:
                        media.transcoding_status = 'failed'
                        db.session.commit()
                except Exception as db_e:
                    logging.error(f"Failed to update transcoding status to failed: {db_e}")
    
    thread = threading.Thread(target=job)
    thread.start()

def _transcode_video(filename):
    from storage import storage
    key = f'media/{filename}'
    base_name = filename.rsplit('.', 1)[0]
    
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, filename)
        
        # Download
        logging.info(f"Downloading {filename} for transcoding...")
        with open(input_path, 'wb') as f:
            storage.download(key, f)
            
        # Output paths
        out_720p = os.path.join(tmpdir, f"{base_name}_720p.mp4")
        out_480p = os.path.join(tmpdir, f"{base_name}_480p.mp4")
        out_audio = os.path.join(tmpdir, f"{base_name}_audio.m4a")
        
        # ffmpeg commands with better logging
        def run_ffmpeg(args):
            process = subprocess.run(args, capture_output=True, text=True)
            if process.returncode != 0:
                logging.error(f"ffmpeg error: {process.stderr}")
                raise Exception(f"ffmpeg failed with return code {process.returncode}")

        # Transcode 720p
        logging.info(f"Transcoding {filename} to 720p...")
        run_ffmpeg(['ffmpeg', '-y', '-i', input_path, '-vf', 'scale=-2:720', '-c:v', 'libx264', '-preset', 'fast', '-crf', '28', '-c:a', 'aac', out_720p])
        
        # Transcode 480p
        logging.info(f"Transcoding {filename} to 480p...")
        run_ffmpeg(['ffmpeg', '-y', '-i', input_path, '-vf', 'scale=-2:480', '-c:v', 'libx264', '-preset', 'fast', '-crf', '28', '-c:a', 'aac', out_480p])
        
        # Transcode Audio
        logging.info(f"Transcoding {filename} to audio only...")
        run_ffmpeg(['ffmpeg', '-y', '-i', input_path, '-vn', '-c:a', 'aac', '-b:a', '128k', out_audio])
        
        # Upload
        logging.info(f"Uploading transcoded files for {filename}...")
        with open(out_720p, 'rb') as f:
            storage.upload(f'media/{base_name}_720p.mp4', f, content_type='video/mp4')
        with open(out_480p, 'rb') as f:
            storage.upload(f'media/{base_name}_480p.mp4', f, content_type='video/mp4')
        with open(out_audio, 'rb') as f:
            storage.upload(f'media/{base_name}_audio.m4a', f, content_type='audio/mp4')
