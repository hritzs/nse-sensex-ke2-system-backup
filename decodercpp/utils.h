#pragma once
#include <cstdint>
#include <chrono>
#include <vector>
#include <string>
#include "parser.h"

// Inline timestamp — defined here, no utils.cpp counterpart needed
inline uint64_t now_ms()
{
    using namespace std::chrono;
    return static_cast<uint64_t>(
        duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count()
    );
}

// Snapshot of current live STATE — declared here, defined in utils.cpp
std::vector<TickData> snapshot_ticks();
