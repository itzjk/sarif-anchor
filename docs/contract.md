# Contract version 1

`--json` emits one object. Keys and status meanings are stable within
`schema_version: 1`; consumers should tolerate additional keys. All array indexes
are zero-based. No timestamps or local absolute paths are added to normal results.

Top-level fields include `commit`, `requested_ref`, `analysis_revision`,
`declared_revisions`, `empty`, `complete`, `has_discrepancies`, `exit_code`,
`coverage`, `exclusions`, and `locations`. Each location has `run_index`,
`result_index`, `result_id`, `location_index`, `uri`, decoded `path`, `range`,
`quoted`, `observed`, `status`, and `reason`. The result identifier is the result
GUID when supplied, otherwise its rule ID, otherwise a generated run/result key.
The indexes disambiguate repeated rule IDs. Missing evidence is JSON `null`.

| Status | Evidence established |
| --- | --- |
| `snippet-match` | The declared range exists and its normalized text equals the quote |
| `snippet-mismatch` | The range exists; `observed` differs from `quoted` |
| `missing-file` | No entry exists at the path in the requested tree |
| `invalid-range` | Lines are not positive ordered integers, or exceed the blob |
| `range-only` | The range exists, but no snippet was supplied |
| `unverified` | An unsupported representation or resource/access limit prevented the check |

## Regions and text

SARIF 2.1.0 sections 3.30.5 through 3.30.8 define one-based line numbers,
`endLine` defaulting to `startLine`, and omitted columns covering the full line
excluding its final newline. Section 3.30.13 defines the snippet as the content
of that region. These are the supported semantics from the
[OASIS specification](https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html#_Toc34317685).

Replace CRLF with LF in both blob and quote. Split only on LF. Join selected
lines with LF, without the final line terminator. Do not trim or add a trailing
newline. A trailing newline in the quote therefore mismatches a region that
excludes it. An empty blob has no source lines; `a\n` has one, and `\n` has one
empty line. Lone CR and Unicode separators remain content, not line boundaries.
The default SARIF `newlineSequences` is supported; other sequences are unverified.

Sources are decoded as UTF-8. A direct-URI entry in `run.artifacts` can declare
its `encoding`, overriding `run.defaultEncoding`. Only UTF-8 declarations are
supported; other encodings remain unverified even when their bytes also happen
to be valid UTF-8. If no encoding is declared, UTF-8 is assumed. A leading UTF-8
byte order mark also remains unverified, rather than being counted as source
content or removed by an additional normalization step.

URI percent escapes are decoded once as strict UTF-8. Direct paths with spaces
must encode them as `%20`. Absolute paths, schemes, authorities, queries,
fragments, malformed escapes, backslashes, controls, empty path components, `.`,
and `..` are rejected as unverified, before blob access. Encoded slash and
backslash separators are also refused. Paths are case-sensitive
Git tree names, independent of the checkout filesystem. URI bases and artifact
indexes are not resolved, even when a direct URI is also present.

## Coverage and revisions

`coverage.statuses` counts primary locations by status. `primary_locations`
includes every primary location examined, and `results_without_locations` counts
results that provided none. `secondary_locations_excluded` counts related
locations, flow steps (including indirect steps), stack/graph locations, and
annotations, including run-level flow locations and graph nodes.
Invocation and conversion notification locations are also counted as secondary.
`context_regions_excluded` counts primary context regions.
Inline external properties, external references, or aggregated run data get an
explicit exclusion; their unavailable location count is unknown, not zero
evidence. None of these references is loaded.

`complete` means every primary location was checked with a snippet and there
were no exclusions, absent primary locations, or empty reports. It can be true
with a discrepancy. `has_discrepancies` is independent. Exit priority is invalid
input (`3`), then discrepancies (`1`), then incomplete (`2`), then matches (`0`).

`versionControlProvenance[].revisionId` is recorded per run. Only literal equality
with the resolved full commit ID (ignoring hex case) is `same-commit`; every
other value is `different-revision` and causes exit `1`. Abbreviations, branches,
and provenance for other repositories are not inferred to mean this commit.
Review these declarations when a multi-repository report differs. A declaration
is a producer claim, not proof that an analysis actually ran there. No declaration
means `analysis_revision: "not-declared"` even when every snippet matches.

## Read boundaries and resource limits

The CLI reads a regular report file and invokes system Git with separate
arguments, never a shell. Refs starting with `-` are refused. Git globals,
injected Git environment configuration, replace objects, hooks, filesystem
monitoring, network protocols, and lazy object fetches are disabled. Git still
reads repository metadata needed to locate local objects. It uses `rev-parse`,
`ls-tree`, and raw `cat-file`, without checkout, filters, textconv, or project
configuration loaders. Each tree component is checked before descent, so parent
symlinks and gitlinks are refused too. Regular executable blobs are supported.

Defaults: 16 MiB input, 4 MiB per blob, 8 MiB per Git command output, 8 MiB total
observed evidence, 10,000 primary locations, 5 seconds per Git command, and a
30-second Git-access deadline. Git 2.45+ is required so `--no-lazy-fetch` is
recognized; older Git fails before accessing repository objects. Blob limits and unavailable objects produce
`unverified`; initialization limits and excessive report/location counts are
input errors. Diagnostic reasons remain in the output. Human evidence previews
are limited to 160 characters and terminal controls are escaped; JSON contains
full accepted observed evidence.

If the observed-evidence budget is exceeded after comparison, the measured
status is retained, `observed` becomes null, and an exclusion identifies that
location. Coverage is incomplete; an already established mismatch still causes
exit `1` rather than being replaced by an incomplete-only outcome.

Invalid-input JSON has `schema_version`, `error`, `commit`, and `exit_code: 3`.
`commit` is null if validation failed before resolving a revision; otherwise it
retains the resolved commit, even when a later input-shape error aborts checking.

The repository and Git executable are local trusted inputs. This is not an OS
sandbox for malicious Git installations or repository metadata pointing to
additional local object stores. Source URIs never become filesystem reads.
