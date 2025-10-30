# app/schemas.py
from datetime import date
from pydantic import BaseModel
from typing import Optional, List

class TickerOut(BaseModel):
    symbol: str
    class Config: from_attributes = True

class PriceBarOut(BaseModel):
    date: date; open: float; high: float; low: float; close: float
    adjusted_close: float; volume: int
    class Config: from_attributes = True

class OptionFlowTop5Row(BaseModel):
    contract: str; expiry: str; type: str; strike: str
    volume: int; open_interest: Optional[int]; iv: Optional[str]; notional: Optional[str]

class OptionFlowTop5Response(BaseModel):
    symbol: str; period: str; rows: List[OptionFlowTop5Row]

class OptionFlowUnusualRow(BaseModel):
    contract: str; expiry: str; type: str; strike: str
    volume_today: int; avg_volume_20d: Optional[float]; vol_ratio: Optional[float]
    notional: Optional[str]; iv: Optional[str]; note: Optional[str]

class OptionFlowUnusualResponse(BaseModel):
    symbol: str; rows: List[OptionFlowUnusualRow]

class ProfitTargetResponse(BaseModel):
    symbol: str; contracts: int; option_type: str; strike: float
    premium_paid: float; target_profit: float; underlying_price_now: float
    days_to_expiry: int; assumed_delta: float
    needed_move_in_stock_dollars: float; est_target_stock_price: float
    est_pct_move_in_underlying: float; breakeven_at_expiry: float; disclaimer: str

