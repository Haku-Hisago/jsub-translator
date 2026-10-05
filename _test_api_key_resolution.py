"""回归测试：API Key 解析与「请求失败」bug。

Bug 现场：
    dist/.env 是直接从 .env.example 复制来的**没改过**的模板，里面写着
    DEEPSEEK_API_KEY=sk-your-deepseek-key-here。config.py 早期把 EXE 目录的
    .env **先**加载，而 load_dotenv 默认不覆盖已有变量，于是这份占位符
    挡住了项目根 .env 里的真 key。用户看到的现象只有前端一句
    「请求失败: ...」，因为真正的 401 从来没被渲染出来。

本测试锁住三件事：
  1. 占位符必须被识别成「未配置」，而不是当作有效凭据发出去
  2. 加载顺序里，**更具体的配置后加载**（后加载优先级更高）
  3. /api/* 抛异常时必须返回 JSON，而不是 HTML 500 页面
"""
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  <- {detail}" if detail else ""))


print("=" * 60)
print("测试 1：占位符识别")
print("=" * 60)
for k in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)
from src import config  # noqa: E402

PLACEHOLDERS = [
    "sk-your-deepseek-key-here",
    "sk-ant-your-anthropic-key-here",
    "sk-xxx",
    "YOUR-KEY-HERE",
]
for p in PLACEHOLDERS:
    check(f"占位符被识别: {p!r}", config._looks_like_placeholder(p))

REAL_KEYS = ["sk-753a857679ad7f835d2702f9a6871174", "sk-ant-api03-AbCdEf123456"]
for r in REAL_KEYS:
    check(f"真 key 不被误判: {r[:16]}...", not config._looks_like_placeholder(r))

check("空值不算占位符", not config._looks_like_placeholder(""))
check("None 不算占位符", not config._looks_like_placeholder(None))
check("纯空白不算占位符", not config._looks_like_placeholder("   "))

print()
print("=" * 60)
print("测试 2：占位符 → 配置为空（而不是放行）")
print("=" * 60)
import importlib  # noqa: E402

os.environ["DEEPSEEK_API_KEY"] = "sk-your-deepseek-key-here"
importlib.reload(config)
check("置成占位符后 DEEPSEEK_API_KEY 变空", config.DEEPSEEK_API_KEY == "",
      repr(config.DEEPSEEK_API_KEY))
check("resolve_api_key 返回空", config.resolve_api_key("deepseek") == "")
try:
    config.validate_config("deepseek")
    check("validate_config 应抛错", False, "居然通过了")
except ValueError as e:
    check("validate_config 抛出可读提示", "API Key" in str(e), str(e)[:60])

os.environ["DEEPSEEK_API_KEY"] = "sk-753a857679ad7f835d2702f9a6871174"
importlib.reload(config)
check("换成真 key 后被接受", config.DEEPSEEK_API_KEY.startswith("sk-753a"))
check("resolve_api_key 拿到真 key", config.resolve_api_key("deepseek").startswith("sk-753a"))
try:
    config.validate_config("deepseek")
    check("validate_config 通过", True)
except ValueError as e:
    check("validate_config 通过", False, str(e)[:60])

print()
print("=" * 60)
print("测试 3：加载顺序 — 更具体的配置必须后加载")
print("=" * 60)
import inspect  # noqa: E402

src = inspect.getsource(config._load_env_files)
# 冻结分支里，EXE 目录必须排在 ROOT_DIR(=解包目录) 之后
i_root = src.find("candidates.append(ROOT_DIR")
i_exe = src.find("candidates.append(Path(sys.executable)")
check("冻结分支同时考虑两处 .env", i_root != -1 and i_exe != -1)
check("EXE 目录的 .env 后于解包目录 .env（优先级更高）", i_root < i_exe,
      f"root@{i_root} exe@{i_exe}")
check("ENV_FILES 被记录（便于诊断）",
      hasattr(config, "ENV_FILES") and isinstance(config.ENV_FILES, list))

