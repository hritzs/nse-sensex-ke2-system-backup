// ==============================================
// FILE: src/symbol_parser.cpp
// Parse NSE Derivative Symbols into Structured Data
// Examples:
//   NIFTY26MAY24500CE
//   BANKNIFTY26JUN55000PE
//   RELIANCE26MAY1400CE
//   HDFCBANK26JUNFUT
// ==============================================

#include "symbol_parser.h"
#include "option_types.h"

#include <regex>
#include <string>
#include <algorithm>

namespace SymbolParser
{
    // ------------------------------------------
    // Helpers
    // ------------------------------------------
    static std::string upper_copy(std::string s)
    {
        std::transform(s.begin(), s.end(), s.begin(),
            [](unsigned char c){ return std::toupper(c); });
        return s;
    }

    // ------------------------------------------
    // Main Parse Function
    // ------------------------------------------
    bool parse(const std::string& raw_symbol, OptionTick& out)
    {
        out = OptionTick{};
        out.symbol = raw_symbol;

        std::string sym = upper_copy(raw_symbol);

        // --------------------------------------
        // FUT Pattern
        // Example:
        // RELIANCE26MAYFUT
        // NIFTY26JUNFUT
        // --------------------------------------
        {
            static const std::regex fut_re(
                R"(^([A-Z\-]+)(\d{2}[A-Z]{3}\d{0,2})FUT$)"
            );

            std::smatch m;

            if (std::regex_match(sym, m, fut_re))
            {
                out.underlying = m[1].str();
                out.expiry     = m[2].str();
                out.type       = OptionType::FUTURE;
                out.strike     = 0.0;

                return true;
            }
        }

        // --------------------------------------
        // OPTION Pattern
        // Example:
        // NIFTY26MAY24500CE
        // BANKNIFTY26JUN55000PE
        // RELIANCE26MAY1400CE
        // BAJAJ-AUTO26JUN9300CE
        // --------------------------------------
        {
            static const std::regex opt_re(
                R"(^([A-Z\-]+)(\d{2}[A-Z]{3}\d{0,2})(\d+(?:\.\d+)?)(CE|PE)$)"
            );

            std::smatch m;

            if (std::regex_match(sym, m, opt_re))
            {
                out.underlying = m[1].str();
                out.expiry     = m[2].str();
                out.strike     = std::stod(m[3].str());

                std::string cp = m[4].str();

                if (cp == "CE")
                    out.type = OptionType::CALL;
                else
                    out.type = OptionType::PUT;

                return true;
            }
        }

        // --------------------------------------
        // Unknown / Unsupported
        // --------------------------------------
        out.type = OptionType::UNKNOWN;
        return false;
    }
}