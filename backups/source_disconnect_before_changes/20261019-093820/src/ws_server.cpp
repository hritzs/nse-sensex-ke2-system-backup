#include "ws_server.h"
#include "App.h"

#include "contract_loader.h"
#include <string>
#include <thread>
#include <sstream>
#include <iostream>
#include <mutex>
#include <cmath>
#include <fstream>
#include <vector>
#include <algorithm>
#include <filesystem>
#include <cstdlib>
#include <chrono>
#include <atomic>

namespace Analytics {
    void update_eod_data(const std::string& key, double price);
    void set_strike_override(const std::string& expiry, double strike);
    void set_gap_override(const std::string& expiry, double gap);
    void set_future_override(const std::string& target, const std::string& base, double offset);
    void delete_future_override(const std::string& target);
    void set_future_type(const std::string& symbol, bool use_actual);
    void load_overrides();
    void compute_eod_ivs();
    std::string get_eod_ivs_json();
    void set_eod_timestamp(double epoch);
}

/* =========================================================
   STATE CACHE & RECOVERY
========================================================= */
static std::unordered_map<std::string, std::string> LATEST_STATE_CACHE;
static std::mutex CACHE_MTX;
static std::atomic<unsigned> MARKET_SOURCE_CLIENTS{0};

void save_snapshot() {
    // Copy the cache under the lock, then release it before doing
    // any disk I/O. Previously this held CACHE_MTX for the entire
    // file write, which stalls every broadcast_analytics() call
    // (i.e. every live tick) for the duration of the write, once
    // per minute -- a real periodic lag source on the tick path.
    std::unordered_map<std::string, std::string> snapshot_copy;
    {
        std::lock_guard<std::mutex> lock(CACHE_MTX);
        snapshot_copy = LATEST_STATE_CACHE;
    }

    std::ofstream file("screener_snapshot.jsonl");
    if (file.is_open()) {
        for (const auto& pair : snapshot_copy) {
            file << pair.second << "\n";
        }
    }
}

void load_snapshot() {
    std::ifstream file("screener_snapshot.jsonl");
    std::string line;
    if (file.is_open()) {
        std::lock_guard<std::mutex> lock(CACHE_MTX);
        while (std::getline(file, line)) {
            if (line.empty()) continue;
            // Auto-clean legacy corrupted pipe-delimited symbols
            if (line.find("\"symbol\":\"") != std::string::npos && line.find("|") != std::string::npos) continue;
            size_t pos = line.find("\"symbol\":\"");
            if (pos != std::string::npos) {
                size_t start = pos + 10;
                size_t end = line.find("\"", start);
                if (end != std::string::npos) {
                    std::string sym = line.substr(start, end - start);
                    LATEST_STATE_CACHE[sym] = line;
                }
            }
        }
        std::cout << "[SNAPSHOT] Loaded " << LATEST_STATE_CACHE.size() << " saved states from screener_snapshot.jsonl" << std::endl;
    }
}

