#include <arpa/inet.h>
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cctype>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <lzo/lzo1z.h>
#include <map>
#include <mutex>
#include <netinet/in.h>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <deque>
#include <ctime>
#include <unistd.h>

namespace
{
    // ============================================================
    // CONFIG
    // ============================================================

    constexpr const char* MULTICAST_IP = "233.1.2.5";
    constexpr int MULTICAST_PORT = 34330;

    // ============================================================
    // CAPITAL MARKET / CM MULTICAST
    // ============================================================

    constexpr const char* CM_MULTICAST_IP = "233.1.2.5";
    constexpr int CM_MULTICAST_PORT = 34074;

    constexpr uint16_t CM_TX_7208 = 7208;
    constexpr uint16_t CM_TX_7207 = 7207;
    constexpr uint16_t CM_TX_8207 = 8207;
    constexpr size_t CM_INDEX_RECORD_START = 42;
    constexpr size_t CM_INDEX_RECORD_SIZE = 72;
    constexpr size_t CM_INDEX_VALUE_OFFSET = 22;
    constexpr size_t CM_INDEX_NAME_SIZE = 21;


    constexpr size_t CM_OUTER_HEADER_SIZE = 4;
    constexpr size_t CM_BCAST_HEADER_SIZE = 40;
    constexpr size_t CM_RECORD_COUNT_OFFSET = 40;
    constexpr size_t CM_RECORD_START = 42;
    constexpr size_t CM_RECORD_SIZE = 262;

    constexpr size_t CM_INDICATIVE_CLOSE_OFFSET = 258;
    constexpr size_t CM_BID_PRICE_OFFSET = 68;
    constexpr size_t CM_ASK_PRICE_OFFSET = 148;


    constexpr size_t CM_MAX_PACKETS = 50;

    constexpr const char* HTTP_BIND = "0.0.0.0";
    constexpr int HTTP_PORT = 8100;

    constexpr const char* CONTRACT_FILE = "shared/GreekTokens.csv";
    constexpr const char* CM_SECURITY_FILE =
        "/mnt/greektokens_share/GreekExcel_Kim/security.txt";

    constexpr uint16_t TX_7201 = 7201;
    constexpr uint16_t TX_7207 = 7207;
    constexpr uint16_t TX_7208 = 7208;

    constexpr uint16_t TX_8207 = 8207;

    constexpr uint16_t TX_17201 = 17201;
    constexpr uint16_t TX_17202 = 17202;
    constexpr uint16_t TX_17208 = 17208;
    constexpr uint16_t TX_17211 = 17211;

    constexpr size_t FNO_HEADER_SIZE = 40;
    constexpr size_t FNO_COUNT_SIZE = 2;
    constexpr size_t FNO_START = 42;
    constexpr size_t FNO_RECORD_SIZE = 214;

    constexpr size_t INDEX_HEADER_SIZE = 40;
    constexpr size_t INDEX_COUNT_SIZE = 2;
    constexpr size_t INDEX_START = 42;
    constexpr size_t INDEX_RECORD_SIZE = 71;

    constexpr size_t MAX_PACKET = 65536;
    constexpr size_t MAX_DECOMPRESSED = 65536;

    constexpr uint8_t LZO_MAGIC[8] =
    {
        0x59, 0x5A, 0x2A, 0x2A,
        0x31, 0x32, 0x33, 0x00
    };

    volatile std::sig_atomic_t g_running = 1;

    // ============================================================
    // CONTRACT / FUTURE STATE
    // ============================================================

    struct FutureQuote
    {
        uint32_t token = 0;

        std::string symbol;
        std::string expiry;

        double ltp = 0.0;
        double bid = 0.0;
        double ask = 0.0;

        bool has_ltp = false;
        bool has_bid = false;
        bool has_ask = false;

        uint64_t updates = 0;
        uint64_t last_rx_ms = 0;
    };

    std::unordered_map<uint32_t, FutureQuote> g_futures;

    // Symbols that have a nearest non-expired monthly F&O future.
    std::unordered_set<std::string> g_monthly_future_symbols;


    // ============================================================
    // INDEX STATE
    // ============================================================

    struct IndexQuote
    {
        std::string name;

        double live_value = 0.0;
        double indicative_value = 0.0;

        bool has_live = false;
        bool has_indicative = false;

        uint64_t live_updates = 0;
        uint64_t indicative_updates = 0;

        std::string live_time;
        std::string indicative_time;
    };

    std::unordered_map<std::string, IndexQuote> g_indices;

    // ============================================================
    // CAPITAL MARKET / INDICATIVE CLOSE STATE
    // ============================================================

    struct CmQuote
    {
        uint32_t token = 0;
        std::string symbol;

        // BCAST_HEADER AlphaChar contains only the first two
        // characters of the symbol. It is retained for diagnostics.
        std::string alpha;

        double ltp = 0.0;
        double bid = 0.0;
        double ask = 0.0;
        double closing_price = 0.0;
        double open_price = 0.0;
        double high_price = 0.0;
        double low_price = 0.0;
        double indicative_close = 0.0;

        bool has_ltp = false;
        bool has_bid = false;
        bool has_ask = false;
        bool has_closing_price = false;
        bool has_open_price = false;
        bool has_high_price = false;
        bool has_low_price = false;
        bool has_indicative_close = false;

        uint64_t updates = 0;
        uint64_t last_rx_ms = 0;
    };

    std::unordered_map<uint32_t, CmQuote> g_cm_quotes;

    // Forward declarations used by CM symbol-map loader.
    static std::string trim(std::string s);
    static std::vector<std::string>
    parse_csv_line(const std::string& line);


    std::unordered_map<uint32_t, std::string> g_cm_symbol_by_token;

    static void load_cm_symbol_map()
    {
        std::ifstream f(CM_SECURITY_FILE);

        if (!f)
        {
            std::cerr
                << "[CM] WARNING: cannot open "
                << CM_SECURITY_FILE
                << " for symbol map\n";
            return;
        }

        g_cm_symbol_by_token.clear();

        std::string line;

        while (std::getline(f, line))
        {
            if (line.empty())
                continue;

            // security.txt is pipe-delimited:
            // field 0 = NSE CM security token
            // field 1 = ticker symbol
            // field 2 = security series
            //
            // Keep only normal EQ cash instruments for the
            // cash/futures combined dashboard.
            std::vector<std::string> cols;
            std::string field;
            std::stringstream ss(line);

            while (std::getline(ss, field, '|'))
                cols.push_back(field);

            if (cols.size() < 3)
                continue;

            if (cols[2] != "EQ")
                continue;

            try
            {
                const uint32_t token =
                    static_cast<uint32_t>(
                        std::stoul(trim(cols[0]))
                    );

                const std::string symbol =
                    trim(cols[1]);

                if (token != 0 && !symbol.empty())
                    g_cm_symbol_by_token[token] = symbol;
            }
            catch (...)
            {
                // Version/header/malformed rows are ignored.
            }
        }

        std::cerr
            << "[CM] symbol map loaded from "
            << CM_SECURITY_FILE
            << ": "
            << g_cm_symbol_by_token.size()
            << " EQ symbols\n";
    }


    // ============================================================
    // GLOBAL DIAGNOSTICS
    // ============================================================

    uint64_t g_udp_packets = 0;
    uint64_t g_decomp_ok = 0;
    uint64_t g_decomp_fail = 0;

    uint64_t g_cm_udp_packets = 0;
    uint64_t g_cm_decomp_ok = 0;
    uint64_t g_cm_decomp_fail = 0;
    uint64_t g_cm_7208 = 0;
    uint64_t g_cm_updates = 0;

    uint64_t g_tx_7201 = 0;
    uint64_t g_tx_7207 = 0;
    uint64_t g_tx_7208 = 0;
    uint64_t g_tx_8207 = 0;
    uint64_t g_tx_17201 = 0;
    uint64_t g_tx_17202 = 0;
    uint64_t g_tx_17208 = 0;
    uint64_t g_tx_17211 = 0;

    uint64_t g_future_updates = 0;
    uint64_t g_unknown_future_tokens = 0;

    uint64_t g_last_rx_ms = 0;

    std::mutex g_state_mtx;

    // ============================================================
    // HELPERS
    // ============================================================

    static void signal_handler(int)
    {
        g_running = 0;
    }

    static inline uint16_t be16(const uint8_t* p)
    {
        return static_cast<uint16_t>(
            (static_cast<uint16_t>(p[0]) << 8) |
            static_cast<uint16_t>(p[1])
        );
    }

    static inline uint32_t be32(const uint8_t* p)
    {
        return
            (static_cast<uint32_t>(p[0]) << 24) |
            (static_cast<uint32_t>(p[1]) << 16) |
            (static_cast<uint32_t>(p[2]) << 8)  |
            static_cast<uint32_t>(p[3]);
    }

    static inline uint64_t be64_u(const uint8_t* p)
    {
        uint64_t v = 0;

        for (int i = 0; i < 8; ++i)
        {
            v =
                (v << 8) |
                static_cast<uint64_t>(p[i]);
        }

        return v;
    }

    static uint64_t now_ms()
    {
        using namespace std::chrono;

        return static_cast<uint64_t>(
            duration_cast<milliseconds>(
                system_clock::now().time_since_epoch()
            ).count()
        );
    }

    static std::string now_ist()
    {
        using namespace std::chrono;

        const auto now = system_clock::now();
        const std::time_t tt =
            system_clock::to_time_t(now);

        std::tm tm{};

        localtime_r(&tt, &tm);

        const auto ms =
            duration_cast<milliseconds>(
                now.time_since_epoch()
            ) % 1000;

        std::ostringstream ss;

        ss << std::put_time(
                 &tm,
                 "%Y-%m-%dT%H:%M:%S"
             )
           << '.'
           << std::setw(3)
           << std::setfill('0')
           << ms.count()
           << "+05:30";

        return ss.str();
    }

    static std::string trim(std::string s)
    {
        while (
            !s.empty() &&
            std::isspace(
                static_cast<unsigned char>(s.front())
            )
        )
        {
            s.erase(s.begin());
        }

        while (
            !s.empty() &&
            std::isspace(
                static_cast<unsigned char>(s.back())
            )
        )
        {
            s.pop_back();
        }

        return s;
    }

    static std::vector<std::string>
    parse_csv_line(const std::string& line)
    {
        std::vector<std::string> out;

        std::string cell;
        bool in_quotes = false;

        for (char c : line)
        {
            if (c == '"')
            {
                in_quotes = !in_quotes;
                continue;
            }

            if (c == ',' && !in_quotes)
            {
                out.push_back(trim(cell));
                cell.clear();
            }
            else
            {
                cell.push_back(c);
            }
        }

        out.push_back(trim(cell));

        return out;
    }

    static std::string normalize_name(
        const std::string& src)
    {
        std::string s;

        for (char c : src)
        {
            if (
                std::isalnum(
                    static_cast<unsigned char>(c)
                )
            )
            {
                s.push_back(
                    static_cast<char>(
                        std::toupper(
                            static_cast<unsigned char>(c)
                        )
                    )
                );
            }
        }

        return s;
    }

    static std::string json_escape(
        const std::string& s)
    {
        std::string out;

        out.reserve(s.size() + 8);

        for (char c : s)
        {
            switch (c)
            {
                case '"':
                    out += "\\\"";
                    break;

                case '\\':
                    out += "\\\\";
                    break;

                case '\n':
                    out += "\\n";
                    break;

                case '\r':
                    out += "\\r";
                    break;

                case '\t':
                    out += "\\t";
                    break;

                default:
                    out += c;
                    break;
            }
        }

        return out;
    }

    // ============================================================
    // EXPIRY SORTING
    // ============================================================

    static int month_number(
        const std::string& m)
    {
        if (m == "JAN") return 1;
        if (m == "FEB") return 2;
        if (m == "MAR") return 3;
        if (m == "APR") return 4;
        if (m == "MAY") return 5;
        if (m == "JUN") return 6;
        if (m == "JUL") return 7;
        if (m == "AUG") return 8;
        if (m == "SEP") return 9;
        if (m == "OCT") return 10;
        if (m == "NOV") return 11;
        if (m == "DEC") return 12;

        return 0;
    }

    static long expiry_sort_key(
        const std::string& expiry)
    {
        std::stringstream ss(expiry);

        std::string d;
        std::string m;
        std::string y;

        std::getline(ss, d, '-');
        std::getline(ss, m, '-');
        std::getline(ss, y, '-');

        if (
            d.empty() ||
            m.empty() ||
            y.empty()
        )
        {
            return 0;
        }

        try
        {
            int day = std::stoi(d);
            int year = std::stoi(y);

            if (year < 100)
                year += 2000;

            int mon = month_number(
                normalize_name(m)
            );

            // Reject malformed expiry dates such as 0-DEC-36.
            if (
                day < 1 ||
                day > 31 ||
                mon < 1 ||
                mon > 12
            )
            {
                return 0;
            }

            return
                static_cast<long>(year) * 10000L +
                static_cast<long>(mon) * 100L +
                static_cast<long>(day);
        }
        catch (...)
        {
            return 0;
        }
    }

    // ============================================================
    // FUTURE UNIVERSE
    // ============================================================

    static bool load_future_universe()
    {
        std::ifstream f(CONTRACT_FILE);

        if (!f.is_open())
        {
            std::cerr
                << "[FNO] ERROR: cannot open "
                << CONTRACT_FILE
                << "\n";

            return false;
        }

        struct FutureCandidate
        {
            uint32_t token = 0;
            std::string symbol;
            std::string expiry;
            long expiry_key = 0;
        };

        // One nearest non-expired future per symbol.
        std::unordered_map<
            std::string,
            FutureCandidate
        > nearest;

        std::string line;

        std::getline(
            f,
            line
        );

        size_t candidates = 0;
        size_t expired = 0;
        size_t invalid_expiry = 0;

        // Current local calendar date as YYYYMMDD.
        std::time_t now = std::time(nullptr);
        std::tm today_tm{};

#if defined(_WIN32)
        localtime_s(&today_tm, &now);
#else
        localtime_r(&now, &today_tm);
#endif

        const long today_key =
            static_cast<long>(today_tm.tm_year + 1900) * 10000L +
            static_cast<long>(today_tm.tm_mon + 1) * 100L +
            static_cast<long>(today_tm.tm_mday);

        while (
            std::getline(
                f,
                line
            )
        )
        {
            auto cols =
                parse_csv_line(line);

            if (cols.size() < 8)
                continue;

            const std::string exchange =
                normalize_name(cols[1]);

            const std::string symbol =
                trim(cols[3]);

            const std::string expiry =
                trim(cols[4]);

            const std::string op_type =
                normalize_name(cols[6]);

            if (exchange != "NSE")
                continue;

            if (
                op_type != "FUT" &&
                op_type != "XX"
            )
            {
                continue;
            }

            if (
                symbol.empty() ||
                expiry.empty() ||
                cols[7].empty()
            )
            {
                continue;
            }

            uint32_t token = 0;

            try
            {
                token =
                    static_cast<uint32_t>(
                        std::stoul(
                            cols[7]
                        )
                    );
            }
            catch (...)
            {
                continue;
            }

            if (token == 0)
                continue;

            const long exp_key =
                expiry_sort_key(expiry);

            if (exp_key <= 0)
            {
                ++invalid_expiry;
                continue;
            }

            if (exp_key < today_key)
            {
                ++expired;
                continue;
            }

            ++candidates;

            auto it =
                nearest.find(symbol);

            if (
                it == nearest.end() ||
                exp_key < it->second.expiry_key
            )
            {
                nearest[symbol] =
                    FutureCandidate{
                        token,
                        symbol,
                        expiry,
                        exp_key
                    };
            }
        }

        // Same 216-symbol universe used by F&O.
        // CM processing below is restricted to these symbols.
        g_monthly_future_symbols.clear();

        for (const auto& [future_symbol, candidate] : nearest)
        {
            g_monthly_future_symbols.insert(
                normalize_name(future_symbol)
            );
        }

        // Rebuild the live universe using ONLY the nearest future
        // independently selected for each symbol.
        g_futures.clear();

        size_t count = 0;

        for (const auto& [symbol, candidate] : nearest)
        {
            FutureQuote q;

            q.token = candidate.token;
            q.symbol = candidate.symbol;
            q.expiry = candidate.expiry;

            g_futures[candidate.token] = q;

            ++count;
        }

        std::cout
            << "[FNO] Future candidates: "
            << candidates
            << "\n";

        std::cout
            << "[FNO] Expired futures skipped: "
            << expired
            << "\n";

        std::cout
            << "[FNO] Invalid expiries skipped: "
            << invalid_expiry
            << "\n";

        std::cout
            << "[FNO] Nearest futures by symbol: "
            << count
            << "\n";

        std::cout
            << "[FNO] Unique nearest-future tokens: "
            << g_futures.size()
            << "\n";

        return !g_futures.empty();
    }

