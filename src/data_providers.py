"""Market data providers behind a common interface.

All external market-data access goes through a DataProvider so the backing
service (Yahoo Finance today, Interactive Brokers later) can be swapped
without touching analysis or route code. Use get_provider()/set_provider()
to access or replace the active provider.
"""

import logging
import time
from datetime import date
from typing import Optional, Protocol

import pandas as pd
import yfinance as yf

from config import (
    IBKR_OPTIONS_COMMISSION_PER_CONTRACT,
    IBKR_OPTIONS_MIN_COMMISSION_PER_ORDER,
    OPTIONS_MAX_COMMISSION_DRAG_PCT,
    OPTIONS_MAX_DAYS_TO_EXPIRY,
    OPTIONS_MAX_STRIKE_DISCOUNT_PCT,
    OPTIONS_MIN_DAYS_TO_EXPIRY,
    OPTIONS_MIN_OPEN_INTEREST,
)

LOGGER = logging.getLogger(__name__)


def run_with_retries(operation, description, attempts=3, delay_seconds=1.0):
    """Run a network-bound operation with simple retry and backoff."""
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break

            LOGGER.warning(
                "%s failed on attempt %s/%s: %s. Retrying in %.1fs",
                description,
                attempt,
                attempts,
                error,
                delay_seconds,
            )
            time.sleep(delay_seconds)

    raise last_error


class DataProvider(Protocol):
    """Interface every market data source must implement."""

    name: str

    def get_price_history(self, symbol: str, days: int = 300) -> pd.DataFrame:
        """Daily OHLCV bars with columns Open/High/Low/Close/Volume."""
        ...

    def get_fundamentals(self, symbol: str) -> dict:
        """Fundamental metrics dict (valuation, profitability, growth, ...)."""
        ...

    def get_benchmark_metrics(self, symbol: str) -> dict:
        """The small metric set used for sector benchmark medians."""
        ...

    def lookup_symbol(self, symbol: str) -> Optional[dict]:
        """Validate a symbol; return {'symbol', 'name'} or None if unknown."""
        ...

    def get_earnings_calendar(self, symbol: str) -> dict:
        """Next earnings date and EPS/revenue estimates, if known."""
        ...

    def get_put_options(self, symbol: str, current_price: Optional[float] = None) -> list:
        """Near-the-money, near-term, liquid cash-secured put candidates."""
        ...


def _extract_fundamental_metrics(symbol, info):
    """Extract key fundamental metrics from Yahoo Finance info."""
    metrics = {}

    try:
        # Market Metrics
        metrics['market_cap'] = info.get('marketCap', None)
        metrics['enterprise_value'] = info.get('enterpriseValue', None)
        metrics['shares_outstanding'] = info.get('sharesOutstanding', None)

        # Valuation Ratios
        metrics['pe_ratio'] = info.get('trailingPE', None)
        metrics['forward_pe'] = info.get('forwardPE', None)
        metrics['peg_ratio'] = info.get('pegRatio', None)
        metrics['price_to_book'] = info.get('priceToBook', None)
        metrics['price_to_sales'] = info.get('priceToSalesTrailing12Months', None)
        metrics['ev_to_revenue'] = info.get('enterpriseToRevenue', None)
        metrics['ev_to_ebitda'] = info.get('enterpriseToEbitda', None)

        # Profitability Metrics
        metrics['profit_margin'] = info.get('profitMargins', None)
        metrics['operating_margin'] = info.get('operatingMargins', None)
        metrics['return_on_assets'] = info.get('returnOnAssets', None)
        metrics['return_on_equity'] = info.get('returnOnEquity', None)

        # Financial Health
        metrics['total_debt'] = info.get('totalDebt', None)
        metrics['total_cash'] = info.get('totalCash', None)
        metrics['debt_to_equity'] = info.get('debtToEquity', None)
        metrics['current_ratio'] = info.get('currentRatio', None)
        metrics['quick_ratio'] = info.get('quickRatio', None)

        # Growth Metrics
        metrics['revenue_growth'] = info.get('revenueGrowth', None)
        metrics['earnings_growth'] = info.get('earningsGrowth', None)

        # Dividend Information
        metrics['dividend_yield'] = info.get('dividendYield', None)
        metrics['payout_ratio'] = info.get('payoutRatio', None)

        # Other Key Metrics
        metrics['beta'] = info.get('beta', None)
        metrics['52_week_high'] = info.get('fiftyTwoWeekHigh', None)
        metrics['52_week_low'] = info.get('fiftyTwoWeekLow', None)
        metrics['avg_volume'] = info.get('averageVolume', None)

        # Company Information
        metrics['company_name'] = info.get('longName', info.get('shortName', None))
        metrics['sector'] = info.get('sector', None)
        metrics['industry'] = info.get('industry', None)
        metrics['country'] = info.get('country', None)
        metrics['employees'] = info.get('fullTimeEmployees', None)

        # Current price
        metrics['current_price'] = info.get('currentPrice', None)
        metrics['previous_close'] = info.get('previousClose', None)

    except Exception as e:
        LOGGER.warning("Error extracting metrics for %s: %s", symbol, e)
        return None

    return metrics


