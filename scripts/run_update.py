from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"


def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"update-{timestamp}.log"
    command = [sys.executable, str(ROOT / "scripts" / "generate_data.py"), "--provider", "ths", "--stage", "auto"]
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=ROOT, text=True, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        print(f"FaCail update failed. See {log_path}")
        raise SystemExit(result.returncode)
    print(f"FaCail update finished. See {log_path}")


if __name__ == "__main__":
    main()
