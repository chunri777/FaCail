from __future__ import annotations

import json
from pathlib import Path

from providers.ths_provider import ThsProvider


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def main() -> None:
    provider = ThsProvider()
    status = provider.check_access()
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "ths_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if not status["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