def validate_growth_data(quarterly_data, metric_name, required_quarters=4):
    """Validate data quality for growth calculations"""
    if quarterly_data.empty:
        return False, f"No quarterly data available for {metric_name}"

    if quarterly_data.shape[1] < required_quarters:
        return False, f"Insufficient quarters for {metric_name} (need {required_quarters}, got {quarterly_data.shape[1]})"

    # Check if metric exists
    metric_rows = [idx for idx in quarterly_data.index if metric_name.lower() in str(idx).lower()]
    if not metric_rows:
        return False, f"Metric '{metric_name}' not found in quarterly data"

    return True, "Data valid"


def calculate_yoy_growth(quarterly_data, metric_name):
    """Calculate year-over-year growth from quarterly data"""
    try:
        # Validate data quality first
        is_valid, message = validate_growth_data(quarterly_data, metric_name)
        if not is_valid:
            LOGGER.info("Data validation failed for %s: %s", metric_name, message)
            return None

        # Find the row with the specified metric
        metric_rows = [idx for idx in quarterly_data.index if metric_name.lower() in str(idx).lower()]
        metric_data = quarterly_data.loc[metric_rows[0]]

        # Get most recent quarter vs same quarter last year (4 quarters ago)
        current_q = metric_data.iloc[0]  # Most recent quarter
        year_ago_q = metric_data.iloc[3] if len(metric_data) >= 4 else None

        if (year_ago_q is None or year_ago_q == 0 or
                pd.isna(current_q) or pd.isna(year_ago_q)):
            return None

        growth_rate = (current_q - year_ago_q) / abs(year_ago_q)
        return growth_rate

    except Exception as e:
        LOGGER.warning("Error calculating YoY growth for %s: %s", metric_name, e)
        return None


def calculate_roe_growth(quarterly_financials, quarterly_balance_sheet):
    """Calculate ROE growth rate over time"""
    try:
        if quarterly_financials.empty or quarterly_balance_sheet.empty:
            return None

        # Find Net Income
        income_rows = [idx for idx in quarterly_financials.index
                       if 'net income' in str(idx).lower() and 'common' not in str(idx).lower()]
        if not income_rows:
            return None

        # Find Stockholders Equity
        equity_rows = [idx for idx in quarterly_balance_sheet.index
                       if 'stockholder' in str(idx).lower() and 'equity' in str(idx).lower()]
        if not equity_rows:
            return None

        income_data = quarterly_financials.loc[income_rows[0]]
        equity_data = quarterly_balance_sheet.loc[equity_rows[0]]

        # Calculate ROE for recent quarter and year-ago quarter using TTM approach
        if len(income_data) >= 4 and len(equity_data) >= 4:
            # Current TTM (Trailing Twelve Months) ROE
            current_ttm_income = income_data.iloc[0:4].sum()  # Sum of last 4 quarters
            current_equity = equity_data.iloc[0]  # Most recent equity

            # Year-ago TTM ROE (if we have enough data)
            if len(income_data) >= 7 and len(equity_data) >= 4:
                year_ago_ttm_income = income_data.iloc[3:7].sum()  # Sum of quarters 4-7
                year_ago_equity = equity_data.iloc[3]  # Equity 4 quarters ago
            else:
                # Fallback: annualize the year-ago quarter
                year_ago_ttm_income = income_data.iloc[3] * 4
                year_ago_equity = equity_data.iloc[3]

            if (current_equity > 0 and year_ago_equity > 0 and
                    not pd.isna(current_ttm_income) and not pd.isna(year_ago_ttm_income)):

                current_roe = current_ttm_income / current_equity
                year_ago_roe = year_ago_ttm_income / year_ago_equity

                if year_ago_roe != 0:
                    roe_growth = (current_roe - year_ago_roe) / abs(year_ago_roe)
                    return roe_growth

        return None

    except Exception as e:
        LOGGER.warning("Error calculating ROE growth: %s", e)
        return None


