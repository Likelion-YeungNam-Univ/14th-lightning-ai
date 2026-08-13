from pydantic import BaseModel, Field


class StockSearchItem(BaseModel):
    stock_code: str
    name: str
    market: str  # domestic | overseas
    already_added: bool


class StockSearchResponse(BaseModel):
    items: list[StockSearchItem]
    # 빈 결과의 사유 구분(F-3.3.1): unsupported_overseas | None(일반 빈 결과)
    reason: str | None = None


class PopularStockItem(BaseModel):
    stock_code: str
    name: str
    market: str
    already_added: bool


class MyStockItem(BaseModel):
    stock_code: str
    name: str
    market: str
    display_order: int
    is_default: bool


class MyStocksResponse(BaseModel):
    items: list[MyStockItem]


class StockAddRequest(BaseModel):
    stock_codes: list[str] = Field(min_length=1)


class AddedStock(BaseModel):
    stock_code: str
    name: str
    market: str  # F-3.5.1 — 다른 구분에 들어간 경우 프론트 안내 근거


class StockAddResponse(BaseModel):
    added: list[AddedStock]
    already_registered: list[str]  # 멱등 처리로 무시된 코드


class StockDeleteResponse(BaseModel):
    remaining: int  # 삭제한 종목이 속한 구분의 남은 개수 (F-3.6)


class StockOrderRequest(BaseModel):
    market: str
    stock_codes: list[str] = Field(min_length=1)  # 해당 구분의 전체 목록을 정렬 순서대로


class StockOrderResponse(BaseModel):
    stocks: list[str]
