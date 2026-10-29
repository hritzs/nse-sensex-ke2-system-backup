#include "decoder.h"
#include "feed_log.h"
#include "market_feed.h"
#include <stdexcept>
#include <chrono>
#include "parser_7208.h"
#include "contract_loader.h"
#include "analytics_engine.h"

// ================================================================
// LOCAL MONOTONIC CLOCK FOR DECODER LATENCY MEASUREMENTS
// ================================================================

static inline uint64_t decoder_steady_ns()
{
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()
        ).count()
    );
}



#include "ws_server.h"

namespace Analytics {
    void update_oi(const std::string& symbol, uint64_t oi);
    void update_volume(const std::string& symbol, uint64_t vol);
    void update_lpp(const std::string& symbol, double high, double low);
}


// ------------------------------------------------------------
// Forward declarations for NSE transaction parsers.
// Implementations are defined later in decoder.cpp.
// ------------------------------------------------------------
static void parse_17202(const uint8_t* b, size_t len);
static void parse_7202(const uint8_t* b, size_t len);
static void parse_17201(const uint8_t* b, size_t len);
static void parse_7201(const uint8_t* b, size_t len);
static void parse_7220(const uint8_t* b, size_t len);
static void parse_17130(const uint8_t* b, size_t len);
static void parse_7130(const uint8_t* b, size_t len);


#include <vector>
#include <iostream>
#include <atomic>
#include <lzo/lzo1z.h>
#include <cstdint>
#include <unordered_map>
#include <string>
#include <sstream>
#include <algorithm>

extern std::unordered_map<uint32_t, std::pair<std::string, Exchange>> TOKEN_MAP;
extern std::unordered_map<uint32_t, std::pair<std::string, Exchange>> BSE_TOKEN_MAP;

static std::atomic<uint64_t> total_packets{0};
static std::atomic<uint64_t> dropped_packets{0};
static std::atomic<uint64_t> parsed_packets{0};
static std::atomic<uint64_t> uncompressed_packets{0}, lzo_errors{0}, framing_errors{0};

static inline uint16_t be16(const uint8_t* p)
{
    return ((uint16_t)p[0] << 8) | p[1];
}


static inline uint64_t be64(const uint8_t* p) {
    return ((uint64_t)p[0] << 56) |
           ((uint64_t)p[1] << 48) |
           ((uint64_t)p[2] << 40) |
           ((uint64_t)p[3] << 32) |
           ((uint64_t)p[4] << 24) |
           ((uint64_t)p[5] << 16) |
           ((uint64_t)p[6] << 8)  |
            (uint64_t)p[7];
}

static inline uint32_t be32(const uint8_t* p)
{
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  |
            (uint32_t)p[3];
}

// ======================================
// BSE NFCAST DECOMPRESSION PARSER
// ======================================
class BseParser {
    const uint8_t* buf;
    size_t len;
    size_t pos;

public:
    BseParser(const uint8_t* b, size_t l) : buf(b), len(l), pos(0) {}

    bool eof() const { return pos >= len; }

    uint8_t read_u8() {
        if (pos + 1 > len) throw std::out_of_range("BSE u8");
        return buf[pos++];
    }

    int16_t read_s16() {
        if (pos + 2 > len) throw std::out_of_range("BSE u16");
        int16_t v = (int16_t)((buf[pos] << 8) | buf[pos+1]);
        pos += 2;
        return v;
    }

    uint16_t read_u16() {
        if (pos + 2 > len) throw std::out_of_range("BSE u16");
        uint16_t v = (uint16_t)((buf[pos] << 8) | buf[pos+1]);
        pos += 2;
        return v;
    }

    uint32_t read_u32() {
        if (pos + 4 > len) throw std::out_of_range("BSE u32");
        uint32_t v = ((uint32_t)buf[pos] << 24) | ((uint32_t)buf[pos+1] << 16) | ((uint32_t)buf[pos+2] << 8) | buf[pos+3];
        pos += 4;
        return v;
    }

