from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from envweaver import Verifier

WEB_CACHE = "/tmp/web_real_cache"
RESPONSE_LOG = "/tmp/claw-judge-responses.jsonl"


class GeneralVerifier(Verifier):
    def __init__(self, verifier, response_dir, cache_output):
        super().__init__(
            entry=verifier.entry,
            runtime=verifier.runtime,
            timeout=verifier.timeout,
            required_env=verifier.required_env,
            requires_task_instance=verifier.requires_task_instance,
        )
        self.response_dir = response_dir
        self.cache_output = cache_output

    def run(self, verifier_instance, *, task_instance=None, files=None, env=None):
        if self.cache_output is not None:
            self.runtime.download(
                instance=task_instance, source=WEB_CACHE, target=self.cache_output
            )
        self.response_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with TemporaryDirectory(dir=self.response_dir) as temp:
            local = Path(temp) / "responses.jsonl"
            local.touch(mode=0o600)
            self.runtime.upload(instance=verifier_instance, source=local, target=RESPONSE_LOG)
            try:
                return super().run(
                    verifier_instance, task_instance=task_instance, files=files, env=env
                )
            finally:
                self.runtime.download(instance=verifier_instance, source=RESPONSE_LOG, target=local)
                local.chmod(0o600)
                local.replace(self.response_dir / f"{verifier_instance.id}.jsonl")


def for_round(task, output, number, search_settings):
    directory = output / f"round-{number}"
    search = task.metadata["web_search"]
    context = list(task.context)
    verifier = GeneralVerifier(
        task.verifier,
        directory / "judge-responses" / task.name,
        directory / "web-cache" / task.name if search else None,
    )
    setup = task.setup
    if search:
        previous = output / f"round-{number - 1}" / "web-cache" / task.name
        if previous.is_dir():
            context.append({"source": str(previous), "target": WEB_CACHE})
        env = [f"SERP_DEV_KEY={search_settings['api_key']}"]
        if "api_url" in search_settings:
            env.append(f"SERP_API_URL={search_settings['api_url']}")
        setup = {**setup, "command": ["env", *env, *setup["command"]]}
    return replace(task, context=context, verifier=verifier, setup=setup)
