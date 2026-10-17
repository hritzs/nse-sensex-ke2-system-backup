#include "analytics_engine.h"
#include "symbol_parser.h"
#include "expiry_engine.h"
#include "iv.h"
#include "greeks.h"
#include "contract_loader.h"

#include <iostream>
#include <algorithm>
#include <vector>
#include <unordered_map>
#include <string>
#include <sstream>
#include <iomanip>
#include <cmath>
#include <chrono>
#include <mutex>
#include <ctime>
#include <fstream>
#include <filesystem>
#include <hiredis/hiredis.h>

// ---------------------------------
// GLOBALS FROM MAIN.CPP
// ---------------------------------
extern redisContext* g_redis;
extern std::mutex g_redisMtx;
extern void SystemLog(const std::string& msg, bool is_error = false);

namespace Analytics
{
    /* =========================================================
       STATE
    ========================================================= */

    static std::unordered_map<std::string, double> FUT;
    static std::unordered_map<std::string, double> FUT_EMA;
    static std::mutex STATE_MTX;

    constexpr double R = 0.0; // ❗ MUST BE 0.0 WHEN PRICING ON FUTURES (Black-76 Model)

    constexpr double FUT_ALPHA = 0.12;
    constexpr double IV_ALPHA  = 0.10;

    // PUT-CALL PARITY STATE CACHE
    static std::unordered_map<std::string, double> OPT_LTP;
    static std::unordered_map<std::string, double> OPT_BID;
    static std::unordered_map<std::string, double> OPT_ASK;
    static std::unordered_map<std::string, std::vector<double>> EXP_STRIKES;
    static std::unordered_map<std::string, double> SYN_FUT;
    static std::unordered_map<std::string, double> SYN_FUT_BID;
    static std::unordered_map<std::string, double> SYN_FUT_ASK;

    // VOL SURFACE: key = symbol|expiry|strike
    static std::unordered_map<std::string, double> IV_SURFACE;

    // HISTORY TRACKING FOR INTRADAY CHANGES & OI
    struct IvSnapshot {
        double ts;
        double iv;
    };
    struct OptionStats {
        std::vector<IvSnapshot> snaps;
        double max_iv = -1e9;
        double min_iv = 1e9;
        uint64_t oi = 0;
        uint64_t volume = 0;
        double lpp_high = 0.0;
        double lpp_low = 0.0;
    };
    static std::unordered_map<std::string, OptionStats> OPT_STATS;
    static std::unordered_map<std::string, double> EXP_EPOCH_CACHE;

    // USER OVERRIDES
    static std::unordered_map<std::string, double> STRIKE_OVERRIDES;
    static std::unordered_map<std::string, double> GAP_OVERRIDES;
    struct FutOverride {
        std::string base_expiry;
        double offset;
    };
    static std::unordered_map<std::string, FutOverride> FUTURE_OVERRIDES;
    static std::unordered_map<std::string, bool> USE_ACTUAL_FUTURE;

    static double EOD_GLOBAL_EPOCH = 0;

    void set_eod_timestamp(double epoch) {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        EOD_GLOBAL_EPOCH = epoch;
    }

    // Internal save without mutex (lock held by caller)
    void _save_overrides() {
        std::filesystem::create_directory("user_configs");
        std::ofstream f("user_configs/backend_overrides.txt");
        for (const auto& pair : STRIKE_OVERRIDES) f << "STRIKE|" << pair.first << "|" << pair.second << "\n";
        for (const auto& pair : GAP_OVERRIDES) f << "GAP|" << pair.first << "|" << pair.second << "\n";
        for (const auto& pair : FUTURE_OVERRIDES) f << "FUTURE|" << pair.first << "|" << pair.second.base_expiry << "|" << pair.second.offset << "\n";
        for (const auto& pair : USE_ACTUAL_FUTURE) f << "ACTUAL_FUT|" << pair.first << "|" << pair.second << "\n";
    }

    void load_overrides() {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        std::ifstream f("user_configs/backend_overrides.txt");
        std::string line;
        while (std::getline(f, line)) {
            std::stringstream ss(line);
            std::string type, k, v1, v2;
            std::getline(ss, type, '|'); std::getline(ss, k, '|');
            if (type == "STRIKE") { std::getline(ss, v1); STRIKE_OVERRIDES[k] = std::stod(v1); }
            else if (type == "GAP") { std::getline(ss, v1); GAP_OVERRIDES[k] = std::stod(v1); }
            else if (type == "ACTUAL_FUT") { std::getline(ss, v1); USE_ACTUAL_FUTURE[k] = std::stoi(v1); }
            else if (type == "FUTURE") {
                std::getline(ss, v1, '|'); std::getline(ss, v2);
                FUTURE_OVERRIDES[k] = {v1 == "null" ? "" : v1, std::stod(v2)};
            }
        }
    }

    void set_strike_override(const std::string& expiry, double strike) {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        if (strike <= 0) STRIKE_OVERRIDES.erase(expiry);
        else STRIKE_OVERRIDES[expiry] = strike;
        _save_overrides();
    }
    void set_gap_override(const std::string& expiry, double gap) {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        if (gap <= 0) GAP_OVERRIDES.erase(expiry);
        else GAP_OVERRIDES[expiry] = gap;
        _save_overrides();
    }
    void set_future_override(const std::string& target, const std::string& base, double offset) {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        FUTURE_OVERRIDES[target] = {base, offset};
        _save_overrides();
    }
    void delete_future_override(const std::string& target) {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        FUTURE_OVERRIDES.erase(target);
        _save_overrides();
    }
    void set_future_type(const std::string& symbol, bool use_actual) {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        USE_ACTUAL_FUTURE[symbol] = use_actual;
        _save_overrides();
    }

    /* =========================================================
       EOD BHAVCOPY CACHE & BS SOLVER
    ========================================================= */
    static std::string CACHED_EOD_JSON = "";
    static std::unordered_map<std::string, double> EOD_STORE;
    static std::unordered_map<std::string, double> EOD_IV_STORE;

    void update_eod_data(const std::string& key, double price) {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        EOD_STORE[key] = price;
    }

