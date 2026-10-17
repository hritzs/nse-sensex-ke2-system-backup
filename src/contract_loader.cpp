#include "contract_loader.h"
#include <iostream>
#include <sstream>
#include <algorithm>
#include <iomanip>
#include <cctype>
#include <fstream>
#include <filesystem>

ContractLoader g_contractLoader;

// =====================================================
// CSV PARSER - Handles quoted fields with commas
// =====================================================
static std::vector<std::string> parseCSVLine(const std::string& line) {
    std::vector<std::string> cols;
    std::string cell;
    bool inQuotes = false;
    
    // Helper lambda to strip spaces and \r before pushing to columns
    auto add_cell = [&]() {
        size_t start = cell.find_first_not_of(" \t\r\n");
        if (start == std::string::npos) {
            cols.push_back("");
        } else {
            size_t end = cell.find_last_not_of(" \t\r\n");
            cols.push_back(cell.substr(start, end - start + 1));
        }
        cell.clear();
    };

    for (size_t i = 0; i < line.length(); ++i) {
        char c = line[i];
        
        if (c == '"') {
            inQuotes = !inQuotes;
        } else if (c == ',' && !inQuotes) {
            add_cell();
        } else {
            cell += c;
        }
    }
    add_cell();
    
    return cols;
}
// =====================================================
// GET ALL SYMBOLS - Dynamic discovery
// =====================================================
std::vector<std::string> ContractLoader::getAllSymbols() {
    std::vector<std::string> symbols;
    for (const auto& pair : symbolMap_) {
        if (!pair.first.empty()) {
            symbols.push_back(pair.first);
        }
    }
    std::sort(symbols.begin(), symbols.end()); // Keep UI dropdowns clean and alphabetized
    return symbols;
}
// =====================================================
// DATE PARSING (like hoadley.js calculateDTE)
// =====================================================
time_t ContractLoader::parseExpiryDate(const std::string& expiryDateStr) {
    // Format: "30-JUN-26" or "30-APR-2026"
    static std::unordered_map<std::string, int> months = {
        {"JAN", 0}, {"FEB", 1}, {"MAR", 2}, {"APR", 3}, {"MAY", 4}, {"JUN", 5},
        {"JUL", 6}, {"AUG", 7}, {"SEP", 8}, {"OCT", 9}, {"NOV", 10}, {"DEC", 11}
    };
    
    std::string str = expiryDateStr;
    std::transform(str.begin(), str.end(), str.begin(), ::toupper);
    
    // Split by '-'
    std::vector<std::string> parts;
    std::stringstream ss(str);
    std::string part;
    while (std::getline(ss, part, '-')) {
        parts.push_back(part);
    }
    
    if (parts.size() != 3) return 0;
    
    // Ensure the day and year parts are numeric before calling std::stoi
    auto is_numeric = [](const std::string& s) {
        return !s.empty() && std::all_of(s.begin(), s.end(), ::isdigit);
    };
    if (!is_numeric(parts[0]) || !is_numeric(parts[2])) return 0;

    int day = std::stoi(parts[0]);
    int month = months.count(parts[1]) ? months[parts[1]] : 0;
    int year = std::stoi(parts[2]);
    
    // Handle 2-digit year
    if (year < 100) year += 2000;
    
    // Create date at 15:30:00 (market close)
    std::tm tm = {};
    tm.tm_year = year - 1900;
    tm.tm_mon = month;
    tm.tm_mday = day;
    tm.tm_hour = 15;
    tm.tm_min = 30;
    tm.tm_sec = 0;
    
    return std::mktime(&tm);
}

double ContractLoader::calculateDTE(const std::string& expiryDateStr) {
    time_t expiryTs = parseExpiryDate(expiryDateStr);
    if (expiryTs == 0) return 0.000001;
    
    std::time_t now = std::time(nullptr);
    std::tm* nowTm = std::localtime(&now);
    nowTm->tm_hour = 15;
    nowTm->tm_min = 30;
    nowTm->tm_sec = 0;
    std::time_t todayClose = std::mktime(nowTm);
    
    double diffMs = difftime(expiryTs, todayClose);
    double dte = diffMs / (24 * 60 * 60); // Convert to days
    
    if (dte <= 0) {
        // Check if same day
        std::tm* expTm = std::localtime(&expiryTs);
        if (nowTm->tm_year == expTm->tm_year && 
            nowTm->tm_mon == expTm->tm_mon && 
            nowTm->tm_mday == expTm->tm_mday) {
            // Same day - calculate remaining minutes
            int nowMin = nowTm->tm_hour * 60 + nowTm->tm_min;
            int marketEnd = 15 * 60 + 30;
            dte = std::max(0.000001, (marketEnd - nowMin) / (24.0 * 60.0));
        } else {
            dte = 0.000001;
        }
    }
    
    return std::max(0.000001, dte);
}

