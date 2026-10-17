// ==============================================
// FILE: src/test_symbol_parser.cpp
// Standalone Test for NSE Symbol Parser
// ==============================================

#include <iostream>
#include <iomanip>
#include <vector>
#include <string>

#include "symbol_parser.h"
#include "option_types.h"

static std::string type_to_string(OptionType t)
{
    switch (t)
    {
        case OptionType::CALL:    return "CALL";
        case OptionType::PUT:     return "PUT";
        case OptionType::FUTURE:  return "FUTURE";
        default:                 return "UNKNOWN";
    }
}

static void print_result(const std::string& sym)
{
    OptionTick tick;

    bool ok = SymbolParser::parse(sym, tick);

    std::cout << "=====================================\n";
    std::cout << "Symbol : " << sym << "\n";

    if (!ok)
    {
        std::cout << "Parse  : FAILED\n";
        return;
    }

    std::cout << "Parse  : SUCCESS\n";
    std::cout << "Underl.: " << tick.underlying << "\n";
    std::cout << "Expiry : " << tick.expiry << "\n";
    std::cout << "Strike : " << std::fixed << std::setprecision(2)
              << tick.strike << "\n";
    std::cout << "Type   : " << type_to_string(tick.type) << "\n";
}

int main()
{
    std::vector<std::string> tests =
    {
        "NIFTY26MAY24500CE",
        "BANKNIFTY26JUN55000PE",
        "RELIANCE26MAY1400CE",
        "HDFCBANK26JUNFUT",
        "BAJAJ-AUTO26JUN9300CE",
        "INFY26MAY1600PE",
        "SBIN26JULFUT",
        "INVALIDSYMBOL"
    };

    std::cout << "\n";
    std::cout << "=====================================\n";
    std::cout << "     NSE Symbol Parser Test Suite\n";
    std::cout << "=====================================\n\n";

    for (const auto& s : tests)
        print_result(s);

    std::cout << "=====================================\n";

    return 0;
}