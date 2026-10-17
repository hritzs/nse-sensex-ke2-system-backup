#pragma once
#include <string>
#include <cstdint>
#include <optional>
#include "contract_loader.h"

// KE2 source events carry observed values only, independently of analytics.
void publish_market_quote(Exchange exchange, uint32_t token, double ltp,
                          double bid, double ask,
                          std::optional<uint64_t> ltq = {},
                          std::optional<uint64_t> bid_qty = {},
                          std::optional<uint64_t> ask_qty = {});
void publish_market_oi(Exchange exchange, uint32_t token, uint64_t oi);
bool market_source_active();
void broadcast_market_source(const std::string& msg);
