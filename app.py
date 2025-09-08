"""
Stock Portfolio Analysis Web Service
A Flask application to display fundamental and technical analysis for your stock portfolio
"""

from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend for web
import io
import base64
import json
import os
from datetime import datetime, timedelta
import numpy as np

app = Flask(__name__)

# Portfolio configuration file
PORTFOLIO_FILE = 'portfolio.json'

def load_portfolio():
    """Load portfolio from JSON file"""
    try:
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, 'r') as f:
                data = json.load(f)
                return data.get('portfolio', [])
        else:
            # Create default portfolio if file doesn't exist
            default_portfolio = []
            save_portfolio(default_portfolio)
            return default_portfolio
    except Exception as e:
        print(f"Error loading portfolio: {e}")
        return []

def save_portfolio(portfolio):
    """Save portfolio to JSON file"""
    try:
        data = {'portfolio': portfolio}
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        print(f"Error saving portfolio: {e}")
        return False

def get_portfolio_stocks():
    """Get list of stock symbols from portfolio"""
    portfolio = load_portfolio()
    return [stock['symbol'] for stock in portfolio]

def get_company_name_from_yf(symbol):
    """Get company name from Yahoo Finance"""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return info.get('longName', info.get('shortName', symbol))
    except:
        return symbol

# Global variables to cache data
data_cache = {}
fundamental_cache = {}
last_update = {}

def fetch_single_stock_data(symbol):
    """Fetch data for a single stock on demand"""
    global data_cache, fundamental_cache, last_update
    
    # Check if we have cached data for this stock and if it's still fresh (cache for 1 hour)
    if (symbol in data_cache and symbol in last_update and 
        (datetime.now() - last_update[symbol]).seconds < 3600):
        print(f"Using cached data for {symbol}")
        return
    
    print(f"Fetching fresh data for {symbol}...")
    
    try:
        # Fetch market data with explicit auto_adjust=True for consistent behavior
        # This adjusts prices for stock splits and dividends, providing cleaner technical analysis
        data = yf.download(symbol, period='300d', interval='1d', auto_adjust=True)
        data = data.dropna()
        
        # Get fundamental data
        ticker = yf.Ticker(symbol)
        info = ticker.info
        basic_metrics = extract_fundamental_metrics(symbol, info)
        enhanced_metrics = extract_enhanced_growth_metrics(ticker)
        
        # Combine basic and enhanced metrics
        if basic_metrics:
            basic_metrics.update(enhanced_metrics)
            fundamental_data = basic_metrics
        else:
            fundamental_data = enhanced_metrics
        
        # Calculate technical indicators
        data = calculate_technical_indicators(data)
        
        # Cache the data
        data_cache[symbol] = data
        fundamental_cache[symbol] = fundamental_data
        last_update[symbol] = datetime.now()
        
        print(f"Data fetch completed for {symbol}.")
        
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        # Store empty data to avoid repeated failed requests
        data_cache[symbol] = pd.DataFrame()
        fundamental_cache[symbol] = {}
        last_update[symbol] = datetime.now()

def fetch_stock_data():
    """Legacy function - kept for backward compatibility but now does nothing"""
    # This function is now obsolete since we load stocks on demand
    # Keeping it to avoid breaking existing code that might call it
    pass

def extract_fundamental_metrics(stock, info):
    """Extract key fundamental metrics from Yahoo Finance info"""
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
        metrics['sector'] = info.get('sector', None)
        metrics['industry'] = info.get('industry', None)
        metrics['country'] = info.get('country', None)
        metrics['employees'] = info.get('fullTimeEmployees', None)
        
        # Current price
        metrics['current_price'] = info.get('currentPrice', None)
        metrics['previous_close'] = info.get('previousClose', None)
        
    except Exception as e:
        print(f"Error extracting metrics for {stock}: {e}")
        return None
    
    return metrics

