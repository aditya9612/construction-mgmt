from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PaginationParams:
    limit: int = 20
    offset: int = 0
    search: Optional[str] = None

    def normalized(self) -> "PaginationParams":
        limit = max(1, min(self.limit, 100))
        offset = max(0, self.offset)
        return PaginationParams(limit=limit, offset=offset, search=self.search)

