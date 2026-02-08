from flask import jsonify


class ValidationError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFoundError(Exception):
    def __init__(self, message="Song not found", status_code=404):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def register_error_handlers(app):
    @app.errorhandler(ValidationError)
    def handle_validation_error(e):
        return jsonify({"error": e.message}), e.status_code

    @app.errorhandler(NotFoundError)
    def handle_not_found_error(e):
        return jsonify({"error": e.message}), e.status_code
