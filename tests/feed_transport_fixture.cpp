#include "ws_server.h"
#include "decoder.h"
#include "parser_7208.h"
#include "market_feed.h"
#include "contract_loader.h"
#include <hiredis/hiredis.h>
#include <mutex>
#include <thread>
#include <chrono>
#include <unordered_map>
#include <vector>
#include <cstdlib>
std::unordered_map<uint32_t,std::pair<std::string,Exchange>> TOKEN_MAP, BSE_TOKEN_MAP;
redisContext* g_redis = nullptr;
std::mutex g_redisMtx;
void SystemLog(const std::string&, bool) {}
static void u16(std::vector<uint8_t>& p,uint16_t v) {p.push_back(v>>8);p.push_back(v);}
static void u32(std::vector<uint8_t>& p,uint32_t v) {u16(p,v>>16);u16(p,v);}
int main(int argc,char** argv) {
 if(argc!=2) return 2;
 TOKEN_MAP[200]={"NIFTY|27-OCT-26|0|FUT",Exchange::NSE};
 TOKEN_MAP[300]={"RELIANCE|27-OCT-26|0|FUT",Exchange::NSE};
 TOKEN_MAP[301]={"RELIANCE|27-OCT-26|100|CE",Exchange::NSE};
 BSE_TOKEN_MAP[200]={"SENSEX|27-OCT-26|0|FUT",Exchange::BSE};
 std::vector<uint8_t> p;u32(p,2020);p.resize(26);u16(p,1);
 u32(p,200);p.resize(p.size()+46);u16(p,1);p.resize(p.size()+12);
 u32(p,0);u32(p,10);u32(p,10000);
 for(int i=0;i<12;++i)u16(p,0);
 u16(p,static_cast<uint16_t>(-5));for(int i=0;i<4;++i)u16(p,0);
 u16(p,5);for(int i=0;i<4;++i)u16(p,0);
 // Two NSE MBP records: an actual stock future and a stock option.
 std::vector<uint8_t> nse(42+214*2,0);nse[41]=2;nse[10]=7208>>8;nse[11]=7208&255;
 auto put32=[&](size_t off,uint32_t v) {
   nse[off]=v>>24;nse[off+1]=v>>16;nse[off+2]=v>>8;nse[off+3]=v;
 };
 put32(42,300);nse[47]=1;
 put32(42+12,15000);put32(42+60,14995);put32(42+120,15005);
 put32(42+214,301);nse[42+214+5]=1;
 put32(42+214+12,1845);put32(42+214+60,1840);put32(42+214+120,1855);
 start_ws_server(std::atoi(argv[1]));
 for(int i=0;i<150;++i) {
  std::this_thread::sleep_for(std::chrono::milliseconds(100));
  process_packet(Exchange::BSE,p.data(),p.size());
  parse_7208(nse.data(),nse.size());
  publish_market_oi(Exchange::NSE,301,4820000);
 }
 return 0;
}