    void compute_eod_ivs() {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        
        static size_t last_processed_count = 0;
        static double last_processed_epoch = -1;

        // Prevent WebSocket broadcast freezes when a new client connects
        if (!EOD_IV_STORE.empty() && EOD_STORE.size() == last_processed_count && EOD_GLOBAL_EPOCH == last_processed_epoch) {
            SystemLog("[ANALYTICS] EOD IVs already computed. Serving from memory to maintain WS stream.");
            return;
        }
        last_processed_count = EOD_STORE.size();
        last_processed_epoch = EOD_GLOBAL_EPOCH;
        
        double base_epoch = EOD_GLOBAL_EPOCH > 0 ? EOD_GLOBAL_EPOCH : std::time(nullptr);
        std::time_t base_t = static_cast<std::time_t>(base_epoch);
        std::tm tm_base; localtime_r(&base_t, &tm_base);

        // 1. CHECK REDIS CACHE
        char date_buf[64];
        std::strftime(date_buf, sizeof(date_buf), "%Y%m%d", &tm_base);
        std::string cache_key = "eod_ivs_" + std::string(date_buf);

        if (g_redis) {
            bool exists = false;
            {
                std::lock_guard<std::mutex> r_lock(g_redisMtx);
                redisReply* reply = (redisReply*)redisCommand(g_redis, "EXISTS %s", cache_key.c_str());
                exists = (reply && reply->type == REDIS_REPLY_INTEGER && reply->integer == 1);
                if (reply) freeReplyObject(reply);
            }

            if (exists) {
                SystemLog("[ANALYTICS] CACHE HIT! EOD IVs already computed for today. Loading instantly from Redis...");
                
                std::lock_guard<std::mutex> r_lock(g_redisMtx);
                redisReply* reply = (redisReply*)redisCommand(g_redis, "HGETALL %s", cache_key.c_str());
                if (reply && reply->type == REDIS_REPLY_ARRAY) {
                    EOD_IV_STORE.clear();
                    for (size_t i = 0; i < reply->elements; i += 2) {
                        if (reply->element[i] && reply->element[i+1]) {
                            std::string k = reply->element[i]->str;
                            double v = std::stod(reply->element[i+1]->str);
                            EOD_IV_STORE[k] = v;
                        }
                    }
                    freeReplyObject(reply);
                    CACHED_EOD_JSON = ""; // Invalidate JSON Cache
                    SystemLog("[ANALYTICS] Successfully loaded " + std::to_string(EOD_IV_STORE.size()) + " IVs from Redis!");
                    return;
                }
                if (reply) freeReplyObject(reply);
            }
        }

        SystemLog("[ANALYTICS] CACHE MISS: Computing 80,000+ EOD IVs via Newton-Raphson. This might take a moment...");
        tm_base.tm_hour = 0; tm_base.tm_min = 0; tm_base.tm_sec = 0;
        std::time_t eod_day_start = std::mktime(&tm_base);
        
        CACHED_EOD_JSON = ""; // Invalidate JSON Cache
        EOD_IV_STORE.clear();
        
        for (const auto& pair : EOD_STORE) {
            const std::string& key = pair.first;
            double px = pair.second;
            
            std::vector<std::string> parts;
            std::stringstream ss(key);
            std::string p;
            while (std::getline(ss, p, '|')) parts.push_back(p);
            
            if (parts.size() != 4) continue;
            
            std::string sym = parts[0];
            std::string exp = parts[1];
            double strike = 0;
            try { strike = std::stod(parts[2]); } catch(...) { continue; }
            std::string type = parts[3]; // CE or PE
            
            if (type != "CE" && type != "PE") continue;
            if (px <= 0) continue;
            
            double eod_S = 0;
            std::string ce_k = sym + "|" + exp + "|" + parts[2] + "|CE";
            std::string pe_k = sym + "|" + exp + "|" + parts[2] + "|PE";
            std::string fut_k = sym + "|" + exp + "|FUT";
            
            if (EOD_STORE.count(ce_k) && EOD_STORE.count(pe_k)) {
                double ce_px = EOD_STORE[ce_k];
                double pe_px = EOD_STORE[pe_k];
                if (ce_px > 0 && pe_px > 0) {
                    double f = strike + ce_px - pe_px;
                    if (f > 0) eod_S = f;
                }
            }
            if (eod_S <= 0 && EOD_STORE.count(fut_k)) {
                eod_S = EOD_STORE[fut_k];
            }
            if (eod_S <= 0) continue;
            
            int day = 0, year = 0, month = 0;
            if (exp.length() >= 9 && exp[2] == '-' && exp[6] == '-') {
                try {
                    day = std::stoi(exp.substr(0, 2));
                    std::string m_str = exp.substr(3, 3);
                    year = 2000 + std::stoi(exp.substr(7, 2));
                    if (m_str == "JAN") month = 0; else if (m_str == "FEB") month = 1;
                    else if (m_str == "MAR") month = 2; else if (m_str == "APR") month = 3;
                    else if (m_str == "MAY") month = 4; else if (m_str == "JUN") month = 5;
                    else if (m_str == "JUL") month = 6; else if (m_str == "AUG") month = 7;
                    else if (m_str == "SEP") month = 8; else if (m_str == "OCT") month = 9;
                    else if (m_str == "NOV") month = 10; else if (m_str == "DEC") month = 11;
                } catch(...) {}
            }
            
            double static_dte = 0.0001;
            if (year > 0 && day > 0) {
                std::tm tm_exp = {0};
                tm_exp.tm_mday = day;
                tm_exp.tm_mon = month;
                tm_exp.tm_year = year - 1900;
                std::time_t exp_start = std::mktime(&tm_exp);
                double diff = std::difftime(exp_start, eod_day_start) / 86400.0;
                static_dte = std::max(0.0001, std::round(diff));
            }

            double T = static_dte / 365.0;
            
            double iv = 0;
            if (type == "CE") iv = IV::call_iv(px, eod_S, strike, R, T);
            else if (type == "PE") iv = IV::put_iv(px, eod_S, strike, R, T);
            
            if (iv <= 0 || std::isnan(iv)) continue;
            
            double iv_pct = iv * 100.0;
            
            std::string key_h = sym + "_" + exp + "_" + parts[2] + "_" + type + "_IV";
            EOD_IV_STORE[key_h] = iv_pct;
            
            std::string clean_exp = exp;
            clean_exp.erase(std::remove(clean_exp.begin(), clean_exp.end(), '-'), clean_exp.end());
            
            std::string key_c = sym + "_" + clean_exp + "_" + parts[2] + "_" + type + "_IV";
            EOD_IV_STORE[key_c] = iv_pct;
        }
        
        SystemLog("[ANALYTICS] Computed " + std::to_string(EOD_IV_STORE.size()) + " EOD IVs.");

        // 2. SAVE TO REDIS CACHE (TTL: 24 HOURS)
        if (g_redis && !EOD_IV_STORE.empty()) {
            std::vector<const char*> argv;
            std::vector<size_t> argvlen;

            std::string cmd = "HSET";
            argv.push_back(cmd.c_str()); argvlen.push_back(cmd.length());
            argv.push_back(cache_key.c_str()); argvlen.push_back(cache_key.length());

            std::vector<std::string> val_strs;
            val_strs.reserve(EOD_IV_STORE.size());

            for (const auto& pair : EOD_IV_STORE) {
                argv.push_back(pair.first.c_str());
                argvlen.push_back(pair.first.length());
                
                val_strs.push_back(std::to_string(pair.second));
                argv.push_back(val_strs.back().c_str());
                argvlen.push_back(val_strs.back().length());
            }

            if (argv.size() > 2) {
                std::lock_guard<std::mutex> r_lock(g_redisMtx);
                redisReply* reply = (redisReply*)redisCommandArgv(g_redis, argv.size(), argv.data(), argvlen.data());
                if (reply) freeReplyObject(reply);
                reply = (redisReply*)redisCommand(g_redis, "EXPIRE %s 86400", cache_key.c_str());
                if (reply) freeReplyObject(reply);
                SystemLog("[CACHE] EOD IVs securely cached in Redis for next reboot.");
            }
        }
    }

