import json
import os
import re
import stat
from urllib.parse import unquote, urlsplit

from .git import GitError, GitTree, LimitError, Limits, read_limited


class InputError(Exception):
    pass


def object_value(value, label):
    if not isinstance(value, dict):
        raise InputError(label + " must be an object")
    return value


def array_value(value, label):
    if not isinstance(value, list):
        raise InputError(label + " must be an array")
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON property: " + key)
        result[key] = value
    return result


def invalid_constant(value):
    raise InputError("non-finite JSON number: " + value)


def load_report(path, limits):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InputError("report must be a regular file")
            raw = read_limited(stream, limits.report_bytes)
        if len(raw) > limits.report_bytes:
            raise InputError("report size limit exceeded")
        report = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object,
                            parse_constant=invalid_constant)
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError("cannot read a UTF-8 JSON report") from exc
    object_value(report, "report")
    if report.get("version") != "2.1.0":
        raise InputError("expected SARIF version 2.1.0")
    array_value(report.get("runs"), "runs")
    for run in report["runs"]:
        object_value(run, "run")
        tool = object_value(run.get("tool"), "tool")
        driver = object_value(tool.get("driver"), "tool.driver")
        if not isinstance(driver.get("name"), str) or not driver["name"]:
            raise InputError("tool.driver.name must be a nonempty string")
        for result in array_value(run.get("results", []), "results"):
            object_value(result, "result")
            object_value(result.get("message"), "result.message")
            for name in ("ruleId", "guid"):
                if name in result and not isinstance(result[name], str):
                    raise InputError(name + " must be a string")
            for loc in array_value(result.get("locations", []), "locations"):
                object_value(loc, "location")
    return report


def repo_path(artifact):
    if "uriBaseId" in artifact:
        return None, "unresolved-uri-base"
    if "index" in artifact:
        return None, "indirect-artifact-reference"
    uri = artifact.get("uri")
    if not isinstance(uri, str) or not uri:
        return None, "missing-uri"
    if any(ord(c) < 33 or ord(c) == 127 for c in uri) or "\\" in uri:
        return None, "invalid-relative-uri"
    if re.search(r"%(?![0-9A-Fa-f]{2})", uri):
        return None, "invalid-uri-escape"
    if re.search(r"%(?:2f|5c)", uri, re.IGNORECASE):
        return None, "encoded-path-separator"
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return None, "invalid-relative-uri"
    if parsed.scheme:
        return None, "absolute-or-remote-uri"
    if parsed.netloc or uri.startswith("/"):
        return None, "absolute-uri"
    if "?" in uri or "#" in uri:
        return None, "uri-query-or-fragment"
    try:
        path = unquote(parsed.path, encoding="utf-8", errors="strict")
        path.encode("utf-8")
    except (UnicodeError, ValueError):
        return None, "invalid-uri-encoding"
    if "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        return None, "unsafe-path"
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return None, "absolute-uri"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return None, "path-traversal-or-empty-component"
    return path, None


def excluded_locations(result):
    count = 0
    stack = [(None, result)]
    while stack:
        key, node = stack.pop()
        if isinstance(node, dict):
            if key == "location":
                count += 1
                continue
            for child_key, value in node.items():
                if child_key in ("relatedLocations", "annotations") or (child_key == "locations" and node is not result):
                    count += len(array_value(value, child_key))
                elif child_key not in ("properties", "fixes"):
                    stack.append((child_key, value))
        elif isinstance(node, list):
            stack.extend((None, item) for item in node)
    return count


def source_encodings(run):
    encodings = {}
    for artifact in array_value(run.get("artifacts", []), "artifacts"):
        object_value(artifact, "artifact")
        location = object_value(artifact.get("location", {}), "artifact.location")
        path, reason = repo_path(location)
        if reason is None and "encoding" in artifact:
            encodings.setdefault(path, []).append(artifact["encoding"])
    return encodings


