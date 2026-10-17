#pragma once

#include <string>

namespace Config
{
    // NSE Feed
    constexpr const char* NSE_MULTICAST_IP = "233.1.2.5";
    constexpr int NSE_PORT = 34330;
    constexpr const char* NSE_LOCAL_IP = "0.0.0.0";

    // BSE Feed (SENSEX)
    constexpr const char* BSE_MULTICAST_IP = "233.1.2.4";
    constexpr int BSE_PORT = 2004;
    constexpr const char* BSE_LOCAL_IP = "172.16.1.9";

    constexpr size_t MAX_PACKET = 4096;
}
