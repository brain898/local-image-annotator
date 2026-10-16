# 局部 · 图片批注与 Codex 局部修改工具

给 AI 生成的 PPT 页面、海报做局部修改：在网页上框出要改的地方、写清要求，点「去 Codex」交给 Codex 修改，结果自动回到网页。

和让图片模型重画整张图不同，这个工具**只把选区内的像素贴回原图，选区外像素与原图逐像素一致**，由程序检查「范围外变化像素数为 0」后才算完成。反复修改不会让其他区域累积噪点。

## 环境要求

- **Windows**：「去 Codex」通过 `codex://` 链接唤起 Codex 桌面版，目前只在 Windows 上实现和验证过。
- **Codex 桌面版**：图片修改由发送后的 Codex 聊天完成，使用它内置的图片编辑能力，不需要额外的图片 API Key。
- **Python 3.10+**。

## 安装

```powershell
git clone https://github.com/brain898/local-image-annotator.git
cd local-image-annotator
pip install -r requirements.txt
```

可选依赖：

| 功能 | 安装 | 说明 |
|---|---|---|
| 识别图中文字 | `pip install rapidocr onnxruntime` | 离线识别中文，只用包内自带模型，不联网下载 |
| 模型输出尺寸变化时的配准 | `pip install -r requirements-alignment.txt` | OpenCV，用于 Codex 执行任务时纠正轻微错位 |

## 启动

```powershell
python server.py --port 47831
```

或运行 `./start.ps1`。浏览器打开 <http://127.0.0.1:47831>。使用期间保持这个窗口开着，关闭即停止服务。服务只监听本机，图片、批注和历史版本都保存在本地 `data/` 目录，关机不会丢失。

## 完成一轮修改

顶栏的 4 步进度会高亮当前该做的一步。

1. **导入图片**：PNG / JPG。原图永久保留，另存无损 PNG 作为比较基准。
2. **标出要改的地方并写要求**，两种方式：
   - **框选**：在图上拖出矩形，在右侧写「要改成什么」。
   - **改图中文字**：点右栏「识别文字」，每行显示为「原文 → 改成…」，填写新内容后点「加入批注」，会自动生成选区和修改要求。
   - 只想改选区里的一部分时，用画布上方的「画笔」在红框内涂出允许修改的范围，没涂到的像素保持原样。
   - 可加一条「整图共同要求」，比如保留原字体和配色。
3. **去 Codex 点发送**：点「去 Codex」，会打开 Codex 并预填好指令，检查后**在 Codex 里点发送**（工具不会替你自动发送）。按钮不可用时，下方会写明原因。
4. **自动显示结果**：Codex 修改并通过像素检查后，网页自动载入新版本。可以前后滑动对比、恢复历史版本、导出原尺寸 PNG（不带批注框）。

快捷键：B 框选、M 画笔、E 擦除、H 平移，空格临时平移，滚轮缩放，Delete 删除选中的选区。

## 工作原理

```text
网页框选 → 保存任务（原图坐标、要求、编号预览）→ 打开 Codex 并预填
        → Codex 按专用 Skill 裁出参考图 → 内置图片编辑 → 检查对齐
        → worker.py 只把允许范围的像素贴回原图 → 校验范围外 0 像素变化
        → 保存新版本 → 网页自动更新
```

- 专用 Skill 在 `.agents/skills/local-image-edit/SKILL.md`，规定 Codex 的执行步骤。`worker.py` 提供 claim / prepare / normalize / compose / fail 等命令。
- 「参考范围」（画布上的蓝色虚线）是给模型看的周边内容，「允许修改范围」是红框或画笔涂出的部分，两者分开。参考边距可在选区卡片的「位置与参考范围」里调整。
- 模型返回的图片尺寸或位置有变化时，不会直接缩放贴回，只能通过有锚点证据的配准纠正；配准不可靠就判失败，可以调整选区或要求后重试。

数据目录：

- `data/store.sqlite`：任务、版本、图片和草稿。
- `data/images/`：原始上传文件和各版本 PNG。
- `data/tasks/任务ID/`：`task.json`、`批注.md`、编号预览、参考裁图、遮罩、模型输出与 `validation.json`。

## 限制

- 一次最多 20 个不重叠的选区；单个参考裁图超过 3840 像素时需要拆分选区。
- 平面底色上的单色文字效果最好。纹理、渐变、照片背景上的文字可能因无法可靠对齐而失败。
- 像素检查只能保证选区外不变，选区内改得好不好需要人看。执行任务的 Codex 会查看模型结果，有明显错位、接缝或文字放不下时判失败。
- 任务按顺序执行。修改期间如果你恢复了别的版本，旧任务的结果会保存为分支版本，不会覆盖你的新操作。

## 开发与测试

```powershell
python -m unittest discover -s tests -v
python tests/browser_states.py
python tests/browser_refresh.py
python tests/browser_requests.py
python tests/browser_ocr_mask.py
```

浏览器测试需要 `pip install playwright` 和本机 Chrome，使用临时数据和 47832 端口，不调用图片模型，也不会打开 Codex。测试报告写到 `evidence/`（不纳入版本库）。