def check_location(location, run, tree, encodings):
    row = dict(path=None, range=None, quoted=None, observed=None,
               status="unverified", reason=None)
    physical = object_value(location.get("physicalLocation", {}), "physicalLocation")
    artifact = object_value(physical.get("artifactLocation", {}), "artifactLocation")
    path, reason = repo_path(artifact)
    row["path"] = path
    row["uri"] = artifact.get("uri")
    region = object_value(physical.get("region", {}), "region")
    start = region.get("startLine")
    end = region.get("endLine", start)
    row["range"] = {"startLine": start, "endLine": end}
    snippet = region.get("snippet")
    if snippet is not None:
        snippet = object_value(snippet, "region.snippet")
        if "text" in snippet and not isinstance(snippet["text"], str):
            raise InputError("region.snippet.text must be a string")
        row["quoted"] = snippet.get("text")
    if reason:
        row["reason"] = reason
        return row
    declared_encodings = encodings.get(path, [run.get("defaultEncoding", "utf-8")])
    if any(not isinstance(value, str) or value.lower() not in ("utf-8", "utf8")
           for value in declared_encodings):
        row["reason"] = "unsupported-source-encoding"
        return row
    unsupported = {"startColumn", "endColumn", "charOffset", "charLength", "byteOffset", "byteLength"}
    if unsupported.intersection(region):
        row["reason"] = "columns-or-offsets"
        return row
    if run.get("newlineSequences", ["\r\n", "\n"]) != ["\r\n", "\n"]:
        row["reason"] = "custom-newline-sequences"
        return row
    if start is None:
        row["reason"] = "missing-line-region"
        return row
    if type(start) is not int or type(end) is not int or start < 1 or end < start:
        row.update(status="invalid-range", reason="expected-positive-ordered-lines")
        return row
    try:
        blob, reason = tree.blob(path)
    except LimitError as exc:
        row["reason"] = str(exc)
        return row
    except GitError:
        row["reason"] = "local-object-unavailable"
        return row
    if reason:
        row.update(status="missing-file" if reason == "missing-file" else "unverified", reason=reason)
        return row
    if blob.startswith(b"\xef\xbb\xbf"):
        row["reason"] = "unsupported-byte-order-mark"
        return row
    try:
        if b"\0" in blob:
            raise UnicodeError()
        text = blob.decode("utf-8")
    except UnicodeError:
        row["reason"] = "binary-or-non-utf8"
        return row
    lines = text.replace("\r\n", "\n").split("\n") if text else []
    if text.endswith("\n"):
        lines.pop()
    if end > len(lines):
        row.update(status="invalid-range", reason="range-outside-blob")
        return row
    row["observed"] = "\n".join(lines[start - 1:end])
    if snippet is None:
        row.update(status="range-only", reason="no-snippet")
    elif "text" not in snippet or "binary" in snippet:
        row.update(status="unverified", reason="unsupported-snippet-content")
    elif row["observed"] == snippet["text"].replace("\r\n", "\n"):
        row["status"] = "snippet-match"
    else:
        row.update(status="snippet-mismatch", reason="text-differs-at-declared-range")
    return row


def check(report_path, repo, revision, limits=None):
    limits = limits or Limits()
    report = load_report(report_path, limits)
    tree = GitTree(repo, revision, limits)
    try:
        return _check_report(report, tree, revision, limits)
    except InputError as exc:
        exc.commit = tree.commit
        raise


def _check_report(report, tree, revision, limits):
    rows, declarations, exclusions = [], [], []
    result_count = 0
    secondary_count = 0
    no_locations = 0
    context_count = 0
    observed_bytes = 0
    if report.get("inlineExternalProperties"):
        exclusions.append(dict(reason="inline-external-data-excluded"))
    for ri, run in enumerate(report["runs"]):
        encodings = source_encodings(run)
        secondary_count += len(array_value(run.get("threadFlowLocations", []), "threadFlowLocations"))
        secondary_count += excluded_locations({"graphs": run.get("graphs", [])})
        secondary_count += excluded_locations({"invocations": run.get("invocations", []),
                                               "conversion": run.get("conversion", {})})
        for provenance in array_value(run.get("versionControlProvenance", []), "versionControlProvenance"):
            object_value(provenance, "versionControlProvenance entry")
            declared = provenance.get("revisionId")
            if declared is not None and not isinstance(declared, str):
                raise InputError("revisionId must be a string")
            if declared is not None:
                declarations.append(dict(run_index=ri, revision=declared,
                                         repository_uri=provenance.get("repositoryUri"),
                                         status="same-commit" if declared.lower() == tree.commit else "different-revision"))
        if run.get("externalPropertyFileReferences") or run.get("runAggregates"):
            exclusions.append(dict(run_index=ri, reason="external-or-aggregated-data-excluded"))
        for rj, result in enumerate(run.get("results", [])):
            result_count += 1
            secondary_count += excluded_locations(result)
            locations = result.get("locations", [])
            if not locations:
                no_locations += 1
            for li, location in enumerate(locations):
                if len(rows) >= limits.locations:
                    raise InputError("primary location count limit exceeded")
                physical = object_value(location.get("physicalLocation", {}), "physicalLocation")
                if "contextRegion" in physical:
                    context_count += 1
                row = check_location(location, run, tree, encodings)
                observed_bytes += len((row["observed"] or "").encode("utf-8"))
                if row["observed"] is not None and observed_bytes > limits.output_bytes:
                    row.update(observed=None, reason="evidence-output-size-limit")
                    exclusions.append(dict(run_index=ri, result_index=rj, location_index=li,
                                           reason="evidence-output-size-limit"))
                row.update(run_index=ri, result_index=rj,
                           result_id=result.get("guid") or result.get("ruleId") or f"run-{ri}/result-{rj}",
                           location_index=li)
                rows.append(row)
    counts = {name: sum(r["status"] == name for r in rows) for name in (
        "snippet-match", "snippet-mismatch", "missing-file", "invalid-range", "range-only", "unverified")}
    empty = result_count == 0
    incomplete = bool(empty or counts["range-only"] or counts["unverified"] or secondary_count
                      or no_locations or context_count or exclusions)
    mismatch = bool(counts["snippet-mismatch"] or counts["missing-file"] or counts["invalid-range"]
                    or any(p["status"] == "different-revision" for p in declarations))
    exit_code = 1 if mismatch else 2 if incomplete else 0
    return dict(schema_version=1, commit=tree.commit, requested_ref=revision,
                analysis_revision="declared" if declarations else "not-declared",
                declared_revisions=declarations, empty=empty, complete=not incomplete,
                has_discrepancies=mismatch, exit_code=exit_code,
                coverage=dict(results=result_count, primary_locations=len(rows),
                              secondary_locations_excluded=secondary_count,
                              context_regions_excluded=context_count,
                              results_without_locations=no_locations, statuses=counts),
                exclusions=exclusions, locations=rows)