    // ============================================================
    // SEEDED LZO1Z DECOMPRESSOR
    // ============================================================

    struct DecompResult
    {
        std::vector<uint8_t> data;
        bool ok = false;
    };

    static DecompResult
    lzo1z_decompress_seeded(
        const uint8_t* src,
        size_t n,
        const uint8_t* seed,
        size_t seed_len)
    {
        DecompResult r;

        r.data.reserve(
            seed_len + n * 4
        );

        r.data.insert(
            r.data.end(),
            seed,
            seed + seed_len
        );

        size_t ip = 0;

        auto rb =
            [&]() -> uint8_t
            {
                if (ip >= n)
                    throw std::out_of_range(
                        "read"
                    );

                return src[ip++];
            };

        auto copy_lit =
            [&](size_t c)
            {
                if (ip + c > n)
                    throw std::out_of_range(
                        "literal"
                    );

                r.data.insert(
                    r.data.end(),
                    src + ip,
                    src + ip + c
                );

                ip += c;
            };

        auto copy_match =
            [&](size_t back, size_t len)
            {
                if (
                    !back ||
                    r.data.size() < back
                )
                {
                    throw std::runtime_error(
                        "match"
                    );
                }

                size_t mp =
                    r.data.size() - back;

                for (
                    size_t i = 0;
                    i < len;
                    ++i
                )
                {
                    r.data.push_back(
                        r.data[mp++]
                    );
                }
            };

        try
        {
            uint8_t t = rb();

            if (t > 17)
            {
                copy_lit(
                    t - 17
                );

                t = rb();
            }

            for (;;)
            {
                if (t < 16)
                {
                    size_t c = t;

                    if (!c)
                    {
                        while (
                            ip < n &&
                            src[ip] == 0
                        )
                        {
                            c += 255;
                            ++ip;
                        }

                        c +=
                            15 +
                            src[ip++];
                    }

                    copy_lit(
                        c + 3
                    );

                    t = rb();

                    continue;
                }

                for (;;)
                {
                    if (t >= 64)
                    {
                        copy_match(
                            ((t >> 2) & 7) |
                            (rb() << 3),
                            ((t >> 5) & 3) + 2
                        );
                    }
                    else if (t >= 32)
                    {
                        size_t len =
                            t & 0x1f;

                        if (!len)
                        {
                            while (
                                ip < n &&
                                src[ip] == 0
                            )
                            {
                                len += 255;
                                ++ip;
                            }

                            len +=
                                31 +
                                src[ip++];
                        }

                        len += 2;

                        uint8_t b1 = rb();
                        uint8_t b2 = rb();

                        copy_match(
                            (b1 >> 2) |
                            (b2 << 6),
                            len
                        );
                    }
                    else if (t >= 16)
                    {
                        size_t len =
                            t & 7;

                        if (!len)
                        {
                            while (
                                ip < n &&
                                src[ip] == 0
                            )
                            {
                                len += 255;
                                ++ip;
                            }

                            len +=
                                7 +
                                src[ip++];
                        }

                        len += 2;

                        uint8_t b1 = rb();
                        uint8_t b2 = rb();

                        size_t back =
                            (b1 >> 2) |
                            (b2 << 6);

                        back +=
                            (1 << 14) |
                            (((t >> 3) & 1) << 13);

                        copy_match(
                            back,
                            len
                        );
                    }
                    else
                    {
                        break;
                    }

                    if (ip >= n)
                    {
                        r.ok = true;
                        goto done;
                    }

                    t = rb();

                    if (t == 17)
                    {
                        r.ok = true;
                        goto done;
                    }

                    uint8_t lit =
                        t & 3;

                    if (lit)
                        copy_lit(lit);

                    t = rb();
                }

                if (t == 17)
                {
                    r.ok = true;
                    goto done;
                }

                uint8_t lit =
                    t & 3;

                if (!lit)
                    break;

                copy_lit(lit);

                t = rb();
            }

            r.ok = true;
        }
        catch (...)
        {
        }

    done:

        if (r.data.size() > seed_len)
        {
            r.data.erase(
                r.data.begin(),
                r.data.begin() +
                static_cast<ptrdiff_t>(
                    seed_len
                )
            );
        }
        else
        {
            r.data.clear();
        }

        return r;
    }

    // ============================================================
    // PROCESS INDEX 7207
    // ============================================================

    // ============================================================
    // PROCESS INDEX 8207
    // ============================================================

    // ============================================================
    // PROCESS CM 7208 / BCAST_ONLY_MBP
    // ============================================================

    static void parse_cm_7208(
        const uint8_t* b,
        size_t len)
    {
        /*
         * NSE CM BCAST_ONLY_MBP:
         *
         * BCAST_HEADER              40 bytes @ 0
         * NoOfRecords                2 bytes @ 40
         * Record data               262 bytes @ 42
         *
         * Record:
         *   Token                  +0
         *   LastTradedPrice       +16
         *   ClosingPrice         +242
         *   OpenPrice            +246
         *   HighPrice            +250
         *   LowPrice             +254
         *   IndicativeClosePrice +258
         *
         * All prices are integer price * 100.
         */

        if (len < CM_RECORD_START)
            return;

        const uint16_t records =
            be16(b + CM_RECORD_COUNT_OFFSET);

        if (records == 0 || records > 100)
            return;

        const size_t available =
            len - CM_RECORD_START;

        const size_t max_records =
            available / CM_RECORD_SIZE;

        const size_t actual_records =
            std::min<size_t>(
                records,
                max_records
            );

        if (actual_records == 0)
            return;

        char alpha_buf[3] = {};
        alpha_buf[0] =
            static_cast<char>(b[8]);
        alpha_buf[1] =
            static_cast<char>(b[9]);

        std::string alpha(alpha_buf);

        for (size_t r = 0; r < actual_records; ++r)
        {
            const uint8_t* rec =
                b +
                CM_RECORD_START +
                r * CM_RECORD_SIZE;

            const uint32_t token =
                be32(rec);

        // CM_FUTURE_UNIVERSE_FILTER
        // Only process securities whose symbol has a nearest
        // non-expired monthly F&O future.
        const auto cm_symbol_it =
            g_cm_symbol_by_token.find(token);

        if (
            cm_symbol_it ==
            g_cm_symbol_by_token.end()
        )
        {
            continue;
        }

        const std::string cm_symbol_key =
            normalize_name(
                cm_symbol_it->second
            );

        if (
            g_monthly_future_symbols.find(
                cm_symbol_key
            ) ==
            g_monthly_future_symbols.end()
        )
        {
            continue;
        }


            if (token == 0)
                continue;

            const double ltp =
                static_cast<double>(
                    be32(
                        rec + 16
                    )
                ) / 100.0;

            const double closing_price =
                static_cast<double>(
                    be32(
                        rec + 242
                    )
                ) / 100.0;

            const double open_price =
                static_cast<double>(
                    be32(
                        rec + 246
                    )
                ) / 100.0;

            const double high_price =
                static_cast<double>(
                    be32(
                        rec + 250
                    )
                ) / 100.0;

            const double low_price =
                static_cast<double>(
                    be32(
                        rec + 254
                    )
                ) / 100.0;

            const double bid =
                static_cast<double>(
                    be32(rec + CM_BID_PRICE_OFFSET)
                ) / 100.0;

            const double ask =
                static_cast<double>(
                    be32(rec + CM_ASK_PRICE_OFFSET)
                ) / 100.0;

            const double indicative_close =
                static_cast<double>(
                    be32(
                        rec + CM_INDICATIVE_CLOSE_OFFSET
                    )
                ) / 100.0;

            std::lock_guard<std::mutex> lock(
                g_state_mtx
            );

            CmQuote& q =
                g_cm_quotes[token];

            q.token = token;
            q.symbol =
                g_cm_symbol_by_token.count(token)
                    ? g_cm_symbol_by_token[token]
                    : std::string();
            q.alpha = alpha;

            q.ltp = ltp;
            q.bid = bid;
            q.ask = ask;
            q.closing_price = closing_price;
            q.open_price = open_price;
            q.high_price = high_price;
            q.low_price = low_price;
            q.indicative_close = indicative_close;

            q.has_ltp = (ltp > 0.0);
            q.has_bid = (bid > 0.0);
            q.has_ask = (ask > 0.0);
            q.has_closing_price = (closing_price > 0.0);
            q.has_open_price = (open_price > 0.0);
            q.has_high_price = (high_price > 0.0);
            q.has_low_price = (low_price > 0.0);

            /*
             * IMPORTANT:
             *
             * This is the ACTUAL NSE IndicativeClosePrice field.
             * Do not calculate it from the order book.
             *
             * NSE sends zero outside the indicative-close period.
             */
            q.has_indicative_close =
                (indicative_close > 0.0);

            ++q.updates;
            q.last_rx_ms = now_ms();

            ++g_cm_updates;
        }
    }

    // ============================================================
    // PROCESS ONE CM BROADCAST MESSAGE
    // ============================================================


    // ============================================================
    // CM 7207 - LIVE INDEX VALUES
    //
    // BCAST_INDICES
    // Record size = 71 bytes
    // IndexName  +0   (21 bytes)
    // IndexValue +21  (LONG, paisa/100)
    // ============================================================
    
    static std::string canonical_cm_index_name(
        const uint8_t* rec)
    {
        char raw[23] = {};

        std::memcpy(
            raw,
            rec,
            22
        );

        std::string name;

        for (int i = 0; i < 22; ++i)
        {
            const unsigned char c =
                static_cast<unsigned char>(raw[i]);

            if (c == 0)
                break;

            if (c >= 32 && c <= 126)
            {
                name.push_back(
                    static_cast<char>(c)
                );
            }
        }

        const size_t first =
            name.find_first_not_of(' ');

        if (first == std::string::npos)
            return "";

        const size_t last =
            name.find_last_not_of(' ');

        name =
            name.substr(
                first,
                last - first + 1
            );

        std::string normalized;
        bool previous_space = false;

        for (char c : name)
        {
            if (c == ' ')
            {
                if (!previous_space)
                {
                    normalized.push_back(' ');
                    previous_space = true;
                }
            }
            else
            {
                normalized.push_back(
                    static_cast<char>(
                        std::toupper(
                            static_cast<unsigned char>(c)
                        )
                    )
                );

                previous_space = false;
            }
        }

        name = normalized;

        if (
            name == "NIFTY" ||
            name == "NIFTY 50" ||
            name == "NIFTY50"
        )
        {
            return "NIFTY";
        }

        if (
            name == "BANKNIFTY" ||
            name == "NIFTY BANK"
        )
        {
            return "BANKNIFTY";
        }

        if (
            name == "FINNIFTY" ||
            name == "NIFTY FIN SERVICE" ||
            name == "NIFTY FIN SERV" ||
            name == "NIFTY FINANCIAL SERVICES"
        )
        {
            return "FINNIFTY";
        }

        if (
            name == "MIDCPNIFTY" ||
            name == "NIFTY MIDCAP SELECT" ||
            name == "NIFTY MIDCAP"
        )
        {
            return "MIDCPNIFTY";
        }

        if (
            name == "NIFTYNXT50" ||
            name == "NIFTY NEXT 50"
        )
        {
            return "NIFTYNXT50";
        }

        static int cm_index_name_diag_count = 0;

        if (cm_index_name_diag_count < 100)
        {
            ++cm_index_name_diag_count;

            std::cerr
                << "[CM_INDEX_NAME_DIAG] name=["
                << name
                << "]\\n";
        }

        return "";
    }



static void parse_cm_index_7207(
        const uint8_t* b,
        size_t len)
    {
        if (len < CM_INDEX_RECORD_START)
            return;

        const uint16_t records =
            be16(
                b + CM_RECORD_COUNT_OFFSET
            );

        // NSE BCAST_INDICES supports at most 6 records.
        if (
            records == 0 ||
            records > 6
        )
        {
            return;
        }

        const size_t required =
            CM_INDEX_RECORD_START
            + static_cast<size_t>(records)
              * CM_INDEX_RECORD_SIZE;

        if (required > len)
            return;

        for (
            uint16_t i = 0;
            i < records;
            ++i
        )
        {
            const uint8_t* rec =
                b
                + CM_INDEX_RECORD_START
                + static_cast<size_t>(i)
                  * CM_INDEX_RECORD_SIZE;

            const std::string canonical =
                canonical_cm_index_name(rec);

            // Ignore every index except the five
            // displayed by the dashboard.
            if (canonical.empty())
                continue;

            const uint32_t raw_live =
                be32(rec + CM_INDEX_VALUE_OFFSET);

            const double live =
                static_cast<double>(
                    raw_live
                ) / 100.0;

            if (
                canonical == "NIFTY" ||
                canonical == "BANKNIFTY" ||
                canonical == "FINNIFTY" ||
                canonical == "MIDCPNIFTY" ||
                canonical == "NIFTYNXT50"
            )
            {
                static int index_raw_diag = 0;

                if (index_raw_diag < 20)
                {
                    ++index_raw_diag;


                }
            }

            if (
                live <= 0.0 ||
                live > 100000.0
            )
            {
                continue;
            }

            std::lock_guard<std::mutex> lock(
                g_state_mtx
            );

            IndexQuote& q =
                g_indices[canonical];

            q.name = canonical;
            q.live_value = live;
            q.has_live = true;
            ++q.live_updates;
            q.live_time = now_ist();
        }
    }

static void parse_cm_index_8207(
        const uint8_t* b,
        size_t len)
    {
        if (len < CM_INDEX_RECORD_START)
            return;

        const uint16_t records =
            be16(
                b + CM_RECORD_COUNT_OFFSET
            );

        // NSE BCAST_INDICATIVE_INDICES supports
        // at most 6 records.
        if (
            records == 0 ||
            records > 6
        )
        {
            return;
        }

        const size_t required =
            CM_INDEX_RECORD_START
            + static_cast<size_t>(records)
              * CM_INDEX_RECORD_SIZE;

        if (required > len)
            return;

        for (
            uint16_t i = 0;
            i < records;
            ++i
        )
        {
            const uint8_t* rec =
                b
                + CM_INDEX_RECORD_START
                + static_cast<size_t>(i)
                  * CM_INDEX_RECORD_SIZE;

            const std::string canonical =
                canonical_cm_index_name(rec);

            if (canonical.empty())
                continue;

            const uint32_t raw_indicative =
                be32(rec + CM_INDEX_VALUE_OFFSET);

            const double indicative =
                static_cast<double>(
                    raw_indicative
                ) / 100.0;

            if (
                indicative <= 0.0 ||
                indicative > 100000.0
            )
            {
                continue;
            }

            std::lock_guard<std::mutex> lock(
                g_state_mtx
            );

            IndexQuote& q =
                g_indices[canonical];

            q.name = canonical;
            q.indicative_value =
                indicative;
            q.has_indicative = true;
            ++q.indicative_updates;
            q.indicative_time =
                now_ist();
        }
    }

static void process_cm_message(
        const uint8_t* data,
        size_t len)
    {
        /*
         * The compression packet's decompressed/uncompressed data
         * contains 8 bytes before BCAST_HEADER.
         *
         * Therefore:
         *
         *   data + 8  -> BCAST_HEADER
         *   BCAST_HEADER + 10 -> TransactionCode
         */

        if (len < 8 + CM_BCAST_HEADER_SIZE)
            return;

        const uint8_t* b =
            data + 8;

        const size_t blen =
            len - 8;

        const uint16_t tx =
            be16(b + 10);

        if (tx == CM_TX_7207)
        {
            static int cm7207_diag = 0;

            if (cm7207_diag < 5)
            {
                ++cm7207_diag;



                if (blen >= CM_INDEX_RECORD_START)
                    std::cerr
                        << be16(
                            b + CM_RECORD_COUNT_OFFSET
                        );
                else
                    std::cerr << "?";

                std::cerr << " HEX:";

                const size_t dump_len =
                    std::min<size_t>(160, blen);

                for (size_t i = 0; i < dump_len; ++i)
                {
                    if (i % 16 == 0)
                        std::cerr << "\n  ";

                    std::cerr
                        << std::hex
                        << std::setw(2)
                        << std::setfill('0')
                        << static_cast<unsigned>(
                            b[i]
                        )
                        << " "
                        << std::dec;
                }

                std::cerr << "\n";
            }

            parse_cm_index_7207(
                b,
                blen
            );
            return;
        }

        if (tx == CM_TX_8207)
        {
            static int cm8207_diag = 0;

            if (cm8207_diag < 5)
            {
                ++cm8207_diag;



                if (blen >= CM_INDEX_RECORD_START)
                    std::cerr
                        << be16(
                            b + CM_RECORD_COUNT_OFFSET
                        );
                else
                    std::cerr << "?";

                std::cerr << " HEX:";

                const size_t dump_len =
                    std::min<size_t>(160, blen);

                for (size_t i = 0; i < dump_len; ++i)
                {
                    if (i % 16 == 0)
                        std::cerr << "\n  ";

                    std::cerr
                        << std::hex
                        << std::setw(2)
                        << std::setfill('0')
                        << static_cast<unsigned>(
                            b[i]
                        )
                        << " "
                        << std::dec;
                }

                std::cerr << "\n";
            }

            parse_cm_index_8207(
                b,
                blen
            );
            return;
        }

        if (tx != CM_TX_7208)
            return;

        ++g_cm_7208;

        /*
         * MessageLength is the BCAST_HEADER field at +38.
         * Use it when valid so multiple packed messages are
         * never accidentally mixed.
         */
        size_t message_len = blen;

        if (blen >= CM_BCAST_HEADER_SIZE)
        {
            const uint16_t header_message_len =
                be16(b + 38);

            if (
                header_message_len >= CM_RECORD_START &&
                header_message_len <= blen
            )
            {
                message_len =
                    header_message_len;
            }
        }

        parse_cm_7208(
            b,
            message_len
        );
    }

