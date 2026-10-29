"""Build the existing indicative server for isolated portable-runtime ports."""
from pathlib import Path
import re
import subprocess
ROOT=Path(__file__).resolve().parents[1]
def main():
    text=(ROOT/"indicative_close_server/server.cpp").read_text()
    text="#include <cstdlib>\n"+text
    text=text.replace('constexpr const char* HTTP_BIND = "0.0.0.0";',
        'const char* HTTP_BIND = std::getenv("INDICATIVE_HTTP_HOST") ? std::getenv("INDICATIVE_HTTP_HOST") : "127.0.0.1";')
    text=text.replace("constexpr int HTTP_PORT = 8100;",
        'const int HTTP_PORT = std::getenv("INDICATIVE_HTTP_PORT") ? std::atoi(std::getenv("INDICATIVE_HTTP_PORT")) : 8100;')
    head,tail=text.split("    static void http_loop()",1)
    tail=tail.replace("htonl(\n                INADDR_ANY\n            )","inet_addr(HTTP_BIND)",1)
    text=head+"    static void http_loop()"+tail
    text=re.sub(r'inet_addr\(\s*"0\.0\.0\.0"\s*\)',
        'inet_addr(std::getenv("FEED_LOCAL_IP") ? std::getenv("FEED_LOCAL_IP") : "0.0.0.0")',text)
    target=ROOT/"standalone/build/indicative/server_linux.cpp"
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
    subprocess.run(["g++","-std=c++17","-O2",str(target),"-llzo2","-pthread","-o",
        str(ROOT/"standalone/native/indicative_close")],check=True)
if __name__=="__main__":main()
