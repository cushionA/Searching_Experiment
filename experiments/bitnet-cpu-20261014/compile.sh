#!/bin/sh
# Inside the isolated container, with the two pinned source archives laid out as BitNet/3rdparty/llama.cpp.
set -eu
python3 utils/codegen_tl2.py --model bitnet_b1_58-3B --BM 160,320,320 --BK 96,96,96 --bm 32,32,32
cmake -B build -DBITNET_X86_TL2=OFF -DCMAKE_C_COMPILER=clang-18 -DCMAKE_CXX_COMPILER=clang++-18 \
  -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_EXAMPLES=ON -DLLAMA_BUILD_COMMON=ON \
  -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_UI=OFF -DLLAMA_BUILD_APP=OFF \
  -DLLAMA_BUILD_TESTS=OFF -DGGML_CUDA=OFF -DGGML_OPENMP=OFF
/usr/bin/time -v cmake --build build --target llama-server --config Release -j 4
