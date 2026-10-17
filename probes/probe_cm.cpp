#include <arpa/inet.h>
#include <netinet/in.h>
#include <unistd.h>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <chrono>
#include <iomanip>
#include <iostream>
#include <map>
#include <vector>
#include <lzo/lzo1z.h>

namespace {

constexpr const char* MCAST_IP = "233.1.2.5";
constexpr int MCAST_PORT = 34074;
constexpr size_t MAX_PACKET = 65536;
constexpr size_t MAX_DECOMP = 65536;

volatile std::sig_atomic_t g_running = 1;
void sig_handler(int) { g_running = 0; }

uint64_t g_udp_packets = 0;
uint64_t g_decomp_ok = 0;
uint64_t g_decomp_fail = 0;
uint64_t g_comp_zero = 0;
std::map<uint16_t, uint64_t> g_tx_hist;
std::map<size_t, uint64_t> g_udp_len_hist;
std::map<uint8_t, uint64_t> g_market_type_hist;
std::map<uint16_t, int> g_dump_budget = {
    {7201, 3}, {7207, 3}, {7208, 5}, {8207, 3}, {18703, 3},
    {7214, 2}, {7215, 2}, {7210, 2}
};

inline uint16_t be16(const uint8_t* p) {
    return (uint16_t(p[0]) << 8) | uint16_t(p[1]);
}
inline uint32_t be32(const uint8_t* p) {
    return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) |
           (uint32_t(p[2]) << 8) | uint32_t(p[3]);
}

void hex_dump(const uint8_t* b, size_t len, size_t max_bytes) {
    size_t n = std::min(len, max_bytes);
    for (size_t i = 0; i < n; ++i) {
        std::cerr << std::hex << std::setw(2) << std::setfill('0')
                   << unsigned(b[i]) << " ";
        if ((i + 1) % 16 == 0) std::cerr << "\n";
    }
    std::cerr << std::dec << std::setfill(' ') << "\n";
}

std::string ascii_name(const uint8_t* p, size_t n) {
    std::string s;
    for (size_t i = 0; i < n; ++i) {
        char c = static_cast<char>(p[i]);
        if (c == 0) break;
        s.push_back(c);
    }
    while (!s.empty() && (s.back() == ' ')) s.pop_back();
    return s;
}

void analyze_7208(const uint8_t* b, size_t blen) {
    if (blen < 42 + 2) { std::cerr << "[7208] too short blen=" << blen << "\n"; return; }
    uint16_t no_records = be16(b + 40);
    std::cerr << "[7208] NoOfRecords=" << no_records << " blen=" << blen << "\n";
    size_t max_recs = no_records > 2 ? 2 : no_records;
    for (size_t r = 0; r < max_recs; ++r) {
        size_t base = 42 + r * 262;
        if (base + 262 > blen) { std::cerr << "  record[" << r << "] out of range\n"; continue; }
        uint32_t token = be32(b + base + 0);
        int32_t  ltp   = static_cast<int32_t>(be32(b + base + 16));
        int32_t  close = static_cast<int32_t>(be32(b + base + 242));
        int32_t  open  = static_cast<int32_t>(be32(b + base + 246));
        int32_t  high  = static_cast<int32_t>(be32(b + base + 250));
        int32_t  low   = static_cast<int32_t>(be32(b + base + 254));
        int32_t  indic = static_cast<int32_t>(be32(b + base + 258));
        std::cerr << "  record[" << r << "] token=" << token
                   << " LTP=" << (ltp / 100.0)
                   << " Close=" << (close / 100.0)
                   << " Open=" << (open / 100.0)
                   << " High=" << (high / 100.0)
                   << " Low=" << (low / 100.0)
                   << " IndicativeClose_raw=" << indic
                   << " IndicativeClose=" << (indic / 100.0)
                   << "\n";
    }
}

void analyze_index(const uint8_t* b, size_t blen, const char* label, size_t value_off) {
    if (blen < 42 + 2) { std::cerr << "[" << label << "] too short\n"; return; }
    uint16_t no_records = be16(b + 40);
    std::cerr << "[" << label << "] NoOfRecords=" << no_records << "\n";
    size_t max_recs = no_records > 6 ? 6 : no_records;
    for (size_t r = 0; r < max_recs; ++r) {
        size_t base = 42 + r * 71;
        if (base + 71 > blen) { std::cerr << "  index[" << r << "] out of range\n"; continue; }
        std::string name = ascii_name(b + base, 21);
        int32_t val = static_cast<int32_t>(be32(b + base + value_off));
        int32_t closing = static_cast<int32_t>(be32(b + base + 37));
        std::cerr << "  index[" << r << "] name=" << name
                   << " value=" << (val / 100.0)
                   << " closingIndex=" << (closing / 100.0) << "\n";
    }
}

void analyze_18703(const uint8_t* b, size_t blen) {
    if (blen < 42 + 2) return;
    uint16_t no_records = be16(b + 40);
    std::cerr << "[18703] NoOfRecords=" << no_records << "\n";
    size_t max_recs = no_records > 5 ? 5 : no_records;
    for (size_t r = 0; r < max_recs; ++r) {
        size_t base = 42 + r * 18;
        if (base + 18 > blen) continue;
        uint32_t token = be32(b + base + 0);
        uint16_t mtype = be16(b + base + 4);
        int32_t fillp = static_cast<int32_t>(be32(b + base + 6));
        int32_t fillv = static_cast<int32_t>(be32(b + base + 10));
        int32_t idxv  = static_cast<int32_t>(be32(b + base + 14));
        std::cerr << "  ticker[" << r << "] token=" << token
                   << " marketType=" << mtype
                   << " fillPrice=" << (fillp / 100.0)
                   << " fillVol=" << fillv
                   << " marketIndexValue=" << (idxv / 100.0) << "\n";
    }
}

