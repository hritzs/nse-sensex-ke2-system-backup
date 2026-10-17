#pragma once
#include <cstdint>
#include <string>

class MulticastReceiver {
public:
    MulticastReceiver(const std::string& group, uint16_t port);
    ~MulticastReceiver();
    bool start();
    int  receive(unsigned char* buf, size_t len);
    void stop();
private:
    std::string group_;
    uint16_t    port_;
    int         sock_;
    bool        started_;
};
