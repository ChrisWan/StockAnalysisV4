"""Interactive Brokers DataProvider backed by TWS or IB Gateway.

Connects to a locally running TWS (paper: port 7497, live: 7496) or IB
Gateway (paper: 4002, live: 4001) through ib_async. All network calls run on
a dedicated thread that owns the asyncio event loop and the single IB
connection, so Flask's worker threads can call the provider safely.

Price history comes from reqHistoricalData; fundamentals come from
reqFundamentalData ReportSnapshot, which returns validated Refinitiv (LSEG)
ratio data as XML. The parsing helpers are pure functions so they can be
unit-tested without a TWS connection.
"""

import asyncio
import logging
import threading
import xml.etree.ElementTree as ET

import pandas as pd

LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 60
# After a failed connect, skip reconnect attempts for this long so a missing
# TWS does not add connect latency to every request (the fallback provider
# answers instead).
CONNECT_RETRY_COOLDOWN_SECONDS = 60

# Refinitiv ReportSnapshot ratio field -> (our metric key, scale factor).
# Percent-style fields are scaled to fractions to match the Yahoo-based
# metrics the scoring layer already expects (e.g. ROE 0.15, not 15.0).
RATIO_FIELD_MAP = {
    'NPRICE': ('current_price', 1.0),
    'NHIG': ('52_week_high', 1.0),
    'NLOW': ('52_week_low', 1.0),
    'MKTCAP': ('market_cap', 1e6),
    'PEEXCLXOR': ('pe_ratio', 1.0),
    'APENORM': ('forward_pe', 1.0),
    'PRICE2BK': ('price_to_book', 1.0),
    'TTMPR2REV': ('price_to_sales', 1.0),
    'TMPR2REV': ('price_to_sales', 1.0),
    'TTMROEPCT': ('return_on_equity', 0.01),
    'TTMROAPCT': ('return_on_assets', 0.01),
    'TTMNPMGN': ('profit_margin', 0.01),
    'TTMOPMGN': ('operating_margin', 0.01),
    'TTMREVCHG': ('revenue_growth', 0.01),
    'TTMEPSCHG': ('earnings_growth', 0.01),
    'QCURRATIO': ('current_ratio', 1.0),
    'QQUICKRATI': ('quick_ratio', 1.0),
    'QTOTD2EQ': ('debt_to_equity', 0.01),
    'YIELD': ('dividend_yield', 0.01),
    'BETA': ('beta', 1.0),
    'EMPLOYEES': ('employees', 1.0),
}

# TRBC economic sector names -> the sector names used by the app's
# benchmarks (which follow Yahoo Finance's sector taxonomy).
TRBC_SECTOR_MAP = {
    'Technology': 'Technology',
    'Healthcare': 'Healthcare',
    'Financials': 'Financial Services',
    'Consumer Cyclicals': 'Consumer Cyclical',
    'Consumer Non-Cyclicals': 'Consumer Defensive',
    'Utilities': 'Utilities',
    'Energy': 'Energy',
    'Industrials': 'Industrials',
    'Basic Materials': 'Materials',
    'Real Estate': 'Real Estate',
    'Telecommunications Services': 'Communication Services',
}

# Yahoo-style symbol suffixes -> (IBKR currency, primary exchange).
SYMBOL_SUFFIX_MAP = {
    '.DE': ('EUR', 'IBIS'),   # XETRA
    '.F': ('EUR', 'FWB'),     # Frankfurt
    '.L': ('GBP', 'LSE'),     # London
    '.PA': ('EUR', 'SBF'),    # Paris
    '.AS': ('EUR', 'AEB'),    # Amsterdam
}


def symbol_to_contract_kwargs(symbol):
    """Translate a (possibly Yahoo-suffixed) symbol into IBKR contract kwargs."""
    for suffix, (currency, primary_exchange) in SYMBOL_SUFFIX_MAP.items():
        if symbol.upper().endswith(suffix):
            return {
                'symbol': symbol[:-len(suffix)].upper(),
                'exchange': 'SMART',
                'currency': currency,
                'primaryExchange': primary_exchange,
            }
    return {'symbol': symbol.upper(), 'exchange': 'SMART', 'currency': 'USD'}


def bars_to_dataframe(bars):
    """Convert IBKR historical bars to the OHLCV frame the app expects."""
    if not bars:
        return pd.DataFrame()

    frame = pd.DataFrame({
        'Open': [bar.open for bar in bars],
        'High': [bar.high for bar in bars],
        'Low': [bar.low for bar in bars],
        'Close': [bar.close for bar in bars],
        'Volume': [bar.volume for bar in bars],
    }, index=pd.DatetimeIndex([pd.Timestamp(bar.date) for bar in bars], name='Date'))
    return frame.dropna()


def map_trbc_sector(trbc_name):
    """Map a TRBC economic sector name to the app's sector taxonomy."""
    if not trbc_name:
        return None
    trbc_name = trbc_name.strip()
    if trbc_name in TRBC_SECTOR_MAP:
        return TRBC_SECTOR_MAP[trbc_name]
    for known, mapped in TRBC_SECTOR_MAP.items():
        if trbc_name.startswith(known):
            return mapped
    return trbc_name


