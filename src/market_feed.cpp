#include "market_feed.h"
#include <chrono>
#include <cmath>
#include <sstream>
#include <iomanip>

static long long receipt_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}
static void price(std::ostream& out, double v) {
    if (std::isfinite(v) && v >= 0) out << std::setprecision(15) << v;
    else out << "null";
}
static void qty(std::ostream& out, std::optional<uint64_t> v) {
    if (v) out << *v; else out << "null";
}
void publish_market_quote(Exchange exchange, uint32_t token, double ltp,
                          double bid, double ask, std::optional<uint64_t> ltq,
                          std::optional<uint64_t> bid_qty, std::optional<uint64_t> ask_qty,
                          std::optional<uint64_t> volume) {
    if (!market_source_active()) return;
    std::ostringstream out;
    out << "{\"type\":\"market_quote\",\"exchange\":\""
        << (exchange == Exchange::NSE ? "NSE" : "BSE")
        << "\",\"token\":" << token << ",\"received_ms\":" << receipt_ms()
        << ",\"exchange_ts\":null,\"last_trade_ts\":null,\"ltp\":";
    price(out, ltp); out << ",\"bid\":"; price(out,bid > 0 ? bid : NAN);
    out << ",\"ask\":"; price(out,ask > 0 ? ask : NAN);
    out << ",\"ltq\":"; qty(out,ltq);
    out << ",\"bid_qty\":"; qty(out,bid_qty);
    out << ",\"ask_qty\":"; qty(out,ask_qty);
    out << ",\"volume\":"; qty(out,volume);
    out << "}";
    broadcast_market_source(out.str());
}
void publish_market_oi(Exchange exchange, uint32_t token, uint64_t oi) {
    if (!market_source_active()) return;
    std::ostringstream out;
    out << "{\"type\":\"market_oi\",\"exchange\":\""
        << (exchange == Exchange::NSE ? "NSE" : "BSE")
        << "\",\"token\":" << token << ",\"oi\":" << oi
        << ",\"received_ms\":" << receipt_ms() << "}";
    broadcast_market_source(out.str());
}

void publish_market_index(uint32_t code, const std::string& symbol, double value) {
    if (!market_source_active() || !std::isfinite(value) || value <= 0) return;
    if (symbol.empty() || symbol.find_first_of("\"\\") != std::string::npos) return;
    for (unsigned char c : symbol) if (c < 32) return;
    std::ostringstream out;
    out << "{\"type\":\"market_index\",\"exchange\":\"BSE\",\"index_code\":"
        << code << ",\"symbol\":\"" << symbol << "\",\"ltp\":";
    price(out,value);
    out << ",\"received_ms\":" << receipt_ms() << "}";
    broadcast_market_source(out.str());
}
