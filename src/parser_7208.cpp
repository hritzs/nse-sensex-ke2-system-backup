// NSE F&O NNF 9.48, tables 79-81A (March 2026).
#include "parser_7208.h"
#include "market_feed.h"
#include "analytics_engine.h"
#include "ws_server.h"
#include "contract_loader.h"
#include <cstdint>
#include <sstream>
#include <string>
#include <unordered_map>

extern std::unordered_map<uint32_t, std::pair<std::string, Exchange>> TOKEN_MAP;
static uint16_t be16(const uint8_t* p) { return uint16_t(p[0]) << 8 | p[1]; }
static uint32_t be32(const uint8_t* p) {
    return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

void parse_7208(const uint8_t* b, size_t len) {
    constexpr size_t start = 42;
    if (len < start) return;
    const auto tx = be16(b + 10);
    if (tx != 7208 && tx != 17208) return;
    const bool enhanced = tx == 17208;
    const size_t record_size = enhanced ? 258 : 214;
    const auto count = be16(b + 40);
    // A truncated batch must never publish a plausible partial snapshot.
    if (count == 0 || count > 2 || len < start + size_t(count) * record_size) return;
    const size_t ltp_offset = enhanced ? 16 : 12;
    const size_t bid_offset = enhanced ? 68 : 60;
    const size_t ask_offset = enhanced ? 148 : 120;
    for (uint16_t r = 0; r < count; ++r) {
        const auto* rec = b + start + size_t(r) * record_size;
        const uint32_t token = be32(rec);
        const auto book = be16(rec + 4);
        if (book != 1 && book != 2) continue;
        const auto it = TOKEN_MAP.find(token);
        if (it == TOKEN_MAP.end()) continue;
        const auto& symbol = it->second.first;
        const double ltp = be32(rec + ltp_offset) / 100.0;
        const double bid = be32(rec + bid_offset) / 100.0;
        const double ask = be32(rec + ask_offset) / 100.0;
        publish_market_quote(Exchange::NSE, token, ltp, bid, ask);
        const auto message = Analytics::process_tick(symbol, ltp, bid, ask);
        if (symbol.size() >= 6 && symbol.rfind("|0|FUT") == symbol.size() - 6) {
            const auto first = symbol.find('|');
            const auto last = symbol.find('|', first + 1);
            const auto expiry = first != std::string::npos && last != std::string::npos
                ? symbol.substr(first + 1, last - first - 1) : std::string{};
            std::ostringstream out;
            out << "{\"type\":\"future_quote\",\"symbol\":\"" << symbol
                << "\",\"expiry\":\"" << expiry << "\",\"ltp\":" << ltp
                << ",\"bid1\":" << bid << ",\"ask1\":" << ask << "}";
            broadcast_raw(out.str());
        }
        if (!message.empty()) broadcast_raw(message);
    }
}
