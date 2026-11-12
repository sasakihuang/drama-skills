#!/usr/bin/env python3
"""Make the two covers every finished film goes out with: an upright one (3:4, 1080x1440) and a wide
one (4:3, 1440x1080), the two shapes Douyin's publishing page asks for.

    cover.py frames <成片.mp4> --out <封面目录> [--animation <动画目录> | --clean <不带字幕的成片>] [--count 12]
    cover.py prompt <封面目录>
    cover.py make   <封面目录>

``frames`` lays out candidate pictures in ``<封面目录>/候选帧`` with a contact sheet and the second
each one comes from: evenly through the film, each the steadiest frame of its stretch, taken from
the same cut without subtitles when there is one (the edit skill keeps ``成片-未混音.mp4``); or,
given a motion-graphics film's animation folder, every scene once everything in it is on screen,
drawn again by the renderer without captions or headings. It also starts a ``封面.json`` there.

By default each cover is drawn whole by gpt-image-2, words and all: ``prompt`` turns ``封面.json``
(the title, the hook, the subject to draw, the look) into one production job per cover from a fixed
template, with a frame of the film as the reference for footage so its people stay the same. The
production skill prices, shows, has the creator confirm and runs them (0.40 yuan each at the yoyo
relay); nothing is generated here. ``make`` then fits each AI picture to its cover.

Where there is no current AI picture (none drawn, the creator would rather not pay, its words were
changed afterwards, or ``"ai": false`` because the words came out wrong), ``make`` draws that cover
itself with the page in ``assets/cover-page``, in the browser the motion kit renders with, free, in
one of two styles:

- poster (the default for a motion-graphics film): a cover made for the film, not a frame of it.
  The film's title glows on the film's own dark ground, a hook line and a small English line under
  it, and the one thing that tells the film (the chart, the number) cut out of its frame, enlarged
  and laid in, its edges melting into the ground. Where that thing is in the frame is found by
  brightness unless the spec says.
- photo (the default for footage): the frame fills the cover, darkened where the words sit, or
  becomes a card on a blurred copy of itself when its shape does not suit the cover.

The words are set at the largest size that fits. Both covers are written beside the film as
``<片名>-封面-竖3比4.jpg`` and ``<片名>-封面-横4比3.jpg``. This script itself sends nothing anywhere;
the AI pictures are made only by the production skill, after the creator confirms. The last stdout
line is one JSON object.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple

import animation

MINIMUM_PYTHON = (3, 9)

PAGE = Path(__file__).resolve().parents[1] / "assets" / "cover-page"
SPEC = "封面.json"
SIDES = ("vertical", "horizontal")
SIZE = {"vertical": (1080, 1440), "horizontal": (1440, 1080)}
NAME = {"vertical": "竖", "horizontal": "横"}
SUFFIX = {"vertical": "竖3比4", "horizontal": "横4比3"}
STYLES = ("poster", "photo")
PLACES = {"vertical": ("top", "middle", "bottom"), "horizontal": ("left", "center", "right", "top", "bottom")}
FONT_KINDS = ("bold", "light", "brush")
FONT_FILES = ("noto-sans-sc-chinese-simplified-400-normal.woff2", "noto-sans-sc-chinese-simplified-700-normal.woff2",
              "noto-sans-sc-chinese-simplified-900-normal.woff2", "ma-shan-zheng-chinese-simplified-400-normal.woff2")
ACCENT = "#ffd23f"
COLOUR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
# What a cover's words may hold: they are read at a glance, often in a phone's grid of small covers.
MAX_LINES = 3
LINE_WIDTH = 9.0
TITLE_WIDTH = 20.0
TAG_WIDTH = 14.0
SUB_WIDTH = 18.0
EN_LETTERS = 40
ZOOM = (1.0, 3.0)
SMALLEST_CROP = 0.1
# A picture this much wider or narrower than its cover would lose too much to cropping: it goes on a card.
CARD_BEYOND = 1.6
# A poster's ground is the frame's own: its corners, darkened when the frame is light.
DARK_GROUND = 40
# The subject of a dark frame: what is brighter than this (0-255) on a frame shrunk to SCAN pixels wide,
# with a margin round it.
BRIGHT = 48
SCAN = 240
MARGIN = 0.04
CANDIDATES = 12
WINDOW = 3.0
STILL_AT = 0.5
KEYS = {"film", "title", "sub", "en", "tag", "font", "accent", "style", "animation", "clean", "at", "image", "crop",
        "visual", "look", "ai", "reference", "vertical", "horizontal"}
SIDE_KEYS = {"style", "at", "image", "crop", "place", "zoom", "focus", "ai"}
# The AI cover: one gpt-image-2 picture per cover through the production skill (0.40 yuan each at the
# yoyo relay), drawn whole from a fixed template; asked a little larger than the cover and fitted to it.
AI_SIZE = {"vertical": (1152, 1536), "horizontal": (1536, 1152)}
AI_NAME = {"vertical": "cover-3x4.png", "horizontal": "cover-4x3.png"}
AI_FOLDER = "AI出图"
AI_PRICE = 0.40
VISUAL_LETTERS = 200
LOOK = "黑底金色、高级感，深色背景带一点暖色光晕和金色微光"
FONT_WORDS = {"bold": "粗黑体", "light": "细黑体、字距拉开、素雅", "brush": "毛笔书法字"}
PRODUCE = Path(__file__).resolve().parents[2] / "short-drama-produce" / "scripts"
# How far the film without subtitles may differ in length from the film itself and still be the same cut.
SAME_CUT = 0.5
VERSION_SUFFIXES = ("-草稿", "_草稿", "-正式版", "_正式版", "-draft", "_draft", "-final", "_final")
SOUND_FILES = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")

Box = Tuple[float, float, float, float]


class Problem(Exception):
    pass


def emit(payload: Dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


# ------------------------------------------------------------------- what a cover says
def width(text: str) -> float:
    """How wide a line reads, in full-width characters: digits, Latin letters and spaces count half."""
    return sum(1.0 if ord(letter) >= 0x2E80 else 0.5 for letter in text.replace("**", ""))


def markup(line: str) -> str:
    """A title line as the page's HTML: escaped, with **重点** turned into the highlighted part."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(line, quote=False))


def number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Problem(f"{what}要写数字，现在写的是 {json.dumps(value, ensure_ascii=False)}。")
    return float(value)


