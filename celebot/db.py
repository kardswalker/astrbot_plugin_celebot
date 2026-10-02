"""地图库：SQLite 存储 + 模糊搜索。不依赖 AstrBot，可单独测试。"""

from __future__ import annotations

import difflib
from functools import lru_cache
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

# 细分难度：绿/黄/红是大厅内的相对难度，爆(cracked)为 Grandmaster 特有，+N 表示超出该档（GM+1、GM+2…）
SUB_DIFFICULTIES = ["绿", "黄", "红", "爆"]
SUB_ALIASES = {
    "绿": "绿", "绿色": "绿", "green": "绿",
    "黄": "黄", "黄色": "黄", "yellow": "黄",
    "红": "红", "红色": "红", "red": "红",
    "爆": "爆", "cracked": "爆", "crack": "爆",
}
_PLUS = re.compile(r"^(?:(gm|grandmaster)\s*)?\+(\d*)$", re.I)

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
    sub_difficulty TEXT,
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

MIN_SCORE = 0.4

try:
    from pypinyin import Style, lazy_pinyin
except ImportError:  # 拼音搜索为可选功能
    lazy_pinyin = None

_CJK = re.compile(r"[一-鿿]")


@lru_cache(maxsize=4096)
def _pinyin(text: str) -> tuple[str, str]:
    """返回 (全拼, 首字母)，无汉字或未安装 pypinyin 时为空串。"""
    if lazy_pinyin is None or not _CJK.search(text):
        return "", ""
    full = lazy_pinyin(text)
    return "".join(full), "".join(p[0] for p in full if p)


def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def _best_window(q: str, n: str) -> float:
    """q 与 n 中长度相近的子串的最大相似度，用于容忍错别字。"""
    if len(n) <= len(q):
        return difflib.SequenceMatcher(None, q, n).ratio()
    return max(difflib.SequenceMatcher(None, q, n[i : i + len(q)]).ratio() for i in range(len(n) - len(q) + 1))


def _is_subsequence(q: str, n: str) -> bool:
    it = iter(n)
    return all(c in it for c in q)


def _score(q: str, tokens: list[str], name: str) -> float:
    """q 为归一化后的查询，tokens 为原始分词；返回 0~1 的匹配度。"""
    n = norm(name)
    if not n:
        return 0.0
    if n == q:
        return 1.0
    if n.startswith(q):
        return 0.9
    if q in n:
        return 0.8
    if len(tokens) > 1 and all(norm(t) in n for t in tokens):
        return 0.75  # 多关键词全部命中，顺序不限

    best = 0.0
    full, initials = _pinyin(name)
    if full:
        for p in (full, initials):
            if q == p or (len(q) >= 2 and p.startswith(q)):
                best = max(best, 0.7)
            elif len(q) >= 3 and q in p:
                best = max(best, 0.6)
    if len(q) >= 2:
        if _is_subsequence(q, n):
            best = max(best, 0.45 + 0.1 * len(q) / len(n))  # 越紧凑越靠前
        qb, nb = _bigrams(q), _bigrams(n)
        dice = 2 * len(qb & nb) / (len(qb) + len(nb))
        if dice >= 0.5:
            best = max(best, dice * 0.75)
        if len(q) >= 4 and dice >= 0.25:  # 先用 bigram 粗筛，避免对所有名称做昂贵的窗口比较
            r = _best_window(q, n)
            if r >= 0.75:
                best = max(best, r * 0.7)
    return best


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
    sub_difficulty: str | None
    tags: list[str]
    icon: str | None
    collection: str
    author: str
    sid: str | None
    updated_by: str
    updated_at: int

    def names(self) -> list[str]:
        return [self.name, self.name_en, *self.aliases]

    @property
    def difficulty_label(self) -> str:
        """展示用难度，如 `Grandmaster 红`、`Grandmaster+1`；都没有时为空串。"""
        d, sub = self.difficulty or "", self.sub_difficulty or ""
        if d and sub:
            return f"{d}{sub}" if sub.startswith("+") else f"{d} {sub}"
        return d or sub


