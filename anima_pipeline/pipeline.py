"""日本語 -> Anima プロンプトの変換パイプライン。

  1. 日本語 -> 英語に翻訳          (ChatClient.translate_ja_en)
  2. Gemma が anima_rules.txt の順序で Anima タグ列(+任意の自然文)を生成する
  3. 辞書でスナップ補正            (Normalizer.snap_tag: 別名->正規形・近い実在タグへ)
  4. 検出したキャラ/作品名・アーティスト名を、キャラブロックの頭へ注入する
     (キャラ/作品名はそのまま、アーティストは @ 付き)
  5. 文字列へ整形                  (constrain.render: 並べ替えない)
  6. ネガティブプロンプト(config.NEGATIVE_PROMPT)を結果に添える

使うサーバーは Gemma(config.CHAT_URL)だけ。手で調整するルールは
prompts/anima_rules.txt にあり、実行時に読み込む。JSON 出力の指示はコードが付加し、
ルールファイルには保存しない。
"""
from __future__ import annotations
import json
import re
from pathlib import Path

import config
import constrain
from postprocess import Normalizer
from generate import ChatClient


def load_rules() -> str:
    return Path(config.RULES_PROMPT).read_text(encoding="utf-8").strip()


def load_fewshot() -> list[dict]:
    """prompts/fewshot.txt のお手本を読み込む。各例は DESCRIPTION / TAGS / NATURAL の
    ラベルを持ち、例同士は 3 個以上のダッシュだけの行 (---) で区切る。'#' で始まる行は
    コメント。DESCRIPTION と TAGS が無いブロックは飛ばす。
    返り値: [{"description": str, "tags": [str, ...], "natural": str}, ...]。

    実行時の本番メッセージとまったく同じ user 形式("DESCRIPTION (English): ...")で
    各例を提示し、assistant 側は本番と同じ JSON({"tags": [...], "natural": "..."})にする
    (= 出力の「形」を手本でそのまま見せる)。USE_FEWSHOT=False かファイルが無ければ空。"""
    if not getattr(config, "USE_FEWSHOT", True):
        return []
    p = Path(config.FEWSHOT)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    label_re = re.compile(r"(?i)^\s*(DESCRIPTION|TAGS|NATURAL)\s*:\s*(.*)$")
    examples: list[dict] = []
    for block in re.split(r"(?m)^\s*-{3,}\s*$", text):
        fields: dict[str, list[str]] = {}
        current: str | None = None
        for line in block.splitlines():
            if line.lstrip().startswith("#"):
                continue
            m = label_re.match(line)
            if m:
                current = m.group(1).upper()
                fields.setdefault(current, [])
                if m.group(2).strip():
                    fields[current].append(m.group(2).strip())
            elif current and line.strip():
                fields[current].append(line.strip())
        desc = " ".join(fields.get("DESCRIPTION", [])).strip()
        tags: list[str] = []
        for line in fields.get("TAGS", []):
            for part in line.split(","):
                v = part.strip()
                if v.startswith("-"):
                    v = v[1:].strip()
                if v:
                    tags.append(v)
        natural = " ".join(fields.get("NATURAL", [])).strip()
        if not desc or not tags:
            continue  # 必須(説明文・出力タグ)が無いブロックは飛ばす
        examples.append({"description": desc, "tags": tags, "natural": natural})
    return examples


