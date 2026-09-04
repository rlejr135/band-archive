from io import BytesIO

from werkzeug.datastructures import FileStorage

from upload_helpers import prepare_upload


def test_prepare_upload_preserves_original_name_size_and_rewinds_stream():
    file = FileStorage(stream=BytesIO(b'hello'), filename='recording.m4a')

    upload = prepare_upload(file, 'stored.m4a', 'audio/mp4')

    assert upload.filename == 'stored.m4a'
    assert upload.original_filename == 'recording.m4a'
    assert upload.content_type == 'audio/mp4'
    assert upload.file_size == 5
    assert file.read() == b'hello'
