#pragma once
#include <mutex>
// Shared by health reports, decoder diagnostics and service logs.
inline std::mutex FEED_LOG_MUTEX;
