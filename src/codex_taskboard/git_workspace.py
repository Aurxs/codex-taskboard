"""Small, local-only Git operations. No reset, stash, push, or destructive cleanup."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

from .errors import ValidationError


class GitError(ValidationError):
    pass


def git(cwd: str, *args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_MERGE_AUTOEDIT": "no"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"Git 操作失败：{exc}") from exc
    if check and result.returncode:
        raise GitError(result.stderr.decode(errors="replace").strip() or "Git 操作失败")
    return result.stdout.decode(errors="surrogateescape").rstrip("\n")


def repository(cwd: str) -> tuple[str, str]:
    root = git(cwd, "rev-parse", "--show-toplevel")
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return str(Path(root).resolve()), str(Path(common).resolve())


def current_branch(cwd: str) -> str | None:
    return git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD", check=False) or None


def commit(cwd: str, ref: str = "HEAD") -> str:
    if not ref or ref.startswith("-"):
        raise GitError("请选择有效分支")
    return git(cwd, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")


def local_branch(cwd: str, name: str) -> str:
    if not name or name.startswith("-"):
        raise GitError("请选择合入目标分支")
    git(cwd, "check-ref-format", "--branch", name)
    return commit(cwd, f"refs/heads/{name}")


def pin_branch(cwd: str, name: str, sha: str) -> None:
    # update-ref's empty old value creates a ref only if absent.
    existing = git(cwd, "rev-parse", "--verify", f"refs/heads/{name}", check=False)
    if existing:
        if existing != sha:
            raise GitError("任务起始分支已变化，请检查原工作树")
        return
    git(cwd, "update-ref", f"refs/heads/{name}", sha, "")


def clean(cwd: str) -> bool:
    return not git(cwd, "status", "--porcelain", "--untracked-files=normal")


def is_ancestor(cwd: str, older: str, newer: str) -> bool:
    result = subprocess.run(["git", "-C", cwd, "merge-base", "--is-ancestor", older, newer],
                            capture_output=True, timeout=30)
    if result.returncode not in (0, 1):
        raise GitError(result.stderr.decode(errors="replace"))
    return result.returncode == 0


def normalize_scopes(scopes: list[str], root: str | None = None) -> list[str]:
    result = []
    for raw in scopes:
        if not isinstance(raw, str):
            raise ValidationError("修改范围必须是文件或目录路径")
        value = raw.strip().replace("\\", "/")
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or any(c in value for c in "*?[]\x00"):
            raise ValidationError("修改范围必须是仓库内的明确文件或目录，不支持通配符")
        if ".git" in path.parts:
            raise ValidationError("修改范围不能包含 Git 内部目录")
        if root:
            resolved = (Path(root) / value).resolve()
            if not resolved.is_relative_to(Path(root).resolve()):
                raise ValidationError("修改范围不能越出仓库")
            value = resolved.relative_to(Path(root).resolve()).as_posix()
        else:
            value = path.as_posix()
        if value not in result:
            result.append(value)
    return sorted(result)


def overlaps(a: str, b: str, *, ignore_case: bool = False) -> bool:
    if ignore_case:
        a, b = a.casefold(), b.casefold()
    return a == "." or b == "." or a == b or a.startswith(b + "/") or b.startswith(a + "/")


def changed_paths(cwd: str, base: str, head: str) -> list[str]:
    # --no-renames includes both the deleted old path and the new path.
    return [p for p in git(cwd, "diff", "--no-renames", "--name-only", "-z", base, head, "--").split("\x00") if p]


def outside_scopes(cwd: str, base: str, head: str, scopes: list[str]) -> list[str]:
    if not scopes:
        return []
    insensitive = git(cwd, "config", "--bool", "core.ignorecase", check=False) == "true"
    def covered(path: str) -> bool:
        if insensitive:
            path = path.casefold()
        return any(s == "." or path == s or path.startswith(s + "/")
                   for s in (v.casefold() if insensitive else v for v in scopes))
    return [p for p in changed_paths(cwd, base, head) if not covered(p)]


def checked_out_paths(cwd: str, branch: str) -> list[str]:
    records = git(cwd, "worktree", "list", "--porcelain", "-z").split("\x00\x00")
    result = []
    for record in records:
        fields = dict(part.split(" ", 1) for part in record.split("\x00") if " " in part)
        if fields.get("branch") == f"refs/heads/{branch}" and fields.get("worktree"):
            result.append(fields["worktree"])
    return result


def publish(cwd: str, branch: str, expected: str, result: str) -> None:
    if local_branch(cwd, branch) != expected:
        raise GitError("合入目标已前进，需要重新准备合并")
    if not is_ancestor(cwd, expected, result):
        raise GitError("合并结果没有包含目标版本")
    paths = checked_out_paths(cwd, branch)
    if paths:
        if len(paths) != 1 or not clean(paths[0]):
            raise GitError("目标工作区有未提交修改，等待处理后重试")
        if current_branch(paths[0]) != branch or commit(paths[0]) != expected:
            raise GitError("目标工作区已变化，请重新准备合并")
        git(paths[0], "merge", "--ff-only", result)
    else:
        git(cwd, "update-ref", f"refs/heads/{branch}", result, expected)
    if local_branch(cwd, branch) != result:
        raise GitError("合入目标发生并发变更，请核对结果")