def calculate_technical_indicators(data):
    """Calculate technical indicators"""
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)
    
    if len(data) < 30:
        return data
    
    data = data.sort_index()
    
    # 30-Day Moving Average
    data['30_Moving_Avg'] = data['Close'].rolling(window=30, min_periods=1).mean()
    
    # Stochastic Oscillator
    high_14 = data['High'].rolling(window=14, min_periods=1).max()
    low_14 = data['Low'].rolling(window=14, min_periods=1).min()
    
    denominator = (high_14 - low_14)
    denominator = denominator.replace(0, 1e-10)
    data['%K'] = (data['Close'] - low_14) * 100 / denominator
    data['%K'] = data['%K'].rolling(window=3, min_periods=1).mean()
    data['%D'] = data['%K'].rolling(window=3, min_periods=1).mean()
    data['Smoothed_%D'] = data['%D'].rolling(window=30, min_periods=1).mean()
    
    # MACD
    data['12_EMA'] = data['Close'].ewm(span=12, adjust=False).mean()
    data['26_EMA'] = data['Close'].ewm(span=26, adjust=False).mean()
    data['MACD'] = data['12_EMA'] - data['26_EMA']
    data['Signal_Line'] = data['MACD'].ewm(span=9, adjust=False).mean()
    data['Smoothed_MACD'] = data['MACD'].rolling(window=15, min_periods=1).mean()
    data['Smoothed_Signal_Line'] = data['Signal_Line'].rolling(window=15, min_periods=1).mean()
    
    # Generate buy/sell signals
    data['buy_ma'] = (data['Close'] > data['30_Moving_Avg'])
    data['buy_stochastic'] = (data['Smoothed_%D'] > 30) & (data['Smoothed_%D'].shift(1) <= 30)
    data['buy_macd'] = (data['Smoothed_MACD'] > data['Smoothed_Signal_Line']) & (data['Smoothed_MACD'].shift(1) <= data['Smoothed_Signal_Line'].shift(1))
    
    data['sell_ma'] = (data['Close'] < data['30_Moving_Avg'])
    data['sell_stochastic'] = (data['Smoothed_%D'] < 30) & (data['Smoothed_%D'].shift(1) >= 30)
    data['sell_macd'] = (data['Smoothed_MACD'] < data['Smoothed_Signal_Line']) & (data['Smoothed_MACD'].shift(1) >= data['Smoothed_Signal_Line'].shift(1))
    
    window = 10
    buy_signal = (
        data['buy_ma'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool) &
        data['buy_stochastic'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool) &
        data['buy_macd'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool)
    )
    data['Buy_Signal'] = buy_signal
    
    sell_signal = (
        data['sell_ma'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool) &
        data['sell_stochastic'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool) &
        data['sell_macd'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool)
    )
    data['Sell_Signal'] = sell_signal
    
    return data

