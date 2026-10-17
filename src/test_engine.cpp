// ==============================================
// FILE: src/test_engine.cpp
// Standalone Test for Pricing / IV / Greeks
// ==============================================

#include <iostream>
#include <iomanip>

#include "bs.h"
#include "iv.h"
#include "greeks.h"

int main()
{
    // ------------------------------------------
    // Sample Inputs
    // ------------------------------------------
    double S     = 24500.0;   // Underlying
    double K     = 24600.0;   // Strike
    double r     = 0.06;      // 6%
    double t     = 28.0 / 365.0; // 28 DTE
    double sigma = 0.18;      // 18% assumed vol

    std::cout << std::fixed << std::setprecision(6);

    // ------------------------------------------
    // Black-Scholes Prices
    // ------------------------------------------
    double call_px = BS::call_price(S, K, r, sigma, t);
    double put_px  = BS::put_price (S, K, r, sigma, t);

    // ------------------------------------------
    // Solve IV Back from Premium
    // ------------------------------------------
    double call_iv = IV::call_iv(call_px, S, K, r, t);
    double put_iv  = IV::put_iv (put_px,  S, K, r, t);

    // ------------------------------------------
    // Greeks using recovered IV
    // ------------------------------------------
    double c_delta = Greeks::call_delta(S, K, r, call_iv, t);
    double p_delta = Greeks::put_delta (S, K, r, put_iv,  t);

    double gamma   = Greeks::gamma(S, K, r, call_iv, t);

    double vega    = Greeks::vega(S, K, r, call_iv, t);

    double c_theta = Greeks::call_theta(S, K, r, call_iv, t);
    double p_theta = Greeks::put_theta (S, K, r, put_iv,  t);

    // ------------------------------------------
    // Print Results
    // ------------------------------------------
    std::cout << "=====================================\n";
    std::cout << "      C++ Derivatives Engine Test\n";
    std::cout << "=====================================\n\n";

    std::cout << "Underlying : " << S << "\n";
    std::cout << "Strike     : " << K << "\n";
    std::cout << "Rate       : " << r * 100.0 << "%\n";
    std::cout << "DTE        : 28 days\n";
    std::cout << "Input Vol  : " << sigma * 100.0 << "%\n\n";

    std::cout << "----- Prices -----\n";
    std::cout << "Call Price : " << call_px << "\n";
    std::cout << "Put Price  : " << put_px  << "\n\n";

    std::cout << "----- Implied Vol -----\n";
    std::cout << "Call IV    : " << call_iv * 100.0 << "%\n";
    std::cout << "Put IV     : " << put_iv  * 100.0 << "%\n\n";

    std::cout << "----- Greeks -----\n";
    std::cout << "Call Delta : " << c_delta << "\n";
    std::cout << "Put Delta  : " << p_delta << "\n";
    std::cout << "Gamma      : " << gamma   << "\n";
    std::cout << "Vega       : " << vega    << "\n";
    std::cout << "Call Theta : " << c_theta << "\n";
    std::cout << "Put Theta  : " << p_theta << "\n";

    std::cout << "\n=====================================\n";

    return 0;
}