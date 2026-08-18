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
    order_id: str
    payment_key: str
    amount: int  # 5,000 / 10,000 / 30,000 중 하나 (C-8.2)


class PointChargeResponse(BaseModel):
    balance: int
    charged: int
