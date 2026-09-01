# AUTO PROMPT BOARD · 最终迭代概述

## 交付物
| 文件 | 说明 |
|---|---|
| `auto-prompt-board.html` | 汽车AIGC创作工作台（单文件，全内联零依赖，**内置全量车系数据 + 热门车系多视角图**） |
| `dongchedi_cache.json` | 懂车帝全量车系缓存（**4534 车系 / 645 品牌**） |
| `dongchedi_photos.json` | 135 款热门车系多视角图 URL（已内联进 HTML） |
| `dongchedi_server.py` | 懂车帝本地数据服务（端口8765，**含多视角参考图**） |
| `fetch_all.py` / `gen_inline.py` / `patch_html.py` | 抓取 → 内联数据生成 → HTML 改造脚本（可复用） |

## 核心成果（已通过 Playwright 真实渲染验证）

### 1. 懂车帝全量数据 ✅
- **4534 款车系 / 645 个品牌**（远超验收标准 ≥4000 / ≥400）
- 关键接口：`motor/brand/m/v1/series?brand_id=X`（替代失效的 select/series）

### 2. 真实参考图（封面 + 多视角）✅
- **100% 车系有真实封面图**（CDN 直链）
- **135 款热门车系预抓取多视角图**（外观3+内饰2），内联进 HTML，**无需启动服务器**即可看到完整6张
- 其他车系启动 `python dongchedi_server.py` 后获取
- 每张图带角标 + 分辨率标签 + 角度名称 + 复制按钮
- 复制图片到剪贴板，粘贴到其他网站/应用

### 3. 车型展示：榜单 + 前10 + 专业卡片 ✅
- **4 个榜单 tab**：🔥热门榜 / 🆕新车榜 / 📈销量榜 / ⭐收藏
- 每个榜单**只展示前 10 条**，"查看更多"每次 +10
- 专业卡片样式（懂车帝/汽车之家风格）：左图 + 右信息（车名/品牌·级别/价格/能源·级别标签）
- 筛选标签（动力 8 类 + 车型 7 类）保留

### 4. 统一详情弹层 ✅
- 兼容 Mock + DCD 数据源
- 弹层三段式：头部 + 多视角参考图 6 宫格 + 配置参数表
- 收藏 / 加入创作（联动 selected 状态）

### 5. 内容方向精简 ✅
- 16 个分类（去掉了"设计脑洞"），全屏 1280×800 正好显示完整
- 布局：内容方向列加宽（25%），分类名允许换行不再截断

### 6. 每日资讯三模块 ✅
- **资讯**（6 条）：选题向行业动态（新车/行情/智驾/保值/补能/市场）
- **选题**（8 条）：精简主选题（前瞻/对比/PK/智驾/盘点/场景）
- **备用**（5 条）：撞车时启用
- 资讯条目用蓝色左边框 + 渐变背景区分

### 7. 易用性优化 ✅
- 修复 `renderHeaderStats` 空值保护、`name` 字段品牌重复、`DCD_STATIC` 残留引用、`brandChips.parentNode` null 等 bug
- 修复 `.vgrid display:grid` 覆盖品牌分组列表的 bug
- 修复 `showVehicleDetail` 中 ph 始终 null 的 bug
- 实测 JS 错误：无

## 第二阶段 · 阶段一+二落地（本轮完成）

针对用户三个痛点做纯前端修复，**零 AI 依赖、不改后端、不破坏现有 UI**。

### 阶段一：车型数据标准化 ✅
- 新增 `normalizeEnergy()`：动力归一 燃油/纯电/插混/增程/油混（空值归"燃油"）
- 新增 `inferBodyType()`：级别+车名推断车身形态，替代硬编码 `'suv'`
- 结果：动力筛选 **7 类**、车型筛选 **11 类**

### 阶段二：车型选择逻辑 ✅
- **榜单真正差异化**：`dcdRankLoad` 保存完整榜单、`rankVehicles` 按 series_id 匹配位次排最前、`renderVGrid` 注入榜单车
- **筛选"全部"默认 A-Z 排序**：`cmpAZ` 按品牌拼音首字母排
- **车型库 A-Z 字母索引**：26 字母索引栏，点击直达品牌分组

---

## 本轮需求落地（选择车型去A-Z / 榜单真实 / 品牌列表 / 选中同步 / AI自配置Key）

### ① 字段映射 bug 修正（关键）
- 实测 `DCD_FULL_DATA` 紧凑数组字段顺序为 `[series_id, 车系名, 品牌, 价格, 级别, 能源, 图片]`
- 即 **`row[4]=级别`、`row[5]=能源`**（此前一度搞反，本轮最终纠正为 `rawEnergy=row[5]`、`level=row[4]`）
- 能源归一分布：燃油 2583 / 纯电 1529 / 插混 345 / 增程 76 / 油混 1

