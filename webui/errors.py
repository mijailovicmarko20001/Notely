"""Domain exceptions raised by service modules (decks.py, media.py, jobs.py,
review.py, playlist.py, config.py).

Route handlers mostly don't need to catch these -- main.py registers a
handler for NotelyError (and for HTTPException, so both paths agree) that
turns any of them into a consistent `{"error": "...", "detail": "..."}`
JSON body (A4). `detail` is kept alongside `error` only because
static/app.js's error-reporting reads `.detail` off the response; new code
should read `.error`.
"""


class NotelyError(Exception):
    """Base for typed errors a service layer raises instead of a bare
    ValueError/Exception, so the API layer doesn't need a try/except per
    route to get a sane status code."""

    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class ValidationError(NotelyError):
    """Malformed or unsupported input that isn't caught by Pydantic (e.g. a
    filename with the wrong extension, an unparseable deck)."""

    status_code = 422


class NotFoundError(NotelyError):
    status_code = 404


class ConflictError(NotelyError):
    status_code = 409


class TooLargeError(NotelyError):
    status_code = 413


class ServerError(NotelyError):
    """Something failed on our side (subprocess crash, missing binary) --
    message shown to the client should stay generic; specifics go to
    `log.error(...)` at the raise site (S6)."""

    status_code = 500
