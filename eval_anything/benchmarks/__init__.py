from .claw import ClawGeneral
from .deepswe import DeepSWE
from .last_exam import LastExamDocker
from .terminal import TerminalBench

BENCHMARKS = {cls.name: cls for cls in (TerminalBench, DeepSWE, ClawGeneral, LastExamDocker)}
