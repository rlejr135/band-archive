from errors import NotFoundError


def get_or_404(session, model, record_id, message=None):
    """Load a model by primary key while preserving each route's error text."""
    record = session.get(model, record_id)
    if not record:
        if message is None:
            raise NotFoundError()
        raise NotFoundError(message)
    return record
