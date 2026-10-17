// ================================
// main.cpp
// ================================
#include "parser.h"

#include <iostream>
#include <fstream>
#include <sstream>
#include <vector>
#include <unordered_map>

#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>

std::unordered_map<uint32_t,std::string> TOKEN_MAP;

std::vector<std::string> split(std::string s)
{
    std::vector<std::string> out;
    std::string cur;

    for(char c:s)
    {
        if(c==',')
        {
            out.push_back(cur);
            cur.clear();
        }
        else cur.push_back(c);
    }

    out.push_back(cur);
    return out;
}

bool load_contract(std::string file)
{
    std::ifstream f(file);
    if(!f.is_open()) return false;

    std::string line;
    getline(f,line);

    auto hdr=split(line);

    int tok=-1,sym=-1;

    for(int i=0;i<hdr.size();i++)
    {
        if(hdr[i]=="FinInstrmId") tok=i;
        if(hdr[i]=="StockNm") sym=i;
    }

    while(getline(f,line))
    {
        auto c=split(line);
        if(c.size()<=std::max(tok,sym)) continue;

        TOKEN_MAP[std::stoul(c[tok])] = c[sym];
    }

    std::cout<<"Loaded "<<TOKEN_MAP.size()<<" tokens\n";
    return true;
}

int main()
{
    if(!load_contract("NSE_FO.csv"))
    {
        std::cout<<"contract fail\n";
        return 0;
    }

    int sock = socket(AF_INET,SOCK_DGRAM,0);

    int reuse=1;
    setsockopt(sock,SOL_SOCKET,SO_REUSEADDR,&reuse,sizeof(reuse));

    sockaddr_in addr{};
    addr.sin_family=AF_INET;
    addr.sin_port=htons(34330);
    addr.sin_addr.s_addr=INADDR_ANY;

    bind(sock,(sockaddr*)&addr,sizeof(addr));

    ip_mreq mreq{};
    mreq.imr_multiaddr.s_addr=inet_addr("233.1.2.5");
    mreq.imr_interface.s_addr=INADDR_ANY;

    setsockopt(sock,IPPROTO_IP,IP_ADD_MEMBERSHIP,&mreq,sizeof(mreq));

    std::cout<<"Listening...\n";

    uint8_t buf[2048];

    while(true)
    {
        int n=recv(sock,buf,sizeof(buf),0);
        if(n>0) process_packet(buf,n);
    }
}