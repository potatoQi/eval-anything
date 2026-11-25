# Agents’ Last Exam v1.1 · Docker

运行官方 `selected_tasks/docker_support.txt` 的 78 道 Linux 桌面题，每题运行清单指定的第 0 个变体一次。默认使用 `AleClawAgent`，CUA 端口为 5000。新评测通过 Git 查询 [standalone-ale-claw 分支](https://github.com/potatoQi/agents-last-exam/tree/standalone-ale-claw) 的最新提交，把完整 SHA 写入输出目录的 `run.json`。任务、评分器和默认 AleClaw 共用该提交。`--resume` 沿用记录的 SHA，即使首次准备数据时中断也一样。其他可在 Linux 做题的 EnvWeaver Agent 可通过 `--harness` 选择。

需要本机 Docker、Hugging Face CLI（`hf`）和已获批的 [v1.1 数据包访问权限](https://huggingface.co/datasets/agents-last-exam/agents-last-exam-data-archive)。首次运行自动校验并解包约 39.2 GB 的压缩包、拉取官方桌面镜像并构建评分环境。已用官方脚本解包时，可传 `--benchmark-settings '{"data_dir":"/absolute/path/task-data-v1.1"}'` 复用数据。请使用含明文参考文件的完整私有包。

从 eval-anything 项目目录运行。下面的命令使用本地提供 `AleClawAgent` 的 EnvWeaver 源码。做题模型用 `--harness-settings` 配置，需要模型裁判的题从 `verifier_env` 读取裁判服务配置。

```bash
uv run --with-editable /path/to/EnvWeaver eval-anything run agents-last-exam-docker \
  --harness-settings '{"model":"openai/YOUR_MODEL","options":{"base_url":"${MODEL_BASE_URL}","api_key":"${MODEL_API_KEY}"}}' \
  --eval-settings '{"verifier_env":{"JUDGE_BASE_URL":"${JUDGE_BASE_URL}","JUDGE_API_KEY":"${JUDGE_API_KEY}"}}' \
  --output outputs/ale-docker
```

`--concurrency` 默认 1，按题卡的 CPU、内存需求调整。用原命令加 `--resume` 续跑。题面、初始化和评分使用固定版本的上游代码，参考文件在做题结束后传入。支持部分得分，`summary.json` 的 `mean_score` 是全部 78 题的平均分，有缺失评分时为 `null`；`valid`、`invalid` 和 `mean_valid_score` 可用于查看评分覆盖情况。
