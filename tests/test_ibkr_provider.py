import os
import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

import data_providers
import ibkr_provider


SAMPLE_SNAPSHOT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ReportSnapshot Major="2" Minor="1" Revision="1">
  <CoIDs>
    <CoID Type="CompanyName">Apple Inc</CoID>
    <CoID Type="RepNo">A0F0</CoID>
  </CoIDs>
  <CoGeneralInfo>
    <Employees LastUpdated="2026-01-01">164000</Employees>
  </CoGeneralInfo>
  <peerInfo lastUpdated="2026-05-01">
    <IndustryInfo>
      <Industry type="TRBC" order="1" reported="0" code="52">Technology</Industry>
      <Industry type="TRBC" order="2" reported="0" code="5210">Technology Equipment</Industry>
      <Industry type="NAICS" order="1" reported="0" code="334220">Radio and TV Broadcasting</Industry>
    </IndustryInfo>
  </peerInfo>
  <Ratios PriceCurrency="USD" ReportingCurrency="USD" ExchangeRate="1.0">
    <Group ID="Price and Volume">
      <Ratio FieldName="NPRICE" Type="N">195.50</Ratio>
      <Ratio FieldName="NHIG" Type="N">237.23</Ratio>
      <Ratio FieldName="NLOW" Type="N">164.08</Ratio>
    </Group>
    <Group ID="Income Statement">
      <Ratio FieldName="MKTCAP" Type="N">2950000.0</Ratio>
    </Group>
    <Group ID="Valuation">
      <Ratio FieldName="PEEXCLXOR" Type="N">29.8</Ratio>
      <Ratio FieldName="PRICE2BK" Type="N">44.6</Ratio>
      <Ratio FieldName="TTMPR2REV" Type="N">7.5</Ratio>
    </Group>
    <Group ID="Profitability">
      <Ratio FieldName="TTMROEPCT" Type="N">147.25</Ratio>
      <Ratio FieldName="TTMNPMGN" Type="N">24.30</Ratio>
      <Ratio FieldName="TTMOPMGN" Type="N">31.51</Ratio>
      <Ratio FieldName="TTMREVCHG" Type="N">2.02</Ratio>
    </Group>
    <Group ID="Financial Strength">
      <Ratio FieldName="QCURRATIO" Type="N">0.95</Ratio>
      <Ratio FieldName="QTOTD2EQ" Type="N">140.0</Ratio>
    </Group>
    <Group ID="Other">
      <Ratio FieldName="BETA" Type="N">1.24</Ratio>
      <Ratio FieldName="LATESTADATE" Type="D">2025-09-27</Ratio>
    </Group>
  </Ratios>