    uint64_t read_u64() {
        if (pos + 8 > len) throw std::out_of_range("BSE u64");
        uint64_t hi = read_u32();
        uint64_t lo = read_u32();
        return (hi << 32) | lo;
    }

    void skip(size_t n) {
        if (pos + n <= len) pos += n;
        else throw std::out_of_range("BSE skip");
    }

    int64_t read_compressed(int64_t base, bool wide_quantity = false) {
        if (pos + 2 > len) throw std::out_of_range("BSE compressed");
        int16_t diff = read_s16();
        if (diff == 32767) {
            if (!wide_quantity) return read_u32();
            const uint64_t value = read_u64();
            if (value > INT64_MAX) throw std::out_of_range("BSE wide quantity");
            return static_cast<int64_t>(value);
        }
        return base + diff;
    }
};

static bool decompress_lzo(
    const uint8_t* in,
    size_t inlen,
    std::vector<uint8_t>& out)
{
    static const bool initialized = lzo_init() == LZO_E_OK;
    if (!initialized) return false;

    out.resize(65536);
    lzo_uint out_len = 65536;

    int r = lzo1z_decompress_safe(
        in,
        (lzo_uint)inlen,
        out.data(),
        &out_len,
        nullptr
    );

    if (r != LZO_E_OK)
        return false;

    out.resize((size_t)out_len);
    return true;
}

static void print_stats()
{
#ifdef KE2_FEED_LIBRARY
    return;
#endif
    std::unique_lock<std::mutex> lock(FEED_LOG_MUTEX, std::try_to_lock);
    if (!lock.owns_lock()) return;
    static uint64_t last_print = 0;
    const uint64_t total = total_packets.load();
    if (total >= last_print + 5000) {
        last_print = total;
        std::cout << "[DECODE STATS] DATAGRAMS=" << total
                  << " MESSAGES_PARSED=" << parsed_packets.load()
                  << " REJECTED_UNITS=" << dropped_packets.load()
                  << " UNCOMPRESSED=" << uncompressed_packets.load()
                  << " LZO_ERRORS=" << lzo_errors.load()
                  << " FRAMING_ERRORS=" << framing_errors.load() << "\n";
    }
}

