#include "ke2_feed.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <winsock2.h>
#include <windows.h>
#define process_id GetCurrentProcessId
#else
#include <unistd.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#define process_id getpid
#endif
#define CHECK(x) do {if(!(x)){fprintf(stderr,"FAIL line %d: %s (%s)\n",__LINE__,#x,ke2_feed_last_error());return 1;}}while(0)
static unsigned free_udp_port(void) {
 struct sockaddr_in address;
#ifdef _WIN32
 SOCKET fd=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);int length=sizeof(address);
 if(fd==INVALID_SOCKET)return 0;
#else
 int fd=socket(AF_INET,SOCK_DGRAM,IPPROTO_UDP);socklen_t length=sizeof(address);
 if(fd<0)return 0;
#endif
 memset(&address,0,sizeof(address));address.sin_family=AF_INET;
 address.sin_addr.s_addr=htonl(INADDR_ANY);
 if(bind(fd,(const struct sockaddr*)&address,sizeof(address))!=0 ||
    getsockname(fd,(struct sockaddr*)&address,&length)!=0)address.sin_port=0;
#ifdef _WIN32
 closesocket(fd);
#else
 close(fd);
#endif
 return ntohs(address.sin_port);
}
static int events=0,metadata=0,reentry=0;
static char last[4096];
static ke2_feed_handle ctx=NULL;
static void KE2_CALL on_event(void* user,const char* data,uint32_t size) {
 (void)user;++events;
 if(size>=sizeof(last))abort();memcpy(last,data,size);last[size]=0;
 if(ctx && ke2_feed_stop(ctx)==KE2_INVALID_STATE)++reentry;
}
static void KE2_CALL on_master(void* user,const char* data,uint32_t size) {
 (void)user;
 if(!size || !strstr(data,"instrument_id"))abort();++metadata;
}
static void be16(unsigned char* p,unsigned v){p[0]=(unsigned char)(v>>8);p[1]=(unsigned char)v;}
static void be32(unsigned char* p,unsigned v){be16(p,v>>16);be16(p+2,v);}
static void put16(unsigned char* p,unsigned* pos,unsigned v){be16(p+*pos,v);*pos+=2;}
int main(void) {
 char nse[128],bse[128];FILE* f;ke2_feed_handle other=NULL;
 unsigned char packet[512]={0};unsigned pos=0,i;
 ke2_feed_stats stats={0};
 snprintf(nse,sizeof(nse),"sdk-nse-%lu.csv",(unsigned long)process_id());
 snprintf(bse,sizeof(bse),"sdk-bse-%lu.csv",(unsigned long)process_id());
 f=fopen(nse,"wb");CHECK(f);
 fputs("xx.Token,Exch,Type,Symbol,Expiry,Strike,OpType,Token\n123,NSE,FUTURE,NIFTY,03-NOV-26,0,XX,123\n",f);fclose(f);
 f=fopen(bse,"wb");CHECK(f);
 fputs("xx.Token,Exch,Type,Symbol,Expiry,Strike,OpType,Token\n456,BSE,FUTURE,SENSEX,29-OCT-26,0,,IO\n",f);fclose(f);
 CHECK(ke2_feed_abi_version()==1);
 CHECK(ke2_feed_create(nse,bse,on_event,NULL,&ctx)==KE2_OK);
 CHECK(ke2_feed_create(nse,bse,on_event,NULL,&other)==KE2_INVALID_STATE);
 CHECK(other==NULL);
 CHECK(ke2_feed_instruments(ctx,0,on_master,NULL)==KE2_OK && metadata==2);
 /* BSE 2020, one outright record, one depth level. */
 be32(packet,2020);be16(packet+26,1);be32(packet+28,456);
 be32(packet+28+12,123456); /* low word of traded quantity */
 be16(packet+78,1);be32(packet+96,10);be32(packet+100,10000);
 pos=104;for(i=0;i<12+9;++i)put16(packet,&pos,0);
 put16(packet,&pos,(unsigned short)-5);
 for(i=0;i<4;++i)put16(packet,&pos,0);
 put16(packet,&pos,5);for(i=0;i<4;++i)put16(packet,&pos,0);
 CHECK(ke2_feed_process_datagram(ctx,KE2_BSE_FO,packet,pos)==KE2_OK);
 CHECK(events==1 && strstr(last,"\"token\":456") && strstr(last,"\"bid\":99.95"));
 CHECK(reentry==1);
 CHECK(ke2_feed_process_datagram(ctx,KE2_BSE_EQ,packet,pos)==KE2_OK && events==1);
 CHECK(ke2_feed_process_datagram(ctx,KE2_BSE_FO,packet,pos-1)==KE2_OK && events==1);
 /* NSE uncompressed 7208 framing and one record. */
 memset(packet,0,sizeof(packet));be16(packet+2,1);
 {unsigned char* b=packet+14;
 be16(b+10,7208);be16(b+38,256);be16(b+40,1);
 be32(b+42,123);be16(b+46,1);
 be32(b+54,20000);be32(b+102,19995);be32(b+162,20005);}
 CHECK(ke2_feed_process_datagram(ctx,KE2_NSE_FO,packet,270)==KE2_OK);
 CHECK(events==2 && strstr(last,"\"exchange\":\"NSE\"") && strstr(last,"\"ltp\":200"));
 /* Fixed LZO1Z-compressed NSE fixture also exercises the Windows LZO build. */
 {static const unsigned char compressed[]={18,0,47,0,3,28,40,0,55,0,1,1,128,10,0,123,128,20,128,3,78,32,0,32,12,0,3,78,27,0,32,24,0,3,78,37,0,32,56,0,0,17,0,0};
 memset(packet,0,sizeof(packet));be16(packet+2,1);be16(packet+4,sizeof(compressed));
 memcpy(packet+6,compressed,sizeof(compressed));
 CHECK(ke2_feed_process_datagram(ctx,KE2_NSE_FO,packet,6+sizeof(compressed))==KE2_OK);
 CHECK(events==3 && strstr(last,"\"ltp\":200"));}
 /* Real index packet identity, not a made-up derivative instrument. */
 memset(packet,0,sizeof(packet));be32(packet,2011);be16(packet+26,1);
 be32(packet+28,1);be32(packet+48,7380000);memcpy(packet+52,"SENSEX ",7);
 CHECK(ke2_feed_process_datagram(ctx,KE2_BSE_EQ,packet,68)==KE2_OK);
 CHECK(events==4 && strstr(last,"market_index") && strstr(last,"73800"));
 CHECK(ke2_feed_reload_masters(ctx,"missing-master.csv",bse)!=KE2_OK);
 metadata=0;CHECK(ke2_feed_instruments(ctx,0,on_master,NULL)==KE2_OK && metadata==2);
 {ke2_feed_stream invalid={sizeof(ke2_feed_stream),KE2_BSE_FO,"127.0.0.1","0.0.0.0",2004};
 CHECK(ke2_feed_start(ctx,&invalid,1)==KE2_INVALID_ARGUMENT);}
 /* Transactional socket setup and repeated start/stop on private test groups.
    These are not the production multicast endpoints. */
 {ke2_feed_stream streams[2]={
  {sizeof(ke2_feed_stream),KE2_BSE_FO,"239.255.243.31","0.0.0.0",0},
  {sizeof(ke2_feed_stream),KE2_BSE_EQ,"127.0.0.1","0.0.0.0",0}};
 streams[0].port=free_udp_port();CHECK(streams[0].port);streams[1].port=streams[0].port;
 CHECK(ke2_feed_start(ctx,streams,2)==KE2_INVALID_ARGUMENT);
 for(i=0;i<2;++i){
  CHECK(ke2_feed_start(ctx,streams,1)==KE2_OK);
  CHECK(ke2_feed_reload_masters(ctx,nse,bse)==KE2_INVALID_STATE);
  CHECK(ke2_feed_process_datagram(ctx,KE2_BSE_FO,packet,68)==KE2_INVALID_STATE);
  CHECK(ke2_feed_start(ctx,streams,1)==KE2_INVALID_STATE);
  CHECK(ke2_feed_stop(ctx)==KE2_OK);
 }}
 stats.struct_size=sizeof(stats);
 CHECK(ke2_feed_get_stats(ctx,&stats)==KE2_OK && stats.events==4 && !stats.running);
 CHECK(ke2_feed_process_datagram(ctx,999,packet,68)==KE2_INVALID_ARGUMENT);
 CHECK(ke2_feed_stop(ctx)==KE2_OK);
 CHECK(ke2_feed_destroy(ctx)==KE2_OK);
 CHECK(ke2_feed_get_stats(ctx,&stats)==KE2_INVALID_ARGUMENT);
 ctx=NULL;
 CHECK(ke2_feed_create(nse,bse,on_event,NULL,&ctx)==KE2_OK);
 CHECK(ke2_feed_destroy(ctx)==KE2_OK);
 remove(nse);remove(bse);
 puts("SDK_SMOKE_OK NSE+BSE+INDEX+ATOMIC_RELOAD+LIFECYCLE");return 0;
}
