// ================================
// parser.cpp
// ================================
#include "parser.h"
#include "decompressor.h"

#include <iostream>
#include <vector>
#include <cstring>

extern std::unordered_map<uint32_t,std::string> TOKEN_MAP;

static inline uint16_t be16(const uint8_t* p)
{
    return (p[0]<<8)|p[1];
}

static inline uint16_t le16(const uint8_t* p)
{
    return p[0]|(p[1]<<8);
}

static inline uint32_t le32(const uint8_t* p)
{
    return p[0] | (p[1]<<8) | (p[2]<<16) | (p[3]<<24);
}

void parse_7208(const uint8_t* b,size_t len)
{
    if(len<256) return;
    std::cout << "HDR40+: ";
    for(int i=40;i<50 && i<len;i++) printf("%02X ", b[i]);
    printf("\n");
    uint16_t recs = be16(b+40);
    if(recs==0 || recs>50) return;

    for(int r=0;r<recs;r++)
    {
        const uint8_t* rec = b+42+r*214;

        uint32_t token = (rec[0]<<24)|(rec[1]<<16)|(rec[2]<<8)|rec[3];
        uint16_t book  = be16(rec+4);

        if(book!=1 && book!=2) continue;
        if(!TOKEN_MAP.count(token)) continue;

        
        double ltp     = ((rec[12]<<24)|(rec[13]<<16)|(rec[14]<<8)|rec[15]) / 100.0;

        // MBP buffer starts offset 56
        const uint8_t* mbp = rec+56;

        uint32_t bidqty1 = (mbp[0]<<24)|(mbp[1]<<16)|(mbp[2]<<8)|mbp[3];
        double bid1 = ((mbp[4]<<24)|(mbp[5]<<16)|(mbp[6]<<8)|mbp[7]) / 100.0;

        uint32_t askqty1 = (mbp[60]<<24)|(mbp[61]<<16)|(mbp[62]<<8)|mbp[63];
        double ask1 = ((mbp[64]<<24)|(mbp[65]<<16)|(mbp[66]<<8)|mbp[67]) / 100.0;

        std::cout
            << TOKEN_MAP[token]
            << " TOKEN=" << token
            << " LTP=" << ltp
            << " BID1=" << bid1
            << " ASK1=" << ask1
            << "\n";
    }
}

void process_packet(const uint8_t* buf,size_t len)
{
     static bool once=false;

    if(!once)
    {
        once=true;
        std::cout<<"FIRST PACKET "<<len<<" bytes\n";

        for(size_t i=0;i<64 && i<len;i++)
            printf("%02X ",buf[i]);

        printf("\n");
    }
    if(len<6) return;

    uint16_t packets = be16(buf+2);

    size_t pos=4;

    for(int i=0;i<packets;i++)
    {
        if(pos+2>len) return;

        uint16_t comp = be16(buf+pos);
        pos+=2;

        if(pos+comp>len) return;

        std::vector<uint8_t> dec;

        if(comp==0)
        {
            return;
        }

        if(!decompress_lzo(buf+pos,comp,dec))
        {
            std::cout << "DECOMP FAIL len=" << comp << "\n";
            pos += comp;
            continue;
        }

        std::cout << "DECOMP OK size=" << dec.size() << "\n";

        pos+=comp;

        if(dec.size()<60) continue;

        // skip first 8 bytes
        const uint8_t* b = dec.data()+8;
        for(int i=8;i<24 && i<dec.size();i++) printf("%02X ", dec[i]);
        printf("\n");
        size_t blen = dec.size()-8;

        uint16_t tx = be16(b+10);
        std::cout << "TX=" << tx << "\n";
        if(tx==7208)
            parse_7208(b,blen);
    }
}
