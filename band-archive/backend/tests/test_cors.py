from app import create_app
from config import TestingConfig
from extensions import db


def test_production_cors_preflight_allows_upload_capability_header(monkeypatch, tmp_path):
    """Browser multipart resume requests must pass the real production branch."""
    origin = 'https://band-archive.pages.dev'
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', origin)

    class BrowserConfig(TestingConfig):
        DEBUG = False
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'cors.db'}"

    browser_app = create_app(BrowserConfig)
    try:
        response = browser_app.test_client().options(
            '/uploads/multipart/session-id',
            headers={
                'Origin': origin,
                'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'content-type,x-upload-capability',
            },
        )
        assert response.status_code == 200
        assert response.headers['Access-Control-Allow-Origin'] == origin
        allowed_headers = {header.strip().lower() for header in response.headers['Access-Control-Allow-Headers'].split(',')}
        assert {'content-type', 'x-upload-capability'} <= allowed_headers
        allowed_methods = {method.strip().upper() for method in response.headers['Access-Control-Allow-Methods'].split(',')}
        assert {'GET', 'POST', 'OPTIONS'} <= allowed_methods
    finally:
        with browser_app.app_context():
            db.session.remove()
            db.drop_all()