// Parse and decompress BSE NFCAST Message 2020 / 2021
void parse_bse_nfcast(const uint8_t* buf, size_t len) {
    BseParser p(buf, len);
    if (len < 4) return;
    uint32_t msg_type = p.read_u32();

    if (len < 28) return; // Full header size is 28 bytes

    // ----------------------------------
    // SPOT INDEX BROADCAST (SENSEX/BANKEX)
    // ----------------------------------
    if (msg_type == 2011 || msg_type == 2012) {
        p.skip(22);
        uint16_t num_records = p.read_u16();
        if (num_records > 24 || len < 28 + size_t(num_records) * 40)
            throw std::out_of_range("BSE index record count");
        for (int i = 0; i < num_records; i++) {
            if (p.eof()) break;
            
            const uint32_t index_code = p.read_u32(); // Exchange index identity
            p.skip(16); // High, Low, Open, PrevClose
            int32_t index_val_raw = p.read_u32();
            double index_value = index_val_raw / 100.0;
            
            char index_id[8] = {0};
            for (int j = 0; j < 7; j++) index_id[j] = p.read_u8();
            p.skip(9); // Reserved fields
            
            std::string symbol(index_id);
            symbol.erase(std::find(symbol.begin(), symbol.end(), '\0'), symbol.end());
            while (!symbol.empty() && symbol.back() == ' ') symbol.pop_back();
            
            // Inject spot price into the analytics engine!
            if (!symbol.empty() && index_value > 0) {
                publish_market_index(index_code, symbol, index_value);
                Analytics::update_underlying(symbol, index_value);
            }
        }
        return;
    }

    // Complex/spread instruments have extended identifiers and may carry
    // negative spread prices. They are absent from our outright contract
    // master and must never be interpreted as an outright SENSEX token.
    if (msg_type == 2021) return;
    if (msg_type != 2020) return;

    p.skip(22); // Skip Res1(4), Res2(4), Res3(2), Time(8), Res4(2), Res5(2)
    uint16_t num_records = p.read_u16();

    if (num_records == 0 || num_records > 6) throw std::out_of_range("BSE record count");
    struct Decoded {
        uint32_t token; double ltp, best_bid, best_ask;
        uint64_t ltq, volume;
        std::optional<uint64_t> bid_qty, ask_qty;
    };
    std::vector<Decoded> decoded;
    for (int i = 0; i < num_records; i++) {
        uint64_t token = msg_type == 2020 ? p.read_u32() : p.read_u64();
        p.skip(4); // no. of trades
        uint64_t volume = p.read_u64();
        p.skip(34); // value, flags, market/session, last-trade time, reserved
        uint16_t num_price_points = p.read_u16();
        if (num_price_points > 5) throw std::out_of_range("BSE depth count");
        p.skip(12); // Julian timestamp, close
        uint64_t ltq = p.read_u64();
        if (ltq > INT64_MAX) throw std::out_of_range("BSE quantity");
        int64_t ltp_raw = p.read_u32();
        for (int k = 0; k < 12; k++)
            p.read_compressed(k >= 6 && k <= 8 ? static_cast<int64_t>(ltq) : ltp_raw,
                              k >= 6 && k <= 8);
        // NFCAST 6.1/6.2/6.3: nine fields after WAP, before depth.
        // Side indicators are raw shorts, never compression escape markers.
        p.read_compressed(ltp_raw);                 // closing reference
        p.read_compressed(ltq, true); p.read_u16();        // imbalance quantity / side
        p.read_compressed(ltq, true); p.read_u16();        // market-order imbalance / side
        p.read_compressed(ltq, true); p.read_compressed(ltp_raw);
        p.read_compressed(ltq, true); p.read_compressed(ltp_raw);
        double best_bid = 0, best_ask = 0;
        std::optional<uint64_t> bid_qty, ask_qty;
        for (int side = 0; side < 2; ++side) {
            int64_t bases[5] = {ltp_raw,static_cast<int64_t>(ltq),static_cast<int64_t>(ltq),
                               static_cast<int64_t>(ltq),static_cast<int64_t>(ltq)};
            for (unsigned level = 0; level < num_price_points; ++level) {
                int16_t diff = p.read_s16();
                if (diff == (side == 0 ? 32766 : -32766)) break;
                int64_t values[5];
                values[0] = diff == 32767 ? p.read_u32() : bases[0] + diff;
                for (int field = 1; field < 5; ++field)
                    values[field] = p.read_compressed(bases[field], field == 1 || field == 3);
                if (values[0] < 0 || values[1] < 0)
                    throw std::out_of_range("BSE negative depth");
                if (level == 0) {
                    if (side == 0) {best_bid=values[0]/100.0;bid_qty=values[1];}
                    else {best_ask=values[0]/100.0;ask_qty=values[1];}
                }
                std::copy(values,values+5,bases);
            }
        }
        if (token <= UINT32_MAX)
            decoded.push_back({static_cast<uint32_t>(token),ltp_raw/100.0,best_bid,best_ask,
                               ltq,volume,bid_qty,ask_qty});
    }
    for (const auto& record : decoded) {
        const auto token=record.token;
        const auto ltp=record.ltp, best_bid=record.best_bid, best_ask=record.best_ask;
        // Publish raw SENSEX/BSE quotes even before analytics is ready.
        if (BSE_TOKEN_MAP.find(token) != BSE_TOKEN_MAP.end())
            publish_market_quote(Exchange::BSE, token, ltp, best_bid, best_ask,
                                 record.ltq, record.bid_qty, record.ask_qty, record.volume);

#ifdef KE2_FEED_LIBRARY
        continue; // Embedded raw-feed library does not run analytics.
#endif
        // ----------------------------------
        // PUSH TO ANALYTICS
        // ----------------------------------
        auto it = BSE_TOKEN_MAP.find(token);
        if (it != BSE_TOKEN_MAP.end()) {
            const std::string& symbol = it->second.first;
            
            const uint64_t analytics_rx_ns = decoder_steady_ns();

            std::string msg =
                Analytics::process_tick(
                    symbol,
                    ltp,
                    best_bid,
                    best_ask
                );

            const uint64_t analytics_done_ns =
                decoder_steady_ns();

            const uint64_t analytics_latency_ns =
                analytics_done_ns >= analytics_rx_ns
                    ? analytics_done_ns - analytics_rx_ns
                    : 0;

            static std::atomic<uint64_t>
                analytics_diag_count{0};

            const uint64_t ad =
                analytics_diag_count.fetch_add(
                    1,
                    std::memory_order_relaxed
                );

            if (
                analytics_latency_ns
                    > 10'000'000ULL
                &&
                (ad % 100) == 0
            ) {
                std::cout
                    << "[ANALYTICS LATENCY]"
                    << " symbol=" << symbol
                    << " latency_ms="
                    << (
                        analytics_latency_ns
                        / 1'000'000.0
                    )
                    << std::endl;
            }

            static std::atomic<int> pt_diag_count{0};
            int pt_n = pt_diag_count.fetch_add(1);

            if (pt_n < 40)
            {
                std::cout
                    << "[PROCESS_TICK DIAG] symbol=" << symbol
                    << " ltp=" << ltp
                    << " bid=" << best_bid
                    << " ask=" << best_ask
                    << " result_bytes=" << msg.size()
                    << std::endl;

                if (!msg.empty())
                {
                    std::cout
                        << "[PROCESS_TICK JSON] "
                        << msg.substr(0, 800)
                        << std::endl;
                }
            }

            if (!msg.empty()) {
                broadcast_raw(msg);
            } else if (symbol.size() >= 6 &&
                       symbol.compare(symbol.size()-6, 6, "|0|FUT") == 0) {
                const auto p1 = symbol.find('|');
                const auto p2 = symbol.find('|', p1 + 1);
                std::ostringstream q;
                q << "{\"type\":\"future_quote\",\"symbol\":\"" << symbol
                  << "\",\"expiry\":\"" << symbol.substr(p1+1,p2-p1-1)
                  << "\",\"ltp\":" << ltp << ",\"bid1\":" << best_bid
                  << ",\"ask1\":" << best_ask << "}";
                broadcast_raw(q.str());
            }
        }
    }
}


