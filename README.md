# eval-anything

用 EnvWeaver 运行完整的 Agent benchmark。

```bash
uv run eval-anything run terminal-bench-2.1 \
  --harness-settings '{
    "model": "openai/Qwen3.8-27B",
    "options": {
      "api_base": "http://your-gateway:8000/v1"
    },
    "env": {
      "OPENAI_API_KEY": "your-model-key"
    }
  }' \
  --output outputs/tb
```

默认从 PyPI 安装 EnvWeaver。使用本地源码时，把示例中的 `uv run` 换成 `uv run --with-editable /path/to/EnvWeaver`。

## 命令

| 命令 | 用途 |
|---|---|
| `uv run eval-anything run <benchmark> [选项]` | 运行评测，保存结果。 |
| `uv run eval-anything score <benchmark> [选项]` | 对已有提交独立评分。[支持独立评分的 benchmark](docs/supplement.md#支持独立评分的-benchmark) |

## `run` 参数

| 参数 | 用途与默认值 | 命令片段示例 |
|---|---|---|
| `<benchmark>` | 必填，放在 `run` 后。可选值见[Benchmark 名称](docs/supplement.md#benchmark)。 | `run terminal-bench-2.1` |
| `--harness` | 填 EnvWeaver 的 Agent 类名，省略时使用该 benchmark 的默认 Agent。见 [Harness 说明](docs/supplement.md#harness)。 | `--harness MiniSweAgent` |
| `--harness-settings` | 传给所选 Agent 的构造参数，例如模型、服务地址和密钥。 | `--harness-settings '{"model":"openai/Qwen3.8-27B"}'` |
| `--eval-settings` | 传给 EnvWeaver `EvalConfig` 的参数。 | `--eval-settings '{"max_retries":2}'` |
| `--benchmark-settings` | Benchmark 专属参数。直接透传给各 Benchmark 适配器 | `--benchmark-settings '{"gateway":{"url":"http://your-gateway:8000/v1","api_key":"your-model-key"}}'` |
| `--concurrency` | 并发上限，默认 1。可填正整数，或分别设置准备、做题和评分三个阶段的上限。 | `--concurrency 16` 或 `--concurrency '{"setup":4,"agent":16,"verify":4}'` |
| `--cache` | 原始数据和任务池的缓存目录，中断后复用同一目录可续传题包，默认 `.cache/eval-anything`。 | `--cache /data/eval-cache` |
| `--output` | 本次运行的输出目录，默认 `outputs/run`。 | `--output outputs/tb` |
| `--setup-proxy` | 下载原题、附件和构建依赖时使用的 HTTP(S) 代理，可省略。Docker 拉取镜像使用 Docker 自身的代理配置。 | `--setup-proxy http://proxy:8080` |
| `--runtime-proxy` | 评测期间的 HTTP(S) 代理，可省略。有需要用到的 Benchmark 会使用该字段。 | `--runtime-proxy http://proxy:8080` |
| `--resume` | 续跑已有结果。在原命令末尾加此开关，沿用相同的 benchmark、设置和输出目录。 | `--output outputs/tb --resume` |
| `-h`、`--help` | 显示命令帮助并退出。 | `uv run eval-anything run --help` |

## `score` 参数

| 参数 | 用途与默认值 | 命令片段示例 |
|---|---|---|
| `<benchmark>` | 必填，选择支持独立评分的 benchmark。 | `score gdpval-aa-v2.1` |
| `--output` | 必填，评分结果的输出目录。 | `--output outputs/comparison` |
| `--task` | 只评指定的任务 ID，可重复传入。省略时评所有可评分的任务。 | `--task <id1> --task <id2>` |
| `--concurrency` | 同时评分的任务数，填正整数，默认 1。 | `--concurrency 4` |
| `--cache` | 评分资源的缓存目录，默认 `.cache/eval-anything`。 | `--cache /data/eval-cache` |
| `--setup-proxy` | 下载评分资源时使用的 HTTP(S) 代理，可省略。 | `--setup-proxy http://proxy:8080` |
| `--resume` | 从同一输出目录续跑，复用已完成的评分。 | `--output outputs/comparison --resume` |
| `-h`、`--help` | 显示评分命令帮助。 | `uv run eval-anything score --help` |
