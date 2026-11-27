"""Download a receipt selected for the shared static and judge evaluation corpus."""
import argparse
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
from zipfile import ZipFile

from dart_job_seeker.dart import Dart


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", help="DART 접수번호")
    parser.add_argument("--output", type=Path, default=Path("evaluations/data"))
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{14}", args.receipt):
        parser.error("접수번호는 14자리 숫자여야 합니다.")
    key = os.getenv("DART_API_KEY")
    if not key:
        parser.error("DART_API_KEY가 필요합니다.")
    raw = Dart(key).fetch("document.xml", rcept_no=args.receipt)
    with ZipFile(BytesIO(raw)) as archive:
        if not any(name.lower().endswith(".xml") for name in archive.namelist()) or archive.testzip():
            raise ValueError("원문 XML ZIP 검증 실패")
    args.output.mkdir(parents=True, exist_ok=True)
    dest = args.output / f"{args.receipt}-{sha256(raw).hexdigest()}.zip"
    dest.write_bytes(raw)
    print(dest)


if __name__ == "__main__":
    main()
