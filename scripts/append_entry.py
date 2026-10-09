#!/usr/bin/env python3
"""Дописывает одну строку в личный рабочий отчет и пересобирает HTML.

Данные лежат в ~/.cursor/work-log. Сам отчет — обычный файл
~/Documents/Рабочий отчет.html, чтобы его находил поиск Finder.
В git он попадать не должен.
"""

from __future__ import annotations

import argparse
import fcntl
import html
import json
import os
import re
import socket
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
JOURNAL_DIR = Path.home() / ".cursor" / "work-log"
DATA_PATH = JOURNAL_DIR / "journal.json"
HTML_PATH = Path.home() / "Documents" / "Рабочий отчет.html"
LOCK_PATH = JOURNAL_DIR / ".lock"
HOST = "127.0.0.1"
PORT = 47655
ITEM_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
LAUNCH_LABEL = "local.work-journal"
MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def assert_private(path: Path) -> None:
    resolved = path.resolve()
    for parent in (resolved, *resolved.parents):
        if (parent / ".git").exists():
            raise SystemExit(f"отказ: журнал внутри git-репозитория ({parent})")


def title_for(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def phrase(text: str) -> str:
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [normalize(line) for line in raw.split("\n")]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return cleaned.rstrip(".")


def same(left: str, right: str) -> bool:
    return phrase(left).casefold() == phrase(right).casefold()


class JournalError(Exception):
    pass


def as_item(raw: str | dict) -> dict:
    if isinstance(raw, str):
        return {"id": str(uuid.uuid4()), "title": phrase(raw), "detail": ""}
    item_id = str(raw.get("id") or "").strip() or str(uuid.uuid4())
    return {
        "id": item_id,
        "title": phrase(str(raw.get("title", ""))),
        "detail": phrase(str(raw.get("detail", ""))),
    }


def normalize_journal(data: dict) -> None:
    for day in data.get("days", []):
        for project in day.get("projects", []):
            project["items"] = [as_item(item) for item in project.get("items", [])]


def load() -> dict:
    if not DATA_PATH.exists():
        return {"days": []}
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    normalize_journal(data)
    return data


TEMPLATE_PATH = Path(__file__).resolve().parent / "page_template.html"


def render(data: dict) -> str:
    payload = json.dumps(
        {
            "today": datetime.now(TZ).date().isoformat(),
            "days": data.get("days") or [],
        },
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template.replace("__DATA__", payload)
        .replace("__HOST__", HOST)
        .replace("__PORT__", str(PORT))
    )


def save(data: dict) -> None:
    assert_private(JOURNAL_DIR)
    assert_private(HTML_PATH)
    normalize_journal(data)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    JOURNAL_DIR.chmod(0o700)
    _replace(DATA_PATH, json.dumps(data, ensure_ascii=False, indent=2) + "\n", 0o600)
    if HTML_PATH.is_symlink():
        HTML_PATH.unlink()
    _replace(HTML_PATH, render(data), 0o644)


def _replace(path: Path, text: str, mode: int) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)


