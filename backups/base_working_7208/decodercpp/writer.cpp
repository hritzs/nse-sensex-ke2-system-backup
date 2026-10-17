#include "writer.h"
#include "parser.h"
#include "utils.h"

#include <filesystem>
#include <fstream>
#include <iomanip>
#include <thread>

static void write_csv(const std::string& path, const std::vector<TickData>& rows) {
    std::string tmp = path + ".tmp";
    std::ofstream f(tmp, std::ios::trunc);
    if (!f) return;
    f << "token,symbol,ltp,volume,last_trade_qty,last_trade_time,"
         "market_type,open,high,low,close,total_buy_qty,total_sell_qty,ts_ms\n";
    f << std::fixed << std::setprecision(2);
    for (const auto& x : rows) {
        f << x.token      << ','
          << x.symbol     << ','
          << x.ltp        << ','
          << x.volume     << ','
          << x.last_trade_qty  << ','
          << x.last_trade_time << ','
          << x.market_type     << ','
          << x.open       << ','
          << x.high       << ','
          << x.low        << ','
          << x.close      << ','
          << x.total_buy_qty  << ','
          << x.total_sell_qty << ','
          << x.ts_ms      << '\n';
    }
    f.close();
    std::error_code ec;
    std::filesystem::rename(tmp, path, ec);
    if (ec) {
        std::filesystem::remove(path, ec);
        std::filesystem::rename(tmp, path, ec);
    }
}

static void write_json(const std::string& path, const std::vector<TickData>& rows) {
    std::string tmp = path + ".tmp";
    std::ofstream f(tmp, std::ios::trunc);
    if (!f) return;
    f << std::fixed << std::setprecision(2);
    f << "{\"ts_ms\":" << now_ms()
      << ",\"count\":"  << rows.size()
      << ",\"ticks\":[";
    bool first = true;
    for (const auto& x : rows) {
        if (!first) f << ',';
        first = false;
        f << "{"
          << "\"token\":"          << x.token          << ","
          << "\"symbol\":\""       << json_escape(x.symbol) << "\","
          << "\"ltp\":"            << x.ltp             << ","
          << "\"volume\":"         << x.volume          << ","
          << "\"last_trade_qty\":" << x.last_trade_qty  << ","
          << "\"last_trade_time\":"<< x.last_trade_time << ","
          << "\"market_type\":"    << x.market_type     << ","
          << "\"open\":"           << x.open            << ","
          << "\"high\":"           << x.high            << ","
          << "\"low\":"            << x.low             << ","
          << "\"close\":"          << x.close           << ","
          << "\"total_buy_qty\":"  << x.total_buy_qty   << ","
          << "\"total_sell_qty\":" << x.total_sell_qty  << ","
          << "\"ts_ms\":"          << x.ts_ms
          << "}";
    }
    f << "]}";
    f.close();
    std::error_code ec;
    std::filesystem::rename(tmp, path, ec);
    if (ec) {
        std::filesystem::remove(path, ec);
        std::filesystem::rename(tmp, path, ec);
    }
}

std::thread start_writer(std::atomic<bool>& running,
                         const std::string& out_dir,
                         int json_ms) {
    return std::thread([&running, out_dir, json_ms]() {
        std::filesystem::create_directories(out_dir);
        const std::string csv_path  = out_dir + "/live.csv";
        const std::string json_path = out_dir + "/live.json";
        while (running.load()) {
            auto rows = snapshot_ticks();
            write_csv (csv_path,  rows);
            write_json(json_path, rows);
            std::this_thread::sleep_for(std::chrono::milliseconds(json_ms));
        }
        auto rows = snapshot_ticks();
        write_csv (csv_path,  rows);
        write_json(json_path, rows);
    });
}
