# CLI 参数补充说明

## Benchmark

运行 `eval-anything run <benchmark>` 时，将 `<benchmark>` 替换为表中的参数值。

| Benchmark 使用说明 | `<benchmark>` 参数值 |
|---|---|
| [Terminal-Bench 2.1](../eval_anything/benchmarks/terminal/README.md) | `terminal-bench-2.1` |
| [DeepSWE v1.1](../eval_anything/benchmarks/deepswe/README.md) | `deepswe-v1.1` |
| [Claw-Eval General](../eval_anything/benchmarks/claw/README.md) | `claw-eval-general` |
| [Agents’ Last Exam v1.1 Docker](../eval_anything/benchmarks/last_exam/README.md) | `agents-last-exam-docker` |
| [GDPval-AA v2.1](../eval_anything/benchmarks/gdpval/README.md) | `gdpval-aa-v2.1` |

## 支持独立评分的 benchmark

| `<benchmark>` 参数值 | 如何评分 |
|---|---|
| `gdpval-aa-v2.1` | 对每道题，裁判匿名查看双方交付的文件，判断哪一方更符合题目要求，也可以判平。[评分命令](../eval_anything/benchmarks/gdpval/README.md#比较两份提交) |

## Harness

`--harness` 填 EnvWeaver 支持的 Agent 类名，区分大小写。省略时使用该 benchmark 的默认 Agent。类名和构造参数见 [EnvWeaver Agent 文档](https://potatoqi.github.io/EnvWeaver/latest/agent/)。
