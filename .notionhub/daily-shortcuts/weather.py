#!/usr/bin/python
# -*- coding: UTF-8 -*-

import argparse
import json

from shortcut_update import update_weather


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Daily weather properties")
    parser.add_argument("content", help="JSON payload from an iOS Shortcut")
    options = parser.parse_args()
    result = update_weather(options.content)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
