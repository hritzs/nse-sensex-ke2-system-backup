#include "expiry_utils.h"
#include <ctime>
#include <unordered_map>

/*
    Simple institutional hack:
    You should replace this with real NSE calendar later
*/

static std::unordered_map<std::string, int> MONTH = {
    {"JAN",0},{"FEB",1},{"MAR",2},{"APR",3},{"MAY",4},{"JUN",5},
    {"JUL",6},{"AUG",7},{"SEP",8},{"OCT",9},{"NOV",10},{"DEC",11}
};

double ExpiryUtils::to_epoch(const std::string& exp_in)
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
    tm.tm_isdst = -1;

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
    
        if (cand_t < t - 86400) {
            tm.tm_year = curr_year + 1;
            cand_t = std::mktime(&tm);
        }
        return cand_t;
    }
}