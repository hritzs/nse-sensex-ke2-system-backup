cmake_minimum_required(VERSION 3.10)

project(nse7208)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_library(LZO_LIB lzo2 REQUIRED)

add_executable(nse7208
    decodercpp/main.cpp
    decodercpp/parser.cpp
    decodercpp/decompressor.cpp
)

target_link_libraries(nse7208 ${LZO_LIB})