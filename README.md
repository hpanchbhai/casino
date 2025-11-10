# Stock Tracker

Full-stack system for tracking, analyzing, and visualizing U.S. stock + options data with a Robinhood-style dark dashboard.

## Features

- Real-time stock price tracking
- Options flow analysis
- Dynamic categories (AI, Energy, Custom lists)
- Interactive dark-mode dashboard
- Daily insights (PCR, IV rank, etc.)
- Historical price charts
- Options activity tables (Today/1W/1M)

## Architecture

- **Core API** (port 8000): Categories, price bars, options data
- **Insights API** (port 8002): PCR, IV rank, options analytics
- **PostgreSQL**: Historical data + options chains
- **React Dashboard**: Robinhood-style dark UI
- **ETL Pipeline**: Price bars + options chain ingestion

## Prerequisites

- macOS with Homebrew
- Python 3.11+
- Node 18+ with pnpm/npm
- Docker Desktop
- Git

Quick install:
```bash
brew install python node jq git
brew install pnpm   # or: brew install npm
```

## Setup

1. **Clone & Enter Directory**
```bash
git clone https://github.com/hpanchbhai/stock-tracker.git
cd stock-tracker
```

2. **Python Environment**
```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install fastapi uvicorn[standard] sqlalchemy[asyncio] asyncpg pydantic yfinance pandas numpy python-dateutil httpx
```

3. **Node Dependencies**
```bash
cd dashboard
pnpm install    # or: npm install
cd ..
```

4. **Start Postgres (Docker)**
```bash
docker compose up -d stock-tracker-db
# Verify:
docker ps --filter "name=stock-tracker-db"
```

## Running the Services

1. **Core API** (port 8000)
```bash
# New terminal, activate venv first
source .venv/bin/activate
cd stock-tracker
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

2. **Insights API** (port 8002)
```bash
# New terminal, activate venv first
source .venv/bin/activate
cd stock-tracker
uvicorn app.insights_api:app --reload --host 127.0.0.1 --port 8002
```

3. **Dashboard** (port 5173)
```bash
cd dashboard
pnpm dev    # or: npm run dev
```

4. **Load Sample Data**
```bash
# From repo root with venv active:
PYTHONPATH=. python -m etl.fetch_prices --symbol AAPL --period 1y
PYTHONPATH=. python -m etl.snap_options --symbols "AAPL,NVDA,MSFT" --days 2
PYTHONPATH=. python -m etl.compute_option_insights
```

## Verify Setup

1. **Health Checks**
```bash
# Core API
curl -s http://127.0.0.1:8000/health | jq .
# Insights API
curl -s http://127.0.0.1:8002/health | jq .
```

2. **Sample Data**
```bash
# Categories
curl -s http://127.0.0.1:8000/categories | jq .

# AI Sector symbols
curl -s http://127.0.0.1:8000/category/aiSector/symbols | jq .

# AAPL price & insights
curl -s http://127.0.0.1:8000/pricebars/AAPL/latest | jq .
curl -s "http://127.0.0.1:8002/insights/AAPL/latest" | jq .
```

3. **Dashboard**
Open http://127.0.0.1:5173 in your browser

## Project Structure

```
stock-tracker/
├── app/                    # Core + Insights APIs
│   ├── main.py            # Core API (categories, prices)
│   ├── insights_api.py    # Insights API (PCR, IV rank)
│   └── database.py        # Async SQLAlchemy engine
├── etl/                    # Data ingestion
│   ├── fetch_prices.py    # Daily OHLCV
│   ├── snap_options.py    # Options chains
│   └── compute_option_insights.py
├── dashboard/             # React frontend
│   └── src/
│       ├── App.jsx       # Main dashboard
│       └── components/   # UI components
└── config/               # Categories & symbols
```

## Configuration

- Database credentials in `app/database.py`
- Categories in `config/categories.json`
- API endpoints in `dashboard/src/App.jsx`

## Development

- Branch naming: `feature/*`, `fix/*`
- Commit prefixes: `feat:`, `fix:`, `chore:`, `docs:`
- Version tags: `v0.1.0`, `v0.2.0`, etc.

## License

MIT

## Contributing

1. Fork the repo
2. Create feature branch
3. Commit changes
4. Push to branch
5. Open pull request