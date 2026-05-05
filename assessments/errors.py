# assessments/errors.py
from rest_framework.response import Response


class CBTError:
    """Stable, machine-readable error codes the frontend branches on."""
    ALREADY_SUBMITTED  = "ATTEMPT_ALREADY_SUBMITTED"
    DUPLICATE_REPLAY   = "DUPLICATE_REPLAY"
    TIME_ELAPSED       = "TIME_ELAPSED"
    NOT_STARTED        = "ATTEMPT_NOT_STARTED"
    INVALID_PAYLOAD    = "INVALID_PAYLOAD"
    NO_STUDENT_PROFILE = "NO_STUDENT_PROFILE"
    AUTH_REQUIRED      = "AUTH_REQUIRED"
    TEST_NOT_FOUND     = "TEST_NOT_FOUND"
    ATTEMPT_NOT_FOUND  = "ATTEMPT_NOT_FOUND"
    SERVER_ERROR       = "SERVER_ERROR"
    NO_ACTIVE_ATTEMPT  = "NO_ACTIVE_ATTEMPT"


def err(code: str, detail: str, *, status_code: int = 400, **extra):
    body = {"code": code, "detail": detail, **extra}
    return Response(body, status=status_code)