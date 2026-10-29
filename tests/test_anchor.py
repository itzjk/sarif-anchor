import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from sarif_anchor.check import InputError, check
from sarif_anchor.git import GitError, GitTree, LimitError, Limits


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = json.loads((ROOT / "examples/report.sarif").read_text())


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo with spaces"
        self.repo.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Example", GIT_AUTHOR_EMAIL="example@example.invalid",
                        GIT_COMMITTER_NAME="Example", GIT_COMMITTER_EMAIL="example@example.invalid")
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.source = self.repo / "example.py"
        self.source.write_bytes(b"def clean(name):\n    return name.strip()\n")
        self.first = self.commit()
        self.report = self.base / "report.sarif"
        self.data = copy.deepcopy(EXAMPLE)
        self.write_report()

    def git(self, *args, input=None):
        return subprocess.check_output(["git", "-C", str(self.repo), "-c",
                                        "core.hooksPath=" + os.devnull, *args],
                                       env=self.env, input=input, stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "Update synthetic source")
        return self.git("rev-parse", "HEAD")

    def write_report(self):
        self.report.write_text(json.dumps(self.data), encoding="utf-8")

    def run_check(self, ref=None, **kwargs):
        self.write_report()
        return check(self.report, self.repo, ref or self.first, **kwargs)

    @property
    def result(self):
        return self.data["runs"][0]["results"][0]

    @property
    def physical(self):
        return self.result["locations"][0]["physicalLocation"]

    def test_exact_commit_ignores_dirty_checkout_and_index(self):
        self.source.write_text("staged different text\n")
        self.git("add", "example.py")
        self.source.write_text("dirty different text\n")
        before = (self.git("rev-parse", "HEAD"), self.git("status", "--porcelain"),
                  (self.repo / ".git/index").read_bytes(), self.source.read_bytes(), self.report.read_bytes())
        result = self.run_check()
        self.assertEqual(result["locations"][0]["status"], "snippet-match")
        self.assertEqual(result["commit"], self.first)
        self.assertEqual(result["exit_code"], 0)
        after = (self.git("rev-parse", "HEAD"), self.git("status", "--porcelain"),
                 (self.repo / ".git/index").read_bytes(), self.source.read_bytes(), self.report.read_bytes())
        self.assertEqual(before, after)

    def test_line_shift_and_changed_text_do_not_relocate(self):
        for content in ("def clean(name):\n    name = str(name)\n    return name.strip()\n",
                        "def clean(name):\n    return name.lower()\n"):
            with self.subTest(content=content):
                self.source.write_text(content)
                revision = self.commit()
                report = self.run_check(revision)
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(report["locations"][0]["status"], "snippet-mismatch")
                self.assertEqual(report["locations"][0]["range"], {"startLine": 2, "endLine": 2})
                self.assertEqual(self.run_check()["exit_code"], 0)

    def test_deleted_blob(self):
        self.source.unlink()
        result = self.run_check(self.commit())
        self.assertEqual(result["locations"][0]["status"], "missing-file")

    def test_invalid_ranges(self):
        for start, end in ((0, 1), (-1, 1), (2, 1), (1, 3), (3, 3), (True, 2), (1, "2")):
            with self.subTest(start=start, end=end):
                self.physical["region"].update(startLine=start, endLine=end)
                result = self.run_check()
                self.assertEqual(result["locations"][0]["status"], "invalid-range")
                self.assertEqual(result["exit_code"], 1)

    def test_range_only_is_incomplete(self):
        del self.physical["region"]["snippet"]
        result = self.run_check()
        self.assertEqual(result["locations"][0]["status"], "range-only")
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["complete"])

    def test_empty_and_absent_results(self):
        for data in ({"version": "2.1.0", "runs": []},
                     {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "test"}}}]}):
            self.data = data
            result = self.run_check()
            self.assertTrue(result["empty"])
            self.assertEqual(result["exit_code"], 2)

    def test_result_without_primary_location(self):
        self.result.pop("locations")
        result = self.run_check()
        self.assertEqual(result["coverage"]["results_without_locations"], 1)
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["empty"])

    def test_multiple_primary_and_secondary_locations_preserve_mixed_outcome(self):
        self.result["locations"].append(copy.deepcopy(self.result["locations"][0]))
        self.result["locations"][1]["physicalLocation"]["region"]["snippet"]["text"] = "wrong"
        unsupported = copy.deepcopy(self.result["locations"][0])
        unsupported["physicalLocation"]["region"]["startColumn"] = 1
        self.result["locations"].append(unsupported)
        self.result["relatedLocations"] = [copy.deepcopy(self.result["locations"][0])]
        self.result["codeFlows"] = [{"threadFlows": [{"locations": [{"index": 0}, {"location": {}}]}]}]
        result = self.run_check()
        self.assertEqual([r["location_index"] for r in result["locations"]], [0, 1, 2])
        self.assertEqual(result["coverage"]["secondary_locations_excluded"], 3)
        self.assertEqual(result["coverage"]["statuses"]["snippet-mismatch"], 1)
        self.assertEqual(result["coverage"]["statuses"]["unverified"], 1)
        self.assertEqual(result["exit_code"], 1)
        self.assertFalse(result["complete"])

    def test_full_lines_crlf_unicode_tabs_and_spaces(self):
        text = "one\n\t café  \nthird\n"
        self.source.write_bytes(text.replace("\n", "\r\n").encode())
        revision = self.commit()
        self.physical["region"] = {"startLine": 1, "endLine": 2,
                                    "snippet": {"text": "one\r\n\t café  "}}
        result = self.run_check(revision)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["locations"][0]["observed"], "one\n\t café  ")
        for bad in ("one\n café  ", "one\n\t café", "one\n\t café  \n"):
            self.physical["region"]["snippet"]["text"] = bad
            self.assertEqual(self.run_check(revision)["exit_code"], 1)

    def test_no_final_newline_empty_file_and_empty_line(self):
        for content, start, quote, expected in ((b"a\nb", 2, "b", "snippet-match"),
                                                (b"", 1, "", "invalid-range"),
                                                (b"\n", 1, "", "snippet-match"),
                                                (b"a\n", 2, "", "invalid-range"),
                                                (b"a\x0bb\n", 1, "a\x0bb", "snippet-match")):
            with self.subTest(content=content):
                self.source.write_bytes(content)
                revision = self.commit()
                self.physical["region"] = {"startLine": start, "snippet": {"text": quote}}
                self.assertEqual(self.run_check(revision)["locations"][0]["status"], expected)

    def test_unsafe_uris_are_unverified_without_external_reads(self):
        for uri in ("../secret", "%2e%2e/secret", "a/%2e%2e/secret", "file:///etc/passwd",
                    "/etc/passwd", "https://example.invalid/file", "//example.invalid/file",
                    "C:/secret", "a\\b", "a%00b", "%2Fetc/passwd", "a%2fb", "a%ZZ", "a?x", "a#x", "a/./b"):
            with self.subTest(uri=uri):
                self.physical["artifactLocation"]["uri"] = uri
                with patch.object(GitTree, "blob", side_effect=AssertionError("URI reached Git")):
                    result = self.run_check()
                self.assertEqual(result["locations"][0]["status"], "unverified")
                self.assertEqual(result["exit_code"], 2)

    def test_nested_and_encoded_literal_paths(self):
        folder = self.repo / "folder with spaces"
        folder.mkdir()
        (folder / "[café]*.py").write_bytes(self.source.read_bytes())
        revision = self.commit()
        self.physical["artifactLocation"]["uri"] = "folder%20with%20spaces/%5Bcaf%C3%A9%5D%2A.py"
        self.assertEqual(self.run_check(revision)["exit_code"], 0)

    def test_malformed_authority_preserves_other_discrepancies(self):
        self.physical["region"]["snippet"]["text"] = "different"
        for uri in ("//[bad/path", "http://[::1"):
            with self.subTest(uri=uri):
                unsupported = copy.deepcopy(self.result["locations"][0])
                unsupported["physicalLocation"]["artifactLocation"]["uri"] = uri
                self.result["locations"] = [self.result["locations"][0], unsupported]
                result = self.run_check()
                self.assertEqual(result["exit_code"], 1)
                self.assertFalse(result["complete"])
                self.assertEqual(result["locations"][0]["status"], "snippet-mismatch")
                self.assertEqual(result["locations"][1]["status"], "unverified")
                self.assertEqual(result["locations"][1]["reason"], "invalid-relative-uri")

    def test_declared_encoding_is_honored_before_decoding(self):
        self.source.write_bytes(b"AB")
        revision = self.commit()
        self.physical["region"] = {"startLine": 1, "snippet": {"text": "AB"}}
        run = self.data["runs"][0]
        for name in ("UTF-16BE", "latin1"):
            with self.subTest(encoding=name):
                run["defaultEncoding"] = name
                result = self.run_check(revision)
                self.assertEqual(result["locations"][0]["reason"], "unsupported-source-encoding")
                self.assertEqual(result["exit_code"], 2)
                self.assertIsNone(result["locations"][0]["observed"])
                del run["defaultEncoding"]
                run["artifacts"] = [{"location": {"uri": "example.py"}, "encoding": name}]
                self.assertEqual(self.run_check(revision)["exit_code"], 2)
                del run["artifacts"]
        run["defaultEncoding"] = "UTF-16BE"
        run["artifacts"] = [{"location": {"uri": "example.py"}, "encoding": "UTF-8"}]
        self.assertEqual(self.run_check(revision)["exit_code"], 0)
        run["defaultEncoding"] = "utf-8"
        run["artifacts"] = [{"location": {"uri": "unrelated.py"}, "encoding": "UTF-16BE"}]
        self.assertEqual(self.run_check(revision)["exit_code"], 0)

    def test_unresolved_base_and_indirect_reference(self):
        for key, value in (("uriBaseId", "%SRCROOT%"), ("index", 0)):
            with self.subTest(key=key):
                self.physical["artifactLocation"][key] = value
                self.assertEqual(self.run_check()["exit_code"], 2)
                del self.physical["artifactLocation"][key]

    def test_columns_offsets_custom_newlines_and_binary_snippet(self):
        for key in ("startColumn", "endColumn", "charOffset", "charLength", "byteOffset", "byteLength"):
            self.physical["region"][key] = 1
            self.assertEqual(self.run_check()["locations"][0]["reason"], "columns-or-offsets")
            del self.physical["region"][key]
        self.data["runs"][0]["newlineSequences"] = ["\r"]
        self.assertEqual(self.run_check()["exit_code"], 2)
        del self.data["runs"][0]["newlineSequences"]
        self.physical["region"]["snippet"] = {"binary": "YQ=="}
        self.assertEqual(self.run_check()["exit_code"], 2)

    def test_symlink_and_parent_symlink_not_followed(self):
        target = self.base / "outside"
        target.mkdir()
        secret = target / "secret"
        secret.write_text("private\n")
        (self.repo / "link").symlink_to(target, target_is_directory=True)
        revision = self.commit()
        for path in ("link", "link/secret"):
            self.physical["artifactLocation"]["uri"] = path
            result = self.run_check(revision)
            self.assertEqual(result["locations"][0]["reason"], "symlink")
            self.assertIsNone(result["locations"][0]["observed"])
        self.assertEqual(secret.read_text(), "private\n")

    def test_submodule_not_entered(self):
        self.git("update-index", "--add", "--cacheinfo", "160000," + self.first + ",vendor")
        self.git("commit", "-qm", "Add synthetic gitlink")
        for path in ("vendor", "vendor/example.py"):
            self.physical["artifactLocation"]["uri"] = path
            result = self.run_check("HEAD")
            self.assertEqual(result["locations"][0]["reason"], "submodule")

    def test_binary_and_non_utf8(self):
        for content in (b"a\0b", b"a\xffb"):
            self.source.write_bytes(content)
            result = self.run_check(self.commit())
            self.assertEqual(result["locations"][0]["reason"], "binary-or-non-utf8")

    def test_byte_order_mark_is_not_treated_as_source_content(self):
        self.source.write_bytes(b"\xef\xbb\xbfAB")
        revision = self.commit()
        self.physical["region"] = {"startLine": 1, "snippet": {"text": "\ufeffAB"}}
        result = self.run_check(revision)
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["locations"][0]["reason"], "unsupported-byte-order-mark")

    def test_directory_is_not_regular_blob(self):
        (self.repo / "dir").mkdir()
        (self.repo / "dir/file").write_text("hi")
        revision = self.commit()
        self.physical["artifactLocation"]["uri"] = "dir"
        self.assertEqual(self.run_check(revision)["locations"][0]["reason"], "not-regular-blob")

    def test_revision_provenance_is_separate_from_snippet(self):
        provenance = {"repositoryUri": "https://example.invalid/repo", "revisionId": self.first}
        self.data["runs"][0]["versionControlProvenance"] = [provenance]
        self.assertEqual(self.run_check()["declared_revisions"][0]["status"], "same-commit")
        provenance["revisionId"] = "0" * 40
        result = self.run_check()
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["locations"][0]["status"], "snippet-match")
        self.assertEqual(result["declared_revisions"][0]["status"], "different-revision")

    def test_invalid_refs_and_option_injection(self):
        for ref in ("--help", "-c", "", "nonexistent", "HEAD\0", "HEAD:example.py"):
            with self.subTest(ref=ref), self.assertRaises(GitError):
                check(self.report, self.repo, ref)

    def test_limits(self):
        self.assertEqual(self.run_check(limits=Limits(blob_bytes=1))["locations"][0]["reason"], "blob-size-limit")
        with self.assertRaises(InputError):
            self.run_check(limits=Limits(report_bytes=1))
        with self.assertRaises(InputError):
            self.result["locations"] *= 2
            self.run_check(limits=Limits(locations=1))
        self.result["locations"] = self.result["locations"][:1]
        self.assertEqual(self.run_check(limits=Limits(output_bytes=100))["exit_code"], 0)
        with self.assertRaises(LimitError):
            GitTree(self.repo, self.first, Limits(output_bytes=1))
        with self.assertRaises(LimitError):
            GitTree(self.repo, self.first, Limits(total_seconds=0))
        tree = GitTree(self.repo, self.first, Limits())
        tree.deadline = time.monotonic() - 1
        with self.assertRaises(LimitError):
            tree.blob("example.py")

    def test_git_timeout_and_stream_limit_kill_the_process(self):
        popen = subprocess.Popen
        for program, limits, reason in (
                ("import time; time.sleep(10)", Limits(git_seconds=0.05), "time limit"),
                ("import sys,time; sys.stdout.buffer.write(b'x'*100000); sys.stdout.flush(); time.sleep(10)",
                 Limits(output_bytes=64), "size limit")):
            processes = []

            def launch(*args, **kwargs):
                process = popen([sys.executable, "-c", program], **kwargs)
                processes.append(process)
                return process

            started = time.monotonic()
            with patch("sarif_anchor.git.subprocess.Popen", side_effect=launch):
                with self.assertRaisesRegex(LimitError, reason):
                    GitTree(self.repo, self.first, limits)
            self.assertLess(time.monotonic() - started, 3)
            self.assertTrue(all(p.poll() is not None for p in processes))

    def test_evidence_budget_prevents_full_success(self):
        self.source.write_text("x" * 200 + "\n")
        revision = self.commit()
        self.physical["region"] = {"startLine": 1, "snippet": {"text": "x" * 200}}
        result = self.run_check(revision, limits=Limits(output_bytes=150))
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["locations"][0]["reason"], "evidence-output-size-limit")

    def test_evidence_budget_preserves_known_discrepancies(self):
        self.source.write_text("x" * 200 + "\n")
        revision = self.commit()
        self.physical["region"] = {"startLine": 1, "snippet": {"text": "different"}}
        unsupported = copy.deepcopy(self.result["locations"][0])
        unsupported["physicalLocation"]["artifactLocation"]["uri"] = "https://example.invalid/source"
        self.result["locations"].append(unsupported)
        result = self.run_check(revision, limits=Limits(output_bytes=150))
        self.assertEqual(result["exit_code"], 1)
        self.assertTrue(result["has_discrepancies"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["locations"][0]["status"], "snippet-mismatch")
        self.assertIsNone(result["locations"][0]["observed"])
        self.assertEqual(result["exclusions"][0]["reason"], "evidence-output-size-limit")
        self.assertEqual(result["locations"][1]["reason"], "absolute-or-remote-uri")
        self.assertEqual(len(result["exclusions"]), 1)

    def test_special_report_is_rejected(self):
        fifo = self.base / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(InputError, "regular file"):
            check(fifo, self.repo, self.first)

    def test_no_filters_hooks_or_lazy_fetch(self):
        marker = self.base / "executed"
        script = self.base / "tripwire.sh"
        script.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\nexit 1\n")
        script.chmod(0o755)
        self.git("config", "filter.tripwire.smudge", str(script))
        self.git("config", "diff.tripwire.textconv", str(script))
        (self.repo / ".gitattributes").write_text("*.py filter=tripwire diff=tripwire\n")
        revision = self.commit()
        self.assertEqual(self.run_check(revision)["exit_code"], 0)
        oid = self.git("rev-parse", revision + ":example.py")
        self.git("config", "extensions.partialClone", "origin")
        self.git("config", "remote.origin.promisor", "true")
        self.git("config", "remote.origin.url", "tripwire::remote")
        self.git("config", "protocol.tripwire.allow", "always")
        helper = self.base / "git-remote-tripwire"
        shutil.copyfile(script, helper)
        helper.chmod(0o755)
        (self.repo / ".git/objects" / oid[:2] / oid[2:]).unlink()
        with patch.dict(os.environ, {"PATH": str(self.base) + os.pathsep + os.environ["PATH"]}):
            result = self.run_check(revision)
        self.assertEqual(result["locations"][0]["reason"], "local-object-unavailable")
        self.assertFalse(marker.exists())

    def test_git_environment_injection_and_replace_objects_ignored(self):
        other = self.base / "other"
        other.mkdir()
        with patch.dict(os.environ, {"GIT_DIR": str(other), "GIT_CONFIG_COUNT": "1",
                                    "GIT_CONFIG_KEY_0": "alias.cat-file", "GIT_CONFIG_VALUE_0": "!false"}):
            self.assertEqual(self.run_check()["exit_code"], 0)
        self.source.write_text("different\n")
        second = self.commit()
        self.git("replace", self.first, second)
        self.assertEqual(self.run_check()["exit_code"], 0)

    def test_external_properties_and_contexts_are_excluded(self):
        self.data["runs"][0]["externalPropertyFileReferences"] = {"results": [{"location": {"uri": "remote"}}]}
        self.physical["contextRegion"] = {"startLine": 1, "endLine": 2}
        result = self.run_check()
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["coverage"]["context_regions_excluded"], 1)
        self.assertTrue(result["exclusions"])

    def test_run_level_locations_are_counted(self):
        self.data["runs"][0]["threadFlowLocations"] = [{"location": {}}]
        self.data["runs"][0]["graphs"] = [{"nodes": [{"id": "n", "location": {}}]}]
        result = self.run_check()
        self.assertEqual(result["coverage"]["secondary_locations_excluded"], 2)
        self.assertEqual(result["exit_code"], 2)

    def test_notification_locations_and_inline_data_are_excluded(self):
        invocation = {"executionSuccessful": True,
                      "toolExecutionNotifications": [{"message": {"text": "example"},
                                                       "locations": [{"physicalLocation": self.physical}]}],
                      "toolConfigurationNotifications": [{"message": {"text": "example"},
                                                           "locations": [{}]}]}
        self.data["runs"][0]["invocations"] = [invocation]
        self.data["runs"][0]["conversion"] = {"invocation": invocation}
        result = self.run_check()
        self.assertEqual(result["coverage"]["secondary_locations_excluded"], 4)
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse(result["complete"])
        del self.data["runs"][0]["invocations"]
        del self.data["runs"][0]["conversion"]
        self.data["inlineExternalProperties"] = [{"version": "2.1.0", "results": []}]
        result = self.run_check()
        self.assertEqual(result["exclusions"][0]["reason"], "inline-external-data-excluded")
        self.assertEqual(result["exit_code"], 2)

    def test_cli_json_is_stable_and_errors_use_exit_three(self):
        command = [sys.executable, "-m", "sarif_anchor", str(self.report), "--repo", str(self.repo), "--ref", self.first, "--json"]
        one = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        two = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(one.returncode, 0)
        self.assertEqual(one.stdout, two.stdout)
        self.assertEqual(json.loads(one.stdout)["schema_version"], 1)
        for raw in ("{", '{"version":"2.1.0","runs":[],"runs":[]}',
                    '{"version":"2.1.0","runs":NaN}', '{"version":"1.0.0","runs":[]}'):
            self.report.write_text(raw)
            process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(process.returncode, 3)
            self.assertEqual(json.loads(process.stdout)["exit_code"], 3)

    def test_late_input_error_keeps_resolved_commit(self):
        self.physical["region"] = []
        self.write_report()
        process = subprocess.run([sys.executable, "-m", "sarif_anchor", str(self.report),
                                  "--repo", str(self.repo), "--ref", self.first, "--json"],
                                 cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(process.returncode, 3)
        self.assertEqual(json.loads(process.stdout)["commit"], self.first)

    def test_large_byte_limit_options_do_not_preallocate_the_limit(self):
        process = subprocess.run([sys.executable, "-m", "sarif_anchor", str(self.report),
                                  "--repo", str(self.repo), "--ref", self.first, "--json",
                                  "--max-report-bytes", str(2 ** 128),
                                  "--max-blob-bytes", str(2 ** 128)],
                                 cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["locations"][0]["status"], "snippet-match")

    def test_bare_repository_and_annotated_tag(self):
        self.git("tag", "-am", "Original", "original", self.first)
        self.assertEqual(self.run_check("original")["commit"], self.first)
        bare = self.base / "bare.git"
        subprocess.check_call(["git", "clone", "--bare", "-q", str(self.repo), str(bare)], env=self.env)
        self.assertEqual(check(self.report, bare, self.first)["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
