#!/usr/bin/env bash
# FrameKeep - (C) 2025 Tanner Lucier
# Licensed under the MIT License
#
# Builds the minimal FFmpeg that FrameKeep ships: only the inputs, filters, codecs and containers FrameKeep
# uses, with every external library linked statically into the FFmpeg DLLs (no extra runtime DLLs).
# Runs in an MSYS2 UCRT64 shell on Windows (locally or in .github/workflows/ffmpeg.yml):
#
#   bash tools/build_ffmpeg.sh [--deps] [WORK_DIR]
#
# --deps installs the toolchain and libraries with pacman first. The result is
# WORK_DIR/dist/framekeep-ffmpeg-<FF_VERSION>-<FK_REV>-win64.zip, laid out like the upstream builds
# (<name>/bin/*.exe|dll, <name>/LICENSE) so tools/fetch_ffmpeg.py can install it.

set -euo pipefail

FF_VERSION=9.0.2
FF_SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
VVENC_TAG=v1.14.0
VMAF_TAG=v3.2.1
FK_REV=fk3   # bump when the configure line or a library changes without a new FFmpeg version

if [ "${1:-}" = "--deps" ]; then
    shift
    pacman -S --needed --noconfirm make git diffutils patch tar zip vim \
        mingw-w64-ucrt-x86_64-{gcc,nasm,pkgconf,cmake,ninja,meson} \
        mingw-w64-ucrt-x86_64-{x264,x265,svt-av1,aom,dav1d,SDL2,libvpl,amf-headers,ffnvcodec-headers}
fi
[ "${MSYSTEM:-}" = "UCRT64" ] || { echo "Run this from an MSYS2 UCRT64 shell"; exit 1; }
PATCHES=$(dirname "$(realpath "$0")")/patches

WORK=$(realpath -m "${1:-build/ffmpeg}")
PREFIX=$WORK/prefix
NAME=framekeep-ffmpeg-$FF_VERSION-$FK_REV-win64
JOBS=$(nproc)
mkdir -p "$WORK" "$PREFIX"
cd "$WORK"

# --- VVenC (not packaged by MSYS2) ---------------------------------------------------------------
if [ ! -f "$PREFIX/lib/pkgconfig/libvvenc.pc" ]; then
    rm -rf vvenc
    git clone --depth 1 --branch "$VVENC_TAG" https://github.com/fraunhoferhhi/vvenc.git vvenc
    cmake -S vvenc -B vvenc/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" \
        -DBUILD_SHARED_LIBS=OFF -DVVENC_LIBRARY_ONLY=ON -DVVENC_ENABLE_LINK_TIME_OPT=OFF -DVVENC_ENABLE_WERROR=OFF
    cmake --build vvenc/build -j "$JOBS"
    cmake --install vvenc/build
fi

# --- libvmaf (MSYS2's package has no built-in models, which the benchmark's default libvmaf call needs) ---
if [ ! -f "$PREFIX/lib/pkgconfig/libvmaf.pc" ]; then
    rm -rf vmaf
    git clone --depth 1 --branch "$VMAF_TAG" https://github.com/Netflix/vmaf.git vmaf
    meson setup vmaf/libvmaf vmaf/build --prefix="$PREFIX" --libdir=lib --buildtype=release --default-library=static \
        -Dbuilt_in_models=true -Denable_tests=false -Denable_docs=false
    meson compile -C vmaf/build
    meson install -C vmaf/build
fi