### ② 选择车型区精简
- 移除「A-Z 全部」tab，榜单只留 **热门/新车/销量/收藏**，默认热门榜
- 移除所有列表图片的「懂车帝」徽标
- 热门/销量走懂车帝真实接口；新车榜按车系上新顺序（懂车帝接口暂无独立新车榜类型）

### ③ 车型库默认品牌 A-Z 列表
- 默认不显示车型，只显示 **车企品牌 A-Z 列表**（字母组可展开收起）
- 每个品牌归唯一字母（DCD_BRAND_PY 645 条映射）；点击品牌 → 展示该品牌车型 + 返回按钮

### ④ 选中车型同步到内容区
- 移除选择车型模块底部「已选栏」；参考图（多视角 6 宫格）+ 上下移/移除整合进内容区参数速览卡片

### ⑤ AI 自配置 Key（用户第 48-63 条要求）
- **默认关闭 AI**，本地模板完整可用（离线/零成本）
- `state.ai` 只存 `enabled/provider/apiKeyConfigured/model/baseUrl`，**不存明文 Key**
- Key 存用户浏览器 localStorage，**源码零真实 Key**（仅 placeholder）
- `AIProvider` 抽象：OpenAI / DeepSeek / 通义千问 / 智谱 GLM / 豆包 / 其他 OpenAI 兼容服务
- 新增「设置」页：AI 开关 / 服务商 / Key（显示隐藏）/ 模型 / Base URL / 测试连接 / 保存
- 生成按钮随状态切换：「使用模板生成」⇄「AI智能生成」
- AI 失败自动 fallback 到 `buildPrompt()`/`genTitle()`，弹层带「重试 AI」

### 验证
- Node vm 沙箱冒烟测试全过（字段映射/能源/品牌列表/内容区参考图/按钮切换/无真实 Key）
- 语法检查通过；临时脚本已清理

---

## AI 联调修复（CORS 跨域 + 服务商默认模型）

### 问题
用户填入真实 DeepSeek Key 后「测试连接」失败。排查：Key 本身有效（curl 直连 HTTP 200），根因是 **浏览器 CORS 跨域**——DeepSeek/通义/智谱/豆包等国内大模型 API 均不返回 `Access-Control-Allow-Origin`，页面从 `file://` 直连 fetch 会被浏览器拦截。

### 解决方案（本地代理）
- 后端 `dongchedi_server.py` 新增 `POST /api/ai` 代理接口：接收 `{baseUrl, apiKey, model, messages, temperature, max_tokens}`，转发到大模型 `/chat/completions`，返回 `{ok, content}`
  - **Key 仅本次请求使用**，不落盘、不缓存、不记录日志
  - 白名单校验 host（deepseek/openai/aliyuncs/bigmodel/volces/moonshot/zhipuai/siliconflow），防 SSRF
- 前端 `AIProvider.request()` 改为**优先走 `http://127.0.0.1:8765/api/ai` 本地代理**（同源无 CORS），代理不可用（服务未启动/服务商不支持）自动回退直连

### 服务商默认模型（新增 `AI_PROVIDER_MODELS`）
模型留空时不再硬编码 `gpt-4o-mini`，改为按服务商自动选择：
- OpenAI → `gpt-4o-mini` / DeepSeek → `deepseek-chat` / 通义 → `qwen-plus` / 智谱 → `glm-4-flash` / 豆包 → `doubao-1.5-pro-32k`
- 切换服务商时模型输入框 placeholder 同步提示默认模型名
- 测试连接按钮改用下拉框当前选择（而非已保存 state）

### 验证
- 端到端实测：`POST /api/ai` 用真实 DeepSeek Key 返回 `{"ok":true,"content":"成功"}` 及完整标题/prompt
- 前后端语法检查通过；源码确认无 Key 泄漏（`sk-` 仅出现在 placeholder）

---

## 使用方式
浏览器**双击打开 `auto-prompt-board.html`** 即可使用（数据已内联，无需启动服务器）。

```bash
python dongchedi_server.py   # 可选：需要冷门车系的多视角图时启动
```

## 注意事项
- 全量数据为抓取时点快照，如需更新可重跑 `fetch_all.py` → `gen_inline.py` → `patch_html.py`
- 服务启动时强制 no_proxy 避免工作环境代理干扰
- 多视角图按 `wg[1]/wg[3]/wg[5]` + `ns[0]/ns[1]` 索引取（详见方法论 v2 第 2.4 节）