def _row_to_map(r: sqlite3.Row) -> MapEntry:
    return MapEntry(
        id=r["id"], name=r["name"], name_en=r["name_en"],
        aliases=json.loads(r["aliases"]), difficulty=r["difficulty"],
        sub_difficulty=r["sub_difficulty"], tags=json.loads(r["tags"]), icon=r["icon"], collection=r["collection"],
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


def normalize_sub_difficulty(value: str | None) -> str | None:
    """规范化细分难度：绿/黄/红/爆，或 +N（`+` 等同 `+1`）。空、`-` 表示清除。"""
    if value is None or not value.strip() or value.strip() in ("-", "无", "none"):
        return None
    v = value.strip()
    if v.lower() in SUB_ALIASES:
        return SUB_ALIASES[v.lower()]
    m = _PLUS.match(v)
    if m:
        return f"+{int(m.group(2) or 1)}"
    raise ValueError(f"未知细分难度「{v}」，可用：" + " / ".join(SUB_DIFFICULTIES) + " / +1 / +2 …")


def parse_difficulty_filter(token: str) -> tuple[str | None, str | None]:
    """把 `专家` / `红` / `爆` / `gm+2` 解析为 (难度, 细分难度)；无法识别时抛 ValueError。"""
    m = _PLUS.match(token)
    if m:
        return ("Grandmaster" if m.group(1) else None), f"+{int(m.group(2) or 1)}"
    if token.lower() in SUB_ALIASES:
        return None, SUB_ALIASES[token.lower()]
    return normalize_difficulty(token), None


class MapDB:
    # 允许通过 edit 修改的字段
    EDITABLE = ("name", "name_en", "difficulty", "sub_difficulty", "author", "collection")
    # 编辑字段的中文/简写别名
    FIELD_ALIASES = {"名称": "name", "中文名": "name", "英文名": "name_en", "en": "name_en", "难度": "difficulty",
                     "diff": "difficulty", "细分": "sub_difficulty", "细分难度": "sub_difficulty",
                     "sub": "sub_difficulty", "作者": "author", "合集": "collection"}

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.icon_dir = self.data_dir / "icons"
        self.icon_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.data_dir / "maps.db")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        if "sub_difficulty" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(maps)")}:
            self.conn.execute("ALTER TABLE maps ADD COLUMN sub_difficulty TEXT")  # 旧版本数据库升级
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
                    "INSERT OR IGNORE INTO maps (name,name_en,aliases,difficulty,sub_difficulty,tags,icon,collection,author,sid,updated_by,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (m["name"], m.get("name_en", ""), json.dumps(m.get("aliases", []), ensure_ascii=False),
                     m.get("difficulty"), m.get("sub_difficulty"), json.dumps(m.get("tags", []), ensure_ascii=False), m.get("icon"),
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

    def list_maps(self, collection: str | None = None, difficulty: str | None = None,
                  sub_difficulty: str | None = None) -> list[MapEntry]:
        sql, args = "SELECT * FROM maps WHERE 1=1", []
        if collection:
            sql += " AND collection=?"; args.append(collection)
        if difficulty:
            sql += " AND difficulty=?"; args.append(difficulty)
        if sub_difficulty:
            sql += " AND sub_difficulty=?"; args.append(sub_difficulty)
        return [_row_to_map(r) for r in self.conn.execute(sql + " ORDER BY id", args)]

    def search(self, query: str, collection: str | None = None, limit: int = 10,
               use_filters: bool = True) -> list[MapEntry]:
        """模糊搜索，返回按匹配度降序的结果。

        支持：名称/别名/作者的包含与前缀匹配、多关键词、错别字与漏字（bigram 相似度、子序列）、
        拼音全拼与首字母（需要 pypinyin）。查询里出现合集名/别名（如「春游 水晶」）时自动限定合集。
        """
        tokens = [t for t in re.split(r"\s+", query.strip()) if t]
        if collection is None and len(tokens) > 1:
            for t in tokens:
                c = self.find_collection(t)
                if c:
                    collection = c.key
                    tokens.remove(t)
                    break
        pool = self.list_maps(collection)
        # 完全等于某个标签/难度/细分难度的关键词（专家、gm、红、爆、gm+2…）作为过滤条件，而不是参与名称匹配。
        # 若按条件过滤后一张都没有（比如在搜名为 "Red Horizon" 的图），就退回为普通文本搜索。
        known_tags = {norm(t) for m in pool for t in m.tags}
        want_tags: set[str] = set()
        want_diff: set[str] = set()
        want_sub: set[str] = set()
        rest: list[str] = []
        filtering = False
        for t in tokens:
            nt, low = norm(t), t.lower()
            plus = _PLUS.match(t)
            if plus:
                want_sub.add(f"+{int(plus.group(2) or 1)}")
                if plus.group(1):
                    want_diff.add("Grandmaster")
            elif low in SUB_ALIASES:
                want_sub.add(SUB_ALIASES[low])
            elif nt in known_tags:
                want_tags.add(nt)
            elif len(nt) >= 2 and low in DIFFICULTY_ALIASES:
                want_diff.add(DIFFICULTY_ALIASES[low])
            else:
                rest.append(t)
        if use_filters and (want_tags or want_diff or want_sub):
            filtered = [
                m for m in pool
                if want_tags <= {norm(t) for t in m.tags}
                and (not want_diff or m.difficulty in want_diff)
                and (not want_sub or m.sub_difficulty in want_sub)
            ]
            if filtered and not rest:
                return filtered[:limit]
            if filtered:
                pool, tokens = filtered, rest
                filtering = True
        q = norm("".join(tokens))
        if not q:
            return []
        scored: list[tuple[float, MapEntry]] = []
        for m in pool:
            best = max((_score(q, tokens, n) for n in m.names() if n), default=0.0)
            if m.author:
                best = max(best, _score(q, tokens, m.author) * 0.6)
            if best >= MIN_SCORE:
                scored.append((best, m))
        if not scored and filtering:  # 过滤后文字一个都对不上：把条件词当作名称的一部分重搜（如 "red horizon"）
            return self.search(query, collection, limit, use_filters=False)
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [m for _, m in scored[:limit]]

    def add_map(self, name: str, collection: str, *, name_en: str = "", aliases: list[str] | None = None,
                difficulty: str | None = None, sub_difficulty: str | None = None,
                tags: list[str] | None = None, author: str = "",
                icon: str | None = None, user: str = "") -> MapEntry:
        if not name.strip():
            raise ValueError("地图名称不能为空")
        coll = self.find_collection(collection)
        if not coll:
            raise ValueError(f"合集「{collection}」不存在")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO maps (name,name_en,aliases,difficulty,sub_difficulty,tags,icon,collection,author,updated_by,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (name.strip(), name_en, json.dumps(aliases or [], ensure_ascii=False), normalize_difficulty(difficulty),
                 normalize_sub_difficulty(sub_difficulty), json.dumps(tags or [], ensure_ascii=False), icon, coll.key, author, user, int(time.time())),
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
        field_name = self.FIELD_ALIASES.get(field_name.lower(), field_name)
        if field_name not in self.EDITABLE:
            raise ValueError("可修改字段：" + " / ".join(self.EDITABLE) + "（别名、标签、图标请用专用指令）")
        if field_name == "difficulty":
            value = normalize_difficulty(value)  # type: ignore[assignment]
        elif field_name == "sub_difficulty":
            value = normalize_sub_difficulty(value)  # type: ignore[assignment]
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
