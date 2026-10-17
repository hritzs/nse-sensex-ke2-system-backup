#pragma once
#include <string>

namespace ExpiryUtils
{
    // convert "26JUN" or "26JUN2025" → epoch seconds
    double to_epoch(const std::string& expiry);
}