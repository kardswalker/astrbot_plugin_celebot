from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.star.filter.command import GreedyStr

from .celebot.db import DIFFICULTIES, MapDB, MapEntry, normalize_difficulty

SEED_DIR = Path(__file__).parent / "seed"
PAGE_SIZE = 15


def _split_list(text: str) -> list[str]:
    return [x.strip() for x in text.replace("，", ",").replace("、", ",").split(",") if x.strip()]


@register("astrbot_plugin_celebot", "kardswalker", "Celeste 地图查询机器人：官图与合集地图库", "0.1.0")
class CeleBot(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.db: MapDB | None = None

    async def initialize(self):
        self.db = MapDB(StarTools.get_data_dir())
        n = self.db.seed_if_empty(SEED_DIR)
        if n:
            logger.info(f"[celebot] 已导入种子地图数据 {n} 张")

    async def terminate(self):
        if self.db:
            self.db.close()

    # ---------- 工具 ----------
    def _is_editor(self, event: AstrMessageEvent) -> bool:
        return event.is_admin() or str(event.get_sender_id()) in {str(x) for x in self.config.get("editors", [])}

    def _collection_name(self, key: str) -> str:
        c = self.db.find_collection(key)  # type: ignore[union-attr]
        return c.name if c else key

    def _format(self, m: MapEntry) -> str:
        title = m.name if not m.name_en or m.name_en == m.name else f"{m.name}（{m.name_en}）"
        lines = [f"#{m.id} {title}", f"合集：{self._collection_name(m.collection)}"]
        if m.difficulty:
            lines.append(f"难度：{m.difficulty}")
        if m.author:
            lines.append(f"作者：{m.author}")
        if m.tags:
            lines.append("标签：" + "、".join(m.tags))
        if m.aliases:
            lines.append("别名：" + "、".join(m.aliases))
        return "\n".join(lines)

    def _one_line(self, m: MapEntry) -> str:
        extra = f" [{m.difficulty}]" if m.difficulty else ""
        en = f" / {m.name_en}" if m.name_en and m.name_en != m.name else ""
        return f"#{m.id} {m.name}{en}{extra}"

    def _detail_result(self, event: AstrMessageEvent, m: MapEntry):
        chain = []
        icon = self.db.icon_path(m)  # type: ignore[union-attr]
        if icon:
            chain.append(Image.fromFileSystem(str(icon)))
        chain.append(Plain(self._format(m)))
        return event.chain_result(chain)

    # ---------- 查询 ----------
    @filter.command("地图", alias={"map"})
    async def search_map(self, event: AstrMessageEvent, query: GreedyStr):
        """查询地图。用法：/地图 <名称/别名/作者>，或 /地图 #编号"""
        query = query.strip()
        if query.startswith("#") and query[1:].isdigit():
            m = self.db.get(int(query[1:]))
            hits = [m] if m else []
        else:
            hits = self.db.search(query)
        if not hits:
            yield event.plain_result(f"没有找到「{query}」相关的地图。")
        elif len(hits) == 1 or (hits[0].names() and query.lower() in (n.lower() for n in hits[0].names())):
            yield self._detail_result(event, hits[0])
        else:
            lines = [f"找到 {len(hits)} 张相关地图（/地图 #编号 查看详情）："]
            lines += [self._one_line(m) for m in hits]
            yield event.plain_result("\n".join(lines))

    @filter.command("合集")
    async def list_collections(self, event: AstrMessageEvent):
        """列出所有已收录的合集"""
        lines = ["已收录合集（/合集地图 <合集> [难度] [页码]）："]
        for c in self.db.collections():
            n = len(self.db.list_maps(c.key))
            lines.append(f"{c.name}（{c.name_en}）· {n} 张")
        yield event.plain_result("\n".join(lines))

    @filter.command("合集地图")
    async def list_collection_maps(self, event: AstrMessageEvent, args: GreedyStr):
        """列出合集内的地图。用法：/合集地图 <合集> [难度] [页码]"""
        tokens = args.split()
        if not tokens:
            yield event.plain_result("用法：/合集地图 <合集> [难度] [页码]")
            return
        coll = self.db.find_collection(tokens[0])
        if not coll:
            yield event.plain_result(f"合集「{tokens[0]}」不存在，可用 /合集 查看。")
            return
        page, difficulty = 1, None
        for t in tokens[1:]:
            if t.isdigit():
                page = max(1, int(t))
                continue
            try:
                difficulty = normalize_difficulty(t)
            except ValueError as e:
                yield event.plain_result(str(e))
                return
        maps = self.db.list_maps(coll.key, difficulty)
        pages = max(1, -(-len(maps) // PAGE_SIZE))
        page = min(page, pages)
        chunk = maps[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        head = f"{coll.name}" + (f" · {difficulty}" if difficulty else "") + f"（第 {page}/{pages} 页，共 {len(maps)} 张）"
        yield event.plain_result("\n".join([head] + [self._one_line(m) for m in chunk]))

    # ---------- 编辑（需授权）----------
    async def _deny(self, event: AstrMessageEvent):
        return event.plain_result("你没有编辑地图库的权限。请联系管理员把你的 ID 加入插件配置的 editors。")

    @filter.command("地图添加")
    async def add_map(self, event: AstrMessageEvent, args: GreedyStr):
        """添加地图。用法：/地图添加 <合集> <名称> [en=英文名] [alias=别名1,别名2] [diff=难度] [tag=标签1,标签2] [author=作者]"""
        if not self._is_editor(event):
            yield await self._deny(event)
            return
        tokens = args.split()
        if len(tokens) < 2:
            yield event.plain_result("用法：/地图添加 <合集> <名称> [en=..] [alias=a,b] [diff=难度] [tag=a,b] [author=..]")
            return
        name_parts, opts = [], {}
        for t in tokens[1:]:
            k, sep, v = t.partition("=")
            if sep and k in ("en", "alias", "diff", "tag", "author"):
                opts[k] = v
            elif not opts:
                name_parts.append(t)
            else:
                yield event.plain_result(f"无法解析「{t}」，名称中的空格请放在选项之前。")
                return
        try:
            m = self.db.add_map(
                " ".join(name_parts), tokens[0], name_en=opts.get("en", ""), aliases=_split_list(opts.get("alias", "")),
                difficulty=opts.get("diff"), tags=_split_list(opts.get("tag", "")), author=opts.get("author", ""),
                user=str(event.get_sender_id()),
            )
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        yield event.plain_result("已添加：\n" + self._format(m) + "\n（用 /地图图标 " + str(m.id) + " 附带图片可设置图标）")

    @filter.command("地图修改")
    async def edit_map(self, event: AstrMessageEvent, map_id: int, field: str, value: GreedyStr):
        """修改字段。用法：/地图修改 <编号> <name|name_en|difficulty|author|collection> <新值>（difficulty 填 - 清空）"""
        if not self._is_editor(event):
            yield await self._deny(event)
            return
        try:
            m = self.db.edit(map_id, field, value, str(event.get_sender_id()))
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        yield event.plain_result("已修改：\n" + self._format(m))

    async def _list_edit(self, event: AstrMessageEvent, kind: str, map_id: int, op: str, items: str):
        if not self._is_editor(event):
            return await self._deny(event)
        if op not in ("add", "del", "set"):
            return event.plain_result("操作应为 add / del / set，例如：/地图别名 12 add 别名1,别名2")
        fn = self.db.edit_aliases if kind == "别名" else self.db.edit_tags
        try:
            m = fn(map_id, op, _split_list(items), str(event.get_sender_id()))
        except ValueError as e:
            return event.plain_result(str(e))
        return event.plain_result(f"已更新{kind}：\n" + self._format(m))

    @filter.command("地图别名")
    async def edit_alias(self, event: AstrMessageEvent, map_id: int, op: str, items: GreedyStr):
        """管理别名。用法：/地图别名 <编号> <add|del|set> <别名1,别名2>"""
        yield await self._list_edit(event, "别名", map_id, op, items)

    @filter.command("地图标签")
    async def edit_tag(self, event: AstrMessageEvent, map_id: int, op: str, items: GreedyStr):
        """管理类型标签。用法：/地图标签 <编号> <add|del|set> <标签1,标签2>"""
        yield await self._list_edit(event, "标签", map_id, op, items)

    @filter.command("地图图标")
    async def set_icon(self, event: AstrMessageEvent, map_id: int):
        """设置图标。用法：发送 /地图图标 <编号> 并附带一张图片（或回复一条带图消息）"""
        if not self._is_editor(event):
            yield await self._deny(event)
            return
        img = None
        for comp in event.get_messages():
            if isinstance(comp, Image):
                img = comp
            elif isinstance(comp, Reply) and not img:
                img = next((c for c in (comp.chain or []) if isinstance(c, Image)), None)
        if not img:
            yield event.plain_result("请在指令消息中附带图片，或回复一条带图片的消息。")
            return
        try:
            path = await img.convert_to_file_path()
            m = self.db.set_icon_from_file(map_id, Path(path), str(event.get_sender_id()))
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        yield self._detail_result(event, m)

    @filter.command("地图删除")
    async def delete_map(self, event: AstrMessageEvent, map_id: int):
        """删除地图。用法：/地图删除 <编号>"""
        if not self._is_editor(event):
            yield await self._deny(event)
            return
        try:
            m = self.db.delete(map_id)
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        yield event.plain_result(f"已删除 #{m.id} {m.name}")

    @filter.command("合集添加")
    async def add_collection(self, event: AstrMessageEvent, key: str, name: str, name_en: str = ""):
        """新增合集。用法：/合集添加 <key> <中文名> [英文名]（key 为字母数字，如 gallery）"""
        if not self._is_editor(event):
            yield await self._deny(event)
            return
        try:
            c = self.db.add_collection(key, name, name_en)
        except ValueError as e:
            yield event.plain_result(str(e))
            return
        yield event.plain_result(f"已新增合集：{c.name}（key={c.key}）")
