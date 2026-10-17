// ==============================================
// FILE: include/bs.h
// Black-Scholes Pricing Engine Header
// ==============================================

#pragma once

namespace BS
{
    // ------------------------------------------
    // Standard Normal Distribution
    // ------------------------------------------
    double norm_pdf(double x);
    double norm_cdf(double x);

    // ------------------------------------------
    // Core Black-Scholes Terms
    // ------------------------------------------
    double d1(
        double S,      // Spot / Futures Price
        double K,      // Strike
        double r,      // Risk-free Rate
        double sigma,  // Volatility
        double t       // Time in Years
    );

    double d2(
        double S,
        double K,
        double r,
        double sigma,
        double t
    );

    // ------------------------------------------
    // Option Pricing
    // ------------------------------------------
    double call_price(
        double S,
        double K,
        double r,
        double sigma,
        double t
    );

    double put_price(
        double S,
        double K,
        double r,
        double sigma,
        double t
    );

    // ------------------------------------------
    // Vega (for IV Solver)
    // ------------------------------------------
    double vega(
        double S,
        double K,
        double r,
        double sigma,
        double t
    );
}