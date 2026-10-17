// ==============================================
// FILE: include/symbol_parser.h
// NSE Derivative Symbol Parser Header
// ==============================================

#pragma once

#include <string>
#include "option_types.h"

namespace SymbolParser
{
    // ------------------------------------------
    // Parse NSE derivative symbol into OptionTick
    //
    // Examples:
    //   NIFTY26MAY24500CE
    //   BANKNIFTY26JUN55000PE
    //   RELIANCE26MAY1400CE
    //   HDFCBANK26JUNFUT
    //
    // Returns:
    //   true  = parsed successfully
    //   false = unsupported / unknown format
    // ------------------------------------------
    bool parse(
        const std::string& raw_symbol,
        OptionTick& out
    );
}