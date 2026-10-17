#!/usr/bin/env python3

from pathlib import Path
import shutil
import re
import sys


ROOT = Path(".").resolve()

MAIN = ROOT / "src/main.cpp"
DECODER = ROOT / "src/decoder.cpp"
ANALYTICS = ROOT / "src/analytics_engine.cpp"

for p in (MAIN, DECODER, ANALYTICS):
    if not p.exists():
        raise SystemExit(f"ERROR: missing {p}")


# ================================================================
# BACKUPS
# ================================================================

def backup(path):
    dst = path.with_name(path.name + ".before_live_latency_patch")
    shutil.copy2(path, dst)
    print(f"BACKUP_CREATED: {dst}")


backup(MAIN)
backup(DECODER)
backup(ANALYTICS)


def read(path):
    return path.read_text()


def write(path, text):
    path.write_text(text)


def require(text, needle, name):
    if needle not in text:
        raise RuntimeError(
            f"ANCHOR NOT FOUND: {name}\n"
            f"Expected:\n{needle[:500]}"
        )


# ================================================================
# MAIN.CPP
# ================================================================

main = read(MAIN)


# ------------------------------------------------
# Includes
# ------------------------------------------------

include_anchor = '#include <queue>'

require(
    main,
    include_anchor,
    "main.cpp queue include"
)

if '#include <deque>' not in main:
    main = main.replace(
        include_anchor,
        '#include <queue>\n'
        '#include <deque>\n'
        '#include <chrono>\n'
        '#include <cstdint>\n'
    )


# ------------------------------------------------
# Replace old queue declaration
# ------------------------------------------------

old_queue = """std::queue<std::pair<Exchange, std::vector<uint8_t>>> PACKET_QUEUE;
std::mutex QUEUE_MTX;
std::condition_variable QUEUE_CV;
static std::atomic<bool> g_running{true};"""

