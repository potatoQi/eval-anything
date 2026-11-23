# Terminal-Bench 2.1

## 参数怎么填

默认 Harness 是 `Terminus2Agent`。

| 参数 | 填法 |
|---|---|
| `--harness-settings` | 传给 Agent 的 JSON 参数，字段见下表。 |
| `--benchmark-settings` | 可选。配置 APT 镜像、Python 包索引、直连主机和下载重试，见下文。 |
| `--setup-proxy` | 可选。下载原题或构建镜像时需要代理才填 HTTP(S) 地址。Docker 拉取基础镜像使用 Docker 自身的代理配置。 |
| `--runtime-proxy` | 可选。做题容器需要代理访问外网时填写容器可达的 HTTP(S) 地址。 |

| Terminus2Agent 字段 | 填什么 |
|---|---|
| `model` | 必填。带提供方前缀的模型名，如 `openai/Qwen3.8-27B`。 |
| `options` | 可选。Terminus 2 原生参数。自建服务在 `api_base` 填沙箱可达的模型 API 地址；需要覆盖采样温度时填 `temperature`。 |
| `env` | 可选。传给沙箱内 Agent 的环境变量字典。使用需要鉴权的 OpenAI 兼容服务时填 `OPENAI_API_KEY`。 |
| `cache` | 是否复用 Agent 安装缓存，默认 `true`。 |

## 在国内服务器运行

在原运行命令上加以下参数，也可将 JSON 存为文件后传入路径。

```bash
--benchmark-settings '{
  "apt_mirror": "https://mirrors.ustc.edu.cn",
  "pypi_index": "https://pypi.org/simple",
  "direct_hosts": ["mirrors.ustc.edu.cn"],
  "retries": 3,
  "timeout": 60
}'
```

`apt_mirror` 填同时提供 Debian、Ubuntu 仓库的镜像根地址，默认沿用题目自带源。仅改写发行版官方仓库，保留发行版、组件及签名校验。缺少 CA 证书的镜像使用临时 HTTP 源安装证书，再使用指定的 HTTPS 源。
`pypi_index` 填不带凭据的 Simple API 地址，默认沿用原索引。中科大 PyPI 地址为 `https://mirrors.ustc.edu.cn/pypi/simple`。
`direct_hosts` 默认为空列表，应填实际需要绕过代理的主机名。
`retries` 是 APT、pip 和 uv 遇到下载故障时的额外重试次数，默认 3。单次网络超时 `timeout` 默认 60 秒。

首次运行会先从官方任务镜像准备两道 QEMU 题的 Python，以及三道 PyTorch 题的官方评分依赖。PyTorch 依赖可能有数 GB，构建时会断网检查评分命令能否启动。准备成功后才进入 Eval，同配置重跑复用本地镜像。其他题仍可按需联网安装依赖。