def calculate_fundamental_score(metrics):
    """Calculate fundamental analysis score"""
    if not metrics:
        return {'total_score': 0, 'category_scores': {}, 'ranking': 'POOR', 'recommendation': 'HOLD'}
    
    category_scores = {}
    
    # Valuation Score
    valuation_score = 0
    valuation_count = 0
    
    pe = metrics.get('pe_ratio')
    if pe and pe > 0:
        if pe < 15:
            valuation_score += 10
        elif pe < 25:
            valuation_score += 7
        elif pe < 35:
            valuation_score += 4
        else:
            valuation_score += 1
        valuation_count += 1
    
    pb = metrics.get('price_to_book')
    if pb and pb > 0:
        if pb < 1.5:
            valuation_score += 10
        elif pb < 3:
            valuation_score += 7
        elif pb < 5:
            valuation_score += 4
        else:
            valuation_score += 1
        valuation_count += 1
    
    category_scores['valuation'] = (valuation_score / max(valuation_count, 1)) if valuation_count > 0 else 0
    
    # Profitability Score
    profitability_score = 0
    profitability_count = 0
    
    roe = metrics.get('return_on_equity')
    if roe and roe > 0:
        if roe > 0.20:
            profitability_score += 10
        elif roe > 0.15:
            profitability_score += 8
        elif roe > 0.10:
            profitability_score += 6
        elif roe > 0.05:
            profitability_score += 3
        else:
            profitability_score += 1
        profitability_count += 1
    
    profit_margin = metrics.get('profit_margin')
    if profit_margin and profit_margin > 0:
        if profit_margin > 0.20:
            profitability_score += 10
        elif profit_margin > 0.15:
            profitability_score += 8
        elif profit_margin > 0.10:
            profitability_score += 6
        elif profit_margin > 0.05:
            profitability_score += 3
        else:
            profitability_score += 1
        profitability_count += 1
    
    category_scores['profitability'] = (profitability_score / max(profitability_count, 1)) if profitability_count > 0 else 0
    
    # Enhanced Growth Score with weighted components
    growth_components = {}
    
    # Revenue Growth (30% weight) - Enhanced YoY or fallback to basic
    revenue_growth = metrics.get('revenue_growth_yoy') or metrics.get('revenue_growth')
    if revenue_growth is not None:
        if revenue_growth > 0.20:
            growth_components['revenue'] = 10
        elif revenue_growth > 0.10:
            growth_components['revenue'] = 8
        elif revenue_growth > 0.05:
            growth_components['revenue'] = 6
        elif revenue_growth > 0:
            growth_components['revenue'] = 4
        else:
            growth_components['revenue'] = 1
    
    # Earnings Growth (20% weight) - Existing metric
    earnings_growth = metrics.get('earnings_growth')
    if earnings_growth is not None:
        if earnings_growth > 0.20:
            growth_components['earnings'] = 10
        elif earnings_growth > 0.10:
            growth_components['earnings'] = 8
        elif earnings_growth > 0.05:
            growth_components['earnings'] = 6
        elif earnings_growth > 0:
            growth_components['earnings'] = 4
        else:
            growth_components['earnings'] = 1
    
    # Operating Cash Flow Growth (15% weight) - NEW
    ocf_growth = metrics.get('ocf_growth_yoy')
    if ocf_growth is not None:
        if ocf_growth > 0.15:
            growth_components['ocf'] = 10
        elif ocf_growth > 0.05:
            growth_components['ocf'] = 8
        elif ocf_growth > 0:
            growth_components['ocf'] = 6
        elif ocf_growth > -0.05:
            growth_components['ocf'] = 3
        else:
            growth_components['ocf'] = 1
    
    # ROE Growth (35% weight) - NEW - Highest weight as requested
    roe_growth = metrics.get('roe_growth_yoy')
    if roe_growth is not None:
        if roe_growth > 0.10:  # >10% ROE improvement
            growth_components['roe'] = 10
        elif roe_growth > 0.05:  # 5-10% improvement
            growth_components['roe'] = 8
        elif roe_growth > 0:     # Any positive improvement
            growth_components['roe'] = 6
        elif roe_growth > -0.05: # Small decline acceptable
            growth_components['roe'] = 4
        else:                    # Significant ROE decline
            growth_components['roe'] = 1
    
    # Calculate weighted growth score
    weights = {
        'revenue': 0.30,  # 30%
        'earnings': 0.20, # 20%
        'ocf': 0.15,      # 15%
        'roe': 0.35       # 35%
    }
    
    weighted_score = 0
    total_weight = 0
    
    for component, score in growth_components.items():
        weight = weights.get(component, 0)
        weighted_score += score * weight
        total_weight += weight
    
    # Normalize to 0-10 scale
    category_scores['growth'] = (weighted_score / total_weight) if total_weight > 0 else 0
    
    # Financial Health Score
    health_score = 0
    health_count = 0
    
    debt_to_equity = metrics.get('debt_to_equity')
    if debt_to_equity is not None:
        if debt_to_equity < 0.3:
            health_score += 10
        elif debt_to_equity < 0.6:
            health_score += 8
        elif debt_to_equity < 1.0:
            health_score += 6
        elif debt_to_equity < 2.0:
            health_score += 3
        else:
            health_score += 1
        health_count += 1
    
    current_ratio = metrics.get('current_ratio')
    if current_ratio is not None:
        if 1.5 <= current_ratio <= 3:
            health_score += 10
        elif 1.2 <= current_ratio < 1.5 or 3 < current_ratio <= 4:
            health_score += 7
        elif 1.0 <= current_ratio < 1.2 or 4 < current_ratio <= 5:
            health_score += 4
        else:
            health_score += 1
        health_count += 1
    
    category_scores['financial_health'] = (health_score / max(health_count, 1)) if health_count > 0 else 0
    
    # Calculate total score
    total_score = sum(category_scores.values()) / len(category_scores)
    
    # Determine ranking and recommendation
    if total_score >= 8:
        ranking = 'EXCELLENT'
        recommendation = 'STRONG BUY'
    elif total_score >= 6:
        ranking = 'GOOD'
        recommendation = 'BUY'
    elif total_score >= 4:
        ranking = 'AVERAGE'
        recommendation = 'HOLD'
    elif total_score >= 2:
        ranking = 'BELOW_AVERAGE'
        recommendation = 'SELL'
    else:
        ranking = 'POOR'
        recommendation = 'STRONG SELL'
    
    return {
        'total_score': total_score,
        'category_scores': category_scores,
        'ranking': ranking,
        'recommendation': recommendation
    }