new_queue = r'''
// ================================================================
// LIVE MARKET PACKET QUEUE
//
// IMPORTANT:
//
// This is intentionally NOT an unbounded FIFO.
//
// If analytics/decode falls behind, retaining old packets is worse
// than dropping stale packets because the application is a LIVE
// market-data consumer.
//
// We therefore use a bounded deque and drop the OLDEST packet when
// the queue reaches capacity.
// ================================================================

struct QueuedPacket {
    Exchange exchange;
    std::vector<uint8_t> data;

    // Local monotonic receive timestamp.
    uint64_t rx_ns{0};
};

static constexpr size_t MAX_PACKET_QUEUE = 2000;

std::deque<QueuedPacket> PACKET_QUEUE;
std::mutex QUEUE_MTX;
std::condition_variable QUEUE_CV;

static std::atomic<bool> g_running{true};

// ================================================================
// QUEUE / LATENCY DIAGNOSTICS
// ================================================================

struct FeedDiagnostics {

    std::atomic<uint64_t> packets_received{0};
    std::atomic<uint64_t> packets_processed{0};
    std::atomic<uint64_t> packets_dropped{0};

    std::atomic<uint64_t> bytes_received{0};

    std::atomic<uint64_t> queue_latency_ns_total{0};
    std::atomic<uint64_t> queue_latency_ns_max{0};

    std::atomic<uint64_t> processing_ns_total{0};
    std::atomic<uint64_t> processing_ns_max{0};

    std::atomic<uint64_t> stale_packets{0};

    std::atomic<uint64_t> max_queue_depth{0};

    std::atomic<uint64_t> diagnostics_ticks{0};

    uint64_t last_report_ns{0};
    uint64_t last_packets{0};
    uint64_t last_processed{0};
    uint64_t last_dropped{0};
};

static FeedDiagnostics FEED_DIAG;


static inline uint64_t steady_ns()
{
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()
        ).count()
    );
}


static inline void atomic_max(
    std::atomic<uint64_t>& target,
    uint64_t value)
{
    uint64_t current = target.load(
        std::memory_order_relaxed
    );

    while (
        value > current &&
        !target.compare_exchange_weak(
            current,
            value,
            std::memory_order_relaxed,
            std::memory_order_relaxed))
    {
    }
}


// ================================================================
// LIVE FEED HEALTH REPORT
// ================================================================

static void print_feed_diagnostics()
{
    const uint64_t now = steady_ns();

    if (FEED_DIAG.last_report_ns == 0) {
        FEED_DIAG.last_report_ns = now;
        FEED_DIAG.last_packets =
            FEED_DIAG.packets_received.load();
        FEED_DIAG.last_processed =
            FEED_DIAG.packets_processed.load();
        FEED_DIAG.last_dropped =
            FEED_DIAG.packets_dropped.load();

        return;
    }

    const uint64_t elapsed =
        now - FEED_DIAG.last_report_ns;

    if (elapsed < 1'000'000'000ULL)
        return;

    const double seconds =
        elapsed / 1'000'000'000.0;

    const uint64_t received =
        FEED_DIAG.packets_received.load();

    const uint64_t processed =
        FEED_DIAG.packets_processed.load();

    const uint64_t dropped =
        FEED_DIAG.packets_dropped.load();

    const uint64_t rx_delta =
        received - FEED_DIAG.last_packets;

    const uint64_t processed_delta =
        processed - FEED_DIAG.last_processed;

    const uint64_t dropped_delta =
        dropped - FEED_DIAG.last_dropped;

    const double rx_rate =
        rx_delta / seconds;

    const double processed_rate =
        processed_delta / seconds;

    const double drop_rate =
        dropped_delta / seconds;

    const uint64_t total_q_ns =
        FEED_DIAG.queue_latency_ns_total.load();

    const uint64_t total_p_ns =
        FEED_DIAG.processing_ns_total.load();

    const double avg_queue_ms =
        processed > 0
            ? (
                static_cast<double>(total_q_ns) /
                static_cast<double>(processed) /
                1'000'000.0
              )
            : 0.0;

    const double avg_processing_ms =
        processed > 0
            ? (
                static_cast<double>(total_p_ns) /
                static_cast<double>(processed) /
                1'000'000.0
              )
            : 0.0;

    const uint64_t max_queue_ns =
        FEED_DIAG.queue_latency_ns_max.load();

    const uint64_t max_processing_ns =
        FEED_DIAG.processing_ns_max.load();

    size_t depth = 0;

    {
        std::lock_guard<std::mutex> lock(QUEUE_MTX);
        depth = PACKET_QUEUE.size();
    }

    std::cout
        << "\n"
        << "============================================================\n"
        << "[LIVE FEED HEALTH]\n"
        << "------------------------------------------------------------\n"
        << "RX rate             : "
        << rx_rate
        << " pkt/s\n"

        << "PROCESS rate        : "
        << processed_rate
        << " pkt/s\n"

        << "DROP rate           : "
        << drop_rate
        << " pkt/s\n"

        << "TOTAL received      : "
        << received
        << "\n"

        << "TOTAL processed     : "
        << processed
        << "\n"

        << "TOTAL dropped       : "
        << dropped
        << "\n"

        << "QUEUE depth         : "
        << depth
        << " / "
        << MAX_PACKET_QUEUE
        << "\n"

        << "QUEUE max depth     : "
        << FEED_DIAG.max_queue_depth.load()
        << "\n"

        << "AVG queue latency   : "
        << avg_queue_ms
        << " ms\n"

        << "MAX queue latency   : "
        << (
            static_cast<double>(max_queue_ns) /
            1'000'000.0
        )
        << " ms\n"

        << "AVG processing      : "
        << avg_processing_ms
        << " ms\n"

        << "MAX processing      : "
        << (
            static_cast<double>(max_processing_ns) /
            1'000'000.0
        )
        << " ms\n"

        << "STALE packets       : "
        << FEED_DIAG.stale_packets.load()
        << "\n"

        << "------------------------------------------------------------\n";

    if (depth > MAX_PACKET_QUEUE * 0.75) {
        std::cout
            << "[WARNING] FIFO queue > 75% capacity\n";
    }

    if (dropped_delta > 0) {
        std::cout
            << "[WARNING] PACKETS DROPPED THIS SECOND: "
            << dropped_delta
            << "\n";
    }

    if (avg_queue_ms > 100.0) {
        std::cout
            << "[CRITICAL] Average queue latency > 100 ms\n";
    }

    if (max_queue_ns > 1'000'000'000ULL) {
        std::cout
            << "[CRITICAL] Queue latency exceeded 1 second\n";
    }

    if (avg_processing_ms > 1.0) {
        std::cout
            << "[WARNING] Average packet processing > 1 ms\n";
    }

    std::cout
        << "============================================================\n"
        << std::endl;

    FEED_DIAG.last_report_ns = now;
    FEED_DIAG.last_packets = received;
    FEED_DIAG.last_processed = processed;
    FEED_DIAG.last_dropped = dropped;
}


// ================================================================
// WORKER THREAD
// ================================================================

static void live_packet_worker()
{
    while (g_running) {

        QueuedPacket item;

        {
            std::unique_lock<std::mutex> lock(QUEUE_MTX);

            QUEUE_CV.wait(
                lock,
                [] {
                    return
                        !g_running ||
                        !PACKET_QUEUE.empty();
                }
            );

            if (
                !g_running &&
                PACKET_QUEUE.empty()
            ) {
                return;
            }

            item =
                std::move(
                    PACKET_QUEUE.front()
                );

            PACKET_QUEUE.pop_front();
        }

        if (item.data.empty())
            continue;

        const uint64_t process_start =
            steady_ns();

        uint64_t queue_latency_ns = 0;

        if (process_start >= item.rx_ns)
            queue_latency_ns =
                process_start - item.rx_ns;

        /*
         * A packet waiting more than 500 ms is already stale
         * for the LIVE simulation.
         */
        if (queue_latency_ns > 500'000'000ULL) {
            FEED_DIAG.stale_packets.fetch_add(
                1,
                std::memory_order_relaxed
            );
        }

        FEED_DIAG.queue_latency_ns_total.fetch_add(
            queue_latency_ns,
            std::memory_order_relaxed
        );

        atomic_max(
            FEED_DIAG.queue_latency_ns_max,
            queue_latency_ns
        );

        process_packet(
            item.exchange,
            item.data.data(),
            item.data.size()
        );

        const uint64_t process_end =
            steady_ns();

        const uint64_t processing_ns =
            process_end - process_start;

        FEED_DIAG.processing_ns_total.fetch_add(
            processing_ns,
            std::memory_order_relaxed
        );

        atomic_max(
            FEED_DIAG.processing_ns_max,
            processing_ns
        );

        FEED_DIAG.packets_processed.fetch_add(
            1,
            std::memory_order_relaxed
        );

        print_feed_diagnostics();
    }
}
'''

