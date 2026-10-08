# Zotero Cloud MCP：读取与受控写入

**v0.2.0 已实现真正的文献管理操作，不再只是“允许使用全权限 Key”。** 可以创建分类和文献条目、修改元数据、调整归类与标签、管理笔记与批注、合并重复候选、移入回收站和恢复，并在单独启用后永久删除符合条件的条目。

这是单所有者、自托管的实验性项目，非 OpenAI/Zotero 官方产品，尚无独立安全审计。**默认不启用写入，不会因为更新 GitHub 就修改真实文献库。** 开源代码不等于公开文献、服务器口令或 API Key。

## 已实现的范围

| 功能 | 具体行为 |
| --- | --- |
| 分类 | 新建子分类、重命名、移动；防止循环。可删除空叶子分类，保护配置的根分类。 |
| 文献 | 导入 Zotero JSON 文献记录、批量修改合法字段，保留 author/editor 等创建者角色。 |
| 归类与标签 | 加入/移出指定分类，添加/移除/重命名标签；保留范围外已有归类。 |
| 重复项 | 按标准化 DOI/标题找候选。人工复核后保留主条目、迁移子附件/笔记、合并标签和归类，最后将副本移入回收站。 |
| 删除 | 支持回收站与恢复；永久删除须另行启用，只接受已在回收站、无子项的记录。 |
| 内容读取 | 读取元数据、子项、笔记、批注及 Zotero 已同步的附件全文索引。索引缺失不等于 PDF 不存在，部分索引不会报告为全文完整。 |
| 笔记和批注 | 创建/修改纯文本笔记，创建/修改有明确字段及真实 PDF 定位数据的批注。 |
| 导出 | 指定文献导出为 BibTeX、RIS、CSL-JSON。 |
| 审核与记录 | 修改前预览、独立网页主人确认、版本冲突拒绝、重复执行防护、私人操作记录；可逆操作可生成新的撤销计划。 |

**不包含**：PDF 二进制上传/下载、自动向出版社抓取 PDF、OCR、BibTeX/RIS 导入解析（导入为 Zotero JSON）、与桌面客户端完全等价的事务性合并、账号/群组管理、任意 API 访问，以及对永久删除或部分/不确定失败的万能回滚。读写功能不应被理解为“Zotero 所有功能全部复刻”。

## 必须分清的三层权限

**第一层：Zotero Key。** 需要目标库读取权限；实际写入还检查该目标个人库/群组库的写权限。Key 泄露后，攻击者可能绕过本插件操作 Zotero。

**第二层：服务器开关。** `ZOTERO_ALLOW_WRITE_KEY=true` 只让程序接受有写权限的 Key；**真正的管理工具另需 `ZOTERO_ENABLE_WRITES=true`**，并配置私人修改记录数据库。

**第三层：OAuth 与逐次确认。** 老的 `zotero:read` 令牌不能写入；需要重新授权 `zotero:write`。每份修改计划还必须由主人在独立复核页面批准。向模型工具传 `confirmed: true` 不能绕过这一步。

## 配置

已有 `render.yaml` 仍是一个 Free Python 服务，不自动创建付费资源、不自动开启写入。构建与启动：

```
pip install -r requirements.txt && python -m unittest discover -s tests -q
python -m zotero_cloud_mcp
```

只运行一个 worker，健康检查 `/healthz`。私有 GitHub 仓库须授权 Render 的 GitHub 集成，不必为了部署公开源码。

