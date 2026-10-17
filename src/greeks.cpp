// ==============================================
// FILE: src/greeks.cpp
// Black-Scholes Greeks Engine (Production Grade)
// ==============================================

#include "greeks.h"
#include "bs.h"

#include <cmath>

namespace Greeks
{
    // ------------------------------------------
    // Call Delta
    // ------------------------------------------
    double call_delta(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        if (t <= 0.0) return (S > K) ? 1.0 : 0.0;

        double D1 = BS::d1(S, K, r, sigma, t);
        return BS::norm_cdf(D1);
    }

    // ------------------------------------------
    // Put Delta
    // ------------------------------------------
    double put_delta(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        if (t <= 0.0) return (S < K) ? -1.0 : 0.0;

        double D1 = BS::d1(S, K, r, sigma, t);
        return BS::norm_cdf(D1) - 1.0;
    }

    // ------------------------------------------
    // Gamma
    // Same for Call / Put
    // ------------------------------------------
    double gamma(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        if (S <= 0.0 || sigma <= 0.0 || t <= 0.0)
            return 0.0;

        double D1 = BS::d1(S, K, r, sigma, t);

        return BS::norm_pdf(D1) /
               (S * sigma * std::sqrt(t));
    }

    // ------------------------------------------
    // Vega
    // Same for Call / Put
    // Returned per 1 volatility point
    // ------------------------------------------
    double vega(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        return BS::vega(S, K, r, sigma, t);
    }

    // ------------------------------------------
    // Call Theta
    // Returned per day
    // ------------------------------------------
    double call_theta(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        if (t <= 0.0) return 0.0;

        double D1 = BS::d1(S, K, r, sigma, t);
        double D2 = BS::d2(S, K, r, sigma, t);

        double term1 =
            -(S * BS::norm_pdf(D1) * sigma) /
            (2.0 * std::sqrt(t));

        double term2 =
            -r * K * std::exp(-r * t) *
            BS::norm_cdf(D2);

        return (term1 + term2) / 365.0;
    }

    // ------------------------------------------
    // Put Theta
    // Returned per day
    // ------------------------------------------
    double put_theta(
        double S,
        double K,
        double r,
        double sigma,
        double t
    )
    {
        if (t <= 0.0) return 0.0;

        double D1 = BS::d1(S, K, r, sigma, t);
        double D2 = BS::d2(S, K, r, sigma, t);

        double term1 =
            -(S * BS::norm_pdf(D1) * sigma) /
            (2.0 * std::sqrt(t));

        double term2 =
            r * K * std::exp(-r * t) *
            BS::norm_cdf(-D2);

        return (term1 + term2) / 365.0;
    }
}