def parse_earnings_calendar(calendar, today=None):
    """Normalize yfinance's Ticker.calendar dict into the app's earnings shape.

    `calendar` is a plain dict (not a DataFrame) with keys like 'Earnings
    Date' (a list of one or two date objects - two when the exact date isn't
    confirmed yet and Yahoo only knows the estimated window), 'Earnings
    Average/Low/High', and 'Revenue Average/Low/High'. Returns a dict with
    next_earnings_date always present (None when unknown).
    """
    empty = {
        'next_earnings_date': None,
        'days_until': None,
        'eps_estimate': None,
        'eps_estimate_low': None,
        'eps_estimate_high': None,
        'revenue_estimate': None,
    }
    if not calendar:
        return empty

    earnings_dates = calendar.get('Earnings Date')
    if not earnings_dates:
        return empty

    if not isinstance(earnings_dates, (list, tuple)):
        earnings_dates = [earnings_dates]

    next_date = min(earnings_dates)
    today = today or date.today()

    return {
        'next_earnings_date': next_date.isoformat(),
        'days_until': (next_date - today).days,
        'eps_estimate': calendar.get('Earnings Average'),
        'eps_estimate_low': calendar.get('Earnings Low'),
        'eps_estimate_high': calendar.get('Earnings High'),
        'revenue_estimate': calendar.get('Revenue Average'),
    }


