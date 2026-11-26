"""Per-attempt model routing for the ALE Docker benchmark."""

from envweaver import AleClawAgent as NativeAleClawAgent

from ..session import ModelSession


class AleClawAgent(ModelSession, NativeAleClawAgent):
    sdk_agent = NativeAleClawAgent

    def run(self, *, goal, runtime, instance, output, cwd):
        # The SDK keeps worker options separate from the saved constructor config.
        generation = self._options.setdefault("extra_generation_kwargs", {})
        for key in ("headers", "extra_headers"):
            if key in generation:
                generation[key] = {
                    k: v for k, v in (generation[key] or {}).items() if k.lower() != "x-session-id"
                }
        headers = generation.setdefault("extra_headers", {})
        headers["X-Session-Id"] = self.start_model_session(output)
        return super().run(goal=goal, runtime=runtime, instance=instance, output=output, cwd=cwd)
