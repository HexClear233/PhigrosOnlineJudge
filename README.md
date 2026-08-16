# PhigrosOnlineJudge（Phigros OJ）

同步人本地赛事成绩统计工具。通过本地 OCR 自动识别 Phigros 结算截图，
计算 RKS 综合成绩，并生成 ACM 风格排行榜，供小型群赛／个人练习使用。

> **同人爱好工具，非官方**，不具有任何官方效力。成绩数据仅供娱乐参考。
> 一切游戏内容版权属于南京鸽游网络有限公司（Pigeon Games）。开源协议：MIT。

---

## 功能一览

| 能力 | 说明 |
|------|------|
| OCR 成绩识别 | 上传结算截图自动识别曲目/难度/ACC/分数等，输出 RKS（横屏 16:9 / 16:10 / 手机 2.2:1 三种布局） |
| 赛事管理 | 管理员创建 **课题曲赛事**（指定曲目+难度）与 **自选曲赛事**（Top N），支持封榜/揭榜 |
| 成绩提交 | 截图 OCR 上传（所有选手）+ 手动录入（管理员/组织者） |
| 审核平台 | 管理员/组织者查看截图与识别数据，修正后「通过/驳回」，榜单只计已通过成绩 |
| ACM 排行榜 | 课题曲矩阵（每曲高 RKS × 权重），自选曲取前 N 谱面求和排名；封榜隐藏成绩 |
| 排行榜导出 | Markdown / HTML / PNG（内容体现选手、曲目、难度、成绩；封榜掩码） |
| 自动揭榜 | 封榜可设置揭榜时间，到点访问自动解除封锁 |
| 反作弊 | 截图 SHA-256 精确去重 + 基础范围校验 |

## 快速开始

```bash
# 安装依赖（Python 3.10+，个人开发使用3.11.4）
pip install -r requirements.txt   # 见下方「依赖」

# 启动服务（自动建表并导入歌曲库）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 <http://127.0.0.1:8000>，**首个注册用户自动成为管理员**。

## 目录结构

```text
app/               # 网页服务器（FastAPI + SQLite + Jinja2）
  main.py          #  全部路由（页面 + 表单 + OCR 集成）
  models.py        #  SQLAlchemy 模型
  db.py            #  SQLite 引擎与会话（含轻量迁移）
  seed.py          #  从 diff_board CSV 导入歌曲库
  auth.py          #  bcrypt + Cookie 会话
  leaderboard.py   #  榜单聚合计算
  exporters.py     #  排行榜导出（Markdown/HTML/PNG）
  test_e2e.py      #  端到端测试
  templates/ static/   # 页面与样式
ocr/               # OCR 识别引擎（analyzer / settle_extract / rks / 解析）
ocr_templates/     # OCR 结算界面区域配置（regions_default.json）
diff_board/        # 各版本歌曲难度定数表（CSV，用于导入歌曲库）
Test_sample/       # 结算截图样本（OCR 校准与测试用）
Illu_Scrapy/       # 曲绘抓取工具链（从 Wiki 抓取 jacket 封面）
data/              # 运行时生成：SQLite 数据库、上传截图、日志（不入库）
项目计划书.md        # 项目计划书（需求/里程碑）
详细设计说明书.md     # 详细设计说明书（架构/数据模型/模块/接口）
```

## 依赖

核心运行依赖：`fastapi`、`uvicorn`、`sqlalchemy>=2.0`、`jinja2`、`bcrypt`、`paddleocr`、
PaddleOCR 依赖的 `paddlepaddle`、图像处理 `pillow`、HTTP 客户端 `httpx`（测试）。

> 建议在原 `requirements.txt` 基础上按实际环境整理；PaddleOCR 首次运行会下载识别模型。

## 测试

```bash
python -m ocr.test_analyzer        # RKS 公式 + OCR 接口
python -m ocr.test_settle_extract  # 结算截图全字段提取
python -m app.test_e2e             # 端到端：注册→建赛→提交→审核→榜单→封揭榜→导出
```

## 已知限制

- 竖屏 9:16 结算截图暂无布局样本，暂不支持；评级字段 OCR 置信度偏低，仅供展示参考
- 反作弊仅精确哈希去重，未做感知哈希；字段一致性校验为基础范围检查
- 本地单机设计，无需服务器

## 更多文档

- [`app/README.md`](app/README.md) —— 网页服务器细节与启动说明
- [`Illu_Scrapy/README.md`](Illu_Scrapy/README.md) —— 曲绘抓取工具使用说明
<!-- - [`项目计划书.md`](项目计划书.md) · [`详细设计说明书.md`](详细设计说明书.md) -->
