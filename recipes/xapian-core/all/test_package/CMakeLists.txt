cmake_minimum_required(VERSION 3.15)
project(test_package LANGUAGES CXX)

find_package(xapian REQUIRED CONFIG)

add_executable(${PROJECT_NAME} test_package.cpp)
target_include_directories(${PROJECT_NAME} PRIVATE ${XAPIAN_INCLUDE_DIR})
target_link_libraries(${PROJECT_NAME} PRIVATE ${XAPIAN_LIBRARIES})
