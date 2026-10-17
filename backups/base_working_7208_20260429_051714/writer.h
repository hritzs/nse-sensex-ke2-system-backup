#pragma once
#include <atomic>
#include <string>
#include <thread>

std::thread start_writer(std::atomic<bool>& running,
                         const std::string& out_dir,
                         int json_ms);
