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

`--harness` 填 EnvWeaver 支持的 Agent 类名，区分大小写。省略时使用该 benchmark 的默认 Agent。类名和构造参数见 [EnvWeaver Agent 文档](https://potatoqi.github.io/EnvWeaver/latest/agent/)。

Claw-Eval General 仅接受 `ClawEvalAgent`，该类也仅用于这个 benchmark。
