// ================================
// decompressor.h
// ================================
#pragma once
#include <vector>
#include <cstdint>

bool decompress_lzo(const uint8_t* in,size_t inlen,std::vector<uint8_t>& out);