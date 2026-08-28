<p align="center">
  <img
    src="docs/assets/brand/zh-CN/hengmu-banner.png"
    width="100%"
    alt="衡木——受证据约束的架构设计与决策系统；青野开源项目"
  >
</p>

<p align="center">
  <a href="https://qingye-lab.github.io/hengmu/">项目网站</a> ·
  <a href="README.md">English</a> · <strong>简体中文</strong>
</p>

<p align="center">
  <a href="https://github.com/qingye-lab/hengmu/actions/workflows/ci.yml">
    <img alt="CI" src="https://github.com/qingye-lab/hengmu/actions/workflows/ci.yml/badge.svg?branch=main">
  </a>
  <a href="https://github.com/qingye-lab/hengmu/releases">
    <img alt="版本 1.1.1" src="https://img.shields.io/badge/version-1.1.1-173FBE">
  </a>
  <img alt="Python 3.11–3.14" src="https://img.shields.io/badge/python-3.11%E2%80%933.14-161719">
  <a href="LICENSE">
    <img alt="MIT 许可证" src="https://img.shields.io/badge/license-MIT-173FBE">
  </a>
</p>

<p align="center">
  <a href="#为什么选择衡木">为什么选择衡木</a> ·
  <a href="#在不同-ide-中安装">安装</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#工作原理">工作原理</a> ·
  <a href="#工作流">工作流</a> ·
  <a href="#信任模型">信任模型</a> ·
  <a href="#packages-and-ide-compatibility">兼容性</a> ·
  <a href="#feedback-and-compatibility-reports">反馈</a> ·
  <a href="#文档">文档</a>
</p>

---

衡木是一个面向 Codex 和兼容 Agent Plugins 的本地优先、受证据约束的架构审查
与目标设计工具。它把仓库事实、已批准的设计意图和明确约束，转化为受证据约束
的现状审查、开放式或受约束的目标架构、可追溯决策、可执行计划和确定性策略结果。

现状审查与目标设计是两条同等重要的入口。既有系统可以从候选问题出发，经过
独立核实形成改造决策；新系统或重大重构可以从已批准的 Design Brief 出发，
直接形成完整目标架构，不虚构 Review 或 Finding。必需、偏好和禁止约束都要
接受挑战，不能被当作可行性或适配性的证明。

这一架构视角覆盖性能效率、可靠性、安全与隐私边界、数据与 API 契约、
可观测性、测试、部署、技术债、设计比例性、技术选型与运行现实。专项视角还
覆盖 AI Agent 的 Context 与经济性、Memory、工具权限、隐私、行为证据、
技术演进、移动系统和多项目组合。

如果你正在寻找仓库本地架构审查、AI Agent 架构审计、目标架构设计、架构决策治理、
改造规划或确定性质量门禁，衡木就是为这类工作流构建的。它不是托管式架构服务，
也不是通用代码 Linter。

它同时覆盖两个层级：

- 单个仓库：读取项目自己的 Profile、约束、关键链路、规则和审核历史；
- 项目组合：识别重复建设、技术栈扩散、共享能力、所有权冲突、
  项目间数据流和隐性耦合。

| 能力 | 衡木做什么 |
| --- | --- |
| 现状审查 | 审查边界与工程质量，并让问题保持候选状态，直到独立核实完成证据解析。 |
| 目标架构设计 | 围绕运行与部署单元、数据所有权、接口、信任边界、关键链路、运维和技术选择，设计开放式或受约束目标。 |
| 决策与计划 | 比较可行方案、记录未选方案为何落选，再把已授权决策转成有顺序的改造或 Greenfield 实施切片。 |
| 证据治理 | 把事实、Knowledge、来源、权限、接受状态和确定性策略绑定为一条可审计链路。 |

## Packages and IDE compatibility

衡木从同一份源码发布两种压缩包。两种压缩包的发现 Manifest 和实际生效的宿主
UI 投影不同，但底层 Skill 和本地架构运行时不分叉。

