// ==============================================
// FILE: src/bs.cpp
// Black-Scholes Pricing Engine (Production Grade)
// ==============================================

#include "bs.h"

#include <cmath>
#include <algorithm>

namespace BS
{
    constexpr double PI = 3.14159265358979323846;

    // ------------------------------------------
    // Standard Normal PDF
    // ------------------------------------------
    double norm_pdf(double x)
    {
        return std::exp(-0.5 * x * x) / std::sqrt(2.0 * PI);
    }

    // ------------------------------------------
    // Standard Normal CDF
    // Uses erf() from cmath
    // ------------------------------------------
    double norm_cdf(double x)
    {
        return 0.5 * (1.0 + std::erf(x / std::sqrt(2.0)));
    }

    // ------------------------------------------
    // d1
    // ------------------------------------------
    double d1(
        double S,      // Spot / Futures price
        double K,      // Strike
        double r,      // Risk-free rate
        double sigma,  // Volatility
        double t       // Time in years
    )
    {
        if (S <= 0.0 || K <= 0.0 || sigma <= 0.0 || t <= 0.0)
            return 0.0;

        return (
            std::log(S / K) +
            (r + 0.5 * sigma * sigma) * t
        ) / (sigma * std::sqrt(t));
    }

    // ------------------------------------------
    // d2
    // ------------------------------------------
    double d2(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        return d1(S, K, r, sigma, t) - sigma * std::sqrt(t);
    }

    // ------------------------------------------
    // Call Price
    // ------------------------------------------
    double call_price(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        if (t <= 0.0)
            return std::max(0.0, S - K);

        double D1 = d1(S, K, r, sigma, t);
        double D2 = d2(S, K, r, sigma, t);

        return
            S * norm_cdf(D1)
            - K * std::exp(-r * t) * norm_cdf(D2);
    }

    // ------------------------------------------
    // Put Price
    // ------------------------------------------
    double put_price(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        if (t <= 0.0)
            return std::max(0.0, K - S);

        double D1 = d1(S, K, r, sigma, t);
        double D2 = d2(S, K, r, sigma, t);

        return
            K * std::exp(-r * t) * norm_cdf(-D2)
            - S * norm_cdf(-D1);
    }

    // ------------------------------------------
    // Vega
    // Useful for IV solver
    // ------------------------------------------
    double vega(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        if (t <= 0.0)
            return 0.0;

        double D1 = d1(S, K, r, sigma, t);

        return (S * norm_pdf(D1) * std::sqrt(t)) * 0.01;
    }
}