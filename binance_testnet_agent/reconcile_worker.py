from __future__ import annotations

import json
import time

from .config import AgentConfig
from .reconcile import AccountReconciler


def main() -> None:
    while True:
        config = AgentConfig.from_env()
        if config.reconciliation_enabled and config.api_key and config.api_secret:
            try:
                print(json.dumps(AccountReconciler(config).run().to_dict(), ensure_ascii=False), flush=True)
            except Exception as exc:
                print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), flush=True)
        time.sleep(60)


if __name__ == "__main__":
    main()
