#!/usr/bin/env python3
"""Download the Comprehensive Rules text the RULES tool reads, and keep it only if it is the pinned one.

The text is Wizards of the Coast's, so the repository does not carry it: `setup.sh` and the acceptance preflight
call this to put it at data/rules/MagicCompRules.txt. A different sha256 is refused, not written: a new rules text
is a provenance event (data/rules/README.md). Stdlib only.

  python3 eval/fetch_comp_rules.py            # fetch it unless the pinned file is already there
"""

import hashlib
import os
import sys
import urllib.request

SHA256 = "8d860e451f20f38865b725b42d82feb714c725373dd8f3b32b8652b3eeb070ca"
SOURCE_URL = "https://media.wizards.com/2026/downloads/MagicCompRules%2020260925.txt"
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "rules", "MagicCompRules.txt")


# For the tests that need the text: on a fresh clone it is not there until setup.sh or acceptance has run.
MISSING = "no Comprehensive Rules text at data/rules/MagicCompRules.txt: run python3 eval/fetch_comp_rules.py"


def present(dest=DEST):
    return os.path.isfile(dest)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(dest=DEST, sha256=SHA256, url=SOURCE_URL, urlopen=urllib.request.urlopen):
    if os.path.isfile(dest):
        with open(dest, "rb") as f:
            if _sha(f.read()) == sha256:
                return dest
    with urlopen(url, timeout=60) as r:
        data = r.read()
    if _sha(data) != sha256:
        sys.exit("refused %s: its sha256 is %s, the pinned one is %s (data/rules/README.md)"
                 % (url, _sha(data), sha256))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    return dest


if __name__ == "__main__":
    print("rules text:", os.path.normpath(fetch()))
