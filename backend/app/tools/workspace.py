from __future__ import annotations

import asyncio
import os
import codecs

from ..config import settings


def workspace_dir(task_id: int) -> str:
    d = os.path.join(settings.artifacts_dir, "workspace", f"task{task_id}")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_path(task_id: int, rel: str) -> str:
    """Resolve a path inside the task workspace, blocking traversal outside it."""
    base = os.path.realpath(workspace_dir(task_id))
    full = os.path.realpath(os.path.join(base, rel))
    if full != base and not full.startswith(base + os.sep):
        raise ValueError("path escapes the workspace")
    return full


def write_file(task_id: int, path: str, content: str) -> dict:
    full = _safe_path(task_id, path)
    os.makedirs(os.path.dirname(full) or workspace_dir(task_id), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": path, "bytes": len(content.encode())}


def read_file(task_id: int, path: str, max_chars: int = 20000) -> str:
    full = _safe_path(task_id, path)
    if not os.path.exists(full):
        return f"ERROR: no such file: {path}"
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        return f.read()[:max_chars]


def read_page(task_id: int, path: str, offset: int = 0, limit: int = 20000) -> dict:
    """Read a byte-addressed page, matching AST source_range coordinates."""
    offset, limit = int(offset), int(limit)
    if offset < 0 or not 1 <= limit <= 100000:
        raise ValueError('Invalid file page')
    full = _safe_path(task_id, path)
    with open(full, 'rb') as file:
        total = os.fstat(file.fileno()).st_size
        file.seek(offset)
        content = file.read(limit)
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        text = decoder.decode(content, final=offset + len(content) >= total)
        pending = decoder.getstate()[0]
        if pending and offset + len(content) < total:
            # Finish a split UTF-8 scalar; following pages start on a boundary.
            extra = file.read(3)
            content += extra
            text += decoder.decode(extra, final=offset + len(content) >= total)
            pending = decoder.getstate()[0]
        consumed = len(content) - len(pending)
    end = offset + consumed
    return {'path': path, 'text': text, 'offset': offset,
            'units': 'bytes', 'total_bytes': total, 'next_offset': end if end < total else None}


async def run_command(task_id: int, command: str, timeout: float = 120.0) -> dict:
    """Run a shell command in the task workspace. Runs on the host machine — gated by the
    enable_shell setting. Output is captured and truncated."""
    cwd = workspace_dir(task_id)
    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"timed_out": True, "stdout": "", "stderr": f"command exceeded {timeout}s and was killed",
                    "return_code": -1}
        return {
            "stdout": out.decode(errors="replace")[:15000],
            "stderr": err.decode(errors="replace")[:5000],
            "return_code": proc.returncode,
            "timed_out": False,
        }
    except Exception as e:  # noqa: BLE001
        return {"stdout": "", "stderr": str(e), "return_code": -1, "timed_out": False}


def list_files(task_id: int) -> list[dict]:
    base = workspace_dir(task_id)
    out = []
    for root, _dirs, files in os.walk(base):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, base)
            try:
                st = os.stat(full)
                out.append({"path": rel, "size": st.st_size, "modified": st.st_mtime})
            except OSError:
                continue
    return sorted(out, key=lambda x: -x["modified"])
