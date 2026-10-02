"""离线导入脚本：从本地 Celeste/Everest 的 Mods 目录提取合集地图，生成插件的种子数据。

用法：
    python tools/import_collabs.py --mods "D:/SteamLibrary/steamapps/common/Celeste/Mods"

输出：
    seed/maps.json     地图种子数据（插件首次启动时导入数据库）
    seed/icons/...     地图图标（仅冬游有逐图卡片，其余合集回退到难度级别图标）

数据来源：
    - Maps/<合集>/<难度>/<文件>.bin   → 地图列表（文件名即地图 ID）
    - Dialog/*.txt                    → 地图名（中/英）与作者
    - Graphics/Atlases/Gui/...        → 图标
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed"

# 难度目录 → (难度名, 排序)
TIERS = {
    "1-Beginner": ("Beginner", 1),
    "2-Intermediate": ("Intermediate", 2),
    "3-Advanced": ("Advanced", 3),
    "4-Expert": ("Expert", 4),
    "5-Grandmaster": ("Grandmaster", 5),
}

# 合集定义。difficulty_from_dir：目录名即难度；icon_* 见下方 pick_icon。
COLLABS = [
    {
        "key": "sj2021",
        "zip": "StrawberryJam2021.zip",
        "mod": "StrawberryJam2021",
        "name": "草莓酱合集",
        "name_en": "Strawberry Jam Collab",
        "aliases": ["草莓酱", "SJ", "SJ2021", "Strawberry Jam"],
        "difficulty_from_dir": True,
        "tier_icon": "Graphics/Atlases/Gui/areas/SJ2021/lobby/{tier}.png",
    },
    {
        "key": "spring2020",
        "zip": "SpringCollab2020.zip",
        "mod": "SpringCollab2020",
        "name": "春游合集",
        "name_en": "Spring Collab 2020",
        "aliases": ["春游", "SC", "SC2020", "Spring Collab"],
        "difficulty_from_dir": True,
        "tier_icon": "Graphics/Atlases/Gui/areas/SpringCollab2020/lobbies/{spring}.png",
    },
    {
        "key": "winter2021",
        "zip": "WinterCollab2021.zip",
        "mod": "WinterCollab2021",
        "name": "冬游合集",
        "name_en": "Winter Collab 2021",
        "aliases": ["冬游", "WC", "WC2021", "Winter Collab"],
        "difficulty_from_dir": False,  # 冬游不分难度目录
        "card_icon": "Graphics/Atlases/Gui/areaselect/WinterCollab2021/1-Maps/{file}_card.png",
        "fallback_icon": "Graphics/Atlases/Gui/areas/WinterCollab2021/flake.png",
    },
]

SPRING_ICONS = {1: "1-bgnricon", 2: "2-imicon", 3: "3-advanced", 4: "4-experticon", 5: "5-gmicon"}
CJK = re.compile(r"[一-鿿]")
DIALOG_LINE = re.compile(r"^([A-Za-z0-9_\-\.]+)=(.*)$")
BRACES = re.compile(r"\{[^}]*\}")


def parse_dialog(z: zipfile.ZipFile, lang: str) -> dict[str, str]:
    name = f"Dialog/{lang}.txt"
    if name not in z.namelist():
        return {}
    # Celeste 的 Dialog 允许 `key=` 后值写在后续行，直到下一个 key；这里取第一个非空行。
    out: dict[str, str] = {}
    cur: str | None = None
    for line in z.read(name).decode("utf-8-sig", "replace").splitlines():
        if line.startswith("#"):
            continue
        m = DIALOG_LINE.match(line)
        if m:
            cur = m.group(1)
            if m.group(2).strip():
                out[cur] = m.group(2).strip()
        elif cur and cur not in out and line.strip():
            out[cur] = line.strip()
    return out


def clean(s: str) -> str:
    return BRACES.sub("", s).strip()


def split_author(raw: str) -> str:
    """作者行形如 'Sapphire Summit | 作者：Banana 23' 或 'Author | Banana 23'，取最后一段并去前缀。"""
    tail = raw.split("|")[-1].strip()
    return re.sub(r"^((作者|Author)\s*[:：]|by\s+)\s*", "", tail, flags=re.I).strip()


def dialog_key(mod: str, folder: str, file: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", f"{mod}_{folder}_{file}")


def save_icon(z: zipfile.ZipFile, src: str, dest_rel: str) -> str | None:
    if src not in z.namelist():
        return None
    dest = SEED / "icons" / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(z.read(src))
    return dest_rel.replace("\\", "/")


def import_collab(mods: Path, c: dict) -> tuple[dict, list[dict]]:
    z = zipfile.ZipFile(mods / c["zip"])
    zh = parse_dialog(z, "Simplified Chinese")
    en = parse_dialog(z, "English")
    maps: list[dict] = []
    pat = re.compile(rf"^Maps/{c['mod']}/(\d+-[^/]+)/([^/]+)\.bin$")
    for entry in sorted(z.namelist()):
        m = pat.match(entry)
        if not m:
            continue
        folder, file = m.groups()
        if folder.startswith("0-"):  # 大厅 / 练习场 / 序幕
            continue
        key = dialog_key(c["mod"], folder, file)
        name_en = clean(en.get(key, "")) or file
        name_zh = clean(zh.get(key, ""))
        name_zh = name_zh if CJK.search(name_zh) else ""  # 没有汉化时简中 Dialog 会回落成英文
        author = split_author(en.get(key + "_author", "") or zh.get(key + "_author", ""))
        tags: list[str] = []
        if file.lower().startswith("zz-heartside") or "heartside" in file.lower():
            tags.append("红心面")
            name_en = name_en if name_en != file else "Heartside"

        difficulty = None
        icon = None
        if c.get("difficulty_from_dir") and folder in TIERS:
            tier, n = TIERS[folder]
            difficulty = tier
            src = c["tier_icon"].format(tier=folder, spring=SPRING_ICONS[n])
            icon = save_icon(z, src, f"{c['key']}/{folder}.png")
        if "card_icon" in c:
            icon = save_icon(z, c["card_icon"].format(file=file), f"{c['key']}/{file}.png")
            icon = icon or save_icon(z, c["fallback_icon"], f"{c['key']}/default.png")

        maps.append(
            {
                "name": name_zh or name_en,
                "name_en": name_en,
                "aliases": [],
                "difficulty": difficulty,
                "tags": tags,
                "icon": icon,
                "collection": c["key"],
                "author": author,
                "sid": f"{c['mod']}/{folder}/{file}",
            }
        )
    coll = {k: c[k] for k in ("key", "name", "name_en", "aliases")}
    return coll, maps


OFFICIAL = {
    "key": "vanilla",
    "name": "官图",
    "name_en": "Celeste (Vanilla)",
    "aliases": ["原版", "本体", "vanilla"],
}


def official_maps() -> list[dict]:
    chapters = [
        ("0", "序章", "Prologue", ["Prologue"], None),
        ("1", "先辈之城", "Forsaken City", ["城市", "1A"], "1"),
        ("2", "旧址", "Old Site", ["2A"], "2"),
        ("3", "天空度假村", "Celestial Resort", ["酒店", "度假村", "3A"], "3"),
        ("4", "黄金山脊", "Golden Ridge", ["山脊", "4A"], "4"),
        ("5", "镜之神殿", "Mirror Temple", ["神殿", "5A"], "5"),
        ("6", "映像", "Reflection", ["6A"], "6"),
        ("7", "山巅", "The Summit", ["7A"], "7"),
        ("8", "核心", "Core", ["8A"], "8"),
        ("9", "农场", "Farewell", ["终章", "告别", "Farewell", "9"], None),
    ]
    out = []
    for num, zh, en, aliases, _ in chapters:
        out.append(
            {
                "name": zh,
                "name_en": en,
                "aliases": aliases,
                "difficulty": None,
                "tags": ["A面"] if num not in ("0", "9") else [],
                "icon": None,
                "collection": "vanilla",
                "author": "Maddy Makes Games",
                "sid": f"Celeste/{num}-{en.replace(' ', '')}",
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mods", required=True, help="Celeste/Mods 目录")
    args = ap.parse_args()
    mods = Path(args.mods)

    collections = [OFFICIAL]
    maps = official_maps()
    for c in COLLABS:
        coll, ms = import_collab(mods, c)
        collections.append(coll)
        maps.extend(ms)
        print(f"{c['key']}: {len(ms)} maps, {sum(1 for m in ms if m['icon'])} icons")

    SEED.mkdir(exist_ok=True)
    (SEED / "maps.json").write_text(
        json.dumps({"collections": collections, "maps": maps}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"total {len(maps)} maps -> {SEED / 'maps.json'}")


if __name__ == "__main__":
    main()
