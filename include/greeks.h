// ==============================================
// FILE: include/greeks.h
// Black-Scholes Greeks Engine Header
// ==============================================

#pragma once

namespace Greeks
{
    // ------------------------------------------
    // Delta
    // ------------------------------------------
    double call_delta(
        double S,      // Underlying Price
        double K,      // Strike
        double r,      // Risk-free Rate
        double sigma,  // Volatility
        double t       // Time in Years
    );

    double put_delta(
        double S,
        double K,
        double r,
        double sigma,
        double t
    );

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
    );

    // ------------------------------------------
    // Vega
    // Same for Call / Put
    // Returned per 1 vol point
    // ------------------------------------------
    double vega(
        double S,
        double K,
        double r,
        double sigma,
        double t
    );

    // ------------------------------------------
    // Theta (per day)
    // ------------------------------------------
    double call_theta(
        double S,
        double K,
        double r,
        double sigma,
        double t
    );

    double put_theta(
        double S,
        double K,
        double r,
        double sigma,
        double t
    );
}