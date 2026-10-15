import json
import secrets
import string
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "issues.json"
ALPHABET = string.ascii_lowercase + string.digits


def load() -> dict:
    if not DB.exists():
        return {}
    return json.loads(DB.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    DB.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def new_id(data: dict, length: int = 6) -> str:
    while True:
        rid = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if rid not in data:
            return rid


def add(text: str) -> str:
    data = load()
    rid = new_id(data)
    data[rid] = text
    save(data)
    return rid


def get(rid: str):
    return load().get(rid)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("用法:")
        print("  issue add 你的问题")
        print("  issue get <id>")
        print("  issue list")
        return

    cmd, rest = args[0], args[1:]

    if cmd == "add":
        text = " ".join(rest).strip()
        if not text:
            print("问题内容不能为空")
            return
        rid = add(text)
        print(f"已记录 [{rid}]: {text}")

    elif cmd == "get":
        if not rest:
            print("请提供 ID，例如: issue get a3f9k2")
            return
        rid = rest[0]
        text = get(rid)
        print(f"[{rid}] {text}" if text else f"没有找到 ID: {rid}")

    elif cmd == "list":
        data = load()
        if not data:
            print("还没有记录")
            return
        for rid, text in data.items():
            print(f"[{rid}] {text}")

    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()