require(
    main,
    old_queue,
    "old PACKET_QUEUE declaration"
)

main = main.replace(
    old_queue,
    new_queue,
    1
)


# ------------------------------------------------
# Replace worker_thread()
# ------------------------------------------------

worker_pattern = re.compile(
    r"void worker_thread\(\)\s*\{.*?\n\}",
    re.S
)

m = worker_pattern.search(main)

if not m:
    raise RuntimeError(
        "Could not locate worker_thread()"
    )

main = (
    main[:m.start()]
    +
    """void worker_thread()
{
    live_packet_worker();
}"""
    +
    main[m.end():]
)


# ------------------------------------------------
# Replace enqueue block
# ------------------------------------------------

old_enqueue = """std::vector<uint8_t> pkt(buf.begin(), buf.begin() + n);
            {
                std::lock_guard<std::mutex> lock(QUEUE_MTX);
                PACKET_QUEUE.emplace(exchange, std::move(pkt));
            }
            QUEUE_CV.notify_one();"""

new_enqueue = """QueuedPacket pkt;

            pkt.exchange = exchange;

            pkt.data.assign(
                buf.begin(),
                buf.begin() + n
            );

            pkt.rx_ns = steady_ns();

            FEED_DIAG.packets_received.fetch_add(
                1,
                std::memory_order_relaxed
            );

            FEED_DIAG.bytes_received.fetch_add(
                static_cast<uint64_t>(n),
                std::memory_order_relaxed
            );

            {
                std::lock_guard<std::mutex> lock(QUEUE_MTX);

                /*
                 * LIVE MARKET POLICY:
                 *
                 * Never allow old packets to accumulate forever.
                 *
                 * Drop oldest first when full.
                 */
                if (
                    PACKET_QUEUE.size()
                    >= MAX_PACKET_QUEUE
                ) {

                    PACKET_QUEUE.pop_front();

                    FEED_DIAG.packets_dropped.fetch_add(
                        1,
                        std::memory_order_relaxed
                    );
                }

                PACKET_QUEUE.emplace_back(
                    std::move(pkt)
                );

                atomic_max(
                    FEED_DIAG.max_queue_depth,
                    static_cast<uint64_t>(
                        PACKET_QUEUE.size()
                    )
                );
            }

            QUEUE_CV.notify_one();"""

