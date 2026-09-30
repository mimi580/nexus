"""Turn a pytest JUnit report into GitHub check annotations (one per failure)."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def main(path: str) -> int:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"::error title=junit::could not read {path}: {exc}")
        return 0
    cases = list(root.iter("testcase"))
    failed = sum(1 for c in cases if c.find("failure") is not None or c.find("error") is not None)
    skipped = sum(1 for c in cases if c.find("skipped") is not None)
    files = sorted({(c.get("classname") or "").split(".")[1] if "." in (c.get("classname") or "") else "" for c in cases})
    print(f"::notice title=pytest summary::{len(cases)} tests, {failed} failed, {skipped} skipped across {len(files)} files: {' '.join(files)}")
    for case in cases:
        for tag in ("failure", "error"):
            node = case.find(tag)
            if node is None:
                continue
            name = f"{case.get('classname')}::{case.get('name')}"
            detail = (node.get("message") or "") + "\n" + (node.text or "")[-1500:]
            detail = detail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
            print(f"::error title={name}::{detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "junit.xml"))
