# BMSON2PM

鼓王3谱面编辑与格式研究平台。项目使用 React、TypeScript、Canvas、Web Audio API 与 FastAPI，内部以版本化统一谱面模型保存数据，BMSON 与 BMS 作为交换格式。

## 本地启动

```bash
make install
make dev-api   # http://127.0.0.1:8000
make dev-web   # http://127.0.0.1:5173
```

PM3 音频生成需要 `ffmpeg`；离线 ROM 构建还需要 `mksquashfs` 与 `unsquashfs`。macOS
可通过 `brew install ffmpeg squashfs` 安装，其他系统安装对应的 `squashfs-tools` 包。

前端通过 Vite 将 `/api` 代理到 FastAPI。首次打开会加载内置示例谱面；左上角“导入”菜单会明确区分 BMSON、NoteList JSON 与传统 BMS，即使前两者都使用 `.json` 扩展名也不会混用解析器。右上角导出菜单可选择目标格式。

## 已实现

- 五难度项目与版本化统一数据模型
- BMSON 1.0/0.21 导入、1.0 导出与逐对象未知字段保留
- NoteList JSON 自动识别导入与独立导出（默认 TPB 48、samplelist/notelist 引用）
- NoteList 任意非负 `track` ID 识别；`0..5` 兼容六路输入，其余 ID 自动建立匿名 Track 并原号导出
- 旧版导入器保存在 `notelist_unmapped_notes` 中的可恢复音符会在项目加载时自动晋升为动态 Track
- 稳定 Sound Channel 引用，同名音频通道不会在往返时合并
- 动态 Track Canvas 时间轴：六路 PM3 正式输入之外可保留任意数量的匿名 Track
- 5Key、6Key 与更多按键 BMS 无损导入；未占用正式输入的通道默认建立匿名 Track
- Track 右键菜单支持全轨选择、新建并迁移、区间迁移、交换、合并与同 Tick 冲突去重
- Track 结构编辑完整接入撤销/重做；`Ctrl/Cmd+A` 选择谱面音符，`Ctrl/Cmd+Shift+A` 清选，`[`/`]` 切换 Track，`Ctrl/Cmd+Shift+N` 新建匿名 Track
- 长音符尾端拖动，Escape 可取消未提交的编辑事务
- 添加、删除、复制、粘贴、左右小鼓镜像、量化，以及增量命令式撤销和重做
- 单调时钟驱动的播放头、可拖拽定位、播放自动跟随与提前调度 Key 音试听
- 长音按 PM3 `hold_number` 或 1/16 回退密度的 1.5 倍速度连续触发；每一击使用独立音频实例并保留尾音叠加
- Key 音按资源 URL 预解码进 256 MB LRU 内存缓存，优先加载开头事件并使用低延迟 AudioContext
- PM3 `BG_xxx.ogg` 自动加载，按原 Track 16 Tick 起播；手动音乐仍可显式覆盖
- PM3 Key 音从游戏只读目录 `media/sound/note` 预加载并按音符采样引用真实播放
- 音频文件加载与波形渲染
- STOP-aware 播放换算、自动保存、冲突分类、统计和画布问题定位
- 传统 BMS 解析、Lane 映射、导出、扩展保留与兼容性报告
- 高精度 BMS 小节可复用源有理数位置并以稀疏多行重建，AGEHA 全谱可稳定往返
- BMS 目录导入会一并保存 WAV/OGG Key 音；声明扩展名与实际文件不同时可按同目录同名资源匹配
- 时间轴完整拦截触摸板横向滚动，并禁止横向越界手势触发浏览器前进/后退
- Track 时间轴显示小节、整拍与吸附细分网格，并支持 BMS 变长小节和低缩放密度控制
- PM3 FPGA 仿射加解密、SongList/原始谱面解析与 12 PPQN 无损映射
- 受信只读目录浏览、文本/十六进制查看和二进制/文本差异
- 下载 `.enc` 优先、内建 `.enccut` + 内置 cut table 回退的真实加载顺序
- 音乐、Key 音、预览音频和 SWF MV 引用识别，不修改游戏只读源目录
- PM3 原密文、cut data、明文 token、辅助 Track 与未知行原样保留
- PM3 完整歌曲音频生成、PowerOn 格式 `update.lst`、SquashFS 音频分包、`sound.rom`
  符号链接和 `lua_script.rom` MV 映射离线重建；跨格式谱面会自动补齐 Track 16 BGM
  启动事件，实际使用的自定义 Key 音会转换为 44.1 kHz PCM WAV 并写入 `sound.rom`
- 自定义 PM3 MV 上传与静态兼容性校验：使用 ID 20..99，接受 656x488、SWF 8/9、
  AS2 且含 `low/middle/high/full` 状态帧的 SWF；离线版本会累计重建 `ui.rom` 和
  `ui_mv6.rom`，并回读校验 SWF 内容及控制器加载链接
