#include "ke2_feed.h"
#ifdef _WIN32
# include <winsock2.h>
# include <ws2tcpip.h>
using socket_type=SOCKET;
static constexpr socket_type bad_socket=INVALID_SOCKET;
static void close_socket(socket_type s) {closesocket(s);}
#else
# include <arpa/inet.h>
# include <sys/socket.h>
# include <sys/select.h>
# include <unistd.h>
using socket_type=int;
static constexpr socket_type bad_socket=-1;
static void close_socket(socket_type s) {close(s);}
#endif
#include "decoder.h"
#include "market_feed.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <unordered_map>
#include <vector>

using TokenMap=std::unordered_map<uint32_t,std::pair<std::string,Exchange>>;
TokenMap TOKEN_MAP, BSE_TOKEN_MAP;
struct Stream {socket_type fd; Exchange exchange;};
struct ke2_feed_context {
    ke2_feed_callback callback;
    void* user;
    std::atomic<bool> running{false};
    std::atomic<uint64_t> datagrams{0}, events{0}, socket_errors{0}, callback_errors{0}, last_ms{0};
    std::vector<Stream> sockets;
    std::vector<std::thread> workers;
    std::vector<std::pair<uint32_t,std::string>> instruments;
    std::mutex decode;
#ifdef _WIN32
    bool wsa=false;
#endif
};
static std::unique_ptr<ke2_feed_context> instance;
static std::mutex api_mutex;
static thread_local std::string error_text;
static thread_local bool in_callback=false;
static uint64_t now_ms() {
 return std::chrono::duration_cast<std::chrono::milliseconds>(
  std::chrono::system_clock::now().time_since_epoch()).count();
}
static int32_t fail(int32_t code,const std::string& message) {error_text=message;return code;}
template<class F> static int32_t protect(F fn) noexcept {
 if(in_callback)return fail(KE2_INVALID_STATE,"Context API cannot be called from a callback");
 try {std::lock_guard<std::mutex> lock(api_mutex);error_text.clear();return fn();}
 catch(const std::invalid_argument& e){return fail(KE2_INVALID_ARGUMENT,e.what());}
 catch(const std::exception& e){return fail(KE2_INTERNAL_ERROR,e.what());}
 catch(...){return fail(KE2_INTERNAL_ERROR,"Unknown native exception");}
}
static bool valid(ke2_feed_handle h){return h && h==instance.get();}
static Exchange exchange_of(uint32_t e) {
 switch(e){case KE2_NSE_FO:return Exchange::NSE;case KE2_BSE_FO:return Exchange::BSE;
 case KE2_BSE_EQ:return Exchange::BSE_EQ;default:throw std::invalid_argument("Invalid exchange");}
}
static void invoke(ke2_feed_context& c,ke2_feed_callback fn,void* user,const std::string& json) {
 in_callback=true;
 try {fn(user,json.data(),static_cast<uint32_t>(json.size()));}
 catch(...){++c.callback_errors;}
 in_callback=false;
}
bool market_source_active(){return instance!=nullptr;}
void broadcast_market_source(const std::string& json) {
 if(instance){++instance->events;invoke(*instance,instance->callback,instance->user,json);}
}
// The SDK exposes feed events, not analytics or legacy WebSocket broadcasts.
void broadcast_raw(const std::string&){}
namespace Analytics {
std::string process_tick(const std::string&,double,double,double){return {};}
void update_underlying(const std::string&,double){}
void update_oi(const std::string&,uint64_t){}
void update_volume(const std::string&,uint64_t){}
void update_lpp(const std::string&,double,double){}
}
static std::string trim(std::string s) {
 const auto a=s.find_first_not_of(" \t\r\n");
 if(a==std::string::npos)return {};
 return s.substr(a,s.find_last_not_of(" \t\r\n")-a+1);
}
static std::vector<std::string> csv(const std::string& line) {
 std::vector<std::string> row;std::string value;bool quoted=false;
 for(size_t i=0;i<line.size();++i){
  char ch=line[i];
  if(ch=='"'){
   if(quoted && i+1<line.size() && line[i+1]=='"'){value+='"';++i;}
   else quoted=!quoted;
  } else if(ch==',' && !quoted){row.push_back(trim(value));value.clear();}
  else value+=ch;
 }
 if(quoted)throw std::invalid_argument("Unterminated CSV quote");
 row.push_back(trim(value));return row;
}
static std::string json_string(const std::string& s) {
 std::ostringstream out;out<<'"';
 for(unsigned char ch:s){
  if(ch=='"' || ch=='\\')out<<'\\'<<ch;
  else if(ch<32)out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<unsigned(ch)<<std::dec;
  else out<<ch;
 }
 out<<'"';return out.str();
}
static std::string iso_expiry(const std::string& s) {
 int y=0,m=0,d=0;
 if(s.size()==10 && s[4]=='-' && s[7]=='-'){
  y=std::stoi(s.substr(0,4));m=std::stoi(s.substr(5,2));d=std::stoi(s.substr(8,2));
 } else {
  const auto a=s.find('-'),b=s.find('-',a==std::string::npos?0:a+1);
  if(a==std::string::npos || b==std::string::npos)throw std::invalid_argument("Invalid expiry");
  d=std::stoi(s.substr(0,a));y=std::stoi(s.substr(b+1));if(y<100)y+=2000;
  std::string month=s.substr(a+1,b-a-1);
  std::transform(month.begin(),month.end(),month.begin(),[](unsigned char c){return std::toupper(c);});
  const std::string names[]={"JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"};
  for(int i=0;i<12;++i)if(month==names[i])m=i+1;
 }
 const std::chrono::year_month_day day{std::chrono::year(y),std::chrono::month(unsigned(m)),std::chrono::day(unsigned(d))};
 if(!day.ok())throw std::invalid_argument("Invalid expiry date");
 std::ostringstream out;out<<std::setfill('0')<<std::setw(4)<<y<<'-'<<std::setw(2)<<m<<'-'<<std::setw(2)<<d;return out.str();
}
struct Masters {TokenMap nse,bse;std::vector<std::pair<uint32_t,std::string>> metadata;};
static void load_file(Masters& out,const char* path,uint32_t exchange) {
 if(!path || !*path)return;
 std::ifstream input(std::filesystem::path(reinterpret_cast<const char8_t*>(path)));
 if(!input)throw std::runtime_error("Cannot open master: "+std::string(path));
 std::string line;if(!std::getline(input,line))throw std::invalid_argument("Empty master");
 if(line.compare(0,3,"\xef\xbb\xbf")==0)line.erase(0,3);
 auto headers=csv(line);std::map<std::string,size_t> columns;
 for(size_t i=0;i<headers.size();++i)columns[headers[i]]=i;
 const char* token_column=exchange==KE2_NSE_FO?"Token":"xx.Token";
 for(const char* name:{token_column,"Symbol","Expiry","Strike","OpType"})
  if(!columns.count(name))throw std::invalid_argument("Master missing column: "+std::string(name));
 size_t loaded=0;
 while(std::getline(input,line)){
  if(trim(line).empty())continue;
  auto row=csv(line);
  auto get=[&](const char* key)->std::string{auto it=columns.find(key);return it!=columns.end() && it->second<row.size()?row[it->second]:"";};
  auto op=get("OpType");
  if(op.empty() && (get("Type")=="FUTURE" || get("Type")=="FUTIDX" || get("Type")=="FUTSTK"))op="FUT";
  if(op!="CE" && op!="PE" && op!="XX" && op!="FUT")continue;
  std::string token_text=get(token_column),symbol=get("Symbol"),expiry=get("Expiry"),iso;
  uint64_t token=0;double strike=0;const bool option=op=="CE" || op=="PE";
  try {
   if(token_text.empty() || token_text.find_first_not_of("0123456789")!=std::string::npos)continue;
   token=std::stoull(token_text);if(!token || token>UINT32_MAX || symbol.empty())continue;
   iso=iso_expiry(expiry);
   if(option){size_t used=0;strike=std::stod(get("Strike"),&used);
    if(used!=get("Strike").size() || !std::isfinite(strike) || strike<0)continue;}
  } catch(const std::exception&){continue;}
  std::transform(symbol.begin(),symbol.end(),symbol.begin(),[](unsigned char c){return std::toupper(c);});
  std::ostringstream strike_text;strike_text<<std::setprecision(15)<<strike;
  const std::string full=symbol+"|"+expiry+"|"+(option?strike_text.str():"0")+"|"+(option?op:"FUT");
  auto& map=exchange==KE2_NSE_FO?out.nse:out.bse;
  auto old=map.find(static_cast<uint32_t>(token));
  if(old!=map.end()){
   if(old->second.first!=full)throw std::invalid_argument("Ambiguous duplicate token");
   continue;
  }
  map[static_cast<uint32_t>(token)]={full,exchange_of(exchange)};
  const std::string idx="|NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|NIFTYNXT50|SENSEX|BANKEX|SENSEX50|";
  const bool index=idx.find("|"+symbol+"|")!=std::string::npos;
  std::ostringstream meta;
  meta<<"{\"instrument_id\":"<<token<<",\"exchange\":\""<<(exchange==KE2_NSE_FO?"NSE":"BSE")
      <<"\",\"symbol\":"<<json_string(symbol)<<",\"instrument_type\":\""
      <<(option?"OPT":"FUT")<<(index?"IDX":"STK")<<"\",\"expiry\":"<<json_string(iso)
      <<",\"strike\":"<<(option?strike_text.str():"null")<<",\"option_type\":"
      <<(option?json_string(op):"null")<<",\"lot_size\":null,\"tick_size\":null}";
  out.metadata.emplace_back(exchange,meta.str());++loaded;
 }
 if(!loaded)throw std::invalid_argument("Master has no supported contracts: "+std::string(path));
}
static Masters load_masters(const char* nse,const char* bse) {
 Masters m;load_file(m,nse,KE2_NSE_FO);load_file(m,bse,KE2_BSE_FO);
 if(m.nse.empty() && m.bse.empty())throw std::invalid_argument("At least one nonempty master required");
 for(const auto& [token,unused]:m.nse)if(m.bse.count(token))
  throw std::invalid_argument("Ambiguous instrument_id across NSE and BSE");
 return m;
}
static void commit_masters(ke2_feed_context& c,Masters&& m) {
 TOKEN_MAP.swap(m.nse);BSE_TOKEN_MAP.swap(m.bse);c.instruments.swap(m.metadata);
}

