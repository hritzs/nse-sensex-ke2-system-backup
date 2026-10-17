cmake_minimum_required(VERSION 3.16)

project(nse_decoder)
add_compile_definitions(DISABLE_KDB)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ==========================================
# Include Paths
# ==========================================
include_directories(
    include
    /opt/uWebSockets/src
    /opt/uWebSockets/uSockets/src
)

# ==========================================
# Main Live Feed + Quant Analytics Engine
# ==========================================
add_executable(nse_decoder
    src/main.cpp
    src/contract_loader.cpp
    src/decoder.cpp
    src/parser_7208.cpp
    src/writer.cpp
    src/ws_server.cpp
    src/expiry_engine.cpp
    src/bs.cpp
    src/iv.cpp
    src/greeks.cpp
    src/symbol_parser.cpp
    src/analytics_engine.cpp
)

target_link_libraries(
    nse_decoder
    pthread
    ssl
    crypto
    z
    lzo2
    m
    hiredis
    zmq
    /opt/uWebSockets/uSockets/uSockets.a
)

# ==========================================
# ZeroMQ Ingestion Feed Handler
# ==========================================
add_executable(feed_handler src/services/feed_handler/feed_handler.cpp)
target_link_libraries(
    feed_handler
    pthread
    zmq
)

# ==========================================
# Quant Math Engine Test
# ==========================================
add_executable(test_engine
    src/test_engine.cpp
    src/bs.cpp
    src/iv.cpp
    src/greeks.cpp
)

target_include_directories(test_engine PRIVATE include)

target_link_libraries(
    test_engine
    m
)

# ==========================================
# Symbol Parser Test
# ==========================================
add_executable(test_symbol_parser
    src/test_symbol_parser.cpp
    src/symbol_parser.cpp
)

target_include_directories(test_symbol_parser PRIVATE include)

target_link_libraries(
    test_symbol_parser
    m
)