// ==============================================
// FILE: include/analytics_engine.h
// Live Tick Analytics Engine Header
// ==============================================

#pragma once

#include <string>

namespace Analytics
{
    // ------------------------------------------
    // Process incoming live tick
    //
    // Inputs:
    //   symbol = NSE contract symbol
    //   ltp    = last traded price
    //   bid1   = best bid
    //   ask1   = best ask
    //
    // Returns:
    //   JSON string with IV / Greeks
    //   Empty string if ignored / insufficient data
    //
    // Example:
    // {
    //   "symbol":"NIFTY26MAY24500CE",
    //   "underlying":"NIFTY",
    //   "ltp":121.35,
    //   "iv":18.72,
    //   "delta":0.51,
    //   "gamma":0.00032,
    //   "theta":-10.42,
    //   "vega":27.10
    // }
    // ------------------------------------------
    std::string process_tick(
        const std::string& symbol,
        double ltp,
        double bid1,
        double ask1
    );

    // ------------------------------------------
    // Update underlying asset price directly
    // (Used for Spot Indices like SENSEX/NIFTY)
    // ------------------------------------------
    void update_underlying(const std::string& symbol, double price);
}