import unittest
from datetime import date, timedelta

import data_providers
import edgar_provider


def _q(end, value, days=91, filed=None):
    """Build a duration-typed XBRL fact entry ending on `end`."""
    end_d = date.fromisoformat(end)
    entry = {
        'start': (end_d - timedelta(days=days)).isoformat(),
        'end': end,
        'val': value,
    }
    if filed:
        entry['filed'] = filed
    return entry


def _i(end, value, filed=None):
    """Build an instant-typed XBRL fact entry."""
    entry = {'end': end, 'val': value}
    if filed:
        entry['filed'] = filed
    return entry


QUARTER_ENDS = [
    '2024-03-30', '2024-06-29', '2024-09-28', '2024-12-28',
    '2025-03-29', '2025-06-28', '2025-09-27', '2025-12-27',
    '2026-03-28',
]
REVENUES = [100.0, 110.0, 120.0, 130.0, 110.0, 121.0, 132.0, 143.0, 121.0]
NET_INCOMES = [10.0, 11.0, 12.0, 13.0, 11.0, 12.1, 13.2, 14.3, 12.1]


def build_company_facts():
    return {
        'entityName': 'Fixture Corp',
        'facts': {
            'us-gaap': {
                'Revenues': {'units': {'USD': [
                    _q(end, val) for end, val in zip(QUARTER_ENDS, REVENUES)]}},
                'NetIncomeLoss': {'units': {'USD': [
                    _q(end, val) for end, val in zip(QUARTER_ENDS, NET_INCOMES)]}},
                'NetCashProvidedByUsedInOperatingActivities': {'units': {'USD': [
                    _q(end, val + 5) for end, val in zip(QUARTER_ENDS, NET_INCOMES)]}},
                'EarningsPerShareDiluted': {'units': {'USD/shares': [
                    _q(end, 1.0) for end in QUARTER_ENDS]}},
                'StockholdersEquity': {'units': {'USD': [
                    _i(end, 200.0 + 10 * index)
                    for index, end in enumerate(QUARTER_ENDS)]}},
                'AssetsCurrent': {'units': {'USD': [_i(QUARTER_ENDS[-1], 200.0)]}},
                'LiabilitiesCurrent': {'units': {'USD': [_i(QUARTER_ENDS[-1], 100.0)]}},
            },
            'dei': {
                'EntityCommonStockSharesOutstanding': {'units': {'shares': [
                    _i(QUARTER_ENDS[-1], 1000.0)]}},
            },
        },
    }


class XbrlComputationTests(unittest.TestCase):
    def test_quarterly_series_extracts_direct_quarters(self):
        series = edgar_provider.quarterly_series(
            [_q(end, val) for end, val in zip(QUARTER_ENDS, REVENUES)])
        self.assertEqual(len(series), 9)
        self.assertEqual(series[-1][1], 121.0)

    def test_quarterly_series_derives_quarters_from_ytd_chains(self):
        # A company reporting cash flow only as fiscal-year-to-date values
        fy_start = '2025-01-01'
        entries = [
            {'start': fy_start, 'end': '2025-03-31', 'val': 50.0},
            {'start': fy_start, 'end': '2025-06-30', 'val': 105.0},
            {'start': fy_start, 'end': '2025-09-30', 'val': 165.0},
            {'start': fy_start, 'end': '2025-12-31', 'val': 230.0},
        ]
        series = dict(edgar_provider.quarterly_series(entries))
        self.assertEqual(series[date(2025, 3, 31)], 50.0)     # direct Q1
        self.assertEqual(series[date(2025, 6, 30)], 55.0)     # 105 - 50
        self.assertEqual(series[date(2025, 9, 30)], 60.0)     # 165 - 105
        self.assertEqual(series[date(2025, 12, 31)], 65.0)    # 230 - 165

    def test_quarterly_series_prefers_latest_filing_for_duplicates(self):
        entries = [
            _q('2025-03-29', 100.0, filed='2025-05-01'),
            _q('2025-03-29', 105.0, filed='2026-02-01'),  # restated later
        ]
        series = edgar_provider.quarterly_series(entries)
        self.assertEqual(series, [(date(2025, 3, 29), 105.0)])

    def test_compute_fundamentals_produces_expected_metrics(self):
        metrics = edgar_provider.compute_fundamentals(
            build_company_facts(), current_price=100.0)

        self.assertEqual(metrics['company_name'], 'Fixture Corp')
        # Latest quarter revenue 121 vs year-ago 110 -> +10%
        self.assertAlmostEqual(metrics['revenue_growth_yoy'], 0.10, places=6)
        # TTM margin: income (12.1+13.2+14.3+12.1) / revenue (121+132+143+121)
        self.assertAlmostEqual(metrics['profit_margin'], 51.7 / 517.0, places=6)
        # ROE: TTM income / latest equity (280)
        self.assertAlmostEqual(metrics['return_on_equity'], 51.7 / 280.0, places=6)
        # Earnings TTM growth: 51.7 vs 47.0 (prior four quarters: 11+12+13+11)
        self.assertAlmostEqual(metrics['earnings_growth'], (51.7 - 47.0) / 47.0, places=6)
        self.assertIn('roe_growth_yoy', metrics)
        self.assertAlmostEqual(metrics['current_ratio'], 2.0)
        # Market-based: P/E = 100 / TTM EPS (4.0); market cap = 100 * 1000
        self.assertAlmostEqual(metrics['pe_ratio'], 25.0)
        self.assertAlmostEqual(metrics['market_cap'], 100_000.0)
        self.assertAlmostEqual(metrics['price_to_sales'], 100_000.0 / 517.0, places=4)
        self.assertAlmostEqual(metrics['price_to_book'], 100_000.0 / 280.0, places=4)

    def test_compute_fundamentals_without_price_skips_market_ratios(self):
        metrics = edgar_provider.compute_fundamentals(build_company_facts())
        self.assertNotIn('pe_ratio', metrics)
        self.assertNotIn('market_cap', metrics)
        self.assertIn('profit_margin', metrics)

    def test_compute_fundamentals_handles_empty_facts(self):
        self.assertEqual(edgar_provider.compute_fundamentals({'facts': {}}), {})