def parse_report_snapshot(xml_text):
    """Parse a Refinitiv ReportSnapshot XML document into a metrics dict.

    Returns {} when the document is empty or unparseable.
    """
    if not xml_text:
        return {}

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        LOGGER.warning("Could not parse ReportSnapshot XML: %s", error)
        return {}

    metrics = {}

    for ratio in root.iter('Ratio'):
        field_name = ratio.get('FieldName')
        mapping = RATIO_FIELD_MAP.get(field_name)
        if mapping is None or ratio.text is None:
            continue

        key, scale = mapping
        try:
            metrics[key] = float(ratio.text) * scale
        except ValueError:
            continue

    # Company name
    for coid in root.iter('CoID'):
        if coid.get('Type') == 'CompanyName' and coid.text:
            metrics['company_name'] = coid.text.strip()
            break

    # Sector / industry from TRBC classification
    trbc_industries = [
        element for element in root.iter('Industry')
        if element.get('type') == 'TRBC' and element.text
    ]
    if trbc_industries:
        # order="1" is the most significant classification when present
        trbc_industries.sort(key=lambda element: element.get('order') or '9')
        metrics['sector'] = map_trbc_sector(trbc_industries[0].text)
        if len(trbc_industries) > 1 and trbc_industries[1].text:
            metrics['industry'] = trbc_industries[1].text.strip()

    for employees in root.iter('Employees'):
        if employees.text:
            try:
                metrics['employees'] = int(float(employees.text))
            except ValueError:
                pass
            break

    return metrics


class _IBLoop:
    """Dedicated thread owning the asyncio loop and the IB connection."""

    def __init__(self):
        self._loop = None
        self._thread = None
        self._start_lock = threading.Lock()

    def _ensure_running(self):
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run, name='ibkr-event-loop', daemon=True)
            self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro, timeout=REQUEST_TIMEOUT_SECONDS):
        """Run a coroutine on the IB event loop and return its result."""
        self._ensure_running()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)


class IBKRProvider:
    """DataProvider backed by a local TWS / IB Gateway instance."""

    name = 'ibkr'

    def __init__(self, host='127.0.0.1', port=7497, client_id=11, readonly=True):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.readonly = readonly
        self._ib = None
        self._loop = _IBLoop()
        self._connect_lock = threading.Lock()
        self._last_connect_failure = None

    # -- connection management -------------------------------------------------

    def _connect_sync(self):
        """Ensure a live IB connection, creating it on the IB loop thread."""
        import time as _time

        with self._connect_lock:
            if self._ib is not None and self._ib.isConnected():
                return

            if (self._last_connect_failure is not None and
                    _time.monotonic() - self._last_connect_failure < CONNECT_RETRY_COOLDOWN_SECONDS):
                raise ConnectionError(
                    f'IBKR connection to {self.host}:{self.port} recently failed; '
                    f'retrying after cooldown')

            try:
                self._loop.submit(self._connect_async(), timeout=CONNECT_TIMEOUT_SECONDS + 5)
                self._last_connect_failure = None
            except Exception:
                self._last_connect_failure = _time.monotonic()
                raise

    async def _connect_async(self):
        from ib_async import IB

        if self._ib is None:
            self._ib = IB()
        if self._ib.isConnected():
            return

        await self._ib.connectAsync(
            self.host,
            self.port,
            clientId=self.client_id,
            timeout=CONNECT_TIMEOUT_SECONDS,
            readonly=self.readonly,
        )
        LOGGER.info(
            "Connected to IBKR at %s:%s (clientId=%s, readonly=%s)",
            self.host, self.port, self.client_id, self.readonly,
        )

    def is_available(self):
        """True when a connection to TWS/Gateway can be established."""
        try:
            self._connect_sync()
            return True
        except Exception as error:
            LOGGER.info("IBKR not available at %s:%s: %s", self.host, self.port, error)
            return False

    def _run(self, coro_factory, timeout=REQUEST_TIMEOUT_SECONDS):
        self._connect_sync()
        return self._loop.submit(coro_factory(), timeout=timeout)

    async def _qualified_contract(self, symbol):
        from ib_async import Stock

        contract = Stock(**symbol_to_contract_kwargs(symbol))
        qualified = await self._ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f'IBKR could not resolve symbol {symbol!r}')
        return qualified[0]

    # -- DataProvider interface ------------------------------------------------

    def get_price_history(self, symbol, days=300):
        async def fetch():
            contract = await self._qualified_contract(symbol)
            duration = f'{min(days, 365)} D' if days <= 365 else f'{-(-days // 365)} Y'
            bars = await self._ib.reqHistoricalDataAsync(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting='1 day',
                whatToShow='ADJUSTED_LAST',
                useRTH=True,
            )
            return bars

        return bars_to_dataframe(self._run(lambda: fetch()))

    def get_fundamentals(self, symbol):
        async def fetch():
            contract = await self._qualified_contract(symbol)
            return await self._ib.reqFundamentalDataAsync(contract, 'ReportSnapshot')

        xml_text = self._run(lambda: fetch())
        metrics = parse_report_snapshot(xml_text)
        if not metrics:
            raise ValueError(f'IBKR returned no fundamental data for {symbol!r}')
        return metrics

    def get_benchmark_metrics(self, symbol):
        fundamentals = self.get_fundamentals(symbol)
        return {
            'pe_ratio': fundamentals.get('pe_ratio'),
            'price_to_book': fundamentals.get('price_to_book'),
            'price_to_sales': fundamentals.get('price_to_sales'),
            'return_on_equity': fundamentals.get('return_on_equity'),
            'profit_margin': fundamentals.get('profit_margin'),
        }

    def lookup_symbol(self, symbol):
        async def fetch():
            contract = await self._qualified_contract(symbol)
            details = await self._ib.reqContractDetailsAsync(contract)
            return details

        try:
            details = self._run(lambda: fetch())
        except ValueError:
            return None

        if not details:
            return None
        return {
            'symbol': symbol,
            'name': details[0].longName or symbol,
        }

    def disconnect(self):
        if self._ib is not None and self._ib.isConnected():
            self._loop.submit(self._disconnect_async())

    async def _disconnect_async(self):
        self._ib.disconnect()