require(
    main,
    old_enqueue,
    "multicast queue enqueue block"
)

main = main.replace(
    old_enqueue,
    new_enqueue,
    1
)

write(MAIN, main)


# ================================================================
# DECODER.CPP
# ================================================================

decoder = read(DECODER)

# Add receive timestamp around process_tick.

old_process = """std::string msg = Analytics::process_tick(symbol, ltp, best_bid, best_ask);"""

new_process = """const uint64_t analytics_rx_ns = steady_ns();

            std::string msg =
                Analytics::process_tick(
                    symbol,
                    ltp,
                    best_bid,
                    best_ask
                );

            const uint64_t analytics_done_ns =
                steady_ns();

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
            }"""

require(
    decoder,
    old_process,
    "Analytics::process_tick call"
)

decoder = decoder.replace(
    old_process,
    new_process,
    1
)


# ------------------------------------------------
# Make decoder diagnostics less noisy
# ------------------------------------------------

write(DECODER, decoder)


# ================================================================
# ANALYTICS ENGINE
# ================================================================

analytics = read(ANALYTICS)


# ------------------------------------------------
# Add quote freshness state
# ------------------------------------------------

anchor = """static std::unordered_map<std::string, double> SYN_FUT;"""

require(
    analytics,
    anchor,
    "SYN_FUT declaration"
)

freshness_block = r'''
// ================================================================
// LIVE QUOTE FRESHNESS
// ================================================================
//
// OPT_LTP is the price cache.
//
// These timestamps are intentionally separate so that a cached
// option price can never silently masquerade as a current quote.
//
// All timestamps are local monotonic receive timestamps.
// ================================================================

static std::unordered_map<std::string, uint64_t>
    OPT_RX_NS;

static std::unordered_map<std::string, uint64_t>
    SYN_FUT_RX_NS;

static constexpr uint64_t
    LIVE_OPTION_MAX_AGE_NS =
        500'000'000ULL; // 500 ms

static constexpr uint64_t
    LIVE_PAIR_MAX_SKEW_NS =
        100'000'000ULL; // 100 ms

static inline bool live_quote_fresh(
    const std::string& key,
    uint64_t now_ns)
{
    auto it = OPT_RX_NS.find(key);

    if (it == OPT_RX_NS.end())
        return false;

    if (it->second == 0)
        return false;

    if (now_ns < it->second)
        return false;

    return
        (now_ns - it->second)
        <= LIVE_OPTION_MAX_AGE_NS;
}


static inline bool live_option_pair_fresh(
    const std::string& ce_key,
    const std::string& pe_key,
    uint64_t now_ns)
{
    auto ce_it = OPT_RX_NS.find(ce_key);
    auto pe_it = OPT_RX_NS.find(pe_key);

    if (
        ce_it == OPT_RX_NS.end() ||
        pe_it == OPT_RX_NS.end()
    ) {
        return false;
    }

    if (
        ce_it->second == 0 ||
        pe_it->second == 0
    ) {
        return false;
    }

    if (!live_quote_fresh(ce_key, now_ns))
        return false;

    if (!live_quote_fresh(pe_key, now_ns))
        return false;

    const uint64_t diff =
        ce_it->second > pe_it->second
            ? ce_it->second - pe_it->second
            : pe_it->second - ce_it->second;

    return diff <= LIVE_PAIR_MAX_SKEW_NS;
}


static inline bool live_syn_fut_fresh(
    const std::string& key,
    uint64_t now_ns)
{
    auto it = SYN_FUT_RX_NS.find(key);

    if (it == SYN_FUT_RX_NS.end())
        return false;

    if (it->second == 0)
        return false;

    if (now_ns < it->second)
        return false;

    return
        (now_ns - it->second)
        <= LIVE_OPTION_MAX_AGE_NS;
}
'''