def located(folder: Path, value: Any, what: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise Problem(f"{what}要写文件路径。")
    path = Path(value.strip()).expanduser()
    return (path if path.is_absolute() else folder / path).resolve()


def title_lines(value: Any) -> List[str]:
    lines = value.splitlines() if isinstance(value, str) else [] if value is None else value
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise Problem("title 写成一行一项的列表，例如 [\"放量阳线\"] 或 [\"看见**放量**\", \"先问这三句\"]。")
    lines = [line.strip() for line in lines if line.strip()]
    if not lines:
        raise Problem("title 还没写：海报式写片名，画面式写让人想点开的那句话，一到两行。")
    if len(lines) > MAX_LINES:
        raise Problem(f"title 写了 {len(lines)} 行，最多 {MAX_LINES} 行：封面一眼要读完。")
    for index, line in enumerate(lines, 1):
        if line.count("**") % 2 or "****" in line:
            raise Problem(f"title 第 {index} 行「{line}」的 ** 没配成对：重点词前后各一个 **。")
        if width(line) > LINE_WIDTH:
            raise Problem(f"title 第 {index} 行「{line.replace('**', '')}」有 {width(line):g} 个字宽，一行最多 {LINE_WIDTH:g} 个"
                          "（数字、字母算半个）：拆成两行，或者删字。")
    total = sum(width(line) for line in lines)
    if total > TITLE_WIDTH:
        raise Problem(f"title 一共 {total:g} 个字宽，最多 {TITLE_WIDTH:g} 个：封面上的字越少越抓眼，删到只剩钩子。")
    return lines


def short_line(raw: Dict[str, Any], key: str, limit: float, what: str) -> str:
    value = raw.get(key) or ""
    if not isinstance(value, str) or "**" in value or "\n" in value or width(value) > limit:
        raise Problem(f"{key} 是{what}：一行、不超过 {limit:g} 个字宽（数字、字母算半个）、不用 **。")
    return value.strip()


def crop_box(value: Any, label: str) -> Optional[List[float]]:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise Problem(f"{label}的 crop 写 [左, 上, 宽, 高]，都是占整个画面的比例（0 到 1），例如 [0.05, 0.2, 0.9, 0.5]。")
    x, y, w, h = (number(item, f"{label}的 crop") for item in value)
    if min(x, y) < 0 or min(w, h) < SMALLEST_CROP or x + w > 1.0001 or y + h > 1.0001:
        raise Problem(f"{label}的 crop 出了画面或者太小：左加宽、上加高都不能超过 1，宽和高至少 {SMALLEST_CROP:g}。")
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def check_spec(raw: Any, folder: Path, seconds: Optional[float]) -> Dict[str, Any]:
    """封面.json made whole: every field checked, both covers' style and picture settled, defaults filled in.

    `seconds` is the film's length, to check that each `at` falls inside it (None skips that)."""
    if not isinstance(raw, dict):
        raise Problem(f"{SPEC} 要写成一个 {{…}} 对象。")
    unknown = sorted(set(raw) - KEYS)
    if unknown:
        raise Problem(f"{SPEC} 里有认不得的项：{'、'.join(unknown)}。能写的是：{'、'.join(sorted(KEYS))}。")
    spec: Dict[str, Any] = {"film": located(folder, raw.get("film"), "film（成片）"), "lines": title_lines(raw.get("title"))}
    spec["sub"] = short_line(raw, "sub", SUB_WIDTH, "标题下面那句钩子")
    spec["tag"] = short_line(raw, "tag", TAG_WIDTH, "标题上面那行小字（系列名、第几讲）")
    english = raw.get("en") or ""
    if not isinstance(english, str) or "\n" in english or len(english) > EN_LETTERS or any(ord(c) > 0x2E7F for c in english):
        raise Problem(f"en 是最下面那行英文小字，只写英文，不超过 {EN_LETTERS} 个字母。")
    spec["en"] = english.strip()
    font = raw.get("font") or "bold"
    if font not in FONT_KINDS:
        raise Problem("font 写 bold（粗黑体，默认）、light（细字加宽字距，素雅）或 brush（毛笔字，片头是毛笔大字时用）。")
    spec["font"] = font
    accent = raw.get("accent") or ACCENT
    if not isinstance(accent, str) or not COLOUR.fullmatch(accent):
        raise Problem(f"accent 是重点词的颜色，写成 {ACCENT} 这样的六位色号。")
    spec["accent"] = accent
    spec["animation"] = None
    if raw.get("animation"):
        spec["animation"] = located(folder, raw["animation"], "animation（动画目录）")
        if not (spec["animation"] / "index.html").is_file() or not (spec["animation"] / "film.js").is_file():
            raise Problem(f"animation 指的 {spec['animation']} 不是排好的动画目录（要有 index.html 和 film.js）。")
    spec["clean"] = None
    if raw.get("clean"):
        if spec["animation"] is not None:
            raise Problem("animation 和 clean 只写一个：图文动效片写 animation，剧情实拍片写 clean（不带字幕的那一版）。")
        spec["clean"] = located(folder, raw["clean"], "clean（不带字幕的成片）")
        if not spec["clean"].is_file():
            raise Problem(f"clean 指的文件找不到：{spec['clean']}。")
    visual, look = raw.get("visual") or "", raw.get("look") or ""
    if not isinstance(visual, str) or len(visual) > VISUAL_LETTERS or not isinstance(look, str) or len(look) > VISUAL_LETTERS:
        raise Problem(f"visual（AI 封面画什么）和 look（什么样的风格）各写一两句话，不超过 {VISUAL_LETTERS} 个字。")
    spec["visual"], spec["look"] = visual.strip(), look.strip()
    for key in ("ai", "reference"):
        if raw.get(key) is not None and not isinstance(raw[key], bool):
            raise Problem(f"{key} 写 true 或 false。")
    spec["ai"] = raw.get("ai", True)
    # Footage has people to keep: their AI cover is drawn from a frame of the film unless told otherwise.
    spec["reference"] = raw["reference"] if raw.get("reference") is not None else spec["clean"] is not None
    shared_style = raw.get("style") or ("poster" if spec["animation"] is not None else "photo")
    sides: Dict[str, Dict[str, Any]] = {}
    for side in SIDES:
        own = raw.get(side) or {}
        label = f"{NAME[side]}封面"
        if not isinstance(own, dict):
            raise Problem(f"{side} 要写成一个 {{…}} 对象。")
        strange = sorted(set(own) - SIDE_KEYS)
        if strange:
            raise Problem(f"{side} 里有认不得的项：{'、'.join(strange)}。能写的是：{'、'.join(sorted(SIDE_KEYS))}。")
        style = own.get("style") or shared_style
        if style not in STYLES:
            raise Problem(f"style 写 poster（海报式：片名加片子里的核心画面，图文动效片默认）或 photo（画面式：画面铺满加大字，"
                          f"剧情实拍片默认），现在是 {style!r}。")
        # The cover's own line wins over the shared one; one line names a second or a picture, not both.
        source = own if ("at" in own or "image" in own) else raw
        if source.get("at") is not None and source.get("image"):
            raise Problem(f"{label}同时写了 at 和 image：用成片里的一刻就写 at，用一张图就写 image，只写一个。")
        chosen: Dict[str, Any] = {"style": style, "at": None, "image": None}
        if source.get("image"):
            chosen["image"] = located(folder, source["image"], f"{label}的 image")
            if not chosen["image"].is_file():
                raise Problem(f"{label}的 image 找不到：{chosen['image']}。")
        elif source.get("at") is not None:
            at = number(source["at"], f"{label}的 at（成片第几秒）")
            if at < 0 or (seconds is not None and at >= seconds):
                raise Problem(f"{label}的 at 是 {at:g} 秒，成片只有 {seconds or 0:.2f} 秒。")
            chosen["at"] = round(at, 3)
        # Neither is fine here: an AI cover needs no frame. The program's version asks for one when it is drawn.
        chosen["crop"] = crop_box(own["crop"] if "crop" in own else raw.get("crop"), label)
        if own.get("ai") is not None and not isinstance(own["ai"], bool):
            raise Problem(f"{side} 的 ai 写 true 或 false。")
        chosen["ai"] = own.get("ai", spec["ai"])
        place = own.get("place")
        if place is not None and place not in PLACES[side]:
            raise Problem(f"{side} 的 place 写 {' / '.join(PLACES[side])}，现在是 {place!r}。")
        zoom = number(own.get("zoom", 1), f"{label}的 zoom")
        if not ZOOM[0] <= zoom <= ZOOM[1]:
            raise Problem(f"{label}的 zoom 是放大几倍，在 {ZOOM[0]:g} 到 {ZOOM[1]:g} 之间。")
        focus = own.get("focus", [0.5, 0.5])
        if (not isinstance(focus, list) or len(focus) != 2
                or not all(0 <= number(value, f"{label}的 focus") <= 1 for value in focus)):
            raise Problem(f"{label}的 focus 写 [横向, 竖向]，都是 0 到 1：放大时朝画面的哪一点放，[0.5, 0.5] 是正中。")
        chosen.update(place=place, zoom=zoom, focus=[float(value) for value in focus])
        sides[side] = chosen
    spec["sides"] = sides
    return spec


def shape_for(side: str, picture: Tuple[int, int]) -> str:
    """For a photo cover: 'full' when the picture can fill it, 'card' when cropping it would lose too much."""
    cover_w, cover_h = SIZE[side]
    ratio = (picture[0] / picture[1]) / (cover_w / cover_h)
    return "full" if 1 / CARD_BEYOND <= ratio <= CARD_BEYOND else "card"


def cover_paths(film: Path) -> Dict[str, Path]:
    """Where the covers go: beside the film, named after it without its draft or final mark."""
    stem = film.stem
    for suffix in VERSION_SUFFIXES:
        if stem.endswith(suffix) and len(stem) > len(suffix):
            stem = stem[: -len(suffix)]
            break
    return {side: film.parent / f"{stem}-封面-{SUFFIX[side]}.jpg" for side in SIDES}


def page_for(side: str) -> str:
    """The cover page at 1080x1440, or turned on its side for the wide cover (it names its size in four places)."""
    text = (PAGE / "index.html").read_text(encoding="utf-8")
    if side == "horizontal":
        text = text.replace("1080", "@W@").replace("1440", "@H@").replace("@W@", "1440").replace("@H@", "1080")
    return text


def side_data(side: str, own: Dict[str, Any], name: str, size: Tuple[int, int],
              crop: Optional[List[float]], ground: str) -> Dict[str, Any]:
    """What the page is told about one cover: its style, its picture and the part of it to show."""
    data: Dict[str, Any] = {"style": own["style"], "picture": name, "size": list(size), "place": own["place"]}
    if own["style"] == "poster":
        shown = crop or [0.0, 0.0, 1.0, 1.0]
        data.update(crop=shown, bg=ground, aspect=round((shown[2] * size[0]) / (shown[3] * size[1]), 4))
    else:
        data.update(mode=shape_for(side, size), aspect=round(size[0] / size[1], 4), zoom=own["zoom"], focus=own["focus"])
    return data


def page_data(spec: Dict[str, Any], sides: Dict[str, Dict[str, Any]]) -> str:
    data: Dict[str, Any] = {"lines": [markup(line) for line in spec["lines"]], "sub": spec["sub"], "en": spec["en"],
                            "tag": spec["tag"], "font": spec["font"], "accent": spec["accent"], **sides}
    return "window.COVER = " + json.dumps(data, ensure_ascii=False) + ";\n"


# ------------------------------------------------------------------- the AI cover
def ai_prompt(spec: Dict[str, Any], side: str, referenced: bool) -> str:
    """The picture prompt for one AI cover, from a fixed template: every cover reads the same way (a tag,
    a big title, a hook line, a small English line at the top, the subject below), so results stay alike
    from film to film; only the words, the subject and the look are the film's own."""
    gold = "金色" if spec["accent"].casefold() == ACCENT else f"色号 {spec['accent']} 的颜色"
    brush = spec["font"] == "brush"
    plain = [line.replace("**", "") for line in spec["lines"]]
    visual = spec["visual"] if spec["visual"][-1:] in "。！？.!?" else spec["visual"] + "。"
    lines = [f"{'竖版 3:4' if side == 'vertical' else '横版 4:3'} 的短视频封面，像电影海报一样精致：{spec['look'] or LOOK}。",
             f"画面内容：{visual}"]
    if referenced:
        lines.append("参考图是片子里的一帧：人物的长相、发型、服装和场景都照参考图，不要换人；参考图上的字幕和文字不要画进来。")
    lines.append("文字（必须一字不差、清晰锐利，不要多写任何别的字）：")
    items = []
    if spec["tag"]:
        items.append(f"最上方一个圆角小标签，{gold}底、黑色字：「{spec['tag']}」")
    face = f"{FONT_WORDS[spec['font']]}，{gold if brush else '暖白色'}，带一点发光"
    if len(plain) == 1:
        title = f"超大标题，{face}：「{plain[0]}」"
    else:
        title = f"超大标题，分 {len(plain)} 行，{face}：" + "，".join(f"第{n}行「{line}」" for n, line in enumerate(plain, 1))
    marked = re.findall(r"\*\*(.+?)\*\*", " ".join(spec["lines"]))
    if marked:
        title += "；其中" + "、".join(f"「{word}」" for word in marked) + f"用{'白色' if brush else gold}"
    items.append(title)
    if spec["sub"]:
        items.append(f"标题下一行{gold}粗体字：「{spec['sub']}」")
    if spec["en"]:
        items.append(f"再下一行很小的浅灰色英文，字距拉开：「{spec['en'].upper()}」")
    lines += [f"{number_}. {item}" for number_, item in enumerate(items, 1)]
    lines.append(("排版：文字集中在画面上半部分、水平居中；画面主体在下半部分；" if side == "vertical"
                  else "排版：文字集中在画面上方、水平居中；画面主体在下方，可以左右铺开；") + "所有文字离画面边缘至少 8%。")
    lines.append("不要水印、二维码、logo，不要任何多余的文字。")
    return "\n".join(lines)


def words_of(spec: Dict[str, Any]) -> Dict[str, Any]:
    """What an AI cover was asked to say and show: a cover drawn before any of it changed is out of date."""
    return {key: spec[key] for key in ("lines", "sub", "tag", "en", "visual", "look", "font", "accent")}


def project_of(film: Path) -> Tuple[Path, str]:
    """The project a film belongs to and its episode-folder path: <项目>/剧集/<EP>/制作成果/成片/<片>.mp4."""
    folders = film.parents
    if (len(folders) < 5 or film.parent.name != "成片" or folders[1].name not in ("制作成果", "production")
            or folders[3].name not in ("剧集", "episodes") or not re.fullmatch(r"EP\d{3,}", folders[2].name, re.IGNORECASE)):
        raise Problem("AI 封面的图要存进项目的 剧集/<EP001 这样的集>/制作成果/ 里，可这条成片不在 <项目>/剧集/EP001/制作成果/成片/ 下："
                      "把成片放到那里再来，或者这条用程序版（封面.json 写 \"ai\": false）。")
    return folders[4], f"{folders[3].name}/{folders[2].name}/{folders[1].name}"


def ai_targets(film: Path) -> Dict[str, Tuple[str, Path]]:
    """Where each AI cover is written by the production skill: (path inside the project, full path)."""
    root, episode = project_of(film)
    return {side: (f"{episode}/封面/{AI_NAME[side]}", root / episode / "封面" / AI_NAME[side]) for side in SIDES}


def fit_picture(source: Path, dest: Path, size: Tuple[int, int]) -> None:
    """A picture filled out to exactly this size, cropped evenly if its shape is a little off."""
    width, height = size
    ffmpeg(["-loglevel", "error", "-i", str(source), "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}",
            "-frames:v", "1", "-pix_fmt", "yuvj444p", "-q:v", "2", str(dest)], f"存 {dest.name}")


# ------------------------------------------------------------------- reading a picture
def gray_pixels(picture: Path, size: Tuple[int, int]) -> Tuple[int, int, bytes]:
    """The picture shrunk to SCAN pixels wide, in grey levels (0-255), row after row."""
    wide = SCAN
    tall = max(2, round(size[1] * SCAN / size[0]))
    done = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(picture), "-vf",
                           f"scale={wide}:{tall}:flags=area,format=gray", "-frames:v", "1", "-f", "rawvideo", "-"],
                          capture_output=True)
    if done.returncode != 0 or len(done.stdout) != wide * tall:
        raise Problem(f"读不出 {picture.name} 的像素。")
    return wide, tall, done.stdout