def server_up() -> bool:
    sock = socket.socket()
    sock.settimeout(0.3)
    try:
        sock.connect((HOST, PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def stop_service() -> None:
    label = f"gui/{os.getuid()}/{LAUNCH_LABEL}"
    result = subprocess.run(
        ["launchctl", "bootout", label],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        os._exit(0)


def ensure_server() -> None:
    if server_up():
        return
    label = f"gui/{os.getuid()}/{LAUNCH_LABEL}"
    probe = subprocess.run(
        ["launchctl", "print", label],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode == 0:
        subprocess.run(
            ["launchctl", "kickstart", label],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    script = Path(__file__).resolve().parent / "journal_server.py"
    subprocess.Popen(
        [sys.executable, str(script)],
        cwd=script.parent,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _mutate(mutator, wake_server: bool) -> str:
    assert_private(JOURNAL_DIR)
    assert_private(HTML_PATH)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    JOURNAL_DIR.chmod(0o700)
    with LOCK_PATH.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = load()
            result = mutator(data)
            if result not in {"unchanged", "exists"}:
                save(data)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    if wake_server and result not in {"unchanged", "exists"}:
        ensure_server()
    return result


def update_item(item_id: str, title: str, detail: str) -> str:
    if not ITEM_ID.fullmatch(item_id or ""):
        raise JournalError("неизвестная запись")

    def mutate(data: dict) -> str:
        for day in data["days"]:
            for project in day["projects"]:
                for item in project["items"]:
                    if item["id"] != item_id:
                        continue
                    new_title = phrase(title)
                    new_detail = phrase(detail)
                    if not new_title:
                        raise JournalError("пустой заголовок")
                    if item["title"] == new_title and item["detail"] == new_detail:
                        return "unchanged"
                    item["title"] = new_title
                    item["detail"] = new_detail
                    return "updated"
        raise JournalError("запись не найдена")

    return _mutate(mutate, wake_server=False)


def day_bucket(data: dict, day: date) -> dict:
    iso = day.isoformat()
    for item in data["days"]:
        if item["date"] == iso:
            return item
    created = {"date": iso, "title": title_for(day), "projects": []}
    data["days"].insert(0, created)
    data["days"].sort(key=lambda item: item["date"], reverse=True)
    return created


def project_group(bucket: dict, project: str) -> dict:
    group = next((item for item in bucket["projects"] if same(item["name"], project)), None)
    if group is None:
        group = {"name": project, "items": []}
        bucket["projects"].append(group)
    return group


def append(day: date, project: str, text: str, detail: str = "", update: str = "") -> str:
    project = phrase(project)
    text = phrase(text)
    detail = phrase(detail)
    update = phrase(update)
    if not project or not text:
        raise SystemExit("нужны проект и текст")

    def mutate(data: dict) -> str:
        bucket = day_bucket(data, day)
        if update:
            group = next((item for item in bucket["projects"] if same(item["name"], project)), None)
            if group is None:
                raise SystemExit("запись для обновления не найдена")
            for item in group["items"]:
                if not same(item["title"], update):
                    continue
                if item["title"] == text and item["detail"] == detail:
                    return "unchanged"
                item["title"] = text
                item["detail"] = detail
                return "updated"
            raise SystemExit("запись для обновления не найдена")
        group = project_group(bucket, project)
        if any(same(item["title"], text) for item in group["items"]):
            return "exists"
        group["items"].append({"id": str(uuid.uuid4()), "title": text, "detail": detail})
        return "added"

    return _mutate(mutate, wake_server=True)


def show(day: date | None) -> str:
    data = load()
    days = data["days"]
    if day is not None:
        days = [item for item in days if item["date"] == day.isoformat()]
    if not days:
        return "пусто"
    lines: list[str] = []
    for item in days:
        lines.append(item["title"])
        for project in item["projects"]:
            lines.append(f"{project['name']}")
            for entry in project["items"]:
                lines.append(f"- {entry['title']}")
                if entry["detail"]:
                    lines.append(f"  {entry['detail']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Имя проекта, как в журнале")
    parser.add_argument("--text", help="Понятный заголовок правки, без точки на конце")
    parser.add_argument("--detail", default="", help="Подробность под треугольником. Необязательно.")
    parser.add_argument(
        "--update",
        default="",
        help="Текущий заголовок строки, которую нужно заменить, а не дублировать",
    )
    parser.add_argument("--date", help="День YYYY-MM-DD в Europe/Moscow. По умолчанию сегодня.")
    parser.add_argument("--show", action="store_true", help="Показать журнал, без записи")
    args = parser.parse_args()
    target = date.fromisoformat(args.date) if args.date else datetime.now(TZ).date()
    if args.show:
        print(show(target if args.date else None))
        return
    if not args.project or not args.text:
        raise SystemExit("для записи нужны --project и --text")
    print(append(target, args.project, args.text, args.detail, args.update))


if __name__ == "__main__":
    main()
