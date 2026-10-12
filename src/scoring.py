"""Fundamental analysis, metric scoring and the recommendation scorecard."""

import logging

from config import (
    DEFAULT_SCORECARD_WEIGHTS,
    NEGATIVE_VALUE_PENALTY_SCORE,
    SCORING_PROFILES,
    SECTOR_SCORECARD_WEIGHTS,
)
from benchmarks import get_sector_benchmarks

LOGGER = logging.getLogger(__name__)


def normalize_weights(weights, fallback):
    """Normalize a weight mapping so it sums to 1."""
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return fallback.copy()

    return {
        key: round(value / total_weight, 4)
        for key, value in weights.items()
    }


def get_scorecard_weights(sector):
    """Return normalized scorecard weights for a sector."""
    weights = SECTOR_SCORECARD_WEIGHTS.get(sector, DEFAULT_SCORECARD_WEIGHTS).copy()
    return normalize_weights(weights, DEFAULT_SCORECARD_WEIGHTS)


def get_scoring_profile(profile_name):
    """Return scoring profile metadata and normalized weights."""
    profile_key = (profile_name or 'balanced').strip().lower()
    if profile_key not in SCORING_PROFILES:
        profile_key = 'balanced'

    profile = SCORING_PROFILES[profile_key]
    return {
        'key': profile_key,
        'label': profile['label'],
        'weights': normalize_weights(profile['weights'], SCORING_PROFILES['balanced']['weights']),
    }


def combine_weight_profiles(sector, profile_name):
    """Blend sector-specific weights with a user-selected scoring profile."""
    sector_weights = get_scorecard_weights(sector)
    scoring_profile = get_scoring_profile(profile_name)
    if scoring_profile['key'] == 'balanced':
        return sector_weights, scoring_profile

    combined = {
        key: sector_weights[key] * 0.5 + scoring_profile['weights'][key] * 0.5
        for key in DEFAULT_SCORECARD_WEIGHTS.keys()
    }
    return normalize_weights(combined, DEFAULT_SCORECARD_WEIGHTS), scoring_profile


def score_metric_relative_to_sector(value, metric_type, sector, higher_is_better=True):
    """Graded 0-10 score for a metric compared to its sector median.

    Returns None when the value is unavailable (metric excluded from
    scoring). Negative values receive an explicit penalty score instead of
    being excluded: a negative P/E means negative earnings and a negative
    ROE or margin means the company is losing money — both are signal, not
    missing data.
    """
    if value is None:
        return None
    if value <= 0:
        return NEGATIVE_VALUE_PENALTY_SCORE

    benchmarks = get_sector_benchmarks(sector)

    # Get median value for this metric type
    median_key = f"{metric_type}_median"

    # Handle legacy naming for some metrics
    if metric_type == "pb" and median_key not in benchmarks:
        median_key = "price_to_book_median"
    elif metric_type == "ps" and median_key not in benchmarks:
        median_key = "price_to_sales_median"
    elif metric_type == "roe" and median_key not in benchmarks:
        median_key = "return_on_equity_median"

    if median_key not in benchmarks:
        # Fallback to absolute scoring if metric not in benchmarks
        return score_metric_absolute(value, metric_type)

    median = benchmarks[median_key]

    # Calculate percentage difference from median
    if higher_is_better:
        # For metrics like ROE, profit margin (higher = better)
        if value >= median * 1.5:      # 50% better than median
            return 10
        elif value >= median * 1.25:   # 25% better than median
            return 9
        elif value >= median * 1.1:    # 10% better than median
            return 8
        elif value >= median:          # At or above median
            return 7
        elif value >= median * 0.9:    # Within 10% of median
            return 6
        elif value >= median * 0.75:   # 25% below median
            return 4
        elif value >= median * 0.5:    # 50% below median
            return 2
        else:                          # More than 50% below median
            return 1
    else:
        # For metrics like P/E, P/B (lower = better)
        if value <= median * 0.67:     # 33% better than median (much lower)
            return 10
        elif value <= median * 0.8:    # 20% better than median
            return 9
        elif value <= median * 0.9:    # 10% better than median
            return 8
        elif value <= median:          # At or below median
            return 7
        elif value <= median * 1.1:    # Within 10% of median
            return 6
        elif value <= median * 1.25:   # 25% above median
            return 4
        elif value <= median * 1.5:    # 50% above median
            return 2
        else:                          # More than 50% above median
            return 1


