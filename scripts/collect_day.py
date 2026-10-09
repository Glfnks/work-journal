#!/usr/bin/env python3
"""Собирает сырьё для отчёта о работе за один календарный день.

Печатает коммиты, незакоммиченные правки, запросы чатов Cursor и факт
изменения настроек. Это не текст отчёта. Секреты в вывод не попадают.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
HOME = Path.home()
DEFAULT_ROOT = HOME / "PycharmProjects"
CURSOR_PROJECTS = HOME / ".cursor" / "projects"
SKIP_WALK = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".idea",
    "screenshots",
}
NOISE_PARTS = {"__pycache__", ".DS_Store"}
SECRET_RE = re.compile(
    r"(^|/)(\.env(?:\..*)?|.*\.env|.*credential.*|.*secret.*|.*password.*"
    r"|.*\.pem|.*\.key|id_rsa|.*token.*|my_real_config\.env)(/|$)",
    re.IGNORECASE,
)
TS_RE = re.compile(r"<timestamp>(.*?)</timestamp>", re.DOTALL)
UQ_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
MAX_FILES = 80
MAX_CHATS = 30
QUERY_LIMIT = 400


def git(repo: Path, *args: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "TZ": "Europe/Moscow", "LC_ALL": "C"},
    )


def find_repos(root: Path) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir():
        return found
    for dirpath, dirnames, _filenames in os.walk(root):
        current = Path(dirpath)
        if (current / ".git").exists():
            found.append(current)
            dirnames[:] = []
            continue
        dirnames[:] = [name for name in dirnames if name not in SKIP_WALK]
    return found


def parse_git_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(TZ)
    except ValueError:
        return None


def on_day(moment: datetime | None, target: date) -> bool:
    return moment is not None and moment.date() == target


def own_commit(name: str, email: str, identities: set[str]) -> bool:
    email_l = email.lower().strip()
    name_l = name.lower().strip()
    if email_l and email_l in identities:
        return True
    if name_l and name_l in identities:
        return True
    return "ermolenko" in email_l or "ermolenko" in name_l


def is_secret(path: str) -> bool:
    return SECRET_RE.search(path.replace("\\", "/")) is not None


def is_noise(path: str) -> bool:
    parts = set(Path(path).parts)
    if parts & NOISE_PARTS or path.endswith(".pyc"):
        return True
    return path.endswith(".DS_Store")


def is_debug_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return "screenshots/debug_ui" in normalized or normalized.startswith("screenshots/")


def repo_meta(repo: Path) -> tuple[str, str, bool]:
    remote = git(repo, "remote", "get-url", "origin").stdout.strip()
    if not remote:
        remotes = git(repo, "remote", "-v").stdout.strip().splitlines()
        remote = remotes[0] if remotes else ""
    work = "transformator" in remote or "a-hub.gitlab" in remote
    return remote, repo.name, work


def identities_for(repo: Path) -> set[str]:
    values: set[str] = set()
    for args in (
        ("config", "--get", "user.email"),
        ("config", "--get", "user.name"),
        ("config", "--global", "--get", "user.email"),
        ("config", "--global", "--get", "user.name"),
    ):
        value = git(repo, *args).stdout.strip().lower()
        if value:
            values.add(value)
    return values


def commit_files(repo: Path, sha: str) -> list[tuple[str, str]]:
    result = git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", "-M", sha)
    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        status, _, path = line.partition("\t")
        if "\t" in path:
            path = path.split("\t")[-1]
        rows.append((status.strip(), path.strip()))
    return rows


def on_remote(repo: Path, sha: str) -> bool:
    return bool(git(repo, "branch", "-r", "--contains", sha).stdout.strip())


def local_branches(repo: Path, sha: str) -> str:
    result = git(repo, "branch", "--contains", sha)
    names = []
    for line in result.stdout.splitlines():
        name = line.strip().lstrip("* ").strip()
        if name and not name.startswith("("):
            names.append(name)
    return ", ".join(names[:6]) or "—"


def collect_commits(repo: Path, target: date, identities: set[str]) -> tuple[list[str], int]:
    since = (target - timedelta(days=2)).isoformat()
    until = (target + timedelta(days=2)).isoformat()
    result = git(
        repo,
        "log",
        "--all",
        "--no-merges",
        f"--since={since}",
        f"--until={until}",
        "--date=iso-strict",
        "--pretty=format:%H%x1f%aI%x1f%cI%x1f%an%x1f%ae%x1f%s%x1e",
    )
    blocks: list[str] = []
    skipped = 0
    if result.returncode != 0:
        return [f"git log не выполнен: {result.stderr.strip()}"], 0
    for raw in result.stdout.split("\x1e"):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split("\x1f")
        if len(parts) < 6:
            continue
        sha, author_date, committer_date, name, email, subject = parts[:6]
        if not (
            on_day(parse_git_date(author_date), target)
            or on_day(parse_git_date(committer_date), target)
        ):
            continue
        if identities and not own_commit(name, email, identities):
            skipped += 1
            continue
        files = commit_files(repo, sha)
        pushed = on_remote(repo, sha)
        lines = [
            f"- {sha[:10]} {'на remote-tracking' if pushed else 'только локально'}"
            f" ветки: {local_branches(repo, sha)}",
            f"  {subject.strip()}",
        ]
        shown = secrets = debug = noise = 0
        for status, path in files:
            if is_secret(path):
                secrets += 1
                continue
            if is_debug_artifact(path):
                debug += 1
                continue
            if is_noise(path):
                noise += 1
                continue
            if shown < MAX_FILES:
                lines.append(f"  {status} {path}")
                shown += 1
        hidden = max(0, len(files) - shown - secrets - debug - noise)
        if hidden:
            lines.append(f"  … ещё файлов: {hidden}")
        if secrets:
            lines.append(f"  секреты, содержимое скрыто: {secrets}")
        if debug:
            lines.append(f"  отладочные артефакты UI, список скрыт: {debug}")
        blocks.append("\n".join(lines))
    return blocks, skipped


def parse_status_z(payload: str) -> list[tuple[str, str]]:
    chunks = payload.split("\0")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(chunks):
        item = chunks[index]
        if not item:
            index += 1
            continue
        status = item[:2]
        path = item[3:] if len(item) > 3 else ""
        if status and status[0] in {"R", "C"}:
            index += 1
            if index < len(chunks):
                path = chunks[index]
        entries.append((status.strip() or "?", path))
        index += 1
    return entries


def file_day(repo: Path, path: str) -> date | None:
    candidate = repo / path
    if not candidate.exists():
        return None
    return datetime.fromtimestamp(candidate.stat().st_mtime, TZ).date()


def collect_uncommitted(repo: Path, target: date, today: date) -> list[str]:
    result = git(repo, "status", "--porcelain=v1", "-z", "-uall")
    if result.returncode != 0:
        return [f"git status не выполнен: {result.stderr.strip()}"]
    lines: list[str] = []
    secrets = debug = undated_deletes = shown = 0
    for status, path in parse_status_z(result.stdout):
        if not path or is_noise(path):
            continue
        if is_secret(path):
            secrets += 1
            continue
        if is_debug_artifact(path):
            debug += 1
            continue
        deleted = "D" in status and not (repo / path).exists()
        if deleted:
            if target == today and shown < MAX_FILES:
                lines.append(f"- {status} {path} (удалён, дата по файлу неизвестна)")
                shown += 1
            elif target != today:
                undated_deletes += 1
            continue
        if file_day(repo, path) != target:
            continue
        if shown < MAX_FILES:
            lines.append(f"- {status} {path}")
            shown += 1
    notes: list[str] = []
    if secrets:
        notes.append(f"- секреты изменены, содержимое скрыто: {secrets}")
    if debug:
        notes.append(f"- отладочные артефакты UI, список скрыт: {debug}")
    if undated_deletes:
        notes.append(
            f"- удаления в рабочей копии без даты, к этому дню не отнесены: {undated_deletes}"
        )
    return lines + notes


def cursor_project_label(folder: str) -> str:
    marker = "PycharmProjects-"
    if marker in folder:
        return folder.split(marker, 1)[1]
    return folder


def parse_chat_stamp(text: str) -> datetime | None:
    cleaned = text.split("(", 1)[0].strip()
    try:
        return datetime.strptime(cleaned, "%A, %b %d, %Y, %I:%M %p").replace(tzinfo=TZ)
    except ValueError:
        return None


def collect_chats(target: date) -> list[str]:
    if not CURSOR_PROJECTS.is_dir():
        return []
    grouped: dict[str, list[str]] = {}
    for jsonl in CURSOR_PROJECTS.glob("*/agent-transcripts/*/*.jsonl"):
        try:
            modified = datetime.fromtimestamp(jsonl.stat().st_mtime, TZ).date()
        except OSError:
            continue
        if modified < target:
            continue
        project = cursor_project_label(jsonl.parents[3].name)
        try:
            text = jsonl.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if '"role": "user"' not in line and '"role":"user"' not in line:
                continue
            stamp_match = TS_RE.search(line)
            query_match = UQ_RE.search(line)
            if not stamp_match or not query_match:
                continue
            stamp = parse_chat_stamp(stamp_match.group(1))
            if not on_day(stamp, target):
                continue
            query = re.sub(r"\s+", " ", query_match.group(1)).strip()
            if len(query) > QUERY_LIMIT:
                query = query[: QUERY_LIMIT - 1] + "…"
            clock = stamp.strftime("%H:%M") if stamp else ""
            grouped.setdefault(project, []).append(f"- {clock} {query}")
    blocks: list[str] = []
    for project, queries in sorted(grouped.items()):
        visible = queries[:MAX_CHATS]
        body = "\n".join(visible)
        extra = len(queries) - len(visible)
        if extra:
            body += f"\n- … ещё запросов: {extra}"
        blocks.append(f"### {project}\n{body}")
    return blocks


def touched_on(path: Path, target: date) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, TZ).date() == target
    except OSError:
        return False


def collect_settings(target: date) -> list[str]:
    notes: list[str] = []
    skills = HOME / ".cursor" / "skills"
    if skills.is_dir():
        for path in skills.rglob("*"):
            if path.is_file() and touched_on(path, target):
                notes.append(f"- {path.relative_to(HOME)}")
    user_dir = HOME / "Library" / "Application Support" / "Cursor" / "User"
    for name in ("settings.json", "keybindings.json"):
        path = user_dir / name
        if path.is_file() and touched_on(path, target):
            notes.append(f"- изменён Cursor User/{name}, содержимое не читалось")
    return notes


def render_repo(repo: Path, target: date, today: date) -> str | None:
    remote, label, work = repo_meta(repo)
    identities = identities_for(repo)
    commits, skipped = collect_commits(repo, target, identities)
    dirty = collect_uncommitted(repo, target, today)
    if not commits and not dirty:
        return None
    parts = [
        f"## {label}",
        f"path: {repo}",
        f"remote: {remote or '—'}",
        f"класс: {'рабочий' if work else 'прочий'}",
    ]
    if not identities:
        parts.append("автор коммитов не отфильтрован: в git config нет user.email")
    parts.append("\n### Коммиты за день")
    parts.append("\n".join(commits) if commits else "нет")
    if skipped:
        parts.append(f"пропущено чужих коммитов: {skipped}")
    parts.append("\n### Незакоммиченное, изменённое в этот день")
    parts.append("\n".join(dirty) if dirty else "нет")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="День YYYY-MM-DD в Europe/Moscow. По умолчанию сегодня.")
    parser.add_argument("--root", action="append", type=Path, help="Каталог с репозиториями.")
    args = parser.parse_args()
    today = datetime.now(TZ).date()
    target = date.fromisoformat(args.date) if args.date else today
    repos: list[Path] = []
    seen: set[Path] = set()
    for root in args.root or [DEFAULT_ROOT]:
        repos.extend(find_repos(root.expanduser()))
    print(f"# Сырьё за {target.isoformat()} (Europe/Moscow)")
    print("Это список правок, не текст отчёта. Секреты не включены.")
    rendered = 0
    for repo in repos:
        resolved = repo.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        block = render_repo(resolved, target, today)
        if not block:
            continue
        rendered += 1
        print(f"\n{block}")
    if not rendered:
        print("\nРепозитории с правками за этот день не найдены.")
    print("\n# Чаты Cursor за этот день")
    chats = collect_chats(target)
    print("\n".join(chats) if chats else "нет")
    print("\n# Настройки вне репозиториев")
    settings = collect_settings(target)
    print("\n".join(settings) if settings else "нет")


if __name__ == "__main__":
    main()