static socket_type make_socket(const ke2_feed_stream& cfg) {
 if(cfg.struct_size!=sizeof(ke2_feed_stream) || !cfg.multicast_group || !cfg.port || cfg.port>65535)
  throw std::invalid_argument("Invalid stream configuration/struct size");
 exchange_of(cfg.exchange);
 in_addr group{},local{};
 if(inet_pton(AF_INET,cfg.multicast_group,&group)!=1 ||
    (ntohl(group.s_addr)&0xf0000000)!=0xe0000000)
  throw std::invalid_argument("Group must be a multicast IPv4 address");
 if(inet_pton(AF_INET,cfg.local_interface?cfg.local_interface:"0.0.0.0",&local)!=1)
  throw std::invalid_argument("Interface must be an IPv4 address");
 socket_type fd=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);
 if(fd==bad_socket)throw std::runtime_error("UDP socket creation failed");
 try {
  int yes=1;
  if(setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,reinterpret_cast<const char*>(&yes),sizeof(yes))!=0)
   throw std::runtime_error("SO_REUSEADDR failed");
  sockaddr_in address{};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_ANY);
  address.sin_port=htons(static_cast<uint16_t>(cfg.port));
  if(bind(fd,reinterpret_cast<const sockaddr*>(&address),sizeof(address))!=0)
   throw std::runtime_error("UDP bind failed on port "+std::to_string(cfg.port));
  ip_mreq membership{};membership.imr_multiaddr=group;membership.imr_interface=local;
  if(setsockopt(fd,IPPROTO_IP,IP_ADD_MEMBERSHIP,reinterpret_cast<const char*>(&membership),sizeof(membership))!=0)
   throw std::runtime_error("Multicast join failed; verify the local interface");
  return fd;
 } catch(...) {close_socket(fd);throw;}
}
static void decode(ke2_feed_context& c,Exchange exchange,const uint8_t* data,size_t length) {
 std::lock_guard<std::mutex> lock(c.decode);
 ++c.datagrams;c.last_ms=now_ms();
 process_packet(exchange,data,length);
}
static void receive_loop(ke2_feed_context* c,Stream stream) {
 std::vector<uint8_t> buffer(65536);
 while(c->running){
  fd_set readfds;FD_ZERO(&readfds);FD_SET(stream.fd,&readfds);
  timeval timeout{0,200000};
  int ready=select(static_cast<int>(stream.fd)+1,&readfds,nullptr,nullptr,&timeout);
  if(!c->running)break;
  if(ready==0)continue;
  if(ready<0){++c->socket_errors;continue;}
  int bytes=static_cast<int>(recvfrom(stream.fd,reinterpret_cast<char*>(buffer.data()),
      static_cast<int>(buffer.size()),0,nullptr,nullptr));
  if(bytes<=0){++c->socket_errors;continue;}
  try {decode(*c,stream.exchange,buffer.data(),static_cast<size_t>(bytes));}
  catch(...){++c->socket_errors;}
 }
}
static void stop(ke2_feed_context& c) {
 c.running=false;
 for(auto& worker:c.workers)if(worker.joinable())worker.join();
 c.workers.clear();
 for(const auto& stream:c.sockets)close_socket(stream.fd);
 c.sockets.clear();
}
extern "C" {
uint32_t KE2_CALL ke2_feed_abi_version(void){return 1;}
const char* KE2_CALL ke2_feed_last_error(void){return error_text.c_str();}
int32_t KE2_CALL ke2_feed_create(const char* nse,const char* bse,
 ke2_feed_callback callback,void* user,ke2_feed_handle* out) {
 return protect([&]()->int32_t{
  if(!out || !callback)return fail(KE2_INVALID_ARGUMENT,"Callback and output handle required");
  *out=nullptr;
  if(instance)return fail(KE2_INVALID_STATE,"Only one feed context per process is supported");
  auto masters=load_masters(nse,bse);
  auto c=std::make_unique<ke2_feed_context>();c->callback=callback;c->user=user;
#ifdef _WIN32
  WSADATA data{};
  if(WSAStartup(MAKEWORD(2,2),&data)!=0)return fail(KE2_IO_ERROR,"WSAStartup failed");
  c->wsa=true;
#endif
  commit_masters(*c,std::move(masters));
  instance=std::move(c);*out=instance.get();return KE2_OK;
 });
}
int32_t KE2_CALL ke2_feed_reload_masters(ke2_feed_handle h,const char* nse,const char* bse) {
 return protect([&]()->int32_t{
  if(!valid(h))return fail(KE2_INVALID_ARGUMENT,"Invalid handle");
  if(h->running)return fail(KE2_INVALID_STATE,"Stop feed before reloading masters");
  auto masters=load_masters(nse,bse);commit_masters(*h,std::move(masters));return KE2_OK;
 });
}
int32_t KE2_CALL ke2_feed_instruments(ke2_feed_handle h,uint32_t exchange,
 ke2_feed_callback callback,void* user) {
 return protect([&]()->int32_t{
  if(!valid(h) || !callback || exchange>KE2_BSE_EQ)return fail(KE2_INVALID_ARGUMENT,"Invalid handle/callback/exchange");
  for(const auto& [e,json]:h->instruments)
   if(!exchange || e==exchange)invoke(*h,callback,user,json);
  return KE2_OK;
 });
}
int32_t KE2_CALL ke2_feed_process_datagram(ke2_feed_handle h,uint32_t exchange,
 const uint8_t* data,uint32_t length) {
 return protect([&]()->int32_t{
  if(!valid(h) || !data || !length || length>65535)return fail(KE2_INVALID_ARGUMENT,"Invalid handle/buffer/length");
  if(h->running)return fail(KE2_INVALID_STATE,"Cannot inject packets while live input is running");
  decode(*h,exchange_of(exchange),data,length);return KE2_OK;
 });
}
int32_t KE2_CALL ke2_feed_start(ke2_feed_handle h,const ke2_feed_stream* streams,uint32_t count) {
 return protect([&]()->int32_t{
  if(!valid(h))return fail(KE2_INVALID_ARGUMENT,"Invalid handle");
  if(h->running)return fail(KE2_INVALID_STATE,"Feed already running");
  const ke2_feed_stream defaults[]={
   {sizeof(ke2_feed_stream),KE2_NSE_FO,"233.1.2.5","0.0.0.0",34330},
   {sizeof(ke2_feed_stream),KE2_BSE_FO,"233.1.2.4","0.0.0.0",2004},
   {sizeof(ke2_feed_stream),KE2_BSE_EQ,"233.1.2.3","0.0.0.0",2003}};
  if(!streams && !count){streams=defaults;count=3;}
  if(!streams || !count || count>16)return fail(KE2_INVALID_ARGUMENT,"Expected 1..16 streams");
  try {
   for(uint32_t i=0;i<count;++i){
    if((streams[i].exchange==KE2_NSE_FO && TOKEN_MAP.empty()) ||
       (streams[i].exchange==KE2_BSE_FO && BSE_TOKEN_MAP.empty()))
     throw std::invalid_argument("Requested exchange has no loaded master");
    for(uint32_t j=0;j<i;++j)
     if(streams[i].port==streams[j].port &&
        streams[i].multicast_group && streams[j].multicast_group &&
        std::strcmp(streams[i].multicast_group,streams[j].multicast_group)==0)
      throw std::invalid_argument("Duplicate multicast endpoint");
    h->sockets.push_back({make_socket(streams[i]),exchange_of(streams[i].exchange)});
   }
   h->running=true;
   for(const auto& stream:h->sockets)h->workers.emplace_back(receive_loop,h,stream);
  } catch(const std::invalid_argument&){stop(*h);throw;}
  catch(const std::exception& e){stop(*h);return fail(KE2_IO_ERROR,e.what());}
  return KE2_OK;
 });
}
int32_t KE2_CALL ke2_feed_stop(ke2_feed_handle h) {
 return protect([&]()->int32_t{
  if(!valid(h))return fail(KE2_INVALID_ARGUMENT,"Invalid handle");
  stop(*h);return KE2_OK;
 });
}
int32_t KE2_CALL ke2_feed_get_stats(ke2_feed_handle h,ke2_feed_stats* out) {
 return protect([&]()->int32_t{
  if(!valid(h) || !out || out->struct_size!=sizeof(ke2_feed_stats))
   return fail(KE2_INVALID_ARGUMENT,"Invalid handle/stats struct size");
  *out={sizeof(ke2_feed_stats),h->running?1u:0u,h->datagrams.load(),h->events.load(),
    h->socket_errors.load(),h->callback_errors.load(),h->last_ms.load()};return KE2_OK;
 });
}
int32_t KE2_CALL ke2_feed_destroy(ke2_feed_handle h) {
 return protect([&]()->int32_t{
  if(!valid(h))return fail(KE2_INVALID_ARGUMENT,"Invalid handle");
  stop(*h);
#ifdef _WIN32
  if(h->wsa)WSACleanup();
#endif
  instance.reset();TOKEN_MAP.clear();BSE_TOKEN_MAP.clear();return KE2_OK;
 });
}
}
