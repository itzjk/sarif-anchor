import argparse
import os
from pathlib import Path
import subprocess


def create_demo(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Example", GIT_AUTHOR_EMAIL="example@example.invalid",
               GIT_COMMITTER_NAME="Example", GIT_COMMITTER_EMAIL="example@example.invalid",
               GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
               GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(destination), "-c", "core.hooksPath=" + os.devnull,
             *args], env=env, stderr=subprocess.DEVNULL).decode().strip()

    git("init", "-q")
    source = destination / "example.py"
    source.write_text("def clean(name):\n    return name.strip()\n", encoding="utf-8")
    git("add", "example.py")
    git("commit", "-qm", "Add the original example")
    git("tag", "original")
    source.write_text("def clean(name):\n    name = str(name)\n    return name.strip()\n", encoding="utf-8")
    git("add", "example.py")
    git("commit", "-qm", "Insert a line before the cited code")
    git("tag", "shifted")
    source.write_text("dirty checkout, deliberately unrelated\n", encoding="utf-8")
    print("Created two commits: original, shifted; checkout is dirty.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a synthetic two-commit demo.")
    parser.add_argument("destination")
    create_demo(parser.parse_args().destination)