class YahooFinanceProvider:
    """DataProvider backed by Yahoo Finance via yfinance."""

    name = 'yahoo'

    def get_price_history(self, symbol, days=300):
        data = yf.download(symbol, period=f'{days}d', interval='1d', auto_adjust=True)
        # yf.download returns MultiIndex columns like ('Close', 'SPY') even
        # for a single symbol; flatten to match the documented flat
        # Open/High/Low/Close/Volume contract every caller relies on.
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)
        return data.dropna()

    def get_fundamentals(self, symbol):
        ticker = yf.Ticker(symbol)
        info = ticker.info
        basic_metrics = _extract_fundamental_metrics(symbol, info)
        enhanced_metrics = self._extract_enhanced_growth_metrics(ticker)

        if basic_metrics:
            basic_metrics.update(enhanced_metrics)
            return basic_metrics
        return enhanced_metrics

    def get_benchmark_metrics(self, symbol):
        info = yf.Ticker(symbol).info
        return {
            'pe_ratio': info.get('trailingPE'),
            'price_to_book': info.get('priceToBook'),
            'price_to_sales': info.get('priceToSalesTrailing12Months'),
            'return_on_equity': info.get('returnOnEquity'),
            'profit_margin': info.get('profitMargins'),
        }

    def lookup_symbol(self, symbol):
        info = yf.Ticker(symbol).info
        if not info or 'symbol' not in info:
            return None
        return {
            'symbol': symbol,
            'name': info.get('longName', info.get('shortName', symbol)),
        }

    def get_earnings_calendar(self, symbol):
        calendar = yf.Ticker(symbol).calendar
        return parse_earnings_calendar(calendar)

    def get_put_options(self, symbol, current_price=None):
        ticker = yf.Ticker(symbol)
        if current_price is None:
            info = ticker.info
            current_price = info.get('currentPrice') or info.get('previousClose')
        if not current_price:
            return []

        today = date.today()
        lower_bound = current_price * (1 - OPTIONS_MAX_STRIKE_DISCOUNT_PCT)
        candidates = []
        for expiration in ticker.options:
            days_to_expiry = (date.fromisoformat(expiration) - today).days
            if not OPTIONS_MIN_DAYS_TO_EXPIRY <= days_to_expiry <= OPTIONS_MAX_DAYS_TO_EXPIRY:
                continue

            puts = ticker.option_chain(expiration).puts
            nearby = puts[(puts['strike'] >= lower_bound) & (puts['strike'] <= current_price)]

            for _, row in nearby.iterrows():
                if (row.get('openInterest') or 0) < OPTIONS_MIN_OPEN_INTEREST:
                    continue

                # Yahoo's free options feed frequently reports bid/ask as 0
                # (observed for essentially every AAPL put across every
                # expiration in testing, not just after-hours) while
                # lastPrice - the most recent actual trade - stays populated.
                # Prefer the live bid when there is one; fall back to
                # lastPrice as an estimated premium rather than dropping the
                # contract entirely, and say which one we used so the caller
                # can be transparent about it.
                bid = row.get('bid') or 0
                last_price = row.get('lastPrice') or 0
                gross_premium = bid if bid > 0 else last_price
                if gross_premium <= 0:
                    continue

                strike = row['strike']

                # IBKR's $0.65/contract, $1.00-minimum commission schedule is
                # specific to US options; a non-USD chain (e.g. a German
                # stock's Eurex options) needs an entirely different fee
                # schedule that hasn't been researched, so those candidates
                # keep their gross premium untouched rather than being
                # silently misprized with the wrong currency's fee schedule.
                currency = row.get('currency') or 'USD'
                if currency == 'USD':
                    commission = max(IBKR_OPTIONS_COMMISSION_PER_CONTRACT, IBKR_OPTIONS_MIN_COMMISSION_PER_ORDER)
                    commission_drag_pct = commission / (gross_premium * 100)
                    if commission_drag_pct > OPTIONS_MAX_COMMISSION_DRAG_PCT:
                        continue
                    premium = gross_premium - commission / 100
                    commission_applicable = True
                else:
                    commission = None
                    commission_drag_pct = None
                    premium = gross_premium
                    commission_applicable = False

                annualized_yield = premium / strike * 365 / days_to_expiry if days_to_expiry else None
                candidates.append({
                    'expiration': expiration,
                    'days_to_expiry': days_to_expiry,
                    'strike': strike,
                    'premium': round(premium, 2),
                    'gross_premium': gross_premium,
                    'premium_source': 'bid' if bid > 0 else 'last_trade',
                    'commission_applicable': commission_applicable,
                    'commission': round(commission, 2) if commission is not None else None,
                    'commission_drag_pct': round(commission_drag_pct * 100, 2) if commission_drag_pct is not None else None,
                    'bid': bid,
                    'ask': row.get('ask'),
                    'last_price': last_price,
                    'implied_volatility': row.get('impliedVolatility'),
                    'open_interest': int(row.get('openInterest') or 0),
                    'volume': int(row.get('volume') or 0),
                    'breakeven': round(strike - premium, 2),
                    'discount_to_current_pct': round((current_price - strike) / current_price * 100, 2),
                    'annualized_yield_pct': round(annualized_yield * 100, 2) if annualized_yield else None,
                    'contract_symbol': row.get('contractSymbol'),
                })

        candidates.sort(key=lambda c: (c['days_to_expiry'], -c['strike']))
        return candidates

    def _extract_enhanced_growth_metrics(self, ticker):
        """Extract enhanced growth metrics from quarterly statements."""
        try:
            quarterly_financials = ticker.quarterly_financials
            quarterly_cashflow = ticker.quarterly_cashflow
            quarterly_balance_sheet = ticker.quarterly_balance_sheet

            return {
                'revenue_growth_yoy': calculate_yoy_growth(quarterly_financials, 'Total Revenue'),
                'ocf_growth_yoy': calculate_yoy_growth(quarterly_cashflow, 'Operating Cash Flow'),
                'roe_growth_yoy': calculate_roe_growth(quarterly_financials, quarterly_balance_sheet),
            }
        except Exception as e:
            LOGGER.warning("Error extracting enhanced growth metrics: %s", e)
            return {}