    // ============================================================
    // PROCESS CM UDP PACKET
    // ============================================================

    static void process_cm_udp(
        const uint8_t* udp,
        size_t n)
    {
        ++g_cm_udp_packets;

        /*
         * NSE BcastPackData:
         *
         *   +0  cNetId[2]
         *   +2  iNoPackets (BE16)
         *   +4  packed broadcast packets
         *
         * CM / Equity network id = 4.
         */

        if (n < CM_OUTER_HEADER_SIZE)
            return;

        if (udp[0] != 0x04)
            return;

        const uint16_t packets =
            be16(udp + 2);

        if (
            packets == 0 ||
            packets > CM_MAX_PACKETS
        )
        {
            return;
        }

        size_t pos =
            CM_OUTER_HEADER_SIZE;

        for (
            uint16_t i = 0;
            i < packets;
            ++i
        )
        {
            if (pos + 2 > n)
                break;

            /*
             * Every packed packet starts with a 2-byte
             * compression length.
             */
            const uint16_t comp_len =
                be16(udp + pos);

            pos += 2;

            if (comp_len == 0)
            {
                /*
                 * Uncompressed broadcast message.
                 *
                 * The first 8 bytes precede BCAST_HEADER.
                 * BCAST_HEADER.MessageLength is at:
                 *
                 *   data + 8 + 38
                 */
                if (
                    pos + 8 + CM_BCAST_HEADER_SIZE >
                    n
                )
                {
                    break;
                }

                const uint8_t* data =
                    udp + pos;

                const size_t available =
                    n - pos;

                uint16_t message_len =
                    be16(
                        data + 8 + 38
                    );

                if (
                    message_len < CM_BCAST_HEADER_SIZE ||
                    message_len > available - 8
                )
                {
                    /*
                     * Cannot safely determine the next packed
                     * packet boundary. Do not scan arbitrary bytes.
                     */
                    break;
                }

                process_cm_message(
                    data,
                    static_cast<size_t>(
                        message_len
                    ) + 8
                );

                pos +=
                    static_cast<size_t>(
                        message_len
                    ) + 8;

                continue;
            }

            if (
                comp_len > 65535 ||
                pos + comp_len > n
            )
            {
                break;
            }

            std::vector<uint8_t> decoded(
                MAX_DECOMPRESSED
            );

            lzo_uint out_len =
                static_cast<lzo_uint>(
                    decoded.size()
                );

            const int rc =
                lzo1z_decompress(
                    udp + pos,
                    static_cast<lzo_uint>(
                        comp_len
                    ),
                    decoded.data(),
                    &out_len,
                    nullptr
                );

            pos += comp_len;

            if (rc != LZO_E_OK)
            {
                ++g_cm_decomp_fail;
                continue;
            }

            decoded.resize(
                static_cast<size_t>(
                    out_len
                )
            );

            ++g_cm_decomp_ok;

            process_cm_message(
                decoded.data(),
                decoded.size()
            );
        }
    }

    // ============================================================
    // CM MULTICAST THREAD
    // ============================================================

    static void cm_multicast_loop()
    {
        const int sock =
            socket(
                AF_INET,
                SOCK_DGRAM,
                0
            );

        if (sock < 0)
        {
            std::cerr
                << "[CM] socket() failed: "
                << std::strerror(errno)
                << "\n";

            return;
        }

        int reuse = 1;

        setsockopt(
            sock,
            SOL_SOCKET,
            SO_REUSEADDR,
            &reuse,
            sizeof(reuse)
        );

        sockaddr_in local{};

        local.sin_family =
            AF_INET;

        local.sin_port =
            htons(
                CM_MULTICAST_PORT
            );

        local.sin_addr.s_addr =
            htonl(
                INADDR_ANY
            );

        if (
            bind(
                sock,
                reinterpret_cast<sockaddr*>(
                    &local
                ),
                sizeof(local)
            ) < 0
        )
        {
            std::cerr
                << "[CM] bind failed: "
                << std::strerror(errno)
                << "\n";

            close(sock);
            return;
        }

        ip_mreq mreq{};

        mreq.imr_multiaddr.s_addr =
            inet_addr(
                CM_MULTICAST_IP
            );

        mreq.imr_interface.s_addr =
            inet_addr(
                "0.0.0.0"
            );

        if (
            setsockopt(
                sock,
                IPPROTO_IP,
                IP_ADD_MEMBERSHIP,
                &mreq,
                sizeof(mreq)
            ) < 0
        )
        {
            std::cerr
                << "[CM] multicast join failed: "
                << std::strerror(errno)
                << "\n";

            close(sock);
            return;
        }

        std::cout
            << "[CM] JOIN SUCCESS "
            << CM_MULTICAST_IP
            << ":"
            << CM_MULTICAST_PORT
            << "\n";

        std::vector<uint8_t> buf(
            MAX_PACKET
        );

        while (g_running)
        {
            const ssize_t n =
                recv(
                    sock,
                    buf.data(),
                    buf.size(),
                    0
                );

            if (n <= 0)
                continue;

            process_cm_udp(
                buf.data(),
                static_cast<size_t>(n)
            );
        }

        close(sock);
    }

    // ============================================================
    // PROCESS F&O 7208 / 17208
    // ============================================================

    static void parse_fno(
        const uint8_t* b,
        size_t len,
        uint16_t tx)
    {
        /*
         * NSE current F&O protocol:
         *
         * 7208  BCAST_ONLY_MBP
         *   record size = 214 bytes
         *   LTP         = +12
         *   MBP         = +56
         *   price       = MBP + 4
         *
         * 17208 BCAST_ENHNCD_ONLY_MBP
         *   record size = 258 bytes
         *   LTP         = +16
         *   MBP         = +60
         *   enhanced MBP price = MBP + 8
         *
         * The enhanced feed is currently the dominant feed on
         * this multicast, so it must NOT be decoded as 214-byte
         * records.
         */

        if (len < FNO_START)
            return;

        const bool enhanced =
            (tx == TX_17208);

        const size_t record_size =
            enhanced
                ? 258
                : 214;

        const size_t ltp_offset =
            enhanced
                ? 16
                : 12;

        const size_t mbp_offset =
            enhanced
                ? 60
                : 56;

        const size_t mbp_item_size =
            enhanced
                ? 16
                : 12;

        const size_t mbp_price_offset =
            enhanced
                ? 8
                : 4;

        uint16_t records =
            be16(
                b + FNO_HEADER_SIZE
            );

        if (tx == TX_17208)
        {
            static int debug_count = 0;

            if (debug_count < 5)
            {
                ++debug_count;





                const size_t dump_len =
                    std::min<size_t>(len, 80);

                for (size_t i = 0; i < dump_len; ++i)
                {
                    std::cerr
                        << " "
                        << std::hex
                        << std::setw(2)
                        << std::setfill('0')
                        << static_cast<unsigned>(
                            b[i]
                        );
                }

                std::cerr
                    << std::dec
                    << std::setfill(' ')
                    << "\n";
            }
        }

        if (
            records == 0 ||
            records > 100
        )
        {
            return;
        }

        const size_t available =
            len - FNO_START;

        const size_t max_records =
            available / record_size;

        records =
            static_cast<uint16_t>(
                std::min<size_t>(
                    records,
                    max_records
                )
            );

        for (
            uint16_t r = 0;
            r < records;
            ++r
        )
        {
            const uint8_t* rec =
                b +
                FNO_START +
                static_cast<size_t>(r) *
                record_size;

            const uint32_t token =
                be32(rec);

            auto it =
                g_futures.find(token);

            if (it == g_futures.end())
            {
                ++g_unknown_future_tokens;
                continue;
            }

            /*
             * BookType:
             * 1 = RL
             * 2 = ST
             *
             * Process only the regular/special-term books,
             * matching NSE's documented behavior.
             */
            const uint16_t book =
                be16(
                    rec + 4
                );

            if (
                book != 1 &&
                book != 2
            )
            {
                continue;
            }

            FutureQuote& q =
                it->second;

            const double ltp =
                static_cast<double>(
                    be32(
                        rec + ltp_offset
                    )
                ) / 100.0;

            const uint8_t* mbp =
                rec + mbp_offset;

            /*
             * Five buy levels followed by five sell levels.
             * We only expose best bid / best ask.
             */
            const double bid =
                static_cast<double>(
                    be32(
                        mbp +
                        mbp_price_offset
                    )
                ) / 100.0;

            const double ask =
                static_cast<double>(
                    be32(
                        mbp +
                        5 * mbp_item_size +
                        mbp_price_offset
                    )
                ) / 100.0;

            /*
             * Ignore obviously malformed zero prices.
             * A valid market update should have a positive
             * LTP / book price.
             */
            if (
                ltp <= 0.0 ||
                bid < 0.0 ||
                ask < 0.0
            )
            {
                continue;
            }

            std::lock_guard<std::mutex> lock(
                g_state_mtx
            );

            q.ltp =
                ltp;

            q.bid =
                bid;

            q.ask =
                ask;

            q.has_ltp =
                true;

            q.has_bid =
                true;

            q.has_ask =
                true;

            ++q.updates;

            q.last_rx_ms =
                now_ms();

            ++g_future_updates;
        }
    }

    // ============================================================
    // PROCESS ONE DECOMPRESSED MESSAGE
    // ============================================================

    static void process_message(
        const uint8_t* p,
        size_t len)
    {
        if (len < 4)
            return;

        const uint16_t mlen =
            be16(p);

        const uint16_t tx =
            be16(p + 2);

        if (
            mlen < 4 ||
            mlen > len
        )
        {
            return;
        }

        const uint8_t* body =
            p + 4;

        const size_t body_len =
            mlen - 4;

        switch (tx)
        {
            case TX_7201:
                ++g_tx_7201;
                break;

            case TX_7207:
                // 7207 BCAST_INDICES belongs to the NSE Cash
                // broadcast circuit, not the F&O 233.1.2.5:34330
                // stream used by this standalone F&O server.
                //
                // Do NOT parse apparent 7207 hits from this F&O
                // stream as live index data. They can be false
                // transaction-code matches inside the F&O payload.
                ++g_tx_7207;
                break;

            case TX_7208:
                ++g_tx_7208;
                parse_fno(
                    body,
                    body_len,
                    tx
                );
                break;

            case TX_8207:
                ++g_tx_8207;

                break;

            case TX_17201:
                ++g_tx_17201;
                break;

            case TX_17202:
                ++g_tx_17202;
                break;

            case TX_17208:
                ++g_tx_17208;
                parse_fno(
                    body,
                    body_len,
                    tx
                );
                break;

            case TX_17211:
                ++g_tx_17211;
                break;

            default:
                break;
        }
    }

    // ============================================================
    // PROCESS UDP PACKET
    // ============================================================

