"""deploy/push.sh must ship exactly a commit that is on origin/main, never the working tree."""

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

PUSH_SH = Path(__file__).resolve().parents[1] / "deploy" / "push.sh"


def find_bash() -> str:
    if os.name == "nt":  # System32\bash.exe is WSL; use the bash that ships with Git for Windows
        git = shutil.which("git")
        for folder in Path(git).parents if git else ():  # git may be in Git\cmd or Git\mingw64\bin
            if (folder / "bin" / "bash.exe").exists():
                return str(folder / "bin" / "bash.exe")
        pytest.fail("Git for Windows bash not found; this test needs it to run deploy/push.sh")
    return shutil.which("bash") or pytest.fail("bash not found")


def git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", str(origin), str(work))
    (work / "tqqq-blend" / "deploy").mkdir(parents=True)
    (work / "tqqq-blend" / "deploy" / "push.sh").write_bytes(PUSH_SH.read_bytes())
    (work / "tqqq-blend" / "config.toml").write_text("capital = 1\n")
    git(work, "add", "-A")
    git(work, "commit", "-m", "merged")
    git(work, "push", "-q", "origin", "HEAD:main")
    return work


def bundle(repo: Path, commit: str, out: Path) -> subprocess.CompletedProcess:
    script = (repo / "tqqq-blend" / "deploy" / "push.sh").as_posix()
    return subprocess.run(
        [find_bash(), script, "--bundle-only", commit, out.as_posix()], cwd=repo, capture_output=True, text=True
    )


def test_bundle_is_the_commit_not_the_working_tree(repo: Path, tmp_path: Path):
    (repo / "tqqq-blend" / "config.toml").write_text("capital = 1000000\n")  # unreviewed local edit
    (repo / "tqqq-blend" / "untracked.py").write_text("print('not merged')\n")
    out = tmp_path / "bundle.tgz"
    result = bundle(repo, "origin/main", out)
    assert result.returncode == 0, result.stderr
    with tarfile.open(out) as tar:
        names = set(tar.getnames())
        committed = tar.extractfile("config.toml").read().replace(b"\r\n", b"\n")  # autocrlf may add CR
        assert committed == b"capital = 1\n"
    assert "untracked.py" not in names and "deploy/push.sh" in names


def test_commit_not_on_main_is_refused(repo: Path, tmp_path: Path):
    git(repo, "checkout", "-q", "-b", "feat/unmerged")
    (repo / "tqqq-blend" / "config.toml").write_text("capital = 1000000\n")
    git(repo, "commit", "-qam", "unmerged")
    out = tmp_path / "bundle.tgz"
    result = bundle(repo, "HEAD", out)
    assert result.returncode != 0
    assert "not on origin/main" in result.stdout
    assert not out.exists()
