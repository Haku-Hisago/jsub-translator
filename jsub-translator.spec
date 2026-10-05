# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('web/templates', 'web/templates'), ('src', 'src'), ('.env.example', '.')]
binaries = []
hiddenimports = [
    'flask', 'faster_whisper', 'yt_dlp', 'openai', 'anthropic',
    'ctranslate2', 'av', 'dotenv', 'webbrowser',
    'sherpa_onnx',
]
tmp_ret = collect_all('faster_whisper')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('ctranslate2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# --- Speaker diarization models -------------------------------------------
# sherpa-onnx runs the pyannote segmentation-3.0 model (ONNX export) plus a
# 3D-Speaker ERes2Net embedding model. Both are tiny (~45 MB total) compared
# to the torch-based pyannote route (~3 GB), and need no network access or
# HuggingFace token at runtime — so they ship inside the EXE.
#
# Each entry must be listed as (source_file, destination_DIR) explicitly.
# Passing a *directory* as the source and letting PyInstaller flatten it does
# NOT preserve the layout, and only model.onnx (not the whole folder) is needed
# at runtime anyway.
import os as _os_spk

_ROOT = _os_spk.path.dirname(_os_spk.path.abspath(SPEC))
_SEG_DIR = _os_spk.path.join(_ROOT, 'models', 'sherpa-onnx-pyannote-segmentation-3-0')
_EMB_FILE = _os_spk.path.join(
    _ROOT, 'models', '3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx'
)
# Must mirror src/config.py's _bundled_or(): <dest>/models/<same relative name>
_SPK_DATAS = [
    (_os_spk.path.join(_SEG_DIR, 'model.onnx'),
     'models/sherpa-onnx-pyannote-segmentation-3-0'),
    (_os_spk.path.join(_SEG_DIR, 'model.int8.onnx'),
     'models/sherpa-onnx-pyannote-segmentation-3-0'),
    (_EMB_FILE, 'models'),
]
for _src, _dst in _SPK_DATAS:
    if _os_spk.path.exists(_src):
        datas.append((_src, _dst))
        print(f'[spec] bundling diarization model: {_os_spk.path.basename(_src)} -> {_dst}')
    else:
        print(f'[spec] WARNING: diarization model missing, NOT bundled: {_src}')

# --- CUDA runtime: cuBLAS -------------------------------------------------
# CTranslate2 loads cuBLAS *dynamically at runtime* and its wheel ships cuDNN
# but NEVER cuBLAS. Without those DLLs every GPU run dies with
# "Library cublas64_12.dll is not found or cannot be loaded".
#
# They must end up inside the bundled 'ctranslate2' package, next to
# ctranslate2.dll — that is where the last known-good build kept them.
#   * If they sit in site-packages/ctranslate2/, the collect_all('ctranslate2')
#     above already picks them up (nothing to do here).
#   * Otherwise fall back to the nvidia-cublas-cu12 wheel and add them
#     explicitly, targeting the 'ctranslate2' directory.
import os as _os

_cublas_names = ('cublas64_12.dll', 'cublasLt64_12.dll', 'cudart64_12.dll')
_found = {}

try:
    import ctranslate2 as _ct2
    _ct2_dir = _os.path.dirname(_ct2.__file__)
    for _n in _cublas_names:
        if _os.path.isfile(_os.path.join(_ct2_dir, _n)):
            _found[_n] = _os.path.join(_ct2_dir, _n)
except Exception:  # noqa: BLE001 - build must not fail over this
    pass

if not _found:
    try:
        import nvidia.cublas as _nc

        _nbin = _os.path.join(_os.path.dirname(_nc.__file__), 'bin')
        for _n in _os.listdir(_nbin):
            if _n.lower().endswith('.dll'):
                _found[_n] = _os.path.join(_nbin, _n)
                binaries.append((_found[_n], 'ctranslate2'))
    except Exception:  # noqa: BLE001
        pass

if _found:
    print(f'[spec] cuBLAS available: {", ".join(sorted(_found))}')
else:
    print(
        '[spec] WARNING: no cuBLAS found — the EXE will build, but GPU '
        'inference will fail at runtime.\n'
        '[spec]   Fix: pip install nvidia-cublas-cu12, or drop\n'
        '[spec]        cublas64_12.dll + cublasLt64_12.dll + cudart64_12.dll\n'
        '[spec]        into site-packages/ctranslate2/ before building.'
    )


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='jsub-translator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
