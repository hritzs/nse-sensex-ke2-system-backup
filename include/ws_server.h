// ==============================================
// FILE: include/ws_server.h
// WebSocket Broadcast Header
// ==============================================

#pragma once

#include <string>

// Start websocket server
void start_ws_server(int port);

// Legacy tick broadcast
void broadcast_tick(
    const std::string& symbol,
    double ltp,
    double bid1,
    double ask1
);

// Raw JSON broadcast
void broadcast_raw(
    const std::string& msg
);