print()
print("=" * 60)
print("测试 4：dist/.env 里不能残留占位符")
print("=" * 60)
dist_env = ROOT / "dist" / ".env"
if dist_env.is_file():
    txt = dist_env.read_text(encoding="utf-8", errors="replace")
    check("dist/.env 无 deepseek 占位符", "your-deepseek-key-here" not in txt)
    check("dist/.env 无 anthropic 占位符", "your-anthropic-key-here" not in txt)

    def grab(text, name):
        for line in text.splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
        return None

    ds = grab(txt, "DEEPSEEK_API_KEY")
    check("dist/.env 的 DEEPSEEK_API_KEY 不为空", bool(ds), repr(ds))
    check("dist/.env 的 key 形如真实凭据",
          bool(ds) and ds.startswith("sk-") and "your" not in ds, repr(ds))
else:
    check("dist/.env 存在", False, str(dist_env))

print()
print("=" * 60)
print("测试 5：/api/* 异常必须返回 JSON（前端才解析得动）")
print("=" * 60)
os.environ["DEEPSEEK_API_KEY"] = "sk-753a857679ad7f835d2702f9a6871174"
importlib.reload(config)

try:
    import app as app_mod
    client = app_mod.app.test_client()

    # 触发一个必然的 400（缺输入）
    r = client.post("/api/start", json={})
    check("缺输入 → 400", r.status_code == 400, str(r.status_code))
    check("缺输入 → JSON", r.is_json, r.content_type)
    check("缺输入 → 含 error 字段", "error" in (r.get_json() or {}))

    # 触发配置错误：把 provider 指向一个不存在的，validate_config 会抛
    r2 = client.post("/api/start", json={"url": "https://example.com/x.mp4",
                                         "provider": "nonexistent-provider"})
    check("非法 provider → JSON 而非 HTML", r2.is_json,
          f"content_type={r2.content_type}")
    body = r2.get_json() or {}
    check("非法 provider → error 可读", "error" in body, str(body)[:80])

    # 上传：不带文件
    r3 = client.post("/api/upload", data={}, content_type="multipart/form-data")
    check("无文件上传 → 400 JSON", r3.status_code == 400 and r3.is_json,
          f"{r3.status_code} {r3.content_type}")

    # 关键：任何 /api/* 路由抛未捕获异常，都要是 JSON。
    # app.py 里挂了一条 test-only 端点 /api/_selftest_boom 专门验证这条链路。
    r4 = client.get("/api/_selftest_boom")
    check("未捕获异常 → JSON", r4.is_json, f"content_type={r4.content_type}")
    check("未捕获异常 → 状态码 500", r4.status_code == 500, str(r4.status_code))
    j4 = r4.get_json() or {}
    check("未捕获异常 → 带出异常信息",
          "selftest" in (j4.get("error") or ""), str(j4)[:120])

    # /api/health 自检端点能用，且不泄露明文 key
    r6 = client.get("/api/health")
    check("/api/health → 200 JSON", r6.status_code == 200 and r6.is_json,
          f"{r6.status_code} {r6.content_type}")
    h = r6.get_json() or {}
    check("health 报告 deepseek 已配置",
          (h.get("api_keys", {}).get("deepseek", {}) or {}).get("configured") is True,
          str(h.get("api_keys"))[:100])
    check("health 不泄露明文 key",
          "sk-753a857679ad7f835d2702f9a6871174" not in json.dumps(h))
    check("health 报告输出目录可写", h.get("output_dir_writable") is True,
          str(h.get("output_dir_writable")))
    check("health 报出加载的 .env 文件", bool(h.get("env_files_loaded")))

    # 非 API 路由仍走 HTML（不要影响正常页面）
    r5 = client.get("/nonexistent-page")
    check("非 API 路由仍是 HTML 404", not r5.is_json, r5.content_type)
except Exception as e:
    import traceback
    traceback.print_exc()
    check("Flask 集成测试可运行", False, f"{type(e).__name__}: {e}")

print("\n[数字配置项：写错值不能让应用起不来]")
# 裸写 int(os.getenv(...)) 的后果：.env 里一个笔误会让整个应用在**导入阶段**
# 就抛 ValueError（实测报错是 invalid literal for int() with base 10: 'auto'
# 加一段 config.py traceback）。打包版被双击时用户看不到控制台，
# 表现为「点了没反应」。改成退回默认值 + 明确警告。
import subprocess as _sp  # noqa: E402

