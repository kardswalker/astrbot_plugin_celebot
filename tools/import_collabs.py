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
import io
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed"

# 难度目录 → (难度名, 排序)
DIFFICULTIES = ["Beginner", "Intermediate", "Advanced", "Expert", "Grandmaster"]
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
        # 逐图图标：Assets 包里的贴纸，路径 Stickers/SJ2021/<难度目录>/<地图文件>.png
        "sticker_zip": "StrawberryJam2021Assets.zip",
        "sticker_path": "Graphics/Atlases/Stickers/SJ2021/{folder}/{file}.png",
        # 贴纸文件名与地图文件名不一致的情况：地图 "<目录>/<文件>" → 贴纸文件名（不含扩展名，不区分大小写）
        "sticker_aliases": {
            "1-Beginner/asteriskblue": "Asterisk",
            "1-Beginner/Bing_Over_Google": "BingOverGoogle",
            "1-Beginner/Flagpole1up": "Flagpole",
            "1-Beginner/Owen-Shirrell": "OwenShirrell",
            "2-Intermediate/GlowWoomii": "Woomii",
            "4-Expert/Archire": "Archra",
            "5-Grandmaster/CaptainCarpensir": "Aiden",  # Belly of the Beast（作者 Aiden）
            "5-Grandmaster/Linj": "LinjGM",  # Ivory
        },
        "sub_difficulty_file": "sj2021_difficulty.json",  # 细分难度（颜色 / 爆 / +N），来自社区维基
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
    {
        "key": "gallery2024",
        "zip": "ChineseNewYear2024Collab.zip",
        "mod": "ChineseNewYear2024",
        "name": "画游合集",
        "name_en": "Gallery Collab (CNY 2024)",
        "aliases": ["画游", "GC", "Gallery Collab", "CNY2024", "新年合集", "画游新年合集"],
        "difficulty_from_dir": False,
        "tags_from_dialog": True,  # Dialog 的 collabcreditstags 形如 "Yellow Advanced"：颜色 + 难度
        "prefer_zh_author": True,
        # 逐图图标：Stickers/ChineseNewYear2024/Lobby/<英文名>.png，按归一化英文名匹配
        "sticker_dir": "Graphics/Atlases/Stickers/ChineseNewYear2024/Lobby/",
        "sticker_name_aliases": {  # 地图文件 → 贴纸文件名（不含扩展名），用于英文名对不上的
            "youziAfterain": "Afterain",
            "StarSapphireGDDN": "GDDN",
            "ShadowRo": "Season_Traveler",
        },
        "fallback_icon": "Graphics/Atlases/Gui/areas/CNY2024/Lobby/1-Lobby.png",
    },
]

COLORS_ZH = {"Yellow": "黄", "Red": "红", "Green": "绿"}
DATA = Path(__file__).resolve().parent / "data"


def load_sub_difficulty(c: dict) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """返回 ({(难度, 归一化英文名): 细分难度}, {难度: 心门细分难度})。"""
    if "sub_difficulty_file" not in c:
        return {}, {}
    data = json.loads((DATA / c["sub_difficulty_file"]).read_text(encoding="utf-8"))
    by_name = {
        (tier, norm_name(n)): sub
        for tier, groups in data.items() if tier in DIFFICULTIES
        for sub, names in groups.items() for n in names
    }
    return by_name, data.get("heartside", {})


def norm_name(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())

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
            cur = m.group(1).lower()  # Celeste 的 Dialog key 不区分大小写
            if m.group(2).strip():
                out[cur] = m.group(2).strip()
        elif cur and cur not in out and line.strip():
            out[cur] = line.strip()
    return out


def clean(s: str) -> str:
    s = BRACES.sub("", s).strip()
    # 汉化里常见 "香 皂" 这类排版空格，去掉汉字之间的空格
    return re.sub(r"(?<=[一-鿿]) +(?=[一-鿿])", "", s)


def split_author(raw: str) -> str:
    """作者行形如 'Sapphire Summit | 作者：Banana 23' 或 'Author | Banana 23'，取最后一段并去前缀。"""
    tail = raw.split("|")[-1].strip()
    return re.sub(r"^((作者|Author)\s*[:：]|by\s+)\s*", "", tail, flags=re.I).strip()