    static void process_udp(
        const uint8_t* udp,
        size_t n)
    {
        ++g_udp_packets;

        /*
         * IMPORTANT:
         *
         * Use the SAME NSE outer framing as the production decoder:
         *
         *   +0 : sequence / packet header
         *   +2 : number of compressed messages (uint16 BE)
         *   +4 : first compressed-message length (uint16 BE)
         *   +6 : first compressed payload
         *
         * Each compressed message is:
         *
         *   uint16 compressed_length
         *   uint8  compressed_data[compressed_length]
         *
         * After LZO decompression the production decoder skips
         * the first 8 bytes and reads transaction code at +10
         * from the remaining NSE message.
         *
         * DO NOT scan for transaction codes or LZO magic inside
         * arbitrary payload bytes.
         */

        if (n < 4)
            return;

        const uint16_t packets =
            be16(udp + 2);

        if (
            packets == 0 ||
            packets > 50
        )
        {
            return;
        }

        size_t pos = 4;

        for (
            uint16_t i = 0;
            i < packets;
            ++i
        )
        {
            if (pos + 2 > n)
                break;

            const uint16_t comp =
                be16(udp + pos);

            pos += 2;

            if (
                comp == 0 ||
                comp > 2048
            )
            {
                break;
            }

            if (pos + comp > n)
                break;

            std::vector<uint8_t> decoded(
                MAX_DECOMPRESSED
            );

            lzo_uint out_len =
                static_cast<lzo_uint>(
                    decoded.size()
                );

            if (
                lzo_init() != LZO_E_OK
            )
            {
                return;
            }

            const int rc =
                lzo1z_decompress(
                    udp + pos,
                    static_cast<lzo_uint>(comp),
                    decoded.data(),
                    &out_len,
                    nullptr
                );

            pos += comp;

            if (rc != LZO_E_OK)
            {
                ++g_decomp_fail;
                continue;
            }

            decoded.resize(
                static_cast<size_t>(out_len)
            );

            ++g_decomp_ok;

            /*
             * Same validation/offsets as production decoder:
             *
             *   dec + 8  -> NSE message
             *   tx       -> NSE message + 10
             */
            if (decoded.size() <= 18)
                continue;

            const uint8_t* b =
                decoded.data() + 8;

            const size_t blen =
                decoded.size() - 8;

            if (blen < 12)
                continue;

            const uint16_t tx =
                be16(b + 10);

            /*
             * Diagnostic is deliberately limited.
             * We want to prove framing before changing any
             * individual 7208/17208 record parser.
             */
            static int frame_diag = 0;

            if (frame_diag < 30)
            {
                ++frame_diag;


            }

            switch (tx)
            {
                case TX_7201:
                    ++g_tx_7201;
                    break;

                case TX_7207:
                    /*
                     * Do not parse 7207 as index data from this
                     * F&O multicast.
                     */
                    ++g_tx_7207;
                    break;

                case TX_7208:
                    ++g_tx_7208;

                    /*
                     * IMPORTANT:
                     * parse_fno() expects the NSE message beginning
                     * at its 40-byte broadcast header, exactly like
                     * production parse_7208().
                     *
                     * Therefore pass b, not b+12.
                     */
                    parse_fno(
                        b,
                        blen,
                        tx
                    );
                    break;

                case TX_8207:
                    ++g_tx_8207;


                    break;

                case TX_17201:
                    ++g_tx_17201;
                    break;

                case TX_17202:
                    ++g_tx_17202;
                    break;

                case TX_17208:
                    ++g_tx_17208;

                    parse_fno(
                        b,
                        blen,
                        tx
                    );
                    break;

                case TX_17211:
                    ++g_tx_17211;
                    break;

                default:
                    break;
            }

            g_last_rx_ms =
                now_ms();
        }
    }

    // ============================================================
    // MULTICAST THREAD
    // ============================================================

    static void multicast_loop()
    {
        const int sock =
            socket(
                AF_INET,
                SOCK_DGRAM,
                0
            );

        if (sock < 0)
        {
            std::cerr
                << "[FNO] socket() failed: "
                << std::strerror(errno)
                << "\n";

            return;
        }

        int reuse = 1;

        setsockopt(
            sock,
            SOL_SOCKET,
            SO_REUSEADDR,
            &reuse,
            sizeof(reuse)
        );

        sockaddr_in local{};

        local.sin_family =
            AF_INET;

        local.sin_port =
            htons(
                MULTICAST_PORT
            );

        local.sin_addr.s_addr =
            htonl(
                INADDR_ANY
            );

        if (
            bind(
                sock,
                reinterpret_cast<sockaddr*>(&local),
                sizeof(local)
            ) < 0
        )
        {
            std::cerr
                << "[FNO] bind failed: "
                << std::strerror(errno)
                << "\n";

            close(sock);

            return;
        }

        ip_mreq mreq{};

        mreq.imr_multiaddr.s_addr =
            inet_addr(
                MULTICAST_IP
            );

        mreq.imr_interface.s_addr =
            inet_addr("0.0.0.0");

        if (
            setsockopt(
                sock,
                IPPROTO_IP,
                IP_ADD_MEMBERSHIP,
                &mreq,
                sizeof(mreq)
            ) < 0
        )
        {
            std::cerr
                << "[FNO] multicast join failed: "
                << std::strerror(errno)
                << "\n";

            close(sock);

            return;
        }

        std::cout
            << "[FNO] JOIN SUCCESS "
            << MULTICAST_IP
            << ":"
            << MULTICAST_PORT
            << "\n";

        std::vector<uint8_t> buf(
            MAX_PACKET
        );

        while (g_running)
        {
            const ssize_t n =
                recv(
                    sock,
                    buf.data(),
                    buf.size(),
                    0
                );

            if (n <= 0)
                continue;

            process_udp(
                buf.data(),
                static_cast<size_t>(n)
            );
        }

        close(sock);
    }

    // ============================================================
    // BUILD API JSON
    // ============================================================

    static std::string make_json()
    {
        std::lock_guard<std::mutex> lock(
            g_state_mtx
        );

        // --------------------------------------------------------
        // Group futures by symbol.
        // --------------------------------------------------------

        std::map<
            std::string,
            std::vector<const FutureQuote*>
        > grouped;

        for (const auto& kv : g_futures)
        {
            const FutureQuote& q =
                kv.second;

            grouped[q.symbol]
                .push_back(&q);
        }

        for (auto& kv : grouped)
        {
            auto& futures =
                kv.second;

            std::sort(
                futures.begin(),
                futures.end(),
                [](const FutureQuote* a,
                   const FutureQuote* b)
                {
                    return expiry_sort_key(
                        a->expiry
                    ) <
                    expiry_sort_key(
                        b->expiry
                    );
                }
            );
        }

        std::ostringstream out;

        out
            << "{"
            << "\"ok\":true,"
            << "\"timestamp\":\""
            << json_escape(
                now_ist()
            )
            << "\","
            << "\"futures\":[";

        bool first_symbol = true;

        for (
            const auto& kv :
            grouped
        )
        {
            if (!first_symbol)
                out << ",";

            first_symbol = false;

            const std::string& symbol =
                kv.first;

            const auto& futures =
                kv.second;

            out
                << "{"
                << "\"symbol\":\""
                << json_escape(
                    symbol
                )
                << "\","
                << "\"rows\":[";

            bool first_future = true;

            for (
                const auto* q :
                futures
            )
            {
                if (!first_future)
                    out << ",";

                first_future = false;

                out
                    << "{"
                    << "\"expiry\":\""
                    << json_escape(
                        q->expiry
                    )
                    << "\",";

                out
                    << "\"ltp\":";

                if (q->has_ltp)
                    out << std::fixed
                        << std::setprecision(2)
                        << q->ltp;
                else
                    out << "null";

                out
                    << ",\"bid\":";

                if (q->has_bid)
                    out << std::fixed
                        << std::setprecision(2)
                        << q->bid;
                else
                    out << "null";

                out
                    << ",\"ask\":";

                if (q->has_ask)
                    out << std::fixed
                        << std::setprecision(2)
                        << q->ask;
                else
                    out << "null";

                out
                    << ",\"updates\":"
                    << q->updates
                    << "}";
            }

            out
                << "]"
                << "}";
        }

        out
            << "],"
            << "\"indices\":[";

        bool first_index = true;

        for (
            const auto& kv :
            g_indices
        )
        {
            const IndexQuote& q =
                kv.second;

            if (!first_index)
                out << ",";

            first_index = false;

            out
                << "{"
                << "\"name\":\""
                << json_escape(
                    q.name
                )
                << "\",";

            out
                << "\"live\":";

            if (q.has_live)
                out << std::fixed
                    << std::setprecision(2)
                    << q.live_value;
            else
                out << "null";

            out
                << ",\"indicative\":";

            if (q.has_indicative)
                out << std::fixed
                    << std::setprecision(2)
                    << q.indicative_value;
            else
                out << "null";

            out
                << "}";
        }

        out
            << "],"
            << "\"cm_equities\":[";

        bool first_cm = true;

        for (const auto& kv : g_cm_quotes)
        {
            const CmQuote& q =
                kv.second;

            if (!first_cm)
                out << ",";

            first_cm = false;

            out
                << "{"
                << "\"token\":"
                << q.token
                << ","
                << "\"symbol\":\""
                << json_escape(q.symbol)
                << "\","
                << "\"alpha\":\""
                << json_escape(q.alpha)
                << "\","
                << "\"ltp\":";

            if (q.has_ltp)
                out << std::fixed
                    << std::setprecision(2)
                    << q.ltp;
            else
                out << "null";

            out
                << ",\"bid\":";
            if (q.has_bid)
                out << std::fixed
                    << std::setprecision(2)
                    << q.bid;
            else
                out << "null";

            out
                << ",\"ask\":";
            if (q.has_ask)
                out << std::fixed
                    << std::setprecision(2)
                    << q.ask;
            else
                out << "null";

            out
                << ",\"closing_price\":";

            if (q.has_closing_price)
                out << std::fixed
                    << std::setprecision(2)
                    << q.closing_price;
            else
                out << "null";

            out
                << ",\"open_price\":";

            if (q.has_open_price)
                out << std::fixed
                    << std::setprecision(2)
                    << q.open_price;
            else
                out << "null";

            out
                << ",\"high_price\":";

            if (q.has_high_price)
                out << std::fixed
                    << std::setprecision(2)
                    << q.high_price;
            else
                out << "null";

            out
                << ",\"low_price\":";

            if (q.has_low_price)
                out << std::fixed
                    << std::setprecision(2)
                    << q.low_price;
            else
                out << "null";

            out
                << ",\"indicative_close\":";

            if (q.has_indicative_close)
                out << std::fixed
                    << std::setprecision(2)
                    << q.indicative_close;
            else
                out << "null";

            out
                << ",\"updates\":"
                << q.updates
                << "}";
        }

        out
            << "],"
            << "\"diag\":{"
            << "\"udp_packets\":"
            << g_udp_packets
            << ","
            << "\"decomp_ok\":"
            << g_decomp_ok
            << ","
            << "\"decomp_fail\":"
            << g_decomp_fail
            << ","
            << "\"tx_7207\":"
            << g_tx_7207
            << ","
            << "\"tx_7208\":"
            << g_tx_7208
            << ","
            << "\"tx_8207\":"
            << g_tx_8207
            << ","
            << "\"tx_17208\":"
            << g_tx_17208
            << ","
            << "\"cm_udp_packets\":"
            << g_cm_udp_packets
            << ","
            << "\"cm_decomp_ok\":"
            << g_cm_decomp_ok
            << ","
            << "\"cm_decomp_fail\":"
            << g_cm_decomp_fail
            << ","
            << "\"cm_7208\":"
            << g_cm_7208
            << ","
            << "\"cm_updates\":"
            << g_cm_updates
            << ","
            << "\"future_updates\":"
            << g_future_updates
            << ","
            << "\"unknown_future_tokens\":"
            << g_unknown_future_tokens
            << "}"
            << "}";

        return out.str();
    }

    // ============================================================
    // HTML
    // ============================================================

    static const char* HTML = R"HTML(
<!doctype html>
<html lang="en">
<head>
<link rel="icon" href="data:,">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>F&O Market Dashboard</title>

<style>
* {
    box-sizing: border-box;
}

html,
body {
    margin: 0;
    width: 100%;
    min-height: 100%;
}

body {
    background: #080d18;
    color: #e9eef8;
    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
    font-size: 13px;
}

header {
    position: sticky;
    top: 0;
    z-index: 50;

    padding: 12px 18px 10px;

    background: rgba(8,13,24,.97);
    backdrop-filter: blur(10px);

    border-bottom: 1px solid #202a3e;
}

.topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    margin-bottom: 10px;
}

.title-wrap {
    min-width: 0;
}

h1 {
    margin: 0;
    font-size: 19px;
    line-height: 1.2;
    font-weight: 750;
    letter-spacing: -.2px;
}

.subtitle {
    margin-top: 3px;
    color: #75839e;
    font-size: 11px;
}

.live {
    display: flex;
    align-items: center;
    gap: 7px;
    white-space: nowrap;

    padding: 6px 9px;
    border: 1px solid #26354e;
    border-radius: 7px;

    color: #aebbd2;
    background: #0d1423;

    font-size: 11px;
    font-weight: 650;
}

.live-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #38d996;
    box-shadow: 0 0 9px rgba(56,217,150,.65);
}

.live.offline .live-dot {
    background: #e36b76;
    box-shadow: 0 0 8px rgba(227,107,118,.5);
}

.controls {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}

.search {
    flex: 1 1 300px;
    min-width: 220px;
    max-width: 440px;

    height: 34px;
    padding: 0 11px;

    border: 1px solid #29364f;
    border-radius: 7px;

    outline: none;

    background: #0d1423;
    color: #f4f7fc;

    font: inherit;
}

.search:focus {
    border-color: #48658f;
}

.search::placeholder {
    color: #68758d;
}

button,
select {
    height: 34px;
    padding: 0 10px;

    border: 1px solid #29364f;
    border-radius: 7px;

    background: #0d1423;
    color: #b9c5d9;

    font: inherit;
    font-size: 12px;

    outline: none;
}

button {
    cursor: pointer;
}

button:hover,
select:hover {
    border-color: #415371;
}

button.active {
    background: #18243a;
    border-color: #526b91;
    color: #f2f6fd;
}

.status {
    margin-left: auto;
    color: #73819a;
    font-size: 11px;
    white-space: nowrap;
}

main {
    padding: 12px 18px 30px;
}

.summary {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 9px;

    color: #7e8ba3;
    font-size: 11px;
}

.summary strong {
    color: #c8d2e4;
    font-weight: 650;
}

.table-shell {
    width: 100%;

    border: 1px solid #202b40;
    border-radius: 8px;

    overflow: hidden;
    background: #0b1220;
}

.table-scroll {
    width: 100%;
    overflow: auto;
    max-height: calc(100vh - 145px);
}

table {
    width: 100%;
    min-width: 650px;
    border-collapse: separate;
    border-spacing: 0;
    table-layout: fixed;
}

thead th {
    position: sticky;
    top: 0;
    z-index: 10;

    height: 34px;
    padding: 0 12px;

    background: #101827;

    border-bottom: 1px solid #29344a;

    color: #7887a2;

    font-size: 10px;
    font-weight: 700;
    letter-spacing: .5px;
    text-transform: uppercase;

    text-align: right;

    cursor: pointer;
    user-select: none;
}

thead th:hover {
    color: #aebbd0;
}

thead th:first-child {
    text-align: left;
}

thead th:nth-child(1) {
    width: 28%;
}

thead th:nth-child(2) {
    width: 18%;
}

thead th:nth-child(3),
thead th:nth-child(4),
thead th:nth-child(5) {
    width: 18%;
}

tbody tr {
    height: 34px;
}

tbody tr:nth-child(even) {
    background: rgba(255,255,255,.012);
}

tbody tr:hover {
    background: #111c2d;
}

td {
    height: 34px;
    padding: 0 12px;

    border-bottom: 1px solid #172238;

    color: #c5cfdf;

    font-size: 12px;
    font-variant-numeric: tabular-nums;

    text-align: right;

    white-space: nowrap;
}

td:first-child {
    text-align: left;
}

.symbol-cell {
    color: #eef3fb;
    font-weight: 700;
    letter-spacing: .1px;
}

