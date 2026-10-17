// ==============================================
// FILE: include/iv.h
// Implied Volatility Solver Header
// ==============================================

#pragma once

namespace IV
{
    // ------------------------------------------
    // Call Implied Volatility
    //
    // market_price = option premium (LTP)
    // S            = underlying spot/futures
    // K            = strike price
    // r            = risk-free rate (e.g. 0.06)
    // t            = time to expiry in years
    //
    // Returns volatility in decimal form:
    // 0.18 = 18%
    // ------------------------------------------
    double call_iv(
        double market_price,
        double S,
        double K,
        double r,
        double t
    );

    // ------------------------------------------
    // Put Implied Volatility
    // ------------------------------------------
    double put_iv(
        double market_price,
        double S,
        double K,
        double r,
        double t
    );

    // ------------------------------------------
    // Synthetic Underlying (Put-Call Parity)
    // ------------------------------------------
    double implied_forward(
        double call_price,
        double put_price,
        double K,
        double r,
        double T
    );
}