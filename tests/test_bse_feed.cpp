#include "decoder.h"
#include "parser_7208.h"
#include "market_feed.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <unordered_map>
#include <vector>
#include <lzo/lzo1z.h>
#include <fstream>
#include <sstream>
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
 for(int i=0;i<9;++i)u16(p,0); // NFCAST 6.3 auction/reserved fields
 u16(p,static_cast<uint16_t>(-5));for(int i=0;i<4;++i)u16(p,0);
 u16(p,5);for(int i=0;i<4;++i)u16(p,0);
 return p;
}
int main(int argc, char** argv) {
 if (argc==3) {
   std::ifstream master(argv[1]); std::string line; std::getline(master,line);
   while(std::getline(master,line)) {
     std::istringstream row(line);std::string token;std::getline(row,token,',');
     try { BSE_TOKEN_MAP[std::stoul(token)]={"SENSEX|29-OCT-26|0|FUT",Exchange::BSE}; }
     catch(...) {}
   }
   std::ifstream input(argv[2],std::ios::binary);assert(input);
   unsigned packets=0,rows=0,two_sided=0,missing=0;
   char prefix[4];
   while(input.read(prefix,4)) {
     uint32_t size=(uint8_t(prefix[0])<<24)|(uint8_t(prefix[1])<<16)|
                   (uint8_t(prefix[2])<<8)|uint8_t(prefix[3]);
     assert(size<=65536);std::vector<uint8_t> data(size);
     assert(input.read(reinterpret_cast<char*>(data.data()),size));
     events.clear();process_packet(Exchange::BSE,data.data(),data.size());++packets;
     for(const auto& event:events) {
       if(event.find("market_quote")==std::string::npos)continue;
       ++rows;
       if(event.find("\"bid\":null")==std::string::npos &&
          event.find("\"ask\":null")==std::string::npos)++two_sided;
       else ++missing;
     }
   }
   std::cout<<"BSE_CAPTURE packets="<<packets<<" quote_rows="<<rows
            <<" two_sided="<<two_sided<<" missing_book="<<missing<<"\n";
   assert(packets>0 && rows>0 && two_sided>0);
   return 0;
 }

 TOKEN_MAP[860819]={"NIFTY|27-OCT-26|100|CE",Exchange::NSE};
 BSE_TOKEN_MAP[860819]={"SENSEX|27-OCT-26|0|FUT",Exchange::BSE};
 auto packet=bse();
 process_packet(Exchange::BSE_EQ,packet.data(),packet.size());
 assert(events.empty()); // EQ cash IDs must never alias FO IDs.
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
 // Complex 2021 records are unsupported, including IDs fitting in 32 bits.
 auto extended=packet;extended[3]=0xe5;
 extended.insert(extended.begin()+28,4,0);
 process_packet(Exchange::BSE,extended.data(),extended.size());
 assert(events.empty() && seen.empty() && legacy.empty());
 events.clear();seen.clear();legacy.clear();
 extended[28]=1;
 process_packet(Exchange::BSE,extended.data(),extended.size());
 assert(events.empty());

 // A truncated second BSE record must not publish even the first record.
 auto multi=packet;multi[27]=2;
 multi.insert(multi.end(),packet.begin()+28,packet.end());
 process_packet(Exchange::BSE,multi.data(),multi.size());
 assert(events.size()==2);
 events.clear();seen.clear();legacy.clear();
 multi.pop_back();process_packet(Exchange::BSE,multi.data(),multi.size());
 assert(events.empty() && seen.empty() && legacy.empty());
 // Escaped Long Long quantities occupy eight bytes, unlike price/order escapes.
 auto wide=packet;wide.resize(wide.size()-20);
 u16(wide,static_cast<uint16_t>(-5));
 u16(wide,32767);u32(wide,1);u32(wide,10); // 2^32+10 actual bid quantity
 u16(wide,0);u16(wide,32767);u32(wide,0);u32(wide,0);u16(wide,0);
 u16(wide,5);for(int i=0;i<4;++i)u16(wide,0);
 process_packet(Exchange::BSE,wide.data(),wide.size());
 assert(events.size()==1 && events[0].find("\"bid_qty\":4294967306")!=std::string::npos);
 events.clear();seen.clear();legacy.clear();

 // Explicit end-of-book markers produce missing books, never invented prices.
 auto sparse=packet;sparse.resize(sparse.size()-20);
 u16(sparse,32766);u16(sparse,static_cast<uint16_t>(-32766));
 process_packet(Exchange::BSE,sparse.data(),sparse.size());
 assert(events.size()==1 && events[0].find("\"bid\":null")!=std::string::npos);
 assert(events[0].find("\"ask\":null")!=std::string::npos);
 assert(events[0].find("\"volume\":0")!=std::string::npos);
 events.clear();seen.clear();legacy.clear();
 // Real exchange index identity travels independently of F&O instrument IDs.
 std::vector<uint8_t> index;u32(index,2011);index.resize(26);u16(index,1);
 u32(index,1);index.resize(index.size()+16);u32(index,7400000);
 for(char c:std::string("SENSEX"))index.push_back(c);
 index.push_back(0);index.resize(index.size()+9);
 process_packet(Exchange::BSE_EQ,index.data(),index.size());
 assert(events.size()==1 && events[0].find("market_index")!=std::string::npos);
 assert(events[0].find("\"ltp\":74000")!=std::string::npos);
 events.clear();index.pop_back();process_packet(Exchange::BSE_EQ,index.data(),index.size());
 assert(events.empty());

 // Protocol-defined legacy and enhanced MBP: two records detect stride errors.
 TOKEN_MAP[860820]={"NIFTY|27-OCT-26|150|PE",Exchange::NSE};
 auto put16=[](std::vector<uint8_t>& v,size_t p,uint16_t x){v[p]=x>>8;v[p+1]=x;};
 auto put32=[](std::vector<uint8_t>& v,size_t p,uint32_t x){
   v[p]=x>>24;v[p+1]=x>>16;v[p+2]=x>>8;v[p+3]=x;
 };
 auto build=[&](uint16_t tx) {
   const size_t stride=tx==17208?258:214;
   std::vector<uint8_t> v(42+stride*2,0);
   put16(v,10,tx);put16(v,38,v.size());put16(v,40,2);
   for(size_t r=0;r<2;++r) {
     const auto base=42+r*stride;put32(v,base,860819+r);put16(v,base+4,1);
     put32(v,base+(tx==17208?16:12),1845+r*100);
     put32(v,base+(tx==17208?68:60),1840+r*100);
     put32(v,base+(tx==17208?148:120),1855+r*100);
   }
   return v;
 };
 auto check=[&]() {
   assert(events.size()==2 && seen.size()==2 && legacy.empty());
   assert(events[0].find("\"ltp\":18.45")!=std::string::npos);
   assert(events[0].find("\"bid\":18.4")!=std::string::npos);
   assert(events[0].find("\"ask\":18.55")!=std::string::npos);
   assert(events[1].find("\"ltp\":19.45")!=std::string::npos);
   events.clear();seen.clear();legacy.clear();
 };
 for(uint16_t tx:{7208,17208}) {
   auto nse=build(tx);parse_7208(nse.data(),nse.size());check();
   for(size_t n=0;n<nse.size();++n)parse_7208(nse.data(),n);
   assert(events.empty() && seen.empty());
   // Uncompressed sequential packet: zero compression marker is valid.
   std::vector<uint8_t> raw;u16(raw,2);u16(raw,1);u16(raw,0);
   raw.resize(raw.size()+8);raw.insert(raw.end(),nse.begin(),nse.end());
   process_packet(Exchange::NSE,raw.data(),raw.size());check();
   // A mixed compressed/uncompressed envelope must route both in order.
   std::vector<uint8_t> payload(8,0);payload.insert(payload.end(),nse.begin(),nse.end());
   std::vector<uint8_t> compressed(payload.size()*2+256);
   std::vector<uint8_t> work(LZO1Z_999_MEM_COMPRESS);
   lzo_uint size=compressed.size();
   assert(lzo1z_999_compress(payload.data(),payload.size(),compressed.data(),&size,work.data())==LZO_E_OK);
   std::vector<uint8_t> mixed;u16(mixed,2);u16(mixed,2);
   u16(mixed,size);mixed.insert(mixed.end(),compressed.begin(),compressed.begin()+size);
   u16(mixed,0);mixed.insert(mixed.end(),payload.begin(),payload.end());
   process_packet(Exchange::NSE,mixed.data(),mixed.size());
   assert(events.size()==4 && seen.size()==4);events.clear();seen.clear();
   // Reject a truncated raw message before it can publish either record.
   raw.pop_back();process_packet(Exchange::NSE,raw.data(),raw.size());
   assert(events.empty());
 }
 std::cout<<"NSE 7208/17208, compressed/raw batches, BSE routing and bounds passed\n";
}