    std::string get_eod_ivs_json() {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        
        // Return 80,000 option strings instantly from memory if already built
        if (!CACHED_EOD_JSON.empty()) return CACHED_EOD_JSON;

        std::ostringstream ss;
        ss << "{";
        bool first = true;
        for (const auto& pair : EOD_IV_STORE) {
            if (!first) ss << ",";
            ss << "\"" << pair.first << "\":" << std::fixed << std::setprecision(4) << pair.second;
            first = false;
        }
        for (const auto& pair : EOD_STORE) {
            std::string js_key = pair.first;
            std::replace(js_key.begin(), js_key.end(), '|', '_');
            if (!first) ss << ",";
            ss << "\"" << js_key << "\":" << std::fixed << std::setprecision(4) << pair.second;
            first = false;
        }
        ss << "}";
        CACHED_EOD_JSON = ss.str();
        return CACHED_EOD_JSON;
    }

    static bool parse_symbol_or_pipe(const std::string& symbol, OptionTick& t) {
        if (symbol.find('|') != std::string::npos) {
            std::vector<std::string> parts;
            std::stringstream ss(symbol);
            std::string p;
            while (std::getline(ss, p, '|')) parts.push_back(p);
            
            if (parts.size() == 4) {
                t.underlying = parts[0];
                t.expiry = parts[1];
                try {
                    t.strike = std::stod(parts[2]);
                } catch (...) {
                    t.strike = 0.0;
                    return false;
                }
                
                std::string typeStr = parts[3];
                if (typeStr == "CE") t.type = OptionType::CALL;
                else if (typeStr == "PE") t.type = OptionType::PUT;
                else t.type = OptionType::FUTURE;
                
                std::string d = t.expiry;
                d.erase(std::remove(d.begin(), d.end(), '-'), d.end());
                
                if (t.type == OptionType::FUTURE) {
                    t.symbol = t.underlying + d + "FUT";
                } else {
                    std::string strikeStr = parts[2];
                    strikeStr.erase(strikeStr.find_last_not_of('0') + 1, std::string::npos);
                    if (!strikeStr.empty() && strikeStr.back() == '.') strikeStr.pop_back();
                    t.symbol = t.underlying + d + strikeStr + typeStr;
                }
                return true;
            }
            return false;
        }
        return SymbolParser::parse(symbol, t);
    }

    /* =========================================================
       SAFE NUM
    ========================================================= */
    static inline double safe_num(double val) {
        return (std::isnan(val) || std::isinf(val)) ? 0.0 : val;
    }

    /* =========================================================
       TIME
    ========================================================= */