def score_metric_absolute(value, metric_type):
    """Fallback absolute scoring for metrics without sector benchmarks"""
    if metric_type == "pe_ratio":
        if value < 15: return 10
        elif value < 25: return 7
        elif value < 35: return 4
        else: return 1
    elif metric_type == "price_to_book":
        if value < 1.5: return 10
        elif value < 3: return 7
        elif value < 5: return 4
        else: return 1
    elif metric_type == "return_on_equity":
        if value > 0.20: return 10
        elif value > 0.15: return 8
        elif value > 0.10: return 6
        elif value > 0.05: return 3
        else: return 1
    elif metric_type == "profit_margin":
        if value > 0.20: return 10
        elif value > 0.15: return 8
        elif value > 0.10: return 6
        elif value > 0.05: return 3
        else: return 1
    else:
        return 5  # Default middle score


def _build_sector_comparison_entry(label, value, median, metric_type, sector,
                                   higher_is_better, as_percent=False):
    """Build one sector comparison entry with a graded 0-10 score.

    Metrics with a missing value or missing sector median return None and
    are excluded. Negative values are kept, marked, and penalized.
    """
    if value is None or median is None:
        return None

    def fmt(number):
        return f"{round(number * 100, 1)}%" if as_percent else round(number, 2)

    score = score_metric_relative_to_sector(value, metric_type, sector, higher_is_better)
    entry = {
        'label': label,
        'company_value': fmt(value),
        'sector_median': fmt(median),
        'comparison': 'Higher is Better' if higher_is_better else 'Lower is Better',
        'better_than_sector': (value > median) if higher_is_better else (0 < value < median),
        'score': score,
    }
    if value <= 0:
        entry['note'] = 'Negative value - penalty score applied.'
    return entry


