cmake_minimum_required(VERSION 3.16)
project(sd_protocol LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(SD_COVERAGE "Build with gcov instrumentation" OFF)

add_library(sd_protocol src/version.cpp)
target_include_directories(sd_protocol PUBLIC include)
target_compile_options(sd_protocol PRIVATE -Wall -Wextra -Wpedantic)
if(SD_COVERAGE)
  target_compile_options(sd_protocol PUBLIC --coverage -O0)
  target_link_options(sd_protocol PUBLIC --coverage)
endif()

enable_testing()
find_package(GTest REQUIRED)
add_executable(sd_protocol_tests tests/test_version.cpp)
target_link_libraries(sd_protocol_tests PRIVATE sd_protocol GTest::gtest_main)
add_test(NAME sd_protocol_tests COMMAND sd_protocol_tests)
