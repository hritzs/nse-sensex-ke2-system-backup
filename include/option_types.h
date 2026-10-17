// ==============================================
// FILE: include/option_types.h
// Core Data Structures for Option Chain Engine
// ==============================================

#pragma once

#include <string>
#include <cstdint>

enum class OptionType
{
    CALL,
    PUT,
    FUTURE,
    UNKNOWN
};

// ------------------------------------------
// Raw Live Tick
// ------------------------------------------
struct OptionTick
{
    std::string symbol;

    std::string underlying;   // NIFTY, BANKNIFTY, RELIANCE...
    std::string expiry;       // 29MAY2026 etc

    double strike = 0.0;
    OptionType type = OptionType::UNKNOWN;

    double ltp  = 0.0;
    double bid1 = 0.0;
    double ask1 = 0.0;

    uint64_t ts = 0;         // epoch ms
};

// ------------------------------------------
// Analytics Output
// ------------------------------------------
struct OptionAnalytics
{
    std::string symbol;

    double future = 0.0;

    double iv = 0.0;
    double iv_bid = 0.0;
    double iv_ask = 0.0;

    double delta = 0.0;
    double gamma = 0.0;
    double theta = 0.0;
    double vega = 0.0;

    double theo = 0.0;
};
// ------------------------------------------
// Combined Node
// ------------------------------------------
struct ChainNode
{
    OptionTick tick;
    OptionAnalytics analytics;
};

// ------------------------------------------
// Utility Helpers
// ------------------------------------------
inline bool is_call(OptionType t)
{
    return t == OptionType::CALL;
}

inline bool is_put(OptionType t)
{
    return t == OptionType::PUT;
}

inline bool is_future(OptionType t)
{
    return t == OptionType::FUTURE;
}