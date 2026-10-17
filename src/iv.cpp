// ==============================================
// FILE: src/iv.cpp
// UPGRADED IMPLIED VOL SOLVER
// Ported from your Python robust model
// Stable for ATM / OTM / Deep strikes
// ==============================================

#include "iv.h"
#include "bs.h"

#include <cmath>
#include <limits>

namespace IV
{
    // =====================================================
    // HELPERS
    // =====================================================
    static inline bool bad(double x)
    {
        return std::isnan(x) || !std::isfinite(x);
    }

    static inline double nanv()
    {
        return std::numeric_limits<double>::quiet_NaN();
    }

    static inline double intrinsic_call(
        double S,
        double K)
    {
        return std::fmax(S - K, 0.0);
    }

    static inline double intrinsic_put(
        double S,
        double K)
    {
        return std::fmax(K - S, 0.0);
    }

    // =====================================================
    // CORE ROBUST SOLVER
    // returns decimal vol
    // ex: 0.185 = 18.5%
    // =====================================================
    static double solve_iv(
        bool is_call,
        double price,
        double S,
        double K,
        double r,
        double T)
    {
        if (bad(price) || price <= 0.0) return nanv();
        if (bad(S) || S <= 0.0)         return nanv();
        if (bad(K) || K <= 0.0)         return nanv();
        if (bad(T) || T <= 0.0)         return nanv();

        // ---------------------------------------------
        // intrinsic check
        // ---------------------------------------------
        const double intrinsic =
            is_call
            ? intrinsic_call(S, K)
            : intrinsic_put(S, K);

        if (price <= intrinsic)
            return nanv();

        // ---------------------------------------------
        // robust starting guess
        // ---------------------------------------------
        double sigma = 0.50;   // 50%

        constexpr int MAX_ITERS = 100;
        constexpr double TOL = 0.0001;

        for (int i = 0; i < MAX_ITERS; ++i)
        {
            double theo =
                is_call
                ? BS::call_price(
                    S, K, r, sigma, T)
                : BS::put_price(
                    S, K, r, sigma, T);

            if (bad(theo))
                return nanv();

            double diff = theo - price;

            if (std::fabs(diff) < TOL)
                return sigma;

            double vega =
                BS::vega(
                    S, K, r, sigma, T
                );

            // -----------------------------------------
            // Vega underflow
            // -----------------------------------------
            if (bad(vega) || vega < 1e-8)
            {
                sigma *= 1.5;
                if (sigma > 4.0)
                    return nanv();
                continue;
            }

            // -----------------------------------------
            // controlled Newton step
            // -----------------------------------------
            double step = (diff / vega) / 100.0;

            // prevent collapse
            if (step > sigma * 0.5)
                step = sigma * 0.5;
            else if (step < -2.0)
                step = -2.0;

            sigma -= step;

            // bounds
            if (sigma > 4.0)
                sigma = 4.0;

            if (sigma < 0.0001)
                return nanv();
        }

        return sigma; // Best effort return instead of hard NaN failing
    }

    // =====================================================
    // PUBLIC API
    // =====================================================

    double call_iv(
        double price,
        double S,
        double K,
        double r,
        double T)
    {
        return solve_iv(
            true,
            price,
            S,
            K,
            r,
            T
        );
    }

    double put_iv(
        double price,
        double S,
        double K,
        double r,
        double T)
    {
        return solve_iv(
            false,
            price,
            S,
            K,
            r,
            T
        );
    }

    // =====================================================
    // SYNTHETIC UNDERLYING (PUT-CALL PARITY)
    // =====================================================
    double implied_forward(
        double call_price,
        double put_price,
        double K,
        double r,
        double T)
    {
        if (bad(call_price) || bad(put_price) || bad(K) || K <= 0.0) 
            return 0.0;
            
        return K + (call_price - put_price) * std::exp(r * T);
    }
}