.expiry-cell {
    color: #7888a4;
    font-size: 11px;
}

.ltp-cell {
    color: #f3f6fb;
    font-weight: 700;
}

.bid-cell {
    color: #75d6a8;
}

.ask-cell {
    color: #e9a0a7;
}

.changed {
    animation: priceFlash .32s ease-out;
}

@keyframes priceFlash {
    0% {
        background: rgba(255,255,255,.16);
    }

    100% {
        background: transparent;
    }
}

.empty {
    padding: 35px 15px;
    text-align: center;
    color: #65738d;
}

.indices-section {
    margin-bottom: 14px;
}

.section-title {
    margin: 2px 0 7px;
    color: #9aa8be;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .7px;
}

.indices {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
}

.index-card {
    min-width: 190px;
    padding: 9px 11px;

    border: 1px solid #202b40;
    border-radius: 7px;

    background: #0d1423;
}

.index-name {
    margin-bottom: 7px;
    color: #dce4f1;
    font-weight: 700;
}

.index-values {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 7px;
}

.index-metric-label {
    color: #687690;
    font-size: 9px;
    text-transform: uppercase;
}

.index-metric-value {
    margin-top: 2px;
    color: #e8eef8;
    font-size: 13px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}

@media (max-width: 700px) {
    header {
        padding: 10px;
    }

    main {
        padding: 10px;
    }

    .status {
        width: 100%;
        margin-left: 0;
    }

    .table-scroll {
        max-height: calc(100vh - 185px);
    }
}

/* Combined Cash + Future table */
.combined-market-shell {
    margin-top: 18px;
}

.combined-market-shell .table-scroll {
    overflow-x: auto;
    overflow-y: auto;
    width: 100%;
}

.combined-market-shell table {
    min-width: 980px;
}

.combined-market-shell th:first-child,
.combined-market-shell td:first-child {
    position: sticky;
    left: 0;
    z-index: 3;
    background: #0f172a;
}

.combined-market-shell tbody td:first-child {
    background: #111827;
    font-weight: 700;
}

.combined-market-shell thead tr:first-child th {
    position: sticky;
    top: 0;
    z-index: 4;
}

.combined-market-shell thead tr:nth-child(2) th {
    position: sticky;
    top: 42px;
    z-index: 4;
}

.combined-market-shell .group-cash {
    text-align: center;
}

.combined-market-shell .group-future {
    text-align: center;
}

.combined-market-shell .cash-ltp {
    font-weight: 700;
}

.combined-market-shell .cash-bid {
    font-weight: 600;
}

.combined-market-shell .cash-ask {
    font-weight: 600;
}

.combined-market-shell .indicative-close {
    font-weight: 700;
}

.combined-market-shell .future-ltp {
    font-weight: 700;
}

.combined-market-shell .future-bid {
    font-weight: 600;
}

.combined-market-shell .future-ask {
    font-weight: 600;
}

.combined-market-shell .empty-message {
    text-align: center;
    padding: 18px;
    opacity: 0.7;
}


/* Combined dashboard uses symbol search only. */
#allBtn,
#liveBtn,
#missingBtn,
#sortSelect {
    display: none !important;
}


<style>
#allBtn,
#liveBtn,
#missingBtn,
#sortSelect {
    display: none !important;
}

#favoritesBtn,
#combinedSortSelect {
    white-space: nowrap;
}

.favorite-star {
    border: 0;
    background: transparent;
    cursor: pointer;
    font-size: 18px;
    line-height: 1;
    padding: 2px 4px;
}

.favorite-star:hover {
    transform: scale(1.15);
}

.combined-fav-row {
    font-weight: 600;
}

.basis-positive {
    font-weight: 600;
}

.basis-negative {
    font-weight: 600;
}

#combinedMarketBody td,
#combinedMarketBody th {
    white-space: nowrap;
}

.indices-section {
    display: none !important;
}

<style>

/* ============================================================
   MARKET DASHBOARD UI UPGRADE
   ============================================================ */

:root {
    --panel-bg: rgba(13, 21, 38, 0.94);
    --panel-bg-soft: rgba(18, 28, 48, 0.72);
    --line: rgba(148, 163, 184, 0.14);
    --line-strong: rgba(148, 163, 184, 0.22);
    --text-main: #e8eef8;
    --text-muted: #8ea0ba;
    --accent: #69a7ff;
    --positive: #35d39a;
    --negative: #ff6f7d;
    --star: #ffd166;
    --index-bg: rgba(105, 167, 255, 0.10);
}


/* ------------------------------------------------------------
   PAGE
   ------------------------------------------------------------ */

body {
    overflow-x: hidden;
}

main {
    max-width: none !important;
}


/* ------------------------------------------------------------
   TOP STATUS AREA
   ------------------------------------------------------------ */

.market-toolbar {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 18px;
    margin-bottom: 14px;
}

.market-title-block {
    min-width: 240px;
}

.market-title-block .section-title {
    margin: 0;
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 0.04em;
}

.market-subtitle {
    margin-top: 5px;
    color: var(--text-muted);
    font-size: 11px;
    letter-spacing: 0.01em;
}

.market-summary {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: flex-end;
}

.summary-card {
    min-width: 82px;
    padding: 7px 11px;
    border: 1px solid var(--line);
    border-radius: 9px;
    background: var(--panel-bg-soft);
    backdrop-filter: blur(8px);
}

.summary-label {
    display: block;
    color: var(--text-muted);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
}

.summary-card strong {
    display: block;
    margin-top: 2px;
    color: var(--text-main);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
}


/* ------------------------------------------------------------
   TABLE SHELL
   ------------------------------------------------------------ */

.table-shell {
    position: relative;
    overflow: hidden !important;
    border: 1px solid var(--line-strong);
    border-radius: 11px;
    background:
        linear-gradient(
            180deg,
            rgba(15, 24, 42, 0.98),
            rgba(8, 14, 27, 0.98)
        );
    box-shadow:
        0 10px 30px rgba(0, 0, 0, 0.18);
}

.table-scroll {
    width: 100%;
    max-height: calc(100vh - 235px);
    min-height: 360px;
    overflow: auto !important;
    scrollbar-width: thin;
}


/* ------------------------------------------------------------
   TABLE
   ------------------------------------------------------------ */

.market-table {
    width: 100%;
    min-width: 1100px;
    table-layout: fixed;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 12px;
}


/* Fixed column widths */

.market-table .col-star {
    width: 38px;
}

.market-table .col-type {
    width: 66px;
}

.market-table .col-symbol {
    width: 150px;
}

.market-table .col-price {
    width: 92px;
}

.market-table .col-indicative {
    width: 98px;
}

.market-table .col-expiry {
    width: 104px;
}

.market-table .col-basis {
    width: 92px;
}


/* ------------------------------------------------------------
   HEADER
   ------------------------------------------------------------ */

.market-table thead th {
    position: sticky;
    z-index: 8;
    padding: 9px 8px;
    border-bottom: 1px solid var(--line-strong);
    background: #101a2e;
    color: #87a9db;
    text-align: right;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.055em;
    white-space: nowrap;
}

.market-table thead tr:first-child th {
    top: 0;
}

.market-table thead tr:nth-child(2) th {
    top: 34px;
    background: #0d1729;
    color: #9ab4d9;
}

.market-table thead .cash-group {
    text-align: center;
    color: #69a7ff;
}

.market-table thead .future-group {
    text-align: center;
    color: #8cbcff;
}

.market-table thead .star-header,
.market-table thead .type-header,
.market-table thead .symbol-header {
    text-align: left;
}

.market-table thead .star-header {
    text-align: center;
}


/* ------------------------------------------------------------
   STICKY FIRST COLUMNS
   ------------------------------------------------------------ */

.market-table .sticky-left {
    position: sticky;
    z-index: 10;
}

.market-table .star-header {
    left: 0;
}

.market-table .type-header {
    left: 38px;
}

.market-table .symbol-header {
    left: 104px;
}


/* ------------------------------------------------------------
   BODY
   ------------------------------------------------------------ */

.market-table tbody tr {
    transition:
        background 100ms ease,
        box-shadow 100ms ease;
}

.market-table tbody tr:nth-child(even) {
    background: rgba(255, 255, 255, 0.018);
}

.market-table tbody tr:hover {
    background: rgba(105, 167, 255, 0.075);
    box-shadow:
        inset 3px 0 0 var(--accent);
}

.market-table tbody td {
    height: 34px;
    padding: 6px 8px;
    border-bottom: 1px solid var(--line);
    color: var(--text-main);
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}


/* Sticky body columns */

.market-table tbody td:nth-child(1) {
    position: sticky;
    left: 0;
    z-index: 5;
    width: 38px;
    text-align: center;
    background: #0b1323;
}

.market-table tbody td:nth-child(2) {
    position: sticky;
    left: 38px;
    z-index: 5;
    width: 66px;
    text-align: center;
    background: #0b1323;
}

.market-table tbody td:nth-child(3) {
    position: sticky;
    left: 104px;
    z-index: 5;
    width: 150px;
    background: #0b1323;
}


/* Keep sticky backgrounds on hovered rows. */

.market-table tbody tr:hover td:nth-child(1),
.market-table tbody tr:hover td:nth-child(2),
.market-table tbody tr:hover td:nth-child(3) {
    background: #101c31;
}


/* ------------------------------------------------------------
   SYMBOL
   ------------------------------------------------------------ */

.market-symbol {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    width: 100%;
    justify-content: flex-end;
}

.symbol-name {
    font-weight: 800;
    letter-spacing: 0.02em;
}

.symbol-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 42px;
    padding: 2px 5px;
    border-radius: 5px;
    background: var(--index-bg);
    color: var(--accent);
    font-size: 8px;
    font-weight: 800;
    letter-spacing: 0.06em;
}


/* ------------------------------------------------------------
   TYPE
   ------------------------------------------------------------ */

.type-eq {
    color: #b1bed2;
    font-weight: 700;
    font-size: 10px;
}

.type-index {
    color: var(--accent);
    font-weight: 900;
    font-size: 10px;
}


/* ------------------------------------------------------------
   PRICES
   ------------------------------------------------------------ */

.cash-ltp,
.future-ltp {
    font-weight: 700;
}

.future-ltp {
    color: #e9f1ff;
}

.bid-cell {
    color: #aed7c8;
}

.ask-cell {
    color: #e6b9bd;
}


/* ------------------------------------------------------------
   BASIS %
   ------------------------------------------------------------ */

.basis-cell {
    font-weight: 850;
    letter-spacing: 0.01em;
}

.basis-positive {
    color: var(--positive) !important;
}

.basis-negative {
    color: var(--negative) !important;
}

.basis-neutral {
    color: var(--text-muted) !important;
}


/* ------------------------------------------------------------
   FAVORITE
   ------------------------------------------------------------ */

.favorite-star {
    width: 26px;
    height: 26px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: #70819b;
    cursor: pointer;
    font-size: 16px;
    transition:
        transform 100ms ease,
        background 100ms ease,
        color 100ms ease;
}

.favorite-star:hover {
    transform: scale(1.15);
    background: rgba(255, 209, 102, 0.08);
    color: var(--star);
}

.favorite-star.is-favorite {
    color: var(--star);
}

.combined-fav-row {
    background:
        linear-gradient(
            90deg,
            rgba(255, 209, 102, 0.055),
            rgba(255, 209, 102, 0.012)
        ) !important;
}

.combined-fav-row td:nth-child(1),
.combined-fav-row td:nth-child(2),
.combined-fav-row td:nth-child(3) {
    background: #171a26 !important;
}


/* ------------------------------------------------------------
   PLACEHOLDERS
   ------------------------------------------------------------ */

.market-muted {
    color: #65758f !important;
}

.no-data {
    color: #596b84;
    text-align: center !important;
    padding: 22px !important;
}


/* ------------------------------------------------------------
   TOOLBAR CONTROLS
   ------------------------------------------------------------ */

#combinedSortSelect,
#favoritesBtn,
#search {
    height: 36px;
}

#combinedSortSelect {
    min-width: 165px;
}

#favoritesBtn {
    min-width: 120px;
}

#favoritesBtn.active {
    border-color: rgba(255, 209, 102, 0.45);
    color: var(--star);
    background: rgba(255, 209, 102, 0.07);
}


/* ------------------------------------------------------------
   OLD CONTROLS
   ------------------------------------------------------------ */

#allBtn,
#liveBtn,
#missingBtn,
#sortSelect {
    display: none !important;
}

.indices-section {
    display: none !important;
}


/* ------------------------------------------------------------
   SCROLLBAR
   ------------------------------------------------------------ */

.table-scroll::-webkit-scrollbar {
    width: 9px;
    height: 9px;
}

.table-scroll::-webkit-scrollbar-track {
    background: rgba(255,255,255,0.025);
}

.table-scroll::-webkit-scrollbar-thumb {
    background: rgba(140,160,190,0.28);
    border-radius: 9px;
}

.table-scroll::-webkit-scrollbar-thumb:hover {
    background: rgba(140,160,190,0.42);
}


/* ------------------------------------------------------------
   RESPONSIVE
   ------------------------------------------------------------ */

@media (max-width: 1100px) {

    .market-toolbar {
        flex-direction: column;
    }

    .market-summary {
        justify-content: flex-start;
    }

}


<style>

/* ============================================================
   THEME TOGGLE STYLES
   ============================================================ */

#themeToggle {
    height: 36px;
    min-width: 96px;
    padding: 0 13px;
    border-radius: 8px;
    cursor: pointer;
    white-space: nowrap;
    transition:
        background 120ms ease,
        border-color 120ms ease,
        color 120ms ease,
        transform 120ms ease;
}

#themeToggle:hover {
    transform: translateY(-1px);
}


/* ============================================================
   LIGHT THEME
   ============================================================ */

html.theme-light {
    background: #eef2f7;
}

html.theme-light body {
    background: #eef2f7 !important;
    color: #172033 !important;
}

html.theme-light main {
    color: #172033 !important;
}


/* Header / top area */

html.theme-light header,
html.theme-light .header,
html.theme-light .topbar {
    background: #ffffff !important;
    color: #172033 !important;
}


/* Search / select / buttons */

html.theme-light #search,
html.theme-light #combinedSortSelect,
html.theme-light #favoritesBtn,
html.theme-light #themeToggle {
    background: #ffffff !important;
    color: #172033 !important;
    border: 1px solid #cbd5e1 !important;
}

html.theme-light #search::placeholder {
    color: #8a97a8 !important;
}

html.theme-light #favoritesBtn:hover,
html.theme-light #themeToggle:hover,
html.theme-light #combinedSortSelect:hover,
html.theme-light #search:hover {
    border-color: #94a3b8 !important;
}


/* Main market shell */

html.theme-light .table-shell {
    background: #ffffff !important;
    border: 1px solid #d7dee8 !important;
    box-shadow:
        0 8px 26px rgba(15, 23, 42, 0.08) !important;
}


/* Toolbar */

html.theme-light .market-title-block .section-title {
    color: #172033 !important;
}

html.theme-light .market-subtitle {
    color: #64748b !important;
}


/* Summary cards */

html.theme-light .summary-card {
    background: #f8fafc !important;
    border: 1px solid #dbe2ea !important;
}

html.theme-light .summary-label {
    color: #64748b !important;
}

html.theme-light .summary-card strong {
    color: #172033 !important;
}


/* Table headers */

html.theme-light .market-table thead th {
    background: #e8eef7 !important;
    color: #405a7c !important;
    border-bottom: 1px solid #cbd5e1 !important;
}

