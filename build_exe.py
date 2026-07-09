import subprocess
import sys
import os

PYTHON = sys.executable

cmd = [
    PYTHON, "-m", "PyInstaller",
    "--name=ChatExporter",
    "--onefile",
    "--windowed",
    "--clean",
    "--noconfirm",
    # cryptography (TRAE SQLCipher 解密需要) — 显式声明避免被裁剪
    "--hidden-import=cryptography",
    "--hidden-import=cryptography.hazmat.primitives.ciphers",
    "--hidden-import=cryptography.hazmat.primitives.ciphers.algorithms",
    "--hidden-import=cryptography.hazmat.primitives.ciphers.modes",
    "--hidden-import=cryptography.hazmat.backends",
    "--collect-submodules=cryptography",
    "main.py"
]

print("Running PyInstaller...")
print(" ".join(cmd))
print()

result = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))
sys.exit(result.returncode)