// ======================================
// NSE FO PARSER IMPLEMENTATIONS
// ======================================

static void dump_market_record(const char* tag, const uint8_t* p, size_t n)
{
#ifdef KE2_FEED_LIBRARY
    return;
#endif
    static int count = 0;

    if (count >= 20)
        return;

    ++count;

    std::cout << "[" << tag << " RAW] len=" << n << " bytes=";

    size_t dump_n = (n < 80) ? n : 80;

    for (size_t i = 0; i < dump_n; ++i)
    {
        std::cout
            << std::hex
            << static_cast<int>(p[i])
            << (i + 1 < dump_n ? " " : "");
    }

    std::cout << std::dec << std::endl;
}

static void parse_17202(const uint8_t* b, size_t len) {
    dump_market_record("17202", b, len);
    if (len < 42) return;
    uint16_t recs = be16(b + 40);
    size_t offset = 42;
    for (uint16_t i = 0; i < recs; i++) {
        if (offset + 38 > len) break;
        uint32_t token = be32(b + offset);

        {
            auto dbg_it = TOKEN_MAP.find(token);

            if (dbg_it != TOKEN_MAP.end())
            {
                static int token_diag = 0;

                if (token_diag < 30)
                {
                    ++token_diag;

                    std::cout
                        << "[17202 TOKEN]"
                        << " token=" << token
                        << " symbol=" << dbg_it->second.first
                        << " exchange=" << static_cast<int>(dbg_it->second.second)
                        << std::endl;
                }
            }

            else
            {
                static int missing_diag = 0;

                if (missing_diag < 10)
                {
                    ++missing_diag;
                    std::cout
                        << "[17202 TOKEN MISS]"
                        << " token=" << token
                        << std::endl;
                }
            }
        }

        uint64_t oi = be64(b + offset + 14);
        auto it = TOKEN_MAP.find(token);
        if (it != TOKEN_MAP.end()) {
            Analytics::update_oi(it->second.first, oi);
            publish_market_oi(Exchange::NSE, token, oi);
        }
        offset += 38;
    }
}