void handle_decoded(const std::vector<uint8_t>& decoded) {
    if (decoded.size() <= 18) return;
    const uint8_t* raw = decoded.data();
    uint8_t market_type = raw[0];
    ++g_market_type_hist[market_type];
    const uint8_t* b = raw + 8;
    size_t blen = decoded.size() - 8;
    if (blen < 12) return;
    uint16_t tx = be16(b + 10);
    ++g_tx_hist[tx];

    auto it = g_dump_budget.find(tx);
    if (it != g_dump_budget.end() && it->second > 0) {
        --it->second;
        std::cerr << "\n==== TX " << tx << " sample (marketType=" << int(market_type)
                   << " decodedLen=" << decoded.size() << ") ====\n";
        hex_dump(b, blen, 320);
        if (tx == 7208) analyze_7208(b, blen);
        else if (tx == 7207) analyze_index(b, blen, "7207", 21);
        else if (tx == 8207) analyze_index(b, blen, "8207", 21);
        else if (tx == 18703) analyze_18703(b, blen);
        std::cerr << "====\n";
    }
}

void process_udp(const uint8_t* udp, size_t n) {
    ++g_udp_packets;
    ++g_udp_len_hist[n];
    if (n < 4) return;
    uint16_t packets = be16(udp + 2);
    if (packets == 0 || packets > 50) return;
    size_t pos = 4;
    for (uint16_t i = 0; i < packets; ++i) {
        if (pos + 2 > n) break;
        uint16_t comp = be16(udp + pos);
        pos += 2;
        if (comp == 0) { ++g_comp_zero; break; }
        if (comp > 8192) break;
        if (pos + comp > n) break;

        std::vector<uint8_t> decoded(MAX_DECOMP);
        lzo_uint out_len = static_cast<lzo_uint>(decoded.size());
        int rc = lzo1z_decompress(udp + pos, static_cast<lzo_uint>(comp),
                                   decoded.data(), &out_len, nullptr);
        pos += comp;
        if (rc != LZO_E_OK) { ++g_decomp_fail; continue; }
        decoded.resize(static_cast<size_t>(out_len));
        ++g_decomp_ok;
        handle_decoded(decoded);
    }
}

} // namespace

int main(int argc, char** argv) {
    int duration_sec = (argc > 1) ? std::atoi(argv[1]) : 20;
    std::signal(SIGINT, sig_handler);
    std::signal(SIGTERM, sig_handler);

    if (lzo_init() != LZO_E_OK) {
        std::cerr << "lzo_init failed\n";
        return 1;
    }

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) { std::cerr << "socket() failed: " << strerror(errno) << "\n"; return 1; }
    int reuse = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    timeval tv{1, 0};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    sockaddr_in local{};
    local.sin_family = AF_INET;
    local.sin_port = htons(MCAST_PORT);
    local.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(sock, reinterpret_cast<sockaddr*>(&local), sizeof(local)) < 0) {
        std::cerr << "bind failed: " << strerror(errno) << "\n";
        close(sock);
        return 1;
    }

    ip_mreq mreq{};
    mreq.imr_multiaddr.s_addr = inet_addr(MCAST_IP);
    mreq.imr_interface.s_addr = inet_addr("0.0.0.0");
    if (setsockopt(sock, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq)) < 0) {
        std::cerr << "multicast join failed: " << strerror(errno) << "\n";
        close(sock);
        return 1;
    }

    std::cerr << "[PROBE] Joined " << MCAST_IP << ":" << MCAST_PORT
               << " for " << duration_sec << "s (read-only, no other services touched)\n";

    std::vector<uint8_t> buf(MAX_PACKET);
    auto start = std::chrono::steady_clock::now();
    while (g_running) {
        auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::steady_clock::now() - start).count();
        if (elapsed >= duration_sec) break;
        ssize_t n = recv(sock, buf.data(), buf.size(), 0);
        if (n <= 0) continue;
        process_udp(buf.data(), static_cast<size_t>(n));
    }
    close(sock);

    std::cerr << "\n============ SUMMARY ============\n";
    std::cerr << "udp_packets=" << g_udp_packets << "\n";
    std::cerr << "decomp_ok=" << g_decomp_ok << "\n";
    std::cerr << "decomp_fail=" << g_decomp_fail << "\n";
    std::cerr << "comp_len_zero_seen=" << g_comp_zero << "\n";
    std::cerr << "market_type_histogram:\n";
    for (auto& kv : g_market_type_hist)
        std::cerr << "  marketType=" << int(kv.first) << " count=" << kv.second << "\n";
    std::cerr << "udp_packet_length_histogram (top by count):\n";
    for (auto& kv : g_udp_len_hist)
        std::cerr << "  len=" << kv.first << " count=" << kv.second << "\n";
    std::cerr << "transaction_code_histogram:\n";
    for (auto& kv : g_tx_hist)
        std::cerr << "  tx=" << kv.first << " count=" << kv.second << "\n";
    std::cerr << "==================================\n";
    return 0;
}
