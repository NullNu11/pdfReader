#!/bin/bash
# Build PDF Reader into a standalone executable
# Usage: bash build.sh

set -e

echo "=== Installing build dependencies ==="
pip3 install pyinstaller PyMuPDF PyQt6

echo ""
echo "=== Building executable ==="
pyinstaller pdf_reader.spec --clean

echo ""
echo "=== Done ==="
echo "Output: dist/PDFReader (or dist/PDFReader.app on macOS)"