def calculate_fundamental_analysis(metrics):
    """Calculate fundamental analysis with clear sector comparison and growth metrics"""
    if not metrics:
        return {
            'sector_comparison': {},
            'growth_analysis': {},
            'company_info': {}
        }

    # Get company sector for comparison
    sector = metrics.get('sector', 'Technology')  # Default to Technology if no sector
    sector_benchmarks = get_sector_benchmarks(sector)
    LOGGER.info("Analyzing %s sector comparison", sector)

    # Part 1: Sector Comparison - Company vs Peers, graded 0-10 per metric
    comparison_specs = [
        ('pe_ratio', 'Price-to-Earnings Ratio', 'pe_ratio', False, False),
        ('price_to_book', 'Price-to-Book Ratio', 'price_to_book', False, False),
        ('price_to_sales', 'Price-to-Sales Ratio', 'price_to_sales', False, False),
        ('return_on_equity', 'Return on Equity', 'return_on_equity', True, True),
        ('profit_margin', 'Profit Margin', 'profit_margin', True, True),
    ]

    sector_comparison = {
        'sector_name': sector,
        'metrics': {}
    }
    for metric_key, label, metric_type, higher_is_better, as_percent in comparison_specs:
        entry = _build_sector_comparison_entry(
            label,
            metrics.get(metric_key),
            sector_benchmarks.get(f'{metric_type}_median'),
            metric_type,
            sector,
            higher_is_better,
            as_percent=as_percent,
        )
        if entry:
            sector_comparison['metrics'][metric_key] = entry

    # Part 2: Growth Analysis - Company's Own Growth Metrics
    growth_analysis = {
        'metrics': {}
    }

    # Revenue Growth
    revenue_growth = metrics.get('revenue_growth_yoy') or metrics.get('revenue_growth')
    if revenue_growth is not None:
        growth_analysis['metrics']['revenue_growth'] = {
            'label': 'Revenue Growth',
            'value': f"{round(revenue_growth * 100, 1)}%",
            'raw_value': revenue_growth,
            'interpretation': get_growth_interpretation(revenue_growth, 'revenue')
        }

    # Earnings Growth
    earnings_growth = metrics.get('earnings_growth')
    if earnings_growth is not None:
        growth_analysis['metrics']['earnings_growth'] = {
            'label': 'Earnings Growth',
            'value': f"{round(earnings_growth * 100, 1)}%",
            'raw_value': earnings_growth,
            'interpretation': get_growth_interpretation(earnings_growth, 'earnings')
        }

    # Operating Cash Flow Growth
    ocf_growth = metrics.get('ocf_growth_yoy')
    if ocf_growth is not None:
        growth_analysis['metrics']['ocf_growth'] = {
            'label': 'Operating Cash Flow Growth',
            'value': f"{round(ocf_growth * 100, 1)}%",
            'raw_value': ocf_growth,
            'interpretation': get_growth_interpretation(ocf_growth, 'ocf')
        }

    # ROE Growth
    roe_growth = metrics.get('roe_growth_yoy')
    if roe_growth is not None:
        growth_analysis['metrics']['roe_growth'] = {
            'label': 'ROE Growth',
            'value': f"{round(roe_growth * 100, 1)}%",
            'raw_value': roe_growth,
            'interpretation': get_growth_interpretation(roe_growth, 'roe')
        }

    # Company Information
    company_info = {
        'name': metrics.get('company_name', 'Unknown'),
        'sector': sector,
        'industry': metrics.get('industry', 'Unknown'),
        'current_price': metrics.get('current_price'),
        'market_cap': metrics.get('market_cap')
    }

    # Financial Health Metrics (simple display)
    financial_health = {}

    debt_to_equity = metrics.get('debt_to_equity')
    if debt_to_equity is not None:
        financial_health['debt_to_equity'] = {
            'label': 'Debt-to-Equity Ratio',
            'value': round(debt_to_equity, 2),
            'interpretation': get_debt_interpretation(debt_to_equity)
        }

    current_ratio = metrics.get('current_ratio')
    if current_ratio is not None:
        financial_health['current_ratio'] = {
            'label': 'Current Ratio',
            'value': round(current_ratio, 2),
            'interpretation': get_liquidity_interpretation(current_ratio)
        }

    return {
        'sector_comparison': sector_comparison,
        'growth_analysis': growth_analysis,
        'financial_health': financial_health,
        'company_info': company_info
    }


def get_growth_interpretation(growth_rate, metric_type):
    """Get simple interpretation for growth rates"""
    if growth_rate is None:
        return "Data not available"

    if metric_type in ['revenue', 'earnings']:
        if growth_rate >= 0.20:
            return "Excellent - Strong growth"
        elif growth_rate >= 0.10:
            return "Good - Solid growth"
        elif growth_rate >= 0.05:
            return "Moderate - Steady growth"
        elif growth_rate >= 0:
            return "Slow - Minimal growth"
        else:
            return "Declining - Negative growth"

    elif metric_type == 'ocf':
        if growth_rate >= 0.15:
            return "Excellent - Strong cash generation"
        elif growth_rate >= 0.05:
            return "Good - Healthy cash flow"
        elif growth_rate >= 0:
            return "Stable - Positive cash flow"
        elif growth_rate >= -0.05:
            return "Concerning - Slight decline"
        else:
            return "Weak - Declining cash flow"

    elif metric_type == 'roe':
        if growth_rate >= 0.10:
            return "Excellent - Improving efficiency"
        elif growth_rate >= 0.05:
            return "Good - Better returns"
        elif growth_rate >= 0:
            return "Stable - Maintaining efficiency"
        elif growth_rate >= -0.05:
            return "Watch - Slight decline"
        else:
            return "Concerning - Declining efficiency"

    return "Unknown"


