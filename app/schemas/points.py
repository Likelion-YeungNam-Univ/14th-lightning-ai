"""C-8 — 포인트 잔액/충전 스키마."""

from pydantic import BaseModel


class PizzaProgress(BaseModel):
    held: int
    target: int
    percent: int


class PointBalanceResponse(BaseModel):
    balance: int
    pizza_progress: PizzaProgress


class PointChargeRequest(BaseModel):
    # 이슈 #83 — 실결제 없이 테스트하려면 둘 다 생략(amount만 전송). 하나만 보내면 400
    order_id: str | None = None
    payment_key: str | None = None
    amount: int  # 5,000 / 10,000 / 30,000 중 하나 (C-8.2)


class PointChargeResponse(BaseModel):
    balance: int
    charged: int
