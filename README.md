# astrbot_plugin_celebot

蔚蓝（Celeste）地图查询插件：内置官图与 Everest 合集地图库，支持按名称 / 别名 / 作者查询，授权用户可以在聊天中维护地图数据。

## 已收录

| 合集 | 数量 | 难度 | 图标 |
| --- | --- | --- | --- |
| 官图 | 10 | - | 暂无 |
| 草莓酱合集（SJ2021） | 116 | 有 | 难度级别图标 |
| 春游合集（SC2020） | 94 | 有 | 难度级别图标 |
| 冬游合集（WC2021） | 22 | 无 | 4 张逐图卡片，其余用合集图标 |

不收录 Crossover Collab。画游合集尚未导入，可用 `/合集添加` 新建后逐张添加。

## 指令

查询（所有人）：

- `/地图 <名称/别名/作者>`：模糊搜索，命中唯一结果时带图标展示详情；`/地图 #12` 按编号查看
- `/合集`：列出合集
- `/合集地图 <合集> [难度] [页码]`：列出合集内地图，如 `/合集地图 春游 专家 2`

编辑（AstrBot 管理员，或在插件配置 `editors` 中填写的用户 ID）：

- `/地图添加 <合集> <名称> [en=英文名] [alias=别名1,别名2] [diff=难度] [tag=标签1,标签2] [author=作者]`
- `/地图修改 <编号> <name|name_en|difficulty|author|collection> <新值>`（difficulty 填 `-` 清空）
- `/地图别名 <编号> <add|del|set> <别名1,别名2>`
- `/地图标签 <编号> <add|del|set> <标签1,标签2>`
- `/地图图标 <编号>`：附带图片，或回复一条带图消息
- `/地图删除 <编号>`
- `/合集添加 <key> <中文名> [英文名]`

## 数据字段

名称、英文名、别名、难度（可选，Beginner / Intermediate / Advanced / Expert / Grandmaster）、类型标签（可选）、图标、所属合集、作者。数据存放于 `data/plugin_data/astrbot_plugin_celebot/`（SQLite + 图标目录），首次启动时由 `seed/` 导入。

## 重新生成种子数据

```
python tools/import_collabs.py --mods "<Celeste>/Mods"
```

从 Mod 的 `Maps/`、`Dialog/`、`Graphics/Atlases/Gui/` 提取地图列表、名称、作者与图标，写入 `seed/`。只在数据库为空时才会导入种子。