static void parse_7202(const uint8_t* b, size_t len) {
    dump_market_record("7202", b, len);
    if (len < 42) return;
    uint16_t recs = be16(b + 40);
    size_t offset = 42;
    for (uint16_t i = 0; i < recs; i++) {
        if (offset + 26 > len) break;
        uint32_t token = be32(b + offset);
        uint32_t oi = be32(b + offset + 14);
        auto it = TOKEN_MAP.find(token);
        if (it != TOKEN_MAP.end()) {
            Analytics::update_oi(it->second.first, oi);
            publish_market_oi(Exchange::NSE, token, oi);
        }
        offset += 26;
    }
}

static void parse_17201(const uint8_t* b, size_t len) {
    dump_market_record("17201", b, len);
    if (len < 42) return;
    uint16_t recs = be16(b + 40);
    size_t offset = 42;
    for (uint16_t i = 0; i < recs; i++) {
        if (offset + 90 > len) break;
        uint32_t token = be32(b + offset);
        uint64_t oi = be64(b + offset + 82);
        auto it = TOKEN_MAP.find(token);
        if (it != TOKEN_MAP.end()) {
            Analytics::update_oi(it->second.first, oi);
            publish_market_oi(Exchange::NSE, token, oi);
        }
        offset += 90;
    }
}

static void parse_7201(const uint8_t* b, size_t len) {
    dump_market_record("7201", b, len);
    if (len < 42) return;
    uint16_t recs = be16(b + 40);
    size_t offset = 42;
    for (uint16_t i = 0; i < recs; i++) {
        if (offset + 86 > len) break;
        uint32_t token = be32(b + offset);
        uint32_t oi = be32(b + offset + 82);
        auto it = TOKEN_MAP.find(token);
        if (it != TOKEN_MAP.end()) {
            Analytics::update_oi(it->second.first, oi);
            publish_market_oi(Exchange::NSE, token, oi);
        }
        offset += 86;
    }
}

static void parse_7220(const uint8_t* b, size_t len) {
    if (len < 44) return;
    uint32_t recs = be32(b + 40);
    size_t offset = 44;
    for (uint32_t i = 0; i < recs; i++) {
        if (offset + 12 > len) break;
        uint32_t token = be32(b + offset);
        double high = be32(b + offset + 4) / 100.0;
        double low = be32(b + offset + 8) / 100.0;
        auto it = TOKEN_MAP.find(token);
        if (it != TOKEN_MAP.end()) Analytics::update_lpp(it->second.first, high, low);
        offset += 12;
    }
}

static void parse_17130(const uint8_t* b, size_t len) {
    if (len < 40) return;
    uint16_t recs = be16(b + 12);
    size_t offset = 40;
    for (uint16_t i = 0; i < recs; i++) {
        if (offset + 12 > len) break;
        uint32_t token = be32(b + offset);
        uint64_t oi = be64(b + offset + 4);
        auto it = TOKEN_MAP.find(token);
        if (it != TOKEN_MAP.end()) {
            Analytics::update_oi(it->second.first, oi);
            publish_market_oi(Exchange::NSE, token, oi);
        }
        offset += 12;
    }
}

static void parse_7130(const uint8_t* b, size_t len) {
    if (len < 40) return;
    uint16_t recs = be16(b + 12);
    size_t offset = 40;
    for (uint16_t i = 0; i < recs; i++) {
        if (offset + 8 > len) break;
        uint32_t token = be32(b + offset);
        uint32_t oi = be32(b + offset + 4);
        auto it = TOKEN_MAP.find(token);
        if (it != TOKEN_MAP.end()) {
            Analytics::update_oi(it->second.first, oi);
            publish_market_oi(Exchange::NSE, token, oi);
        }
        offset += 8;
    }
}