class FakeEdgar:
    name = 'edgar'

    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.received_price = 'unset'

    def get_fundamentals(self, symbol, current_price=None):
        self.received_price = current_price
        if self.error:
            raise self.error
        return dict(self.result)


class FakeMarket:
    name = 'yahoo'

    def __init__(self, fundamentals=None, error=None):
        self.fundamentals, self.error = fundamentals, error

    def get_fundamentals(self, symbol):
        if self.error:
            raise self.error
        return dict(self.fundamentals)


class CompositeProviderTests(unittest.TestCase):
    def test_edgar_metrics_overlay_market_base(self):
        market = FakeMarket({'pe_ratio': 99.0, 'sector': 'Technology', 'current_price': 50.0})
        edgar = FakeEdgar({'pe_ratio': 25.0, 'profit_margin': 0.1})
        provider = data_providers.CompositeProvider(edgar, market)

        merged = provider.get_fundamentals('AAPL')

        self.assertEqual(merged['pe_ratio'], 25.0)        # EDGAR wins
        self.assertEqual(merged['sector'], 'Technology')  # Yahoo-only field kept
        self.assertEqual(edgar.received_price, 50.0)      # price handed to EDGAR
        self.assertIn('edgar', merged['fundamentals_source'])
        self.assertEqual(provider.name, 'edgar+yahoo')

    def test_falls_back_to_market_when_edgar_unavailable(self):
        market = FakeMarket({'pe_ratio': 99.0, 'sector': 'Technology'})
        edgar = FakeEdgar(error=LookupError('not US-listed'))
        provider = data_providers.CompositeProvider(edgar, market)

        merged = provider.get_fundamentals('SAP.DE')

        self.assertEqual(merged['pe_ratio'], 99.0)
        self.assertNotIn('fundamentals_source', merged)

    def test_edgar_only_when_market_fails(self):
        market = FakeMarket(error=RuntimeError('rate limited'))
        edgar = FakeEdgar({'profit_margin': 0.2})
        provider = data_providers.CompositeProvider(edgar, market)

        merged = provider.get_fundamentals('AAPL')

        self.assertEqual(merged['profit_margin'], 0.2)


