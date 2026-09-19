"""Deterministic markdown renderers.

LLM returns JSON; these functions turn it into the human-facing markdown
files. Structured files (INDEX.md, manifest.json) are always regenerated,
never hand-edited by the model.
"""
import json
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"
DIMS = ["开场承诺", "镜头职能单一性", "视角策略", "空间交代时机",
        "注意力预告与释放", "情绪节拍", "信息经济性", "节奏曲线",
        "结构与闭环", "可拍性"]


def load_prompt(name: str, **vars) -> str:
    text = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    for k, v in vars.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def read(path: Path, default: str = "") -> str:
    return path.read_text(encoding="utf-8") if path.exists() else default


def load_meta(path: Path) -> dict:
    p = path / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_meta(path: Path, meta: dict) -> None:
    (path / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def render_prompt_md(d: dict, date: str) -> str:
    c = d.get("constraints", {})
    hints = "\n".join(f"- {h}" for h in d.get("hints", []))
    return f"""# 题目：{d['title']}

> 出题日期：{date} ｜ 难度：{d.get('difficulty', '★★☆')} ｜ 来源：LLM 生成

## 情境

{d['scenario']}

## 约束

- 时长：{c.get('duration', '60 秒以内')}
- 画幅：{c.get('aspect', '竖屏')}
- 对白：{c.get('dialogue', '无')}
- 表现形式：{c.get('form', '不限')}

## 考察重点

{d.get('focus', '')}

想一想：
{hints}

## 交付物

在 `submission.md` 中用**自然语言**写出你的分镜方案：
观众每一秒应该看到什么、为什么这个时候该看这个。
不需要画面分镜图，文字描述即可。写完到面板点「打分」。
"""


def render_review_md(d: dict, title: str, date: str) -> str:
    rows = "\n".join(
        f"| {i + 1}. {s['dim']} | {s['score']}/10 | {s.get('evidence', '')} |"
        for i, s in enumerate(d.get("scores", [])))
    strengths = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(d.get("strengths", [])))
    weak = []
    for w, label in zip(d.get("weakest", []), "AB"):
        rw = "\n".join(
            f"| {r['time']} | {r['visual']} | {r['function']} | {r['intent']} |"
            for r in w.get("rewrite_rows", []))
        weak.append(f"""### 弱项 {label}：{w['dim']}

原方案：{w.get('original', '')}

改写后：

| 时间 | 画面 | 职能 | 意图 |
|---|---|---|---|
{rw}

差距说明：{w.get('gap', '')}""")
    nxt = "、".join(d.get("next_focus", []))
    return f"""# 评分与修改意见

> 题目：{title} ｜ 评审日期：{date} ｜ **总分：{d.get('total', 0)}/100**

## 一句话点评

{d.get('one_liner', '')}

## 逐维评分

| 维度 | 分数 | 依据（引用提交内容） |
|---|---|---|
{rows}

## 最强的点

{strengths}

## 最弱的 2 项 + 改写示范

{chr(10).join(weak)}

## 下次训练重点

{nxt}
"""


def render_revision_md(d: dict, title: str) -> str:
    rev = d.get("revision", {})
    rows = "\n".join(
        f"| {r['time']} | {r['visual']} | {r['function']} | {r['intent']} |"
        for r in rev.get("rows", []))
    changes = "\n".join(
        f"| {c['part']} | {c['v1']} | {c['v2']} | {c['why']} |"
        for c in rev.get("changes", []))
    return f"""# 修改版分镜（v2）：{title}

> 依据 `review.md` 的改写示范整合而成的完整方案。

## 分镜表

| 时间 | 画面（观众看到什么） | 镜头职能 | 注意力意图 |
|---|---|---|---|
{rows}

## 相对 v1 的改动清单

| 环节 | v1 原方案 | v2 改动 | 为什么 |
|---|---|---|---|
{changes}
"""


def render_analysis_md(d: dict, source: str, duration: float) -> str:
    shots = "\n".join(
        f"| {s['time']} | {s['shot']} | {s['function']} |"
        for s in d.get("shots", []))
    prins = "\n\n".join(
        f"### {i + 1}. {p['title']}\n\n{p['detail']}"
        for i, p in enumerate(d.get("principles", [])))
    return f"""# 视频分镜分析：{d.get('title_cn', '')}

来源：{source}，时长约 {duration:.0f} 秒。

## 剧情

{d.get('story', '')}

## 镜头清单

| 时间 | 镜头 | 作用 |
|---|---|---|
{shots}

## 可复用原则

{prins}
"""


def render_index_md(metas: list[dict]) -> str:
    rows = []
    for m in sorted(metas, key=lambda x: x.get("id", ""), reverse=True):
        score = m.get("score")
        score_s = f"**{score}**/100" if score is not None else (
            "待评审" if m.get("status") != "skipped" else "跳过")
        rows.append(
            f"| {m.get('date', m.get('id', '')[:10])} | {m.get('title', '?')} "
            f"| {m.get('difficulty', '—')} | {score_s} "
            f"| {m.get('strongest', '—')} | {m.get('weakest', '—')} "
            f"| [{m['id']}]({m['id']}/) |")
    return "# 训练进度（INDEX）\n\n每次练习一行，评审完成后更新。用于观察能力曲线。\n\n" \
        "| 日期 | 题目 | 难度 | 总分 | 最强项 | 最弱项 | 目录 |\n" \
        "|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