- PM3 MV 浏览器预览：内置离线 Ruffle/WASM，内置与项目私有 SWF 走受限同源接口；
  可切换 `low/middle/high/full`，预览补丁只作用于临时响应，不改动 OTA 原文件
- PM3 多曲离线版本构建：一次合并多个项目/难度的 SongList、谱面、音频与跨分包 ROM，
  输出可审计的 `verNNN` 目录和单一 `update.lst`；后续版本会自动带入并锁定历史歌曲，
  强制累计重建共享 ROM 和 SongList，避免新版本移除早期自制歌曲
- PM3 离线 OTA 只读审计：解析 `update.lst`、逐文件核对 MD5、回读谱面/SongList/ROM，
  并在内存中模拟多个 `verNNN` 的覆盖、删除与 SongList 变化

PM3 文件级 Track、`BG.wav` 虚拟路径、SquashFS 音频分包和辅助 Track 的实证分析见
[docs/pm3-chart-track-analysis.md](docs/pm3-chart-track-analysis.md)。
BMS Key 音资源关联与 AGEHA 实例验证见
[docs/bms-resource-analysis.md](docs/bms-resource-analysis.md)。
音频预解码、低延迟播放和 PM3 背景音乐加载策略见
[docs/audio-playback-analysis.md](docs/audio-playback-analysis.md)。
离线升级链、ROM 分包与静态验证边界见
[docs/pm3-offline-ota-analysis.md](docs/pm3-offline-ota-analysis.md)。
- PM3 明文谱面重建、`.enc` 加密、写后解密重解析与语义 Round-trip
- rewrite 更新包、`update.lst` MD5 清单、SongList 可选重建、ZIP 与 JSON 导出报告
- 多曲版本会从同一只读基线累计重建共享 ROM；界面自动带入并锁定上一版歌曲，不允许后续
  ZIP 遗漏早期自制歌曲
- 仅白名单发布目标、发布前备份、原子替换、故障自动恢复与手动回滚

## 动态 Track 设计

项目模型 1.3 将 Track 分为 `input`、`auxiliary` 与 `anonymous`。Lane 1–6 固定代表 PM3 的六路正式输入；原生 PM3 Track 6..15、17..23 会作为可见、可静音的非计分辅助 Track 导入，Track 16 则按难度保存为背景音乐启动事件。BMS 与 NoteList 的第 7 路及后续通道不会丢弃，而是以带原始 Track/channel 元数据的匿名 Track 进入工作区。5Key BMS 只占用五路正式输入，未使用的正式输入仍保留，便于后续编排。

轨道迁移、交换和合并作用于当前难度，可选择整个 Track 或闭区间 Tick 范围。迁移保留来源 Track；交换同时置换两侧区间内的音符；合并将来源写入目标，并在目标 Track 的操作区间内按 `pulse` 只保留一个音符，目标原有音符优先。清空的匿名 Track 会自动移除，正式六路永不删除。所有操作都以完整项目前后快照作为单条历史命令，因此 Track 本身、音符与 BMS channel 映射可以一起撤销和重做。

BMS 导出会沿用匿名 Track 的原始 channel；BMSON 与 NoteList 导出也会写出对应的动态 lane/track 索引。PM3 导出要求玩家输入归入 Lane 1–6；其余 Track 必须在右键菜单中显式映射为 PM3 辅助 Track 6..15、17..23。只要当前难度仍有任何事件位于待分类 Track，PM3 预检就会阻止导出，而不是静默跳过；辅助事件写回但不计入 `TotalNote`。

右上角显微镜按钮可打开 PM3 只读研究工作台。受信目录路径写在 `backend/config.toml`（已
gitignore），从 `config.example.toml` 复制模板后填入真实路径；包含 `game`（p3 镜像）、
`rewrite`（p4 镜像，含 OTA 更新）与可选 `mirror`（原 FTP 本地镜像）三个只读根。cut table
已内置，不再依赖外部 A36 ROM 镜像；以上路径可用 `BMSON2PM_PM3_GAME_ROOT`、
`BMSON2PM_PM3_REWRITE_ROOT`、`BMSON2PM_PM3_MIRROR_ROOT` 等同名环境变量覆盖，或用
`BMSON2PM_CONFIG` 指向另一个配置文件。API 不接受任意服务器绝对路径。

研究工作台的“OTA 审计”页可以审计 `backend/data/pm3-exports` 中的本地导出，也可以按
generation 扫描配置的 FTP 本地镜像。镜像审计会检查连续 version、累计 edition、路径、
payload 存在性和可选全量 MD5。它不会执行清单操作、挂载 ROM、连接 FTP/SSH 或修改
`machine.cfg`/`update.cfg`。

PM3 安全包默认写入 `backend/data/pm3-exports`，可用 `BMSON2PM_PM3_EXPORT_ROOT` 覆盖。
如需直接发布到测试机的 rewrite 根目录，显式配置 `BMSON2PM_PM3_DEPLOY_ROOT`；未配置时界面只提供安全导出目录。

## 测试

```bash
make test
```
