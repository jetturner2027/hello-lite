cmake_minimum_required(VERSION 3.24)
project(FaceUnlockCP LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# Static CRT: LogonUI must not depend on a VC++ redistributable being present.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

# No WIN32_LEAN_AND_MEAN: credentialprovider.h and the COM allocators need the full headers.
add_compile_definitions(UNICODE _UNICODE NOMINMAX _WIN32_WINNT=0x0A00 NTDDI_VERSION=0x0A000000)
add_compile_options(/W4 /WX /permissive- /EHsc /sdl /guard:cf /utf-8)
add_link_options(/guard:cf /DYNAMICBASE /NXCOMPAT)

set(CORE_SOURCES src/pipe_client.cpp src/cred_store.cpp src/kerb_pack.cpp src/scenario.cpp)
set(CORE_LIBS advapi32 bcrypt crypt32 secur32 credui shell32 shlwapi ole32 uuid wtsapi32 user32)

add_library(FaceUnlockCP SHARED
    ${CORE_SOURCES} src/auto_unlock.cpp src/trace.cpp src/credential.cpp src/provider.cpp src/dll.cpp src/faceunlock_cp.def)
target_link_libraries(FaceUnlockCP PRIVATE ${CORE_LIBS})

# Native tests for the parsing/packing code that LogonUI will run.
add_executable(cp_tests tests/cp_tests.cpp ${CORE_SOURCES})
target_include_directories(cp_tests PRIVATE src)
target_link_libraries(cp_tests PRIVATE ${CORE_LIBS})
