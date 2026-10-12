"""SEC EDGAR fundamentals provider.

Computes fundamental metrics directly from XBRL company facts filed with the
SEC (data.sec.gov) - the audited primary-source numbers, with no data vendor
in between. Covers US-listed companies only; the composite provider falls
back to Yahoo for everything else.

SEC fair-access rules: requests must carry a User-Agent identifying the
caller (set EDGAR_USER_AGENT, e.g. "MyApp you@example.com") and stay under
10 requests/second - trivially satisfied here since results are cached in
memory per symbol.
"""

import logging
import os
import threading
from datetime import date, timedelta

import requests

LOGGER = logging.getLogger(__name__)

TICKER_MAP_URL = 'https://www.sec.gov/files/company_tickers.json'
COMPANY_FACTS_URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
REQUEST_TIMEOUT = 30

# XBRL concept names in preference order for each metric we compute.
REVENUE_CONCEPTS = [
    'RevenueFromContractWithCustomerExcludingAssessedTax',
    'Revenues',
    'SalesRevenueNet',
    'RevenueFromContractWithCustomerIncludingAssessedTax',
]
NET_INCOME_CONCEPTS = ['NetIncomeLoss', 'ProfitLoss']
OCF_CONCEPTS = [
    'NetCashProvidedByUsedInOperatingActivities',
    'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations',
]
EQUITY_CONCEPTS = [
    'StockholdersEquity',
    'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
]
EPS_CONCEPTS = ['EarningsPerShareDiluted', 'EarningsPerShareBasic']
CURRENT_ASSETS_CONCEPTS = ['AssetsCurrent']
CURRENT_LIABILITIES_CONCEPTS = ['LiabilitiesCurrent']


def _user_agent():
    return os.getenv(
        'EDGAR_USER_AGENT',
        'StockAnalysisV4/1.0 (set EDGAR_USER_AGENT to your contact email)')


def pick_concept(us_gaap, concept_names, unit_names):
    """Return the units list for the first available concept, else None."""
    for name in concept_names:
        concept = us_gaap.get(name)
        if not concept:
            continue
        units = concept.get('units', {})
        for unit in unit_names:
            if unit in units and units[unit]:
                return units[unit]
    return None


def _parse_date(value):
    return date.fromisoformat(value) if value else None


def _dedupe_latest_filed(entries, as_of=None):
    """Keep one entry per period end, preferring the most recently filed.

    When `as_of` (an ISO date string) is given, this reconstructs what would
    have been knowable as of that date: facts filed after `as_of` are
    dropped entirely, and facts with no `filed` date are dropped too since
    we can't otherwise prove they were public knowledge by then. Without
    `as_of`, all facts are eligible and only the latest restatement wins.
    """
    if as_of is not None:
        entries = [e for e in entries if e.get('filed') and e['filed'] <= as_of]

    best = {}
    for entry in entries:
        key = (entry.get('start'), entry.get('end'))
        prior = best.get(key)
        if prior is None or (entry.get('filed') or '') > (prior.get('filed') or ''):
            best[key] = entry
    return sorted(best.values(), key=lambda e: e['end'])


def quarterly_series(entries, as_of=None):
    """Extract a clean quarterly series from duration-typed XBRL facts.

    Accepts facts whose duration is roughly one quarter. Where a company
    reports year-to-date values instead (common for cash flow statements),
    quarterly values are derived by differencing consecutive YTD entries of
    the same fiscal year.

    `as_of` (ISO date string), when given, restricts this to facts filed on
    or before that date - see _dedupe_latest_filed().

    Returns a list of (end_date, value) sorted by end date.
    """
    duration_entries = []
    for entry in entries:
        start, end = _parse_date(entry.get('start')), _parse_date(entry.get('end'))
        if start is None or end is None or entry.get('val') is None:
            continue
        duration_entries.append({**entry, '_start': start, '_end': end,
                                 '_days': (end - start).days})

    duration_entries = _dedupe_latest_filed(
        [e for e in duration_entries if e['_days'] <= 400], as_of=as_of)

    # Direct quarterly facts (~91 days)
    quarters = {
        e['_end']: float(e['val'])
        for e in duration_entries if 75 <= e['_days'] <= 105
    }

    # Derive additional quarters from YTD chains (differences of 6/9/12-month
    # facts sharing a fiscal-year start).
    by_start = {}
    for e in duration_entries:
        by_start.setdefault(e['_start'], []).append(e)
    for start, chain in by_start.items():
        chain.sort(key=lambda e: e['_days'])
        for previous, current in zip(chain, chain[1:]):
            gap_days = current['_days'] - previous['_days']
            if 75 <= gap_days <= 105 and current['_end'] not in quarters:
                quarters[current['_end']] = float(current['val']) - float(previous['val'])

    return sorted(quarters.items())


