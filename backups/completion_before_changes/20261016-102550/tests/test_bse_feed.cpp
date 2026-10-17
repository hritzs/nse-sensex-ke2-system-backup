#include "decoder.h"
#include "parser_7208.h"
#include "market_feed.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <unordered_map>
#include <vector>
std::unordered_map<uint32_t,std::pair<std::string,Exchange>> TOKEN_MAP, BSE_TOKEN_MAP;
static std::vector<std::string> events, legacy, seen;
bool market_source_active() { return true; }
void broadcast_market_source(const std::string& s) { events.push_back(s); }
void broadcast_raw(const std::string& s) { legacy.push_back(s); }
namespace Analytics {
std::string process_tick(const std::string& s,double,double,double) { seen.push_back(s); return ""; }
void update_underlying(const std::string&,double) {}
void update_oi(const std::string&,uint64_t) {}
void update_volume(const std::string&,uint64_t) {}
void update_lpp(const std::string&,double,double) {}
}
static void u16(std::vector<uint8_t>& p,uint16_t v) {p.push_back(v>>8);p.push_back(v);}
static void u32(std::vector<uint8_t>& p,uint32_t v) {u16(p,v>>16);u16(p,v);}
static std::vector<uint8_t> bse() {
 std::vector<uint8_t> p;u32(p,2020);p.resize(26);u16(p,1);
 u32(p,860819);p.resize(p.size()+46);u16(p,1);p.resize(p.size()+12);
 u32(p,0);u32(p,10);u32(p,10000);
 for(int i=0;i<12;++i) u16(p,0);
 u16(p,static_cast<uint16_t>(-5));for(int i=0;i<4;++i)u16(p,0);
 u16(p,5);for(int i=0;i<4;++i)u16(p,0);
 return p;
}
int main() {
 TOKEN_MAP[860819]={"NIFTY|27-OCT-26|100|CE",Exchange::NSE};
 BSE_TOKEN_MAP[860819]={"SENSEX|27-OCT-26|0|FUT",Exchange::BSE};
 auto packet=bse();
 process_packet(Exchange::BSE,packet.data(),packet.size());
 assert(events.size()==1);
 assert(events[0].find("\"exchange\":\"BSE\"")!=std::string::npos);
 assert(events[0].find("\"ltp\":100")!=std::string::npos);
 assert(events[0].find("\"bid\":99.95")!=std::string::npos);
 assert(events[0].find("\"ask\":100.05")!=std::string::npos);
 assert(seen.size()==1 && seen[0].find("SENSEX|")==0);
 assert(legacy.size()==1 && legacy[0].find("future_quote")!=std::string::npos);
 events.clear();seen.clear();legacy.clear();
 for(size_t n=0;n<packet.size();++n) process_packet(Exchange::BSE,packet.data(),n);
 assert(events.empty() && seen.empty() && legacy.empty());
 // 2021 extended tokens are not silently truncated to a different instrument.
 auto extended=packet;extended[3]=0xe5;
 extended.insert(extended.begin()+28,4,0);
 process_packet(Exchange::BSE,extended.data(),extended.size());
 assert(events.size()==1);
 events.clear();seen.clear();legacy.clear();
 extended[28]=1;
 process_packet(Exchange::BSE,extended.data(),extended.size());
 assert(events.empty());
 // NSE raw options must be emitted even if analytics returns no surface.
 std::vector<uint8_t> nse(42+214,0);
 nse[41]=1;
 auto set32=[&](size_t offset,uint32_t value) {
   nse[offset]=value>>24;nse[offset+1]=value>>16;
   nse[offset+2]=value>>8;nse[offset+3]=value;
 };
 set32(42,860819);nse[47]=1;
 set32(42+16,1845);set32(42+60,1840);set32(42+120,1855);
 parse_7208(nse.data(),nse.size());
 assert(events.size()==1);
 assert(events[0].find("\"exchange\":\"NSE\"")!=std::string::npos);
 assert(events[0].find("\"ltp\":18.45")!=std::string::npos);
 assert(legacy.empty());
 events.clear();seen.clear();
 for(size_t n=0;n<nse.size();++n)parse_7208(nse.data(),n);
 assert(events.empty() && seen.empty());
 std::cout<<"BSE/NSE routing, raw LTP, legacy futures, extended tokens, and truncation passed\n";
}
