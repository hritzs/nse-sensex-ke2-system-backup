#pragma once

#include <string>
#include <cstdint>

struct TickData
{
    std::string symbol;

    double ltp = 0.0;
    double bid1 = 0.0;
    double ask1 = 0.0;

    uint32_t bidqty1 = 0;
    uint32_t askqty1 = 0;
};