// =====================================================
// LOAD NSE CONTRACTS
// =====================================================
bool ContractLoader::loadNseContracts(const std::string& filepath) {
    std::ifstream file(filepath);
    if (!file.is_open()) {
        std::cerr << "Cannot open NSE contracts: " << filepath << "\n";
        return false;
    }
    
    std::string line;
    std::getline(file, line); // skip header
    
    int count = 0;
    while (std::getline(file, line)) {
        // Use proper CSV parser
        std::vector<std::string> cols = parseCSVLine(line);
        
        if (cols.size() < 8) continue;
        
        // Allow Options (CE/PE), Futures (XX/FUT), and Underlying Spot Indices (empty)
        if (cols[6] != "CE" && cols[6] != "PE" && cols[6] != "XX" && cols[6] != "FUT" && !cols[6].empty()) {
            continue;
        }

        // Skip rows with missing critical token fields
        auto is_numeric = [](const std::string& s) {
            return !s.empty() && std::all_of(s.begin(), s.end(), ::isdigit);
        };
        if (!is_numeric(cols[0]) || !is_numeric(cols[7])) {
            continue;
        }

        // NEW: Robust check for strike price before calling stod
        auto is_double = [](const std::string& s) { char* end = nullptr; strtod(s.c_str(), &end); return end != s.c_str() && *end == '\0'; };
        if (!cols[5].empty() && !is_double(cols[5])) {
            continue;
        }

        try {
            Contract c;
            c.xxToken = std::stoul(cols[0]);
            c.exchange = (cols[1] == "NSE") ? Exchange::NSE : Exchange::BSE;
            c.symbol = cols[3];
            c.expiry = cols[4];  // Full: "30-JUN-26"
            c.strike = cols[5].empty() ? 0.0 : std::stod(cols[5]);
            c.opStr = cols[6];
            
            if (cols[6] == "CE") c.opType = OptionType::CALL;
            else if (cols[6] == "PE") c.opType = OptionType::PUT;
            else {
                c.opType = OptionType::FUTURE;
                c.opStr = "FUT";
            }
            
            // NSE has real market tokens
            c.marketToken = std::stoul(cols[7]);
            
            // Calculate expiry components
            c.expiryShort = c.expiry;
            c.expiryShort.erase(std::remove(c.expiryShort.begin(), c.expiryShort.end(), '-'), 
                               c.expiryShort.end());
            
            c.expiryDate = parseExpiryDate(c.expiry);
            c.dte = calculateDTE(c.expiry);
            
            // Store in maps
            marketTokenMap_[c.marketToken] = c;
            xxTokenMap_[c.xxToken] = c;
            
            std::string symKey = c.symbol;
            symbolMap_[symKey].push_back(&marketTokenMap_[c.marketToken]);
            
            std::string expKey = c.symbol + "|" + c.expiry;
            expiryMap_[expKey].push_back(&marketTokenMap_[c.marketToken]);
            
            count++;
        } catch (std::exception& e) {
            std::cerr << "Error parsing NSE line: " << e.what() << "\n";
        }
    }
    
    std::cout << "Loaded " << count << " NSE contracts\n";
    return true;
}

