"""Custom exception classes for the API."""

from fastapi import HTTPException


class UnauthorizedException(HTTPException):
    """Raised when JWT is missing, invalid, or expired. Maps to 401."""

    def __init__(self, detail: str = "Invalid or expired token") -> None:
        super().__init__(status_code=401, detail=detail)


class ForbiddenException(HTTPException):
    """Raised when user is not allowed (e.g. unapproved or rejected). Maps to 403."""

    def __init__(
        self,
        detail: str = "Forbidden",
        reason: str | None = None,
    ) -> None:
        body: str | dict[str, str] = detail
        if reason is not None:
            body = {"detail": detail, "reason": reason}
        super().__init__(status_code=403, detail=body)


class BadRequestException(HTTPException):
    """Raised on validation failures. Maps to 400."""

    def __init__(self, detail: str = "Bad request") -> None:
        super().__init__(status_code=400, detail=detail)


class NotFoundException(HTTPException):
    """Raised when a resource is not found. Maps to 404."""

    def __init__(self, detail: str = "Not found") -> None:
        super().__init__(status_code=404, detail=detail)


class ConflictException(HTTPException):
    """Raised on duplicate resource conflicts. Maps to 409."""

    def __init__(self, detail: str = "Conflict") -> None:
        super().__init__(status_code=409, detail=detail)