def instant_series(entries, as_of=None):
    """Extract (end_date, value) pairs from instant-typed XBRL facts.

    `as_of` (ISO date string), when given, restricts this to facts filed on
    or before that date - see _dedupe_latest_filed().
    """
    cleaned = []
    for entry in _dedupe_latest_filed(
            [e for e in entries if e.get('val') is not None], as_of=as_of):
        end = _parse_date(entry.get('end'))
        if end is not None:
            cleaned.append((end, float(entry['val'])))
    return sorted(cleaned)


def ttm_sum(series, as_of_index=-1):
    """Sum the four quarters ending at the given index; None if unavailable."""
    if len(series) < 4:
        return None
    index = as_of_index if as_of_index >= 0 else len(series) + as_of_index
    if index < 3:
        return None
    window = series[index - 3:index + 1]
    first, last = window[0][0], window[-1][0]
    if (last - first).days > 320:  # gaps mean missing quarters
        return None
    return sum(value for _, value in window)


def yoy_growth(series):
    """Latest quarter vs the quarter ~one year earlier."""
    if len(series) < 5:
        return None
    latest_end, latest_value = series[-1]
    target = latest_end - timedelta(days=365)
    candidates = [
        (abs((end - target).days), value)
        for end, value in series[:-1]
        if abs((end - target).days) <= 45
    ]
    if not candidates:
        return None
    _, year_ago_value = min(candidates)
    if year_ago_value == 0:
        return None
    return (latest_value - year_ago_value) / abs(year_ago_value)


def ttm_growth(series):
    """Current trailing-twelve-month sum vs the TTM one year earlier."""
    current = ttm_sum(series, -1)
    prior = ttm_sum(series, -5)
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior)