html.theme-light .market-table thead tr:nth-child(2) th {
    background: #f5f8fc !important;
    color: #526a88 !important;
}

html.theme-light .market-table thead .cash-group {
    color: #2563b8 !important;
}

html.theme-light .market-table thead .future-group {
    color: #315f9f !important;
}


/* Table body */

html.theme-light .market-table tbody td {
    color: #1f2937 !important;
    border-bottom: 1px solid #e7ebf0 !important;
}

html.theme-light .market-table tbody tr:nth-child(even) {
    background: #f8fafc !important;
}

html.theme-light .market-table tbody tr:nth-child(odd) {
    background: #ffffff !important;
}

html.theme-light .market-table tbody tr:hover {
    background: #e9f2ff !important;
}


/* Sticky columns */

html.theme-light .market-table tbody td:nth-child(1),
html.theme-light .market-table tbody td:nth-child(2),
html.theme-light .market-table tbody td:nth-child(3) {
    background: #ffffff !important;
}

html.theme-light .market-table tbody tr:nth-child(even) td:nth-child(1),
html.theme-light .market-table tbody tr:nth-child(even) td:nth-child(2),
html.theme-light .market-table tbody tr:nth-child(even) td:nth-child(3) {
    background: #f8fafc !important;
}

html.theme-light .market-table tbody tr:hover td:nth-child(1),
html.theme-light .market-table tbody tr:hover td:nth-child(2),
html.theme-light .market-table tbody tr:hover td:nth-child(3) {
    background: #e9f2ff !important;
}


/* Symbol / type */

html.theme-light .symbol-name {
    color: #172033 !important;
}

html.theme-light .type-eq {
    color: #596a82 !important;
}

html.theme-light .type-index {
    color: #2563b8 !important;
}

html.theme-light .symbol-badge {
    background: #e5efff !important;
    color: #2563b8 !important;
}


/* Prices */

html.theme-light .cash-ltp,
html.theme-light .future-ltp {
    color: #172033 !important;
}

html.theme-light .bid-cell {
    color: #087454 !important;
}

html.theme-light .ask-cell {
    color: #a52c3c !important;
}

html.theme-light .market-muted {
    color: #94a3b8 !important;
}


/* Basis */

html.theme-light .basis-positive {
    color: #00875a !important;
}

html.theme-light .basis-negative {
    color: #d32f45 !important;
}

html.theme-light .basis-neutral {
    color: #8b98aa !important;
}


/* Favorites */

html.theme-light .favorite-star {
    color: #718096 !important;
}

html.theme-light .favorite-star:hover,
html.theme-light .favorite-star.is-favorite {
    color: #d49a00 !important;
}

html.theme-light .combined-fav-row {
    background: #fff8dc !important;
}

html.theme-light .combined-fav-row td:nth-child(1),
html.theme-light .combined-fav-row td:nth-child(2),
html.theme-light .combined-fav-row td:nth-child(3) {
    background: #fff8dc !important;
}


/* Active favorites button */

html.theme-light #favoritesBtn.active {
    background: #fff7d6 !important;
    color: #936b00 !important;
    border-color: #e4c75c !important;
}


/* Click-sort headers */

html.theme-light .sortable-head:hover {
    background: #dceaff !important;
    color: #1d4f91 !important;
}

html.theme-light .sortable-head.sort-active {
    color: #173f79 !important;
}


/* Table scrollbar */

html.theme-light .table-scroll::-webkit-scrollbar-track {
    background: #edf1f5;
}

html.theme-light .table-scroll::-webkit-scrollbar-thumb {
    background: #b8c3d0;
}

html.theme-light .table-scroll::-webkit-scrollbar-thumb:hover {
    background: #98a6b7;
}


/* ============================================================
   DARK THEME
   ============================================================ */

html:not(.theme-light) #themeToggle {
    background: #0d1729 !important;
    color: #d6e3f5 !important;
    border: 1px solid rgba(148, 163, 184, 0.20) !important;
}

html:not(.theme-light) #themeToggle:hover {
    background: #152239 !important;
    border-color: rgba(148, 163, 184, 0.35) !important;
}

</style>

</style>

</style>

</style>
</head>

<body>

<header>

    <div class="topbar">

        <div class="title-wrap">
            <h1>F&O Market Dashboard</h1>

            <div class="subtitle">
                Nearest non-expired futures • NSE F&O
            </div>
        </div>

        <div id="liveBadge" class="live">
            <span class="live-dot"></span>
            <span id="liveText">CONNECTING</span>
        </div>

    </div>

    <div class="controls">

        <input
            id="search"
            class="search"
            type="search"
            placeholder="Search symbol..."
            autocomplete="off"
            spellcheck="false"
        >

        <button id="allBtn" class="active" type="button">
            All
        </button>

        <button id="liveBtn" type="button">
            Live
        </button>

        <button id="missingBtn" type="button">
            Missing
        </button>

        <select id="combinedSortSelect">
            <option value="symbol-asc">Sort: Symbol ↑</option>
            <option value="symbol-desc">Sort: Symbol ↓</option>

            <option value="cash-ltp-asc">Cash LTP ↑</option>
            <option value="cash-ltp-desc">Cash LTP ↓</option>

            <option value="cash-bid-asc">Cash Bid ↑</option>
            <option value="cash-bid-desc">Cash Bid ↓</option>

            <option value="cash-ask-asc">Cash Ask ↑</option>
            <option value="cash-ask-desc">Cash Ask ↓</option>

            <option value="cash-indicative-asc">Indicative Close ↑</option>
            <option value="cash-indicative-desc">Indicative Close ↓</option>

            <option value="future-expiry-asc">Future Expiry ↑</option>
            <option value="future-expiry-desc">Future Expiry ↓</option>

            <option value="future-ltp-asc">Future LTP ↑</option>
            <option value="future-ltp-desc">Future LTP ↓</option>

            <option value="future-bid-asc">Future Bid ↑</option>
            <option value="future-bid-desc">Future Bid ↓</option>

            <option value="future-ask-asc">Future Ask ↑</option>
            <option value="future-ask-desc">Future Ask ↓</option>

            <option value="basis-asc">Future vs Ind % ↑</option>
            <option value="basis-desc">Future vs Ind % ↓</option>

            <option value="favorites-first">★ Favorites First</option>
        </select>

        <button id="favoritesBtn" type="button">
            ★ Favorites
        </button>

        <button id="themeToggle" type="button">
            ☾ Dark
        </button>

        <div id="status" class="status">
            Connecting...
        </div>

    </div>

</header>

<main>

    <section class="table-shell combined-market-shell">
        <div class="market-toolbar">

            <div class="market-title-block">

                <div class="section-title">
                    Cash Market + Nearest Monthly Future
                </div>

                <div class="market-subtitle">
                    Live cash quote • nearest monthly future •
                    future premium / discount vs indicative close
                </div>

            </div>

            <div class="market-summary">

                <div class="summary-card">
                    <span class="summary-label">
                        SYMBOLS
                    </span>
                    <strong id="summarySymbols">
                        —
                    </strong>
                </div>

                <div class="summary-card">
                    <span class="summary-label">
                        LIVE
                    </span>
                    <strong id="summaryLive">
                        —
                    </strong>
                </div>

                <div class="summary-card">
                    <span class="summary-label">
                        CASH
                    </span>
                    <strong id="summaryCash">
                        —
                    </strong>
                </div>

                <div class="summary-card">
                    <span class="summary-label">
                        ★
                    </span>
                    <strong id="summaryFavorites">
                        0
                    </strong>
                </div>

            </div>

        </div>

        <div class="table-scroll">
            <table class="market-table">

                <colgroup>

                    <col class="col-star">
                    <col class="col-type">
                    <col class="col-symbol">

                    <col class="col-price">
                    <col class="col-price">
                    <col class="col-price">
                    <col class="col-indicative">

                    <col class="col-expiry">
                    <col class="col-price">
                    <col class="col-price">
                    <col class="col-price">
                    <col class="col-basis">

                </colgroup>

                <thead>

                    <tr class="group-header">

                        <th
                            rowspan="2"
                            class="sticky-left star-header"
                        >
                            ★
                        </th>

                        <th
                            rowspan="2"
                            class="sticky-left type-header"
                        >
                            TYPE
                        </th>

                        <th
                            rowspan="2"
                            class="sticky-left symbol-header"
                        >
                            SYMBOL
                        </th>

                        <th
                            colspan="4"
                            class="cash-group"
                        >
                            CASH MARKET
                        </th>

                        <th
                            colspan="5"
                            class="future-group"
                        >
                            NEAREST MONTHLY FUTURE
                        </th>

                    </tr>

                    <tr class="column-header">

                        <th>LTP</th>
                        <th>BID</th>
                        <th>ASK</th>
                        <th>IND CLOSE</th>

                        <th>EXPIRY</th>
                        <th>LTP</th>
                        <th>BID</th>
                        <th>ASK</th>
                        <th>FUTURE<br>VS IND %</th>

                    </tr>

                </thead>

                <tbody id="combinedMarketBody"></tbody>

            </table>
        </div>
    </section>



    <section class="indices-section">

        <div class="section-title">
            Priority Indices
        </div>

        <div id="indices" class="indices"></div>

    </section>




</main>

<script>
"use strict";

let latestData = null;
let rowsBySymbol = new Map();

let currentFilter = "all";
let currentSort = "symbol";

let refreshBusy = false;

