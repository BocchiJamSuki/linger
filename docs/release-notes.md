## Linger 1.0.1

Real-time captions for any audio playing on your computer, designed to stay still long enough to read, select, and copy.

### Download

Download `Linger-1.0.1-win64.zip` below, extract it anywhere, and run `Linger.exe`. On first launch, open **⋯ → API key** and paste your ElevenLabs API key (it starts with `sk_`).

The executable is not code-signed, so Windows SmartScreen may show a warning; choose **More info → Run anyway**.

### Fixed in 1.0.1

- A sentence that had just been finalized could appear a second time after a reconnection. Each commit's word timings now determine exactly which audio is kept for replay, so finalized speech is never transcribed twice.

### Highlights

- Captures any output device through WASAPI loopback; no virtual audio cable required
- Append-only transcript that never moves while you read or select
- Segment selection from the timestamp gutter, and a system-wide hotkey (<kbd>Ctrl</kbd>+<kbd>`</kbd>) that copies the latest utterance
- Audio is buffered while the network is down and replayed after reconnecting
- Follows the Windows default output device when it changes
- English, Chinese, Japanese, Korean, Spanish, French, and German transcription, or automatic detection
- Interface in English and Simplified Chinese; light and dark themes
- API key stored in Windows Credential Manager

---

## Linger 1.0.1（简体中文）

为电脑中播放的任何音频生成实时字幕，文字保持稳定，便于阅读、选取与复制。

下载下方的 `Linger-1.0.1-win64.zip`，解压后运行 `Linger.exe`。首次启动时，在 **⋯ → API 密钥** 中粘贴您的 ElevenLabs API 密钥（以 `sk_` 开头）。

可执行文件未经代码签名，Windows SmartScreen 可能给出警告，请选择**更多信息 → 仍要运行**。

**1.0.1 修复**：刚确定的句子在重连后可能重复出现一次。现在依据每句话的词级时间戳，精确决定哪些音频需要保留以备补发，已确定的内容不会被再次转写。