def get_debt_interpretation(debt_to_equity):
    """Get interpretation for debt-to-equity ratio"""
    if debt_to_equity is None:
        return "Data not available"

    if 0.3 <= debt_to_equity <= 1.2:
        return "Optimal - Balanced capital structure"
    elif 0.1 <= debt_to_equity < 0.3:
        return "Conservative - Low debt usage"
    elif 1.2 < debt_to_equity <= 1.8:
        return "Aggressive - Higher leverage"
    elif debt_to_equity < 0.1:
        return "Very Conservative - Minimal debt"
    elif 1.8 < debt_to_equity <= 2.5:
        return "High Leverage - Monitor closely"
    else:
        return "Very High - Potential concern"


def get_liquidity_interpretation(current_ratio):
    """Get interpretation for current ratio"""
    if current_ratio is None:
        return "Data not available"

    if 1.5 <= current_ratio <= 3.0:
        return "Good - Healthy liquidity"
    elif 1.2 <= current_ratio < 1.5:
        return "Adequate - Sufficient liquidity"
    elif 3.0 < current_ratio <= 4.0:
        return "High - Excess cash (could be more efficient)"
    elif 1.0 <= current_ratio < 1.2:
        return "Tight - Monitor cash flow"
    elif current_ratio > 4.0:
        return "Very High - Inefficient cash usage"
    else:
        return "Concerning - Liquidity issues"


def calculate_component_ratio(positive_count, total_count):
    """Convert a positive/total count into a 0-10 component score."""
    if total_count <= 0:
        return 5.0

    return round((positive_count / total_count) * 10, 1)


