#pragma once

#include <unordered_map>
#include <string>
#include <cstdint>
#include <vector>
#include <ctime>

#include "option_types.h"

enum class Exchange { NSE, BSE, BSE_EQ };

struct Contract {
    uint32_t xxToken;
    uint32_t marketToken;
    Exchange exchange;
    OptionType opType;
    std::string symbol;
    std::string expiry;
    std::string expiryShort;
    double strike;
    std::string opStr;
    double dte;
    time_t expiryDate;
    double eodPrice;
};

class ContractLoader {
public:
    bool loadNseContracts(const std::string& filepath);
    bool loadBseContracts(const std::string& filepath);
    
    std::string findLatestBhavcopy(const std::string& dirPath, bool isBSE);
    bool loadNseBhavcopy(const std::string& dirPath);
    bool loadBseBhavcopy(const std::string& dirPath);
    
    Contract* getByMarketToken(uint32_t token);
    Contract* getByXxToken(uint32_t xxToken);
    std::vector<Contract*> getBySymbol(const std::string& symbol);
    std::vector<Contract*> getByExpiry(const std::string& symbol, const std::string& expiry);
    
    // NEW: Get all available symbols dynamically
    std::vector<std::string> getAllSymbols();
    
    static double calculateDTE(const std::string& expiryDateStr);
    static time_t parseExpiryDate(const std::string& expiryDateStr);
    
private:
    std::unordered_map<uint32_t, Contract> marketTokenMap_;
    std::unordered_map<uint32_t, Contract> xxTokenMap_;
    std::unordered_map<std::string, std::vector<Contract*>> symbolMap_;
    std::unordered_map<std::string, std::vector<Contract*>> expiryMap_;
};

extern ContractLoader g_contractLoader;