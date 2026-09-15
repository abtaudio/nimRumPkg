cmake_minimum_required(VERSION 3.10)
project(nimRumAudioSource C)

set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -s -fvisibility=hidden")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -fPIC -Wno-unused-result -Wsign-compare")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -fstack-protector -Wformat -Werror=format-security")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -Wunused -Wextra -Werror -pedantic -Wall")

# Platform defines
add_definitions(-DLIB_PREZO_LINUX)

# Read nimRumPkg version from VERSION file
file(READ "${CMAKE_SOURCE_DIR}/../../VERSION" _PKG_VERSION_RAW)
string(STRIP "${_PKG_VERSION_RAW}" _PKG_VERSION)
string(REPLACE "." ";" _PKG_VERSION_LIST ${_PKG_VERSION})
list(GET _PKG_VERSION_LIST 0 NIM_RUM_PKG_VERSION_MAJOR)
list(GET _PKG_VERSION_LIST 1 NIM_RUM_PKG_VERSION_MINOR)
list(GET _PKG_VERSION_LIST 2 NIM_RUM_PKG_VERSION_PATCH)
add_definitions(
    -DNIM_RUM_PKG_VERSION_MAJOR=${NIM_RUM_PKG_VERSION_MAJOR}
    -DNIM_RUM_PKG_VERSION_MINOR=${NIM_RUM_PKG_VERSION_MINOR}
    -DNIM_RUM_PKG_VERSION_PATCH=${NIM_RUM_PKG_VERSION_PATCH}
)
message(STATUS "nimRumPkg version: ${_PKG_VERSION}")

# Install directories
if(NOT TARGET_ARCH)
    set(TARGET_ARCH ${CMAKE_SYSTEM_PROCESSOR})
endif()
set(INSTALL_DIR ${CMAKE_SOURCE_DIR}/../../RESULT/${TARGET_ARCH})
set(INSTALL_DIR_SO ${INSTALL_DIR}/nimRum)

# audioSource sources
file(GLOB ASRC_MAIN src/*.c)
file(GLOB ASRC_CAPTURE src/capture/*.c)
set(ASRC ${ASRC_MAIN} ${ASRC_CAPTURE})

# Build nimRumAudioSource_ct.so (ctypes — no Python dependency)
add_library(nimRumAudioSource_ct SHARED
    pyLib/nimRumAudioSource_ctypes.c
    ${ASRC}
)

target_include_directories(nimRumAudioSource_ct PRIVATE
    src
    src/capture
)

target_link_libraries(nimRumAudioSource_ct
    m pthread sndfile asound avcodec avutil avformat
)

set_target_properties(nimRumAudioSource_ct PROPERTIES PREFIX "")

install(TARGETS nimRumAudioSource_ct DESTINATION ${INSTALL_DIR_SO})

# Test binary
add_executable(nimRumAudioSourceTest test/nimRumAudioSourceTest.c ${ASRC})
target_include_directories(nimRumAudioSourceTest PRIVATE src src/capture)
target_link_libraries(nimRumAudioSourceTest m pthread sndfile asound avcodec avutil avformat)
install(TARGETS nimRumAudioSourceTest DESTINATION ${INSTALL_DIR})
