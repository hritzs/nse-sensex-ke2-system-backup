#pragma once

#include <vector>
#include <string>

namespace ExpiryEngine
{
    double to_epoch(const std::string& expiry);
    bool is_weekly(const std::string& expiry);
    std::string nearest(const std::vector<std::string>& expiries);
}