// =====================================================
// LOAD BSE CONTRACTS
// =====================================================
bool ContractLoader::loadBseContracts(const std::string& filepath) {
    std::ifstream file(filepath);
    if (!file.is_open()) {
        std::cerr << "Cannot open BSE contracts: " << filepath << "\n";
        return false;
    }
    
    std::string line;
    std::getline(file, line); // skip header
    
    int count = 0;
    while (std::getline(file, line)) {
        // Use proper CSV parser
        std::vector<std::string> cols = parseCSVLine(line);
        
        if (cols.size() < 8) continue;
        
        // Allow Options (CE/PE), Futures (XX/FUT), and Underlying Spot Indices (empty)
        if (cols[6] != "CE" && cols[6] != "PE" && cols[6] != "XX" && cols[6] != "FUT" && !cols[6].empty()) {
            continue;
        }

        // Skip rows with missing critical token fields
        auto is_numeric = [](const std::string& s) {
            return !s.empty() && std::all_of(s.begin(), s.end(), ::isdigit);
        };
        if (!is_numeric(cols[0])) {
            continue;
        }

        try {
            Contract c;
            c.xxToken = std::stoul(cols[0]);
            c.exchange = Exchange::BSE;
            c.symbol = cols[3];
            c.expiry = cols[4];  // Full: "30-APR-2026"
            c.strike = cols[5].empty() ? 0.0 : std::stod(cols[5]);
            c.opStr = cols[6];
            
            if (cols[6] == "CE") c.opType = OptionType::CALL;
            else if (cols[6] == "PE") c.opType = OptionType::PUT;
            else {
                c.opType = OptionType::FUTURE;
                c.opStr = "FUT";
            }
            
            // BSE Token column is "IO" - use xxToken temporarily
            c.marketToken = c.xxToken;
            
            // Calculate expiry components
            c.expiryShort = c.expiry;
            c.expiryShort.erase(std::remove(c.expiryShort.begin(), c.expiryShort.end(), '-'), 
                               c.expiryShort.end());
            
            c.expiryDate = parseExpiryDate(c.expiry);
            c.dte = calculateDTE(c.expiry);
            
            // Store in maps
            xxTokenMap_[c.xxToken] = c;
            
            std::string symKey = c.symbol;
            symbolMap_[symKey].push_back(&xxTokenMap_[c.xxToken]);
            
            std::string expKey = c.symbol + "|" + c.expiry;
            expiryMap_[expKey].push_back(&xxTokenMap_[c.xxToken]);
            
            count++;
        } catch (std::exception& e) {
            std::cerr << "Error parsing BSE line: " << e.what() << "\n";
        }
    }
    
    std::cout << "Loaded " << count << " BSE contracts\n";
    return true;
}

// =====================================================
// LOOKUP METHODS
// =====================================================
Contract* ContractLoader::getByMarketToken(uint32_t token) {
    auto it = marketTokenMap_.find(token);
    return (it != marketTokenMap_.end()) ? &it->second : nullptr;
}

Contract* ContractLoader::getByXxToken(uint32_t xxToken) {
    auto it = xxTokenMap_.find(xxToken);
    return (it != xxTokenMap_.end()) ? &it->second : nullptr;
}

std::vector<Contract*> ContractLoader::getBySymbol(const std::string& symbol) {
    auto it = symbolMap_.find(symbol);
    if (it == symbolMap_.end()) return {};
    return it->second;
}

std::vector<Contract*> ContractLoader::getByExpiry(const std::string& symbol, const std::string& expiry) {
    std::string key = symbol + "|" + expiry;
    auto it = expiryMap_.find(key);
    if (it == expiryMap_.end()) return {};
    return it->second;
}

// =====================================================
// BHAVCOPY LOADING (Dynamic Latest Date Detection)
// =====================================================
std::string ContractLoader::findLatestBhavcopy(const std::string& dirPath, bool isBSE) {
    namespace fs = std::filesystem;
    std::string latestFile = "";
    int maxDateVal = 0; // Stored as YYYYMMDD for easy sorting
    
    std::unordered_map<std::string, int> months = {
        {"JAN", 1}, {"FEB", 2}, {"MAR", 3}, {"APR", 4}, {"MAY", 5}, {"JUN", 6},
        {"JUL", 7}, {"AUG", 8}, {"SEP", 9}, {"OCT", 10}, {"NOV", 11}, {"DEC", 12}
    };

    try {
        if (!fs::exists(dirPath) || !fs::is_directory(dirPath)) {
            std::cerr << "[BHAVCOPY] Directory not found: " << dirPath << "\n";
            return "";
        }

        std::string prefix = isBSE ? "bsefo" : "fo";
        std::string suffix = "bhav.csv";

        for (const auto& entry : fs::directory_iterator(dirPath)) {
            if (!entry.is_regular_file()) continue;
            std::string filename = entry.path().filename().string();
            
            // Create lowercase version for robust matching in Linux
            std::string lower_filename = filename;
            std::transform(lower_filename.begin(), lower_filename.end(), lower_filename.begin(), ::tolower);
            
            // Check if it matches foDDMMMYYYYbhav.csv exactly
            if (lower_filename.length() == prefix.length() + 9 + suffix.length() &&
                lower_filename.substr(0, prefix.length()) == prefix &&
                lower_filename.substr(lower_filename.length() - suffix.length()) == suffix) {
                
                // Extract exact date part "12MAY2026" or "12May2026" from original casing
                std::string dateStr = filename.substr(prefix.length(), 9); 
                std::string dayStr = dateStr.substr(0, 2);
                std::string monStr = dateStr.substr(2, 3);
                std::string yrStr = dateStr.substr(5, 4);

                std::transform(monStr.begin(), monStr.end(), monStr.begin(), ::toupper);

                if (months.count(monStr)) {
                    int day = std::stoi(dayStr);
                    int mon = months[monStr];
                    int yr = std::stoi(yrStr);
                    
                    // Create integer YYYYMMDD (e.g., 20260512)
                    int dateVal = yr * 10000 + mon * 100 + day;

                    if (dateVal > maxDateVal) {
                        maxDateVal = dateVal;
                        latestFile = entry.path().string();
                    }
                }
            }
        }
    } catch (const std::exception& e) {
        std::cerr << "[BHAVCOPY] Error scanning directory " << dirPath << ": " << e.what() << "\n";
    }
    
    return latestFile;
}

