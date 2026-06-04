# Option Chain Viewer

An interactive web application built with Streamlit for visualization of stock options chains.

## Overview
Option chains are a window into market sentiment. They allow traders to see how retail investors and institutional players are positioning themselves for a specific expiration date.

Standard option chain viewers can often be cluttered or difficult to read across the "Strike" divide. This viewer is designed to solve that by offering alignment options and visualization tools that highlight the relationship between Calls and Puts.

[Example Yahoo Finance NVidia Option Chain](https://finance.yahoo.com/quote/NVDA/options)

## Key Features

- **Alignment:** This app allows you to align columns and strike prices symmetrically.
- **Visual Comparison Tools:**
    - **Proportional Bars:** Integrated horizontal bars within cells to compare Volume and Open Interest between Calls and Puts at a glance.
    - **Color Heatmaps:** Percentage changes are color-coded (Green for increases, Red for decreases) using gradients to represent the magnitude of the move.
- **ATM-Centric View:** Automatically calculates the At-The-Money (ATM) strike and allows you to trim the chain to a specific radius, keeping the most relevant data front and center.
- **Flexible Layouts:** Includes a "Flip Strikes" mode to visualize the options chain by distance from the strike price.

## Data & Technical Details

- **Source:** Data is pulled from the **Yahoo Finance API** (`yfinance`).
- **Reliability:** Please note that since this uses a free API, data may occasionally be delayed or inconsistent compared to paid professional feeds.
- **Market Hours:** When the markets are closed, Open Interest (OI) and intraday changes may not update or may appear as zero.

## Setup and Usage

To run the application:
```bash
streamlit run streamlitapp.py
```

1. Enter a **Ticker** (e.g., NVDA, AAPL).
2. Optionally provide an **Expiration Date**.
3. Adjust the **Trim** radius to see more or fewer strikes around the current price.
4. Toggle **Flip Put Strikes** for a side-by-side comparison of OTM/ITM data.

## Disclaimer

**NOT Financial Advice!**

Market positioning (Open Interest and Volume) indicates where money is being placed, but it **does not** guarantee the stock price will follow those trends. Always perform your own due diligence.