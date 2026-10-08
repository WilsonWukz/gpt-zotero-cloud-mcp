# Zotero Cloud MCP：中文说明

这是可独立部署的 Zotero 云端只读工具。每个人部署自己的实例、配置自己的凭证，不需要本机 Zotero 常开。**开源代码不等于公开私人文献库，更不等于匿名开放访问。** 本项目与 OpenAI、Zotero 没有官方隶属关系。

## 部署

在 Render 用本仓库或自己的 fork 创建 **Free Web Service**，或者使用 `render.yaml` Blueprint。私有仓库需要另行授权 Render 的 GitHub 集成读取这个仓库；连接 Render 管理插件不等于完成 GitHub 源码授权，不要因此把仓库改成公开。

构建命令：`pip install -r requirements.txt && python -m unittest discover -s tests -q`。
启动命令：`python -m zotero_cloud_mcp`。
健康检查：`/healthz`。只运行一个 worker，不需要数据库。

在 Render Environment 中配置：

| 变量 | 内容 |
| --- | --- |
| `APP_SECRET` | 至少 32 字符的随机签名密钥。 |
| `MCP_LOGIN_PASSWORD` | 另一个至少 32 字符的随机口令，仅在插件授权网页输入。 |
| `ZOTERO_API_KEY` | 专用只读 Zotero 密钥，不勾选任何个人库或群组写权限。 |
| `ZOTERO_COLLECTION_NAME` | 希望开放给插件读取的分类准确名称。 |

Blueprint 会在 Render 内生成前两个值。直接创建服务则通过 Environment 配置。不要在聊天里发送密钥，不要把三种密钥混用。Zotero 密钥在 https://www.zotero.org/settings/keys 创建。分类重名时以 `ZOTERO_COLLECTION_KEY` 指定准确分类；仅返回它及其子分类。个人库 ID 可以自动识别，群组库需要额外设置 `ZOTERO_LIBRARY_TYPE=group` 和 `ZOTERO_LIBRARY_ID`。

## 连接 ChatGPT

在账号可用的个人插件/开发者入口创建远程 MCP 连接：地址填 `https://你的服务域名/mcp`；认证选 **OAuth**；客户端注册选 **DCR（动态客户端注册）**，无需手工填写固定 client ID/secret。本版不采用 CIMD，也不能选择匿名访问。

授权页输入的是 Render 中的 **MCP_LOGIN_PASSWORD**，不是 Google/Zotero 密码、不是 APP_SECRET、更不是 ZOTERO_API_KEY。确认只读授权后，先调用 `zotero_status`，再调用 `list_collections` 和 `list_items`。

## 不要混淆三个验证层次

`/healthz` 返回 200：只说明服务进程正常。
`/readyz` 返回 configured：只说明配置存在。
认证后的 `zotero_status` 成功：才说明实际读到了 Zotero 云端。

`list_items` 返回 `next_start` 时，必须使用相同的 `snapshot_id` 继续读取，直到 `has_more=false`。版本变化、少页和权限错误都不能视为“齐全”。检查文献收录情况时，要给 `check_references` 一份明确目标清单，人工确认 ambiguous/conflict 项。

本版不读取 PDF、笔记、批注，不写入和删除。读取到条目不等于 PDF 已经同步。免费服务可能休眠，首次连接可能受冷启动影响。

## 后续开源

先保持仓库私有。公开前执行 `docs/RELEASING.md`：审核完整 Git 历史、许可证、依赖、安全报告入口及真实联调记录。源码提供 MIT 许可证，第一次公开前确认采用它。

这是实验性 v0.1.0，尚无独立安全审计。离线测试使用假数据，不等于已连通真实 Zotero 或已完成 ChatGPT 授权。单实例只服务一个所有者，不应直接改成多人共用网站。详细限制见 SECURITY.md。
