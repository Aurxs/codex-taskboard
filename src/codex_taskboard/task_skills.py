"""Install Taskboard's explicit-only skills and reference them in task messages."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .errors import ValidationError


EXECUTE_SKILL = "codex-taskboard-execute"
PLAN_SKILL = "codex-taskboard-plan"
MERGE_SKILL = "codex-taskboard-merge"
SKILL_NAMES = (EXECUTE_SKILL, PLAN_SKILL, MERGE_SKILL)
BUNDLED_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"
_MANAGED_MARKER = ".taskboard-managed"
# Install the explicit-only policy before exposing SKILL.md to discovery.
_SKILL_FILES = ("agents/openai.yaml", "SKILL.md")


def global_skills_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    return (Path(codex_home).expanduser() if codex_home else Path.home() / ".codex").resolve() / "skills"


def install_skills() -> Path:
    """Idempotent deployment setup; update only Taskboard-owned skill files."""
    root = global_skills_root()
    # Preflight all sources and destinations before updating any skill.
    files = []
    for name in SKILL_NAMES:
        destination = root / name
        if destination.is_symlink() or (destination.exists() and (
            not destination.is_dir() or not (destination / _MANAGED_MARKER).is_file()
        )):
            raise RuntimeError(f"Cannot install Taskboard skill over an unmanaged path: {destination}")
        if (destination / "agents").is_symlink():
            raise RuntimeError(f"Cannot install Taskboard skill through a symlink: {destination / 'agents'}")
        for relative in _SKILL_FILES:
            files.append((destination / relative, (BUNDLED_SKILLS_ROOT / name / relative).read_bytes()))
    for name in SKILL_NAMES:
        destination = root / name
        destination.mkdir(parents=True, exist_ok=True)
        (destination / _MANAGED_MARKER).touch(exist_ok=True)
    for path, content in files:
        if path.is_file() and not path.is_symlink() and path.read_bytes() == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                temporary.write(content)
                temporary.close()
                temporary_path.replace(path)
            finally:
                temporary_path.unlink(missing_ok=True)
    return root


def turn_input(prompt: str, skill: str | None = None) -> list[dict[str, str]]:
    if skill is None:
        return [{"type": "text", "text": prompt}]
    if skill not in SKILL_NAMES:
        raise ValidationError(f"Unknown Taskboard skill: {skill}")
    path = global_skills_root() / skill / "SKILL.md"
    if not path.is_file():
        raise ValidationError(f"Taskboard skill is not installed: {path}. Run python -m codex_taskboard.task_skills during deployment.")
    return [
        {"type": "text", "text": f"Use [${skill}]({path.as_posix()}) for this Taskboard request.\n\n{prompt}"},
        {"type": "skill", "name": skill, "path": str(path)},
    ]


if __name__ == "__main__":
    print(f"Taskboard skills installed: {install_skills()}")
