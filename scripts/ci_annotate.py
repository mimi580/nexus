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
    for case in root.iter("testcase"):
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