analytics = analytics.replace(
    anchor,
    anchor + "\n" + freshness_block,
    1
)


# ------------------------------------------------
# Timestamp option LTP updates
# ------------------------------------------------

# Find assignments of the form:
#
# OPT_LTP[key] = price;
#
# We don't blindly rewrite every occurrence. We require
# the exact common assignment.
# ------------------------------------------------

opt_assign = re.compile(
    r'(?P<indent>\s*)OPT_LTP\[(?P<key>[^\]]+)\]\s*=\s*price\s*;'
)

matches = list(opt_assign.finditer(analytics))

if not matches:
    print(
        "WARNING: OPT_LTP[key] = price; was not found."
    )
    print(
        "Freshness state was added but option timestamp assignment "
        "must be inserted manually."
    )
else:
    # Replace only the first actual live assignment.
    m = matches[0]

    indent = m.group("indent")
    key_expr = m.group("key")

    replacement = (
        f"{indent}OPT_LTP[{key_expr}] = price;\n"
        f"{indent}OPT_RX_NS[{key_expr}] = "
        f"static_cast<uint64_t>(now() * 1'000'000'000.0);"
    )

    analytics = (
        analytics[:m.start()]
        + replacement
        + analytics[m.end():]
    )


# ------------------------------------------------
# Timestamp synthetic future update
# ------------------------------------------------

old_syn = """SYN_FUT[exp_key] = syn_mid;"""

new_syn = """SYN_FUT[exp_key] = syn_mid;

                    SYN_FUT_RX_NS[exp_key] =
                        static_cast<uint64_t>(
                            now() * 1'000'000'000.0
                        );"""

require(
    analytics,
    old_syn,
    "SYN_FUT assignment"
)

analytics = analytics.replace(
    old_syn,
    new_syn,
    1
)


# ------------------------------------------------
# Remove cross-expiry synthetic fallback
# ------------------------------------------------

old_cross_expiry = """// 3. Chained Interpolation: Fallback to the closest
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
            if (best_syn > 0) return best_syn;"""

if old_cross_expiry in analytics:

    replacement = """/*
             * LIVE SIMULATION POLICY:
             *
             * NEVER borrow a synthetic future from another expiry.
             *
             * A stale synthetic from a different expiry can make the
             * simulation appear live while actually using historical
             * state.
             */
            return 0.0;"""

    analytics = analytics.replace(
        old_cross_expiry,
        replacement,
        1
    )

else:
    print(
        "WARNING: cross-expiry synthetic fallback block "
        "was not found exactly."
    )


# ------------------------------------------------
# Add freshness check to exact synthetic future
# ------------------------------------------------

old_exact_syn = """if (SYN_FUT.count(key) && SYN_FUT[key] > 0)
                return SYN_FUT[key];"""

new_exact_syn = """{
                const uint64_t live_now_ns =
                    static_cast<uint64_t>(
                        now() * 1'000'000'000.0
                    );

                if (
                    SYN_FUT.count(key) &&
                    SYN_FUT[key] > 0 &&
                    live_syn_fut_fresh(
                        key,
                        live_now_ns
                    )
                ) {
                    return SYN_FUT[key];
                }
            }"""

require(
    analytics,
    old_exact_syn,
    "exact synthetic future lookup"
)

analytics = analytics.replace(
    old_exact_syn,
    new_exact_syn,
    1
)


# ------------------------------------------------
# Remove live ATM use of stale options
# ------------------------------------------------

old_atm_candidate = """if (OPT_LTP[ce_k] > 0 && OPT_LTP[pe_k] > 0) {
                                      min_diff = diff;
                                      atm_k = strike;
                                  }"""

