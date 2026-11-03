cmake_minimum_required(VERSION 3.16)
project(sd_protocol LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(SD_COVERAGE "Build with gcov instrumentation" OFF)

add_library(sd_protocol
  src/frame.cpp
  src/hmac.cpp
  src/seq_window.cpp
  src/setpoint.cpp
  src/version.cpp
)
target_include_directories(sd_protocol PUBLIC include PRIVATE src)
# Same limits as the ESP8266 build: no exceptions, no RTTI.
target_compile_options(sd_protocol PRIVATE -Wall -Wextra -Wpedantic -fno-exceptions -fno-rtti)
if(SD_COVERAGE)
  target_compile_options(sd_protocol PUBLIC --coverage -O0)
  target_link_options(sd_protocol PUBLIC --coverage)
endif()

enable_testing()
# CI installs libgtest-dev. Without it (a developer PC), fetch the same release.
find_package(GTest QUIET)
if(NOT GTest_FOUND)
  include(FetchContent)
  FetchContent_Declare(googletest
    URL https://github.com/google/googletest/archive/refs/tags/v1.14.0.tar.gz)
  FetchContent_MakeAvailable(googletest)
  add_library(GTest::gtest_main ALIAS gtest_main)
endif()

add_executable(sd_protocol_tests
  tests/test_frame.cpp
  tests/test_hmac.cpp
  tests/test_seq_window.cpp
  tests/test_setpoint.cpp
  tests/test_version.cpp
)
target_link_libraries(sd_protocol_tests PRIVATE sd_protocol GTest::gtest_main)
add_test(NAME sd_protocol_tests COMMAND sd_protocol_tests)