def build_recommendation_scorecard(fundamental_analysis, technical_recommendation, profile_name='balanced', technical_summary=None):
    """Build an explicit scorecard for the overall recommendation."""
    sector_name = fundamental_analysis.get('sector_comparison', {}).get('sector_name', 'Unknown')
    sector_metrics = fundamental_analysis.get('sector_comparison', {}).get('metrics', {})
    growth_metrics = fundamental_analysis.get('growth_analysis', {}).get('metrics', {})
    weights, scoring_profile = combine_weight_profiles(sector_name, profile_name)

    def metric_label(metric_key, metric_data):
        label = metric_data.get('label') if isinstance(metric_data, dict) else None
        if label:
            return label
        return str(metric_key).replace('_', ' ').title()

    sector_positive_labels = []
    sector_negative_labels = []
    for metric_key, metric_data in sector_metrics.items():
        label = metric_label(metric_key, metric_data)
        if metric_data.get('better_than_sector', False):
            sector_positive_labels.append(label)
        else:
            sector_negative_labels.append(label)

    growth_positive_labels = []
    growth_negative_labels = []
    for metric_key, metric_data in growth_metrics.items():
        label = metric_label(metric_key, metric_data)
        if metric_data.get('raw_value', 0) > 0.05:
            growth_positive_labels.append(label)
        else:
            growth_negative_labels.append(label)

    sector_positives = sum(
        1 for metric_data in sector_metrics.values()
        if metric_data.get('better_than_sector', False)
    )
    growth_positives = sum(
        1 for metric_data in growth_metrics.values()
        if metric_data.get('raw_value', 0) > 0.05
    )

    # Prefer the graded 0-10 metric scores; fall back to the binary
    # better/worse ratio when no graded scores are available.
    graded_scores = {
        metric_label(metric_key, metric_data): metric_data['score']
        for metric_key, metric_data in sector_metrics.items()
        if isinstance(metric_data, dict) and metric_data.get('score') is not None
    }
    if graded_scores:
        sector_score = round(sum(graded_scores.values()) / len(graded_scores), 1)
        sector_scoring_basis = 'graded'
        sector_signal_rule = (
            'Each metric is scored 0-10 against the sector median; '
            'the component score is the average. Negative values receive a penalty score.'
        )
    else:
        sector_score = calculate_component_ratio(sector_positives, len(sector_metrics))
        sector_scoring_basis = 'binary'
        sector_signal_rule = 'Positive when the company beats the sector median for that metric.'

    growth_score = calculate_component_ratio(growth_positives, len(growth_metrics))
    technical_summary = technical_summary or {
        'signal': technical_recommendation,
        'score': {'BUY': 8.0, 'HOLD': 5.0, 'SELL': 2.0}.get(technical_recommendation, 5.0),
        'buy_signals': 0,
        'sell_signals': 0,
        'signal_window_days': 0,
        'trend_confirmation': False,
        'latest_buy_age': None,
        'latest_sell_age': None,
        'setup_state': 'Neutral setup',
        'distance_to_buy': None,
        'distance_to_sell': None,
        'details': {}
    }
    technical_score = technical_summary.get('score', 5.0)
    technical_summary.setdefault('gap_to_buy_threshold', round(max(6.8 - technical_score, 0.0), 1))
    technical_summary.setdefault('gap_to_sell_threshold', round(max(technical_score - 3.4, 0.0), 1))
    technical_summary.setdefault('setup_state', 'Neutral setup')

    weighted_score = round(
        sector_score * weights['sector_alignment'] +
        growth_score * weights['growth_quality'] +
        technical_score * weights['technical_momentum'],
        1
    )

    if weighted_score >= 8.0:
        label = 'STRONG BUY'
    elif weighted_score >= 6.5:
        label = 'BUY'
    elif weighted_score >= 4.5:
        label = 'HOLD'
    elif weighted_score >= 2.5:
        label = 'SELL'
    else:
        label = 'STRONG SELL'

    return {
        'label': label,
        'total_score': weighted_score,
        'max_score': 10,
        'sector': sector_name,
        'profile': scoring_profile,
        'components': {
            'sector_alignment': {
                'label': 'Sector Alignment',
                'score': sector_score,
                'weight': weights['sector_alignment'],
                'scoring_basis': sector_scoring_basis,
                'metric_scores': graded_scores,
                'positive_signals': sector_positives,
                'total_signals': len(sector_metrics),
                'signal_rule': sector_signal_rule,
                'positive_signal_labels': sector_positive_labels,
                'negative_signal_labels': sector_negative_labels
            },
            'growth_quality': {
                'label': 'Growth Quality',
                'score': growth_score,
                'weight': weights['growth_quality'],
                'positive_signals': growth_positives,
                'total_signals': len(growth_metrics),
                'signal_rule': 'Positive when growth is above 5% for that metric.',
                'positive_signal_labels': growth_positive_labels,
                'negative_signal_labels': growth_negative_labels
            },
            'technical_momentum': {
                'label': 'Technical Momentum',
                'score': technical_score,
                'weight': weights['technical_momentum'],
                'signal': technical_summary.get('signal', technical_recommendation),
                'buy_signals': technical_summary.get('buy_signals', 0),
                'sell_signals': technical_summary.get('sell_signals', 0),
                'signal_window_days': technical_summary.get('signal_window_days', 0),
                'trend_confirmation': technical_summary.get('trend_confirmation', False),
                'latest_buy_age': technical_summary.get('latest_buy_age'),
                'latest_sell_age': technical_summary.get('latest_sell_age'),
                'setup_state': technical_summary.get('setup_state', 'Neutral setup'),
                'gap_to_buy_threshold': technical_summary.get('gap_to_buy_threshold'),
                'gap_to_sell_threshold': technical_summary.get('gap_to_sell_threshold'),
                'details': technical_summary.get('details', {})
            }
        }
    }
