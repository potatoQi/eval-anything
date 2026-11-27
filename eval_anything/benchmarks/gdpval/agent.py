from envweaver import StirrupAgent as NativeStirrupAgent

from ..session import ModelSession


class StirrupAgent(ModelSession, NativeStirrupAgent):
    sdk_agent = NativeStirrupAgent

    def _command(self, prompt, images):
        # Eval creates a separate agent for each attempt. Stirrup reuses these
        # client settings for every model call in that attempt.
        options = self._config["client_kwargs"] or {}
        headers = (options.pop("headers", None) or {}) | (options.get("extra_headers") or {})
        options["extra_headers"] = {k: v for k, v in headers.items() if k.lower() != "x-session-id"}
        options["extra_headers"]["X-Session-Id"] = self.start_model_session(self._output)
        self._config["client_kwargs"] = options
        return super()._command(prompt, images)
