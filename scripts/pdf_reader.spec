# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PDF Reader – single-file executable."""

a = Analysis(
    ['../src/pdf_reader.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pymupdf', 'pymupdf._pymupdf'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'email', 'xml', 'pydoc'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PDFReader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # No console window
    windowed=True,          # GUI app
)

# macOS only: create .app bundle
app = BUNDLE(
    exe,
    name='PDFReader.app',
    bundle_identifier='com.pdfreader.app',
)