class FallbackProvider:
    """Try a primary provider, fall back to a secondary one per call.

    Used to prefer Interactive Brokers when TWS/Gateway is running while
    keeping Yahoo Finance as an automatic fallback when it is not.
    """

    def __init__(self, primary: DataProvider, secondary: DataProvider):
        self.primary = primary
        self.secondary = secondary
        self.name = f'{primary.name}+{secondary.name}'

    def _call(self, method_name, *args, **kwargs):
        try:
            return getattr(self.primary, method_name)(*args, **kwargs)
        except Exception as error:
            LOGGER.warning(
                "%s.%s failed (%s); falling back to %s",
                self.primary.name, method_name, error, self.secondary.name,
            )
            return getattr(self.secondary, method_name)(*args, **kwargs)

    def get_price_history(self, symbol, days=300):
        return self._call('get_price_history', symbol, days=days)

    def get_fundamentals(self, symbol):
        return self._call('get_fundamentals', symbol)

    def get_benchmark_metrics(self, symbol):
        return self._call('get_benchmark_metrics', symbol)

    def lookup_symbol(self, symbol):
        return self._call('lookup_symbol', symbol)

    def get_earnings_calendar(self, symbol):
        return self._call('get_earnings_calendar', symbol)

    def get_put_options(self, symbol, current_price=None):
        return self._call('get_put_options', symbol, current_price=current_price)


class CompositeProvider:
    """Yahoo for market data, SEC EDGAR overlaid for validated fundamentals.

    Prices, symbol lookup and benchmark metrics come from the market
    provider. Fundamentals start from the market provider's dict and are
    then overlaid with EDGAR's XBRL-derived metrics wherever EDGAR has a
    value - so US stocks get audited primary-source numbers while non-US
    symbols and gaps transparently keep the Yahoo values.
    """

    def __init__(self, edgar, market: DataProvider):
        self.edgar = edgar
        self.market = market
        self.name = f'{edgar.name}+{market.name}'

    def get_price_history(self, symbol, days=300):
        return self.market.get_price_history(symbol, days=days)

    def lookup_symbol(self, symbol):
        return self.market.lookup_symbol(symbol)

    def get_benchmark_metrics(self, symbol):
        return self.market.get_benchmark_metrics(symbol)

    def get_earnings_calendar(self, symbol):
        return self.market.get_earnings_calendar(symbol)

    def get_put_options(self, symbol, current_price=None):
        return self.market.get_put_options(symbol, current_price=current_price)

    def get_fundamentals(self, symbol):
        try:
            base = self.market.get_fundamentals(symbol)
        except Exception as error:
            LOGGER.warning("%s.get_fundamentals failed (%s); continuing with EDGAR only",
                           self.market.name, error)
            base = {}

        try:
            validated = self.edgar.get_fundamentals(
                symbol, current_price=base.get('current_price'))
        except Exception as error:
            LOGGER.info("EDGAR fundamentals unavailable for %s (%s); using %s data",
                        symbol, error, self.market.name)
            if not base:
                raise
            return base

        merged = dict(base)
        merged.update(validated)
        merged['fundamentals_source'] = (
            f'edgar (validated, {len(validated)} metrics) + {self.market.name}')
        return merged


def build_provider_from_env() -> DataProvider:
    """Build the provider selected by the DATA_PROVIDER environment variable.

    - 'auto' / 'edgar' (default) : SEC EDGAR validated fundamentals overlaid
                                   on Yahoo market data
    - 'yahoo'                    : Yahoo Finance only
    - 'ibkr'                     : Interactive Brokers only (TWS must run)
    - 'ibkr+yahoo'               : IBKR preferred with Yahoo fallback
    """
    import os

    choice = os.getenv('DATA_PROVIDER', 'auto').strip().lower()
    yahoo = YahooFinanceProvider()

    if choice == 'yahoo':
        return yahoo

    if choice in ('ibkr', 'ibkr+yahoo'):
        try:
            from ibkr_provider import IBKRProvider
        except ImportError:
            raise RuntimeError(
                f"DATA_PROVIDER={choice} requires the ib_async package (pip install ib_async)")

        ibkr = IBKRProvider(
            host=os.getenv('IBKR_HOST', '127.0.0.1'),
            port=int(os.getenv('IBKR_PORT', '7497')),
            client_id=int(os.getenv('IBKR_CLIENT_ID', '11')),
            readonly=True,
        )
        return ibkr if choice == 'ibkr' else FallbackProvider(ibkr, yahoo)

    # 'auto' / 'edgar'
    from edgar_provider import EdgarFundamentalsProvider
    return CompositeProvider(EdgarFundamentalsProvider(), yahoo)


_provider: DataProvider = build_provider_from_env()


def get_provider() -> DataProvider:
    """Return the active market data provider."""
    return _provider


def set_provider(provider: DataProvider):
    """Replace the active market data provider (also used by tests)."""
    global _provider
    _provider = provider
