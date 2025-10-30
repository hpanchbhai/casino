# app/models.py
from sqlalchemy import (
    Column, Integer, String, Float, Date, BigInteger, ForeignKey,
    UniqueConstraint, Text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Ticker(Base):
    __tablename__ = "tickers"
    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(16), unique=True, index=True, nullable=False)
    price_bars = relationship("PriceBar", back_populates="ticker", cascade="all, delete-orphan")
    option_flows = relationship("OptionFlow", back_populates="ticker", cascade="all, delete-orphan")

class PriceBar(Base):
    __tablename__ = "price_bars"
    ticker_id = Column(Integer, ForeignKey("tickers.id"), primary_key=True)
    date = Column(Date, primary_key=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    adjusted_close = Column(Float, nullable=False)
    volume = Column(BigInteger, nullable=False)
    ticker = relationship("Ticker", back_populates="price_bars")

class OptionFlow(Base):
    __tablename__ = "option_flow"
    id = Column(Integer, primary_key=True, index=True)
    ticker_id = Column(Integer, ForeignKey("tickers.id"), nullable=False)
    contract_type = Column(String(4), nullable=False)  # CALL or PUT
    strike = Column(Float, nullable=False)
    expiry = Column(Date, nullable=False)
    trade_date = Column(Date, nullable=False, index=True)
    volume_today = Column(Integer, nullable=False)
    avg_volume_20d = Column(Float, nullable=True)
    volume_ratio = Column(Float, nullable=True)
    open_interest = Column(Integer, nullable=True)
    notional_usd = Column(Float, nullable=True)
    iv = Column(Float, nullable=True)
    note = Column(Text, nullable=True)
    ticker = relationship("Ticker", back_populates="option_flows")
    __table_args__ = (
        UniqueConstraint("ticker_id","contract_type","strike","expiry","trade_date",
                         name="uq_contract_day"),
    )

