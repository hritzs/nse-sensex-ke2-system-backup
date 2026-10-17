#pragma once

#include <cstdint>
#include <cstddef>
#include "contract_loader.h" // For Exchange enum

void process_packet(Exchange exchange, const uint8_t* buf, size_t len);