_probe = (
    "import sys; sys.path.insert(0, r'%s');"
    "from src.config import FUSE_BATCH_SIZE, DIARIZE_CLUSTER_THRESHOLD;"
    "print(FUSE_BATCH_SIZE, DIARIZE_CLUSTER_THRESHOLD)"
) % ROOT

_r = _sp.run([sys.executable, "-c", _probe], capture_output=True, text=True,
             env={**__import__("os").environ,
                  "FUSE_BATCH_SIZE": "auto", "DIARIZE_CLUSTER_THRESHOLD": "高"})
check("写错值不崩溃（退出码 0）", _r.returncode == 0,
      f"rc={_r.returncode} {( _r.stderr or '').strip()[:120]}")
check("退回默认值 15 / 0.92", _r.stdout.strip() == "15 0.92", repr(_r.stdout.strip()))
check("给出可读警告（含配置项名）",
      "FUSE_BATCH_SIZE" in _r.stderr and "不是整数" in _r.stderr,
      (_r.stderr or "")[:140])

_r2 = _sp.run([sys.executable, "-c", _probe], capture_output=True, text=True,
              env={**__import__("os").environ, "FUSE_BATCH_SIZE": "20"})
check("合法值仍然生效", _r2.stdout.strip().startswith("20 "), repr(_r2.stdout.strip()))

print("\n[翻译引擎清单：必须只有一份定义]")
# 曾经这份清单被抄了四份：resolve_api_key / validate_config 各写一遍 if/elif，
# translator.create_translator 再写一遍，网页 <option> 还是手写死的。
# 加第三个引擎要改四处，漏一处就是「后端支持但界面选不到」。
from src.config import VALID_PROVIDERS, resolve_api_key, validate_config  # noqa: E402
from src.translator import create_translator  # noqa: E402

os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-deepseek")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")


def _outcome(fn):
    try:
        fn()
        return "ok"
    except Exception:
        return "raise"


# 真不变量：同一个输入，三处判定必须得出同一个结论
_diverged = []
for _p in list(VALID_PROVIDERS) + ["deepseek".upper(), " deepseek ", "", None]:
    _a = _outcome(lambda: resolve_api_key(_p))
    _b = _outcome(lambda: validate_config(provider=_p, api_key="sk-x"))
    _c = _outcome(lambda: create_translator(_p))
    if not (_a == _b == _c == "ok"):
        _diverged.append((_p, _a, _b, _c))
check("合法引擎（含大小写/空格/空串/None）三处判定一致", not _diverged, str(_diverged))

# 曾经的空串 bug：validate_config 把 '' 回退成默认引擎（通过），
# create_translator 却用 `is None` 判断（炸掉）—— 过得了校验，建不出翻译器。
check("空串不再「过得了校验却建不出翻译器」",
      _outcome(lambda: create_translator("")) == "ok"
      and _outcome(lambda: validate_config(provider="", api_key="sk-x")) == "ok")

_bad = _outcome(lambda: create_translator("openai")) == "raise" and \
    _outcome(lambda: validate_config(provider="openai", api_key="sk-x")) == "raise"
check("未知引擎必须两处都拒绝（校验不能放过去）", _bad)

# 清单里的每个引擎都必须真的建得出翻译器（加了名字没加实现要能被测出来）
_unimpl = [p for p in VALID_PROVIDERS
           if _outcome(lambda p=p: create_translator(p)) != "ok"]
check("VALID_PROVIDERS 里每个引擎都建得出翻译器", not _unimpl, str(_unimpl))

# 网页不再手写 <option>，改由服务端下发
_html = (Path(__file__).resolve().parent / "web" / "templates" / "index.html").read_text(
    encoding="utf-8")
check("网页引擎下拉改为服务端下发", "{% for p in providers %}" in _html)
check("网页不再硬编码引擎选项",
      '<option value="deepseek" selected>' not in _html
      and '<option value="anthropic">' not in _html)

print()
print("=" * 60)
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(1 if FAIL else 0)