def compute_fundamentals(company_facts, current_price=None, as_of=None):
    """Compute the app's fundamental metrics from a companyfacts document.

    current_price (optional) enables market-based ratios (P/E, P/S, P/B,
    market cap) on top of the accounting metrics.

    as_of (optional date or ISO date string) restricts computation to facts
    filed on or before that date, reconstructing what would have been known
    at that point in time - used by the backtester to avoid leaking future
    filings into historical scores. Accepts either a date object or an
    already-formatted ISO string for caller convenience.
    """
    if as_of is not None and not isinstance(as_of, str):
        as_of = as_of.isoformat()

    facts = company_facts.get('facts', {})
    us_gaap = facts.get('us-gaap', {})
    dei = facts.get('dei', {})
    if not us_gaap:
        return {}

    metrics = {'company_name': company_facts.get('entityName')}

    revenue = quarterly_series(pick_concept(us_gaap, REVENUE_CONCEPTS, ['USD']) or [], as_of=as_of)
    net_income = quarterly_series(pick_concept(us_gaap, NET_INCOME_CONCEPTS, ['USD']) or [], as_of=as_of)
    ocf = quarterly_series(pick_concept(us_gaap, OCF_CONCEPTS, ['USD']) or [], as_of=as_of)
    eps = quarterly_series(pick_concept(us_gaap, EPS_CONCEPTS, ['USD/shares']) or [], as_of=as_of)
    equity = instant_series(pick_concept(us_gaap, EQUITY_CONCEPTS, ['USD']) or [], as_of=as_of)
    current_assets = instant_series(pick_concept(us_gaap, CURRENT_ASSETS_CONCEPTS, ['USD']) or [], as_of=as_of)
    current_liabilities = instant_series(pick_concept(us_gaap, CURRENT_LIABILITIES_CONCEPTS, ['USD']) or [], as_of=as_of)
    shares = instant_series(
        pick_concept(dei, ['EntityCommonStockSharesOutstanding'], ['shares']) or [], as_of=as_of)

    ttm_revenue = ttm_sum(revenue)
    ttm_income = ttm_sum(net_income)
    ttm_eps = ttm_sum(eps)

    # Growth (year-over-year, from quarterly filings)
    metrics['revenue_growth_yoy'] = yoy_growth(revenue)
    metrics['ocf_growth_yoy'] = yoy_growth(ocf)
    metrics['earnings_growth'] = ttm_growth(net_income)

    # Profitability
    if ttm_revenue and ttm_income is not None and ttm_revenue != 0:
        metrics['profit_margin'] = ttm_income / ttm_revenue
    if equity and ttm_income is not None and equity[-1][1] > 0:
        metrics['return_on_equity'] = ttm_income / equity[-1][1]

    # ROE growth: current TTM ROE vs year-ago TTM ROE
    if len(equity) >= 5:
        current_roe = prior_roe = None
        current_income, prior_income = ttm_sum(net_income, -1), ttm_sum(net_income, -5)
        if current_income is not None and equity[-1][1] > 0:
            current_roe = current_income / equity[-1][1]
        if prior_income is not None and equity[-5][1] > 0:
            prior_roe = prior_income / equity[-5][1]
        if current_roe is not None and prior_roe not in (None, 0):
            metrics['roe_growth_yoy'] = (current_roe - prior_roe) / abs(prior_roe)

    # Financial health
    if current_assets and current_liabilities and current_liabilities[-1][1] != 0:
        metrics['current_ratio'] = current_assets[-1][1] / current_liabilities[-1][1]

    # Market-based ratios when a price is supplied
    if current_price and shares:
        shares_outstanding = shares[-1][1]
        market_cap = current_price * shares_outstanding
        metrics['shares_outstanding'] = shares_outstanding
        metrics['market_cap'] = market_cap
        metrics['current_price'] = current_price
        if ttm_eps:
            metrics['pe_ratio'] = current_price / ttm_eps if ttm_eps != 0 else None
        if ttm_revenue:
            metrics['price_to_sales'] = market_cap / ttm_revenue
        if equity and equity[-1][1] > 0:
            metrics['price_to_book'] = market_cap / equity[-1][1]

    return {key: value for key, value in metrics.items() if value is not None}


class EdgarFundamentalsProvider:
    """Fundamentals-only provider backed by SEC EDGAR XBRL company facts.

    Has no price data; it is meant to be composed with a market data
    provider (see CompositeProvider in data_providers).
    """

    name = 'edgar'

    def __init__(self):
        self._session = requests.Session()
        self._session.headers['User-Agent'] = _user_agent()
        self._ticker_map = None
        self._facts_cache = {}
        self._lock = threading.Lock()

    def _get_json(self, url):
        response = self._session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def _cik_for_symbol(self, symbol):
        with self._lock:
            if self._ticker_map is None:
                raw = self._get_json(TICKER_MAP_URL)
                self._ticker_map = {
                    entry['ticker'].upper(): int(entry['cik_str'])
                    for entry in raw.values()
                }
        return self._ticker_map.get(symbol.upper())

    def get_company_facts(self, symbol):
        """Fetch (and cache) the raw companyfacts document for a symbol."""
        cik = self._cik_for_symbol(symbol)
        if cik is None:
            raise LookupError(f'{symbol!r} not found in SEC EDGAR (not US-listed?)')

        with self._lock:
            if cik in self._facts_cache:
                return self._facts_cache[cik]

        facts = self._get_json(COMPANY_FACTS_URL.format(cik=cik))
        with self._lock:
            self._facts_cache[cik] = facts
        return facts

    def get_fundamentals(self, symbol, current_price=None, as_of=None):
        facts = self.get_company_facts(symbol)
        metrics = compute_fundamentals(facts, current_price=current_price, as_of=as_of)
        if not metrics:
            raise ValueError(f'No usable XBRL facts for {symbol!r}')
        return metrics