def dialog_key(mod: str, folder: str, file: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", f"{mod}_{folder}_{file}").lower()


def file_slug(folder: str, file: str) -> str:
    """不同难度目录下可能有同名地图文件（如 ZZ-HeartSide），图标文件名带上目录避免冲突。"""
    return f"{folder}_{file}"


ICON_MAX = 256  # 聊天里展示足够清晰，同时控制仓库体积


def save_icon(z: zipfile.ZipFile, src: str, dest_rel: str) -> str | None:
    if src not in z.namelist():
        return None
    dest = SEED / "icons" / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = z.read(src)
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        if max(im.size) > ICON_MAX:
            im.thumbnail((ICON_MAX, ICON_MAX), Image.LANCZOS)
        im.save(dest, optimize=True)
    except ImportError:  # 没有 Pillow 就原样写入
        dest.write_bytes(data)
    return dest_rel.replace("\\", "/")


def import_collab(mods: Path, c: dict) -> tuple[dict, list[dict]]:
    z = zipfile.ZipFile(mods / c["zip"])
    zh = parse_dialog(z, "Simplified Chinese")
    en = parse_dialog(z, "English")
    maps: list[dict] = []
    sub_by_name, sub_heart = load_sub_difficulty(c)
    name_stickers: dict[str, str] = {}  # 归一化贴纸名 → zip 内路径（按英文名匹配的合集用）
    if "sticker_dir" in c:
        name_stickers = {
            norm_name(Path(n).stem): n
            for n in z.namelist() if n.startswith(c["sticker_dir"]) and n.lower().endswith(".png")
        }
    stickers = None
    if "sticker_zip" in c:
        sz = zipfile.ZipFile(mods / c["sticker_zip"])
        stickers = (sz, {n.lower(): n for n in sz.namelist()})
    unmatched = set(sub_by_name)
    used_stickers: set[str] = set()
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
        a_en, a_zh = en.get(key + "_author", ""), zh.get(key + "_author", "")
        author = split_author((a_zh or a_en) if c.get("prefer_zh_author") else (a_en or a_zh))
        tags: list[str] = []
        difficulty = None
        sub_difficulty = None
        if c.get("tags_from_dialog"):
            parts = en.get(key + "_collabcreditstags", "").split()
            if len(parts) == 2 and parts[1] in DIFFICULTIES:
                difficulty = parts[1]
                sub_difficulty = COLORS_ZH.get(parts[0], parts[0])

        icon = None
        if c.get("difficulty_from_dir") and folder in TIERS:
            tier, n = TIERS[folder]
            difficulty = tier
            src = c["tier_icon"].format(tier=folder, spring=SPRING_ICONS[n])
            icon = save_icon(z, src, f"{c['key']}/{folder}.png")
        if sub_by_name and difficulty:
            if "heartside" in file.lower():
                sub_difficulty = sub_heart.get(difficulty)
            else:
                sub_difficulty = sub_by_name.get((difficulty, norm_name(name_en)))
                unmatched.discard((difficulty, norm_name(name_en)))
        if stickers:
            sz, lower = stickers
            stem = c.get("sticker_aliases", {}).get(f"{folder}/{file}", file)
            src = lower.get(c["sticker_path"].format(folder=folder, file=stem).lower())
            if src:
                icon = save_icon(sz, src, f"{c['key']}/{file_slug(folder, file)}.png") or icon
        if name_stickers:
            stem = c.get("sticker_name_aliases", {}).get(file, name_en)
            src = name_stickers.get(norm_name(stem))
            if src:
                icon = save_icon(z, src, f"{c['key']}/{file}.png") or icon
                used_stickers.add(src)
        if "card_icon" in c:
            icon = save_icon(z, c["card_icon"].format(file=file), f"{c['key']}/{file}.png")
        if not icon and "fallback_icon" in c:
            icon = save_icon(z, c["fallback_icon"], f"{c['key']}/default.png")

        maps.append(
            {
                "name": name_zh or name_en,
                "name_en": name_en,
                "aliases": [],
                "difficulty": difficulty,
                "sub_difficulty": sub_difficulty,
                "tags": tags,
                "icon": icon,
                "collection": c["key"],
                "author": author,
                "sid": f"{c['mod']}/{folder}/{file}",
            }
        )
    if name_stickers and set(name_stickers.values()) - used_stickers:
        print(f"WARN {c['key']}: 未使用的贴纸 {sorted(set(name_stickers.values()) - used_stickers)}")
    if unmatched:
        print(f"WARN {c['key']}: 细分难度数据里有 {len(unmatched)} 个名称没有对上地图：{sorted(unmatched)}")
    coll = {k: c[k] for k in ("key", "name", "name_en", "aliases")}
    return coll, maps


OFFICIAL = {
    "key": "vanilla",
    "name": "官图",
    "name_en": "Celeste (Vanilla)",
    "aliases": ["原版", "本体", "vanilla"],
}


def official_maps() -> list[dict]:
    # 名称取自游戏本体 Content/Dialog 的 AREA_0..AREA_10（简中 / 英文）；别名为玩家常用叫法。
    chapters = [
        ("0", "序幕", "Prologue", ["序章", "Prologue"]),
        ("1", "被遗弃的城市", "Forsaken City", ["先辈之城", "遗弃之城", "城市", "1A"]),
        ("2", "旧址", "Old Site", ["旧遗址", "2A"]),
        ("3", "天空度假山庄", "Celestial Resort", ["天空度假村", "度假山庄", "度假村", "酒店", "3A"]),
        ("4", "黄金山脊", "Golden Ridge", ["山脊", "4A"]),
        ("5", "镜之寺庙", "Mirror Temple", ["镜之神殿", "神殿", "寺庙", "5A"]),
        ("6", "沉思", "Reflection", ["映像", "倒影", "6A"]),
        ("7", "山顶", "The Summit", ["山巅", "Summit", "7A"]),
        ("8", "尾声", "Epilogue", ["Epilogue"]),
        ("9", "核心", "Core", ["8A"]),
        ("10", "再见", "Farewell", ["终章", "告别", "Farewell"]),
    ]
    return [
        {
            "name": zh,
            "name_en": en,
            "aliases": aliases,
            "difficulty": None,
            "tags": [],
            "icon": None,
            "collection": "vanilla",
            "author": "Maddy Makes Games",
            "sid": f"Celeste/{num}-{en.replace(' ', '')}",
        }
        for num, zh, en, aliases in chapters
    ]


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