function esc(v) {
    return String(v ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function numberValue(v) {

    if (v === null || v === undefined)
        return null;

    const n = Number(v);

    return Number.isFinite(n) ? n : null;
}

function num(v) {

    const n = numberValue(v);

    if (n === null)
        return "—";

    return n.toLocaleString(
        "en-IN",
        {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }
    );
}

function valueKey(v) {

    const n = numberValue(v);

    return n === null
        ? "null"
        : String(n);
}

function isLive(row) {

    // LTP availability determines Live/Missing status.
    // Bid and Ask are independent and may remain —.
    return numberValue(row?.ltp) !== null;
}

function renderIndices(data) {

    const el =
        document.getElementById("indices");

    const rows =
        Array.isArray(data?.indices)
            ? data.indices
            : [];

    // Keep the index panel visible even before actual
    // index records arrive from the backend.
    const configuredNames = [
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "NIFTYNXT50"
    ];

    const byName = new Map();

    for (const x of rows) {

        const name =
            String(x?.name || "").trim();

        if (name)
            byName.set(
                name.toUpperCase(),
                x
            );
    }

    const shown = [];

    for (const name of configuredNames) {

        const actual =
            byName.get(
                name.toUpperCase()
            );

        shown.push(
            actual || {
                name,
                indicative: null,
                live: null
            }
        );
    }

    // Preserve any additional index records
    // supplied by the backend.
    for (const x of rows) {

        const name =
            String(x?.name || "").trim();

        if (!name)
            continue;

        if (
            !configuredNames.includes(
                name.toUpperCase()
            )
        ) {
            shown.push(x);
        }
    }

    el.innerHTML =
        shown.map(x => `

            <div class="index-card">

                <div class="index-name">
                    ${esc(x.name)}
                </div>

                <div class="index-values">

                    <div>
                        <div class="index-metric-label">
                            Indicative
                        </div>

                        <div class="index-metric-value">
                            ${num(x.indicative)}
                        </div>
                    </div>

                    <div>
                        <div class="index-metric-label">
                            Live
                        </div>

                        <div class="index-metric-value">
                            ${num(x.live)}
                        </div>
                    </div>

                </div>

            </div>

        `).join("");
}

function getRows(data) {

    const groups =
        Array.isArray(data?.futures)
            ? data.futures
            : [];

    return groups.map(g => {

        const r =
            Array.isArray(g.rows) && g.rows.length
                ? g.rows[0]
                : {};

        return {
            symbol: String(g.symbol || ""),
            expiry: String(r.expiry || ""),
            ltp: numberValue(r.ltp),
            bid: numberValue(r.bid),
            ask: numberValue(r.ask),
            updates: Number(r.updates || 0)
        };

    });
}

function sortRows(rows) {

    const copy = rows.slice();

    copy.sort((a, b) => {

        if (currentSort === "symbol")
            return a.symbol.localeCompare(b.symbol);

        if (currentSort === "expiry")
            return a.expiry.localeCompare(b.expiry);

        const av =
            numberValue(a[currentSort]);

        const bv =
            numberValue(b[currentSort]);

        if (av === null && bv === null)
            return a.symbol.localeCompare(b.symbol);

        if (av === null)
            return 1;

        if (bv === null)
            return -1;

        if (bv !== av)
            return bv - av;

        return a.symbol.localeCompare(b.symbol);
    });

    return copy;
}

function filteredRows(rows) {

    const query =
        document.getElementById("search")
            .value
            .trim()
            .toUpperCase();

    return rows.filter(row => {

        if (
            query &&
            !row.symbol.toUpperCase().includes(query)
        )
            return false;

        const live = isLive(row);

        if (
            currentFilter === "live" &&
            !live
        )
            return false;

        if (
            currentFilter === "missing" &&
            live
        )
            return false;

        return true;
    });
}

function makeRow(row) {

    const tr =
        document.createElement("tr");

    tr.dataset.symbol = row.symbol;

    tr.innerHTML = `
        <td class="symbol-cell">
            ${esc(row.symbol)}
        </td>

        <td class="expiry-cell">
            ${esc(row.expiry)}
        </td>

        <td class="ltp-cell">
            ${num(row.ltp)}
        </td>

        <td class="bid-cell">
            ${num(row.bid)}
        </td>

        <td class="ask-cell">
            ${num(row.ask)}
        </td>
    `;

    rowsBySymbol.set(
        row.symbol,
        {
            row,
            tr,
            ltpKey: valueKey(row.ltp),
            bidKey: valueKey(row.bid),
            askKey: valueKey(row.ask)
        }
    );

    return tr;
}

function updateExistingRow(entry, row) {

    const cells =
        entry.tr.children;

    const newLtpKey =
        valueKey(row.ltp);

    const newBidKey =
        valueKey(row.bid);

    const newAskKey =
        valueKey(row.ask);

    if (entry.ltpKey !== newLtpKey) {

        cells[2].textContent =
            num(row.ltp);

        cells[2].classList.remove("changed");

        void cells[2].offsetWidth;

        cells[2].classList.add("changed");

        entry.ltpKey = newLtpKey;
    }

    if (entry.bidKey !== newBidKey) {

        cells[3].textContent =
            num(row.bid);

        cells[3].classList.remove("changed");

        void cells[3].offsetWidth;

        cells[3].classList.add("changed");

        entry.bidKey = newBidKey;
    }

    if (entry.askKey !== newAskKey) {

        cells[4].textContent =
            num(row.ask);

        cells[4].classList.remove("changed");

        void cells[4].offsetWidth;

        cells[4].classList.add("changed");

        entry.askKey = newAskKey;
    }
}

function rebuildTable(rows) {

    const body =
        document.getElementById("futuresBody");

    const filtered =
        sortRows(
            filteredRows(rows)
        );

    const fragment =
        document.createDocumentFragment();

    rowsBySymbol.clear();

    for (const row of filtered)
        fragment.appendChild(
            makeRow(row)
        );

    body.replaceChildren(fragment);

    updateSummary(
        rows,
        filtered
    );
}

function updateTable(rows) {

    const filtered =
        sortRows(
            filteredRows(rows)
        );

    const body =
        document.getElementById("futuresBody");

    if (
        body.children.length !==
        filtered.length
    ) {

        rebuildTable(rows);
        return;
    }

    for (
        let i = 0;
        i < filtered.length;
        ++i
    ) {

        if (
            body.children[i]?.dataset.symbol !==
            filtered[i].symbol
        ) {

            rebuildTable(rows);
            return;
        }
    }

    for (const row of filtered) {

        const entry =
            rowsBySymbol.get(row.symbol);

        if (entry)
            updateExistingRow(
                entry,
                row
            );
    }

    updateSummary(
        rows,
        filtered
    );
}

function updateSummary(
    allRows,
    visibleRows
) {

    const live =
        allRows.filter(isLive).length;

    const missing =
        allRows.length - live;

    document.getElementById("rowCount")
        .textContent =
            visibleRows.length;

    document.getElementById("liveCount")
        .textContent =
            live;

    document.getElementById("missingCount")
        .textContent =
            missing;

    document.getElementById("updatedAt")
        .textContent =
            latestData?.timestamp
                ? new Date(
                    latestData.timestamp
                  ).toLocaleTimeString(
                    "en-IN",
                    {
                        hour12: false,
                        fractionalSecondDigits: 3
                    }
                  )
                : "—";
}



function combinedNum(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
}

function combinedFormat(value, digits = 2) {
    const n = combinedNum(value);

    if (n === null)
        return "—";

    return n.toLocaleString(
        "en-IN",
        {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits
        }
    );
}

function combinedBasisPercent(futureLtp, indicative) {
    const f = combinedNum(futureLtp);
    const i = combinedNum(indicative);

    if (
        f === null ||
        i === null ||
        i === 0
    ) {
        return null;
    }

    return ((f - i) / i) * 100.0;
}

function combinedBasisText(value) {
    const n = combinedNum(value);

    if (n === null)
        return "—";

    const sign = n > 0 ? "+" : "";

    return sign + n.toFixed(2) + "%";
}

const combinedFavoritesKey =
    "fo_dashboard_favorites_v1";

let combinedFavorites =
    new Set(
        JSON.parse(
            localStorage.getItem(
                combinedFavoritesKey
            ) || "[]"
        )
    );

let combinedSort =
    "symbol-asc";

let combinedFavoritesOnly =
    false;


function saveCombinedFavorites() {
    localStorage.setItem(
        combinedFavoritesKey,
        JSON.stringify(
            Array.from(combinedFavorites)
        )
    );
}


function toggleCombinedFavorite(symbol) {
    const key =
        String(symbol || "")
            .trim()
            .toUpperCase();

    if (!key)
        return;

    if (combinedFavorites.has(key)) {
        combinedFavorites.delete(key);
    } else {
        combinedFavorites.add(key);
    }

    saveCombinedFavorites();

    renderCombinedMarket(
        latestData || {}
    );
}


function renderCombinedMarket(data) {

    const body =
        document.getElementById(
            "combinedMarketBody"
        );

    if (!body)
        return;


    const cmRows =
        Array.isArray(data?.cm_equities)
            ? data.cm_equities
            : [];


    const futureGroups =
        Array.isArray(data?.futures)
            ? data.futures
            : [];


    const indices =
        Array.isArray(data?.indices)
            ? data.indices
            : [];


    const cmBySymbol =
        new Map();

    for (const cm of cmRows) {

        const symbol =
            String(
                cm?.symbol || ""
            )
            .trim()
            .toUpperCase();

        if (symbol)
            cmBySymbol.set(
                symbol,
                cm
            );
    }


    const futureBySymbol =
        new Map();

    for (const group of futureGroups) {

        const symbol =
            String(
                group?.symbol || ""
            )
            .trim()
            .toUpperCase();

        if (!symbol)
            continue;

        const rows =
            Array.isArray(group?.rows)
                ? group.rows
                : [];

        if (rows.length) {
            futureBySymbol.set(
                symbol,
                rows[0]
            );
        }
    }


    const indexBySymbol =
        new Map();

    for (const index of indices) {

        const symbol =
            String(
                index?.name ||
                index?.symbol ||
                ""
            )
            .trim()
            .toUpperCase();

        if (symbol)
            indexBySymbol.set(
                symbol,
                index
            );
    }


    // ========================================================
    // F&O IS THE MASTER UNIVERSE.
    // This guarantees the full nearest-future 216-row set.
    // ========================================================

    const rows = [];


    for (const group of futureGroups) {

        const symbol =
            String(
                group?.symbol || ""
            )
            .trim()
            .toUpperCase();

        if (!symbol)
            continue;


        const future =
            Array.isArray(group?.rows) &&
            group.rows.length
                ? group.rows[0]
                : null;


        const index =
            indexBySymbol.get(symbol) ||
            null;


        const cm =
            cmBySymbol.get(symbol) ||
            null;


        let type =
            "EQ";


        let cashLtp =
            null;

        let cashBid =
            null;

        let cashAsk =
            null;

        let indicative =
            null;


        if (index) {

            type = "INDEX";

            cashLtp =
                combinedNum(
                    index?.live_value ??
                    index?.live ??
                    null
                );

            // NSE CM index feed does not provide
            // equity-style MBP bid/ask.
            cashBid = null;
            cashAsk = null;

            indicative =
                combinedNum(
                    index?.indicative_value ??
                    index?.indicative ??
                    null
                );

        } else if (cm) {

            cashLtp =
                combinedNum(
                    cm?.ltp
                );

            cashBid =
                combinedNum(
                    cm?.bid
                );

            cashAsk =
                combinedNum(
                    cm?.ask
                );

            indicative =
                cm?.has_indicative_close
                    ? combinedNum(
                        cm?.indicative_close
                    )
                    : null;
        }


        const futureLtp =
            future
                ? combinedNum(
                    future?.ltp
                )
                : null;

        const futureBid =
            future
                ? combinedNum(
                    future?.bid
                )
                : null;

        const futureAsk =
            future
                ? combinedNum(
                    future?.ask
                )
                : null;

        const basis =
            combinedBasisPercent(
                futureLtp,
                indicative
            );


        rows.push({
            symbol,
            type,
            cashLtp,
            cashBid,
            cashAsk,
            indicative,
            expiry:
                future?.expiry ||
                null,
            futureLtp,
            futureBid,
            futureAsk,
            basis,
            favorite:
                combinedFavorites.has(symbol)
        });
    }


    /*
     * ========================================================
     * SUMMARY
     * ========================================================
     */

    const summarySymbols =
        document.getElementById(
            "summarySymbols"
        );

    const summaryLive =
        document.getElementById(
            "summaryLive"
        );

    const summaryCash =
        document.getElementById(
            "summaryCash"
        );

    const summaryFavorites =
        document.getElementById(
            "summaryFavorites"
        );


    const liveSymbols =
        rows.filter(
            row =>
                row.futureLtp !== null &&
                row.futureLtp > 0
        ).length;


    const cashSymbols =
        rows.filter(
            row =>
                row.cashLtp !== null &&
                row.cashLtp > 0
        ).length;


    const favoriteCount =
        rows.filter(
            row =>
                row.favorite
        ).length;


    if (summarySymbols)
        summarySymbols.textContent =
            rows.length.toLocaleString("en-IN");


    if (summaryLive)
        summaryLive.textContent =
            liveSymbols.toLocaleString("en-IN");


    if (summaryCash)
        summaryCash.textContent =
            cashSymbols.toLocaleString("en-IN");


    if (summaryFavorites)
        summaryFavorites.textContent =
            favoriteCount.toLocaleString("en-IN");


    // ========================================================
    // SEARCH
    // ========================================================

    const searchInput =
        document.getElementById(
            "search"
        );

    const searchTerm =
        String(
            searchInput?.value || ""
        )
        .trim()
        .toUpperCase();


    let filtered =
        rows.filter(
            row =>
                !searchTerm ||
                row.symbol.includes(
                    searchTerm
                )
        );


    if (combinedFavoritesOnly) {

        filtered =
            filtered.filter(
                row =>
                    row.favorite
            );
    }


    // ========================================================
    // ========================================================
    // SORT COMPLETE TABLE
    // ========================================================

    function sortNumberValue(value) {

        if (
            value === null ||
            value === undefined ||
            value === ""
        ) {
            return null;
        }

        const n = Number(value);

        return Number.isFinite(n)
            ? n
            : null;
    }

    function sortTextValue(value) {

        if (
            value === null ||
            value === undefined
        ) {
            return "";
        }

        return String(value).trim();
    }

    function rowSortValue(row, sortKey) {

        switch (sortKey) {

            case "cash-ltp":
                return sortNumberValue(
                    row.cashLtp ??
                    row.cash_ltp ??
                    row.cash?.ltp
                );

            case "cash-bid":
                return sortNumberValue(
                    row.cashBid ??
                    row.cash_bid ??
                    row.cash?.bid
                );

            case "cash-ask":
                return sortNumberValue(
                    row.cashAsk ??
                    row.cash_ask ??
                    row.cash?.ask
                );

            case "cash-indicative":
                return sortNumberValue(
                    row.indicative ??
                    row.indicativeClose ??
                    row.indicative_close ??
                    row.cashIndicative ??
                    row.cash?.indicative ??
                    row.cash?.indicativeClose
                );

            case "future-ltp":
                return sortNumberValue(
                    row.futureLtp ??
                    row.future_ltp ??
                    row.future?.ltp
                );

            case "future-bid":
                return sortNumberValue(
                    row.futureBid ??
                    row.future_bid ??
                    row.future?.bid
                );

            case "future-ask":
                return sortNumberValue(
                    row.futureAsk ??
                    row.future_ask ??
                    row.future?.ask
                );

            case "basis":
                return sortNumberValue(
                    row.basis
                );

            case "future-expiry":
                return sortTextValue(
                    row.futureExpiry ??
                    row.future_expiry ??
                    row.future?.expiry
                );

            case "symbol":
                return sortTextValue(
                    row.symbol
                );

            default:
                return null;
        }
    }

    const selectedSort =
        String(
            combinedSort || "symbol-asc"
        );

    let sortMatch =
        selectedSort.match(
            /^(symbol|cash-ltp|cash-bid|cash-ask|cash-indicative|future-expiry|future-ltp|future-bid|future-ask|basis)-(asc|desc)$/
        );

    /*
     * favorites-first is a genuine sort mode.
     * It is NOT applied to every other sort.
     */
    if (selectedSort === "favorites-first") {

        filtered.sort(
            (a, b) => {

                const af =
                    Boolean(a.favorite);

                const bf =
                    Boolean(b.favorite);

                if (af !== bf) {
                    return af ? -1 : 1;
                }

                return String(
                    a.symbol || ""
                ).localeCompare(
                    String(
                        b.symbol || ""
                    )
                );
            }
        );

    } else if (sortMatch) {

        const sortKey =
            sortMatch[1];

        const direction =
            sortMatch[2] === "asc"
                ? 1
                : -1;

        filtered.sort(
            (a, b) => {

                /*
                 * SYMBOL = lexical sort.
                 */
                if (sortKey === "symbol") {

                    const av =
                        rowSortValue(
                            a,
                            "symbol"
                        );

                    const bv =
                        rowSortValue(
                            b,
                            "symbol"
                        );

                    const cmp =
                        av.localeCompare(
                            bv
                        );

                    return (
                        cmp === 0
                            ? 0
                            : cmp * direction
                    );
                }

                /*
                 * EXPIRY = YYYY/date-like text.
                 * Current display format such as 29-SEP-26
                 * sorts correctly when converted through Date
                 * where possible, with lexical fallback.
                 */
                if (
                    sortKey ===
                    "future-expiry"
                ) {

                    const av =
                        rowSortValue(
                            a,
                            sortKey
                        );

                    const bv =
                        rowSortValue(
                            b,
                            sortKey
                        );

                    if (!av && !bv) {
                        return String(
                            a.symbol || ""
                        ).localeCompare(
                            String(
                                b.symbol || ""
                            )
                        );
                    }

                    if (!av) return 1;
                    if (!bv) return -1;

                    const parseExpiry =
                        value => {

                            const m =
                                value.match(
                                    /^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$/
                                );

                            if (!m) {
                                return null;
                            }

                            const months = {
                                JAN: 0,
                                FEB: 1,
                                MAR: 2,
                                APR: 3,
                                MAY: 4,
                                JUN: 5,
                                JUL: 6,
                                AUG: 7,
                                SEP: 8,
                                OCT: 9,
                                NOV: 10,
                                DEC: 11
                            };

                            const month =
                                months[
                                    m[2]
                                        .toUpperCase()
                                ];

                            if (
                                month ===
                                undefined
                            ) {
                                return null;
                            }

                            let year =
                                Number(m[3]);

                            if (year < 100) {
                                year += 2000;
                            }

                            return Date.UTC(
                                year,
                                month,
                                Number(m[1])
                            );
                        };

                    const ad =
                        parseExpiry(av);

                    const bd =
                        parseExpiry(bv);

                    if (
                        ad !== null &&
                        bd !== null &&
                        ad !== bd
                    ) {
                        return (
                            ad - bd
                        ) * direction;
                    }

                    const cmp =
                        av.localeCompare(
                            bv
                        );

                    if (cmp !== 0) {
                        return (
                            cmp * direction
                        );
                    }

                    return String(
                        a.symbol || ""
                    ).localeCompare(
                        String(
                            b.symbol || ""
                        )
                    );
                }

                /*
                 * ALL OTHER COLUMNS = REAL NUMERIC
                 * WHOLE-TABLE SORT.
                 */
                const av =
                    rowSortValue(
                        a,
                        sortKey
                    );

                const bv =
                    rowSortValue(
                        b,
                        sortKey
                    );

                /*
                 * Keep unavailable values at the bottom
                 * for BOTH ascending and descending sorts.
                 */
                if (
                    av === null &&
                    bv === null
                ) {
                    return String(
                        a.symbol || ""
                    ).localeCompare(
                        String(
                            b.symbol || ""
                        )
                    );
                }

                if (av === null) {
                    return 1;
                }

                if (bv === null) {
                    return -1;
                }

                if (av !== bv) {
                    return (
                        av - bv
                    ) * direction;
                }

                /*
                 * Stable deterministic tie-breaker.
                 */
                return String(
                    a.symbol || ""
                ).localeCompare(
                    String(
                        b.symbol || ""
                    )
                );
            }
        );

    } else {

        /*
         * Unknown sort state:
         * always fall back to complete Symbol ASC.
         */
        filtered.sort(
            (a, b) =>
                String(
                    a.symbol || ""
                ).localeCompare(
                    String(
                        b.symbol || ""
                    )
                )
        );
    }

    body.innerHTML = "";


    if (!filtered.length) {

        body.innerHTML = `
            <tr>
                <td
                    colspan="12"
                    style="text-align:center;padding:18px;"
                >
                    No matching symbols
                </td>
            </tr>
        `;

        return;
    }


    for (const row of filtered) {

        const tr =
            document.createElement(
                "tr"
            );


        if (row.favorite) {
            tr.classList.add(
                "combined-fav-row"
            );
        }


        const basisClass =
            row.basis === null
                ? ""
                : (
                    row.basis >= 0
                        ? "basis-positive"
                        : "basis-negative"
                );


        tr.innerHTML = `
            <td>
                <button
                    type="button"
                    class="favorite-star ${
                        row.favorite
                            ? "is-favorite"
                            : ""
                    }"
                    title="${
                        row.favorite
                            ? "Remove favorite"
                            : "Add to favorites"
                    }"
                    aria-label="${
                        row.favorite
                            ? "Remove favorite"
                            : "Add to favorites"
                    }"
                    data-symbol="${row.symbol}"
                >
                    ${row.favorite ? "★" : "☆"}
                </button>
            </td>

            <td>
                <span class="${
                    row.type === "INDEX"
                        ? "type-index"
                        : "type-eq"
                }">
                    ${row.type}
                </span>
            </td>

            <td>
                <div class="market-symbol">

                    ${
                        row.type === "INDEX"
                            ? '<span class="symbol-badge">INDEX</span>'
                            : ""
                    }

                    <span class="symbol-name">
                        ${row.symbol}
                    </span>

                </div>
            </td>

            <td class="cash-ltp">
                ${
                    row.cashLtp === null
                        ? '<span class="market-muted">—</span>'
                        : combinedFormat(row.cashLtp)
                }
            </td>

            <td class="bid-cell">
                ${
                    row.type === "INDEX"
                        ? '<span class="market-muted">—</span>'
                        : row.cashBid === null
                            ? '<span class="market-muted">—</span>'
                            : combinedFormat(row.cashBid)
                }
            </td>

            <td class="ask-cell">
                ${
                    row.type === "INDEX"
                        ? '<span class="market-muted">—</span>'
                        : row.cashAsk === null
                            ? '<span class="market-muted">—</span>'
                            : combinedFormat(row.cashAsk)
                }
            </td>

            <td>
                ${
                    row.indicative === null
                        ? '<span class="market-muted">—</span>'
                        : combinedFormat(row.indicative)
                }
            </td>

            <td>
                ${
                    row.expiry
                        ? row.expiry
                        : '<span class="market-muted">—</span>'
                }
            </td>

            <td class="future-ltp">
                ${
                    row.futureLtp === null
                        ? '<span class="market-muted">—</span>'
                        : combinedFormat(row.futureLtp)
                }
            </td>

            <td class="bid-cell">
                ${
                    row.futureBid === null
                        ? '<span class="market-muted">—</span>'
                        : combinedFormat(row.futureBid)
                }
            </td>

            <td class="ask-cell">
                ${
                    row.futureAsk === null
                        ? '<span class="market-muted">—</span>'
                        : combinedFormat(row.futureAsk)
                }
            </td>

            <td class="basis-cell ${
                row.basis === null
                    ? "basis-neutral"
                    : row.basis > 0
                        ? "basis-positive"
                        : row.basis < 0
                            ? "basis-negative"
                            : "basis-neutral"
            }">
                ${
                    row.basis === null
                        ? '<span class="market-muted">—</span>'
                        : combinedBasisText(row.basis)
                }
            </td>
        `;



        const starButton =
            tr.querySelector(
                ".favorite-star"
            );


        if (starButton) {

            starButton.addEventListener(
                "click",
                () => {
                    toggleCombinedFavorite(
                        row.symbol
                    );
                }
            );
        }


        body.appendChild(tr);
    }
}




/*
 * ============================================================
 * UNIFIED DASHBOARD EVENT HANDLERS
 * ============================================================
 *
 * The old F&O-only controls/renderers are no longer part of
 * the visible dashboard execution path.
 */




async function refresh() {

    if (refreshBusy)
        return;

    refreshBusy = true;

    try {

        const response =
            await fetch(
                "/api/data",
                {
                    cache: "no-store"
                }
            );

        if (!response.ok) {

            throw new Error(
                "HTTP " + response.status
            );
        }


        const data =
            await response.json();


        latestData = data;


        /*
         * Unified visible table.
         */
        renderCombinedMarket(
            data
        );


        /*
         * Live badge.
         */
        const badge =
            document.getElementById(
                "liveBadge"
            );

        if (badge) {

            badge.classList.remove(
                "offline"
            );
        }


        const liveText =
            document.getElementById(
                "liveText"
            );

        if (liveText) {

            liveText.textContent =
                "LIVE";
        }


        /*
         * Dashboard status.
         */
        const futures =
            Array.isArray(
                data?.futures
            )
                ? data.futures
                : [];


        const diag =
            data?.diag || {};


        let liveCount = 0;


        for (
            const group
            of futures
        ) {

            const rows =
                Array.isArray(
                    group?.rows
                )
                    ? group.rows
                    : [];


            const row =
                rows.length
                    ? rows[0]
                    : null;


            const ltp =
                Number(
                    row?.ltp
                );


            if (
                Number.isFinite(ltp) &&
                ltp > 0
            ) {
                ++liveCount;
            }
        }


        const status =
            document.getElementById(
                "status"
            );


        if (status) {

            status.textContent =
                `${futures.length} symbols • ` +
                `${liveCount} live • ` +
                `UDP ${
                    Number(
                        diag.udp_packets || 0
                    ).toLocaleString(
                        "en-IN"
                    )
                } • ` +
                `${
                    Number(
                        diag.future_updates || 0
                    ).toLocaleString(
                        "en-IN"
                    )
                } updates`;
        }


        const updatedAt =
            document.getElementById(
                "updatedAt"
            );


        if (updatedAt) {

            updatedAt.textContent =
                data?.timestamp || "";
        }


    }
    catch (err) {

        console.error(
            "Dashboard refresh failed:",
            err
        );


        const badge =
            document.getElementById(
                "liveBadge"
            );


        if (badge) {

            badge.classList.add(
                "offline"
            );
        }


        const liveText =
            document.getElementById(
                "liveText"
            );


        if (liveText) {

            liveText.textContent =
                "OFFLINE";
        }


        const status =
            document.getElementById(
                "status"
            );


        if (status) {

            status.textContent =
                "Disconnected — retrying...";
        }

    }
    finally {

        refreshBusy = false;
    }
}




/*
 * ============================================================
 * LIGHT / DARK THEME TOGGLE
 * ============================================================
 */

const themeToggle =
    document.getElementById(
        "themeToggle"
    );


function applyDashboardTheme(theme) {

    const isLight =
        theme === "light";


    document.documentElement.classList.toggle(
        "theme-light",
        isLight
    );


    if (themeToggle) {

        themeToggle.textContent =
            isLight
                ? "☾ Dark"
                : "☀ Light";

        themeToggle.title =
            isLight
                ? "Switch to dark mode"
                : "Switch to light mode";
    }


    localStorage.setItem(
        "fo_dashboard_theme",
        isLight
            ? "light"
            : "dark"
    );
}


const storedDashboardTheme =
    localStorage.getItem(
        "fo_dashboard_theme"
    );


applyDashboardTheme(
    storedDashboardTheme === "light"
        ? "light"
        : "dark"
);


if (themeToggle) {

    themeToggle.addEventListener(
        "click",
        () => {

            const isCurrentlyLight =
                document.documentElement
                    .classList
                    .contains(
                        "theme-light"
                    );


            applyDashboardTheme(
                isCurrentlyLight
                    ? "dark"
                    : "light"
            );

        }
    );
}


const searchElement =
    document.getElementById("search");

if (searchElement) {

    searchElement.addEventListener(
        "input",
        () => {

            renderCombinedMarket(
                latestData || {}
            );

        }
    );
}


const combinedSortElement =
    document.getElementById(
        "combinedSortSelect"
    );

if (combinedSortElement) {

    const savedSort =
        localStorage.getItem(
            "fo_dashboard_sort"
        );

    if (
        savedSort &&
        combinedSortElement.querySelector(
            `option[value="${savedSort}"]`
        )
    ) {
        combinedSort =
            savedSort;

        combinedSortElement.value =
            savedSort;
    } else {
        combinedSort =
            combinedSortElement.value ||
            "symbol-asc";
    }

    combinedSortElement.addEventListener(
        "change",
        event => {

            combinedSort =
                String(
                    event.target.value ||
                    "symbol-asc"
                );

            localStorage.setItem(
                "fo_dashboard_sort",
                combinedSort
            );

            renderCombinedMarket(
                latestData || {}
            );
        }
    );
}

const favoritesElement =
    document.getElementById(
        "favoritesBtn"
    );

if (favoritesElement) {

    favoritesElement.addEventListener(
        "click",
        () => {

            combinedFavoritesOnly =
                !combinedFavoritesOnly;

            favoritesElement.textContent =
                combinedFavoritesOnly
                    ? "★ Favorites: ON"
                    : "★ Favorites";

            renderCombinedMarket(
                latestData || {}
            );

        }
    );
}


/*
 * Initial load.
 */

refresh();


/*
 * Live refresh.
 */

setInterval(
    refresh,
    500
);


// ============================================================
// HEADER CLICK -> REAL COMPLETE-TABLE SORT
// ============================================================

document.addEventListener(
    "click",
    event => {

        const th =
            event.target.closest(
                ".combined-market-shell .market-table thead th"
            );

        if (!th) return;

        const row =
            th.parentElement;

        if (!row) return;

        const thead =
            row.parentElement;

        if (!thead) return;

        const rows =
            Array.from(
                thead.querySelectorAll("tr")
            );

        const rowIndex =
            rows.indexOf(row);

        let sortKey = null;

        /*
         * First header row:
         *
         * ★ | TYPE | SYMBOL | CASH MARKET | FUTURE
         *
         * Only SYMBOL is sortable here.
         */
        if (rowIndex === 0) {

            const text =
                th.textContent
                    .trim()
                    .toUpperCase();

            if (text === "SYMBOL") {
                sortKey = "symbol";
            }
        }

        /*
         * Second header row:
         *
         * LTP BID ASK IND CLOSE
         * EXPIRY LTP BID ASK FUTURE VS IND %
         */
        if (rowIndex === 1) {

            const headers =
                Array.from(
                    row.querySelectorAll("th")
                );

            const index =
                headers.indexOf(th);

            const keys = [
                "cash-ltp",
                "cash-bid",
                "cash-ask",
                "cash-indicative",
                "future-expiry",
                "future-ltp",
                "future-bid",
                "future-ask",
                "basis"
            ];

            if (
                index >= 0 &&
                index < keys.length
            ) {
                sortKey =
                    keys[index];
            }
        }

        if (!sortKey) return;

        let nextDirection =
            "asc";

        if (
            combinedSort ===
            `${sortKey}-asc`
        ) {
            nextDirection =
                "desc";
        }

        combinedSort =
            `${sortKey}-${nextDirection}`;

        localStorage.setItem(
            "fo_dashboard_sort",
            combinedSort
        );

        if (combinedSortElement) {
            combinedSortElement.value =
                combinedSort;
        }

        /*
         * THIS is the important part:
         * renderCombinedMarket() sorts the COMPLETE
         * 216-row filtered array using combinedSort.
         */
        renderCombinedMarket(
            latestData || {}
        );
    }
);

</script>

</body>
</html>
)HTML";

    // ============================================================
    // HTTP
    // ============================================================

    static void send_response(
        int client,
        int code,
        const char* content_type,
        const std::string& body)
    {
        std::ostringstream out;

        out
            << "HTTP/1.1 "
            << code
            << " "
            << (
                code == 200
                    ? "OK"
                    : "Not Found"
              )
            << "\r\n"
            << "Content-Type: "
            << content_type
            << "\r\n"
            << "Cache-Control: no-store\r\n"
            << "Access-Control-Allow-Origin: *\r\n"
            << "Content-Length: "
            << body.size()
            << "\r\n"
            << "Connection: close\r\n"
            << "\r\n"
            << body;

        const std::string response =
            out.str();

        send(
            client,
            response.data(),
            response.size(),
            0
        );
    }

    static void http_loop()
    {
        const int server =
            socket(
                AF_INET,
                SOCK_STREAM,
                0
            );

        if (server < 0)
        {
            std::cerr
                << "[HTTP] socket failed: "
                << std::strerror(errno)
                << "\n";

            return;
        }

        int reuse = 1;

        setsockopt(
            server,
            SOL_SOCKET,
            SO_REUSEADDR,
            &reuse,
            sizeof(reuse)
        );

        sockaddr_in addr{};

        addr.sin_family =
            AF_INET;

        addr.sin_port =
            htons(
                HTTP_PORT
            );

        addr.sin_addr.s_addr =
            htonl(
                INADDR_ANY
            );

        if (
            bind(
                server,
                reinterpret_cast<sockaddr*>(&addr),
                sizeof(addr)
            ) < 0
        )
        {
            std::cerr
                << "[HTTP] bind failed: "
                << std::strerror(errno)
                << "\n";

            close(server);

            return;
        }

        if (
            listen(
                server,
                32
            ) < 0
        )
        {
            std::cerr
                << "[HTTP] listen failed: "
                << std::strerror(errno)
                << "\n";

            close(server);

            return;
        }

        std::cout
            << "[HTTP] listening on 0.0.0.0:"
            << HTTP_PORT
            << "\n";

        while (g_running)
        {
            sockaddr_in client_addr{};

            socklen_t len =
                sizeof(client_addr);

            int client =
                accept(
                    server,
                    reinterpret_cast<sockaddr*>(
                        &client_addr
                    ),
                    &len
                );

            if (client < 0)
                continue;

            char request[4096]{};

            const ssize_t n =
                recv(
                    client,
                    request,
                    sizeof(request) - 1,
                    0
                );

            if (n <= 0)
            {
                close(client);
                continue;
            }

            request[n] = '\0';

            const std::string req(
                request,
                static_cast<size_t>(n)
            );

            if (
                req.rfind(
                    "GET /api/data ",
                    0
                ) == 0
            )
            {
                send_response(
                    client,
                    200,
                    "application/json",
                    make_json()
                );
            }
            else if (
                req.rfind(
                    "GET /health ",
                    0
                ) == 0
            )
            {
                send_response(
                    client,
                    200,
                    "application/json",
                    "{\"ok\":true}"
                );
            }
            else if (
                req.rfind(
                    "GET / ",
                    0
                ) == 0
            )
            {
                send_response(
                    client,
                    200,
                    "text/html; charset=utf-8",
                    HTML
                );
            }
            else
            {
                send_response(
                    client,
                    404,
                    "application/json",
                    "{\"error\":\"not found\"}"
                );
            }

            close(client);
        }

        close(server);
    }

} // namespace