# --- FFmpeg --------------------------------------------------------------------------------------
SRC=ffmpeg-$FF_VERSION
if [ ! -d "$SRC" ]; then
    curl -fsSLo "$SRC.tar.xz" "https://ffmpeg.org/releases/$SRC.tar.xz"
    echo "$FF_SHA256  $SRC.tar.xz" | sha256sum -c -
    tar xf "$SRC.tar.xz"
    # FrameKeep's fixes on top of FFmpeg (each patch explains itself). Applied once, right after unpacking.
    for p in "$PATCHES"/*.patch; do
        patch -d "$SRC" -p1 < "$p"
    done
fi

# What FrameKeep uses (framekeep/engine.py, framekeep/benchmark.py, framekeep/app.py):
#   capture   lavfi ddagrab (D3D11) or gdigrab, testsrc2 for the encoder self-test
#   filters   hwdownload format mpdecimate scale setpts trim fps null libvmaf (benchmark)
#   encoders  x264 x265 SVT-AV1 aom VVenC, NVENC/QSV/AMF, ffv1 (benchmark sample), mjpeg (thumbnails)
#   decoders  its own outputs (h264 hevc av1 vvc) plus ffv1, for thumbnails, ffprobe, ffplay and the benchmark
#   files     mp4/mov, matroska, null, image2 (thumbnail .jpg)
# The ffmpeg/ffplay programs pull in the few extra filters they insert themselves (crop, transpose, ...).
ENCODERS=libx264,libx265,libsvtav1,libaom_av1,libvvenc
ENCODERS+=,h264_nvenc,hevc_nvenc,av1_nvenc,h264_qsv,hevc_qsv,av1_qsv,h264_amf,hevc_amf,av1_amf
ENCODERS+=,ffv1,mjpeg,rawvideo,wrapped_avframe
DECODERS=h264,hevc,vvc,libdav1d,ffv1,rawvideo,mjpeg,wrapped_avframe,bmp   # lavfi sends wrapped_avframe, gdigrab bmp
FILTERS=ddagrab,testsrc2,hwdownload,format,mpdecimate,scale,setpts,trim,fps,null,copy,libvmaf
PARSERS=h264,hevc,vvc,av1,mjpeg
BSFS=extract_extradata,h264_mp4toannexb,hevc_mp4toannexb,vvc_mp4toannexb,av1_frame_split,av1_frame_merge
MUXERS=mp4,mov,matroska,webm,null,image2
DEMUXERS=mov,matroska,image2

# Link the external libraries statically into the FFmpeg DLLs. ld prefers libfoo.dll.a over libfoo.a in the same
# folder, so put just the static archives in a folder searched first. libgcc_s (named by x265.pc) is an import
# library for libgcc_s_seh-1.dll; an empty stand-in leaves -static-libgcc to supply libgcc instead.
STATIC=$WORK/static-libs
rm -rf "$STATIC"
mkdir -p "$STATIC"
for lib in x264 x265 SvtAv1Enc aom dav1d SDL2 SDL2main vpl stdc++ pthread winpthread; do
    cp "/ucrt64/lib/lib$lib.a" "$STATIC/"
done
cp "$PREFIX"/lib/lib{vvenc,vmaf}.a "$STATIC/"
ar rcs "$STATIC/libgcc_s.a"

mkdir -p "$SRC/build"
cd "$SRC/build"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:/ucrt64/lib/pkgconfig"
../configure --prefix="$PREFIX" \
    --enable-gpl --enable-version3 --enable-shared --disable-static \
    --disable-debug --disable-doc --disable-network --disable-autodetect \
    --enable-w32threads --enable-d3d11va --enable-dxva2 --enable-ffnvcodec --enable-nvenc --enable-amf \
    --enable-libvpl --enable-sdl2 \
    --enable-libx264 --enable-libx265 --enable-libsvtav1 --enable-libaom --enable-libvvenc --enable-libdav1d \
    --enable-libvmaf \
    --disable-everything \
    --enable-indev=gdigrab,lavfi \
    --enable-encoder=$ENCODERS --enable-decoder=$DECODERS --enable-filter=$FILTERS \
    --enable-parser=$PARSERS --enable-bsf=$BSFS --enable-muxer=$MUXERS --enable-demuxer=$DEMUXERS \
    --enable-protocol=file,pipe \
    --pkg-config-flags=--static --extra-ldflags="-L$STATIC -static-libgcc -static-libstdc++" --extra-libs=-lstdc++ \
    --extra-version="FrameKeep-$FK_REV"
make -j "$JOBS"
make install
cd "$WORK"

# --- Package -------------------------------------------------------------------------------------
rm -rf "dist/$NAME" "dist/$NAME.zip"
mkdir -p "dist/$NAME/bin"
cp "$PREFIX"/bin/{ffmpeg,ffprobe,ffplay}.exe "$PREFIX"/bin/*.dll "dist/$NAME/bin/"
strip --strip-unneeded "dist/$NAME"/bin/*
cp "$SRC/COPYING.GPLv3" "dist/$NAME/LICENSE"
{
    echo "FFmpeg $FF_VERSION built for FrameKeep by tools/build_ffmpeg.sh ($FK_REV), VVenC $VVENC_TAG, libvmaf $VMAF_TAG"
    echo "Source: https://ffmpeg.org/releases/$SRC.tar.xz and https://github.com/fraunhoferhhi/vvenc/tree/$VVENC_TAG"
    echo
    "dist/$NAME/bin/ffmpeg.exe" -hide_banner -buildconf
    echo
    echo "MSYS2 libraries:"
    pacman -Q | grep -E 'ucrt-x86_64-(x264|x265|svt-av1|aom|dav1d|SDL2|libvpl|amf-headers|ffnvcodec-headers|gcc) '
} > "dist/$NAME/BUILDINFO.txt"

# Refuse to publish a build that picked up a runtime DLL dependency beyond Windows itself
deps=$(objdump -p "dist/$NAME"/bin/* | awk '/DLL Name:/ {print $3}' | sort -u)
extra=$(echo "$deps" | grep -viE '^(av|sw)[a-z]*-[0-9]+\.dll$|^(kernel32|user32|gdi32|advapi32|shell32|ole32|oleaut32|ws2_32|bcrypt|imm32|winmm|version|setupapi|cfgmgr32|shlwapi|dxgi|d3d11|d3d9|dxva2|psapi|secur32|mfplat|mfuuid|crypt32|ntdll|uuid|comdlg32|hid|msvcrt)\.dll$|^api-ms-win-' || true)
[ -z "$extra" ] || { echo "Unexpected DLL dependencies:"; echo "$extra"; exit 1; }

(cd dist && zip -qr9 "$NAME.zip" "$NAME")
sha256sum "dist/$NAME.zip"
du -sh "dist/$NAME/bin"
