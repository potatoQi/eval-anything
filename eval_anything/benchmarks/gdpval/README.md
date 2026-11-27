# GDPval-AA v2.1

## 生成交付文件

在仓库根目录构建镜像，设置 `BRAVE_API_KEY` 和做题模型的 `MODEL_API_KEY`。安装的 EnvWeaver 需要提供 `StirrupAgent`。使用本地 SDK 时，在 `uv run --extra gdpval` 后加 `--with-editable /path/to/EnvWeaver`。

```bash
docker build -t eval-anything/gdpval:2.1 -t eval-anything/gdpval:2.1-brave eval_anything/benchmarks/gdpval/assets

uv run --extra gdpval eval-anything run gdpval-aa-v2.1 \
  --benchmark-settings '{"brave_api_key":"${BRAVE_API_KEY}"}' \
  --harness-settings '{
    "model":"openai/Qwen3.8-27B",
    "client_kwargs":{"api_base":"http://your-model-gateway:8000/v1"},
    "context_window_tokens":262144,
    "env":{"OPENAI_API_KEY":"${MODEL_API_KEY}"}
  }' \
  --output outputs/qwen
```

用 `context_window_tokens` 指定服务实际启用的上下文窗口。单次输出上限 `max_tokens` 可选，省略时沿用 harness 默认值；若该值超过上下文窗口，请显式调低。模型地址须在容器内可访问。默认使用 Stirrup 0.2.2，最多 250 步，单条命令限时 600 秒，达到上下文窗口的 70% 后压缩。单题默认时限 10800 秒，可用 `--eval-settings` 覆盖。使用已有镜像时，给 `--benchmark-settings` 增加 `image`。

每道题的同一次尝试共用一个 `X-Session-Id`，不同题和重新做题时使用不同 ID。支持该请求头的模型网关可将连续请求路由到同一后端。ID 可在 Agent 结果的 `metadata.model_session_id` 中查看。

单把 Brave Key 由 Stirrup 直接使用。多把 Key 按顺序填写，例如 `--benchmark-settings '{"brave_api_key":["${BRAVE_KEY_1}","${BRAVE_KEY_2}"]}'`，默认使用 `eval-anything/gdpval:2.1-brave` 镜像。多 Key 时，宿主机的 GDPval 搜索网关保管密钥；收到 HTTP 402 就换下一把重发同一查询，所有题共用当前位置，`--resume` 后继续。全部耗尽时搜索返回 402。配置 `--runtime-proxy` 时，代理地址也需能从宿主机访问。

`outputs/qwen/submissions.json` 按题列出交付原件的相对路径。移动时保留整个输出目录。原命令加 `--resume` 可续跑。

## 比较两份提交

| GDPval 评分参数 | 用途与默认值 | 命令片段示例 |
|---|---|---|
| `--left`、`--right` | 两侧提交来源，均必填。可选来源见下表。 | `--left outputs/qwen --right gpt-5-low` |
| `--judges` | 必填，三位裁判的配置，接受 JSON 对象或 `.json` 文件路径。 | `--judges judges.json` |
| `--renderer-image` | 渲染交付文件与附件的 Docker 镜像，默认 `eval-anything/gdpval:2.1`。自定义镜像需包含 LibreOffice、Poppler、Pillow 和 openpyxl。 | `--renderer-image my-renderer:1` |
| `--seed` | 随机分配裁判和 A/B 顺序的种子，默认 0。 | `--seed 42` |

只比较两侧都有记录的题目。[通用评分参数](../../../README.md#score-参数)

| 来源 | 填写值 | 覆盖 |
|---|---|---|
| 自己的模型 | 运行目录或其中的 `submissions.json` | 该次运行 |
| 公开 GPT-5 low 交付文件 | `gpt-5-low` | 220 题 |
| OpenAI 公开的人类专家交付文件 | `human-expert` | 184 题 |

OpenAI 的 185 份人类提交中，任务 `0e386e32-df20-4d1f-b536-7159bc409ad5` 的 `PrivateCrypMixV2.zip` 已截断。任一侧选择 `human-expert` 时自动跳过该题；生成和模型对模型比较仍覆盖全部 220 道题（双方均有记录时）。

在 `judges.json` 中填写裁判服务地址和凭据环境变量。GPT 和 Claude 使用兼容 Chat Completions 的服务，Gemini 使用原生 generateContent 服务；评音视频还须支持 Gemini Files 上传、处理状态查询和删除。

```json
{
  "gpt": {"base_url":"${GPT_JUDGE_BASE_URL}","api_key":"${GPT_JUDGE_API_KEY}"},
  "claude": {"base_url":"${CLAUDE_JUDGE_BASE_URL}","api_key":"${CLAUDE_JUDGE_API_KEY}"},
  "gemini": {"base_url":"${GEMINI_JUDGE_BASE_URL}","api_key":"${GEMINI_JUDGE_API_KEY}"}
}
```

```bash
uv run --extra gdpval eval-anything score gdpval-aa-v2.1 \
  --left outputs/qwen --right gpt-5-low \
  --judges judges.json --output outputs/qwen-vs-gpt5
```

两个 Chat Completions 地址通常以 `/v1` 结尾，Gemini 地址通常以 `/v1beta` 结尾。使用 Shadow 凭据时填写本地 Shadow 服务地址。

`summary.json` 给出输、赢、平、失败数、数据覆盖和左方偏好率（平局按半胜）。`votes/` 保存各题判决。原命令加 `--resume` 复用已完成的判决；更换对手或评分设置时使用新目录。

## 对齐范围

生成使用公开题面、附件、任务提示及软件包清单。缓存保留下载原件；做题和评分读取同一份输入附件，受影响的 DOCX/PPTX 仅在副本中修正 Office 包的关系或属性。做题使用本机 Docker，从 2026-05-29 的 Debian 快照安装官方列出的 762 个系统包，构建时逐项校验版本。评分按题匿名随机交换 A/B，普通题在三位裁判间分配，有音视频时交给 Gemini。裁判提示、文件渲染和空提交处理在本地实现。两组提交的偏好率只描述这两组之间的比较；官方 Elo 还依赖多模型对局、主动抽样和标定。

[AA 评测方法](https://artificialanalysis.ai/methodology/intelligence-benchmarking) · [OpenAI 原题与人类原件](https://huggingface.co/datasets/openai/gdpval/tree/11e7900cdcac61bc4daf59e65feb238acda98fbf) · [公开 GPT-5 low 原件](https://huggingface.co/datasets/jeqcho/2025-10-16T21-42-02plus00-00_gdpval/tree/87f1744868c7e6d5110f1b6899a9a15343a46e03)