/* =========================================================
   EOD BHAVCOPY PARSER CACHE
========================================================= */
void parse_and_load_csv(const std::string& file_content) {
    auto parse_line = [](const std::string& line) {
        std::vector<std::string> cols; std::string cell; bool inQuotes = false;
        char delimiter = (line.find('\t') != std::string::npos) ? '\t' : ',';
        for (char c : line) {
            if (c == '"') { inQuotes = !inQuotes; continue; } // Strip quotes natively
            else if (c == delimiter && !inQuotes) {
                cell.erase(0, cell.find_first_not_of(" \t")); cell.erase(cell.find_last_not_of(" \t") + 1);
                cols.push_back(cell); cell.clear();
            } else if (c != '\r' && c != '\n') { cell += c; }
        }
        cell.erase(0, cell.find_first_not_of(" \t")); cell.erase(cell.find_last_not_of(" \t") + 1);
        cols.push_back(cell); return cols;
    };

    std::istringstream stream(file_content);
    std::string line;
    if (!std::getline(stream, line)) return;

    std::vector<std::string> headers = parse_line(line);
    auto to_upper = [](std::string s) { for(auto& c: s) c = toupper(c); return s; };
    int instIdx = -1, symIdx = -1, expIdx = -1, strIdx = -1, typIdx = -1, clsIdx = -1, timeIdx = -1;
    for (size_t i=0; i<headers.size(); ++i) {
        std::string h = to_upper(headers[i]);
        if (h == "INSTRUMENT" || h == "INST TYPE") instIdx = i; 
        else if (h == "SYMBOL" || h == "SC_NAME" || h == "UNDLY_TICKER") symIdx = i;
        else if (h == "EXPIRY_DT" || h == "EXPIRY_DATE" || h == "EXPIRY DATE") expIdx = i; 
        else if (h == "STRIKE_PR" || h == "STRIKE_PRICE" || h == "STRIKE") strIdx = i;
        else if (h == "OPTION_TYP" || h == "OPTION_TYPE" || h == "OPT_TYPE") typIdx = i; 
        else if (h == "CLOSE" || h == "CLOSE_PR" || h == "CLOSE_PRICE" || h == "SETTLE_PR") clsIdx = i;
        else if (h == "TIMESTAMP" || h == "DATE") timeIdx = i;
    }

    int loaded_count = 0;
    double latest_bhav_epoch = 0;
    while (std::getline(stream, line)) {
        if (line.empty() || line == "\r") continue;
        std::vector<std::string> cols = parse_line(line);

        int max_req = std::max({instIdx, symIdx, expIdx, strIdx, typIdx, clsIdx});
        if (max_req != -1 && cols.size() <= (size_t)max_req) continue;

        if (timeIdx != -1 && latest_bhav_epoch == 0 && cols.size() > (size_t)timeIdx) {
            std::string t_str = cols[timeIdx];
            if (!t_str.empty()) {
                latest_bhav_epoch = static_cast<double>(ContractLoader::parseExpiryDate(t_str));
                std::string expParts = t_str; 
                std::replace(expParts.begin(), expParts.end(), '-', ' ');
                std::replace(expParts.begin(), expParts.end(), '/', ' ');
                std::istringstream iss(expParts);
                std::string d, m, y;
                iss >> d >> m >> y;
                
                int day = 0, year = 0, month = 0;
                try { day = std::stoi(d); } catch(...) {}
                try { year = std::stoi(y); } catch(...) {}
                if (year > 0 && year < 100) year += 2000;
                
                for(auto& c: m) c = toupper(c);
                if (m.length() > 3) m = m.substr(0, 3);
                if (m == "JAN") month = 0; else if (m == "FEB") month = 1;
                else if (m == "MAR") month = 2; else if (m == "APR") month = 3;
                else if (m == "MAY") month = 4; else if (m == "JUN") month = 5;
                else if (m == "JUL") month = 6; else if (m == "AUG") month = 7;
                else if (m == "SEP") month = 8; else if (m == "OCT") month = 9;
                else if (m == "NOV") month = 10; else if (m == "DEC") month = 11;
                
                if (year > 0 && day > 0) {
                    std::tm tm_bhav = {0};
                    tm_bhav.tm_mday = day;
                    tm_bhav.tm_mon = month;
                    tm_bhav.tm_year = year - 1900;
                    latest_bhav_epoch = static_cast<double>(std::mktime(&tm_bhav));
                }
            }
        }

        std::string inst = instIdx != -1 ? to_upper(cols[instIdx]) : "";
        std::string sym = symIdx != -1 ? cols[symIdx] : "";
        std::string expRaw = expIdx != -1 ? cols[expIdx] : "";
        
        std::string expRawUpper = expRaw;
        for(auto& c: expRawUpper) c = toupper(c);
        
        double closePx = 0;
        try { if (clsIdx != -1) closePx = std::stod(cols[clsIdx]); } catch(...) { continue; }
        
        std::string expParts = expRaw; 
        std::replace(expParts.begin(), expParts.end(), '-', ' ');
        std::replace(expParts.begin(), expParts.end(), '/', ' ');
        std::istringstream iss(expParts);
        std::string d, m, y;
        iss >> d >> m >> y;
        if(d.length() == 1) d = "0" + d;
        for(auto& c: m) c = toupper(c);
        if(m.length() > 3) m = m.substr(0, 3);
        
        std::string y_2 = y;
        if (y_2.length() == 4) y_2 = y_2.substr(2);

        std::string frontend_exp = d + "-" + m + "-" + y_2;

        std::string optTyp = typIdx != -1 ? to_upper(cols[typIdx]) : "";
        
        double strike = 0;
        try { if (strIdx != -1) strike = std::stod(cols[strIdx]); } catch(...) {}

        if (inst == "FUTSTK" || inst == "FUTIDX" || optTyp == "XX" || (strike == 0 && (optTyp.empty() || optTyp == "-"))) {
            Analytics::update_eod_data(sym + "|" + frontend_exp + "|FUT", closePx);
            loaded_count++;
            if (loaded_count <= 5) {
                std::cout << "[BHAVCOPY DEBUG] Parsed FUT: " << sym << " | " << frontend_exp << " | Close: " << closePx << std::endl;
            }
        } else if (inst == "OPTSTK" || inst == "OPTIDX" || optTyp == "CE" || optTyp == "PE") {
            Analytics::update_eod_data(sym + "|" + frontend_exp + "|" + std::to_string((int)strike) + "|" + optTyp, closePx);
            loaded_count++;
            if (loaded_count <= 5) {
                std::cout << "[BHAVCOPY DEBUG] Parsed OPT: " << sym << " | " << frontend_exp << " | " << strike << " | " << optTyp << " | Close: " << closePx << std::endl;
            }
        }
    }

    if (latest_bhav_epoch > 0) Analytics::set_eod_timestamp(latest_bhav_epoch);

    std::cout << "[BHAVCOPY] Loaded " << loaded_count << " EOD records into Analytics Engine." << std::endl;
    Analytics::compute_eod_ivs();
}

struct PerSocketData {};

static uWS::App* APP = nullptr;
static uWS::Loop* LOOP = nullptr;

static std::mutex WS_MTX;
static int CLIENT_COUNT = 0;

