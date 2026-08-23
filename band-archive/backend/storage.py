import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError


class StorageClient:
    """S3 호환 오브젝트 스토리지 클라이언트 (R2, B2 등)"""

    def __init__(self, app=None):
        self._client = None
        self._bucket = None
        if app:
            self.init_app(app)

    def init_app(self, app):
        self._bucket = app.config['S3_BUCKET_NAME']
        self._presign_expires = app.config.get('S3_PRESIGN_EXPIRES', 3600)
        # Tests replace the public methods with a local fake.  Do not let boto3
        # discover credentials or contact a metadata service while building the app.
        if app.testing:
            self._client = None
            return
        self._client = boto3.client(
            's3',
            endpoint_url=app.config['S3_ENDPOINT_URL'],
            aws_access_key_id=app.config['S3_ACCESS_KEY'],
            aws_secret_access_key=app.config['S3_SECRET_KEY'],
            config=BotoConfig(signature_version='s3v4'),
        )

    def upload(self, key, file_obj, content_type=None):
        """파일 업로드. key = S3 object key (e.g. 'media/abc123.mp3')"""
        extra = {}
        if content_type:
            extra['ContentType'] = content_type
        self._client.upload_fileobj(file_obj, self._bucket, key, ExtraArgs=extra)

    def download(self, key, file_obj):
        """파일 다운로드"""
        self._client.download_fileobj(self._bucket, key, file_obj)

    def delete(self, key):
        """파일 삭제"""
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def generate_url(self, key, expires_in=None):
        """presigned URL 생성 (기본: config S3_PRESIGN_EXPIRES)"""
        return self._client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self._bucket, 'Key': key},
            ExpiresIn=expires_in or self._presign_expires,
        )

    def generate_upload_url(self, key, content_type=None, expires_in=600):
        """presigned PUT URL 생성 (클라이언트 직접 업로드용)"""
        params = {'Bucket': self._bucket, 'Key': key}
        if content_type:
            params['ContentType'] = content_type
        return self._client.generate_presigned_url(
            'put_object',
            Params=params,
            ExpiresIn=expires_in,
        )

    def head(self, key):
        """Return object metadata, including ContentLength, or raise provider errors."""
        return self._client.head_object(Bucket=self._bucket, Key=key)

    def create_multipart_upload(self, key, content_type):
        response = self._client.create_multipart_upload(
            Bucket=self._bucket, Key=key, ContentType=content_type,
        )
        return response['UploadId']

    def generate_upload_part_url(self, key, upload_id, part_number, expires_in=3600):
        return self._client.generate_presigned_url(
            'upload_part',
            Params={
                'Bucket': self._bucket, 'Key': key, 'UploadId': upload_id,
                'PartNumber': part_number,
            },
            ExpiresIn=expires_in,
        )

    def complete_multipart_upload(self, key, upload_id, parts):
        return self._client.complete_multipart_upload(
            Bucket=self._bucket, Key=key, UploadId=upload_id,
            MultipartUpload={'Parts': parts},
        )

    def abort_multipart_upload(self, key, upload_id):
        self._client.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)

    def exists(self, key):
        """오브젝트 존재 여부 확인"""
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False

    def copy(self, src_key, dst_key):
        """오브젝트 복사 (rename 대체)"""
        import urllib.parse
        # Boto3 copy_object's CopySource requires URL-encoded key if it contains non-ASCII characters
        encoded_src_key = urllib.parse.quote(src_key)
        self._client.copy_object(
            Bucket=self._bucket,
            CopySource={'Bucket': self._bucket, 'Key': encoded_src_key},
            Key=dst_key,
        )


storage = StorageClient()
