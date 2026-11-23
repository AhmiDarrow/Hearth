#!/bin/bash
# Builds Hearth's Linux package: Hearth-<version>-linux-x86_64.tar.gz and the program alone,
# Hearth_<version>_linux-x86_64, into dist/upload beside the Windows programs.
#
# Run in Linux (WSL on the owner's PC), from this repository with RemedyUI checked out in
# ../../Projects/RemedyUI as rust/hearth/Cargo.toml expects, with cargo-zigbuild and zig on
# the PATH (the build links against glibc 2.28, rust/hearth/remedy.toml):
#
#   pack/build-linux.sh [folder to put the two files in]
set -eo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/dist/upload}"
engine="$(cd "$here/../../Projects/RemedyUI" && pwd)"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/hearth-linux-target}"

# The engine's packager, built from the same checkout the app builds against.
CARGO_TARGET_DIR="$HOME/cargo-remedy-target" cargo build --release -q -p cargo-remedy --manifest-path "$engine/Cargo.toml"
export PATH="$HOME/cargo-remedy-target/release:$PATH"

cd "$here/rust/hearth"
cargo remedy package --target linux
mkdir -p "$out"
cp "$CARGO_TARGET_DIR"/remedy/dist/Hearth-*-linux-x86_64.tar.gz "$CARGO_TARGET_DIR"/remedy/dist/Hearth_*_linux-x86_64 "$out/"
ls -l "$out"
