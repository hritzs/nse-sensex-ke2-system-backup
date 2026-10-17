#include <iostream>
#include <cstring>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>

int main() {
    const char* group = "233.1.2.5";
    int port = 34330;
    const char* message = "DUMMY_C++_PACKET";

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        std::cerr << "Failed to create socket\n";
        return 1;
    }

    struct sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = inet_addr(group);
    addr.sin_port = htons(port);

    std::cout << "Sending dummy C++ multicast packet to " << group << ":" << port << "...\n";
    
    ssize_t n = sendto(sock, message, strlen(message), 0, (struct sockaddr*)&addr, sizeof(addr));
    if (n < 0) {
        std::cerr << "Failed to send packet\n";
    } else {
        std::cout << "Packet sent successfully! Check your Docker container logs.\n";
    }

    close(sock);
    return 0;
}