| 环境变量 | 含义 |
| --- | --- |
| `APP_SECRET` | 至少 32 字符的独立随机签名密钥。 |
| `MCP_LOGIN_PASSWORD` | 另一条至少 32 字符随机口令，只在实例授权/复核网页输入。 |
| `ZOTERO_API_KEY` | 目标库访问凭证，不发到聊天、仓库或日志。 |
| `ZOTERO_COLLECTION_KEY` / `ZOTERO_COLLECTION_NAME` | 允许操作的根分类；Key 优先，建议用稳定 Key。 |
| `ZOTERO_LIBRARY_TYPE` / `ZOTERO_LIBRARY_ID` | 默认个人库；群组库设 `group` 并提供群组 ID。 |
| `ZOTERO_ALLOW_WRITE_KEY` | 默认 `false`，允许有写权限的 Key 时设 `true`。 |
| `ZOTERO_ENABLE_CONTENT_READS` | 默认 `false`，启用扩展读取；写入模式也会启用这些读工具。 |
| `ZOTERO_ENABLE_WRITES` | 默认 `false`，开启真实的管理工具。 |
| `ZOTERO_STATE_DB` | 写入模式必填：独立私人 SQLite 文件的绝对路径，**不是 Zotero 桌面数据库**。 |
| `ZOTERO_ALLOW_PERMANENT_DELETE` | 默认 `false`，永久删除的独立许可；回收站/恢复不需要它。 |
| `PUBLIC_BASE_URL` | HTTPS 服务地址，Render 自动使用 `RENDER_EXTERNAL_URL`。 |

例如在已经配置好的私人持久卷上，创建由服务账号拥有、权限为 `0700` 的目录后设置：

```
ZOTERO_ALLOW_WRITE_KEY=true
ZOTERO_ENABLE_WRITES=true
ZOTERO_STATE_DB=/var/data/zotero-private/changes.sqlite3
ZOTERO_ALLOW_PERMANENT_DELETE=false
```

数据库文件采用 `0600` 权限。它包含私有元数据、修改前值和结果，不得提交 GitHub。默认保留七天；未解决的执行中/结果不确定记录不自动清除。

**免费/临时文件系统不能当成持久操作记录。** 重新部署可能丢失记录，应为正式写入部署可靠的持久存储。`:memory:` 仅供测试。记录丢失后先检查真实 Zotero 状态，不要盲目重复导入或合并。这次代码提交不会自动配置磁盘、产生付费资源或切换线上权限。

## ChatGPT 使用流程

升级部署后刷新自定义 MCP 工具定义，地址仍是 `https://你的域名/mcp`，认证 OAuth、DCR。无需更换已有密钥；如要使用写入，重新连接并明确授予 `zotero:write`，只读旧令牌不会自动升级。

1. `zotero_status`：检查连接与 `capabilities`。进程 Live 不等于真实库可用。
2. `plan_changes`：生成不可变修改计划，先看 before/after 差异。
3. 打开 `review_url`，输入 `MCP_LOGIN_PASSWORD` 后查看具体内容，并批准或撤销。匿名打开链接看不到私人计划内容。
4. `apply_changes`：携带原计划的 `plan_id` 和 `digest` 执行；核对状态及每一步记录。`partial` 或 `uncertain` 不能当成完成。

原五个读取工具保持兼容。新增六个内容读工具及六个管理工具，完整清单和 JSON 例子见 [英文 README](../README.md) 与 [操作示例](MANAGEMENT.md)。

## 安全限制不是“没有写功能”

根分类不能被改名、移走或删除。所有目标必须能通过归类或父子链证明处于允许范围。另有范围外归类的条目可在预览警告后改元数据，但不能通过本插件合并、移入回收站或永久删除。移出分类时必须保留至少一个范围内归类。

每份计划最多 20 个逻辑动作、100 个对象写入；按最多 50 对象分批提交。计划有效期 30 分钟；写入检查库版本及对象版本，不覆盖他人的并发修改。批量接口即使返回 HTTP 200 也要逐对象检查。网络结果不明时不自动重试。合并先迁移内容再删除副本（回收站），不是原子事务，也不做 PDF 去重或全库反向关系重写。

`plan_undo` 是新的复核计划，不会立即回滚。它拒绝后来又改过的对象、永久删除、部分/不确定执行结果；新建文献以移入回收站方式撤销，新建分类通过单独的空叶分类删除计划处理。

## 验证与发布

```
python -m unittest discover -s tests -v
```

测试使用可写、带版本控制的模拟 Zotero API，覆盖实际写请求、主人确认、权限、冲突、重复调用、部分失败、合并、撤销及私人记录存储。**模拟通过不等于已在你的真实库完成写入验收。** 真实写入应在另行授权的测试分类/测试库进行。

默认读模式不会受本次新增功能影响。正式公开发布前继续执行 `docs/RELEASING.md`，核查安全与依赖；不把一次代码提交宣传为独立安全审计或全协议认证。项目原始代码采用 MIT 许可证。