def bright_box(wide: int, tall: int, pixels: bytes) -> Optional[Box]:
    """Where the bright things on a dark picture are, as shares of it (left, top, width, height), with a
    margin round them; None when the picture is not dark enough to tell (its edges are bright too)."""
    edge = sorted(pixels[:wide] + pixels[-wide:] + bytes(pixels[row * wide] for row in range(tall))
                  + bytes(pixels[row * wide + wide - 1] for row in range(tall)))
    if edge[len(edge) // 2] > DARK_GROUND:
        return None
    # A row or a column counts when at least two of its pixels are bright: a lone speck is noise.
    rows = [row for row in range(tall) if sum(1 for value in pixels[row * wide:(row + 1) * wide] if value > BRIGHT) >= 2]
    columns = [column for column in range(wide)
               if sum(1 for row in range(tall) if pixels[row * wide + column] > BRIGHT) >= 2]
    if not rows or not columns:
        return None
    left, right = columns[0] / wide - MARGIN, (columns[-1] + 1) / wide + MARGIN
    top, bottom = rows[0] / tall - MARGIN, (rows[-1] + 1) / tall + MARGIN
    left, top, right, bottom = max(0.0, left), max(0.0, top), min(1.0, right), min(1.0, bottom)
    if right - left < SMALLEST_CROP or bottom - top < SMALLEST_CROP:
        return None
    return (round(left, 4), round(top, 4), round(right - left, 4), round(bottom - top, 4))


def ground_of(picture: Path, crop: Optional[Sequence[float]]) -> str:
    """The colour round the edge of the part of a picture a poster shows, so that part melts into the
    ground it is laid on; darkened when it is light, so light words never sit on a light ground."""
    left, top, wide, tall = crop or (0.0, 0.0, 1.0, 1.0)
    side = 32
    done = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(picture), "-vf",
                           f"crop=iw*{wide}:ih*{tall}:iw*{left}:ih*{top},scale={side}:{side}:flags=area,format=rgb24",
                           "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True)
    if done.returncode != 0 or len(done.stdout) != side * side * 3:
        return "#050607"
    pixels = done.stdout
    edge = [index for index in range(side * side) if index < side or index >= side * (side - 1)
            or index % side in (0, side - 1)]
    red, green, blue = (sorted(pixels[index * 3 + channel] for index in edge)[len(edge) // 2] for channel in range(3))
    light = 0.299 * red + 0.587 * green + 0.114 * blue
    if light > DARK_GROUND:
        # Keep the hue, take it down to a dark one.
        red, green, blue = (round(value * DARK_GROUND / light * 0.5) for value in (red, green, blue))
    return f"#{red:02x}{green:02x}{blue:02x}"


# ------------------------------------------------------------------- candidates
def windows(seconds: float, count: int) -> List[Tuple[float, float]]:
    """Where to look for candidates: `count` stretches spread evenly over the film, clear of its first
    and last moments (a title still landing, the end card); each as (start, length)."""
    lead = min(0.5, seconds * 0.05)
    tail = min(1.5, seconds * 0.08)
    step = max(0.1, seconds - lead - tail) / count
    length = min(WINDOW, step)
    return [(round(max(0.0, lead + step * (index + 0.5) - length / 2), 3), round(length, 3)) for index in range(count)]


def scene_moments(film: Dict[str, Any], count: int) -> List[float]:
    """A motion-graphics film's candidates: every scene once everything in it is on screen, the end card
    left out; then, while there is room, the moments just after each mark in a scene lands (a chart
    reads differently with each ring and note). At most `count`, spread over the whole film."""
    settled: List[float] = []
    marks: List[float] = []
    for scene in film.get("scenes", []):
        if scene.get("type") == "end":
            continue
        start, end = float(scene["start"]), float(scene["end"])
        appeared = sorted(at for at, _, _ in animation.scene_cues(scene) if at > start + 0.3)
        settled.append(animation.within(appeared[-1] + 0.6 if appeared else start + (end - start) * 0.6, start, end))
        marks += [animation.within(at + 0.6, start, end) for at in appeared[:-1]]
    if len(settled) >= count:
        step = len(settled) / count
        return sorted(settled[int(index * step)] for index in range(count))
    chosen = sorted(set(settled))
    spare = [moment for moment in sorted(set(marks)) if all(abs(moment - taken) >= 0.8 for taken in chosen)]
    if len(spare) > count - len(chosen):
        step = len(spare) / (count - len(chosen))
        spare = [spare[int(index * step)] for index in range(count - len(chosen))]
    return sorted(chosen + spare)


# ------------------------------------------------------------------- media
def need_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise Problem("没装 ffmpeg：封面要用它取画面、出图。先照体检脚本说的装上。")


def probe(path: Path) -> Dict[str, Any]:
    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        info = json.loads(done.stdout or "{}")
        stream = info["streams"][0]
        size = (int(stream["width"]), int(stream["height"]))
    except (ValueError, KeyError, IndexError, TypeError):
        raise Problem(f"读不出 {path.name} 的画面：文件坏了，或者不是视频和图片。") from None
    rate = str(stream.get("r_frame_rate") or "30/1").split("/")
    fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 and float(rate[1] or 0) else 30.0
    try:
        seconds: Optional[float] = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        seconds = None
    return {"size": size, "fps": fps if 1 <= fps <= 240 else 30.0, "seconds": seconds}


def ffmpeg(arguments: Sequence[str], what: str) -> subprocess.CompletedProcess:
    done = subprocess.run(["ffmpeg", "-hide_banner", "-y", *arguments], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise Problem(f"{what}失败：" + ((done.stderr or "").strip().splitlines() or ["ffmpeg 没说原因"])[-1])
    return done


def same_film(film: Path, seconds: float, clean: Path) -> None:
    """The film without subtitles has to be the same cut, or its seconds would show other moments."""
    if not clean.is_file():
        raise Problem(f"找不到不带字幕的那一版：{clean}")
    other = probe(clean)["seconds"]
    if other is None or abs(other - seconds) > SAME_CUT:
        raise Problem(f"{clean.name} 有 {other or 0:.2f} 秒，成片 {seconds:.2f} 秒：不是同一版剪辑。"
                      "成片重新剪过的话，用和它同一次渲出来的那一版。")


def grab(film: Path, at: float, dest: Path) -> Path:
    ffmpeg(["-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(film), "-frames:v", "1", str(dest)], f"取成片第 {at:g} 秒的画面")
    if not dest.is_file():
        raise Problem(f"成片第 {at:g} 秒取不出画面。")
    return dest


def steadiest(film: Path, start: float, length: float, fps: float, dest: Path) -> float:
    """The most typical frame of a stretch (ffmpeg's thumbnail filter passes over flashes and blurs), and its second."""
    frames = max(2, int(round(length * fps)))
    done = ffmpeg(["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(film), "-vf", f"thumbnail=n={frames},showinfo",
                   "-frames:v", "1", "-q:v", "2", str(dest)], "挑候选画面")
    if not dest.is_file():
        raise Problem(f"第 {start:g} 秒那一段挑不出画面。")
    found = re.findall(r"pts_time:\s*([\d.]+)", done.stderr or "")
    return round(start + (float(found[-1]) if found else length / 2), 2)


def clean_copy(animation_dir: Path, work: Path) -> Path:
    """A copy of an animation folder whose page draws no captions, no scene headings and no sound:
    on a cover the title takes the heading's place, and a caption line would sit under its words."""
    copy = work / "画面"
    if copy.exists():
        shutil.rmtree(copy)

    def leave_out(_: str, names: List[str]) -> List[str]:
        return [name for name in names
                if name.lower().endswith(SOUND_FILES) or name.startswith("snapshots") or name == ".hyperframes"]

    shutil.copytree(animation_dir, copy, ignore=leave_out)
    page = copy / "index.html"
    text = re.sub(r'\s*<audio id="mix"[^>]*></audio>', "", page.read_text(encoding="utf-8"))
    if "</head>" not in text:
        raise Problem(f"{animation_dir} 的 index.html 不是 animation.py setup 放的那一份。")
    text = text.replace("</head>", "  <style>.mk-captions, .mk-header { display: none !important; }</style>\n  </head>", 1)
    page.write_text(text, encoding="utf-8")
    return copy


def stills(folder: Path, times: Sequence[float], out: Path) -> List[Path]:
    """Frames of a page at these seconds, through the renderer. It empties `out` first."""
    done = animation.run_tool(folder, ["snapshot", "--at", ",".join(f"{t:g}" for t in times), "-o", str(out),
                                       "--no-end", "--describe", "false"], timeout=1800)
    if done.returncode != 0:
        raise Problem("截图失败：" + (animation.plain(done.stderr or done.stdout).strip().splitlines() or ["未知原因"])[-1])
    shots = sorted(out.glob("frame-*.png"))
    if len(shots) != len(times):
        raise Problem(f"要截 {len(times)} 张，只截出 {len(shots)} 张。")
    return shots


def clean_stills(animation_dir: Path, times: Sequence[float], work: Path) -> Dict[float, Path]:
    copy = clean_copy(animation_dir, work)
    try:
        shots = stills(copy, sorted(times), work / ".截图")
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    return dict(zip(sorted(times), shots))


# ------------------------------------------------------------------- commands
def cmd_frames(args: argparse.Namespace) -> None:
    need_ffmpeg()
    film: Path = args.film.resolve()
    if not film.is_file():
        raise Problem(f"找不到成片：{film}")
    info = probe(film)
    seconds = info["seconds"] or 0.0
    if seconds <= 0:
        raise Problem(f"量不出 {film.name} 有多长。")
    clean: Optional[Path] = args.clean.resolve() if args.clean else None
    if clean is not None:
        same_film(film, seconds, clean)
    out: Path = args.out.resolve()
    target = out / "候选帧"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    rows: List[Dict[str, Any]] = []
    sheet: Optional[Path] = None
    if args.animation:
        built = animation.read_film(args.animation.resolve())
        times = scene_moments(built, args.count)
        if not times:
            raise Problem("动画里没有能当封面的画面（只有片尾）。")
        shots = clean_stills(args.animation.resolve(), times, out)
        for index, at in enumerate(sorted(shots), 1):
            dest = target / f"{index:02d}.png"
            shutil.move(str(shots[at]), str(dest))
            rows.append({"n": index, "at": at, "file": dest.name})
        # The renderer splits a long run of stills over several sheets: keep them all, in order.
        for index, made in enumerate(sorted((out / ".截图").glob("contact-sheet*.jpg")), 1):
            kept = target / ("汇总.jpg" if index == 1 else f"汇总-{index}.jpg")
            shutil.move(str(made), str(kept))
            sheet = sheet or kept
        shutil.rmtree(out / ".截图", ignore_errors=True)
    else:
        # A film with burned-in subtitles has the same film without them beside it (the edit skill
        # keeps 成片-未混音.mp4): its frames make cleaner covers, at the same seconds.
        looked = clean or film
        for index, (start, length) in enumerate(windows(seconds, args.count), 1):
            dest = target / f"{index:02d}.jpg"
            rows.append({"n": index, "at": steadiest(looked, start, length, info["fps"], dest), "file": dest.name})
        upright = info["size"][1] > info["size"][0]
        columns = 6 if upright else 4
        grid = f"{columns}x{math.ceil(len(rows) / columns)}"
        sheet = target / "汇总.jpg"
        ffmpeg(["-loglevel", "error", "-start_number", "1", "-i", str(target / "%02d.jpg"),
                "-vf", f"scale={240 if upright else 400}:-2,tile={grid}:padding=8:margin=8:color=0x111111",
                "-frames:v", "1", "-q:v", "3", str(sheet)], "拼汇总图")
    (target / "候选帧.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    spec = out / SPEC
    started = not spec.exists()
    if started:
        starter: Dict[str, Any] = {"film": film.as_posix(), "title": [], "sub": "", "en": "", "tag": "", "visual": "",
                                   "at": None}
        if args.animation:
            starter["animation"] = args.animation.resolve().as_posix()
        if clean is not None:
            starter["clean"] = clean.as_posix()
        spec.write_text(json.dumps(starter, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sheets = sorted(path.name for path in target.glob("汇总*.jpg"))
    print(f"候选画面 {len(rows)} 张在 {target}" + (f"，汇总图 {'、'.join(sheets)}（从左到右、从上到下就是 1、2、3……）" if sheets else "") + "：")
    for row in rows:
        print(f"  {row['n']:02d}  第 {row['at']:.2f} 秒")
    print(f"在 {spec} 里写好标题、钩子和 visual（AI 封面画什么），挑一张把秒数写进 at（程序版的画面、实拍片 AI 封面的人物参考），"
          f"然后运行 cover.py prompt {out} 出 AI 封面的任务单；不花钱就直接 cover.py make {out}。")
    emit({"ok": True, "folder": str(target), "sheet": str(sheet) if sheet else None, "frames": rows,
          "spec": str(spec), "spec_started": started, "film": str(film), "seconds": round(seconds, 3)})


def prepare_page(page: Path) -> None:
    """The cover page's folder: its fonts and animation library, copied from the shared install."""
    install = animation.ensure_install()
    modules = Path(install["home"]) / "node_modules"
    (page / "lib" / "fonts").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(modules / "gsap" / "dist" / "gsap.min.js", page / "lib" / "gsap.min.js")
    for package, name in animation.FONTS:
        if name in FONT_FILES:
            shutil.copyfile(modules / package / name, page / "lib" / "fonts" / name)


def load_spec(folder: Path) -> Tuple[Path, Dict[str, Any]]:
    """封面.json read and checked against the film it names."""
    source = folder / SPEC
    if not source.is_file():
        raise Problem(f"找不到 {source}：先运行 cover.py frames，再照 references/cover.md 写好它。")
    try:
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
    except ValueError as error:
        raise Problem(f"{SPEC} 不是合法的 JSON：{error}") from error
    need_ffmpeg()
    film = located(folder, raw.get("film") if isinstance(raw, dict) else None, "film（成片）")
    if not film.is_file():
        raise Problem(f"film 指的成片找不到：{film}")
    seconds = probe(film)["seconds"]
    spec = check_spec(raw, folder, seconds)
    if spec["clean"] is not None and seconds is not None:
        same_film(film, seconds, spec["clean"])
    return film, spec


def cmd_prompt(args: argparse.Namespace) -> None:
    """Write the production jobs for the AI covers: one gpt-image-2 picture per cover, for the production
    skill to price, show, have confirmed and run. Nothing is generated here."""
    folder: Path = args.folder
    film, spec = load_spec(folder)
    if not spec["visual"]:
        raise Problem("visual 还没写：一两句话写这张封面的核心画面，例如「一根放量的大阳线被金色光圈圈住，下面的成交量柱特别高」。")
    asked = [side for side in SIDES if spec["sides"][side]["ai"]]
    if not asked:
        raise Problem("两张都写了 \"ai\": false：不出 AI 图，直接运行 cover.py make 出程序版。")
    root, episode = project_of(film)
    targets = ai_targets(film)
    work = folder / AI_FOLDER
    work.mkdir(exist_ok=True)
    bindings: List[Dict[str, Any]] = []
    if spec["reference"]:
        # The people on an AI cover of footage are the film's own: a frame of it goes along as the reference.
        moment = next((spec["sides"][side]["at"] for side in SIDES if spec["sides"][side]["at"] is not None), None)
        if moment is None:
            raise Problem("要拿片子里的一帧给 AI 当人物参考：在 封面.json 写 at（从候选里挑人物最清楚的那一刻）。")
        frame = work / "参考帧.png"
        if frame.exists():
            frame.unlink()
        if spec["animation"] is not None:
            shutil.copyfile(clean_stills(spec["animation"], [moment], folder)[moment], frame)
            shutil.rmtree(folder / ".截图", ignore_errors=True)
        else:
            grab(spec["clean"] or film, moment, frame)
        try:
            inside = frame.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            raise Problem(f"封面目录 {folder} 不在项目 {root} 里，参考帧交不给生产技能：封面目录用 <项目>/制作/封面。") from None
        bindings = [{"slot_id": "REF-COVER", "order": 1, "path": inside, "label": "片中画面", "role": "reference_image",
                     "may_control": ["人物长相", "发型和服装", "场景"], "must_not_control": ["文字", "构图"]}]
    label = PurePosixPath(episode).parts[1]
    jobs: Dict[str, Tuple[str, Path]] = {}
    for side in asked:
        width, height = AI_SIZE[side]
        job_id = f"{label}-cover-{'3x4' if side == 'vertical' else '4x3'}"
        job: Dict[str, Any] = {"schema_version": "1.0", "job_id": job_id, "modality": "image", "adapter": "gpt-image-2",
                               "prompt": ai_prompt(spec, side, bool(bindings)), "outputs": [targets[side][0]],
                               "parameters": {"width": width, "height": height, "quality": "high", "prompt_language": "zh-CN"},
                               "overwrite": True}
        if bindings:
            job["reference_bindings"] = bindings
        path = work / f"{SUFFIX[side]}.job.json"
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        jobs[side] = (job_id, path)
    (work / "words.json").write_text(json.dumps(words_of(spec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tool = PRODUCE if PRODUCE.is_dir() else Path("<生产技能目录>") / "scripts"
    price = round(AI_PRICE * len(jobs), 2)
    print(f"AI 封面的出图任务单写好了：{len(jobs)} 张，每张 {AI_PRICE:.2f} 元（yoyo 中转价），共 {price:.2f} 元。还没出图，也没花钱。")
    print("照生产技能的硬闸门一张一张走：报价、prepare 预览、请创作者确认、confirm、run。报价和预览：")
    print(f"  python3 \"{tool / 'provider_adapters.py'}\" --quote " + " ".join(f'"{path}"' for _, path in jobs.values()))
    for job_id, path in jobs.values():
        print(f"  python3 \"{tool / 'production_tool.py'}\" prepare \"{root}\" --job \"{path}\"   （任务号 {job_id}）")
    print(f"出完运行 cover.py make \"{folder}\"：有 AI 图的那张就用它，没出的、或者出完又改过字的，那张用程序版。")
    emit({"ok": True, "jobs": {side: str(path) for side, (_, path) in jobs.items()},
          "job_ids": {side: job_id for side, (job_id, _) in jobs.items()},
          "outputs": {side: str(targets[side][1]) for side in asked}, "price_yuan": price, "project": str(root),
          "reference": bindings[0]["path"] if bindings else None})


def draw_with_program(folder: Path, film: Path, spec: Dict[str, Any], wanted: Sequence[str],
                      covers: Dict[str, Path]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Optional[List[float]]]]:
    """The free covers, drawn by the page: for the sides with no AI picture to use."""
    for side in wanted:
        if spec["sides"][side]["at"] is None and spec["sides"][side]["image"] is None:
            raise Problem(f"{NAME[side]}封面要用程序版，可还没定画面：写 at（成片第几秒，从 cover.py frames 的候选里挑）或者 image。")
    page = folder / "页面"
    prepare_page(page)
    for old in page.glob("pic-*"):
        old.unlink()

    # Each cover's picture as a file beside the page, and what the page is told about it.
    moments = sorted({spec["sides"][side]["at"] for side in wanted if spec["sides"][side]["at"] is not None})
    drawn = clean_stills(spec["animation"], moments, folder) if spec["animation"] and moments else {}
    sides: Dict[str, Dict[str, Any]] = {}
    found: Dict[str, Optional[List[float]]] = {}
    for side in wanted:
        own = spec["sides"][side]
        name = f"pic-{side}.png"
        dest = page / name
        if own["image"] is not None:
            ffmpeg(["-loglevel", "error", "-i", str(own["image"]), "-frames:v", "1", str(dest)], f"读{NAME[side]}封面的图")
        elif own["at"] in drawn:
            shutil.copyfile(drawn[own["at"]], dest)
        else:
            grab(spec["clean"] or film, own["at"], dest)
        size = probe(dest)["size"]
        crop = own["crop"]
        if own["style"] == "poster" and crop is None:
            box = bright_box(*gray_pixels(dest, size))
            crop = list(box) if box else None
        found[side] = crop
        sides[side] = side_data(side, own, name, size, crop, ground_of(dest, crop) if own["style"] == "poster" else "")
    shutil.rmtree(folder / ".截图", ignore_errors=True)
    (page / "cover-data.js").write_text(page_data(spec, sides), encoding="utf-8")

    ready, note = animation.ensure_browser(page)
    if not ready:
        raise Problem(note or "渲染用的浏览器还没准备好。")
    for side in wanted:
        (page / "index.html").write_text(page_for(side), encoding="utf-8")
        shot = stills(page, [STILL_AT], page / ".出图")[0]
        # Full colour detail (4:4:4): the highlighted words' coloured edges stay sharp.
        ffmpeg(["-loglevel", "error", "-i", str(shot), "-pix_fmt", "yuvj444p", "-q:v", "2", str(covers[side])],
               f"存{NAME[side]}封面")
        size = probe(covers[side])["size"]
        if size != SIZE[side]:
            raise Problem(f"{NAME[side]}封面出来是 {size[0]}×{size[1]}，应该是 {SIZE[side][0]}×{SIZE[side][1]}。")
    shutil.rmtree(page / ".出图", ignore_errors=True)
    return sides, found


def cmd_make(args: argparse.Namespace) -> None:
    """Both covers beside the film: the AI picture where there is a current one, the program's version elsewhere."""
    folder: Path = args.folder
    film, spec = load_spec(folder)
    covers = cover_paths(film)
    made: Dict[str, str] = {}
    notes: List[str] = []
    wanted_ai = [side for side in SIDES if spec["sides"][side]["ai"]]
    targets: Dict[str, Tuple[str, Path]] = {}
    if wanted_ai:
        try:
            targets = ai_targets(film)
        except Problem as error:
            notes.append(f"AI 封面出不了：{error}")
    asked = None
    words_file = folder / AI_FOLDER / "words.json"
    if words_file.is_file():
        try:
            asked = json.loads(words_file.read_text(encoding="utf-8"))
        except ValueError:
            asked = None
    for side in wanted_ai:
        if side not in targets:
            continue
        picture = targets[side][1]
        if not picture.is_file():
            notes.append(f"{NAME[side]}封面：还没有 AI 图（没出，或者这条不花钱），用的是程序版。")
        elif asked != words_of(spec):
            notes.append(f"{NAME[side]}封面：AI 图出完以后，封面.json 里的字或画面改过，图上还是旧的，这张先用程序版。"
                         "要 AI 版就重新运行 cover.py prompt，再出一次图。")
        else:
            fit_picture(picture, covers[side], SIZE[side])
            made[side] = "ai"
    rest = [side for side in SIDES if side not in made]
    sides: Dict[str, Dict[str, Any]] = {}
    found: Dict[str, Optional[List[float]]] = {}
    if rest:
        sides, found = draw_with_program(folder, film, spec, rest, covers)
        made.update({side: sides[side]["style"] for side in rest})

    print(f"封面出好了，在成片旁边：{covers['vertical'].name}（1080×1440）、{covers['horizontal'].name}（1440×1080）。")
    for note in notes:
        print(note)
    for side in SIDES:
        if made[side] == "ai":
            print(f"{NAME[side]}封面：用的是 AI 出的图。")
        elif made[side] == "poster":
            data = sides[side]
            where = "整张画面" if data["crop"] == [0.0, 0.0, 1.0, 1.0] else "画面里的 [{:.2f}, {:.2f}, {:.2f}, {:.2f}] 这一块".format(*data["crop"])
            how = "自动找的" if spec["sides"][side]["crop"] is None else "你写的"
            print(f"{NAME[side]}封面：程序版海报式，核心画面用了{where}（{how}；不对就在 封面.json 里写 crop）。")
        elif sides[side]["mode"] == "card":
            print(f"{NAME[side]}封面：程序版画面式，画面和封面方向不一样，做成卡片放在{'字的下面' if side == 'vertical' else '字的旁边'}。")
    if "ai" in made.values():
        expected = "、".join(f"{what}「{text}」" for what, text in (
            ("标签", spec["tag"]), ("标题", " / ".join(line.replace("**", "") for line in spec["lines"])),
            ("钩子", spec["sub"]), ("英文", spec["en"].upper())) if text)
        print(f"AI 画的字要逐字核对：{expected}。有一个字不对，就重出那一张（再确认一次、{AI_PRICE:.2f} 元）；"
              "还不对，就给那一张写 \"ai\": false，再运行一次 make 用程序版。")
    print("两张都打开看一遍：字对不对、主体有没有被切掉、缩到手机格子那么小还读不读得出标题。不满意改 封面.json 再运行。")
    emit({"ok": True, "covers": {side: str(path) for side, path in covers.items()}, "sources": made, "crops": found,
          "film": str(film)})


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="给成片做竖 3:4、横 4:3 两张封面：AI 整张出图（走生产技能确认），程序版免费兜底。")
    sub = parser.add_subparsers(dest="command", required=True)
    frames = sub.add_parser("frames", help="摆出候选画面和汇总图，并起一份 封面.json")
    frames.add_argument("film", type=Path, help="成片 MP4")
    frames.add_argument("--out", type=Path, required=True, help="封面目录，一般是 <项目>/制作/封面")
    frames.add_argument("--animation", type=Path, help="图文动效片的动画目录：从动画重新截，不带字幕")
    frames.add_argument("--clean", type=Path, help="剧情实拍片：同一版剪辑不带字幕的那条（剪辑技能留下的 成片-未混音.mp4）")
    frames.add_argument("--count", type=int, default=CANDIDATES, help="摆几张候选（默认 12）")
    prompt = sub.add_parser("prompt", help="写 AI 封面的出图任务单（交生产技能报价、确认、出图）")
    prompt.add_argument("folder", type=Path, help="封面目录（里面有 封面.json）")
    make = sub.add_parser("make", help="出两张封面放在成片旁边：有 AI 图就用，没有就用程序版")
    make.add_argument("folder", type=Path, help="封面目录（里面有 封面.json）")
    args = parser.parse_args()
    try:
        if args.command == "frames":
            if not 1 <= args.count <= 30:
                raise Problem("--count 在 1 到 30 之间。")
            if args.animation and not args.animation.is_dir():
                raise Problem(f"找不到动画目录：{args.animation}")
            if args.animation and args.clean:
                raise Problem("--animation 和 --clean 只给一个：图文动效片给动画目录，剧情实拍片给不带字幕的那一版。")
            cmd_frames(args)
        else:
            if not args.folder.is_dir():
                raise Problem(f"找不到封面目录：{args.folder}")
            args.folder = args.folder.resolve()
            (cmd_prompt if args.command == "prompt" else cmd_make)(args)
    except (Problem, animation.Problem) as error:
        emit({"ok": False, "error": str(error)}, 1)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        emit({"ok": False, "error": f"{type(error).__name__}: {error}"}, 1)


if __name__ == "__main__":
    main()
