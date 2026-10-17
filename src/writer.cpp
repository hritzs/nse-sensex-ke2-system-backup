#include "writer.h"

#include <unordered_map>
#include <fstream>
#include <mutex>
#include <filesystem>
#include <iostream>
#include <iomanip>

static std::unordered_map<std::string, TickData> BOOK;
static std::mutex MTX;

// ------------------------------------
// Safe JSON escaping
// ------------------------------------
static std::string escape_json(const std::string& s)
{
    std::string out;
    out.reserve(s.size());

    for (char c : s)
    {
        switch (c)
        {
            case '\"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:   out += c;      break;
        }
    }

    return out;
}

// ------------------------------------
// Main Tick Writer
// ------------------------------------
void update_tick(const TickData& tick)
{
    std::lock_guard<std::mutex> lock(MTX);

    // update in-memory latest book
    BOOK[tick.symbol] = tick;

    // ensure folder exists
    std::filesystem::create_directories("data");

    // write atomically
    std::ofstream out("data/ticks.json.tmp", std::ios::trunc);

    if (!out.is_open())
        return;

    out << "[\n";

    bool first = true;

    for (const auto& kv : BOOK)
    {
        const TickData& t = kv.second;

        if (!first)
            out << ",\n";

        first = false;

        out << "  {\n";
        out << "    \"symbol\": \"" << escape_json(t.symbol) << "\",\n";
        out << "    \"ltp\": "     << std::fixed << std::setprecision(2) << t.ltp << ",\n";
        out << "    \"bid1\": "    << std::fixed << std::setprecision(2) << t.bid1 << ",\n";
        out << "    \"ask1\": "    << std::fixed << std::setprecision(2) << t.ask1 << ",\n";
        out << "    \"bidqty1\": " << t.bidqty1 << ",\n";
        out << "    \"askqty1\": " << t.askqty1 << "\n";
        out << "  }";
    }

    out << "\n]\n";
    out.close();

    // replace old file
    std::filesystem::rename("data/ticks.json.tmp", "data/ticks.json");
}