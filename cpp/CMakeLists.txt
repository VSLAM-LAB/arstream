cmake_minimum_required(VERSION 3.20)
project(arstream VERSION 0.1.0 LANGUAGES CXX)

# Header-only subscriber (include/arstream/arstream.hpp). Consumers:
#   find_package(arstream REQUIRED)
#   target_link_libraries(<target> PRIVATE arstream::arstream)
# OpenCV is a dependency of the consumer, resolved by find_dependency(OpenCV) in arstreamConfig.cmake, so the
# package itself carries no pin on an OpenCV version; it is only looked up here to build the smoke test.
option(ARSTREAM_BUILD_TESTS "Build the subscriber smoke test" ON)
find_package(cppzmq REQUIRED)
find_package(nlohmann_json REQUIRED)
if(ARSTREAM_BUILD_TESTS)
    find_package(OpenCV REQUIRED COMPONENTS core imgcodecs imgproc)
endif()

add_library(arstream INTERFACE)
add_library(arstream::arstream ALIAS arstream)
target_compile_features(arstream INTERFACE cxx_std_17)
target_include_directories(arstream INTERFACE
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>)
target_link_libraries(arstream INTERFACE cppzmq nlohmann_json::nlohmann_json opencv_core opencv_imgcodecs opencv_imgproc)

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(TARGETS arstream EXPORT arstreamTargets)
install(EXPORT arstreamTargets NAMESPACE arstream:: DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/arstream)
configure_package_config_file(cmake/arstreamConfig.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/arstreamConfig.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/arstream)
write_basic_package_version_file(${CMAKE_CURRENT_BINARY_DIR}/arstreamConfigVersion.cmake COMPATIBILITY SameMajorVersion)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/arstreamConfig.cmake ${CMAKE_CURRENT_BINARY_DIR}/arstreamConfigVersion.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/arstream)

# Compile check of the header (not part of the conda package, see cpp/pixi.toml)
if(ARSTREAM_BUILD_TESTS)
    add_executable(arstream_subscribe test/subscribe.cpp)
    target_link_libraries(arstream_subscribe PRIVATE arstream::arstream)
    install(TARGETS arstream_subscribe RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif()