class AsOfFilteringTests(unittest.TestCase):
    """Point-in-time correctness: as_of must exclude facts filed later,
    including later *restatements* of a period that was already known.
    """

    def test_dedupe_latest_filed_excludes_facts_filed_after_as_of(self):
        entries = [
            _q('2025-03-29', 100.0, filed='2025-05-01'),
            _q('2025-06-28', 110.0, filed='2025-08-01'),
        ]

        result = edgar_provider._dedupe_latest_filed(entries, as_of='2025-06-01')

        self.assertEqual([e['end'] for e in result], ['2025-03-29'])

    def test_dedupe_latest_filed_excludes_entries_with_no_filed_date_when_as_of_given(self):
        entries = [_q('2025-03-29', 100.0, filed=None)]

        result = edgar_provider._dedupe_latest_filed(entries, as_of='2025-06-01')

        self.assertEqual(result, [])

    def test_dedupe_latest_filed_ignores_restatement_filed_after_as_of(self):
        # The original filing was known by as_of; a later restatement was not.
        entries = [
            _q('2025-03-29', 100.0, filed='2025-05-01'),
            _q('2025-03-29', 999.0, filed='2025-09-01'),  # restated later
        ]

        result = edgar_provider._dedupe_latest_filed(entries, as_of='2025-06-01')

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['val'], 100.0)

    def test_dedupe_latest_filed_without_as_of_uses_latest_restatement(self):
        entries = [
            _q('2025-03-29', 100.0, filed='2025-05-01'),
            _q('2025-03-29', 999.0, filed='2025-09-01'),
        ]

        result = edgar_provider._dedupe_latest_filed(entries)

        self.assertEqual(result[0]['val'], 999.0)

    def test_compute_fundamentals_as_of_hides_future_quarters(self):
        facts = build_company_facts_with_filed_dates()

        # As of right after the 2025-03-29 filing (filed ~2025-05-13), only
        # equity snapshots through that quarter exist (index 4: 200 + 10*4 = 240)
        # and TTM income is the sum of NET_INCOMES[1:5] = 11+12+13+11 = 47.
        metrics = edgar_provider.compute_fundamentals(facts, as_of='2025-05-15')
        self.assertAlmostEqual(metrics['return_on_equity'], 47.0 / 240.0, places=6)

        # The full (unrestricted) data reaches the last quarter (index 8:
        # 200 + 10*8 = 280) with TTM income NET_INCOMES[5:9] = 12.1+13.2+14.3+12.1 = 51.7 -
        # a different point-in-time answer, proving as_of actually changes the result
        # rather than just being ignored.
        full_metrics = edgar_provider.compute_fundamentals(facts)
        self.assertAlmostEqual(full_metrics['return_on_equity'], 51.7 / 280.0, places=6)
        self.assertNotAlmostEqual(metrics['return_on_equity'], full_metrics['return_on_equity'], places=3)

    def test_compute_fundamentals_as_of_accepts_date_object(self):
        from datetime import date as date_cls
        facts = build_company_facts_with_filed_dates()

        by_string = edgar_provider.compute_fundamentals(facts, as_of='2025-05-15')
        by_date = edgar_provider.compute_fundamentals(facts, as_of=date_cls(2025, 5, 15))

        self.assertEqual(by_string, by_date)

    def test_compute_fundamentals_as_of_far_in_past_returns_minimal_data(self):
        facts = build_company_facts_with_filed_dates()

        metrics = edgar_provider.compute_fundamentals(facts, as_of='2024-01-01')

        self.assertNotIn('revenue_growth_yoy', metrics)


def build_company_facts_with_filed_dates():
    """Same shape as build_company_facts(), but each quarter is 'filed'
    ~45 days after its period end, so as_of filtering has something to bite on.
    """
    def filed_for(end):
        end_d = date.fromisoformat(end)
        return (end_d + timedelta(days=45)).isoformat()

    return {
        'entityName': 'Fixture Corp',
        'facts': {
            'us-gaap': {
                'Revenues': {'units': {'USD': [
                    _q(end, val, filed=filed_for(end))
                    for end, val in zip(QUARTER_ENDS, REVENUES)]}},
                'NetIncomeLoss': {'units': {'USD': [
                    _q(end, val, filed=filed_for(end))
                    for end, val in zip(QUARTER_ENDS, NET_INCOMES)]}},
                'NetCashProvidedByUsedInOperatingActivities': {'units': {'USD': [
                    _q(end, val + 5, filed=filed_for(end))
                    for end, val in zip(QUARTER_ENDS, NET_INCOMES)]}},
                'EarningsPerShareDiluted': {'units': {'USD/shares': [
                    _q(end, 1.0, filed=filed_for(end)) for end in QUARTER_ENDS]}},
                'StockholdersEquity': {'units': {'USD': [
                    _i(end, 200.0 + 10 * index, filed=filed_for(end))
                    for index, end in enumerate(QUARTER_ENDS)]}},
                'AssetsCurrent': {'units': {'USD': [
                    _i(QUARTER_ENDS[-1], 200.0, filed=filed_for(QUARTER_ENDS[-1]))]}},
                'LiabilitiesCurrent': {'units': {'USD': [
                    _i(QUARTER_ENDS[-1], 100.0, filed=filed_for(QUARTER_ENDS[-1]))]}},
            },
            'dei': {
                'EntityCommonStockSharesOutstanding': {'units': {'shares': [
                    _i(QUARTER_ENDS[-1], 1000.0, filed=filed_for(QUARTER_ENDS[-1]))]}},
            },
        },
    }


if __name__ == '__main__':
    unittest.main()
