#include <iostream>
// =======================================
// FILE: src/parser_7208.cpp
// FINAL PUBLISH FIXED VERSION
// Feed -> Analytics -> WebSocket Publish
// =======================================

#include "parser_7208.h"
#include "market_feed.h"
#include "analytics_engine.h"
#include "ws_server.h"
#include "contract_loader.h" // For Exchange enum

#include <unordered_map>
#include <string>
#include <sstream>

extern std::unordered_map<uint32_t, std::pair<std::string, Exchange>> TOKEN_MAP;

// ======================================
// BIG ENDIAN HELPERS
// ======================================
static inline uint16_t be16(const uint8_t* p)
{
    return ((uint16_t)p[0] << 8) | p[1];
}

static inline uint32_t be32(const uint8_t* p)
{
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  |
            (uint32_t)p[3];
}

// ======================================
// MAIN 7208 PARSER
// ======================================
void parse_7208(const uint8_t* b, size_t len)
{
    constexpr size_t HEADER_SIZE = 40;
    constexpr size_t COUNT_SIZE  = 2;
    constexpr size_t START       = HEADER_SIZE + COUNT_SIZE;
    constexpr size_t RECORD_SIZE = 214;

    if (len < START)
        return;

    uint16_t recs = be16(b + 40);

    // packet sanity
    if (recs == 0 || recs > 50)
        return;

    size_t available = len - START;
    size_t max_recs  = available / RECORD_SIZE;

    if ((size_t)recs > max_recs)
        recs = (uint16_t)max_recs;

    // ==================================
    // LOOP RECORDS
    // ==================================
    for (uint16_t r = 0; r < recs; r++)
    {
        const size_t offset =
            START + (size_t)r * RECORD_SIZE;

        if (offset + RECORD_SIZE > len)
            break;

        const uint8_t* rec = b + offset;

        // ----------------------------------
        // TOKEN + BOOK TYPE
        // ----------------------------------
        const uint32_t token =
            be32(rec + 0);

        const uint16_t book =
            be16(rec + 4);

        // ------------------------------------------------------------
        // NIFTY FUTURE TOKEN DIAGNOSTIC
        // IMPORTANT: this runs BEFORE the book filter.
        // ------------------------------------------------------------
        if (token == 61093 ||
            token == 62329 ||
            token == 66071)
        {
            static int nifty_book_diag = 0;

            if (nifty_book_diag < 30)
            {
                ++nifty_book_diag;

                std::cout
                    << "[NIFTY FUT RECORD]"
                    << " token=" << token
                    << " book=" << book
                    << std::endl;
            }
        }

        // valid books only
        if (book != 1 && book != 2)
            continue;

        auto it = TOKEN_MAP.find(token);

        if (token == 61093 ||
            token == 62329 ||
            token == 66071)
        {
            static int nifty_fut_packet_diag = 0;

            if (nifty_fut_packet_diag < 30)
            {
                ++nifty_fut_packet_diag;

                if (it != TOKEN_MAP.end())
                {
                    std::cout
                        << "[NIFTY FUT PACKET]"
                        << " token=" << token
                        << " symbol=" << it->second.first
                        << std::endl;
                }
                else
                {
                    std::cout
                        << "[NIFTY FUT PACKET MAP MISS]"
                        << " token=" << token
                        << std::endl;
                }
            }
        }

        if (it == TOKEN_MAP.end())
            continue;

        // Safely extract the symbol string from the pair
        const std::string& symbol = it->second.first;

        // ----------------------------------
        // NIFTY FUTURE RAW RECORD DIAGNOSTIC
        // ----------------------------------
        if (symbol.rfind("NIFTY|", 0) == 0 &&
            symbol.find("|0|FUT") != std::string::npos)
        {
            static int fut_raw_diag = 0;

            if (fut_raw_diag < 6)
            {
                ++fut_raw_diag;

                std::cout
                    << "[NIFTY FUT RAW]"
                    << " token=" << token
                    << " book=" << book
                    << " symbol=" << symbol
                    << " len=" << len
                    << std::endl;

                for (size_t off = 0; off <= 120; off += 4)
                {
                    if (off + 4 <= RECORD_SIZE)
                    {
                        std::cout
                            << "  off=" << off
                            << " u32=" << be32(rec + off)
                            << " price=" << (be32(rec + off) / 100.0)
                            << std::endl;
                    }
                }
            }
        }

        // ----------------------------------------------------
        // NIFTY OPTION RAW RECORD DIAGNOSTIC
        //
        // Dump the first few NIFTY option records so we can
        // identify the actual 17208 option LTP/book offsets.
        // ----------------------------------------------------
        if (
            symbol.rfind("NIFTY|", 0) == 0 &&
            symbol.find("|0|FUT") == std::string::npos &&
            symbol.size() > 0
        )
        {
            static int nifty_option_raw_diag = 0;

            if (nifty_option_raw_diag < 8)
            {
                ++nifty_option_raw_diag;

                std::cout
                    << "[NIFTY OPTION RAW]"
                    << " token=" << token
                    << " symbol=" << symbol
                    << " book=" << book
                    << " len=" << len
                    << std::endl;

                for (size_t off = 0; off <= 120; off += 4)
                {
                    if (off + 4 <= RECORD_SIZE)
                    {
                        const uint32_t raw =
                            be32(rec + off);

                        std::cout
                            << "  off=" << off
                            << " u32=" << raw
                            << " price=" << (raw / 100.0)
                            << std::endl;
                    }
                }
            }
        }

        // ----------------------------------
        // LTP
        //
        // NSE 17208 uses a different LTP location
        // for FUT records than for option records.
        // ----------------------------------
        const bool is_future_symbol =
            symbol.size() >= 6 &&
            symbol.rfind("|0|FUT") == symbol.size() - 6;

        const double ltp =
            // 17208 record:
            // FUT and OPTION LTP are both carried at offset +16.
            (be32(rec + 16) / 100.0);

        // ----------------------------------
        // TOP OF BOOK MBP
        // ----------------------------------
        const uint8_t* mbp =
            rec + 56;

        const double bid1 =
            be32(mbp + 4) / 100.0;

        const double ask1 =
            be32(mbp + 64) / 100.0;

        publish_market_quote(Exchange::NSE, token, ltp, bid1, ask1);

        // ----------------------------------
        // ANALYTICS ENGINE
        // returns empty for fut cache ticks
        // returns JSON for options
        // ----------------------------------
        std::string msg =
            Analytics::process_tick(
                symbol,
                ltp,
                bid1,
                ask1
            );

        // ----------------------------------------------------
        // FUTURE QUOTE STREAM
        //
        // Analytics::process_tick() intentionally returns ""
        // for futures, so publish the raw FUT quote here.
        // This is required by the live simulator.
        // ----------------------------------------------------
        if (
            symbol.size() >= 6 &&
            symbol.rfind("|0|FUT") == symbol.size() - 6
        )
        {
            std::ostringstream q;

            std::string fut_expiry;

            {
                const size_t p1 = symbol.find('|');
                const size_t p2 =
                    (p1 == std::string::npos)
                        ? std::string::npos
                        : symbol.find('|', p1 + 1);

                if (
                    p1 != std::string::npos &&
                    p2 != std::string::npos &&
                    p2 > p1 + 1
                )
                {
                    fut_expiry =
                        symbol.substr(
                            p1 + 1,
                            p2 - p1 - 1
                        );
                }
            }

            static int fut_quote_diag = 0;

            if (
                symbol.rfind("NIFTY|", 0) == 0
                && fut_quote_diag < 30
            )
            {
                ++fut_quote_diag;

                std::cout
                    << "[FUT QUOTE SOURCE]"
                    << " symbol=" << symbol
                    << " token=" << token
                    << " ltp=" << ltp
                    << " raw12=" << (be32(rec + 12) / 100.0)
                    << " raw16=" << (be32(rec + 16) / 100.0)
                    << std::endl;
            }

            q << "{"
              << "\"type\":\"future_quote\","
              << "\"symbol\":\"" << symbol << "\","
              << "\"expiry\":\"" << fut_expiry << "\","
              << "\"ltp\":" << ltp << ","
              << "\"bid1\":" << bid1 << ","
              << "\"ask1\":" << ask1
              << "}";


            static int fut_ws_publish_diag = 0;

            if (fut_ws_publish_diag < 20)
            {
                ++fut_ws_publish_diag;

                std::cout
                    << "[FUT WS PUBLISH]"
                    << " symbol=" << symbol
                    << " expiry=" << fut_expiry
                    << " ltp=" << ltp
                    << std::endl;
            }

            broadcast_raw(q.str());
        }

        // ----------------------------------------------------
        // Analytics option/surface messages
        // ----------------------------------------------------
        if (!msg.empty())
        {
            broadcast_raw(msg);
        }
    }
}