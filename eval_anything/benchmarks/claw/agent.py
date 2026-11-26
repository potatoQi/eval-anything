"""Set the OpenAI session header in the solver process for one Claw attempt."""

from envweaver import ClawEvalAgent as NativeClawEvalAgent

from ..session import ModelSession

# Merge inside the sandbox so existing custom headers stay in the solver environment.
_LAUNCH = r"""
import os, sys
session, *command = sys.argv[1:]
headers = [
    line for line in os.environ.get("OPENAI_CUSTOM_HEADERS", "").splitlines()
    if line.partition(":")[0].strip().lower() != "x-session-id"
]
headers.append("X-Session-Id: " + session)
os.environ["OPENAI_CUSTOM_HEADERS"] = "\n".join(headers)
os.execvp(command[0], command)
"""


class ClawEvalAgent(ModelSession, NativeClawEvalAgent):
    sdk_agent = NativeClawEvalAgent

    def _command(self, prompt, images):
        command = super()._command(prompt, images)
        session = self.start_model_session(self._output)
        return [self._python, "-c", _LAUNCH, session, *command]