// ============================================================
// MAIN
// ============================================================

int main()
{

    load_cm_symbol_map();

    std::signal(
        SIGINT,
        signal_handler
    );

    std::signal(
        SIGTERM,
        signal_handler
    );

    std::cout
        << "============================================================\n"
        << " STANDALONE F&O MARKET DASHBOARD\n"
        << "============================================================\n"
        << "Multicast : "
        << MULTICAST_IP
        << ":"
        << MULTICAST_PORT
        << "\n"
        << "HTTP      : "
        << HTTP_BIND
        << ":"
        << HTTP_PORT
        << "\n"
        << "F&O       : 7208 / 17208\n"
        << "Index     : 7207 / 8207\n"
        << "CM        : 233.1.2.5:34074 / 7208\n"
        << "Mode      : standalone\n"
        << "============================================================\n";

    if (!load_future_universe())
    {
        std::cerr
            << "[FNO] ERROR: future universe is empty\n";

        return 1;
    }

    std::thread http_thread(
        http_loop
    );

    std::thread multicast_thread(
        multicast_loop
    );

    std::thread cm_multicast_thread(
        cm_multicast_loop
    );

    multicast_thread.join();

    if (cm_multicast_thread.joinable())
        cm_multicast_thread.join();

    g_running = 0;

    if (http_thread.joinable())
        http_thread.join();

    std::cout
        << "[FNO] stopped\n";

    return 0;
}
