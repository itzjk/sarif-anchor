import argparse
import json
import sys

from . import __version__
from .check import InputError, check
from .git import GitError, Limits


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(message)


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def human(report):
    print("Commit: " + report["commit"])
    print("Analysis revision: " + report["analysis_revision"])
    for declared in report["declared_revisions"]:
        print("Declared revision: " + json.dumps(declared["revision"]) + " (" + declared["status"] + ")")
    for loc in report["locations"]:
        region = loc["range"]
        label = json.dumps(loc["path"] or loc["uri"], ensure_ascii=True)
        print(f"{loc['status']}  {label}:{region['startLine']}-{region['endLine']}"
              f"  result={json.dumps(loc['result_id'])} location={loc['location_index']}")
        if loc["reason"]:
            print("  Reason: " + loc["reason"])
        if loc["observed"] is not None:
            preview = loc["observed"][:160]
            print("  Observed: " + json.dumps(preview, ensure_ascii=True)
                  + ("... (full evidence in --json)" if len(loc["observed"]) > 160 else ""))
    cov = report["coverage"]
    print("Coverage: " + ", ".join(f"{v} {k}" for k, v in cov["statuses"].items()))
    print(f"Excluded: {cov['secondary_locations_excluded']} secondary locations, "
          f"{cov['context_regions_excluded']} context regions; "
          f"{cov['results_without_locations']} results without primary locations")
    for exclusion in report["exclusions"]:
        print("Excluded: " + exclusion["reason"])
    if report["empty"]:
        print("Empty report: no evidence to verify.")
    print(f"Complete: {str(report['complete']).lower()}; "
          f"discrepancies: {str(report['has_discrepancies']).lower()}; exit {report['exit_code']}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = Parser(description="Check SARIF source citations against a local Git commit.")
    parser.add_argument("report", help="SARIF 2.1.0 JSON file")
    parser.add_argument("--repo", required=True, help="local Git repository")
    parser.add_argument("--ref", required=True, help="revision to resolve to a commit")
    parser.add_argument("--json", action="store_true", help="emit stable JSON instead of text")
    parser.add_argument("--max-blob-bytes", type=positive, default=Limits.blob_bytes)
    parser.add_argument("--max-report-bytes", type=positive, default=Limits.report_bytes)
    parser.add_argument("--version", action="version", version=__version__)
    try:
        args = parser.parse_args(argv)
        report = check(args.report, args.repo, args.ref,
                       Limits(blob_bytes=args.max_blob_bytes, report_bytes=args.max_report_bytes))
    except (InputError, GitError, ValueError, RecursionError, UnicodeError) as exc:
        error = dict(schema_version=1, error=str(exc), commit=getattr(exc, "commit", None), exit_code=3)
        if "--json" in argv:
            print(json.dumps(error, ensure_ascii=True, sort_keys=True))
        else:
            if error["commit"]:
                print("Commit: " + error["commit"], file=sys.stderr)
            print("sarif-anchor: " + json.dumps(str(exc)), file=sys.stderr)
        return 3
    try:
        if args.json:
            print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        else:
            human(report)
    except BrokenPipeError:
        return report["exit_code"]
    return report["exit_code"]