/* =========================================================
   JSON HELPERS
========================================================= */
static std::string extract_json_string(const std::string& json, const std::string& key) {
    std::string search = "\"" + key + "\":";
    size_t pos = json.find(search);
    if (pos == std::string::npos) {
        search = "\"" + key + "\": ";
        pos = json.find(search);
    }
    if (pos != std::string::npos) {
        size_t start = json.find("\"", pos + search.length());
        if (start != std::string::npos) {
            size_t end = json.find("\"", start + 1);
            if (end != std::string::npos) return json.substr(start + 1, end - start - 1);
        }
    }
    return "";
}

static std::string extract_json_number(const std::string& json, const std::string& key) {
    std::string search = "\"" + key + "\":";
    size_t pos = json.find(search);
    if (pos == std::string::npos) {
        search = "\"" + key + "\": ";
        pos = json.find(search);
    }
    if (pos != std::string::npos) {
        size_t start = pos + search.length();
        while (start < json.length() && (json[start] == ' ' || json[start] == '"')) start++;
        size_t end = start;
        while (end < json.length() && (std::isdigit(json[end]) || json[end] == '.' || json[end] == '-')) end++;
        if (end > start) return json.substr(start, end - start);
        else if (start + 4 <= json.length() && json.substr(start, 4) == "null") return "null";
    }
    return "";
}

static std::string scan_dir(const std::string& path, const std::string& prefix) {
    std::string best = "";
    try {
        int max_date = 0;
        std::filesystem::file_time_type latest_time;
        bool found = false;

        std::string lower_prefix = prefix;
        std::transform(lower_prefix.begin(), lower_prefix.end(), lower_prefix.begin(), ::tolower);

        for (const auto& entry : std::filesystem::directory_iterator(path)) {
            std::string fname = entry.path().filename().string();
            std::string lower_fname = fname;
            std::transform(lower_fname.begin(), lower_fname.end(), lower_fname.begin(), ::tolower);

            if (entry.is_regular_file() && lower_fname.find(lower_prefix) == 0 && lower_fname.find("bhav.csv") != std::string::npos) {
                auto ftime = std::filesystem::last_write_time(entry);
                
                size_t end_idx = lower_fname.find("bhav.csv");
                std::string date_str = fname.substr(prefix.length(), end_idx - prefix.length());
                std::string upper_ds = date_str;
                for(auto& c: upper_ds) c = std::toupper(c);

                int day = 0, year = 0, month = 0;
                size_t m_start = 0;
                while (m_start < upper_ds.length() && std::isdigit(upper_ds[m_start])) m_start++;
                size_t m_end = m_start;
                while (m_end < upper_ds.length() && std::isalpha(upper_ds[m_end])) m_end++;

                int file_date = 0;
                if (m_start > 0 && m_end > m_start && m_end < upper_ds.length()) {
                    try {
                        day = std::stoi(upper_ds.substr(0, m_start));
                        year = std::stoi(upper_ds.substr(m_end));
                        std::string m_str = upper_ds.substr(m_start, 3);
                        if (m_str == "JAN") month = 1; else if (m_str == "FEB") month = 2;
                        else if (m_str == "MAR") month = 3; else if (m_str == "APR") month = 4;
                        else if (m_str == "MAY") month = 5; else if (m_str == "JUN") month = 6;
                        else if (m_str == "JUL") month = 7; else if (m_str == "AUG") month = 8;
                        else if (m_str == "SEP") month = 9; else if (m_str == "OCT") month = 10;
                        else if (m_str == "NOV") month = 11; else if (m_str == "DEC") month = 12;
                        
                        if (year > 0 && year < 100) year += 2000;
                        file_date = year * 10000 + month * 100 + day; // Formats as YYYYMMDD for perfect integer sorting
                    } catch(...) {}
                }

                if (file_date > 0) {
                    if (file_date > max_date) {
                        max_date = file_date;
                        best = entry.path().string();
                        found = true;
                    }
                } else {
                    if (!found || ftime > latest_time) {
                        latest_time = ftime;
                        best = entry.path().string();
                        found = true;
                    }
                }
            }
        }
    } catch(...) {}
    return best;
}

/* =========================================================
   SAFE PUBLISH (DEBUG ENABLED)
========================================================= */

static inline void publish(const std::string& msg)
{
    if (!APP || !LOOP)
        return;

    LOOP->defer([msg]()
    {
        if (APP)
        {
            APP->publish(
                "ticks",
                std::string_view(msg.data(), msg.size()),
                uWS::OpCode::TEXT
            );
        }
    });
}

/* =========================================================
   START SERVER
========================================================= */

