# Development and reproducible demo

From a clean clone, install and run the dependency-free integration suite:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests -v
```

The suite uses real temporary Git repositories and synthetic citations. It
checks dirty worktrees and staged files, both revisions, modified and missing
blobs, newline and whitespace semantics, Unicode paths, unsupported locations,
ref and URI rejection, symlinks, gitlinks, binary blobs, resource limits, and
read-only behavior. A missing promisor object is paired with a tripwire remote
helper to ensure a check does not invoke a lazy fetch. Filters and textconv are
also paired with a tripwire.

To reproduce the GIF scenario from the repository root:

```sh
python3 examples/make_demo.py .demo-repo
.venv/bin/sarif-anchor examples/report.sarif --repo .demo-repo --ref original
.venv/bin/sarif-anchor examples/report.sarif --repo .demo-repo --ref shifted
```

The first check exits `0`, the second `1`. Both name their resolved commit.
`make_demo.py` refuses to overwrite an existing destination. It leaves the
checkout dirty deliberately. It creates no security vulnerability or finding.

With VHS, ttyd, a working Chromium browser, and ffmpeg installed:

```sh
PATH="$PWD/.venv/bin:$PATH" vhs docs/demo.tape
```

The tape creates its own temporary two-commit repository. All visible commands
run the installed CLI. Keep the final mismatch visible when adjusting timing.

## Official SARIF validation

With the .NET 8 SDK installed:

```sh
dotnet tool install --tool-path .work/tools Sarif.Multitool --version 5.8.0
.work/tools/sarif validate examples/report.sarif --config docs/sdk-offline.xml --rule-kind Sarif
```

The config disables URL reachability only. Do not pass `--insert`,
`--update-inputs-to-current-sarif`, or fix options. The recorded fixture hash and
validator outcome live in [comparison-results.json](comparison-results.json).
The suite checks this hash so changing the fixture requires revalidation.

## Regressions that must fail

Replacing `GitTree.blob` with a worktree read must fail
`test_exact_commit_ignores_dirty_checkout_and_index`. Changing the no-snippet
branch to return `snippet-match` must fail `test_range_only_is_incomplete`.
Both mutations were run in disposable copies during delivery and produced
failing tests. They protect the two central claims, not just parser behavior.

CI runs the integration suite on Linux and macOS with Python 3.10 and 3.13.
Remote installation and workflow execution can be checked after the repository
has been published; a local build cannot establish that those remote actions ran.