bool ContractLoader::loadNseBhavcopy(const std::string& dirPath) {
    std::string latestFile = findLatestBhavcopy(dirPath, false);
    if (latestFile.empty()) return false;
    
    std::cout << "[BHAVCOPY] Loading latest NSE EOD file: " << latestFile << "\n";
    std::ifstream file(latestFile);
    if (!file.is_open()) return false;
    
    std::string line;
    std::getline(file, line); // Skip header
    int count = 0;
    
    auto is_double = [](const std::string& s) { char* end = nullptr; strtod(s.c_str(), &end); return end != s.c_str() && *end == '\0'; };
    
    while (std::getline(file, line)) {
        std::vector<std::string> cols = parseCSVLine(line);
        if (cols.size() < 9) continue;
        
        std::string symbol = cols[1];
        std::string expiry = cols[2];
        std::string opStr = cols[4];
        
        double strike = (is_double(cols[3])) ? std::stod(cols[3]) : 0.0;
        double closePrice = (is_double(cols[8])) ? std::stod(cols[8]) : 0.0;
        time_t bhavExpiryDate = parseExpiryDate(expiry);
        
        auto contracts = getBySymbol(symbol);
        for (auto* c : contracts) {
            if (c->strike == strike && c->opStr == opStr && c->expiryDate == bhavExpiryDate) {
                c->eodPrice = closePrice;
                count++;
            }
        }
    }
    std::cout << "[BHAVCOPY] Mapped " << count << " NSE EOD closing prices.\n";
    return true;
}

bool ContractLoader::loadBseBhavcopy(const std::string& dirPath) {
    std::string latestFile = findLatestBhavcopy(dirPath, true);
    if (latestFile.empty()) return false;
    
    std::cout << "[BHAVCOPY] Loading latest BSE EOD file: " << latestFile << "\n";
    std::ifstream file(latestFile);
    if (!file.is_open()) return false;
    
    std::string line;
    std::getline(file, line); // Skip header
    int count = 0;
    
    auto is_double = [](const std::string& s) { char* end = nullptr; strtod(s.c_str(), &end); return end != s.c_str() && *end == '\0'; };
    
    while (std::getline(file, line)) {
        std::vector<std::string> cols = parseCSVLine(line);
        if (cols.size() < 9) continue;
        
        std::string symbol = cols[1];
        std::string expiry = cols[2];
        std::string opStr = cols[4];
        
        double strike = (is_double(cols[3])) ? std::stod(cols[3]) : 0.0;
        double closePrice = (is_double(cols[8])) ? std::stod(cols[8]) : 0.0;
        time_t bhavExpiryDate = parseExpiryDate(expiry);
        
        auto contracts = getBySymbol(symbol);
        for (auto* c : contracts) {
            if (c->strike == strike && c->opStr == opStr && c->expiryDate == bhavExpiryDate) {
                c->eodPrice = closePrice;
                count++;
            }
        }
    }
    std::cout << "[BHAVCOPY] Mapped " << count << " BSE EOD closing prices.\n";
    return true;
}