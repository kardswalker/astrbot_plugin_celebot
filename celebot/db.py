"""地图库：SQLite 存储 + 模糊搜索。不依赖 AstrBot，可单独测试。"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

DIFFICULTIES = ["Beginner", "Intermediate", "Advanced", "Expert", "Grandmaster"]
# 常见中文/缩写写法 → 规范难度名
DIFFICULTY_ALIASES = {
    "初级": "Beginner", "入门": "Beginner", "beginner": "Beginner", "b": "Beginner",
    "中级": "Intermediate", "intermediate": "Intermediate", "i": "Intermediate",
    "高级": "Advanced", "advanced": "Advanced", "a": "Advanced",
    "专家": "Expert", "expert": "Expert", "e": "Expert",
    "大师": "Grandmaster", "宗师": "Grandmaster", "grandmaster": "Grandmaster", "gm": "Grandmaster",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_en TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_en TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '[]',
    difficulty TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    icon TEXT,
    collection TEXT NOT NULL REFERENCES collections(key),
    author TEXT NOT NULL DEFAULT '',
    sid TEXT UNIQUE,
    updated_by TEXT NOT NULL DEFAULT 'seed',
    updated_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_maps_collection ON maps(collection);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def norm(s: str) -> str:
    """搜索归一化：小写并去掉空白与标点，保留字母数字与汉字。"""
    return re.sub(r"[\W_]+", "", s.lower())


@dataclass
class Collection:
    key: str
    name: str
    name_en: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class MapEntry:
    id: int
    name: str
    name_en: str
    aliases: list[str]
    difficulty: str | None
    tags: list[str]
    icon: str | None
    collection: str
    author: str
    sid: str | None
    updated_by: str
    updated_at: int

    def names(self) -> list[str]:
        return [self.name, self.name_en, *self.aliases]


def _row_to_map(r: sqlite3.Row) -> MapEntry:
    return MapEntry(
        id=r["id"], name=r["name"], name_en=r["name_en"],
        aliases=json.loads(r["aliases"]), difficulty=r["difficulty"],
        tags=json.loads(r["tags"]), icon=r["icon"], collection=r["collection"],
        author=r["author"], sid=r["sid"], updated_by=r["updated_by"], updated_at=r["updated_at"],
    )


def normalize_difficulty(value: str | None) -> str | None:
    if value is None or not value.strip() or value.strip() in ("-", "无", "none"):
        return None
    v = value.strip()
    d = DIFFICULTY_ALIASES.get(v.lower()) or DIFFICULTY_ALIASES.get(v)
    if d:
        return d
    raise ValueError(f"未知难度「{v}」，可用：" + " / ".join(DIFFICULTIES))


class MapDB:
    # 允许通过 edit 修改的字段
    EDITABLE = ("name", "name_en", "difficulty", "author", "collection")

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.icon_dir = self.data_dir / "icons"
        self.icon_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.data_dir / "maps.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- 种子数据 ----------
    def seed_if_empty(self, seed_dir: Path) -> int:
        """数据库为空时导入 seed/maps.json 与 seed/icons。返回导入地图数。"""
        if self.conn.execute("SELECT 1 FROM maps LIMIT 1").fetchone():
            return 0
        f = Path(seed_dir) / "maps.json"
        if not f.exists():
            return 0
        data = json.loads(f.read_text(encoding="utf-8"))
        src_icons = Path(seed_dir) / "icons"
        if src_icons.exists():
            shutil.copytree(src_icons, self.icon_dir, dirs_exist_ok=True)
        with self.conn:
            for c in data["collections"]:
                self.conn.execute(
                    "INSERT OR IGNORE INTO collections VALUES (?,?,?,?)",
                    (c["key"], c["name"], c.get("name_en", ""), json.dumps(c.get("aliases", []), ensure_ascii=False)),
                )
            for m in data["maps"]:
                self.conn.execute(
                    "INSERT OR IGNORE INTO maps (name,name_en,aliases,difficulty,tags,icon,collection,author,sid,updated_by,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (m["name"], m.get("name_en", ""), json.dumps(m.get("aliases", []), ensure_ascii=False),
                     m.get("difficulty"), json.dumps(m.get("tags", []), ensure_ascii=False), m.get("icon"),
                     m["collection"], m.get("author", ""), m.get("sid"), "seed", int(time.time())),
                )
        return len(data["maps"])

    # ---------- 合集 ----------
    def collections(self) -> list[Collection]:
        rows = self.conn.execute("SELECT * FROM collections ORDER BY rowid").fetchall()
        return [Collection(r["key"], r["name"], r["name_en"], json.loads(r["aliases"])) for r in rows]

    def find_collection(self, text: str) -> Collection | None:
        t = norm(text)
        for c in self.collections():
            if t in {norm(x) for x in (c.key, c.name, c.name_en, *c.aliases)}:
                return c
        return None

    def add_collection(self, key: str, name: str, name_en: str = "", aliases: list[str] | None = None) -> Collection:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", key):
            raise ValueError("合集 key 只能包含字母、数字、下划线和短横线")
        if self.find_collection(key) or self.find_collection(name):
            raise ValueError("合集已存在")
        with self.conn:
            self.conn.execute("INSERT INTO collections VALUES (?,?,?,?)",
                              (key, name, name_en, json.dumps(aliases or [], ensure_ascii=False)))
        return Collection(key, name, name_en, aliases or [])

    # ---------- 地图 ----------
    def get(self, map_id: int) -> MapEntry | None:
        r = self.conn.execute("SELECT * FROM maps WHERE id=?", (map_id,)).fetchone()
        return _row_to_map(r) if r else None

    def list_maps(self, collection: str | None = None, difficulty: str | None = None) -> list[MapEntry]:
        sql, args = "SELECT * FROM maps WHERE 1=1", []
        if collection:
            sql += " AND collection=?"; args.append(collection)
        if difficulty:
            sql += " AND difficulty=?"; args.append(difficulty)
        return [_row_to_map(r) for r in self.conn.execute(sql + " ORDER BY id", args)]

    def search(self, query: str, collection: str | None = None, limit: int = 10) -> list[MapEntry]:
        """按名称/别名/作者模糊搜索，返回按匹配度降序的结果。"""
        q = norm(query)
        if not q:
            return []
        scored: list[tuple[float, MapEntry]] = []
        for m in self.list_maps(collection):
            best = 0.0
            for n in m.names():
                nn = norm(n)
                if not nn:
                    continue
                if nn == q:
                    best = max(best, 1.0)
                elif nn.startswith(q):
                    best = max(best, 0.9)
                elif q in nn:
                    best = max(best, 0.8)
                elif len(q) >= 3:
                    r = difflib.SequenceMatcher(None, q, nn).ratio()
                    if r >= 0.6:
                        best = max(best, r * 0.7)
            if best == 0 and len(q) >= 2 and q in norm(m.author):
                best = 0.5
            if best:
                scored.append((best, m))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [m for _, m in scored[:limit]]

    def add_map(self, name: str, collection: str, *, name_en: str = "", aliases: list[str] | None = None,
                difficulty: str | None = None, tags: list[str] | None = None, author: str = "",
                icon: str | None = None, user: str = "") -> MapEntry:
        if not name.strip():
            raise ValueError("地图名称不能为空")
        coll = self.find_collection(collection)
        if not coll:
            raise ValueError(f"合集「{collection}」不存在")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO maps (name,name_en,aliases,difficulty,tags,icon,collection,author,updated_by,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name.strip(), name_en, json.dumps(aliases or [], ensure_ascii=False), normalize_difficulty(difficulty),
                 json.dumps(tags or [], ensure_ascii=False), icon, coll.key, author, user, int(time.time())),
            )
        return self.get(cur.lastrowid)  # type: ignore[arg-type]

    def _touch(self, map_id: int, user: str, **fields) -> MapEntry:
        if not self.get(map_id):
            raise ValueError(f"地图 #{map_id} 不存在")
        fields.update(updated_by=user, updated_at=int(time.time()))
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.conn:
            self.conn.execute(f"UPDATE maps SET {cols} WHERE id=?", [*fields.values(), map_id])
        return self.get(map_id)  # type: ignore[return-value]

    def edit(self, map_id: int, field_name: str, value: str, user: str = "") -> MapEntry:
        if field_name not in self.EDITABLE:
            raise ValueError("可修改字段：" + " / ".join(self.EDITABLE) + "（别名、标签、图标请用专用指令）")
        if field_name == "difficulty":
            value = normalize_difficulty(value)  # type: ignore[assignment]
        elif field_name == "collection":
            coll = self.find_collection(value)
            if not coll:
                raise ValueError(f"合集「{value}」不存在")
            value = coll.key
        elif field_name == "name" and not value.strip():
            raise ValueError("地图名称不能为空")
        return self._touch(map_id, user, **{field_name: value})

    def _list_edit(self, map_id: int, column: str, op: str, items: list[str], user: str) -> MapEntry:
        m = self.get(map_id)
        if not m:
            raise ValueError(f"地图 #{map_id} 不存在")
        cur: list[str] = list(getattr(m, column))
        if op == "add":
            cur += [i for i in items if i not in cur]
        elif op == "del":
            cur = [i for i in cur if i not in items]
        elif op == "set":
            cur = items
        return self._touch(map_id, user, **{column: json.dumps(cur, ensure_ascii=False)})

    def edit_aliases(self, map_id: int, op: str, items: list[str], user: str = "") -> MapEntry:
        return self._list_edit(map_id, "aliases", op, items, user)

    def edit_tags(self, map_id: int, op: str, items: list[str], user: str = "") -> MapEntry:
        return self._list_edit(map_id, "tags", op, items, user)

    def set_icon_from_file(self, map_id: int, src: Path, user: str = "") -> MapEntry:
        if not self.get(map_id):
            raise ValueError(f"地图 #{map_id} 不存在")
        ext = Path(src).suffix.lower() or ".png"
        rel = f"custom/{map_id}{ext}"
        dest = self.icon_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return self._touch(map_id, user, icon=rel)

    def delete(self, map_id: int) -> MapEntry:
        m = self.get(map_id)
        if not m:
            raise ValueError(f"地图 #{map_id} 不存在")
        with self.conn:
            self.conn.execute("DELETE FROM maps WHERE id=?", (map_id,))
        return m

    def icon_path(self, m: MapEntry) -> Path | None:
        if not m.icon:
            return None
        p = self.icon_dir / m.icon
        return p if p.exists() else None
