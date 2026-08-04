#!/usr/bin/env bash
# Build Paper 7 and refresh the SSRN-upload PDF.
set -euo pipefail
VERSION="v0.1"
OUT_NAME="Khan_2026_execution_repro_${VERSION}.pdf"
cd "$(dirname "$0")"
tectonic main.tex
mkdir -p submission-ssrn
cp main.pdf "submission-ssrn/${OUT_NAME}"
echo "Built:           paper/main.pdf"
echo "Versioned copy:  paper/submission-ssrn/${OUT_NAME}"
