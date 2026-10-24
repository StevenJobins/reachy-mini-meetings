#!/usr/bin/env python3
"""Midterm deck: encrypt / decrypt the slides for the public GitHub Pages site.

The repo is public, so the slides are only committed encrypted (xr-client/pages/midterm/deck.enc,
AES-256-GCM). The key is the repository Actions variable MIDTERM_DECK_KEY, which the GitHub API
only shows to collaborators: the page (xr-client/pages/midterm/index.html) asks for a GitHub token,
reads the variable with it and decrypts in the browser. The plaintext deck.html is gitignored.

    python presentation/midterm/build.py decrypt   # deck.enc -> deck.html (to edit)
    python presentation/midterm/build.py encrypt   # deck.html -> deck.enc (then commit + push)
    python presentation/midterm/build.py newkey    # new key in the repo variable (then encrypt)

Needs `gh` (signed in as a collaborator) and `cryptography` (in the backend venv).
"""
import base64
import os
import subprocess
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit("needs `cryptography`: use the backend venv or `pip install cryptography`")

REPO = "StevenJobins/reachy-mini-meetings"
VAR = "MIDTERM_DECK_KEY"
AAD = b"reachy-midterm-v1"   # must match index.html
HERE = Path(__file__).resolve().parent
PLAIN = HERE / "deck.html"
ENC = HERE.parents[1] / "xr-client" / "pages" / "midterm" / "deck.enc"


def key() -> bytes:
    k = os.environ.get(VAR) or subprocess.run(["gh", "variable", "get", VAR, "-R", REPO],
                                             capture_output=True, text=True, check=True).stdout.strip()
    return base64.b64decode(k)


def main(cmd: str) -> None:
    if cmd == "newkey":
        k = base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()
        subprocess.run(["gh", "variable", "set", VAR, "-R", REPO, "--body", k], check=True)
        print(f"new key stored in {REPO} variable {VAR}; now run: encrypt")
    elif cmd == "encrypt":
        nonce = os.urandom(12)
        ENC.write_bytes(nonce + AESGCM(key()).encrypt(nonce, PLAIN.read_bytes(), AAD))
        print(f"{PLAIN.name} -> {ENC.relative_to(HERE.parents[1])} ({ENC.stat().st_size} bytes)")
    elif cmd == "decrypt":
        data = ENC.read_bytes()
        PLAIN.write_bytes(AESGCM(key()).decrypt(data[:12], data[12:], AAD))
        print(f"{ENC.name} -> {PLAIN.relative_to(HERE.parents[1])}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