    static inline double now()
    {
        using namespace std::chrono;
        return std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::system_clock::now().time_since_epoch()
        ).count();
    }

    /* =========================================================
       MID PRICE (SAFE)
    ========================================================= */

    static inline double mid(double b, double a, double ltp)
    {
        if (b > 0 && a > 0 && a >= b)
            return (a + b) * 0.5;

        return ltp;
    }

    /* =========================================================
       EMA
    ========================================================= */

    static double ema(double prev, double val, double alpha)
    {
        if (prev == 0) return val;
        return alpha * val + (1 - alpha) * prev;
    }

    /* =========================================================
       FUTURES ENGINE (SMOOTHED)
    ========================================================= */

    static double update_future(const std::string& u, const std::string& e, double px)
    {
        std::string key = u + "|" + e;

        std::lock_guard<std::mutex> lock(STATE_MTX);

        // Always retain the exact-expiry live future.
        FUT[key] = px;

        // Generic FUT[u] must represent the nearest currently
        // valid future, never whichever expiry packet arrived last.
        static std::unordered_map<std::string, double> NEAR_FUT_EPOCH;

        const double expiry_epoch =
            static_cast<double>(ContractLoader::parseExpiryDate(e));

        const double now_epoch =
            static_cast<double>(std::time(nullptr));

        if (expiry_epoch > 0.0)
        {
            auto it = NEAR_FUT_EPOCH.find(u);

            const bool current_is_expired =
                (it != NEAR_FUT_EPOCH.end() &&
                 it->second <= now_epoch);

            const bool new_is_nearer =
                (it == NEAR_FUT_EPOCH.end() ||
                 current_is_expired ||
                 expiry_epoch < it->second);

            if (new_is_nearer && expiry_epoch >= now_epoch)
            {
                FUT[u] = px;
                NEAR_FUT_EPOCH[u] = expiry_epoch;
            }
        }
        else if (!FUT.count(u))
        {
            // Safe fallback if the expiry cannot be parsed.
            FUT[u] = px;
        }

        return px;
    }

    static double _get_cascading_future(const std::string& u, const std::string& e, double current_epoch)
    {
        if (FUTURE_OVERRIDES.count(e)) {
            const auto& ovr = FUTURE_OVERRIDES[e];
            if (ovr.base_expiry.empty() || ovr.base_expiry == "null") {
                return ovr.offset;
            } else {
                double base_ep = EXP_EPOCH_CACHE.count(ovr.base_expiry) ? EXP_EPOCH_CACHE[ovr.base_expiry] : 0;
                double base_fut = _get_cascading_future(u, ovr.base_expiry, base_ep);
                if (base_fut > 0) return base_fut + ovr.offset;
            }
        }
        
        bool is_index = (u == "NIFTY" || u == "BANKNIFTY" || u == "FINNIFTY" || u == "MIDCPNIFTY" || u == "SENSEX" || u == "BANKEX" || u == "NIFTYNXT50" || u == "SENSEX50");

        bool use_actual = false;
        if (is_index) {
            use_actual = false; // Strictly enforce synthetic futures for indices
        } else {
            if (USE_ACTUAL_FUTURE.count(u)) use_actual = USE_ACTUAL_FUTURE[u];
            else use_actual = true; // Strictly enforce actual futures for stocks
        }

        std::string key = u + "|" + e;
        
        if (use_actual) {
            if (FUT.count(key) && FUT[key] > 0) return FUT[key];
            
            // 3. Chained Interpolation: Fallback to the closest chronologically preceding expiry (Actual Futures)
            double best_fut = 0;
            double min_time_diff = 1e12;
            for (const auto& pair : FUT) {
                if (pair.first.find(u + "|") == 0 && pair.second > 0) {
                    std::string exp_str = pair.first.substr(u.length() + 1);
                    double ep = 0;
                    auto it = EXP_EPOCH_CACHE.find(exp_str);
                    if (it != EXP_EPOCH_CACHE.end()) ep = it->second;
                    else {
                        ep = static_cast<double>(ContractLoader::parseExpiryDate(exp_str));
                        EXP_EPOCH_CACHE[exp_str] = ep;
                    }
                    double diff = current_epoch - ep;
                    if (diff > 0 && diff < min_time_diff) {
                        min_time_diff = diff;
                        best_fut = pair.second;
                    }
                }
            }
            if (best_fut > 0) return best_fut;

            // 4. Generic Underlying (Near Month) Fallback
            return FUT[u];
        } else {
            // Indexes:
            // Prefer an already-built synthetic future for this expiry.
            if (SYN_FUT.count(key) && SYN_FUT[key] > 0)
                return SYN_FUT[key];

            // Actual futures for this exact expiry are also valid fallbacks.
            if (FUT.count(key) && FUT[key] > 0)
                return FUT[key];

            // IMPORTANT:
            // Before the first synthetic future exists, use the live
            // underlying/index value as the initial anchor. This breaks
            // the circular dependency where an option tick needs S before
            // the option chain can create SYN_FUT.
            auto base_it = FUT.find(u);
            if (base_it != FUT.end() && base_it->second > 0)
                return base_it->second;

            // 3. Chained Interpolation: Fallback to the closest
            // chronologically preceding expiry (Synthetic Futures)
            double best_syn = 0;
            double min_time_diff = 1e12;
            for (const auto& pair : SYN_FUT) {
                if (pair.first.find(u + "|") == 0 && pair.second > 0) {
                    std::string exp_str = pair.first.substr(u.length() + 1);
                    double ep = 0;
                    auto it = EXP_EPOCH_CACHE.find(exp_str);
                    if (it != EXP_EPOCH_CACHE.end()) ep = it->second;
                    else {
                        ep = static_cast<double>(ContractLoader::parseExpiryDate(exp_str));
                        EXP_EPOCH_CACHE[exp_str] = ep;
                    }
                    double diff = current_epoch - ep;
                    if (diff > 0 && diff < min_time_diff) {
                        min_time_diff = diff;
                        best_syn = pair.second;
                    }
                }
            }
            if (best_syn > 0) return best_syn;

            // 4. Generic Underlying (Near Month) Fallback
            return FUT[u];
        }
    }

    static double get_cascading_future(const std::string& u, const std::string& e, double current_epoch)
    {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        return _get_cascading_future(u, e, current_epoch);
    }

    /* =========================================================
       SURFACE KEY
    ========================================================= */

    static std::string key(const std::string& s, const std::string& e, double k)
    {
        return s + "|" + e + "|" + std::to_string((int)k);
    }

    /* =========================================================
       JSON BUILDER (ALWAYS RETURNS DATA NOW)
    ========================================================= */

    static std::string json(
        const OptionTick& t,
        double spot,
        double future,
        double iv,
        double iv_bid,
        double iv_ask,
        double price,
        double bid,
        double ask,
        double d,
        double g,
        double th,
        double v,
        double chg5, double chg15, double chg30, double chg60, double chg1d,
        double max_iv, double min_iv, uint32_t oi,
        double gap, double atm_strike,
        double eod_iv,
        double syn_fut
    )
    {
        std::ostringstream ss;

        ss << "{"
           << "\"type\":\"analytics\","
           << "\"symbol\":\"" << t.symbol << "\",";

        ss << "\"ltp\":" << safe_num(price) << ","
           << "\"bid1\":" << safe_num(bid) << ","
           << "\"ask1\":" << safe_num(ask) << ",";

        ss << "\"spot\":" << safe_num(spot) << ","
           << "\"future\":" << safe_num(future) << ","
           << "\"syn_fut\":" << safe_num(syn_fut) << ","
           << "\"basis\":" << 0 << ",";

        ss << "\"iv\":" << safe_num(iv * 100.0) << ","
           << "\"iv_bid\":" << safe_num(iv_bid * 100.0) << ","
           << "\"iv_ask\":" << safe_num(iv_ask * 100.0) << ",";

        ss << "\"delta\":" << safe_num(d) << ","
           << "\"gamma\":" << safe_num(g) << ","
           << "\"theta\":" << safe_num(th) << ","
           << "\"vega\":" << safe_num(v) << ",";

        ss << "\"underlying\":\"" << t.underlying << "\","
           << "\"expiry\":\"" << t.expiry << "\","
           << "\"strike\":" << t.strike << ","
           << "\"chg5\":" << safe_num(chg5) << "," << "\"chg15\":" << safe_num(chg15) << ","
           << "\"chg30\":" << safe_num(chg30) << "," << "\"chg60\":" << safe_num(chg60) << ","
           << "\"chg1d\":" << safe_num(chg1d) << "," << "\"max_iv\":" << safe_num(max_iv * 100.0) << ","
           << "\"min_iv\":" << safe_num(min_iv * 100.0) << "," << "\"oi\":" << oi << ","
           << "\"gap\":" << safe_num(gap) << ","
           << "\"atm_strike\":" << safe_num(atm_strike) << ","
           << "\"eod_iv\":" << safe_num(eod_iv * 100.0)
           << "}";

        return ss.str();
    }

    /* =========================================================
       MAIN ENGINE (SURFACE SAFE MODE)
    ========================================================= */

    std::string process_tick(
        const std::string& symbol,
        double ltp,
        double bid,
        double ask
    )
    {
        OptionTick t;

        if (!parse_symbol_or_pipe(symbol, t))
        {
            static int parse_fail_count = 0;
            if (parse_fail_count < 30)
            {
                ++parse_fail_count;
                std::cout << "[DIAG] parse_symbol_or_pipe FAILED: "
                          << symbol << std::endl;
            }
            return "";
        }

        static int parse_ok_count = 0;
        if (parse_ok_count < 30)
        {
            ++parse_ok_count;
            std::cout << "[DIAG] process_tick PARSED: "
                      << symbol << " | underlying=" << t.underlying
                      << " | expiry=" << t.expiry
                      << " | strike=" << t.strike
                      << " | type=" << static_cast<int>(t.type)
                      << std::endl;
        }

        /* ---------------- FUTURES ---------------- */

        if (is_future(t.type))
        {
            double px = mid(bid, ask, ltp);

            if (t.underlying == "NIFTY")
            {
                static int nifty_fut_diag = 0;
                if (nifty_fut_diag < 30)
                {
                    ++nifty_fut_diag;
                    std::cout
                        << "[NIFTY FUT DIAG]"
                        << " expiry=" << t.expiry
                        << " ltp=" << ltp
                        << " bid=" << bid
                        << " ask=" << ask
                        << " px=" << px
                        << std::endl;
                }
            }

            update_future(t.underlying, t.expiry, px);
            return "";
        }

        double price = mid(bid, ask, ltp);
        if (price <= 0)
            return "";

        /* ---------------- EXPIRY (SAFE) ---------------- */

        std::time_t t_now = std::time(nullptr);
        std::tm tm_now;
        localtime_r(&t_now, &tm_now);

        double expiry_epoch = static_cast<double>(ContractLoader::parseExpiryDate(t.expiry));
        std::time_t exp_t = static_cast<std::time_t>(expiry_epoch);
        std::tm tm_exp;
        localtime_r(&exp_t, &tm_exp);
        
        std::tm tm_today = tm_now;
        tm_today.tm_hour = 0; tm_today.tm_min = 0; tm_today.tm_sec = 0;
        std::time_t today_start = std::mktime(&tm_today);

        std::tm tm_exp_start = tm_exp;
        tm_exp_start.tm_hour = 0; tm_exp_start.tm_min = 0; tm_exp_start.tm_sec = 0;
        std::time_t exp_start = std::mktime(&tm_exp_start);

        double daysDiff = std::round(std::difftime(exp_start, today_start) / 86400.0);

        double currentMinutes = tm_now.tm_hour * 60.0 + tm_now.tm_min + tm_now.tm_sec / 60.0;
        double startMins = 9.0 * 60.0 + 15.0;
        double endMins = 15.0 * 60.0 + 30.0;
        double totalTradingMins = endMins - startMins;

        double fraction = 0;
        if (currentMinutes < startMins) {
            fraction = 1.0;
        } else if (currentMinutes >= endMins) {
            fraction = 0.0;
        } else {
            fraction = (endMins - currentMinutes) / totalTradingMins;
        }

        double dte = std::max(0.000001, daysDiff + fraction);
        
        double T = dte / 365.0;

        const bool is_index = (t.underlying == "NIFTY" || t.underlying == "BANKNIFTY" || t.underlying == "FINNIFTY" || t.underlying == "MIDCPNIFTY" || t.underlying == "SENSEX" || t.underlying == "BANKEX" || t.underlying == "NIFTYNXT50" || t.underlying == "SENSEX50");

        int months_diff = (tm_exp.tm_year - tm_now.tm_year) * 12 + (tm_exp.tm_mon - tm_now.tm_mon);
        double gap = (months_diff > 2) ? 1500.0 : 50.0;
        if (!is_index) gap = 10.0; // Temporary default for stocks until calculated

        {
            std::lock_guard<std::mutex> lock(STATE_MTX);
            EXP_EPOCH_CACHE[t.expiry] = expiry_epoch;
        }
        
        double S = get_cascading_future(
            t.underlying,
            t.expiry,
            expiry_epoch
        );

        if (t.underlying == "NIFTY")
        {
            static int nifty_s_diag = 0;

            if (nifty_s_diag < 30)
            {
                ++nifty_s_diag;

                std::cout
                    << "[NIFTY S DIAG]"
                    << " expiry=" << t.expiry
                    << " expiry_epoch=" << expiry_epoch
                    << " S=" << S
                    << " ltp=" << price
                    << std::endl;
            }
        }

        if (S <= 0)
            return "";

        double K = t.strike;

        /* ---------------- PUT-CALL PARITY (SYNTHETIC FUTURE) ---------------- */
        
        double active_future = S;
        std::string exp_key = t.underlying + "|" + t.expiry;
        std::string opt_key = exp_key + "|" + std::to_string((int)K) + "|" + (is_call(t.type) ? "CE" : "PE");
        double atm_k = 0;

        {
            std::lock_guard<std::mutex> lock(STATE_MTX);
            OPT_LTP[opt_key] = price; // Cache latest option price
            OPT_BID[opt_key] = bid; // Allow strictly 0 overrides to flush out stale bids cleanly
            OPT_ASK[opt_key] = ask;
            
            auto& strikes = EXP_STRIKES[exp_key];
            if (std::find(strikes.begin(), strikes.end(), K) == strikes.end()) {
                strikes.push_back(K); // Keep track of strikes
            }
            
            // Dynamically calculate the Mode (most frequent) Gap for individual stocks
            if (!is_index && strikes.size() >= 2) {
                std::vector<double> sorted_strikes = strikes;
                std::sort(sorted_strikes.begin(), sorted_strikes.end());
                
                std::vector<double> diffs;
                diffs.reserve(sorted_strikes.size());
                for (size_t i = 1; i < sorted_strikes.size(); ++i) {
                    double d = std::round((sorted_strikes[i] - sorted_strikes[i - 1]) * 100.0) / 100.0;
                    if (d > 0.01) diffs.push_back(d);
                }
                
                if (!diffs.empty()) {
                    std::sort(diffs.begin(), diffs.end());
                    int max_count = 0, current_count = 1;
                    double current_val = diffs[0], mode_val = diffs[0];
                    for (size_t i = 1; i < diffs.size(); ++i) {
                        if (std::abs(diffs[i] - current_val) < 0.01) { current_count++; } 
                        else {
                            if (current_count > max_count) { max_count = current_count; mode_val = current_val; }
                            current_count = 1; current_val = diffs[i];
                        }
                    }
                    if (current_count > max_count) mode_val = current_val;
                    gap = mode_val;
                }
            }
            if (GAP_OVERRIDES.count(t.expiry)) gap = GAP_OVERRIDES[t.expiry];

            double syn_bid = 0.0, syn_ask = 0.0, syn_mid = 0.0;

            auto calc_syn = [&](double k, double& f_bid, double& f_ask, double& f_mid) {
                std::string ce_k = exp_key + "|" + std::to_string((int)k) + "|CE";
                std::string pe_k = exp_key + "|" + std::to_string((int)k) + "|PE";
                double ce_b = OPT_BID[ce_k], ce_a = OPT_ASK[ce_k], ce_p = OPT_LTP[ce_k];
                double pe_b = OPT_BID[pe_k], pe_a = OPT_ASK[pe_k], pe_p = OPT_LTP[pe_k];
                if (ce_b > 0 && ce_a > 0 && pe_b > 0 && pe_a > 0) {
                    f_bid = k + ce_b - pe_a;
                    f_ask = k + ce_a - pe_b;
                    f_mid = (f_bid + f_ask) / 2.0;
                    return true;
                } else if (ce_p > 0 && pe_p > 0) {
                    f_mid = k + ce_p - pe_p; // 100% exact parity identity for R=0 avoids solver convergence crash
                    f_bid = f_mid; f_ask = f_mid;
                    return true;
                }
                return false;
            };

            if (STRIKE_OVERRIDES.count(t.expiry)) {
                atm_k = STRIKE_OVERRIDES[t.expiry];
                calc_syn(atm_k, syn_bid, syn_ask, syn_mid);
            } else {
                double anchor = S > 0 ? S : (FUT.count(t.underlying) ? FUT[t.underlying] : 0);
                
                // Prevent vicious cycle: If the cascading S is stale and way off the spot, 
                // we won't find liquid options. Force anchor back to spot if deviation > 1.5%.
                if (FUT.count(t.underlying) && FUT[t.underlying] > 0) {
                    double spot = FUT[t.underlying];
                    if (std::abs(anchor - spot) / spot > 0.015) {
                        anchor = spot;
                    }
                }

                if (anchor > 0) {
                    double floor_k = std::floor(anchor / gap) * gap;
                    double ceil_k  = std::ceil(anchor / gap) * gap;
                    
                    double f1_bid = 0, f1_ask = 0, f1_mid = 0;
                    bool has_f1 = calc_syn(floor_k, f1_bid, f1_ask, f1_mid);
                    
                    double f2_bid = 0, f2_ask = 0, f2_mid = 0;
                    bool has_f2 = (floor_k != ceil_k) ? calc_syn(ceil_k, f2_bid, f2_ask, f2_mid) : false;

                    if (has_f1 && has_f2) {
                        double d1 = std::abs(anchor - floor_k);
                        double d2 = std::abs(anchor - ceil_k);
                        if (d1 + d2 > 0) {
                            double w1 = 1.0 - (d1 / (d1 + d2));
                            double w2 = 1.0 - (d2 / (d1 + d2));
                            syn_bid = f1_bid * w1 + f2_bid * w2;
                            syn_ask = f1_ask * w1 + f2_ask * w2;
                            syn_mid = f1_mid * w1 + f2_mid * w2;
                        } else {
                            syn_bid = f1_bid; syn_ask = f1_ask; syn_mid = f1_mid;
                        }
                    } else if (has_f1) {
                        syn_bid = f1_bid; syn_ask = f1_ask; syn_mid = f1_mid;
                    } else if (has_f2) {
                        syn_bid = f2_bid; syn_ask = f2_ask; syn_mid = f2_mid;
                    } else {
                        double min_diff = 1e9;
                        for (double strike : strikes) {
                            if (std::abs(strike - std::round(strike / gap) * gap) > 0.01) continue;
                            double diff = std::abs(strike - anchor);
                            if (diff < min_diff) {
                                std::string ce_k = exp_key + "|" + std::to_string((int)strike) + "|CE";
                                std::string pe_k = exp_key + "|" + std::to_string((int)strike) + "|PE";
                                if (OPT_LTP[ce_k] > 0 && OPT_LTP[pe_k] > 0) {
                                    min_diff = diff;
                                    atm_k = strike;
                                }
                            }
                        }
                        if (atm_k > 0) calc_syn(atm_k, syn_bid, syn_ask, syn_mid);
                    }
                }
            }

            if (syn_mid > 0) {

                bool valid_syn = true;

                // A synthetic NIFTY future must remain close to
                // the actual NIFTY spot used for this tick.
                if (is_index && t.underlying == "NIFTY" && S > 0) {
                    const double rel_gap =
                        std::abs(syn_mid - S) / S;

                    if (rel_gap > 0.02) {
                        valid_syn = false;
                    }
                }

                if (valid_syn) {

                    // ------------------------------------------------
                    // NIFTY 18-AUG SYNTHETIC FUTURE DIAGNOSTIC
                    // ------------------------------------------------
                    if (t.underlying == "NIFTY" && t.expiry == "18-AUG-26")
                    {
                        static int syn18_diag = 0;

                        if (syn18_diag < 30)
                        {
                            ++syn18_diag;

                            const double actual_near =
                                FUT.count("NIFTY")
                                    ? FUT["NIFTY"]
                                    : 0.0;

                            std::cout
                                << "[NIFTY SYN18 DIAG]"
                                << " K=" << atm_k
                                << " S=" << S
                                << " gap=" << gap
                                << " syn_mid=" << syn_mid
                                << " syn_bid=" << syn_bid
                                << " syn_ask=" << syn_ask
                                << " actual_near=" << actual_near
                                << std::endl;
                        }
                    }
                    SYN_FUT[exp_key] = syn_mid;

                    if (syn_bid > 0)
                        SYN_FUT_BID[exp_key] = syn_bid;

                    if (syn_ask > 0)
                        SYN_FUT_ASK[exp_key] = syn_ask;
                }
            }
            
            // Per user request: For stocks, use the actual future price. For indices, use the synthetic future price.
            if (is_index) {
                if (SYN_FUT.count(exp_key) && SYN_FUT[exp_key] > 0) active_future = SYN_FUT[exp_key];
            }
            // For stocks, active_future defaults to S (the cascading actual future), which is correct.
        }

        /* ---------------- IV (SOFT MODE) ---------------- */

        double iv = is_call(t.type)
            ? IV::call_iv(price, active_future, K, R, T)
            : IV::put_iv(price, active_future, K, R, T);

        if (std::isnan(iv) || iv <= 0) iv = 0.0;

        std::string skey = key(t.symbol, t.expiry, K);

        {
            std::lock_guard<std::mutex> lock(STATE_MTX);
            IV_SURFACE[skey] = iv;
        }

        double iv_bid = 0.0;
        if (bid > 0) {
            iv_bid = is_call(t.type)
                ? IV::call_iv(bid, active_future, K, R, T)
                : IV::put_iv(bid, active_future, K, R, T);
            if (std::isnan(iv_bid) || iv_bid <= 0) iv_bid = 0.0;
        }

        double iv_ask = 0.0;
        if (ask > 0) {
            iv_ask = is_call(t.type)
                ? IV::call_iv(ask, active_future, K, R, T)
                : IV::put_iv(ask, active_future, K, R, T);
            if (std::isnan(iv_ask) || iv_ask <= 0) iv_ask = 0.0;
        }

        double sigma = iv; 

        /* ---------------- INTRADAY HISTORY & CHANGES ---------------- */
        
        std::string stat_key = exp_key + "|" + std::to_string((int)K) + "|" + (is_call(t.type) ? "CE" : "PE");
        double chg5 = 0, chg15 = 0, chg30 = 0, chg60 = 0, chg1d = 0;
        double max_iv = sigma, min_iv = sigma;
        uint32_t oi = 0;
        double yest_iv_out = 0.0;

        {
            std::lock_guard<std::mutex> lock(STATE_MTX);
            auto& stats = OPT_STATS[stat_key];
            double current_ts = now();
            
            stats.snaps.push_back({current_ts, sigma});
            
            // Prune snapshots older than 65 mins, but ALWAYS preserve the 1st snapshot of the day for 1D change
            if (stats.snaps.size() > 500) {
                IvSnapshot first_snap = stats.snaps.front();
                stats.snaps.erase(
                    std::remove_if(stats.snaps.begin() + 1, stats.snaps.end(),
                        [current_ts](const IvSnapshot& s) { return current_ts - s.ts > 65 * 60; }),
                    stats.snaps.end()
                );
                if (stats.snaps.empty() || stats.snaps.front().ts != first_snap.ts) {
                    stats.snaps.insert(stats.snaps.begin(), first_snap);
                }
            }
            
            if (stats.max_iv == -1e9 || sigma > stats.max_iv) stats.max_iv = sigma;
            if (stats.min_iv == 1e9 || sigma < stats.min_iv) stats.min_iv = sigma;
            
            max_iv = stats.max_iv;
            min_iv = stats.min_iv;
            oi = stats.oi;
            
            auto get_change = [&](double minutes) {
                if (stats.snaps.empty()) return 0.0;
                double target = current_ts - (minutes * 60);
                double oldest_iv = stats.snaps.front().iv;
                for (const auto& s : stats.snaps) {
                    if (s.ts >= target) { oldest_iv = s.iv; break; }
                }
                return (sigma - oldest_iv) * 100.0;
            };
            
            chg5 = get_change(5); chg15 = get_change(15); chg30 = get_change(30); chg60 = get_change(60);

            // EOD IV & 1D Change Logic (Replaces faulty intraday fallback)
            std::string clean_exp = t.expiry;
            clean_exp.erase(std::remove(clean_exp.begin(), clean_exp.end(), '-'), clean_exp.end());
            
            std::string js_key = t.underlying + "_" + clean_exp + "_" + std::to_string((int)K) + "_" + (is_call(t.type) ? "CE" : "PE") + "_IV";
            
            double yest_iv = 0.0;
            if (EOD_IV_STORE.count(js_key)) {
                yest_iv = EOD_IV_STORE[js_key] / 100.0;
            }
            
            chg1d = (yest_iv > 0) ? (sigma - yest_iv) * 100.0 : -9999.0;
            yest_iv_out = yest_iv;
        }

        double display_atm_k = std::round(active_future / gap) * gap;
        if (STRIKE_OVERRIDES.count(t.expiry)) {
            display_atm_k = STRIKE_OVERRIDES[t.expiry];
        }

        /* ---------------- GREEKS ---------------- */
        double greeks_sigma = (sigma > 0.0) ? sigma : 1e-4; // Prevent div-by-zero bounds in Black-Scholes

        double d = is_call(t.type)
            ? Greeks::call_delta(active_future, K, R, greeks_sigma, T)
            : Greeks::put_delta(active_future, K, R, greeks_sigma, T);

        double th = is_call(t.type)
            ? Greeks::call_theta(active_future, K, R, greeks_sigma, T)
            : Greeks::put_theta(active_future, K, R, greeks_sigma, T);

        double g = Greeks::gamma(active_future, K, R, greeks_sigma, T);
        double v = Greeks::vega(active_future, K, R, greeks_sigma, T);

        /* ---------------- ALWAYS OUTPUT ---------------- */

        // --- DEBUG PRINT FOR COMPLETE IV CALCULATION ---
        if (t.symbol == "SENSEX" && K == 78200.0) {
            static int print_count = 0;
            if (print_count++ % 50 == 0) { // Print every 50th tick to avoid console spam
                std::string e_opt_k = t.underlying + "|" + t.expiry + "|" + std::to_string((int)K) + "|" + (is_call(t.type) ? "CE" : "PE");
                double y_px = EOD_STORE.count(e_opt_k) ? EOD_STORE[e_opt_k] : 0.0;
                
                std::cout << "\n[IV CALCULATION] " << t.symbol << " " << t.expiry << " " << K << (is_call(t.type) ? " CE" : " PE") << "\n"
                          << "  - LTP (Premium): " << price << "\n"
                          << "  - True Future (S): " << active_future << "\n"
                          << "  - Current Time (Epoch): " << std::fixed << now() << "\n"
                          << "  - Expiry Time (Epoch): " << std::fixed << expiry_epoch << "\n"
                          << "  - DTE (Days to Expiry): " << (T * 365.0) << " Days\n"
                          << "  - Calculated IV: " << (iv * 100.0) << "%\n"
                          << "  - EOD Bhavcopy Premium: " << y_px << "\n"
                          << "  - 1D Change Output: " << chg1d << "\n"
                          << "---------------------------------------------------\n";
            }
        }

        std::string json_payload = json(
            t, S, active_future, iv, iv_bid, iv_ask, price, bid, ask, d, g, th, v,
            chg5, chg15, chg30, chg60, chg1d, max_iv, min_iv, oi,
            gap, display_atm_k, yest_iv_out, SYN_FUT[t.underlying + "|" + t.expiry]
        );

        // RMQ IMPLEMENTATION (REDIS PUB/SUB MESSAGE QUEUE)
        // Microservices can subscribe via: SUBSCRIBE dalal_greeks_stream
        if (g_redis) {
            std::lock_guard<std::mutex> r_lock(g_redisMtx);
            redisReply* reply = (redisReply*)redisCommand(g_redis, "PUBLISH dalal_greeks_stream %s", json_payload.c_str());
            if (reply) freeReplyObject(reply);
        }

        return json_payload;
    }

    void update_underlying(const std::string& symbol, double price)
    {
        std::lock_guard<std::mutex> lock(STATE_MTX);
        FUT[symbol] = price;
    }

    // Decoder-side OI update.
    void update_oi(const std::string& symbol, uint64_t oi)
    {
        OptionTick t;
        if (!parse_symbol_or_pipe(symbol, t))
            return;

        std::string stat_key;

        if (is_future(t.type))
        {
            stat_key = t.underlying + "|" + t.expiry + "|FUT";
        }
        else
        {
            stat_key =
                t.underlying + "|" +
                t.expiry + "|" +
                std::to_string((int)t.strike) + "|" +
                (is_call(t.type) ? "CE" : "PE");
        }

        std::lock_guard<std::mutex> lock(STATE_MTX);
        OPT_STATS[stat_key].oi = oi;
    }

    // Decoder-side volume update.
    void update_volume(const std::string& symbol, uint64_t volume)
    {
        OptionTick t;
        if (!parse_symbol_or_pipe(symbol, t))
            return;

        std::string stat_key;

        if (is_future(t.type))
        {
            stat_key = t.underlying + "|" + t.expiry + "|FUT";
        }
        else
        {
            stat_key =
                t.underlying + "|" +
                t.expiry + "|" +
                std::to_string((int)t.strike) + "|" +
                (is_call(t.type) ? "CE" : "PE");
        }

        std::lock_guard<std::mutex> lock(STATE_MTX);
        OPT_STATS[stat_key].volume = volume;
    }

    // Decoder-side LPP update.
    void update_lpp(const std::string& symbol, double high, double low)
    {
        OptionTick t;
        if (!parse_symbol_or_pipe(symbol, t))
            return;

        std::string stat_key;

        if (is_future(t.type))
        {
            stat_key = t.underlying + "|" + t.expiry + "|FUT";
        }
        else
        {
            stat_key =
                t.underlying + "|" +
                t.expiry + "|" +
                std::to_string((int)t.strike) + "|" +
                (is_call(t.type) ? "CE" : "PE");
        }

        std::lock_guard<std::mutex> lock(STATE_MTX);
        OPT_STATS[stat_key].lpp_high = high;
        OPT_STATS[stat_key].lpp_low = low;
    }
}