# Taste Trainer 前端设计规范

> 版本：0.1 · 2026-09-21
>
> 当前范围：Provider 配置页、Settings 默认模型、文本输入框、模型选择区。后续前端实现必须以本文件为视觉和交互基线。

## 1. 设计定位

Taste Trainer 不是冷冰冰的后台表单，而是一间“有编辑感的训练工作室”：

- 温暖、克制、可长时间阅读；
- 技术配置要可靠，但不要像浏览器默认表单或运维面板；
- 让用户明确知道“我正在配置什么、当前是否保存、下一步会发生什么”；
- 把 provider 配置当作一个连续工作流，而不是三个孤立的输入框。

关键词：**editorial utility / quiet technical / warm precision**。

不使用：玻璃拟态、霓虹渐变、过度圆角、重阴影、默认浏览器控件、满屏彩色卡片、为了装饰而添加的 badge 或指标。

## 2. 参考与提炼

以下产品只作为信息架构和交互节奏参考，不直接复制品牌视觉：

- [Vercel AI Gateway provider catalog](https://vercel.com/ai-gateway/models/providers)：provider 与模型数量并列呈现，适合借鉴“先看 provider，再看模型集合”的层级。
- [Vercel AI Gateway provider options](https://vercel.com/docs/ai-gateway/models-and-providers/provider-options)：路由顺序、白名单、fallback 等配置需要显式表达，不能藏在不透明的高级设置里。
- [OpenRouter Models](https://openrouter.ai/models)：模型列表强调可扫描、可比较、可筛选；本项目采用其信息密度思路，但不做 marketplace 化。
- [Replicate model settings](https://replicate.com/docs/topics/models/hardware)：配置集中在 Settings 工作流里，并用明确的 Save 完成提交；适合借鉴保存状态和设置分组。
- [Input field design principles](https://www.zachswebdesigns.com/input-field-design-10-ux-rules-for-form-fields-that-users-actually-complete/)：输入框要有清晰边界、可见 label、明确 focus/error 状态，placeholder 不能承担 label 的职责。

提炼成 Taste Trainer 的原则：**清晰的 field anatomy、稳定的保存反馈、可扫读的 model rows、开放留白而不是堆卡片**。

## 3. 视觉语言

### 3.1 色彩 token

保持现有项目的色彩基因，后续实现只引用 token，不在组件内临时写新颜色。

| Token | 值 | 用途 |
|---|---|---|
| `--ink` | `#171817` | 主文字、深色侧栏 |
| `--paper` | `#F5F1E8` | 页面背景，温暖但不发黄 |
| `--surface` | `#FBF9F3` | 表单面板、provider card |
| `--field` | `#FFFDF8` | 输入框内部背景 |
| `--line` | `#C9C2B4` | 边框、分隔线 |
| `--muted` | `#6D6B64` | 辅助说明、placeholder |
| `--blue` | `#2F67D9` | 主操作、focus、链接、选中态 |
| `--green` | `#209B68` | 已保存、成功、在线状态 |
| `--danger` | `#A04737` | 错误文字；背景使用低饱和浅红 |

色彩规则：

- 主色只保留一个蓝色操作色，避免每个状态都有不同的彩色背景；
- `focus` 用蓝色边框 + 很轻的外圈，不用发光；
- success/error 状态用文字和细边框传达，避免大面积 alert 抢走表单焦点；
- provider 卡片与页面背景只做一级明度差，不做浮夸阴影。

### 3.2 字体

- 页面标题：`Playfair Display`，600，紧字距，保留当前产品的编辑感；
- 字段 label、按钮、正文：`DM Sans`；
- API key、URL、model id、provider id、状态小标签：`DM Mono`；
- 输入文字不能依赖浏览器默认字体；每个控件必须明确设置 `font-size`、`line-height` 和 `font-weight`。

## 4. Provider 表单结构

Provider 配置页采用单一清晰的工作流：

```text
Provider heading + 删除
简短说明
────────────────────────
Provider name
[ input                              ]

API key                              状态
[ secret input                      ]

Custom base URL
[ https://...                       ]

[ 保存配置 ]  [ 检测模型 ]
保存/错误反馈（就地出现）
────────────────────────
Available models              全部 / 取消全部
模型状态 / 数量
[ checkbox ] model id      owner
[ checkbox ] model id      owner
```

规则：

- 三个 field 使用统一宽度和统一高度；不要出现截图中 API key 只有一小段、状态文字漂到页面最右侧的断裂布局；
- 表单内容保持一个主要阅读列，建议 `max-width: 720px`；模型列表可以横向铺开；
- label 永远在 input 上方，placeholder 只提供例子；
- API key 的 `NEW` / `SAVED` 是输入框右侧的 suffix，不是脱离 field 的浮动文字；
- URL、key 等长文本允许横向滚动或省略，但不能破坏布局；
- 保存按钮只表达“提交当前配置和勾选结果”；检测按钮只读取模型列表，两个动作的状态在按钮下方就地反馈；
- 删除是低权重图标按钮，靠近 heading，必须有 `aria-label` 和 hover 提示。

## 5. 输入框规范（本次重点）

### 5.1 基础尺寸

- 普通 input：高度 `48px`；
- 多行 textarea：最小高度 `208px`，可垂直 resize；
- 水平 padding `14px`，垂直 padding `12px`；
- radius `8px`；边框 `1px solid var(--line)`；
- input 内文字 `14px / 1.45`；textarea 正文 `14px / 1.65`；
- field 之间至少 `18px`，label 与 input 之间 `8px`；
- 小屏幕保持至少 `44px` 的可点击高度，不压缩成细线控件。

### 5.2 三种输入类型

1. **Provider name**：`DM Sans`，普通文本，placeholder 例如 `OpenRouter / LM Studio / My API`。
2. **API key**：`DM Mono`，password 类型；右侧 status slot 显示 `NEW` / `SAVED`，不要把秘密内容暴露给前端回显。
3. **Custom base URL**：`DM Mono`，URL 文本；focus 时保留完整可读性，错误时在 field 下方显示原因。

### 5.3 状态

| 状态 | 表现 |
|---|---|
| idle | 暖白 field、灰米色边框 |
| hover | 边框略加深，不移动布局 |
| focus | 蓝色边框 + `0 0 0 3px rgba(47,103,217,.12)` |
| filled | 文字使用 `--ink`，placeholder 消失 |
| saving | 按钮显示处理中，field 不闪烁，不清空用户输入 |
| saved | 绿色小字/状态点，说明模型检测已完成 |
| error | 红色细边框或左侧标记；错误文案紧贴 field 下方 |
| disabled | 低对比度背景，但仍保留清晰边界；不能只靠 opacity |

所有错误必须能被屏幕阅读器读到：使用 `aria-invalid` 与 `aria-describedby`，不要只用颜色。

## 6. 按钮与模型列表

### 按钮

- 主按钮：蓝底白字，`48px` 高，radius `8px`，宽度由内容决定，桌面端不要无理由撑满整行；
- 次按钮：透明/纸张底，灰米色边框；
- primary 与 secondary 并排时，两者高度相同，间距 `10px`；
- hover 只做轻微明度变化或 `translateY(-1px)`，不放大、不发光；
- disabled 状态必须看得出“暂不可用”，但文字仍可读；
- 箭头使用小型 SVG/icon 或统一方向符号，不混用不同 glyph 风格。

### 模型列表

- 列表是“可扫描的行”，不是一堆独立卡片；
- 每行高度 `44–48px`，checkbox、model id、owner 三列对齐；
- hover 使用浅蓝/浅灰背景，selected 使用蓝色 checkbox 与极轻的行背景；
- `全部 / 取消全部` 是低权重文字操作，放在列表标题右侧；
- 长 model id 使用 monospace + ellipsis，hover 或辅助文本提供完整值；
- 空状态用一个浅色区域说明“点击检测模型获取列表”，不放巨大插画或无关图标。

## 7. 间距、边框与容器

- 基础间距：`4 / 8 / 12 / 18 / 24 / 32 / 48 / 64px`；
- provider card：surface 背景、`1px` 边框、radius `10px`、无重阴影；
- 表单内部用分隔线组织层级，不给每一个 field 再套一层 card；
- 页面主内容保持足够留白，但第一屏必须看见 heading、表单起始位置和主操作；
- 移动端取消侧栏后，表单仍保持同一 field 顺序，按钮变为等宽纵向堆叠。

## 8. 动效与反馈

- 统一过渡：`150ms ease-out`；
- focus、hover、button loading 是必要动效；不加入装饰性漂浮、弹跳或渐变动画；
- 保存成功后只做一次轻量状态变化，避免 toast 覆盖表单；
- `prefers-reduced-motion: reduce` 时关闭位移和非必要过渡；
- 表单提交失败时保留输入值、把错误贴近问题 field，并让用户可以立即重试。

## 9. 实现约束

- 继续使用当前 React + Vite 结构，不引入新的 UI 框架来解决三个输入框；
- 颜色、字体、间距、radius 统一从 CSS token 来；
- Provider 表单拆出可复用的 `Field` / `SecretField` / `FieldMessage` 结构，避免每个 input 各写一套 CSS；
- 不把截图做成静态图片；所有文字、输入、checkbox、按钮必须保持真实可交互；
- 不修改当前信息架构，不新增无关的导航、统计卡或装饰组件；
- 本次实现优先解决：默认浏览器样式、输入框层级、状态 slot 对齐、按钮尺寸和移动端布局。

## 10. 验收清单

- [ ] 输入框不再出现浏览器默认黑色粗边框；
- [ ] label、placeholder、实际输入文字的层级清晰；
- [ ] API key 的 NEW/SAVED 与输入框对齐，不漂移到卡片边缘；
- [ ] focus、saving、saved、error、disabled 五类状态可辨识；
- [ ] provider name、API key、base URL 的宽度/高度一致；
- [ ] 主次按钮在桌面和移动端都保持合理尺寸；
- [ ] 模型列表可以快速扫读，长 model id 不撑破布局；
- [ ] 键盘操作、focus-visible、错误关联和触控尺寸合格；
- [ ] 视觉上仍然属于 Taste Trainer：暖纸张、深墨色、蓝色操作色、编辑感标题。
