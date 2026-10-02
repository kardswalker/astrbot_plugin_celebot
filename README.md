# astrbot_plugin_celebot

蔚蓝（Celeste）地图查询插件：内置官图与 Everest 合集地图库，支持按名称 / 别名 / 作者查询，授权用户可以在聊天中维护地图数据。

## 已收录

| 合集 | 数量 | 难度 | 图标 |
| --- | --- | --- | --- |
| 官图 | 11 | - | 暂无 |
| 草莓酱合集（SJ2021） | 116 | 大难度 + 细分 | 111 张逐图贴纸（心门用难度图标） |
| 春游合集（SC2020） | 94 | 大难度 | 难度级别图标 |
| 冬游合集（WC2021） | 22 | 无 | 4 张逐图卡片，其余用合集图标 |
| 画游合集（CNY2024） | 26 | 大难度 + 细分 | 合集图标 |

不收录 Crossover Collab。官图名称取自游戏本体简中本地化。

## 难度

- **大难度**：Beginner / Intermediate / Advanced / Expert / Grandmaster（简写 `gm`，与 Expert 同级的大档，不是细分）。
- **细分难度**（可选）：`绿` / `黄` / `红`（大厅内的相对难度）、`爆`（cracked，Grandmaster 特有）、`+1` / `+2`…（超出该档，如 GM+1）。展示为 `Grandmaster 红`、`Grandmaster 爆`、`Grandmaster+1`。
- 草莓酱的细分难度来自 [Celeste Wiki](https://celeste.ink/wiki/Strawberry_Jam_Collab) 各大厅页面（`tools/data/sj2021_difficulty.json`），心门只有 Grandmaster 的 `+1`；画游的颜色来自 Mod 内的 `collabcreditstags`。

## 指令

查询（所有人）：

- `/地图 <名称/别名/作者>`：模糊搜索，命中唯一结果时带图标展示详情；`/地图 #12` 按编号查看
  - 容忍错别字和漏字：`crysal quary`、`水矿场`
  - 支持拼音与首字母：`shuijing`、`sjkc`
  - 多个关键词不限顺序，可带合集限定：`冬游 水晶`、`春游 crystal`
  - 标签、难度、细分难度可作过滤条件：`草莓酱 gm 红`、`草莓酱 爆`、`gm+1`、`画游 红 专家`（只写条件则列出全部符合的地图）
- `/合集`：列出合集
- `/合集地图 <合集> [难度] [细分难度] [页码]`：列出合集内地图，如 `/合集地图 春游 专家 2`、`/合集地图 草莓酱 gm 红`

编辑（AstrBot 管理员，或在插件配置 `editors` 中填写的用户 ID）：

- `/地图添加 <合集> <名称> [en=英文名] [alias=别名1,别名2] [diff=难度] [sub=细分难度] [tag=标签1,标签2] [author=作者]`
- `/地图修改 <编号> <name|name_en|difficulty|sub_difficulty|author|collection> <新值>`（也可用 名称/英文名/难度/细分/作者/合集；难度、细分填 `-` 清空）
- `/地图别名 <编号> <add|del|set> <别名1,别名2>`
- `/地图标签 <编号> <add|del|set> <标签1,标签2>`
- `/地图图标 <编号>`：附带图片，或回复一条带图消息
- `/地图删除 <编号>`
- `/合集添加 <key> <中文名> [英文名]`

## 数据字段

名称、英文名、别名、难度（可选）、细分难度（可选）、类型标签（可选）、图标、所属合集、作者。数据存放于 `data/plugin_data/astrbot_plugin_celebot/`（SQLite + 图标目录），首次启动时由 `seed/` 导入。

## 重新生成种子数据

```
python tools/import_collabs.py --mods "<Celeste>/Mods"
```

从 Mod 的 `Maps/`、`Dialog/`、`Graphics/Atlases/` 提取地图列表、名称、作者与图标（图标缩放到 256px），写入 `seed/`。只在数据库为空时才会导入种子。需要 Pillow 才会缩放图标（可选）。
