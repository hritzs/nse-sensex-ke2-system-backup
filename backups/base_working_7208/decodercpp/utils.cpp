#include "utils.h"
#include "parser.h"
#include <string>
#include <vector>
#include <mutex>
#include <unordered_map>

// These globals are defined in main.cpp — just reference them here
extern std::unordered_map<std::string, TickData> STATE;
extern std::mutex STATE_MTX;

// Returns a thread-safe copy of all current ticks
std::vector<TickData> snapshot_ticks()
{
    std::lock_guard<std::mutex> lock(STATE_MTX);
    std::vector<TickData> out;
    out.reserve(STATE.size());
    for (auto& kv : STATE) {
        out.push_back(kv.second);
    }
    return out;
}
