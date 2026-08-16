# Phigros OJ MVP（网页与服务器）

基于现有 `ocr/` 模块的赛事网页 MVP：FastAPI + SQLite + Jinja2，本地运行。

## 启动

```bash
# 首次或每次启动前无需手动初始化：启动时会自动建表并导入歌曲库
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 <http://127.0.0.1:8000>。

## 功能

- 用户注册/登录/退出（服务端会话 Cookie；**首个注册用户自动成为管理员**）
- 赛事管理（管理员）：课题曲赛事（指定曲目+难度）与自选曲赛事（Top N）
- 成绩提交：
  - **手动录入：仅管理员或赛事组织者（创建者）可用**，普通选手不可直接录入
  - OCR 上传（所有选手）：上传结算截图 → 自动识别 → 选手确认页**只展示
    曲目/难度/ACC**（分数等细节不展示）→ 提交后进入**待审核**队列；
    同一截图哈希不可重复使用
- **审核平台**（管理员 / 赛事组织者）：
  - 全局入口 `/review`（管理员）；赛事内入口 `/contests/{id}/review`（管理员/组织者）
  - 查看截图与 OCR 识别数据，可修正 ACC/曲目/难度/分数后**通过**（计入榜单）
    或**驳回**（附审核意见）；榜单只统计已通过的成绩
- 排行榜：
  - 课题曲：ACM 风格矩阵（每曲最高 RKS × 权重，总分排名）
  - 自选曲：每名选手取全部谱面最高 RKS 的前 N 个求和排名
- 排行榜导出（Markdown / HTML / PNG 图片）：榜单页提供下载按钮；
  接口 `/contests/{id}/leaderboard/export?format=markdown|html|image`
  - 内容完整体现选手、曲目、难度与成绩：课题曲每曲一列表头带曲绘+难度+定数；
    自选曲单元格显示「曲名 / 难度 / RKS」并带曲绘背景，复刻站点榜单样式
  - 封榜时非管理员导出的得分为 `?` 掩码，管理员可见真实成绩
- 封榜/揭榜：封榜后非管理员只能看到 `?`；支持**设置自动揭榜时间**，到点访问榜单/赛事详情即自动解除封榜并恢复成绩显示（未设时间则需手动揭榜）
- 课题曲选曲界面支持按曲名搜索过滤

## 目录

```text
app/
  main.py         # 全部路由（页面 + 表单 + OCR 集成）
  models.py       # SQLAlchemy 模型
  db.py           # SQLite 引擎与会话
  seed.py         # 从 diff_board CSV 导入歌曲库
  auth.py         # bcrypt + Cookie 会话
  leaderboard.py  # 榜单聚合计算
  exporters.py    # 排行榜导出（Markdown/HTML/PNG）
  test_e2e.py     # 端到端测试
  templates/      # Jinja2 页面
  static/         # 样式
data/             # SQLite 数据库与上传截图（运行时生成）
```

## 测试

```bash
python -m ocr.test_analyzer     # RKS 公式 + OCR 接口
python -m ocr.test_settle_extract  # 11 张样本全字段提取
python -m app.test_e2e          # 起服务跑通 注册→建赛→手动/OCR 提交→审核→榜单→封揭榜
```

## 已知限制（MVP）

- 仅横屏结算截图（16:9 / 16:10 / 手机 2.2:1）；竖屏待样本补充
- 评级字段 OCR 置信度偏低，仅展示参考
- 图片哈希精确去重（未做感知哈希）；同一用户可对同一谱面多次提交，榜单取最高 RKS
