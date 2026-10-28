<div align="center">

# Linger

**为电脑中播放的任何音频生成实时英文字幕——文字保持稳定，便于阅读、选取与复制。**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6.svg)
![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB.svg)

[English](README.md) · **简体中文**

</div>

![Linger 深色主题](docs/ui-dark.png)

## 简介

Linger 采集电脑正在播放的音频（视频通话、在线课程、网络研讨会、录播视频等），并借助 [ElevenLabs Scribe](https://elevenlabs.io/speech-to-text) 实时语音识别模型，将其实时转写为英文文本。

大多数实时字幕工具只供浏览：文字滚动而过，随即消失。Linger 则面向**实际使用**而设计：已确定的文字不再改写；阅读或选取时，视图保持静止；在任意应用程序中按一次快捷键，即可复制任意内容，例如查询生词，或将句子粘贴到翻译工具中。

## 功能特性

- **系统音频采集**：通过 WASAPI 回环录制任意输出设备。无需虚拟声卡或额外驱动，无需修改其他应用程序的音频设置，也不会采集麦克风输入。
- **稳定的追加式文本**：已确定的文字只写入一次，此后不再修改。唯一会更新的是正在识别的最后一行；该行被选中时也会暂停更新。
- **按段落选取**：单击左侧时间栏可选中整段；按住 <kbd>Shift</kbd> 扩展选区，按住 <kbd>Ctrl</kbd> 添加或移除段落，也可拖动选取多段。同时支持跨段落的自由文本选取。
- **全局复制快捷键**：在任意应用程序中按 <kbd>Ctrl</kbd>+<kbd>`</kbd> 即可复制最近一句话；两秒内再次按下，可向前多包含一段。
- **便于翻译的输出**：多段内容合并为一行文本，因停顿而被切开的句子粘贴后仍是一个完整的句子。
- **自动重连**：网络或服务的短暂中断会被自动恢复，中断位置会在文本中标注。
- **自动保存**：每一段文字确定后，即刻写入带时间戳的文本文件。
- **自检**：开始使用前，检查音频采集是否正常、API 凭据是否有效。
- 支持**浅色与深色主题**、字号调节及窗口置顶。

## 系统要求

- Windows 10 或 Windows 11
- Python 3.13 或更高版本
- 已开通实时语音识别权限的 [ElevenLabs](https://elevenlabs.io) API 密钥

## 安装

```powershell
git clone https://github.com/BocchiJamSuki/linger.git
cd linger
.\setup.cmd
```

`setup.cmd` 会在 `.venv` 中创建虚拟环境，并安装 `requirements-dev.txt` 中列出的依赖。

## 配置

Linger 从 `ELEVENLABS_API_KEY` 变量读取 API 密钥，并按以下顺序查找：

1. 当前进程的环境变量
2. Windows 用户及系统环境变量（包括程序启动后才设置的值）
3. 项目目录中的 `.env`（或 `.env.txt`）文件：

   ```dotenv
   ELEVENLABS_API_KEY=your-api-key
   ```

## 使用方法

运行 **`Start Linger.cmd`** 启动 Linger，或在终端中执行：

```powershell
.\.venv\Scripts\python.exe main.py
```

1. 在工具栏中选择音频来源。**系统默认**会跟随 Windows 当前的默认输出设备；如果待转写的应用程序使用特定设备播放声音，请选择该设备。
2. 单击**开始**。此时才会连接 ElevenLabs；仅启动程序不会采集音频，也不会产生用量。
3. 单击**停止**结束会话。尚未确定的文字会被保留，并以斜体显示。

在重要场合使用前，建议通过 **⋯ → 运行自检** 确认音频采集正常、API 密钥有效。开启 Windows 的「勿扰」模式，可避免通知音被转写。

### 选取与复制文字

语音按自然停顿切分为段落，而非按句子切分。因此，一个句子（或之后对它的补充、更正）可能分布在多个段落中。Linger 提供以下几种方式，帮助您准确获取所需内容：

| 复制内容 | 操作方法 |
| --- | --- |
| 最近一句话 | 在任意应用程序中按 <kbd>Ctrl</kbd>+<kbd>`</kbd>；再次按下可向前多包含一段。 |
| 一个或多个完整段落 | 单击时间戳。<kbd>Shift</kbd>+单击选取连续范围，<kbd>Ctrl</kbd>+单击添加或移除段落，沿时间栏拖动可选取连续多段。 |
| 任意文字片段 | 在文字上拖动选取，可跨越段落。 |
| 全部文本 | **⋯ → 复制全部内容** |

存在选区或向上滚动后，视图将停止跟随新内容，并出现**回到最新**按钮，可随时返回实时位置。

### 键盘快捷键

| 快捷键 | 功能 |
| --- | --- |
| <kbd>Ctrl</kbd>+<kbd>`</kbd> | 复制最近一句话（全局有效） |
| <kbd>Ctrl</kbd>+<kbd>C</kbd> | 复制当前选区 |
| <kbd>Esc</kbd> | 取消选区 |
| <kbd>Ctrl</kbd>+<kbd>=</kbd> / <kbd>Ctrl</kbd>+<kbd>-</kbd> | 放大 / 缩小字号 |

全局快捷键可在 **⋯ → 全局复制快捷键** 中更改为 <kbd>Alt</kbd>+<kbd>`</kbd>、<kbd>F8</kbd> 或 <kbd>F9</kbd>，也可以关闭。

## 可靠性

- **断线重连**：连接中断后，Linger 会立即重连，随后逐步延长重试间隔。中断时正在识别的文字会被保留并标记为未确认，中断时长会记录在文本中。仅当遇到重试无法解决的错误（如密钥无效、额度耗尽）时，会话才会停止。
- **延迟控制**：若采集的音频积压超过 500 毫秒，或单次发送超过 1 秒，将丢弃过时音频并重新同步，避免延迟持续累积。
- **持续传输**：会话期间（包括静音时段）持续传输音频，确保下一句话的开头不会因重连等待而丢失。

## 数据与隐私

- 仅在会话进行期间向 ElevenLabs 发送音频。启动程序、选择设备以及自检中的音频检测环节均在本地完成。
- API 密钥仅保存在内存中，且只用于连接请求头，不会写入日志、文件或 URL。
- 转写文本以带时间戳的 UTF-8 文本文件形式保存在本地 `文档\Linger\` 目录中。除此之外，不存储或传输任何其他数据。
- 偏好设置（音频来源、主题、字号、快捷键及窗口位置）保存在 Windows 注册表 `HKEY_CURRENT_USER\Software\Linger` 下。

## 已知限制

- 仅支持 Windows，音频采集依赖 WASAPI 回环。
- 转写语言固定为英语。
- 用户界面目前仅提供简体中文。
- 识别准确率、延迟与费用由 ElevenLabs 服务决定。

## 开发

```text
linger/
├── audio.py        WASAPI 回环采集、单声道 PCM16 转换、100 ms 分包
├── scribe.py       凭据读取、连接参数、事件解析、错误分级
├── engine.py       工作线程：流式传输、断线重连、自检
├── transcript.py   段落模型、语句分组、自动保存
├── view.py         转写视图：追加式文本流、时间栏选取、跟随逻辑
├── hotkey.py       全局快捷键（Win32 RegisterHotKey）
├── window.py       主窗口
└── theme.py        配色、样式表、图标、标题栏着色
```

运行测试（网络访问仅限本机）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

重新生成 `docs/` 中的截图：

```powershell
.\.venv\Scripts\python.exe tools\preview.py
```

转写使用 `scribe_v2_realtime` 模型，基于语音活动检测提交段落（静音阈值 0.7 秒）。音频以 48 kHz 单声道 16 位 PCM 格式、每块 100 毫秒发送。

## 许可证

本项目基于 [MIT 许可证](LICENSE) 发布。