void start_ws_server(int port)
{
    setenv("TZ", "Asia/Kolkata", 1);
    tzset();

    // Load permanently saved backend overrides (Futures, Strikes, Gaps)
    Analytics::load_overrides();

    // Start periodic snapshot background thread
    std::thread([]() {
        load_snapshot(); // Load initial state on startup
        while (true) {
            std::this_thread::sleep_for(std::chrono::minutes(1));
            save_snapshot();
        }
    }).detach();

    // Pre-load Bhavcopies strictly into C++ safely
    std::thread([]() {
        std::this_thread::sleep_for(std::chrono::seconds(2)); // Allow WSL network mounts to wake up

        std::string nse_file = scan_dir("/home/administrator/nse_v2_microservices/data/BhavCopy", "fo");

        std::string bse_file = scan_dir("/home/administrator/nse_v2_microservices/data/BhavCopy/BSEfobhavcopy", "BSEfo");

        auto load_csv = [](const std::string& path, const std::string& label) {
            if (path.empty()) return false;
            std::ifstream file(path, std::ios::binary);
            if (file.is_open()) {
                std::stringstream buffer; buffer << file.rdbuf();
                std::cout << "[BHAVCOPY] Auto-Preloaded " << label << " newest file: " << path << std::endl;
                parse_and_load_csv(buffer.str());
                return true;
            }
            return false;
        };

        bool nse_ok = load_csv(nse_file, "NSE");
        if (!nse_ok) std::cout << "[BHAVCOPY] Failed to load NSE file: " << (nse_file.empty() ? "None found in directory" : nse_file) << std::endl;

        bool bse_ok = load_csv(bse_file, "BSE");
        if (!bse_ok) std::cout << "[BHAVCOPY] Failed to load BSE file: " << (bse_file.empty() ? "None found in directory" : bse_file) << std::endl;

    }).detach();

    std::thread([port]()
    {
        LOOP = uWS::Loop::get();
        APP = new uWS::App();

        uWS::App::WebSocketBehavior<PerSocketData> behavior;
        uWS::App::WebSocketBehavior<PerSocketData> source_behavior;
        source_behavior.open = [](auto* ws) {
            MARKET_SOURCE_CLIENTS.fetch_add(1, std::memory_order_relaxed);
            ws->subscribe("market_source");
        };
        source_behavior.close = [](auto*, int, std::string_view) {
            MARKET_SOURCE_CLIENTS.fetch_sub(1, std::memory_order_relaxed);
        };
        source_behavior.maxBackpressure = 1024 * 1024;
        source_behavior.closeOnBackpressureLimit = true;

        /* ---------------- CONNECTION ---------------- */

        behavior.open = [](auto* ws)
        {
            {
                std::lock_guard<std::mutex> lock(WS_MTX);
                CLIENT_COUNT++;
            }

            ws->subscribe("ticks");

            std::cout << "[WS] CLIENT CONNECTED | total=" 
                      << CLIENT_COUNT << std::endl;

            // Preload initial cache to the newly connected client.
            //
            // NOTE: this send loop runs synchronously on the single
            // uWS event loop thread. While it runs, ticks for EVERY
            // connected client are stalled, not just this one. With
            // several expiries x many strikes x CE/PE cached, a full
            // unfiltered replay on every reconnect is a real lag
            // burst. We cap the replay to symbols updated within the
            // last 30s using the timestamp field, since older cached
            // entries are not live data anyway.
            std::vector<std::string> snapshot_msgs;
            size_t total_cache_size = 0;
            {
                std::lock_guard<std::mutex> lock(CACHE_MTX);

                total_cache_size = LATEST_STATE_CACHE.size();
                snapshot_msgs.reserve(total_cache_size);

                const auto now_ms =
                    std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::system_clock::now().time_since_epoch()
                    ).count();

                for (const auto& pair : LATEST_STATE_CACHE) {
                    std::string ts_str =
                        extract_json_number(pair.second, "timestamp");

                    bool recent = true;

                    if (!ts_str.empty() && ts_str != "null") {
                        try {
                            long long ts = std::stoll(ts_str);
                            recent = (now_ms - ts) <= 30000;
                        } catch (...) {
                            recent = true;
                        }
                    }

                    if (recent) {
                        snapshot_msgs.push_back(pair.second);
                    }
                }
            }

            std::cout << "[WS] REPLAY " << snapshot_msgs.size()
                      << " recent symbols to new client (cache total="
                      << total_cache_size << ")" << std::endl;

            for (const auto& msg : snapshot_msgs) {
                ws->send(msg, uWS::OpCode::TEXT);
            }
        };

        /* ---------------- DISCONNECT ---------------- */

        behavior.close = [](auto* ws, int code, std::string_view msg)
        {
            {
                std::lock_guard<std::mutex> lock(WS_MTX);
                CLIENT_COUNT--;
            }

            std::cout << "[WS] CLIENT DISCONNECTED | total="
                      << CLIENT_COUNT << std::endl;
        };

        /* ---------------- MESSAGE (future use) ---------------- */

        behavior.message = [](auto* ws, std::string_view msg, uWS::OpCode op)
        {
            std::string s_msg(msg);
            if (s_msg.find("\"action\":\"set_override\"") != std::string::npos || s_msg.find("\"action\": \"set_override\"") != std::string::npos) {
                std::string target = extract_json_string(s_msg, "target");
                std::string val_str = extract_json_number(s_msg, "value");
                if (!target.empty()) {
                    if (val_str.empty() || val_str == "null") Analytics::set_strike_override(target, 0);
                    else {
                        try { Analytics::set_strike_override(target, std::stod(val_str)); } catch(...) {}
                    }
                }
            }
            else if (s_msg.find("\"action\":\"set_gap_override\"") != std::string::npos || s_msg.find("\"action\": \"set_gap_override\"") != std::string::npos) {
                std::string target = extract_json_string(s_msg, "target");
                std::string val_str = extract_json_number(s_msg, "value");
                if (!target.empty()) {
                    if (val_str.empty() || val_str == "null") Analytics::set_gap_override(target, 0);
                    else {
                        try { Analytics::set_gap_override(target, std::stod(val_str)); } catch(...) {}
                    }
                }
            }
            else if (s_msg.find("\"action\":\"set_future_override\"") != std::string::npos || s_msg.find("\"action\": \"set_future_override\"") != std::string::npos) {
                std::string target = extract_json_string(s_msg, "target");
                std::string base = extract_json_string(s_msg, "base");
                std::string val_str = extract_json_number(s_msg, "offset");
                if (!target.empty() && !val_str.empty() && val_str != "null") {
                    try { Analytics::set_future_override(target, base, std::stod(val_str)); } catch(...) {}
                }
            }
            else if (s_msg.find("\"action\":\"delete_future_override\"") != std::string::npos || s_msg.find("\"action\": \"delete_future_override\"") != std::string::npos) {
                std::string target = extract_json_string(s_msg, "target");
                if (!target.empty()) {
                    Analytics::delete_future_override(target);
                }
            }
            else if (s_msg.find("\"action\":\"set_future_type\"") != std::string::npos || s_msg.find("\"action\": \"set_future_type\"") != std::string::npos) {
                std::string symbol = extract_json_string(s_msg, "symbol");
                bool use_actual = s_msg.find("\"use_actual\":true") != std::string::npos || s_msg.find("\"use_actual\": true") != std::string::npos;
                if (!symbol.empty()) {
                    Analytics::set_future_type(symbol, use_actual);
                }
            }
            if (s_msg.find("\"action\":\"ping\"") != std::string::npos || s_msg.find("\"action\": \"ping\"") != std::string::npos) {
                ws->send("{\"status\":\"pong\"}", uWS::OpCode::TEXT);
            }
        };

        /* ---------------- SERVER ---------------- */

        APP->options("/*", [](auto* res, auto* req) {
            // Handle CORS preflight checks from the browser
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
            res->writeHeader("Access-Control-Allow-Headers", "Content-Type");
            res->end();
        })
        .get("/symbols", [](auto* res, auto* req) {
            // HTTP GET route for the frontend fetch
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            
            // Dynamically load base symbols from the contract loader
            auto allSymbols = g_contractLoader.getAllSymbols();
            std::string json_array = "[";
            for (size_t i = 0; i < allSymbols.size(); ++i) {
                json_array += "\"" + allSymbols[i] + "\"";
                if (i < allSymbols.size() - 1) json_array += ", ";
            }
            json_array += "]";
            
            // Wrap the array in an object as expected by the frontend App.jsx
            std::string json_object = "{\"symbols\":" + json_array + "}";

            res->end(json_object);
        })
        .get("/api/symbols", [](auto* res, auto* req) {
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            auto allSymbols = g_contractLoader.getAllSymbols();
            std::string json_array = "[";
            for (size_t i = 0; i < allSymbols.size(); ++i) {
                json_array += "\"" + allSymbols[i] + "\"";
                if (i < allSymbols.size() - 1) json_array += ", ";
            }
            json_array += "]";
            res->end("{\"available\":" + json_array + ", \"expiry_list\": []}");
        })
        .get("/api/raw_data", [](auto* res, auto* req) {
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            
            std::string json = "{";
            bool first = true;
            {
                std::lock_guard<std::mutex> lock(CACHE_MTX);
                for (const auto& pair : LATEST_STATE_CACHE) {
                    if (!first) json += ",";
                    json += "\"" + pair.first + "\":" + pair.second;
                    first = false;
                }
            }
            json += "}";
            res->end(json);
        })
        .get("/api/portfolio/data", [](auto* res, auto* req) {
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            res->end("{}"); // Return an empty object stub to prevent 404
        })
        .get("/eod-ivs", [](auto* res, auto* req) {
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            res->end(Analytics::get_eod_ivs_json());
        })
        .get("/get-results", [](auto* res, auto* req) {
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            
            std::string filepath = "../shared/BSEResults.csv";
            std::ifstream file(filepath);
            if (!file.is_open()) {
                filepath = "BSEResults.csv"; // Fallback to local
                file.open(filepath);
                if (!file.is_open()) {
                    res->end("[]");
                    return;
                }
            }

            auto escape_json = [](const std::string& s) {
                std::string res;
                for (char c : s) {
                    if (c == '"') res += "\\\"";
                    else if (c == '\\') res += "\\\\";
                    else if (c == '\b') res += "\\b";
                    else if (c == '\f') res += "\\f";
                    else if (c == '\n') res += "\\n";
                    else if (c == '\r') res += "\\r";
                    else if (c == '\t') res += "\\t";
                    else res += c;
                }
                return res;
            };

            auto parse_line = [](const std::string& line) {
                std::vector<std::string> cols;
                std::string cell;
                bool inQuotes = false;
                char delimiter = (line.find('\t') != std::string::npos) ? '\t' : ',';
                for (char c : line) {
                    if (c == '"') { inQuotes = !inQuotes; continue; } // Strip quotes natively
                    else if (c == delimiter && !inQuotes) {
                        cell.erase(0, cell.find_first_not_of(" "));
                        cell.erase(cell.find_last_not_of(" ") + 1);
                        cols.push_back(cell);
                        cell.clear();
                    } else if (c != '\r' && c != '\n') {
                        cell += c;
                    }
                }
                cell.erase(0, cell.find_first_not_of(" "));
                cell.erase(cell.find_last_not_of(" ") + 1);
                cols.push_back(cell);
                return cols;
            };

            std::string line;
            if (!std::getline(file, line)) { res->end("[]"); return; }

            std::string json = "[";
            bool first = true;
            while (std::getline(file, line)) {
                if (line.empty() || line == "\r") continue;
                std::vector<std::string> cols = parse_line(line);
                if (cols.size() < 4) continue;
                
                std::string date_str = cols[3];
                int month = -1;
                std::string s_up = date_str;
                for (char& c : s_up) c = std::toupper(c);
                
                if (s_up.find("JAN") != std::string::npos || s_up.find("-01-") != std::string::npos || s_up.find("/01/") != std::string::npos) month = 1;
                else if (s_up.find("FEB") != std::string::npos || s_up.find("-02-") != std::string::npos || s_up.find("/02/") != std::string::npos) month = 2;
                else if (s_up.find("MAR") != std::string::npos || s_up.find("-03-") != std::string::npos || s_up.find("/03/") != std::string::npos) month = 3;
                else if (s_up.find("APR") != std::string::npos || s_up.find("-04-") != std::string::npos || s_up.find("/04/") != std::string::npos) month = 4;
                else if (s_up.find("MAY") != std::string::npos || s_up.find("-05-") != std::string::npos || s_up.find("/05/") != std::string::npos) month = 5;
                else if (s_up.find("JUN") != std::string::npos || s_up.find("-06-") != std::string::npos || s_up.find("/06/") != std::string::npos) month = 6;
                else if (s_up.find("JUL") != std::string::npos || s_up.find("-07-") != std::string::npos || s_up.find("/07/") != std::string::npos) month = 7;
                else if (s_up.find("AUG") != std::string::npos || s_up.find("-08-") != std::string::npos || s_up.find("/08/") != std::string::npos) month = 8;
                else if (s_up.find("SEP") != std::string::npos || s_up.find("-09-") != std::string::npos || s_up.find("/09/") != std::string::npos) month = 9;
                else if (s_up.find("OCT") != std::string::npos || s_up.find("-10-") != std::string::npos || s_up.find("/10/") != std::string::npos) month = 10;
                else if (s_up.find("NOV") != std::string::npos || s_up.find("-11-") != std::string::npos || s_up.find("/11/") != std::string::npos) month = 11;
                else if (s_up.find("DEC") != std::string::npos || s_up.find("-12-") != std::string::npos || s_up.find("/12/") != std::string::npos) month = 12;
                else if (s_up.length() >= 10 && (s_up[4] == '-' || s_up[4] == '/')) {
                    try { month = std::stoi(s_up.substr(5, 2)); } catch(...) {}
                }
                
                std::string quarter = "N/A";
                std::string month_type = "N/A";

                if (month >= 1 && month <= 12) {
                    if (month >= 1 && month <= 3) quarter = "Q3";
                    else if (month >= 4 && month <= 6) quarter = "Q4";
                    else if (month >= 7 && month <= 9) quarter = "Q1";
                    else if (month >= 10 && month <= 12) quarter = "Q2";

                    if (month == 1 || month == 4 || month == 7 || month == 10) month_type = "First Month";
                    else month_type = "Next Month";
                }

                if (!first) json += ",";
                json += "{";
                json += "\"stock\":\"" + escape_json(cols[1]) + "\",";
                json += "\"company_name\":\"" + escape_json(cols[2]) + "\",";
                json += "\"date\":\"" + escape_json(cols[3]) + "\",";
                json += "\"quarter\":\"" + quarter + "\",";
                json += "\"month_type\":\"" + month_type + "\"";
                json += "}";
                first = false;
            }
            json += "]";
            res->end(json);
        })
        .get("/api/config", [](auto* res, auto* req) {
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            
            std::string key(req->getQuery("key"));
            std::string uid(req->getQuery("user_id"));
            if (key.empty() || uid.empty()) { res->end("[]"); return; }
            
            std::string path = "user_configs/" + uid + "_" + key + ".json";
            std::ifstream f(path);
            if (f.is_open()) {
                std::stringstream buf; buf << f.rdbuf();
                res->end(buf.str());
            } else {
                res->end("[]");
            }
        })
        .post("/api/config", [](auto* res, auto* req) {
            res->onAborted([]() {});
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            
            std::string* buffer = new std::string();
            res->onData([res, buffer](std::string_view data, bool last) {
                buffer->append(data.data(), data.length());
                if (last) {
                    std::string key = extract_json_string(*buffer, "key");
                    std::string uid = extract_json_string(*buffer, "user_id");
                    std::filesystem::create_directory("user_configs");
                    if (!key.empty() && !uid.empty()) std::ofstream("user_configs/" + uid + "_" + key + ".json") << *buffer;
                    delete buffer;
                    res->end("{\"status\":\"success\"}");
                }
            });
        })
        .get("/api/strike_override", [](auto* res, auto* req) {
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            res->end("{}");
        })
        .post("/api/strike_override", [](auto* res, auto* req) {
            res->onAborted([]() {});
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            res->onData([res](std::string_view data, bool last) {
                if (last) {
                    std::string s_data(data);
                    std::string target = extract_json_string(s_data, "expiry");
                    std::string val_str = extract_json_number(s_data, "strike");
                    if (!target.empty()) {
                        if (val_str.empty() || val_str == "null") Analytics::set_strike_override(target, 0);
                        else {
                            try { Analytics::set_strike_override(target, std::stod(val_str)); } catch(...) {}
                        }
                    }
                    res->end("{\"status\":\"success\"}");
                }
            });
        })
        .get("/bhavcopy", [](auto* res, auto* req) {
            res->onAborted([]() {});
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "text/csv");
            
            std::string_view path_param = req->getQuery("path");
            std::string filepath;
            
            if (!path_param.empty()) {
                std::string encoded(path_param);
                std::string decoded;
                for (size_t i = 0; i < encoded.length(); ++i) {
                    if (encoded[i] == '%' && i + 2 < encoded.length()) {
                        int hex_val;
                        std::istringstream is(encoded.substr(i + 1, 2));
                        if (is >> std::hex >> hex_val) { decoded += static_cast<char>(hex_val); i += 2; }
                    } else if (encoded[i] == '+') { decoded += ' '; } 
                    else { decoded += encoded[i]; }
                }

            // Safely translate the Windows UNC path from the UI into the mapped Linux WSL path
            size_t pos = decoded.find_last_of("\\/");
            if (pos != std::string::npos) {
                std::string filename = decoded.substr(pos + 1);
                if (decoded.find("BSEfobhavcopy") != std::string::npos || filename.substr(0, 3) == "BSE") {
                    filepath = "/home/administrator/nse_v2_microservices/data/BhavCopy/BSEfobhavcopy/" + filename;
                } else {
                    filepath = "/home/administrator/nse_v2_microservices/data/BhavCopy/" + filename;
                }
            } else {
                filepath = "/home/administrator/nse_v2_microservices/data/BhavCopy/" + decoded;
            }
            } else {
                filepath = scan_dir("/home/administrator/nse_v2_microservices/data/BhavCopy", "fo");
            }

        if (filepath.empty()) {
            res->writeStatus("404 Not Found");
            res->end("Bhavcopy not found.");
            return;
        }

        std::ifstream file(filepath, std::ios::binary);
            if (!file.is_open()) {
                res->writeStatus("404 Not Found");
                res->end("Bhavcopy not found.");
                return;
            }
            
            std::stringstream buffer;
            buffer << file.rdbuf();
            std::string file_content = buffer.str();

            // Send the HTTP response immediately before heavy processing
            res->end(file_content);

            // Thread securely handles loading EOD state into the analytics C++ engine unblocked
            std::thread([fc = std::move(file_content)]() {
                parse_and_load_csv(fc);
            }).detach();
        })
        .get("/get-historical-data/:symbol", [](auto* res, auto* req) {
            res->onAborted([]() {});
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");

            std::string symbol(req->getParameter(0));
            
            // Uses the local paths for the native WSL build
            std::string filepath = "../stockdump/" + symbol + ".csv";
            if (symbol == "SENSEX" || symbol == "BANKEX") {
                filepath = "../shared/Yogesh/Processed_Sensex_Data/Sensex_Processed_Data.csv";
            }
            
            std::ifstream file(filepath);
            if (!file.is_open()) {
                if (symbol == "SENSEX" || symbol == "BANKEX") {
                    filepath = "Sensex_Processed_Data.csv"; // Fallback to local
                    file.open(filepath);
                } else {
                    filepath = symbol + ".csv";
                    file.open(filepath);
                }
            }
            
            if (!file.is_open()) {
                res->end("[]");
                return;
            }

            // Helper to escape strings for JSON
            auto escape_json = [](const std::string& s) {
                std::string res;
                for (char c : s) {
                    if (c == '"') res += "\\\"";
                    else if (c == '\\') res += "\\\\";
                    else if (c == '\b') res += "\\b";
                    else if (c == '\f') res += "\\f";
                    else if (c == '\n') res += "\\n";
                    else if (c == '\r') res += "\\r";
                    else if (c == '\t') res += "\\t";
                    else res += c;
                }
                return res;
            };

            // Helper to parse CSV lines cleanly (respecting quotes & stripping spaces)
            auto parse_line = [](const std::string& line) {
                std::vector<std::string> cols;
                std::string cell;
                bool inQuotes = false;
                char delimiter = (line.find('\t') != std::string::npos) ? '\t' : ',';
                for (char c : line) {
                    if (c == '"') { inQuotes = !inQuotes; continue; } // Strip quotes natively
                    else if (c == delimiter && !inQuotes) {
                        cell.erase(0, cell.find_first_not_of(" \t"));
                        cell.erase(cell.find_last_not_of(" \t") + 1);
                        cols.push_back(cell);
                        cell.clear();
                    } else if (c != '\r' && c != '\n') {
                        cell += c;
                    }
                }
                cell.erase(0, cell.find_first_not_of(" \t"));
                cell.erase(cell.find_last_not_of(" \t") + 1);
                cols.push_back(cell);
                return cols;
            };

            std::string line;
            if (!std::getline(file, line)) { res->end("[]"); return; }

            std::vector<std::string> headers = parse_line(line);
            std::string json = "[";
            bool first = true;

            while (std::getline(file, line)) {
                if (line.empty() || line == "\r") continue;
                std::vector<std::string> cols = parse_line(line);
                
                if (!first) json += ",";
                json += "{";
                size_t bound = std::min(headers.size(), cols.size());
                for (size_t i = 0; i < bound; ++i) {
                    std::string val = cols[i];
                    if ((headers[i] == "Date" || headers[i] == "ExpiryDate") && val.length() >= 10 && val.find(" 00:00:00") != std::string::npos) {
                        val = val.substr(0, val.find(" 00:00:00"));
                    }
                    json += "\"" + escape_json(headers[i]) + "\":\"" + escape_json(val) + "\"";
                    if (i < bound - 1) json += ",";
                }
                json += "}";
                first = false;
            }
            json += "]";
            res->end(json);
        })
        .get("/api/debug-eod", [](auto* res, auto* req) {
            res->writeHeader("Access-Control-Allow-Origin", "*");
            res->writeHeader("Content-Type", "application/json");
            std::string json = "{ \"bhavcopy_dir\": [";
            bool first = true;
            try {
                for (const auto& entry : std::filesystem::directory_iterator("/home/administrator/nse_v2_microservices/data/BhavCopy")) {
                    if (!first) json += ",";
                    json += "\"" + entry.path().filename().string() + "\"";
                    first = false;
                }
            } catch(std::exception& e) { json += "\"" + std::string(e.what()) + "\""; }
            json += "], \"bse_dir\": [";
            first = true;
            try {
                for (const auto& entry : std::filesystem::directory_iterator("/home/administrator/nse_v2_microservices/data/BhavCopy/BSEfobhavcopy")) {
                    if (!first) json += ",";
                    json += "\"" + entry.path().filename().string() + "\"";
                    first = false;
                }
            } catch(std::exception& e) { json += "\"" + std::string(e.what()) + "\""; }
            json += "] }";
            res->end(json);
        })
        .ws<PerSocketData>("/ke2-source", std::move(source_behavior))
        .ws<PerSocketData>("/*", std::move(behavior))
        .listen("0.0.0.0", port, [port](auto* token)
        {
            if (token)
                std::cout << "[WS] LIVE ON PORT " << port << " (Bound to 0.0.0.0)" << std::endl;
            else
                std::cout << "[WS] FAILED TO START" << std::endl;
        })
        .run();

    }).detach();
}

