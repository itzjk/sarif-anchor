# Existing-tool check

Checked on 2026-10-29 before implementing the citation checker. These are bounded
tests of the published interfaces and source, not a claim that no equivalent
tool exists. Repository popularity did not enter the comparison.

## The task

Create a repository with two commits. `original` contains the quoted return
statement on line 2; `shifted` inserts a statement before it. Leave the checkout
dirty, unrelated to either commit. Ask each tool to check the same existing SARIF
against both commits, retaining the quote and coordinates, without a checkout.
The reproducible fixture is [report.sarif](../examples/report.sarif) and the
setup script is [make_demo.py](../examples/make_demo.py).

## Microsoft SARIF SDK

Inspected source commit
[`986c3f9`](https://github.com/microsoft/sarif-sdk/tree/986c3f9425d8b4df4b56febcf85dd9d43d6d6cd8),
including `ValidateOptions`, `ValidateCommand`, `RewriteOptions`,
`SARIF1007.RegionPropertiesMustBeConsistent`, and emit-finalize region handling.
Ran the published NuGet `Sarif.Multitool` 5.8.0 under .NET 8.

`validate` checks the schema and region consistency. Passing `--repo` and
`--ref original` exits `1` with both options reported unknown. Its interface
therefore could not perform the requested pair of commit-selected checks.
`rewrite` enriches logs; `--insert RegionSnippets` populates evidence rather than
providing this read-only comparison contract. Current source's emit-finalize
handling explicitly resolves snippets against an on-disk checkout before
rebasing paths. That is useful for creating SARIF, but does not reproduce this
dirty-checkout demo against arbitrary local commits with unchanged citations.

The fixture itself passed the official validator with exit `0`, with warnings
about omitted tool metadata. The URL-reachability rule `SARIF2006` was disabled
using [sdk-offline.xml](sdk-offline.xml), while SARIF structural and consistency
rules ran. The first default-rule attempt failed in this environment's .NET
network initialization; it is not counted as a validation pass. No fixes or
input-update flags were used. This run is not a comprehensive compliance audit.

## Adjudge

Cloned the brief's [takoneko8/adjudge](https://github.com/takoneko8/adjudge) URL,
inspected commit
[`6a85224`](https://github.com/takoneko8/adjudge/tree/6a852248e229aef52632cf17bb56fc2191f7b30f),
and installed its version 0.1.0. Its package metadata identifies the upstream as
Kasumiki/adjudge. `triage ... --dry-run` ingested the fixture and exited `0`;
adding `--repo ... --ref original` exited `2` because `--repo` is unknown.
The CLI source and `verify --help` describe opt-in host-scoped network checks,
not local Git citation comparisons. No active verification or model call ran.

## Decision

Continue with the narrowly scoped CLI. Neither tested interface completed this
demo directly. The SDK remains the schema/consistency validator; Adjudge remains
a triage tool. sarif-anchor does not implement either workflow or claim that
matching source text verifies a security finding.

[comparison-results.json](comparison-results.json) records the fixture SHA-256,
versions, source commits, exit codes, and captured output. Machine-specific
project paths are replaced with `$PROJECT`.
Captured SDK and Adjudge output is attributed to their respective projects,
both distributed under the MIT license: [SDK license](https://github.com/microsoft/sarif-sdk/blob/main/LICENSE)
and [Adjudge license](https://github.com/takoneko8/adjudge/blob/main/LICENSE).
