class MemberNotFoundError(Exception):
    def __init__(self, member_number: str) -> None:
        self.member_number = member_number
        super().__init__(f"Member not found: {member_number}")


class ScrapingError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class RateLimitError(Exception):
    pass


class ValidationError(Exception):
    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"{field}: {message}")
