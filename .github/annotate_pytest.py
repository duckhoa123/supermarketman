"""Turn pytest failures into GitHub annotations so they show on the run summary page.

Usage: python .github/annotate_pytest.py backend/pytest.log
"""

import re
import sys


def encode(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main(path: str) -> None:
    log = open(path, encoding="utf-8", errors="replace").read()
    # Tracebacks from --tb=short: "____ test_name ____" headers followed by the failure detail.
    sections = re.split(r"\n_{3,} (.+?) _{3,}\n", log)
    details = {}
    for i in range(1, len(sections) - 1, 2):
        name = sections[i].strip()
        body = sections[i + 1].split("\n=====")[0]
        details[name.split("[")[0].split(".")[-1]] = "\n".join(body.strip().splitlines()[-25:])
    summary = re.findall(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$", log, re.M)
    for kind, test_id, reason in summary[:10]:
        short = test_id.split("::")[-1].split("[")[0]
        body = details.get(short, reason or "")
        print(f"::error title={kind} {test_id}::{encode(body[-3500:])}")
    tail = [ln for ln in log.splitlines() if re.search(r"\d+ (passed|failed|error)", ln)]
    if tail:
        print(f"::notice title=pytest result::{encode(tail[-1])}")


if __name__ == "__main__":
    main(sys.argv[1])