void process_packet(Exchange exchange, const uint8_t* buf, size_t len)
{
    total_packets++;

    if (exchange == Exchange::BSE_EQ) {
        // EQ tokens have a different namespace. Only the documented index
        // messages are consumed until an authoritative cash master is wired.
        if (len < 4 || (be32(buf) != 2011 && be32(buf) != 2012)) {
            print_stats();
            return;
        }
    }
    if (exchange == Exchange::BSE || exchange == Exchange::BSE_EQ) {
        // BSE Direct NFCAST does not use the same LZO chunking layer as NSE
        try {
            parse_bse_nfcast(buf, len);
            parsed_packets++;
        } catch (const std::out_of_range& error) {
            dropped_packets++;
            static std::atomic<unsigned> reports{0};
            if (reports.fetch_add(1) < 5
#ifndef KE2_FEED_LIBRARY
                && true
#else
                && false
#endif
            ) {
                std::lock_guard<std::mutex> lock(FEED_LOG_MUTEX);
                std::cout << "[BSE REJECT] type=" << (len>=4 ? be32(buf):0)
                          << " bytes=" << len << " reason=" << error.what() << std::endl;
            }
        }
        print_stats();
        return;
    }

    // Exchange::NSE LZO decompression layer below
    if (len < 4)
    {
        dropped_packets++;
        print_stats();
        return;
    }

    uint16_t packets = be16(buf + 2);

    if (packets == 0 || packets > 50)
    {
        dropped_packets++;
        print_stats();
        return;
    }

    size_t pos = 4;

    for (uint16_t i = 0; i < packets; i++)
    {
        if (pos + 2 > len)
        {
            dropped_packets++;
            break;
        }

        uint16_t comp = be16(buf + pos);
        pos += 2;

        std::vector<uint8_t> dec;
        if (comp == 0) {
            // NNF 9.48, chapter 9: zero marks an UNCOMPRESSED message.
            // The eight-byte market prefix precedes the 40-byte BCAST header.
            if (len - pos < 48) {
                ++dropped_packets; ++framing_errors; break;
            }
            const size_t message_len = be16(buf + pos + 8 + 38);
            if (message_len < 40 || message_len > len - pos - 8) {
                ++dropped_packets; ++framing_errors; break;
            }
            dec.assign(buf + pos, buf + pos + 8 + message_len);
            pos += 8 + message_len;
            ++uncompressed_packets;
        } else {
            if (size_t(comp) > len - pos) {
                ++dropped_packets; ++framing_errors; break;
            }
            const bool ok = decompress_lzo(buf + pos, comp, dec);
            pos += comp;
            if (!ok) {
                ++dropped_packets; ++lzo_errors; continue;
            }
        }

        if (dec.size() <= 18)
        {
            dropped_packets++;
            continue;
        }

        const uint8_t* b = dec.data() + 8;
        size_t blen = dec.size() - 8;

        if (blen < 12)
        {
            dropped_packets++;
            continue;
        }

        uint16_t tx = be16(b + 10);

        switch (tx)
        {
            case 17208:
            case 7208:
                parse_7208(b, blen);
                parsed_packets++;
                break;
            case 17202:
                parse_17202(b, blen);
                parsed_packets++;
                break;
            case 7202:
                parse_7202(b, blen);
                parsed_packets++;
                break;
            case 17201:
                parse_17201(b, blen);
                parsed_packets++;
                break;
            case 7201:
                parse_7201(b, blen);
                parsed_packets++;
                break;
            case 7220:
                parse_7220(b, blen);
                parsed_packets++;
                break;
            case 17130:
                parse_17130(b, blen);
                parsed_packets++;
                break;
            case 7130:
                parse_7130(b, blen);
                parsed_packets++;
                break;
            default:
                break;
        }
    }

    print_stats();
}