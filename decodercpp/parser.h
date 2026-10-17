// ================================
// parser.h
// ================================
#pragma once
#include <cstdint>
#include <string>
#include <unordered_map>

extern std::unordered_map<uint32_t,std::string> TOKEN_MAP;

void process_packet(const uint8_t* buf, size_t len);
