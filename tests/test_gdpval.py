import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from xml.etree import ElementTree
from zipfile import ZipFile

import envweaver as ew

from eval_anything.benchmarks.gdpval import GDPval
from eval_anything.benchmarks.gdpval.data import Dataset
from eval_anything.benchmarks.gdpval.judge import judge, rendered
from eval_anything.benchmarks.gdpval.office import office_copy
from eval_anything.benchmarks.gdpval.submissions import load_source
from eval_anything.runner import agent_class, evaluate


class GDPvalTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(ew, "StirrupAgent"), "requires Stirrup SDK")
    def test_model_session_is_scoped_to_one_generation_attempt(self):
        options = {
            "headers": {"x-session-id": "stale", "X-Client": "client"},
            "extra_headers": {"X-SESSION-ID": "stale", "X-Trace": "trace"},
        }
        bench = GDPval()
        agent = agent_class(bench, bench.harness)(
            model="openai/test", context_window_tokens=4096, max_tokens=64, client_kwargs=options
        )
        sessions = set()
        for attempt in (agent.new(), agent.new()):
            attempt._python = "python3"
            attempt._remote = "/tmp/stirrup"
            attempt._output = Mock()
            with patch.object(attempt, "_upload_text", return_value="settings.json") as upload:
                attempt._command("do the task", [])
            session = attempt._output.update.call_args.kwargs["metadata"]["model_session_id"]
            kwargs = json.loads(upload.call_args.args[1])["client"]["kwargs"]
            self.assertEqual(
                kwargs,
                {
                    "extra_headers": {
                        "X-Client": "client",
                        "X-Trace": "trace",
                        "X-Session-Id": session,
                    }
                },
            )
            sessions.add(session)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(agent.config["client_kwargs"], options)

    def test_generate_only_prepares_attachments_for_missing_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            dataset = Dataset.__new__(Dataset)
            dataset.rows = {
                name: {"prompt": name, "reference_files": [attachment]}
                for name, attachment in (
                    ("first", "old.txt"),
                    ("second", "new.txt"),
                    ("third", "new.txt"),
                )
            }
            for name in ("old.txt", "new.txt"):
                (cache / name).write_text(name)
            bench = GDPval()
            with (
                patch.object(bench, "load_source", return_value=dataset),
                patch.object(
                    dataset, "reference", side_effect=lambda name: cache / name
                ) as resolve,
            ):
                with (
                    patch.object(bench, "catalog", return_value=["first"]),
                    patch.object(bench, "size", 1),
                ):
                    bench.generate(cache, None)
                resolve.assert_called_once_with("old.txt")
                resolve.reset_mock()
                with patch.object(bench, "size", 3):
                    tasks = bench.generate(cache, None)
                    resolve.assert_called_once_with("new.txt")
                    (cache / "old.txt").unlink()
                    (cache / "new.txt").unlink()
                    resolve.reset_mock()
                    self.assertEqual(
                        [t.name for t in bench.generate(cache, None)], [t.name for t in tasks]
                    )
                    resolve.assert_not_called()
                self.assertEqual(Path(tasks[1].context[0]["source"]).read_text(), "new.txt")

    def test_resume_exports_current_sdk_results_with_their_batch_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # An unrelated batch's report must not contribute submissions.
            stale = root / "round-1/eval-999/results.json"
            stale.parent.mkdir(parents=True)
            stale.write_text("not valid JSON")
            tasks = [
                SimpleNamespace(name=name, metadata={"agent_timeout": timeout})
                for name, timeout in (("a", 60), ("b", 90))
            ]
            for task in tasks:
                batch = root / f"round-1/eval-{task.metadata['agent_timeout']}"
                artifact = batch / "000001/agent/files/answer.txt"
                artifact.parent.mkdir(parents=True)
                artifact.write_text(task.name)
                (batch / "manifest.json").touch()

            def resumed(*, tasks, resume, **kwargs):
                self.assertTrue(resume)
                return [
                    {
                        "name": tasks[0].name,
                        "output_dir": "000001",
                        "score_valid": True,
                        "verification_result": {"score": 1},
                        "agent_stop_reason": "completed",
                        "agent_result": {
                            "metadata": {"submitted_files": {"answer.txt": "files/answer.txt"}}
                        },
                    }
                ]

            bench = GDPval()
            bench.output = root
            with patch("eval_anything.runner.ew.eval", side_effect=resumed):
                evaluate(tasks, object(), {}, 1, bench, root, True)
            entries = json.loads((root / "submissions.json").read_text())
            self.assertEqual(set(entries), {"a", "b"})
            for name, entry in entries.items():
                self.assertEqual(entry["status"], "submitted")
                self.assertEqual((root / entry["files"]["answer.txt"]).read_text(), name)

    def test_truncated_zip_excludes_only_the_human_submission(self):
        task = "0e386e32-df20-4d1f-b536-7159bc409ad5"
        rows = {task: {"deliverable_files": ["this.zip"]}, "other": {"deliverable_files": ["ok"]}}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with patch(
                "eval_anything.benchmarks.gdpval.submissions.Dataset",
                return_value=SimpleNamespace(rows=rows, root=cache),
            ):
                human, _ = load_source("human-expert", cache, None)
                model, _ = load_source("gpt-5-low", cache, None)
            self.assertEqual(set(human), {"other"})
            self.assertEqual(set(model), {task, "other"})

    def test_generation_settings(self):
        minimal = {"model": "openai/test", "context_window_tokens": 262144}
        GDPval(brave_api_key="test").runtime(output=None, proxy=None, config={}, agent=minimal)
        self.assertNotIn("max_tokens", minimal)
        agent = {
            "model": "openai/test",
            "context_window_tokens": 1024,
            "max_tokens": 123,
            "reasoning_effort": "high",
        }
        GDPval(brave_api_key="test").runtime(output=None, proxy=None, config={}, agent=agent)
        self.assertEqual(
            (agent["version"], agent["max_tokens"], agent["reasoning_effort"]),
            ("0.2.2", 123, "high"),
        )
        self.assertEqual(agent["env"]["BRAVE_API_KEY"], "test")
        with self.assertRaisesRegex(ValueError, "max_turns"):
            GDPval().runtime(output=None, proxy=None, config={}, agent={"max_turns": 1})

    def test_brave_key_settings(self):
        for invalid in ([], [""], ["first", 123]):
            with self.assertRaisesRegex(ValueError, "brave_api_key"):
                GDPval(brave_api_key=invalid)
        bench = GDPval(brave_api_key=["first", "first"])
        agent = {"model": "openai/test", "context_window_tokens": 262144}
        with bench.runtime(output=None, proxy=None, config={}, agent=agent):
            self.assertEqual(agent["env"], {"BRAVE_API_KEY": "first"})

    def test_brave_gateway_concurrent_search_and_resume(self):
        bench = GDPval(brave_api_key=["first", "second", "third"])
        config = {"task_runtime_config": {"network": "host"}}
        both_in_flight = threading.Barrier(2, timeout=5)
        calls = []

        def upstream(method, url, *, headers, **kwargs):
            key = headers["X-Subscription-Token"]
            query = url.split("?", 1)[1]
            calls.append((key, query))
            if key == "first":
                both_in_flight.wait()
                code = 402
            else:
                code = {"q=exhaust": 402, "q=denied": 403}.get(query, 200)
            return SimpleNamespace(status=code, data=b"result", headers={})

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("eval_anything.benchmarks.gdpval.brave.docker", return_value="1"),
            patch("eval_anything.benchmarks.gdpval.brave.urllib3.PoolManager") as http,
        ):
            out = Path(directory)
            http.return_value.request.side_effect = upstream

            @contextmanager
            def gateway():
                agent = {"model": "openai/test", "context_window_tokens": 262144}
                with bench.runtime(output=out, proxy=None, config=config, agent=agent):
                    env = agent["env"]
                    self.assertTrue(set(bench.brave_keys).isdisjoint(env.values()))

                    def search(query, token=None):
                        req = Request(
                            env["GDPVAL_BRAVE_SEARCH_URL"] + "?q=" + query,
                            headers={"X-Subscription-Token": token or env["BRAVE_API_KEY"]},
                        )
                        with build_opener(ProxyHandler({})).open(req, timeout=8) as r:
                            return r.read()

                    yield search

            with gateway() as search:
                with self.assertRaises(HTTPError) as denied:
                    search("A", "wrong")
                self.assertEqual(denied.exception.code, 403)
                self.assertFalse(calls)
                with ThreadPoolExecutor(2) as pool:
                    self.assertEqual(list(pool.map(search, ["A", "B"])), [b"result"] * 2)
                self.assertCountEqual(
                    calls, [(k, q) for k in ("first", "second") for q in ("q=A", "q=B")]
                )

            with gateway() as search:
                calls.clear()
                self.assertEqual(search("resume"), b"result")
                with self.assertRaises(HTTPError) as denied:
                    search("denied")
                self.assertEqual(denied.exception.code, 403)
                for _ in range(2):
                    with self.assertRaises(HTTPError) as exhausted:
                        search("exhaust")
                    self.assertEqual(exhausted.exception.code, 402)
                self.assertEqual(
                    calls,
                    [
                        ("second", "q=resume"),
                        ("second", "q=denied"),
                        ("second", "q=exhaust"),
                        ("third", "q=exhaust"),
                    ],
                )
            self.assertEqual((out / ".brave-index").read_text(), "3")

    def test_archived_video_sent_to_native_gemini(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "example.zip"
            with ZipFile(archive, "w") as z:
                z.writestr("project/example.mp4", b"example video")
            parts = rendered(archive.name, archive, root, "unused")
            requests = []

            def respond(method, url, **kwargs):
                body = kwargs.get("body")
                if isinstance(body, io.BufferedReader):
                    body = body.read()
                requests.append((method, url, body))
                if "upload/" in url:
                    return SimpleNamespace(
                        status=200, headers={"X-Goog-Upload-URL": "http://localhost/upload-content"}
                    )
                if url.endswith("upload-content"):
                    return SimpleNamespace(
                        status=200,
                        data=json.dumps(
                            {
                                "file": {
                                    "name": "files/xyz",
                                    "uri": "http://localhost/file-data",
                                    "state": "ACTIVE",
                                }
                            }
                        ),
                    )
                if method == "DELETE":
                    return SimpleNamespace(status=204)
                msg = {"winner": "tie", "reason": "test"}
                result = {
                    "candidates": [
                        {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(msg)}]}}
                    ]
                }
                return SimpleNamespace(status=200, data=json.dumps(result))

            with patch("urllib3.request", side_effect=respond):
                result = judge(
                    {"gemini": {"base_url": "http://localhost/v1beta", "api_key": "test"}},
                    "gemini",
                    parts,
                )
            self.assertEqual(result["winner"], "tie")
            self.assertEqual(requests[1][2], b"example video")
            contents = json.loads(requests[2][2])["contents"][0]["parts"]
            self.assertEqual(contents[-1]["fileData"]["mimeType"], "video/mp4")
            self.assertEqual(requests[-1][:2], ("DELETE", "http://localhost/v1beta/files/xyz"))

    def test_office_input_copy_preserves_body_and_repairs_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "brief.docx"
            root_rels = b'<ns0:Relationships xmlns:ns0="http://schemas.openxmlformats.org/package/2006/relationships"><ns0:Relationship Target="docProps/core.xml"/></ns0:Relationships>'
            broken = b'<Relationships><Relationship Target="https://example.invalid" TargetMode="External"<Relationship Target="theme.xml"/></Relationships>'
            with ZipFile(source, "w") as z:
                z.writestr("_rels/.rels", root_rels)
                z.writestr("word/_rels/document.xml.rels", broken)
                z.writestr("word/document.xml", b"original body")
            before = source.read_bytes()
            fixed = office_copy(source, root / "normalized" / source.name)
            self.assertEqual(before, source.read_bytes())
            with ZipFile(fixed) as z:
                self.assertEqual(
                    len(ElementTree.fromstring(z.read("word/_rels/document.xml.rels"))), 2
                )
                self.assertNotIn(b"ns0:", z.read("_rels/.rels"))
                self.assertIn("docProps/core.xml", z.namelist())
                self.assertEqual(z.read("word/document.xml"), b"original body")
            self.assertEqual(office_copy(fixed, root / "unused"), fixed)

    def test_local_manifest_rejects_external_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "submissions.json").write_text(
                json.dumps(
                    {
                        "some-task": {
                            "status": "submitted",
                            "text": "",
                            "files": {"answer.txt": "../answer.txt"},
                        }
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "relative filename"):
                load_source(str(root), root, None)
