"""Preview or apply expiry in the dedicated telemetry store; never touch JSONL/memory."""

import argparse
import json
import sqlite3

from rio_bot.core.retained_telemetry import RetainedTelemetry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true", help="Delete expired telemetry rows")
    args = parser.parse_args(argv)
    try:
        result = RetainedTelemetry(args.db).maintenance(apply=args.apply)
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps({"status": "UNAVAILABLE", "apply": args.apply}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