/* =========================================================
   LEGACY TICK
========================================================= */

static inline double safe_num(double val)
{
    return (std::isnan(val) || std::isinf(val)) ? 0.0 : val;
}

void broadcast_tick(
    const std::string& symbol,
    double ltp,
    double bid1,
    double ask1)
{
    std::ostringstream ss;

    ss << "{"
       << "\"type\":\"tick\","
       << "\"symbol\":\"" << symbol << "\","
       << "\"ltp\":" << safe_num(ltp) << ","
       << "\"bid1\":" << safe_num(bid1) << ","
       << "\"ask1\":" << safe_num(ask1)
       << "}";

    publish(ss.str());
}

/* =========================================================
   ANALYTICS BROADCAST (DESK FORMAT)
========================================================= */

void broadcast_analytics(
    const std::string& symbol,
    double ltp,
    double bid1,
    double ask1,

    double spot,
    double future,
    double basis,

    double iv_mid,
    double iv_bid,
    double iv_ask,

    double delta,
    double gamma,
    double theta,
    double vega)
{
    const auto now_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()
        ).count();

    std::ostringstream ss;

    ss << "{"
       << "\"type\":\"analytics\","
       << "\"symbol\":\"" << symbol << "\","
       << "\"timestamp\":" << now_ms << ",";

    ss << "\"ltp\":" << safe_num(ltp) << ","
       << "\"bid1\":" << safe_num(bid1) << ","
       << "\"ask1\":" << safe_num(ask1) << ",";

    ss << "\"spot\":" << safe_num(spot) << ","
       << "\"future\":" << safe_num(future) << ","
       << "\"basis\":" << safe_num(basis) << ",";

    ss << "\"iv\":" << safe_num(iv_mid) << ","
       << "\"iv_bid\":" << safe_num(iv_bid) << ","
       << "\"iv_ask\":" << safe_num(iv_ask) << ",";

    ss << "\"delta\":" << safe_num(delta) << ","
       << "\"gamma\":" << safe_num(gamma) << ","
       << "\"theta\":" << safe_num(theta) << ","
       << "\"vega\":" << safe_num(vega);

    ss << "}";

    std::string final_msg = ss.str();
    
    {
        std::lock_guard<std::mutex> lock(CACHE_MTX);
        LATEST_STATE_CACHE[symbol] = final_msg;
    }

    publish(final_msg);
}

/* =========================================================
   RAW PIPELINE
========================================================= */

void broadcast_raw(const std::string& msg)
{
    publish(msg);
}

// Raw market source is isolated from legacy ticks and analytics.
void broadcast_market_source(const std::string& msg) {
    if (!APP || !LOOP) return;
    LOOP->defer([msg]() {
        if (APP) APP->publish("market_source", msg, uWS::OpCode::TEXT);
    });
}

bool market_source_active() {
    return MARKET_SOURCE_CLIENTS.load(std::memory_order_relaxed) != 0;
}
