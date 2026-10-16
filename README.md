# workbuddy/trae模型一键导入助手

Windows 桌面工具，从 OpenAI 兼容或 Anthropic 网关获取模型列表，选择模型后批量导入 WorkBuddy、Trae 和 TraeWork CN。只需填写 Base URL 与 API Key；工具不会读取其他应用保存的连接或密钥。

## 下载与运行

在 [Releases](https://github.com/Gwex323/gateway-model-assistant/releases/latest) 下载 Windows 便携版 ZIP，解压完整文件夹，运行 `网关模型助手.exe`。请保留同目录的 `_internal` 文件夹。需要 Windows 10/11；程序未数字签名，请勿为了运行它关闭系统安全防护。

从源码运行需要 Windows、Python 3.10+ 和 Tkinter：

```powershell
python app.py
```

也可以双击 `启动网关模型助手.bat`；若窗口未出现，可运行 `诊断启动.bat` 查看错误。

## 支持范围

| 目标应用 | OpenAI 兼容协议 | Anthropic 原生协议 | 写入方式 |
| --- | --- | --- | --- |
| WorkBuddy | 支持 | 不支持 | 模型配置文件 |
| Trae | 支持 | 不支持 | 本地模型列表缓存 |
| TraeWork CN | 支持 | 支持 | 客户端自身的模型服务 |

OpenAI 模式可同时选择多个目标；Anthropic 原生模式仅支持 TraeWork CN。普通 Trae 的本地缓存可能被客户端更新或刷新覆盖。TraeWork CN 的内部模型服务不是公开稳定接口，客户端升级后可能需要重新适配。

## 使用方法

1. 选择接口协议，填写 Base URL 和 API Key，点击“获取模型”。
2. 搜索并选择需要导入的模型。搜索框右侧的“全选”只选择当前搜索结果，不会取消其他已选模型。

   “导入状态”优先根据上游模型类型、支持的接口及输入/输出模态判断，缺少可靠声明时才匹配已知模型名称。已识别的图片/视频/3D 生成、音频专用、Embedding 和重排序模型标记为“不支持导入到目标应用”，不可勾选，全选也会跳过；视觉理解对话模型可正常选择。信息不足的别名标记为“类型待确认”并保持可选，不仅凭 image、video、3d 或 vision 等字样禁用，实际调用兼容性仍需核实。元数据互相冲突时，明确的不支持声明优先。
3. 选择导入目标，检查模型能力和自动识别的最大输入、最大输出 Token。图片输入默认自动识别；WorkBuddy 新建或覆盖模型时工具调用默认开启，手动选择“关闭”优先。
4. 对同一连接下的已有模型选择“保留已有”或“覆盖已有”。保留不会修改旧配置；其他连接下的同名模型不会被改绑。Trae 系列可编辑模型显示名称，WorkBuddy 不使用此字段。
5. 点击“预览并导入”，核对变更，并按提示完全退出目标应用（包括托盘和后台进程）后确认。重新打开目标应用，检查模型是否出现。

获取模型时会请求网关的模型列表及必要的公开规格页面，不会自动发起聊天或付费推理。Token 上限只有在上游元数据或可靠官方规格可用时才填写；“未识别”不表示模型不可用。真实模型调用需自行验证，可能产生网关费用。

WorkBuddy 保存的聊天地址会在规范化 Base URL 后追加 `/chat/completions`；Trae 系列按各自协议保存地址。Anthropic 的鉴权方式可选择自动、Bearer 或 `x-api-key`。具体适配范围与限制见 [ADAPTER_NOTES.md](ADAPTER_NOTES.md)。

## 安全与备份

- 工具不会把 API Key 保存到自己的配置或日志；导入后密钥会保存在目标应用的原生配置中。不要把客户端配置、备份或密钥上传到公开仓库。
- 写入 WorkBuddy 或普通 Trae 前会创建 Windows DPAPI 加密备份。备份只能由创建它的 Windows 账户解密；应用界面不提供恢复按钮。TraeWork CN 的服务端持久化模型不通过本地备份恢复。
- 公网地址仅接受 HTTPS；私有网络 HTTP 地址需要明确确认风险。不跟随重定向，不跳过 TLS 证书校验。
- 工具不会强制结束目标应用，也不会在未知或损坏的配置结构上盲目写入。

## 开发与验证

运行离线测试（使用虚构密钥和临时配置，不写真实客户端模型）：

```powershell
python -m unittest discover -s tests -q
```

构建便携版：创建 `.build-venv`，安装 `requirements-build.txt`，再执行 `./build_windows.ps1`。应用本身运行时不需要安装第三方 Python 包。第三方目标图标的归属见 [assets/TARGET_ICONS.md](assets/TARGET_ICONS.md)。