def get_technical_recommendation(stock_data):
    """Get technical analysis recommendation based on latest signals"""
    if stock_data.empty:
        return 'HOLD'
    
    latest_data = stock_data.tail(10)  # Look at last 10 days
    
    buy_signals = latest_data['Buy_Signal'].sum()
    sell_signals = latest_data['Sell_Signal'].sum()
    
    if buy_signals > sell_signals and buy_signals > 0:
        return 'BUY'
    elif sell_signals > buy_signals and sell_signals > 0:
        return 'SELL'
    else:
        return 'HOLD'

def create_technical_chart(stock, stock_data):
    """Create technical analysis chart"""
    fig, axs = plt.subplots(3, 1, figsize=(12, 10), facecolor='white')
    
    # Plot 1: Price and Moving Average
    axs[0].plot(stock_data.index, stock_data['Close'], label='Close Price', color='blue', alpha=0.7)
    axs[0].plot(stock_data.index, stock_data['30_Moving_Avg'], label='30-Day MA', color='red', linewidth=2)
    axs[0].set_title(f'{stock} - Price and Moving Average', fontsize=14, fontweight='bold')
    axs[0].set_ylabel('Price ($)')
    axs[0].legend()
    axs[0].grid(True, alpha=0.3)
    
    # Plot 2: Stochastic Oscillator
    axs[1].plot(stock_data.index, stock_data['Smoothed_%D'], label='Stochastic %D', color='purple', linewidth=2)
    axs[1].axhline(70, color='red', linestyle='--', alpha=0.7, label='Overbought (70)')
    axs[1].axhline(30, color='green', linestyle='--', alpha=0.7, label='Oversold (30)')
    axs[1].set_title(f'{stock} - Stochastic Oscillator', fontsize=14, fontweight='bold')
    axs[1].set_ylabel('Stochastic %D')
    axs[1].legend()
    axs[1].grid(True, alpha=0.3)
    axs[1].set_ylim(0, 100)
    
    # Plot 3: MACD
    axs[2].plot(stock_data.index, stock_data['Smoothed_MACD'], label='MACD', color='blue', linewidth=2)
    axs[2].plot(stock_data.index, stock_data['Smoothed_Signal_Line'], label='Signal Line', color='red', linewidth=2)
    axs[2].axhline(0, color='black', linestyle='-', alpha=0.3)
    axs[2].set_title(f'{stock} - MACD', fontsize=14, fontweight='bold')
    axs[2].set_ylabel('MACD')
    axs[2].set_xlabel('Date')
    axs[2].legend()
    axs[2].grid(True, alpha=0.3)
    
    plt.tight_layout()
    
    # Convert plot to base64 string
    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', dpi=100, bbox_inches='tight')
    img_buffer.seek(0)
    img_str = base64.b64encode(img_buffer.getvalue()).decode()
    plt.close()
    
    return img_str

def get_company_name(symbol):
    """Get company name from Yahoo Finance"""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return info.get('longName', info.get('shortName', symbol))
    except:
        return symbol