</ReportSnapshot>
"""


class ReportSnapshotParsingTests(unittest.TestCase):
    def test_parses_ratios_with_percent_scaling(self):
        metrics = ibkr_provider.parse_report_snapshot(SAMPLE_SNAPSHOT_XML)

        self.assertEqual(metrics['current_price'], 195.50)
        self.assertEqual(metrics['pe_ratio'], 29.8)
        self.assertEqual(metrics['price_to_book'], 44.6)
        self.assertEqual(metrics['price_to_sales'], 7.5)
        # Percent fields are converted to fractions to match the scoring layer
        self.assertAlmostEqual(metrics['return_on_equity'], 1.4725)
        self.assertAlmostEqual(metrics['profit_margin'], 0.2430)
        self.assertAlmostEqual(metrics['revenue_growth'], 0.0202)
        self.assertAlmostEqual(metrics['debt_to_equity'], 1.40)
        # Market cap arrives in millions
        self.assertEqual(metrics['market_cap'], 2_950_000_000_000.0)

    def test_parses_company_name_sector_and_employees(self):
        metrics = ibkr_provider.parse_report_snapshot(SAMPLE_SNAPSHOT_XML)

        self.assertEqual(metrics['company_name'], 'Apple Inc')
        self.assertEqual(metrics['sector'], 'Technology')
        self.assertEqual(metrics['industry'], 'Technology Equipment')
        self.assertEqual(metrics['employees'], 164000)

    def test_maps_trbc_sector_names_to_app_taxonomy(self):
        self.assertEqual(ibkr_provider.map_trbc_sector('Financials'), 'Financial Services')
        self.assertEqual(ibkr_provider.map_trbc_sector('Consumer Non-Cyclicals'), 'Consumer Defensive')
        self.assertEqual(ibkr_provider.map_trbc_sector('Basic Materials'), 'Materials')
        # Unknown names pass through instead of being dropped
        self.assertEqual(ibkr_provider.map_trbc_sector('Something New'), 'Something New')

    def test_handles_empty_and_invalid_xml(self):
        self.assertEqual(ibkr_provider.parse_report_snapshot(''), {})
        self.assertEqual(ibkr_provider.parse_report_snapshot(None), {})
        self.assertEqual(ibkr_provider.parse_report_snapshot('not xml at all <<<'), {})


class BarsAndContractsTests(unittest.TestCase):
    def test_bars_to_dataframe_produces_expected_columns(self):
        bars = [
            SimpleNamespace(date='2026-07-16', open=100.0, high=102.0, low=99.0, close=101.0, volume=1_000_000),
            SimpleNamespace(date='2026-07-17', open=101.0, high=104.0, low=100.5, close=103.5, volume=1_200_000),
        ]

        frame = ibkr_provider.bars_to_dataframe(bars)

        self.assertEqual(list(frame.columns), ['Open', 'High', 'Low', 'Close', 'Volume'])
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame['Close'].iloc[-1], 103.5)
        self.assertIsInstance(frame.index, pd.DatetimeIndex)

    def test_bars_to_dataframe_handles_empty_input(self):
        self.assertTrue(ibkr_provider.bars_to_dataframe([]).empty)

    def test_symbol_to_contract_kwargs_defaults_to_us(self):
        kwargs = ibkr_provider.symbol_to_contract_kwargs('AAPL')
        self.assertEqual(kwargs, {'symbol': 'AAPL', 'exchange': 'SMART', 'currency': 'USD'})

    def test_symbol_to_contract_kwargs_maps_xetra_suffix(self):
        kwargs = ibkr_provider.symbol_to_contract_kwargs('SAP.DE')
        self.assertEqual(kwargs['symbol'], 'SAP')
        self.assertEqual(kwargs['currency'], 'EUR')
        self.assertEqual(kwargs['primaryExchange'], 'IBIS')


class FakeSubProvider:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = []

    def get_fundamentals(self, symbol):
        self.calls.append(symbol)
        if self.error:
            raise self.error
        return self.result

    def get_price_history(self, symbol, days=300):
        return self.get_fundamentals(symbol)

    def get_benchmark_metrics(self, symbol):
        return self.get_fundamentals(symbol)

    def lookup_symbol(self, symbol):
        return self.get_fundamentals(symbol)


class FallbackProviderTests(unittest.TestCase):
    def test_uses_primary_when_it_succeeds(self):
        primary = FakeSubProvider('ibkr', result={'pe_ratio': 10})
        secondary = FakeSubProvider('yahoo', result={'pe_ratio': 99})
        provider = data_providers.FallbackProvider(primary, secondary)

        result = provider.get_fundamentals('AAPL')

        self.assertEqual(result['pe_ratio'], 10)
        self.assertEqual(secondary.calls, [])
        self.assertEqual(provider.name, 'ibkr+yahoo')

    def test_falls_back_when_primary_fails(self):
        primary = FakeSubProvider('ibkr', error=ConnectionError('TWS not running'))
        secondary = FakeSubProvider('yahoo', result={'pe_ratio': 99})
        provider = data_providers.FallbackProvider(primary, secondary)

        result = provider.get_fundamentals('AAPL')

        self.assertEqual(result['pe_ratio'], 99)
        self.assertEqual(primary.calls, ['AAPL'])
        self.assertEqual(secondary.calls, ['AAPL'])


class ProviderFactoryTests(unittest.TestCase):
    def test_yahoo_choice_returns_yahoo_provider(self):
        with mock.patch.dict(os.environ, {'DATA_PROVIDER': 'yahoo'}):
            provider = data_providers.build_provider_from_env()
        self.assertIsInstance(provider, data_providers.YahooFinanceProvider)

    def test_ibkr_choice_returns_ibkr_provider(self):
        with mock.patch.dict(os.environ, {'DATA_PROVIDER': 'ibkr', 'IBKR_PORT': '4002'}):
            provider = data_providers.build_provider_from_env()
        self.assertIsInstance(provider, ibkr_provider.IBKRProvider)
        self.assertEqual(provider.port, 4002)

    def test_auto_choice_returns_edgar_yahoo_composite(self):
        import edgar_provider
        with mock.patch.dict(os.environ, {'DATA_PROVIDER': 'auto'}):
            provider = data_providers.build_provider_from_env()
        self.assertIsInstance(provider, data_providers.CompositeProvider)
        self.assertIsInstance(provider.edgar, edgar_provider.EdgarFundamentalsProvider)
        self.assertIsInstance(provider.market, data_providers.YahooFinanceProvider)

    def test_ibkr_yahoo_choice_returns_fallback_with_ibkr_primary(self):
        with mock.patch.dict(os.environ, {'DATA_PROVIDER': 'ibkr+yahoo'}):
            provider = data_providers.build_provider_from_env()
        self.assertIsInstance(provider, data_providers.FallbackProvider)
        self.assertIsInstance(provider.primary, ibkr_provider.IBKRProvider)
        self.assertIsInstance(provider.secondary, data_providers.YahooFinanceProvider)


if __name__ == '__main__':
    unittest.main()
