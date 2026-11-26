# Claw-Eval General

## 参数怎么填

仅支持 `ClawEvalAgent`。每次做题尝试自动使用独立的 `X-Session-Id`，同题续答复用该 ID，整题重试或下一轮评测会换新 ID。可用 `agent/result.json` 的 `metadata.model_session_id` 核对做题网关日志。

| 参数 | 填法 |
|---|---|
| `--harness-settings` | 填做题模型的 JSON 配置，字段见下表。 |
| `--benchmark-settings` | 填裁判服务和真实搜索服务的配置，字段见下表。 |
| `--setup-proxy` | 可选。下载原题或构建依赖需要代理时填 HTTP(S) 地址。Docker 拉取基础镜像使用 Docker 自身的代理配置。 |
| `--runtime-proxy` | 可选。真实搜索需要代理时填写搜索服务进程可达的 HTTP(S) 地址，只用于运行期间的搜索。 |

| ClawEvalAgent 字段 | 填什么 |
|---|---|
| `model` | 必填。Claw-Eval 的原生做题模型配置对象。 |
| `model.model`、`model.base_url`、`model.api_key` | 分别填部署的模型名、沙箱可达的 OpenAI 兼容 API 地址、该服务的密钥。需要鉴权时填密钥。 |
| `model.context_window`、`model.temperature` | 上下文窗口大小（token），以及采样温度。 |
| `model.extra_body` | 加入做题模型请求的额外字段。可填写 `top_p`、`top_k`、`min_p`、惩罚系数和 `chat_template_kwargs.enable_thinking`。 |
| `model.reasoning_effort`、`model.reasoning_field` | 推理强度，以及多轮对话携带推理内容的字段名。后者省略时由 Claw 按服务地址选择。 |
| `model.input_modalities`、`model.system_prompt_prefix` | 模型支持的输入类型列表，以及加在系统提示词开头的文本。 |
| `cache` | 是否复用 Agent 安装缓存，默认 `true`。 |

| Benchmark 字段 | 填什么 |
|---|---|
| `judge.base_url` | 必填。评分容器可达的裁判 API 地址。服务须提供 `gemini-3-flash-preview`，支持 Gemini `generateContent` 和部分题使用的 `chat/completions`。 |
| `judge.api_key` | 必填。上述裁判服务的密钥。 |
| `search.api_key` | 必填。真实搜索题使用的 Novada 密钥。 |
| `search.api_url` | 可选。真实搜索 API 地址，默认 `https://scraperapi.novada.com/search`。 |
