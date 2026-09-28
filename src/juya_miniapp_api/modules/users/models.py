from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserSummary:
    public_id: str
    juya_number: str
    status: str
