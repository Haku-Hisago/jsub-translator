# 本地部署 Index-Translate-2B（Windows + WSL2 + vLLM）

本文档说明如何在本机跑起 **Index-Translate-2B**，让 jsub-translator 用它做
日语→中文翻译。这是项目的**默认翻译引擎**。

> 目标硬件：**RTX 5060 Laptop / 16 GB VRAM** / Windows 10 或 11。

---

## ⚠️ 先说清楚：本文档哪些是实测、哪些是照官方文档写的

| 内容 | 状态 |
|---|---|
| 官方仓库地址、模型 ID、启动命令、参数默认值、glossary 格式 | ✅ **照抄官方 README**（链接见下），非猜测 |
| jsub-translator 侧的对接代码 | ✅ 已用**真实 HTTP 服务**端到端测试（见 `_test_index_backend.py`，70 条断言） |
| 下面的 WSL2 / CUDA / vLLM 安装步骤 | ⚠️ **照官方文档整理，未在本机实测**（本机无 NVIDIA GPU） |
| RTX 5060 的实际显存占用、翻译速度 | ❌ **未实测**。必须在你的机器上跑一次才能得到真实数字 |

最后一项不是偷懒 —— 本机没有 NVIDIA 显卡，任何「实测」数字都会是编的。
第一次跑起来后，用本文末尾的[验收清单](#8-验收清单)自己确认一遍。

**官方资料（以此为准，本文可能滞后）：**

- 仓库：https://github.com/bilibili/Index-Translate
- 中文 README：https://github.com/bilibili/Index-Translate/blob/main/README_zh.md
- 文本推理说明：https://github.com/bilibili/Index-Translate/blob/main/inference/llm/README_zh.md
- 模型页：https://huggingface.co/IndexTeam/Index-Translate-2B

---

## 1. 环境

Index-Translate 的文本模型**基于 Qwen3.5**，官方要求：

> 需要 CUDA GPU 和支持 **Qwen3.5** 的 vLLM 版本（官方实测 vLLM **0.29**）。

| 组件 | 说明 |
|---|---|
| Windows | 10 / 11 |
| WSL2 | `wsl --install` 安装 Ubuntu |
| NVIDIA 驱动 | **Windows 侧**安装（WSL 里不需要单独装驱动） |
| CUDA | 由 vLLM 的 PyPI wheel 自带，**不需要**单独装 CUDA Toolkit |
| Python | 3.10 ~ 3.12（vLLM 支持范围，以官方为准） |
| vLLM | 需支持 Qwen3.5；官方实测 0.29 |

### 关于 RTX 5060 Laptop（Blackwell 架构）

RTX 50 系是 **Blackwell（sm_120）**，比老卡更挑版本：

- 必须用**较新的** PyTorch / vLLM —— 老版本 wheel 里没有 sm_120 的 kernel，
  典型症状是启动时报 `no kernel image is available for execution on the device`
  或 `CUDA error: no kernel image is available`。
- 官方文档说明 **FP4**（NVFP4 W4A4）量化是**面向 Blackwell GPU** 的。
  换句话说 50 系是官方明确支持的平台之一。
- **不要**为了「能装上」而把 vLLM/PyTorch 锁到旧版本 —— 那正是跑不起来的原因。
  装最新的即可：`pip install -U vllm`（官方命令就是这么写的）。

### 显存估算

官方给出的 bf16 参考：**2B ≈ 8 GB**（长上下文另需 KV cache）。

RTX 5060 Laptop 有 16 GB，装 2B 的 bf16 是够的，但要给 KV cache 留余量：

- `--max-model-len 32768` 时 KV cache 占用明显高于 4096
- 如果启动时报显存不足（OOM），按顺序试：
  1. 调小 `--max-model-len`（如 16384）
  2. 加 `--gpu-memory-utilization 0.90`（默认 0.90，可试 0.85）
  3. 最后才考虑换量化版（见下）

---

## 2. 安装

在 **WSL2 的 Ubuntu** 里执行：

```bash
# 1) 克隆官方仓库
git clone https://github.com/bilibili/Index-Translate.git
cd Index-Translate

# 2) 装 vLLM（官方命令：-U 拉最新，需要带 Qwen3.5 支持的版本）
pip install -U vllm

# 3) 装推理脚本的依赖
pip install -r inference/llm/requirements.txt
```

> 第 2 步**不要**指定版本号。Qwen3.5 支持是较新才加的，
> 锁旧版本会直接导致模型加载失败。

---

## 3. 模型

```text
IndexTeam/Index-Translate-2B
```

首次 `vllm serve` 会自动从 HuggingFace 下载（约 4~5 GB，bf16）。

如果下载慢或连不上，先设镜像再启动：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

### 量化版（仅在 bf16 跑不动时才用）

官方提供 FP8 版本：`IndexTeam/Index-Translate-2B-FP8`

```bash
vllm serve IndexTeam/Index-Translate-2B-FP8 --host 127.0.0.1 --port 8000
```

> 任务要求里写的是「不要默认量化，除非 BF16 实际无法稳定运行」——
> 上面这个顺序就是这个意思：**先用 bf16，不行再换 FP8**。

---

## 4. 启动

```bash
vllm serve IndexTeam/Index-Translate-2B \
    --host 127.0.0.1 --port 8000 \
    --max-model-len 32768
```

**为什么是 32768 而不是官方示例里的 4096：**
官方那句 4096 是给「一句话翻译」的短文本示例用的，README 自己也说明了
「4,096 token 配置用于短文本示例」。本项目是**按批翻译**，一批 20 条字幕
加上前后文，prompt 会明显更长；官方部署预设里 2B/9B 给的就是 32768。

> `--max-model-len` 是**输入 + 输出**的总窗口。设太小会在长批时报
> `This model's maximum context length is ...`。
> 若显存吃紧，往下调到 16384；或把 `.env` 里的 `INDEX_BATCH_SIZE` 调小。

### 确认服务起来了

```bash
curl http://127.0.0.1:8000/v1/models
```

返回里的 `data[].id` 就是**真实模型 ID**。它必须和 `.env` 里的
`INDEX_MODEL` 完全一致 —— 不一致时请求会回 404 `model not found`。

> jsub-translator 会**自动帮你核对**这一点：启动任务时若发现模型名对不上，
> 会直接告诉你服务实际提供了哪些模型名，不用自己去猜。

---

## 5. 测试

官方推荐先单独验一次模型能正常翻译：

```python
from openai import OpenAI

client = OpenAI(
    api_key="EMPTY",                        # 本地服务不校验，官方约定
    base_url="http://127.0.0.1:8000/v1",
)

response = client.chat.completions.create(
    model="IndexTeam/Index-Translate-2B",   # ← 用 /v1/models 确认过的真实 ID
    messages=[
        {"role": "user",
         "content": "将这句话翻译成中文：今日は本当に楽しかった。"}
    ],
    temperature=0,
)

print(response.choices[0].message.content)
```

能打印出中文译文，说明服务可用。

### 再验一次 jsub-translator 的接入

```bash
cd jsub-translator
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'.')
from src.config import probe_index_service, INDEX_MODEL
ok, detail, ids = probe_index_service()
print('服务状态:', ok, detail)
print('服务提供:', ids)
print('本项目配置:', INDEX_MODEL)
print('模型名匹配:', INDEX_MODEL in ids)
"
```

---

## 6. 与 jsub-translator 对接

`.env`（项目根或 EXE 同目录）：

```ini
TRANSLATION_PROVIDER=index
INDEX_BASE_URL=http://127.0.0.1:8000/v1
INDEX_API_KEY=EMPTY
INDEX_MODEL=IndexTeam/Index-Translate-2B
```

**关于 WSL2 的地址：** jsub-translator 跑在 **Windows 侧**，vLLM 跑在 **WSL2 里**。
WSL2 默认会把 `127.0.0.1` 上的监听端口转发到 Windows 的 `localhost`，
所以上面这个 `127.0.0.1:8000` 直接可用，**不需要**改绑 `0.0.0.0`。

若连不上（少见，取决于 WSL 版本/镜像网络模式）：

```bash
# 在 WSL 里看实际 IP
hostname -I
```

然后把 `.env` 的 `INDEX_BASE_URL` 改成 `http://<那个IP>:8000/v1`，
并且启动 vLLM 时用 `--host 0.0.0.0`。

### 切换引擎

| 想用 | 改什么 |
|---|---|
| 本地 Index（默认） | `TRANSLATION_PROVIDER=index` |
| DeepSeek | `TRANSLATION_PROVIDER=deepseek` + 填 `DEEPSEEK_API_KEY` |
| Anthropic | `TRANSLATION_PROVIDER=anthropic` + 填 `ANTHROPIC_API_KEY` |
| Index 失败时兜底到 DeepSeek | `FALLBACK_ENABLED=true`（**会产生云端费用**，默认关） |

也可以在**网页界面**的「翻译引擎」下拉里直接选，不用改文件。

---

## 7. 术语表（Glossary）

词典放在项目的 `dictionary/` 目录，所有 `*.yaml` 都会被加载：

```yaml
terms:            # 一般术语 / 网络词
  推し:
    zh: 推
  レス:
    zh: 饭撒回应

people:           # 人名 —— 固定译名
  高橋みなみ:
    zh: 高桥南

groups:           # 团体名 —— 不擅自汉化
  iLiFE!:
    zh: iLiFE!
```

也接受简写 `推し: 推`。

**只注入本批真正命中的词条** —— 228 条词典里一句字幕通常只命中 3~5 条，
不会把整本词典塞进 prompt 浪费 token。

命中后按官方 instTrans 格式组装成**硬约束**：

```text
1. 【硬性要求】专名/术语对照: 推し→推、レス→饭撒回应
```

硬约束是**一票否决**：译文没落实术语时，jsub-translator 的校验器会判定
该批不通过并重试 —— 不是「建议」，是强制。

> ⚠️ **已知局限**：术语落实的校验用子串匹配，所以**单字目标词**不可靠
> （目标词是「推」时，「主推」也算命中）。术语长度 ≥2 时判断是可靠的。
> 详见 `src/validator.py` 里的说明。

---

## 8. 验收清单

第一次跑通后，逐项确认：

- [ ] `curl http://127.0.0.1:8000/v1/models` 返回了模型 ID
- [ ] `.env` 的 `INDEX_MODEL` 与上面返回的 ID **完全一致**
- [ ] `probe_index_service()` 打印 `服务状态: True`
- [ ] 网页「翻译引擎」下拉能选到 Index-Translate-2B，且 API Key 框变灰
- [ ] `/api/health` 里 `index_service.running` 为 `true`
- [ ] 跑一个短视频，字幕能正常出中文字幕
- [ ] 打开 `dictionary/idol.yaml` 加一个词，重跑，确认译文里出现了指定译法

**记下你的实测数字**（本机没法替你测）：

| 项目 | 你的实测值 |
|---|---|
| 显存占用（`nvidia-smi`） | |
| 单批 20 条字幕平均耗时 | |
| 是否用了量化版 | |

---

## 9. 常见问题

### `Index-Translate service is not running`

jsub-translator 在启动任务前会探测 `/v1/models`，探不到就报这句（而不是抛
`Connection refused`）。含义就是：**WSL 里的 vLLM 没起或还没加载完**。

检查顺序：
1. WSL 里 `vllm serve` 的终端还在吗？有没有报错退出？
2. 模型加载完成了吗？2B 首次加载通常 10~60 秒。
3. Windows 浏览器打开 `http://127.0.0.1:8000/v1/models` 能看到 JSON 吗？

### `没有名为 '...' 的模型`

服务起着，但 `.env` 的 `INDEX_MODEL` 写错了。报错信息里会列出服务实际提供的
模型名，照着改即可。

### `no kernel image is available for execution on the device`

PyTorch/vLLM 版本太旧，不含 Blackwell（sm_120）的 kernel。升级：

```bash
pip install -U vllm
```

### 显存不足 / OOM

按顺序试（**先别急着量化**）：
1. 调小 `--max-model-len`
2. 调小 `.env` 里的 `INDEX_BATCH_SIZE`（20 → 10）
3. 调小 `.env` 里的 `INDEX_CONTEXT_BEFORE` / `INDEX_CONTEXT_AFTER`（3 → 0）
4. 仍不行再换 `Index-Translate-2B-FP8`

### 翻译很慢

本地 2B 模型在笔记本 GPU 上不会很快，这是正常的。优先级按任务要求是
**准确 > 术语稳定 > 上下文一致 > 稳定 > 速度**。想提速：

- 调大 `INDEX_BATCH_SIZE`（批越大越省固定开销，但太大会掉质量）
- 关掉 `ENABLE_REVIEW`（省掉一遍完整推理）
- 确认没有在 CPU 上跑（`nvidia-smi` 看 GPU 利用率）

### 想关掉校验换速度

`.env` 里 `INDEX_VALIDATION_ENABLED=false`。

**但不建议**：校验是拦住「模型少给一条 → 整批字幕错位」这类无声故障的唯一防线。
关掉之后错误会静默流进 ASS 文件。