| 压缩包 | Manifest 与内容 | 目标宿主 | 本仓库已验证的内容 |
| --- | --- | --- | --- |
| Codex 压缩包 | `.codex-plugin/plugin.json` 加 Codex `agents/openai.yaml` 元数据 | Codex | 原生 Manifest、Skill 契约、确定性压缩包及 CI/发布流程 |
| Agent Plugins 压缩包 | 宿主中立的根目录 `plugin.json`、标准 `skills/` 与 `resources/`，以及为 Selector 溯源保留的惰性 Codex Manifest | Cursor、VS Code/GitHub Copilot 及其他 Agent Plugins 1.0 客户端 | 标准 Manifest/目录投影、确定性压缩包，以及排除 Codex 专用的 `agents/openai.yaml` 文件 |

可移植压缩包包含 Agent Skills 和共享运行时资源，不包含 `mcp.json`，也不包含 Cursor 专用的
`.cursor-plugin` 扩展。[Cursor 插件文档](https://cursor.com/docs/plugins.md)
说明符合规范的 Agent Plugin 可以不经修改加载；但本仓库目前还没有记录 Hengmu
在 Cursor 或其他外部 IDE 中安装后的专项冒烟测试。
因此，格式兼容不等同于命令、权限、UI 或市场行为完全一致。

当前证据边界请见[兼容性矩阵](docs/compatibility.md)。Kiro 当前通过 Agent Skills
目录加载同一组 Skill，而不是读取根目录 Agent Plugins Manifest。两种衡木压缩包都
不会安装宿主专用 Hook、权限、Rules、Steering 或自动生命周期行为。

## 在不同 IDE 中安装

从同一个 [GitHub Release](https://github.com/qingye-lab/hengmu/releases) 下载两个
ZIP 及对应的 `.sha256` 文件。Codex 使用 `hengmu-<version>.zip`；下面其他宿主使用
`hengmu-<version>-agent-plugins.zip`。解压前验证下载文件：

在下载目录中，macOS 使用 `shasum`；Linux 可把命令替换为 `sha256sum -c`：

```bash
shasum -a 256 -c hengmu-<version>.zip.sha256
shasum -a 256 -c hengmu-<version>-agent-plugins.zip.sha256
```

把解压目录放在稳定的绝对路径，并记为 `HENGMU_ROOT`。每个宿主都需要完整目录，
不能只复制 `skills/hengmu`；Router、八个聚焦 Skill、Schema、Knowledge 和确定性
CLI 会通过相对路径共同使用 `skills/` 与 `resources/`。

### Codex 与 ChatGPT 桌面端

把 Codex ZIP 解压到个人插件目录，例如 `~/.codex/plugins/hengmu`。然后把下面条目
加入 `~/.agents/plugins/marketplace.json` 的 `plugins` 数组；如果文件已有内容，
请合并，不要覆盖：

```json
{
  "name": "hengmu-local",
  "interface": { "displayName": "Hengmu Local" },
  "plugins": [
    {
      "name": "hengmu",
      "source": {
        "source": "local",
        "path": "./.codex/plugins/hengmu"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Developer Tools"
    }
  ]
}
```

重启 ChatGPT 桌面端，打开 **Plugins**，选择 **Hengmu Local** 并安装。在 Codex CLI
中运行 `/plugins`，从同一 Marketplace 安装后新建会话。Codex 使用
`$hengmu audit this repository` 或自然语言调用；ChatGPT Work 模式使用 `@hengmu`。
Codex IDE 扩展可以使用独立 Skill，但当前不提供插件浏览器；完整衡木插件请通过
ChatGPT 桌面端或 Codex CLI 安装。参见
[OpenAI 官方插件安装文档](https://developers.openai.com/codex/plugins/)。

### Cursor

把 Agent Plugins ZIP 解压到 `~/.cursor/plugins/local/hengmu`，然后重启 Cursor，
或运行 **Developer: Reload Window**。打开 **Customize**，确认衡木 Skill 已启用。
开发时也可以把检出的仓库链接到本地插件目录：

```bash
mkdir -p ~/.cursor/plugins/local
ln -s /absolute/path/to/hengmu ~/.cursor/plugins/local/hengmu
```

使用 `/hengmu audit this repository` 或自然语言调用。Cursor 会加载可移植 Skill，
但本包不安装 Cursor 专用 Rules、Agents、Commands、Hooks 或 Variables。参见
[Cursor Agent Plugins 安装文档](https://cursor.com/docs/plugins.md#installing-plugins)。

### VS Code 与 GitHub Copilot

启用 `chat.plugins.enabled`，运行 **Chat: Install Plugin From Source**，输入
`https://github.com/qingye-lab/hengmu`。如需固定到本地构建版本，请解压 Agent
Plugins ZIP，并在 VS Code Settings 中登记它的绝对路径：

```json
{
  "chat.pluginLocations": {
    "/absolute/path/to/hengmu": true
  }
}
```

打开 **Chat: Open Customizations → Plugins** 确认安装。VS Code 会给插件内的
Skill 加上插件名前缀，因此在 Copilot Chat 中使用
`/hengmu:hengmu audit this repository`，也可以直接用自然语言调用。同一个包
也可以直接安装到 GitHub Copilot CLI：

```bash
copilot plugin install qingye-lab/hengmu
copilot plugin list
copilot
```

安装后新建 Copilot CLI 会话，再调用 `/hengmu ...`。参见官方
[VS Code Agent Plugins](https://code.visualstudio.com/docs/agent-customization/agent-plugins)
和 [GitHub Copilot CLI Plugin](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference)
文档。

### Kiro

Kiro 从 `.kiro/skills/` 发现工作区 Skill；这一安装路径不会读取衡木根目录的
`plugin.json`。把 Agent Plugins ZIP 解压到稳定的 `HENGMU_ROOT` 后，把 Skill 与
共享资源一起投影到目标仓库。仅当 `.kiro/resources` 未被占用时执行；否则请有意识
地合并目录：

```bash
mkdir -p .kiro/skills .kiro/resources
cp -R "$HENGMU_ROOT/skills/." .kiro/skills/
cp -R "$HENGMU_ROOT/resources/." .kiro/resources/
```

在 Kiro 中打开 **Agent Steering & Skills**，确认九个衡木 Skill 都可见。使用
`/hengmu audit this repository` 或自然语言调用。不要只导入 `skills/hengmu`：
Router 会委派给八个同级 Skill，而这些 Skill 还需要共享运行时。参见
[Kiro Agent Skills 官方文档](https://kiro.dev/docs/skills/)。

### 准备共享 Python 运行时

衡木 Skill 可跨宿主复用，但确定性 Helper 需要 Python 3.11–3.14、PyYAML 和
jsonschema。请把锁定依赖安装到 IDE Agent 可见的 `python3` 环境，或从下面已激活
的环境启动 IDE：

```bash
cd "$HENGMU_ROOT"
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --require-hashes -r requirements-runtime.lock
python3 resources/scripts/architecture_tool.py --version
```

最后一条命令应输出 `architecture_tool.py 1.1.1`。Windows PowerShell 使用
`.venv\Scripts\Activate.ps1` 激活。安装不会授予权限或启用 Hook；在允许仓库写入
或 Shell 执行前，请检查对应宿主的 Agent 权限。

## 为什么选择衡木

多数代码与架构评审停得太早：它们只输出观察意见。衡木围绕一条更长、
且始终受证据约束的工程决策闭环设计。

| 常见审核失效方式 | 衡木的应对 |
| --- | --- |
| 模型看到大文件或单例，就直接认定架构有问题。 | 候选问题必须经过独立核实和证据解析，才能成为可信结论。 |
| 团队指定了技术栈，却没有目标架构或明确取舍。 | 衡木会挑战必需、偏好和禁止约束，比较合规变体，并记录完整目标架构，而不是把技术名称当作证明。 |
| 缺失能力只被当作批评，却没有形成设计。 | 已确认缺口会进入方案比较、改造切片、回滚、测试和验收标准。 |
| 每个项目复制同一份架构提示词，随后逐渐分叉。 | 一套全局方法读取仓库本地的 Profile 和真实约束。 |
| 单看每个仓库都合理，放在一起却重复建设基础设施。 | 项目组合审核会建模共享能力、依赖、数据流、所有权和耦合。 |
| 文字策略写了“必须”，自动化却无法证明。 | JSON Schema、哈希、Git 证据、角色策略、指纹、签名和稳定退出码让执行可以复现。 |

<p align="center">
  <img
    src="assets/hengmu-readme-illustrations/zh-CN/03-facts-constraints-target.png"
    alt="青野角色校准仓库事实与必需、偏好、禁止约束，并搭出目标架构"
    width="100%"
  >
</p>

衡木不是一份通用“最佳实践”清单。规则只有在保护已声明的质量属性或关键链路时
才有意义；建议只有在项目能理解成本、依赖、迁移顺序和停止条件时才有价值。

## 快速开始

### 1. 准备运行环境

衡木支持 Python 3.11–3.14。运行时完全本地，不依赖托管服务、遥测、
凭据、网络访问或 MCP Server。

```bash
git clone https://github.com/qingye-lab/hengmu.git
cd hengmu

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --require-hashes -r requirements-runtime.lock
python3 scripts/validate_repository.py
```

Windows PowerShell 使用以下命令激活环境：

```powershell
.venv\Scripts\Activate.ps1
```

### 2. 准备仓库

```bash
HENGMU_ROOT=/path/to/hengmu

python3 "$HENGMU_ROOT/resources/scripts/architecture_tool.py" \
  prepare-project-audit --repo /path/to/your-project
```

目录不存在时，命令会根据仓库事实创建本地控制面；目录已经存在时，
命令只验证并复用，不会覆盖：

```text
.architecture/
├── profile.yaml
├── repository-facts.yaml
├── constraints.md
├── critical-flows.md
├── gate-policy.yaml
├── baseline.yaml
├── risk-acceptances.yaml
├── evidence-providers.yaml
├── evidence/
├── rules/
├── runs/
└── reviews/
```

### 3. 在 Codex 中选择目标

下面的示例使用 Codex 的 `$hengmu` 调用语法。在其他 Agent Plugins 宿主中，请安装
可移植压缩包，并按照宿主文档规定的 UI 或命令语法调用同一个 Skill；`$hengmu`
不是可移植的调用契约。

对于既有系统，从现状审查开始：

```text
使用 $hengmu 审计当前仓库。
把缺失能力作为问题，但在建议结构性改动前先核实证据。
```

你只需要记住 `$hengmu`。单独调用会显示完整能力菜单，也可以直接使用
自然语言描述目标：

```text
$hengmu
$hengmu 核实最新的候选问题
$hengmu 比较队列与持久工作流方案
```

对于新系统或重大重构，先补充并批准 Design Brief，再要求开放式或受约束目标：

```bash
if [ ! -e /path/to/your-project/.architecture/architecture-design-brief.yaml ]; then
  cp "$HENGMU_ROOT/resources/templates/architecture-design-brief.yaml" \
    /path/to/your-project/.architecture/architecture-design-brief.yaml
fi

python3 "$HENGMU_ROOT/resources/scripts/architecture_tool.py" \
  validate-design-brief \
  /path/to/your-project/.architecture/architecture-design-brief.yaml \
  --project /path/to/your-project
```

复制得到的模板固定为 `draft`。改成 `approved` 前，必须补充
`brief.approval`：包括 Gate 策略授权的决策者身份，以及至少一条仓库内批准证据的
路径和 SHA-256，并为每位批准者提供一份 detached SSH 签名。签名必须通过项目
`artifact_signatures` 策略验证。验证器不会把模板作者或一个状态字符串当成批准。

```text
$hengmu 根据已批准的 Design Brief 设计开放式目标架构
$hengmu 把目标限定为 FastAPI、PostgreSQL 和一个生产部署单元；逐项挑战约束并记录落选方案
```

当前 Brief 1.1 路径会生成带完整目标架构的拟议 Decision 1.4。衡木不会代替
用户批准 Brief 或 Decision，也不会实现业务代码。具名决策者接受 Decision 后，
`$hengmu plan …` 可以生成 Greenfield Plan 1.3。

审查路径也可以直接从这一步开始：Skill 会调用准备命令，并在缺失时自动初始化
`.architecture/`。只有明确要求“只读”时，才会执行不写入仓库产物的一次性
Advisory 审计。

项目 Profile 决定哪些质量属性和专项审核真正重要。全局 Skill 提供方法，
仓库提供事实。

```yaml
project:
  name: example-service
  type:
    - ai-agent-platform
  critical_qualities:
    - traceability
    - recoverability
    - privacy
  required_reviews:
    - project-architecture
    - ai-agent-architecture
```

### 4. 验证结果

```bash
python3 "$HENGMU_ROOT/resources/scripts/architecture_tool.py" \
  validate-project /path/to/your-project

python3 "$HENGMU_ROOT/resources/scripts/architecture_tool.py" \
  gate --project /path/to/your-project --stage change

python3 "$HENGMU_ROOT/resources/scripts/architecture_tool.py" \
  gate --project /path/to/your-project \
  --decision .architecture/reviews/<greenfield-decision.yaml> --stage change
```

门禁退出码：`0` 表示通过，`1` 表示策略不通过，`2` 表示输入或配置无效。

## 工作原理

衡木把模型判断与确定性信任分开。它支持两条来源路径；候选审计和约束声明
都不是策略或证明。

<p align="center">
  <img
    src="diagrams/zh-CN/hengmu-governance-loop.svg"
    alt="仓库事实和已批准 Design Brief 分别进入现状审查或开放式、受约束目标设计路径，最终汇合到授权决策、计划和确定性门禁"
    width="100%"
  >
</p>

该图同时维护
[Mermaid 源文件](diagrams/zh-CN/hengmu-governance-loop.mmd)和
[可编辑 Excalidraw 文件](diagrams/zh-CN/hengmu-governance-loop.excalidraw)。

1. **建立事实与意图。** 检查仓库并绑定 Profile；目标设计还需要一份包含
   可度量场景和边界的已批准 Brief。
2. **加载上下文。** 绑定约束、关键链路、已选择 Rule Pack 和任务范围内的
   Knowledge，但不把检测到的技术或所有者声明变成证明。
3. **审查或设计。** 既有系统产出候选问题并进入独立核实；Greenfield 工作
   根据已批准 Brief 比较开放式或符合约束的目标，不制造 Finding。
4. **形成决策。** 记录目标、落选方案、取舍、完整架构模型、来源绑定和拟议状态。
5. **完成授权。** 具名决策者接受、拒绝或替代 Decision；衡木路由器和方案顾问
   都不能执行这一权限转换。
6. **制定计划。** 把已接受的改造或 Greenfield 目标拆成有顺序的实施切片、
   保护措施、回滚、停止条件和验收证据。
7. **执行门禁。** 对绑定来源的产物应用确定性的契约、问题、变更或发布策略。

## 一套方法，多个项目

仓库不应该各自保存一份架构方法副本。它只需要保存让自身决策与其他项目不同的上下文：

- `profile.yaml`：项目类型、关键质量属性和必需审核；
- `constraints.md`：真实的技术、产品、监管和团队限制；
- `critical-flows.md`：不能回归的业务和运行链路；
- `architecture-design-brief.yaml`：目标意图、质量场景、边界和类型化约束；
- `reviews/`：候选与可信 Review、Decision、Plan 和证据历史。

<p align="center">
  <img
    src="assets/hengmu-readme-illustrations/zh-CN/02-one-method-many-projects.png"
    alt="一套共享方法横跨不同项目，青野角色依据项目画像和真实约束调整支点并发现隐性耦合"
    width="100%"
  >
</p>

项目组合审核补上系统之系统视角：哪些能力应该共享，哪些边界必须独立，
数据如何流动，以及一个仓库可能在哪里意外影响另一个仓库。

## 工作流

可安装插件公开一个稳定入口和八个聚焦的工作流 Skill。日常只需使用
`$hengmu`；原始 Skill 名称继续供自动化和兼容调用。

| 你输入的内容 | 必需输入 | 输出 |
| --- | --- | --- |
| `$hengmu` | 已声明的仓库上下文（如有） | 菜单和只读下一步说明 |
| `$hengmu audit/ai/mobile/portfolio …` | 仓库或组合事实与 Profile | 对应范围内的候选 Review |
| `$hengmu verify …` | 候选 Review 与可解析证据 | 绑定来源的可信 Review |
| `$hengmu decide …` | 可信 Review 或已批准 Design Brief | 拟议 Architecture Decision |
| `$hengmu design/specify/constrain …` | 已批准 Brief 1.1、事实、约束和已选择 Knowledge | 带开放式或受约束目标架构的拟议 Decision 1.4 |
| `$hengmu plan …` | 已接受的改造或 Greenfield Decision | 有顺序的改造 Plan 1.2 或 Greenfield Plan 1.3 |
| `$hengmu gate …` | Schema 有效且绑定来源的产物 | 确定性策略结果与稳定退出码 |

子命令不是必需的。像 `$hengmu 帮我比较这两个技术方案` 这样的自然语言，
也会自动进入正确的聚焦工作流。

### 聚焦工作流契约

| 阶段 | Skill | 职责 |
| --- | --- | --- |
| 审计 | `project-architecture-audit` | 单仓库中的边界、数据所有权、契约、可靠性、安全、运维、测试、部署、技术债和比例性。 |
| 审计 | `ai-agent-architecture-audit` | 模型、Context、Memory、检索、工具、注入、审批、恢复、评估、成本、延迟和证据边界。 |
| 审计 | `mobile-architecture-audit` | 本地状态、同步、迁移、后台任务、通知、隐私、缓存和生命周期行为。 |
| 审计 | `portfolio-architecture-audit` | 跨项目的重复建设、技术栈扩散、共享能力、依赖、数据流、所有权和隐性耦合。 |
| 核实 | `architecture-finding-verifier` | 挑战候选问题、解析证据、分配 V0–V5 核实等级，并产出绑定来源的可信 Review。 |
| 决策 | `architecture-solution-advisor` | 设计和比较开放式/受约束目标，挑战必需约束，记录偏好取舍，硬排除禁止选项，并绑定单元、链路、边界、运维和 Knowledge。 |
| 计划 | `architecture-remediation-planner` | 把已接受的改造或 Greenfield 目标转成有顺序的实施切片、适用时的迁移控制、保护、停止条件、回滚和验收标准，不虚构 Finding。 |
| 执行 | `architecture-quality-gate` | 对可信产物应用确定性的契约、问题、变更和发布策略。 |

`$hengmu` 也可以基于现有产物解释只读生命周期状态和下一条有效工作流。
它不会核实 Finding、接受 Decision、修改策略，或代替用户运行 Gate。

### 项目自有的质量证据

衡木把语言 Linter 和质量分析器视为可选 Evidence Provider，而不是架构真相。
内置目录覆盖 Python、JavaScript/TypeScript、Rust、Go、Swift 和 Kotlin/JVM，
也包含架构、契约、测试、运行时、安全与供应链 Provider。

Provider 发现会区分适用标记、项目配置、启用状态、可执行文件可用性和就绪状态。
缺失的可执行文件会保留为明确的“未评估”证据面。衡木不会隐式下载、安装、启用
或添加依赖；如果安装工具能显著改善审查，它会先说明具体工具、范围、版本策略、
命令、受影响文件和后果，再请求授权。

Provider 通过只证明被捕获的命令、可执行文件、声明的项目依赖闭包、隔离缓存模式、
配置、Commit 和输出字节。
只有当结果绑定到适用不变量并经过独立审查后，才会成为架构证据。

Knowledge 策展仅供维护者使用。其源工作流位于
`maintainer/skills/architecture-knowledge-curator/`，不会扩大公开的最终用户 Skill 表面。

## 信任模型

衡木的信任边界很简单：

> 模型可以提出建议；证据、权限、来源和策略决定什么可以成为可信结论或阻断条件。

可信 Review 会绑定被审查仓库的身份与 Git 状态、精确范围、Profile、
仓库事实、已选择 Knowledge、Rule Pack、候选审核、核实者权限、语义 Finding
指纹、关键链路覆盖和可解析证据。

确定性运行时提供：

- 项目、Review、Decision、Plan、策略、基线、风险接受、Knowledge、
  Provider、Benchmark 和治理产物的 JSON Schema；
- 可机器读取的核心与领域 Rule Pack，以及完整覆盖检查；
- 在明确上下文预算内选择、带来源的 Knowledge Pack；
- 可选 Evidence Provider：禁止 Shell、环境变量白名单、超时、结构化输出校验和防篡改运行记录；
- Git 证据解析、精确哈希、签名验证、SARIF、Review Diff、产物迁移、
  Benchmark 评分和分层门禁。

门禁阶段逐级累积：

| 阶段 | 证明内容 |
| --- | --- |
| `contract` | Schema、来源、身份、哈希、角色和覆盖有效。 |
| `finding` | 严重度、置信度、核实、状态、基线、豁免和风险接受符合策略。 |
| `change` | Review 新鲜度、已变更契约、必要决策、迁移兼容性、签名和证据解析可接受。 |
| `release` | 必需证据、决策权限和完整改造验收已经具备。 |

请阅读[保障模型](docs/assurance-model.md)，了解威胁、控制和剩余风险。
门禁通过只证明已提供产物满足策略，不证明被审计产品正确、安全、合规或设计优秀。

<details>
<summary>可信 Review 与证据命令</summary>

```bash
python3 resources/scripts/architecture_tool.py review-bindings \
  --project /path/to/project \
  --candidate .architecture/reviews/example-candidates.yaml

python3 resources/scripts/architecture_tool.py validate-review \
  /path/to/verified.yaml --project /path/to/project

python3 resources/scripts/architecture_tool.py verify-evidence \
  --repo /path/to/project --review /path/to/verified.yaml

python3 resources/scripts/architecture_tool.py verify-review-signature \
  --project /path/to/project --review /path/to/verified.yaml
```

</details>

<details>
<summary>任务范围内的 Knowledge 选择</summary>

```bash
python3 resources/scripts/architecture_tool.py inspect-repository \
  --repo /path/to/project \
  --output /path/to/project/.architecture/repository-facts.yaml

python3 resources/scripts/architecture_tool.py select-knowledge \
  --facts /path/to/project/.architecture/repository-facts.yaml \
  --profile /path/to/project/.architecture/profile.yaml \
  --task "Current architecture audit" \
  --skill project-architecture-audit \
  --output /path/to/project/.architecture/knowledge-selection.yaml \
  --context-output /path/to/project/.architecture/knowledge-context.yaml

python3 resources/scripts/architecture_tool.py validate-knowledge-context \
  /path/to/project/.architecture/knowledge-context.yaml \
  --selection /path/to/project/.architecture/knowledge-selection.yaml \
  --facts /path/to/project/.architecture/repository-facts.yaml \
  --profile /path/to/project/.architecture/profile.yaml
```

</details>

## 治理模式

不是每个项目都需要相同的治理强度。

| 模式 | 适用场景 | 行为 |
| --- | --- | --- |
| Advisory | 项目需要结构化架构帮助，但不需要阻断门禁。 | Skill 产出有证据的产物；维护者保留全部判断权。 |
| Governed | 重要变更需要可信审核、明确决策和变更策略。 | 强制来源、权限、新鲜度和 Finding 策略。 |
| Enforced | 发布必须具备确定性架构证据并完成改造验收。 | 变更和发布门禁成为交付要求。 |

采用建议请参阅[治理模式](docs/governance-modes.md)。`product_mode`
只是声明的运行层级，不是绕过机制：只要明确调用门禁，就会执行其策略。

## 文档

| 文档 | 何时阅读 |
| --- | --- |
| [目标架构](docs/target-architecture.md) | 事实、Knowledge、工作流、信任边界和运行时组件。 |
| [保障模型](docs/assurance-model.md) | 威胁、保证、不保证的内容和剩余风险。 |
| [治理模式](docs/governance-modes.md) | Advisory、Governed 和 Enforced 的采用方式。 |
| [评估指南](docs/evaluation.md) | 行为 Benchmark、消融、评分和解释边界。 |
| [Knowledge 编写](docs/knowledge-authoring.md) | 来源质量、新鲜度、Frontmatter 和策展规则。 |
| [兼容性](docs/compatibility.md) | 支持的 Python、Schema、产物和版本边界。 |
| [宿主兼容性](docs/host-compatibility.md) | 跨 IDE 目标等价、分发路径和宿主专属能力边界。 |
| [支持与反馈](SUPPORT.md) | 可复现缺陷、文档缺口和宿主兼容性报告。 |
| [迁移到 1.0](docs/migrating-to-1.0.md) | 开放/受约束 Brief/Decision/Plan 产物、共存和回滚。 |
| [发布验证](docs/releasing.md) | 确定性 ZIP、校验和、SBOM 和 Attestation。 |
| [路线图](docs/roadmap.md) | 唯一后续计划、证据里程碑和条件触发项。 |
| [实施矩阵](docs/comprehensive-review-implementation.md) | 审核建议如何映射为可执行能力与证据。 |
| [自审历史](.architecture/reviews/README.md) | 衡木如何治理自身仓库。 |
| [视觉资产](docs/assets/brand/README.md) | 双语 Icon、Banner、青野角色和流程图源文件规范。 |

已接受的架构决策位于 [docs/decisions](docs/decisions/)。
仓库目标状态的实施进展记录在
[目标架构实施矩阵](docs/target-architecture-implementation.md)。

## 开发

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --require-hashes -r requirements-dev.lock

python3 scripts/validate_repository.py
python3 resources/scripts/architecture_tool.py validate-project .
python3 resources/scripts/architecture_tool.py validate-history-anchors .
python3 resources/scripts/validate_knowledge.py
python3 -m pytest
python3 resources/scripts/architecture_tool.py gate --project . --stage change
python3 -m ruff check .
python3 -m ruff format --check .
python3 scripts/audit_licenses.py
```

构建并验证两种确定性插件压缩包：

```bash
python3 scripts/package_plugin.py --format codex --output-dir dist
python3 scripts/package_plugin.py --format agent-plugins --output-dir dist
python3 scripts/smoke_test_package.py \
  --format codex \
  --archive dist/hengmu-<version>.zip
python3 scripts/smoke_test_package.py \
  --format agent-plugins \
  --archive dist/hengmu-<version>-agent-plugins.zip
python3 scripts/verify_checksum.py dist/*.zip.sha256
python3 scripts/generate_sbom.py \
  --archive dist/*.zip \
  --output-dir dist
```

Codex 压缩包是 `hengmu-<version>.zip`；可移植的 Agent Plugins 压缩包是
`hengmu-<version>-agent-plugins.zip`，使用宿主中立的根目录 `plugin.json`。
它还把完整的 `.codex-plugin/plugin.json`（包括 Codex 专用的 `interface` 字段）
作为共享 Knowledge Selector 所需的惰性溯源数据保留在包内；Codex 专用的
`skills/*/agents/openai.yaml` 文件不会进入可移植压缩包。

CI 会在 Linux、macOS 和 Windows 上验证支持的 Python 边界。带 Tag 的发布
会同时提供两种确定性 ZIP、对应的 SHA-256 校验和、SPDX SBOM，以及 GitHub
来源和 SBOM Attestation。

## Feedback and compatibility reports

欢迎真实用户反馈，包括同一个 Skill 在 Codex、Cursor 或其他 Agent Plugins 宿主中
表现不同的情况。可用[缺陷报告](https://github.com/qingye-lab/hengmu/issues/new?template=bug_report.yml)
提交可复现问题，或用[功能建议](https://github.com/qingye-lab/hengmu/issues/new?template=feature_request.yml)
提出聚焦改进；提交前请先阅读 [SUPPORT.md](SUPPORT.md)。

报告 IDE 或宿主结果时，请写明压缩包格式、Hengmu 版本或 Commit、客户端名称与版本、
操作系统、安装路径、Skill 或 Prompt、预期结果、实际结果和脱敏日志。不要包含凭据、
私有仓库内容或个人数据。我们只根据有证据的、带时间边界的结果记录客户端支持：
未测试的客户端不会被写成已验证支持。

## 非目标

衡木不会：

- 自主批准架构决策、风险或发布；
- 把每个检测到的技术、模式或大文件都变成 Finding；
- 在没有明确项目组合 Registry 的情况下发现无关仓库；
- 自动实现被审计产品的改造；
- 取代专门的安全、隐私、性能、法律或合规评估；
- 证明系统绝对安全或正确。

## 参与贡献

欢迎聚焦的问题和 Pull Request。请先阅读
[CONTRIBUTING.md](CONTRIBUTING.md)，再阅读
[GOVERNANCE.md](GOVERNANCE.md)、[SECURITY.md](SECURITY.md) 和
[SUPPORT.md](SUPPORT.md)。

修改公开 Schema、CLI 行为、策略、信任边界或持久化产物时，必须分析兼容性、
补充测试和迁移说明；权限发生变化时还必须更新架构决策。

当 Review 或 Selector Runtime 绑定源提交时，请使用 Merge Commit 保留这些提交。
Squash 或 Rebase 合并可能破坏来源祖先关系，并会被
`validate-history-anchors` 拒绝。

## 致谢与许可证

衡木是一个[青野](https://github.com/liyanqing90)开源项目：
**理性结构中的持续进化，在不确定中，持续构建。**

README 的正文插画方法由
[Ian Xiaohei Illustrations](https://github.com/helloianneo/ian-xiaohei-illustrations)
提供，并以青野公开头像和品牌配色重新设计了原创青野角色。技术流程同时提供
Mermaid、Excalidraw、SVG 和 PNG，确保文档可以继续编辑。

PAAD 衍生概念的署名保留在 [NOTICE](NOTICE) 和
[third_party/PAAD-MIT.txt](third_party/PAAD-MIT.txt)。

软件采用 [MIT License](LICENSE)。青野字标用于标识项目来源，
不授予任何暗示青野认可或背书的权利。
