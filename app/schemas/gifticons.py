"""C-9 — 기프티콘 교환 스키마."""

from pydantic import BaseModel


class GifticonExchangeResponse(BaseModel):
    order_id: int
    points_used: int
    issued_code: str  # 시연 범위 — 더미 코드(C-9.1.5)
    balance: int
