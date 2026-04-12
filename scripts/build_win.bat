@echo off
REM Build PDF Reader into a standalone .exe for Windows
REM Usage: double-click or run in cmd

echo === Installing build dependencies ===
pip install pyinstaller PyMuPDF PyQt6

echo.
echo === Building .exe ===
pyinstaller --onefile --noconsole --windowed ^
    --name PDFReader ^
    --hidden-import pymupdf ^
    --hidden-import pymupdf._pymupdf ^
    --exclude-module tkinter ^
    --exclude-module unittest ^
    --distpath ..\dist ^
    --workpath ..\build ^
    ..\src\pdf_reader.py

echo.
echo === Done ===
echo Output: dist\PDFReader.exe
pause
