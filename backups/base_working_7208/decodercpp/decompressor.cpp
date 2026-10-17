#include "decompressor.h"
#include <lzo/lzo1z.h>

bool decompress_lzo(const uint8_t* in,size_t inlen,std::vector<uint8_t>& out)
{
    static bool init=false;

    if(!init)
    {
        if(lzo_init()!=LZO_E_OK) return false;
        init=true;
    }

    out.resize(65536);
    lzo_uint out_len = 65536;

    int r = lzo1z_decompress(
        in,
        (lzo_uint)inlen,
        out.data(),
        &out_len,
        nullptr
    );

    if(r != LZO_E_OK) return false;

    out.resize((size_t)out_len);
    return true;
}