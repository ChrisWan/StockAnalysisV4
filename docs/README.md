# Stock Portfolio Analysis Web Service

A local web service that provides comprehensive fundamental and technical analysis for your stock portfolio.

## Features

- **Portfolio Overview**: Dropdown menu with all stocks in your portfolio
- **Dynamic Portfolio Management**: 
  - Toggle button to show/hide portfolio management interface
  - Add new stocks with real-time validation
  - Remove stocks with confirmation dialog
  - Automatic dropdown refresh (no page reload needed)
- **Fundamental Analysis**: 
  - Key financial metrics (P/E ratio, ROE, profit margins, etc.)
  - Fundamental scoring system (1-10 scale)
  - Category breakdowns (Valuation, Profitability, Growth, Financial Health)
- **Technical Analysis**:
  - Moving averages
  - MACD indicators
  - Stochastic oscillator
  - Interactive charts
- **Buy/Sell Recommendations**:
  - Combined fundamental and technical analysis
  - Clear recommendation badges (Strong Buy, Buy, Hold, Sell, Strong Sell)
- **Persistent Storage**: Portfolio saved in `portfolio.json` file

## Portfolio Stocks

Your current portfolio includes:
NVDA, TSM, CSCO, META, BLBD, CLS, CMG, MFC, OKTA, PEP, ANF, ALL, GOOG, AMZN, AAPL, BRK-B, UBER, SYF, AGX, EAT

## Quick Start

### Option 1: Using the Batch File (Easiest)
1. Double-click `start_service.bat`
2. Wait for the installation and startup to complete
3. Open your browser and go to `http://localhost:5000`

### Option 2: Manual Setup
1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Start the Flask server:
   ```
   python app.py
   ```

3. Open your browser and navigate to:
   ```
   http://localhost:5000
   ```

## Usage

1. **Select a Stock**: Use the dropdown menu to choose any stock from your portfolio
2. **Manage Portfolio**: Click "Manage Portfolio" button to show/hide portfolio management tools
3. **Add Stocks**: Enter ticker symbol and click Add (validates automatically)
4. **Remove Stocks**: Select from dropdown and click Remove (with confirmation)
5. **View Analysis**: The page will automatically load:
   - Fundamental analysis metrics
   - Scoring and recommendations
   - Technical analysis charts
6. **Interpret Results**:
   - **Green badges** = Buy recommendations
   - **Red badges** = Sell recommendations  
   - **Orange badges** = Hold recommendations
   - Scores are rated from 1-10 (10 being excellent)

## Technical Details

### Data Sources
- **Market Data**: Yahoo Finance (yfinance library)
- **Update Frequency**: Data is cached for 1 hour to avoid excessive API calls

### Fundamental Analysis Scoring
- **Valuation**: P/E ratio, Price/Book ratio
- **Profitability**: ROE, Profit margins
- **Growth**: Revenue growth, Earnings growth  
- **Financial Health**: Debt ratios, Current ratio

### Technical Indicators
- **30-Day Moving Average**: Trend direction
- **Stochastic Oscillator**: Momentum (overbought/oversold levels)
- **MACD**: Trend changes and momentum shifts

### Recommendation Logic
- **Overall Recommendation**: Combines fundamental and technical analysis
- **Strong Buy**: Excellent fundamentals + bullish technical signals
- **Buy**: Good fundamentals OR bullish technical signals
- **Hold**: Average fundamentals and neutral technical signals
- **Sell**: Poor fundamentals OR bearish technical signals
- **Strong Sell**: Poor fundamentals + bearish technical signals

## Troubleshooting

### Common Issues
1. **Port 5000 already in use**: Change the port in `app.py` (line: `app.run(debug=True, host='0.0.0.0', port=5000)`)
2. **Missing dependencies**: Run `pip install -r requirements.txt`
3. **Data not loading**: Check your internet connection (required for Yahoo Finance API)

### Browser Compatibility
- Chrome, Firefox, Safari, Edge (modern versions)
- Mobile browsers supported

## Customization

### Adding New Stocks
Edit the `STOCKS` list in `app.py`:
```python
STOCKS = ['NVDA', 'TSM', 'YOUR_NEW_STOCK', ...]
```

### Modifying Scoring Criteria
Adjust the scoring logic in the `calculate_fundamental_score()` function in `app.py`.

### Changing Chart Appearance
Modify the `create_technical_chart()` function to customize colors, styles, and layouts.

## Security Note
This service runs locally on your machine and does not send data to external servers (except for fetching stock data from Yahoo Finance).

## Support
This web service is based on the analysis from your existing Jupyter notebook and provides the same calculations in an easy-to-use web interface.
