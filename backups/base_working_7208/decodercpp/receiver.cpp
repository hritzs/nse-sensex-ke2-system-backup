#include "receiver.h"
#include <arpa/inet.h>
#include <cstring>
#include <iostream>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include "parser.h"      // ← TickData defined here
#include "utils.h"       // ← only now_ms() here
#include "decompressor.h"

MulticastReceiver::MulticastReceiver(const std::string& group, uint16_t port)
    : group_(group), port_(port), sock_(-1), started_(false) {}

MulticastReceiver::~MulticastReceiver() { stop(); }

bool MulticastReceiver::start() {
    if (started_) return true;

    sock_ = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (sock_ < 0) { std::cerr << "socket() failed\n"; return false; }

    int reuse = 1;
    ::setsockopt(sock_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(port_);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);

    if (::bind(sock_, (sockaddr*)&addr, sizeof(addr)) < 0) {
        std::cerr << "bind() failed\n";
        ::close(sock_); sock_ = -1; return false;
    }

    ip_mreq mreq{};
    mreq.imr_multiaddr.s_addr = ::inet_addr(group_.c_str());
    mreq.imr_interface.s_addr = htonl(INADDR_ANY);

    if (::setsockopt(sock_, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq)) < 0) {
        std::cerr << "IP_ADD_MEMBERSHIP failed\n";
        ::close(sock_); sock_ = -1; return false;
    }

    started_ = true;
    std::cout << "Joined multicast " << group_ << ":" << port_ << "\n";
    return true;
}

int MulticastReceiver::receive(unsigned char* buf, size_t len) {
    if (!started_ || sock_ < 0) return -1;
    return (int)::recv(sock_, buf, len, 0);
}

void MulticastReceiver::stop() {
    if (sock_ >= 0) { ::close(sock_); sock_ = -1; }
    started_ = false;
}
