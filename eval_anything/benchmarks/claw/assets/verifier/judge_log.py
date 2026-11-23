"""Record judge HTTP responses before Claw-Eval parses them."""

import base64
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

RESPONSE_LOG = Path("/tmp/claw-judge-responses.jsonl")


@contextmanager
def judge_requests(judge, api_key):
    from claw_eval.graders import llm_judge

    def record(endpoint, response):
        body = response.read().replace(api_key.encode(), b"[REDACTED]")
        row = {
            "time": datetime.now(UTC).isoformat(),
            "endpoint": endpoint,
            "status": response.status_code,
        }
        try:
            row["body"] = body.decode("utf-8")
        except UnicodeDecodeError:
            row["body_base64"] = base64.b64encode(body).decode("ascii")
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.flush()

    # The native grader uses module-level httpx.post; the email graders use the OpenAI client.
    post = llm_judge.httpx.post

    def native_post(*args, **kwargs):
        # OpenLux's Gemini REST endpoint reads the camelCase system instruction.
        body = kwargs["json"]
        body["systemInstruction"] = body.pop("system_instruction")
        body["generationConfig"].update(
            responseMimeType="application/json",
            responseSchema={
                "type": "OBJECT",
                "properties": {"score": {"type": "NUMBER"}, "reasoning": {"type": "STRING"}},
                "required": ["score", "reasoning"],
            },
        )
        response = post(*args, **kwargs)
        record("generateContent", response)
        return response

    def log_chat(response):
        record("chat/completions", response)

    hooks = judge.client._client.event_hooks["response"]
    hooks.append(log_chat)
    try:
        with (
            RESPONSE_LOG.open("a", encoding="utf-8") as stream,
            patch.object(llm_judge.httpx, "post", native_post),
        ):
            yield
    finally:
        hooks.remove(log_chat)