@app.route('/')
def index():
    """Main page"""
    # No longer fetch all stock data at startup - load on demand instead
    # Get current portfolio and sort alphabetically by symbol
    portfolio = load_portfolio()
    portfolio_sorted = sorted(portfolio, key=lambda x: x['symbol'])
    return render_template('index.html', stocks=portfolio_sorted)

@app.route('/api/stock/<symbol>')
def get_stock_data(symbol):
    """API endpoint to get stock data"""
    portfolio_stocks = get_portfolio_stocks()
    if symbol not in portfolio_stocks:
        return jsonify({'error': 'Stock not found in portfolio'}), 404
    
    # Fetch data for this specific stock on demand
    fetch_single_stock_data(symbol)
    
    # Get fundamental data
    fundamental_metrics = fundamental_cache.get(symbol, {})
    fundamental_score = calculate_fundamental_score(fundamental_metrics)
    
    # Get technical data
    stock_data = data_cache.get(symbol, pd.DataFrame())
    technical_recommendation = get_technical_recommendation(stock_data)
    
    # Combine recommendations
    fundamental_rec = fundamental_score.get('recommendation', 'HOLD')
    
    # Overall recommendation logic
    if fundamental_rec in ['STRONG BUY', 'BUY'] and technical_recommendation == 'BUY':
        overall_recommendation = 'STRONG BUY'
    elif fundamental_rec in ['STRONG BUY', 'BUY'] or technical_recommendation == 'BUY':
        overall_recommendation = 'BUY'
    elif fundamental_rec in ['STRONG SELL', 'SELL'] and technical_recommendation == 'SELL':
        overall_recommendation = 'STRONG SELL'
    elif fundamental_rec in ['STRONG SELL', 'SELL'] or technical_recommendation == 'SELL':
        overall_recommendation = 'SELL'
    else:
        overall_recommendation = 'HOLD'
    
    # Format fundamental metrics for display
    formatted_metrics = {}
    for key, value in fundamental_metrics.items():
        if value is not None:
            if key in ['market_cap', 'enterprise_value', 'total_debt', 'total_cash']:
                formatted_metrics[key] = f"${value:,.0f}"
            elif key in ['profit_margin', 'operating_margin', 'return_on_equity', 'return_on_assets', 
                        'revenue_growth', 'earnings_growth', 'dividend_yield', 'revenue_growth_yoy', 
                        'ocf_growth_yoy', 'roe_growth_yoy']:
                formatted_metrics[key] = f"{value:.1%}"
            elif key in ['pe_ratio', 'forward_pe', 'price_to_book', 'price_to_sales', 'debt_to_equity', 'current_ratio', 'beta']:
                formatted_metrics[key] = f"{value:.2f}"
            else:
                formatted_metrics[key] = value
        else:
            formatted_metrics[key] = 'N/A'
    
    # Get last update time for this specific stock
    stock_last_update = last_update.get(symbol)
    
    return jsonify({
        'symbol': symbol,
        'fundamental_metrics': formatted_metrics,
        'fundamental_score': fundamental_score,
        'technical_recommendation': technical_recommendation,
        'overall_recommendation': overall_recommendation,
        'last_updated': stock_last_update.strftime('%Y-%m-%d %H:%M:%S') if stock_last_update else 'N/A'
    })

@app.route('/api/chart/<symbol>')
def get_chart(symbol):
    """API endpoint to get technical analysis chart"""
    portfolio_stocks = get_portfolio_stocks()
    if symbol not in portfolio_stocks:
        return jsonify({'error': 'Stock not found in portfolio'}), 404
    
    # Ensure we have data for this stock
    fetch_single_stock_data(symbol)
    stock_data = data_cache.get(symbol, pd.DataFrame())
    
    if stock_data.empty:
        return jsonify({'error': 'No data available'}), 404
    
    chart_img = create_technical_chart(symbol, stock_data)
    
    return jsonify({'chart': chart_img})

@app.route('/api/portfolio', methods=['GET'])
def get_portfolio():
    """Get current portfolio sorted alphabetically"""
    portfolio = load_portfolio()
    portfolio_sorted = sorted(portfolio, key=lambda x: x['symbol'])
    return jsonify({'portfolio': portfolio_sorted})