def _loads_json_obj(text: str):
    """Gemma の出力から JSON オブジェクトを取り出す。素直に解析できないときは、
    コードフェンスを剥がす / 本文中の {...} を全部拾って本体らしいものを選ぶ、という
    保険を試す(思考文の後ろに JSON が続く場合や、断片が複数ある場合のため)。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if text.startswith("```"):                      # ```json ... ``` を剥がす
        body = text.strip("`")
        nl = body.find("\n")
        if nl != -1:
            body = body[nl + 1:]
        try:
            return json.loads(body.strip())
        except json.JSONDecodeError:
            pass
    candidates = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        candidates.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    if not candidates:
        return None
    for obj in reversed(candidates):
        if "tags" in obj or "natural" in obj:
            return obj
    return candidates[-1]


def _load_alias_map_with_canon(alias_path, tags_jsonl_path) -> dict:
    """別名表を読み込み、別名を持たない正規タグも完全一致できるよう、anima_tags.jsonl
    の正規タグを identity(自分自身への対応)として補う。これで有効なタグが誤って
    ファジーに寄せられるのを防ぐ。"""
    with open(alias_path, encoding="utf-8") as f:
        amap = dict(json.load(f))
    p = Path(tags_jsonl_path)
    if p.exists():
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tag = rec.get("tag")
                if tag:
                    amap.setdefault(tag, tag)
    return amap


_DIRECTIVE_JSON = (
    "\n\n--- OUTPUT FORMAT ---\n"
    'Respond with ONLY a JSON object: {"tags": [...], "natural": "..."}.\n'
    '"tags": an ordered array of the Anima tags, in the exact order described '
    "in the rules above (quality/meta/rating, then count, then per-character "
    "blocks, then pose, camera, background, lighting).\n"
    '"natural": ONE short English sentence only if the scene is complex, '
    'otherwise an empty string "". Pronouns are allowed in "natural".\n'
    "No JSON anywhere else, no labels, no markdown, no explanation."
)

# スナップ補正で「触らない」特殊トークン:
#   @アーティスト名 / 重み付き (tag:1.3) / score_9 などの score タグ。
_WEIGHT_RE = re.compile(r"^\(.+:\s*\d+(?:\.\d+)?\s*\)$")
_SCORE_RE = re.compile(r"^score_\d+$", re.IGNORECASE)


class AnimaPipeline:
    def __init__(self, translate: bool | None = None):
        self.translate = config.TRANSLATE_FIRST if translate is None else translate
        self.rules = load_rules()
        self.fewshot = load_fewshot()

        # スナップ補正用の正規化器(別名表 + 正規タグの identity)。ファジーは高い
        # 閾値にし、完全一致が無い短いタグだけが寄せられる。
        amap = _load_alias_map_with_canon(config.ALIAS_MAP, config.TAGS_JSONL)
        self.norm = Normalizer(amap, use_fuzzy=True,
                               fuzzy_cutoff=getattr(config, "SNAP_FUZZY_CUTOFF", 90))

        self.chat = ChatClient()

        # 一般語(=一般タグ)集合。検出したキャラ/作品/アーティスト名が一般語と
        # 衝突する場合に、誤検出として除外するために使う。
        self._general_known: set[str] = set(self.norm.alias_map.keys())
        self._general_known |= {v.lower() for v in self.norm.alias_map.values()}

        # アーティスト辞書(任意): プロンプト本文中のアーティスト名を完全一致で拾う。
        # 誤検出を避けるためファジーは使わない。ファイルが無ければ無効になる。
        self.artist_norm: Normalizer | None = None
        if getattr(config, "USE_ARTIST_DICT", False) and config.ARTIST_ALIAS_MAP.exists():
            self.artist_norm = Normalizer.from_file(config.ARTIST_ALIAS_MAP,
                                                    use_fuzzy=False)

        # キャラ / 作品(シリーズ)辞書(任意): 本文中のキャラ名・作品名を完全一致で拾う。
        # 同じく完全一致のみ。ファイルが無ければ無効になる。
        self.char_norm: Normalizer | None = None
        if getattr(config, "USE_CHAR_DICT", False) and config.CHAR_ALIAS_MAP.exists():
            self.char_norm = Normalizer.from_file(config.CHAR_ALIAS_MAP,
                                                  use_fuzzy=False)

    # -- プロンプト組み立て ---------------------------------------------------
    def _system_prompt(self) -> str:
        return self.rules + _DIRECTIVE_JSON

    def _messages(self, english: str) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": self._system_prompt()}]
        # お手本(few-shot): 本番と同じ user 形式 + assistant 側は本番と同じ JSON。
        for ex in self.fewshot:
            msgs.append({"role": "user",
                         "content": f"DESCRIPTION (English): {ex['description']}"})
            msgs.append({"role": "assistant",
                         "content": json.dumps({"tags": ex["tags"],
                                                "natural": ex.get("natural", "")},
                                               ensure_ascii=False)})
        msgs.append({"role": "user", "content": f"DESCRIPTION (English): {english}"})
        return msgs

    # -- スナップ補正 ---------------------------------------------------------
    def _snap(self, tags) -> list[str]:
        """Gemma のタグ列を完璧なリストへスナップ補正する。順序は維持する。
        - @... / 重み付き / score_N はそのまま(触らない)。
        - アンダースコアはスペースへ。
        - SNAP_MAX_WORDS 以下の短いタグだけ別名表 / ファジーで実在タグへ寄せる。
          寄せ先が無ければ落とさず原形を残す(Anima は無視するだけ)。
        - それより長い説明的な句(例 "white flowy maxi dress with ...")はそのまま残す。"""
        max_words = getattr(config, "SNAP_MAX_WORDS", 4)
        out: list[str] = []
        seen: set[str] = set()

        def _push(v: str) -> None:
            v = v.strip()
            if v and v.lower() not in seen:
                seen.add(v.lower())
                out.append(v)

        for raw in (tags or []):
            if not isinstance(raw, str):
                continue
            t = raw.strip()
            if not t:
                continue
            if t.startswith("@") or _WEIGHT_RE.match(t) or _SCORE_RE.match(t):
                _push(t)
                continue
            t = re.sub(r"\s+", " ", t.replace("_", " ")).strip()
            if len(t.split()) > max_words:
                _push(t)                         # 説明句はそのまま残す
                continue
            canon = self.norm.snap_tag(t)        # 完全一致 -> ファジー
            _push(canon if canon else t)         # 寄せ先が無ければ原形を残す
        return out

    # -- 検出名 / 指定タグの注入 ---------------------------------------------
    def _inject_head(self, tags: list[str], inject: list[str]) -> list[str]:
        """inject を、先頭の静的タグ群(品質 / メタ / レーティング / 人数)の直後へ
        差し込む = キャラブロックの頭。既出のものは重複させない。"""
        if not inject:
            return tags
        static = {t.lower() for t in config.STATIC_TAGS}
        i = 0
        while i < len(tags) and tags[i].lower() in static:
            i += 1
        existing = {t.lower() for t in tags}
        add: list[str] = []
        for t in inject:
            if t.lower() not in existing:
                existing.add(t.lower())
                add.append(t)
        return tags[:i] + add + tags[i:]

    # -- 実行 -----------------------------------------------------------------
    def run(self, ja_prompt: str, extra_tags: list[str] | None = None) -> dict:
        english = self.chat.translate_ja_en(ja_prompt) if self.translate else ja_prompt

        content = self.chat.chat(self._messages(english))
        obj = _loads_json_obj(content)
        if not isinstance(obj, dict):
            obj = {"tags": [], "natural": ""}

        tags = self._snap(obj.get("tags", []))

        # ユーザー指定タグ(--tags)、本文中で検出したキャラ/作品名、アーティスト名(@付き)を
        # キャラブロックの頭へ注入する。仕様の順序(名前 -> 作品 -> @アーティスト)に寄せて並べる。
        inject: list[str] = []
        for t in (extra_tags or []):
            inject.extend(self._snap([t]))
        inject.extend(self._detect_chars(english))      # キャラ名・作品名(@なし)
        for canon in self._detect_artists(english):
            inject.append(canon if canon.startswith("@") else "@" + canon)
        tags = self._inject_head(tags, inject)

        prompt = constrain.render({"tags": tags, "natural": obj.get("natural", "")})

        return {
            "input_ja": ja_prompt,
            "english": english,
            "raw": content,
            "tags": tags,
            "prompt": prompt,
            "negative": getattr(config, "NEGATIVE_PROMPT", ""),
            "issues": constrain.validate(prompt),
        }

    # -- アーティスト名の検出 -------------------------------------------------
    def _detect_artists(self, text: str) -> list[str]:
        """text 中のアーティスト名を artist 辞書で完全一致検出し、正規タグのリストを返す。
        誤検出を避けるため、(a) 一般語と衝突するもの、(b) 短すぎる表層、は除外する。"""
        if self.artist_norm is None or not text:
            return []
        min_len = getattr(config, "ARTIST_MIN_SURFACE_LEN", 3)
        low = text.lower()
        _tags, spans = self.artist_norm.canonicalize(text)
        seen: set[str] = set()
        hits: list[str] = []
        for start, end, canon in spans:
            surface = low[start:end + 1]
            if len(surface) < min_len:
                continue
            if surface in self._general_known:          # 表層が一般語 -> 除外
                continue
            if canon.lower() in self._general_known:     # 正規形が一般語 -> 除外
                continue
            if canon not in seen:
                seen.add(canon)
                hits.append(canon)
        return hits

    # -- キャラ / 作品(シリーズ)名の検出 -----------------------------------
    def _detect_chars(self, text: str) -> list[str]:
        """text 中のキャラ名・作品名(シリーズ)を char 辞書で完全一致検出し、正規タグの
        リストを返す。誤検出を避けるため、(a) 一般語と衝突するもの、(b) 短すぎる表層、は
        除外する。アーティストと違い @ は付けない(仕様ではキャラ名・作品名は素のタグ)。"""
        if self.char_norm is None or not text:
            return []
        min_len = getattr(config, "CHAR_MIN_SURFACE_LEN", 3)
        low = text.lower()
        _tags, spans = self.char_norm.canonicalize(text)
        seen: set[str] = set()
        hits: list[str] = []
        for start, end, canon in spans:
            surface = low[start:end + 1]
            if len(surface) < min_len:
                continue
            if surface in self._general_known:           # 表層が一般語 -> 除外
                continue
            if canon.lower() in self._general_known:      # 正規形が一般語 -> 除外
                continue
            if canon not in seen:
                seen.add(canon)
                hits.append(canon)
        return hits
