# Benchmark 与 Harness 可选值

## Benchmark

运行 `eval-anything run <benchmark>` 时，将 `<benchmark>` 替换为表中的参数值。

| Benchmark 使用说明 | `<benchmark>` 参数值 |
|---|---|
| [Terminal-Bench 2.1](../eval_anything/benchmarks/terminal/README.md) | `terminal-bench-2.1` |
| [DeepSWE v1.1](../eval_anything/benchmarks/deepswe/README.md) | `deepswe-v1.1` |
| [Claw-Eval General](../eval_anything/benchmarks/claw/README.md) | `claw-eval-general` |
| [Agents’ Last Exam v1.1 Docker](../eval_anything/benchmarks/last_exam/README.md) | `agents-last-exam-docker` |

## Harness

`--harness` 填当前安装的 EnvWeaver 提供的 Agent 类名，区分大小写。

| EnvWeaver 版本 | 可填写的 Agent 类名 |
|---|---|
| PyPI 0.0.2 | `Terminus2Agent`、`MiniSweAgent`、`CodexAgent`、`PiAgent` |
| 当前开发版 | 除上述四个外，还支持 `ClaudeCodeAgent`、`OpenClawAgent`、`ClawEvalAgent`、`AleClawAgent` |

Claw-Eval General 仅接受 `ClawEvalAgent`，该类也仅用于这个 benchmark。
