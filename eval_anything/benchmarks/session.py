from uuid import uuid4


class ModelSession:
    """Per-attempt routing metadata for a benchmark's EnvWeaver Agent."""

    sdk_agent: type

    @staticmethod
    def start_model_session(output):
        session = uuid4().hex
        output.update(metadata={"model_session_id": session})
        return session

    def _identity(self):
        # Keep the SDK harness identity in Eval manifests across routing changes.
        identity = super()._identity()
        identity["class"] = f"{self.sdk_agent.__module__}.{self.sdk_agent.__qualname__}"
        return identity
