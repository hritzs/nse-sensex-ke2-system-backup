#include "expiry_engine.h"
#include <ctime>
#include <unordered_map>
#include <vector>
#include <algorithm>

/* =========================================================
   MONTH MAP
========================================================= */

static std::unordered_map<std::string, int> MONTH = {
    {"JAN",0},{"FEB",1},{"MAR",2},{"APR",3},{"MAY",4},{"JUN",5},
    {"JUL",6},{"AUG",7},{"SEP",8},{"OCT",9},{"NOV",10},{"DEC",11}
};

/* =========================================================
   PARSE EXPIRY STRING → EPOCH (INSTITUTIONAL FIX)
   format: 26JUN or 26JUN2025
========================================================= */

double ExpiryEngine::to_epoch(const std::string& exp_in)
{
    std::string exp = exp_in;
    exp.erase(std::remove(exp.begin(), exp.end(), '-'), exp.end());
    exp.erase(std::remove(exp.begin(), exp.end(), '/'), exp.end());

    if (exp.size() < 5) return 0;

    int day = 0;
    try {
        day = std::stoi(exp.substr(0,2));
    } catch (...) {
        return 0;
    }
    std::string mon = exp.substr(2,3);
    for (char& c : mon) c = std::toupper(c);

    if (!MONTH.count(mon)) return 0;

    std::time_t t = std::time(nullptr);
    std::tm tm = *std::localtime(&t);

    tm.tm_mday = day;
    tm.tm_mon  = MONTH[mon];

    tm.tm_hour = 15;
    tm.tm_min  = 30;
    tm.tm_sec  = 0;
    tm.tm_isdst = -1; // Auto DST

    if (exp.size() >= 9) { // e.g. 26MAY2026
        int year_suffix = 0;
        try {
            year_suffix = std::stoi(exp.substr(5, 4));
        } catch (...) {
            year_suffix = tm.tm_year + 1900;
        }
        tm.tm_year = year_suffix - 1900;
        return std::mktime(&tm);
    } 
    else if (exp.size() >= 7) { // e.g. 26MAY26
        int year_suffix = 0;
        try { year_suffix = std::stoi(exp.substr(5, 2)); } catch(...) { year_suffix = tm.tm_year % 100; }
        if (year_suffix < 100) year_suffix += 2000;
        tm.tm_year = year_suffix - 1900;
        return std::mktime(&tm);
    } 
    else {
        // Guess the year based on current time
        int curr_year = tm.tm_year;
        tm.tm_year = curr_year;
        std::time_t cand_t = std::mktime(&tm);
    
        // If candidate is > 1 day in the past, roll forward to next year
        if (cand_t < t - 86400) {
            tm.tm_year = curr_year + 1;
            cand_t = std::mktime(&tm);
        }
        return cand_t;
    }
}

/* =========================================================
   WEEKLY EXPIRY CHECK (THURSDAY LOGIC)
========================================================= */

bool ExpiryEngine::is_weekly(const std::string& exp)
{
    // heuristic: short dated expiries are weekly
    return exp.size() <= 7;
}

/* =========================================================
   NEAREST EXPIRY PICKER (ATM SURFACE BASE)
========================================================= */

std::string ExpiryEngine::nearest(const std::vector<std::string>& expiries)
{
    if (expiries.empty()) return "";

    return expiries.front(); // already sorted upstream
}