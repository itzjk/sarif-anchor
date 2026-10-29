from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import threading
import time


class GitError(Exception):
    pass


class LimitError(GitError):
    pass


@dataclass(frozen=True)
class Limits:
    report_bytes: int = 16 * 1024 * 1024
    blob_bytes: int = 4 * 1024 * 1024
    output_bytes: int = 8 * 1024 * 1024
    locations: int = 10000
    git_seconds: float = 5
    total_seconds: float = 30


class GitTree:
    def __init__(self, repo, revision, limits):
        if not revision or revision.startswith("-") or "\0" in revision:
            raise GitError("ref must be a nonempty revision that does not start with '-'")
        self.repo = str(Path(repo).absolute())
        self.limits = limits
        self.deadline = time.monotonic() + limits.total_seconds
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1",
                        GIT_ALLOW_PROTOCOL="",
                        GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                        GIT_ATTR_NOSYSTEM="1", LC_ALL="C")
        commit = self.run("rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
        self.commit = commit.decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.commit):
            raise GitError("Git did not return a commit object ID")

    def run(self, *args, limit=None):
        timeout = min(self.limits.git_seconds, self.deadline - time.monotonic())
        if timeout <= 0:
            raise LimitError("total Git time limit exceeded")
        limit = self.limits.output_bytes if limit is None else limit
        command = ["git", "--no-pager", "--no-replace-objects", "--no-optional-locks", "--no-lazy-fetch",
                   "-c", "protocol.allow=never", "-c", "core.hooksPath=" + os.devnull,
                   "-c", "core.fsmonitor=false", "-c", "core.commitGraph=false",
                   "-C", self.repo, *args]
        try:
            process = subprocess.Popen(command, env=self.env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise GitError("could not start Git") from exc
        chunks = []

        def read():
            chunks.append(process.stdout.read(limit + 1))

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            reader.join(timeout)
            if reader.is_alive():
                raise LimitError("Git command time limit exceeded")
            if len(chunks[0]) > limit:
                raise LimitError("Git output size limit exceeded")
            remaining = min(self.limits.git_seconds, self.deadline - time.monotonic())
            if remaining <= 0:
                raise LimitError("total Git time limit exceeded")
            process.wait(timeout=remaining)
            if process.returncode:
                raise GitError("Git could not read the requested local revision or object")
            return chunks[0]
        except subprocess.TimeoutExpired as exc:
            raise LimitError("Git command time limit exceeded") from exc
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join()
            process.stdout.close()

    def blob(self, path):
        tree = self.commit
        parts = path.split("/")
        for index, part in enumerate(parts):
            listing = self.run("ls-tree", "-z", tree, "--", ":(literal)" + part)
            if not listing:
                return None, "missing-file"
            entry, name = listing.rstrip(b"\0").split(b"\t", 1)
            mode, kind, oid = entry.decode("ascii").split()
            if name != part.encode("utf-8"):
                raise GitError("Git returned a different tree entry")
            if mode == "120000":
                return None, "symlink"
            if mode == "160000":
                return None, "submodule"
            if index < len(parts) - 1:
                if kind != "tree":
                    return None, "non-directory-component"
                tree = oid
                continue
            if kind != "blob" or mode not in ("100644", "100755"):
                return None, "not-regular-blob"
            size = int(self.run("cat-file", "-s", oid))
            if size > self.limits.blob_bytes:
                return None, "blob-size-limit"
            return self.run("cat-file", "blob", oid, limit=self.limits.blob_bytes), None
        raise GitError("empty path")
