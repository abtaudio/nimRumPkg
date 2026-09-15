cmake_minimum_required(VERSION 3.10)
project(nimRumDSP C)

set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -s -fvisibility=hidden")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -fPIC -Wno-unused-result -Wsign-compare")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -fstack-protector -Wformat -Werror=format-security")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -Wunused -Wextra -Werror -pedantic -Wall")

# Install directories
if(NOT TARGET_ARCH)
    set(TARGET_ARCH ${CMAKE_SYSTEM_PROCESSOR})
endif()
set(INSTALL_DIR ${CMAKE_SOURCE_DIR}/../../RESULT/${TARGET_ARCH})
set(INSTALL_DIR_SO ${INSTALL_DIR}/nimRum)

# Build libnimRumDSP.so (plugin loaded by libNimRumRx via dlopen)
add_library(nimRumDSP SHARED
    src/nimRumDSP.c
    src/nimRumDSP_biquad.c
    src/nimRumDSP_config.c
)

target_include_directories(nimRumDSP PRIVATE src)
target_link_libraries(nimRumDSP m)

# Symbols are explicitly exported via NIM_RUM_DSP_EXPORT in nimRumDSP.h
set_target_properties(nimRumDSP PROPERTIES
    PREFIX "lib"
)

install(TARGETS nimRumDSP DESTINATION ${INSTALL_DIR_SO})

# Test executable (not installed)
add_executable(nimRumDSP_test test/nimRumDSP_test.c
    src/nimRumDSP.c
    src/nimRumDSP_biquad.c
    src/nimRumDSP_config.c
)
target_include_directories(nimRumDSP_test PRIVATE src)
target_link_libraries(nimRumDSP_test m)
