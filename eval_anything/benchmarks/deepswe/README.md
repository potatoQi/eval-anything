# DeepSWE v1.1

## 参数怎么填

默认 Harness 是 `MiniSweAgent`。

| 参数 | 填法 |
|---|---|
| `--harness-settings` | 传给 Agent 的 JSON 参数，字段见下表。 |
| `--benchmark-settings` | 必填。`gateway.url` 填宿主机可访问的上游模型 API 地址（通常以 `/v1` 结尾），`gateway.api_key` 填其密钥。两项均为非空字符串。 |
| `--setup-proxy` | 可选。下载原题或构建依赖需要代理时填 HTTP(S) 地址。Docker 拉取基础镜像使用 Docker 自身的代理配置。 |
| `--runtime-proxy` | 必须省略。做题容器使用隔离网络，模型请求走上述网关。 |

| MiniSweAgent 字段 | 填什么 |
|---|---|
| `model` | 模型名，如 `openai/Qwen3.8-27B`。省略时由 Mini 的配置决定。 |
| `version` | mini-swe-agent 的 2.x 版本。省略时使用镜像预装的 `2.4.6`。 |
| `env` | 沙箱环境变量字典。把 `OPENAI_API_BASE` 和 `OPENAI_API_KEY` 分别设为 `${GATEWAY_URL}` 和 `${GATEWAY_TOKEN}`，由评测程序填入，无需 export。 |
| `extra_args` | 原生 Mini CLI 参数列表。用 `-c /opt/deepswe/mini.yaml` 加载随题配置，再用 `-c` 指定网关和 Qwen 采样参数。 |
| `cache` | 是否复用 Agent 安装缓存，默认 `true`。 |