new_atm_candidate = """{
                                      const uint64_t live_now_ns =
                                          static_cast<uint64_t>(
                                              now() *
                                              1'000'000'000.0
                                          );

                                      if (
                                          OPT_LTP[ce_k] > 0 &&
                                          OPT_LTP[pe_k] > 0 &&
                                          live_option_pair_fresh(
                                              ce_k,
                                              pe_k,
                                              live_now_ns
                                          )
                                      ) {
                                          min_diff = diff;
                                          atm_k = strike;
                                      }
                                  }"""

require(
    analytics,
    old_atm_candidate,
    "ATM candidate selection"
)

analytics = analytics.replace(
    old_atm_candidate,
    new_atm_candidate,
    1
)


# ------------------------------------------------
# Add diagnostic around synthetic parity
# ------------------------------------------------

parity_anchor = """if (valid_syn) {"""

require(
    analytics,
    parity_anchor,
    "valid synthetic block"
)

parity_diag = r'''
                    /*
                     * LIVE SYNTHETIC PARITY DIAGNOSTIC
                     *
                     * The synthetic should be constructed from a
                     * coherent/fresh CE + PE pair.
                     */
                    if (
                        t.underlying == "NIFTY" &&
                        atm_k > 0
                    ) {
                        const std::string ce_diag_key =
                            exp_key +
                            "|" +
                            std::to_string(
                                static_cast<int>(atm_k)
                            ) +
                            "|CE";

                        const std::string pe_diag_key =
                            exp_key +
                            "|" +
                            std::to_string(
                                static_cast<int>(atm_k)
                            ) +
                            "|PE";

                        const uint64_t diag_now_ns =
                            static_cast<uint64_t>(
                                now() *
                                1'000'000'000.0
                            );

                        if (
                            OPT_LTP.count(ce_diag_key) &&
                            OPT_LTP.count(pe_diag_key) &&
                            live_option_pair_fresh(
                                ce_diag_key,
                                pe_diag_key,
                                diag_now_ns
                            )
                        ) {
                            const double ce_live =
                                OPT_LTP[ce_diag_key];

                            const double pe_live =
                                OPT_LTP[pe_diag_key];

                            const double parity_syn =
                                static_cast<double>(atm_k) +
                                ce_live -
                                pe_live;

                            const double parity_diff =
                                syn_mid -
                                parity_syn;

                            static int parity_diag_count = 0;

                            if (
                                std::abs(parity_diff) > 1.0 &&
                                parity_diag_count < 100
                            ) {
                                ++parity_diag_count;

                                std::cout
                                    << "[LIVE SYN PARITY WARNING]"
                                    << " expiry=" << t.expiry
                                    << " atm=" << atm_k
                                    << " syn=" << syn_mid
                                    << " ce=" << ce_live
                                    << " pe=" << pe_live
                                    << " parity="
                                    << parity_syn
                                    << " diff="
                                    << parity_diff
                                    << std::endl;
                            }
                        }
                    }

'''

analytics = analytics.replace(
    parity_anchor,
    parity_diag + parity_anchor,
    1
)


write(ANALYTICS, analytics)


# ================================================================
# FINAL CHECKS
# ================================================================

print()
print("=" * 72)
print("LIVE FEED LATENCY PATCH APPLIED")
print("=" * 72)
print()
print("BACKUPS:")
print("  src/main.cpp.before_live_latency_patch")
print("  src/decoder.cpp.before_live_latency_patch")
print("  src/analytics_engine.cpp.before_live_latency_patch")
print()
print("MAIN CHANGES:")
print("  1. Unbounded FIFO -> bounded deque")
print("  2. Oldest packet dropped when queue is full")
print("  3. Packet receive timestamp")
print("  4. Queue latency measurement")
print("  5. Processing latency measurement")
print("  6. Packet drop counter")
print("  7. Queue depth / max depth")
print("  8. Stale packet counter")
print("  9. RX / PROCESS / DROP rates")
print(" 10. Automatic 1-second feed health report")
print()
print("ANALYTICS CHANGES:")
print(" 11. Option quote freshness state")
print(" 12. CE/PE pair freshness")
print(" 13. CE/PE timestamp skew detection")
print(" 14. Synthetic freshness")
print(" 15. Cross-expiry synthetic fallback removed")
print(" 16. Live ATM requires fresh CE + PE")
print(" 17. Synthetic parity diagnostic")
print()
print("=" * 72)