# Portfolio Management Guide

## 🎯 **Enhanced Stock Portfolio Analysis with Portfolio Management**

Your stock analysis web service now includes complete portfolio management functionality with persistent storage.

---

## 📁 **Portfolio Storage**

### **portfolio.json**
- All your stocks are now saved in `portfolio.json`
- Includes: ticker symbol, company name, and date added
- Automatically backed up when changes are made
- Portable - you can share or backup this file

### **Structure Example:**
```json
{
    "portfolio": [
        {
            "symbol": "NVDA",
            "name": "NVIDIA Corporation", 
            "date_added": "2024-01-01"
        },
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "date_added": "2024-12-13"
        }
    ]
}
```

---

## ✨ **New Features**

### **1. Add Stocks to Portfolio**
- **Location**: Top section of the webpage
- **How to use**:
  1. Enter any valid stock ticker (e.g., TSLA, MSFT, AMZN)
  2. Click "Add" button or press Enter
  3. System validates the stock and fetches company name automatically
  4. Stock is added to your portfolio and dropdown updates

### **2. Remove Stocks from Portfolio**
- **Location**: Next to the Add section
- **How to use**:
  1. Select a stock from the dropdown
  2. Click "Remove" button
  3. Confirm the removal
  4. Stock is removed from portfolio and dropdown updates

### **3. Dynamic Company Names**
- Company names are fetched automatically from Yahoo Finance
- Displayed in dropdown as: "AAPL - Apple Inc."
- Fallback to hardcoded names if Yahoo Finance is unavailable

---

## 🚀 **How to Use**

### **Starting the Service**
```cmd
# Option 1: Use the batch file
start_service.bat

# Option 2: Manual start
python app.py
```

### **Accessing the Web Interface**
- Open browser to: `http://localhost:5000`
- The interface will show your current portfolio in the dropdown

---

## 📋 **API Endpoints**

### **Portfolio Management**
- `GET /api/portfolio` - Get current portfolio
- `POST /api/portfolio/add` - Add stock to portfolio
- `POST /api/portfolio/remove` - Remove stock from portfolio

### **Stock Analysis** 
- `GET /api/stock/<symbol>` - Get fundamental and technical analysis
- `GET /api/chart/<symbol>` - Get technical analysis charts

---

## 🔧 **Technical Details**

### **Data Persistence**
- Portfolio data is saved to `portfolio.json` 
- Cache is automatically cleared when portfolio changes
- Fresh data is fetched for new stocks

### **Validation**
- Stock symbols are validated against Yahoo Finance
- Duplicate stocks are prevented
- Invalid symbols are rejected with clear error messages

### **Error Handling**
- Network issues are handled gracefully
- Clear error messages for user feedback
- Fallback mechanisms for company name fetching

---

## 🎨 **User Interface**

### **Portfolio Management Section**
- Clean, modern interface with Bootstrap styling
- Color-coded buttons (Green for Add, Red for Remove)
- Loading spinners during operations
- Instant feedback with success/error messages

### **Responsive Design**
- Works on desktop and mobile devices
- Grid layout adapts to screen size
- Touch-friendly buttons and inputs

---

## 📊 **Benefits**

### **Flexibility**
- Add any publicly traded stock
- Remove stocks you no longer track
- Portfolio persists between sessions

### **Data Integrity**
- Automatic validation prevents invalid entries
- Company names are always up-to-date
- Consistent data format

### **User Experience**
- No need to edit code to change portfolio
- Real-time updates to the interface
- Clear visual feedback for all operations

---

## 🔄 **Migration from Old Version**

If you were using the old hardcoded stock list:
1. Your existing stocks are automatically migrated to `portfolio.json`
2. All analysis functionality remains the same
3. You can now add/remove stocks through the web interface

---

## 🛠 **Troubleshooting**

### **Common Issues**

1. **"Stock not found" error**
   - Check if ticker symbol is correct
   - Ensure you have internet connection
   - Try a different, well-known stock symbol

2. **Portfolio not saving**
   - Check write permissions in the application folder
   - Ensure `portfolio.json` is not read-only

3. **Company names not loading**
   - This is usually due to network issues
   - Names will fall back to ticker symbols
   - Try refreshing the page

### **Backup Your Portfolio**
- Simply copy `portfolio.json` to backup your portfolio
- Restore by copying the file back to the application folder

---

## 🎯 **Example Workflow**

1. **Start the service**: `python app.py`
2. **Open browser**: `http://localhost:5000`
3. **Add a new stock**: Enter "MSFT" → Click Add
4. **Select stock**: Choose "MSFT - Microsoft Corporation" from dropdown
5. **View analysis**: See fundamental metrics and technical charts
6. **Remove if needed**: Select stock in remove section → Click Remove

---

## 💡 **Pro Tips**

- **Keyboard Shortcut**: Press Enter in the "Add Stock" field to quickly add stocks
- **Batch Operations**: Add multiple stocks by repeating the add process
- **Portfolio Backup**: Regularly backup your `portfolio.json` file
- **Stock Research**: Use the analysis to research stocks before adding them permanently

Your portfolio management system is now fully functional and ready to use! 🚀
