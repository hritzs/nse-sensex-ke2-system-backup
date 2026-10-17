// ==============================================
// FILE: src/simulation.cpp
// Hedging & Rolling Simulation Engine
// ==============================================

#include <iostream>
#include <iomanip>
#include <vector>
#include <cmath>
#include <cstdlib> // For srand, rand
#include <ctime>   // For time

#include "bs.h"
#include "greeks.h"

// Represents the state of the market at a point in time
struct MarketState {
    double underlying_price; // S
    double rate;             // r
    double vol;              // sigma
    double time_to_expiry;   // T
};

// Represents a single option position
struct OptionPosition {
    double strike;
    int quantity; // > 0 for long, < 0 for short
    bool is_call;
};

// Manages the complete portfolio: option, underlying hedge, and cash
struct Portfolio {
    OptionPosition option;
    double underlying_hedge_shares = 0.0;
    double cash = 0.0;

    // Calculate the total market value of the portfolio
    double market_value(const MarketState& market) const {
        double option_price =
            option.is_call
            ? BS::call_price(market.underlying_price, option.strike, market.rate, market.vol, market.time_to_expiry)
            : BS::put_price(market.underlying_price, option.strike, market.rate, market.vol, market.time_to_expiry);

        if (std::isnan(option_price)) {
            option_price = 0.0; // Handle expired options
        }

        double option_value = option.quantity * option_price;
        double underlying_value = underlying_hedge_shares * market.underlying_price;

        return option_value + underlying_value + cash;
    }

    // Calculate the portfolio's net delta
    double net_delta(const MarketState& market) const {
        double option_delta =
            option.is_call
            ? Greeks::call_delta(market.underlying_price, option.strike, market.rate, market.vol, market.time_to_expiry)
            : Greeks::put_delta(market.underlying_price, option.strike, market.rate, market.vol, market.time_to_expiry);
        
        if (std::isnan(option_delta)) {
            option_delta = 0.0; // Handle expired options
        }

        return (option.quantity * option_delta) + underlying_hedge_shares;
    }
};

void run_simulation() {
    // Seed the random number generator for different results on each run
    srand(time(NULL));

    std::cout << std::fixed << std::setprecision(2);
    std::cout << "\n=====================================\n";
    std::cout << "      HEDGING SIMULATION START\n";
    std::cout << "=====================================\n";

    // --- 1. Initial Setup ---
    MarketState market = {
        24500.0,      // Initial S
        0.06,         // r
        0.18,         // sigma
        28.0 / 365.0  // Initial T (28 days)
    };

    Portfolio portfolio;
    portfolio.option = { 24600.0, -1, true }; // Short 1 Call

    // --- 2. Sell the option and collect premium ---
    double initial_premium = BS::call_price(market.underlying_price, portfolio.option.strike, market.rate, market.vol, market.time_to_expiry);
    portfolio.cash += initial_premium; // Premium received

    std::cout << "Day 28 | Initial State | Sold 1 Call @ " << initial_premium << " | Cash: " << portfolio.cash << std::endl;

    // --- 3. Initial Hedge ---
    double delta_to_hedge = portfolio.net_delta(market);
    portfolio.underlying_hedge_shares -= delta_to_hedge; // Buy shares to neutralize short call delta
    portfolio.cash -= portfolio.underlying_hedge_shares * market.underlying_price;

    std::cout << "       | Hedge         | Bought " << portfolio.underlying_hedge_shares << " shares | Net Delta: " << portfolio.net_delta(market) << " | P&L: " << portfolio.market_value(market) << std::endl;

    // --- 4. Time Simulation Loop (Daily Steps) ---
    const int total_days = 28;
    const double dt = 1.0 / 365.0;

    for (int day = total_days - 1; day >= 0; --day) {
        // a. Time passes
        market.time_to_expiry = day / 365.0;

        // b. Market moves (simple random walk for underlying price)
        // In a real scenario, you'd use a more robust model like GBM or historical data.
        double random_factor = (rand() % 2 == 0) ? 1.005 : 0.995; // Simulate a +/- 0.5% daily move
        market.underlying_price *= random_factor;

        double pnl = portfolio.market_value(market);
        std::cout << "Day " << std::setw(2) << day << " | Market Update | S: " << market.underlying_price << " | P&L before hedge: " << pnl << std::endl;

        // c. Re-hedge the portfolio
        if (day > 0) { // No need to hedge on the last day
            double delta_to_rehedge = portfolio.net_delta(market);
            portfolio.underlying_hedge_shares -= delta_to_rehedge; // Buy/sell shares to re-neutralize
            portfolio.cash -= delta_to_rehedge * market.underlying_price; // Update cash
            std::cout << "       | Re-Hedge      | Adj Shares by " << -delta_to_rehedge << " | Net Delta: " << portfolio.net_delta(market) << " | P&L: " << portfolio.market_value(market) << std::endl;
        }
    }

    // --- 5. Final Settlement ---
    double final_pnl = portfolio.market_value(market);
    std::cout << "\n--- Expiration ---" << std::endl;
    std::cout << "Final Underlying: " << market.underlying_price << std::endl;
    std::cout << "Final P&L: " << final_pnl << std::endl;
    std::cout << "=====================================\n";
    std::cout << "      HEDGING SIMULATION END\n";
    std::cout << "=====================================\n";
}

int main() {
    // You can keep your original test code here if you want
    // For now, let's just run the new simulation
    run_simulation();
    return 0;
}