"""Publish the checked-out analysis model to one managed Notion section."""

import argparse
import ast
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
MODEL = Path("src/judgment_app/analysis/models.py")
START = "## MODEL_SYNC_START"
END = "## MODEL_SYNC_END"
API = "https://api.notion.com/v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    source = (ROOT / MODEL).read_text(encoding="utf-8")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    content = build_content(source, commit)
    if args.dry_run:
        print(content)
        return
    token = os.environ.get("NOTION_TOKEN")
    page_id = os.environ.get("NOTION_PAGE_ID", "")
    if not token or not re.fullmatch(r"[0-9a-fA-F-]{32,36}", page_id):
        raise ValueError("Set NOTION_TOKEN and a valid NOTION_PAGE_ID")
    with requests.Session() as session:
        session.headers.update({
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2026-03-11",
        })
        sync_page(session, page_id, content)
    print(f"Verified model sync for commit {commit}")


def build_content(source, commit):
    """Parse annotations without importing or executing the project module."""
    tree = ast.parse(source)
    digest = hashlib.sha256(source.encode()).hexdigest()
    url = f"https://github.com/hsiaomt/judgment_app/blob/{commit}/{MODEL.as_posix()}"
    lines = [START, "", "此區由 GitHub Actions 維護；人工業務規則請寫在區域外。",
             f"來源：[models.py]({url})", f"Commit: `{commit}`",
             f"Source SHA256: `{digest}`", "", "### 欄位與型別"]
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            fields = [n for n in node.body if isinstance(n, ast.AnnAssign)]
            if fields:
                lines.extend(["", f"#### {node.name}"])
                for field in fields:
                    lines.append(f"- `{ast.unparse(field.target)}`: `{ast.unparse(field.annotation)}`")
    # Use a longer fence than any sequence in the source.
    fence = "`" * max(3, max((len(m[0]) for m in re.finditer(r"`+", source)), default=0) + 1)
    lines.extend(["", "### 完整模型（含欄位說明與驗證規則）", "",
                  fence + "python", source.rstrip(), fence, "", END])
    return "\n".join(lines)


def make_payload(markdown, content):
    starts = list(re.finditer(r"(?m)^## MODEL_SYNC_START\s*$", markdown))
    ends = list(re.finditer(r"(?m)^## MODEL_SYNC_END\s*$", markdown))
    if not starts and not ends:
        return {"type": "insert_content", "insert_content": {
            "content": content, "position": {"type": "end"}}, "allow_async": False}
    if len(starts) != 1 or len(ends) != 1 or starts[0].start() >= ends[0].start():
        raise ValueError("Managed section markers missing, duplicated, or reversed; no write made")
    old = markdown[starts[0].start():ends[0].end()].rstrip()
    if old == content:
        return None
    return {"type": "update_content", "update_content": {"content_updates": [
        {"old_str": old, "new_str": content}]}, "allow_async": False}


def sync_page(session, page_id, content):
    path = f"/pages/{page_id}/markdown"
    before = _read(session, path)
    payload = make_payload(before, content)
    if payload is None:
        return
    # Guard against edits between our initial read and write. Exact old_str also
    # protects replacements; GitHub concurrency serializes our own runs.
    if _read(session, path) != before:
        raise RuntimeError("Page changed during preparation; retry from a fresh read")
    result = _request(session, "PATCH", path, json=payload)
    if result.get("object") == "async_task":
        task_id = result["id"]
        for _ in range(30):
            time.sleep(2)
            task = _request(session, "GET", f"/async_tasks/{task_id}")
            if task.get("status") == "succeeded":
                break
            if task.get("status") in {"failed", "cancelled"}:
                raise RuntimeError("Notion async update did not succeed")
        else:
            raise RuntimeError("Notion update still pending; verify before retrying")
    # Markdown may be normalized by Notion. Check the immutable source revision,
    # source digest, markers, and generated field headings after writing.
    after = _read(session, path)
    make_payload(after, content)  # validates exactly one managed section
    start = after.index(START)
    end = after.index(END, start)
    managed = after[start:end]
    expected = re.findall(r"(?m)^(?:Commit:.*|Source SHA256:.*|#### .*)$", content)
    if not all(item in managed for item in expected):
        raise RuntimeError("Notion readback did not contain the expected model revision")
    expected_code = re.search(r"(?ms)^(`{3,})python\n(.*?)\n\1$", content)
    actual_code = re.search(r"(?ms)^(`{3,})python\n(.*?)\n\1$", managed)
    if not expected_code or not actual_code or expected_code[2] != actual_code[2]:
        raise RuntimeError("Notion readback model source differs from the local file")


def _read(session, path):
    data = _request(session, "GET", path)
    if data.get("truncated") or data.get("unknown_block_ids"):
        raise RuntimeError("Incomplete Notion page read; no safe update possible")
    return data["markdown"]


def _request(session, method, path, **kwargs):
    # Do not retry writes automatically: a timed-out request may have committed.
    response = session.request(method, API + path, timeout=90, **kwargs)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    main()
