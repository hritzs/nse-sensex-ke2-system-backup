"""Cross-build the existing indicative calculation server with a Winsock shim.

Only transport/platform/configuration text is adapted; original calculations
and embedded UI are copied from the source and fingerprinted in the receipt.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
def generate():
    original=ROOT/"indicative_close_server/server.cpp"
    text=original.read_text()
    header = """#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <winsock2.h>
#include <ws2tcpip.h>
#include <cstdlib>
#include <ctime>
using socklen_t = int;

static int portable_setsockopt(SOCKET s,int level,int opt,const void* p,int n) {
    return ::setsockopt(s,level,opt,static_cast<const char*>(p),n);
}
static int portable_recv(SOCKET s,void* p,int n,int flags) {
    return ::recv(s,static_cast<char*>(p),n,flags);
}
static tm* portable_gmtime(const time_t* t,tm* out) { return gmtime_s(out,t)==0?out:nullptr; }
#define setsockopt portable_setsockopt
#define recv portable_recv
#define close closesocket
#define gmtime_r portable_gmtime
"""
    for line in ("#include <arpa/inet.h>","#include <netinet/in.h>","#include <unistd.h>"):
        text=text.replace(line,"")
    text=header+text
    for old,new in (
        ("const int sock =","const SOCKET sock ="),
        ("const int server =","const SOCKET server ="),
        ("int client =","SOCKET client ="),
        ("int client,","SOCKET client,"),
        ("sock < 0","sock == INVALID_SOCKET"),
        ("server < 0","server == INVALID_SOCKET"),
        ("client < 0","client == INVALID_SOCKET")):
        text=text.replace(old,new)
    text=text.replace('constexpr const char* HTTP_BIND = "0.0.0.0";',
        'const char* HTTP_BIND = std::getenv("INDICATIVE_HTTP_HOST") ? std::getenv("INDICATIVE_HTTP_HOST") : "127.0.0.1";')
    text=text.replace("constexpr int HTTP_PORT = 8100;",
        'const int HTTP_PORT = std::getenv("INDICATIVE_HTTP_PORT") ? std::atoi(std::getenv("INDICATIVE_HTTP_PORT")) : 8100;')
    # Bind the HTTP listener to the chosen host, leaving multicast INADDR_ANY.
    head,tail=text.split("    static void http_loop()",1)
    tail=tail.replace("htonl(\n                INADDR_ANY\n            )","inet_addr(HTTP_BIND)",1)
    text=head+"    static void http_loop()"+tail
    text=re.sub(r'inet_addr\(\s*"0\.0\.0\.0"\s*\)',
        'inet_addr(std::getenv("FEED_LOCAL_IP") ? std::getenv("FEED_LOCAL_IP") : "0.0.0.0")',text)
    text=text.replace("int main()\n{","int main()\n{\n    WSADATA winsock; if (WSAStartup(MAKEWORD(2,2), &winsock)) return 2;")
    # Complete HTTP response writes; send() may return fewer bytes than requested.
    old="""        send(
            client,
            response.data(),
            response.size(),
            0
        );"""
    new="""        size_t offset=0;
        while (offset<response.size()) {
            int n=send(client,response.data()+offset,static_cast<int>(response.size()-offset),0);
            if(n<=0) break;
            offset+=static_cast<size_t>(n);
        }"""
    if old not in text:raise RuntimeError("Indicative source transport changed; review adaptation")
    text=text.replace(old,new)
    build=ROOT/"standalone/build/indicative"
    build.mkdir(parents=True,exist_ok=True)
    generated=build/"server_windows.cpp"
    generated.write_text(text)
    output=ROOT/"standalone/native/indicative_close.exe"
    command=["x86_64-w64-mingw32-g++","-std=c++17","-O2","-static","-static-libgcc",
        "-static-libstdc++",str(generated),"-I"+str(ROOT/"build-sdk-windows/lzo-install/include"),
        str(ROOT/"build-sdk-windows/lzo-install/lib/liblzo2.a"),"-lws2_32","-o",str(output)]
    subprocess.run(command,check=True)
    receipt={"original_sha256":hashlib.sha256(original.read_bytes()).hexdigest(),
        "generated_sha256":hashlib.sha256(generated.read_bytes()).hexdigest(),
        "exe_sha256":hashlib.sha256(output.read_bytes()).hexdigest(),"command":command}
    (build/"receipt.json").write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2))
if __name__=="__main__":generate()
