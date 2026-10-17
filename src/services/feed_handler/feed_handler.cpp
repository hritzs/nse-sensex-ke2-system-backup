#include "config.h"
#include "decoder.h"
#include <iostream>
#include <vector>
#include <thread>
#include <cstring>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <zmq.h>

#include "config.h"

// Function to bind to Exchange UDP and push raw bytes to ZeroMQ
void multicast_to_zmq(Exchange exchange, const char* ip, int port, void* zmq_publisher) {
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) return;

    int reuse = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    bind(sock, (sockaddr*)&addr, sizeof(addr));

    ip_mreq mreq{};
    mreq.imr_multiaddr.s_addr = inet_addr(ip);
    mreq.imr_interface.s_addr = htonl(INADDR_ANY);
    setsockopt(sock, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq));

    std::cout << "[FEED HANDLER] Bound to Exchange Multicast: " << ip << ":" << port << std::endl;
    std::vector<uint8_t> buf(Config::MAX_PACKET);

    while (true) {
        ssize_t n = recv(sock, buf.data(), buf.size(), 0);
        if (n > 0) {
            // Format ZMQ Payload: [1-Byte Exchange ID] + [Raw Binary Packet]
            zmq_msg_t msg;
            zmq_msg_init_size(&msg, n + 1);
            uint8_t* data = (uint8_t*)zmq_msg_data(&msg);
            
            data[0] = static_cast<uint8_t>(exchange);
            std::memcpy(data + 1, buf.data(), n);

            zmq_msg_send(&msg, zmq_publisher, 0);
            zmq_msg_close(&msg);
        }
    }
    close(sock);
}

int main() {
    std::cout << "=== STARTING DALAL ZMQ FEED HANDLER ===" << std::endl;
    
    // Initialize ZeroMQ Context and Publisher Socket
    void* context = zmq_ctx_new();
    void* publisher = zmq_socket(context, ZMQ_PUB);
    
    // Bind Publisher to Port 5555 for internal cluster communication
    if (zmq_bind(publisher, "tcp://*:5555") != 0) {
        std::cerr << "[ERROR] Failed to bind ZMQ publisher on port 5555" << std::endl;
        return 1;
    }
    std::cout << "[ZMQ] PUB Socket live on tcp://*:5555" << std::endl;

    std::thread nse_thread(multicast_to_zmq, Exchange::NSE, Config::NSE_MULTICAST_IP, Config::NSE_PORT, publisher);
    std::thread bse_thread(multicast_to_zmq, Exchange::BSE, Config::BSE_MULTICAST_IP, Config::BSE_PORT, publisher);

    nse_thread.join();
    bse_thread.join();
    return 0;
}