@app.route('/api/portfolio/add', methods=['POST'])
def add_to_portfolio():
    """Add stock to portfolio"""
    data = request.get_json()
    symbol = data.get('symbol', '').upper().strip()
    
    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400
    
    # Validate stock exists
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or 'symbol' not in info:
            return jsonify({'error': 'Invalid stock symbol'}), 400
        
        company_name = info.get('longName', info.get('shortName', symbol))
    except:
        return jsonify({'error': 'Unable to fetch stock data'}), 400
    
    # Load current portfolio
    portfolio = load_portfolio()
    
    # Check if stock already exists
    for stock in portfolio:
        if stock['symbol'] == symbol:
            return jsonify({'error': 'Stock already in portfolio'}), 400
    
    # Add new stock
    new_stock = {
        'symbol': symbol,
        'name': company_name,
        'date_added': datetime.now().strftime('%Y-%m-%d')
    }
    portfolio.append(new_stock)
    
    # Save portfolio
    if save_portfolio(portfolio):
        # Clear cache for the specific stock only (if it exists)
        global data_cache, fundamental_cache, last_update
        if symbol in data_cache:
            del data_cache[symbol]
        if symbol in fundamental_cache:
            del fundamental_cache[symbol]
        if symbol in last_update:
            del last_update[symbol]
        
        return jsonify({'message': f'Successfully added {symbol} to portfolio', 'stock': new_stock})
    else:
        return jsonify({'error': 'Failed to save portfolio'}), 500

@app.route('/api/portfolio/remove', methods=['POST'])
def remove_from_portfolio():
    """Remove stock from portfolio"""
    data = request.get_json()
    symbol = data.get('symbol', '').upper().strip()
    
    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400
    
    # Load current portfolio
    portfolio = load_portfolio()
    
    # Find and remove stock
    original_length = len(portfolio)
    portfolio = [stock for stock in portfolio if stock['symbol'] != symbol]
    
    if len(portfolio) == original_length:
        return jsonify({'error': 'Stock not found in portfolio'}), 404
    
    # Save portfolio
    if save_portfolio(portfolio):
        # Clear cache for the specific stock only
        global data_cache, fundamental_cache, last_update
        if symbol in data_cache:
            del data_cache[symbol]
        if symbol in fundamental_cache:
            del fundamental_cache[symbol]
        if symbol in last_update:
            del last_update[symbol]
        
        return jsonify({'message': f'Successfully removed {symbol} from portfolio'})
    else:
        return jsonify({'error': 'Failed to save portfolio'}), 500

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
            print(f"Data validation failed: {message}")
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
        print(f"Error calculating YoY growth for {metric_name}: {e}")
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
        print(f"Error calculating ROE growth: {e}")
        return None

def extract_enhanced_growth_metrics(ticker):
    """Extract enhanced growth metrics from quarterly data"""
    try:
        # Get quarterly data
        quarterly_financials = ticker.quarterly_financials
        quarterly_cashflow = ticker.quarterly_cashflow
        quarterly_balance_sheet = ticker.quarterly_balance_sheet
        
        enhanced_metrics = {}
        
        # 1. Enhanced Revenue Growth (YoY quarterly)
        revenue_growth_yoy = calculate_yoy_growth(quarterly_financials, 'Total Revenue')
        enhanced_metrics['revenue_growth_yoy'] = revenue_growth_yoy
        
        # 2. Operating Cash Flow Growth
        ocf_growth_yoy = calculate_yoy_growth(quarterly_cashflow, 'Operating Cash Flow')
        enhanced_metrics['ocf_growth_yoy'] = ocf_growth_yoy
        
        # 3. ROE Growth Rate
        roe_growth_yoy = calculate_roe_growth(quarterly_financials, quarterly_balance_sheet)
        enhanced_metrics['roe_growth_yoy'] = roe_growth_yoy
        
        return enhanced_metrics
        
    except Exception as e:
        print(f"Error extracting enhanced growth metrics: {e}")
        return {}

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
