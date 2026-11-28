"""注目商品の発売・在庫復活を監視して、ntfy 経由で iPhone に通知するスクリプト。

使い方:
    python monitor.py            # 監視を実行（変化があれば通知し、state.json を更新）
    python monitor.py --dry-run  # 判定結果を表示するだけ（通知も保存もしない）
    python monitor.py --test     # テスト通知を 1 件送る
    python monitor.py --dry-run --only "商品名"
                                 # 1 件だけ試す（enabled: false や担当外の商品も対象）

環境変数:
    MONITOR_RUNNER      どこで動いているか（github / mac。省略時 github）。
                        products.yml の runner: が一致する商品だけを確認する
    STATE_FILE          状態ファイルの名前（省略時 state.json。Mac では state-mac.json）
    NTFY_TOPIC          通知先の ntfy トピック名（必須）
    NTFY_SERVER         ntfy サーバー（省略時 https://ntfy.sh）
    RAKUTEN_APP_ID      楽天ウェブサービスのアプリID（rakuten_search を使う場合）
    RAKUTEN_ACCESS_KEY  楽天ウェブサービスのアクセスキー（発行されている場合）
    RAKUTEN_ENDPOINT    楽天 商品検索API の URL（省略時は下記 DEFAULT_RAKUTEN_ENDPOINT）
    RAKUTEN_REFERER     楽天アプリ登録時の URL（403 になる場合に設定）
    YAHOO_APP_ID        Yahoo!デベロッパーネットワークの Client ID（yahoo_search を使う場合）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
PRODUCTS_FILE = BASE_DIR / "products.yml"
# Mac と GitHub で別々の状態ファイルを使う（同じファイルを取り合わないように）
STATE_FILE = BASE_DIR / (os.environ.get("STATE_FILE") or "state.json")

# 監視を担当する場所。products.yml の runner: と一致する商品だけを確認する
RUNNERS = ("github", "mac")
DEFAULT_RUNNER = "github"

DEFAULT_RAKUTEN_ENDPOINT = (
    "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20220601"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# サイトごとの「購入できる状態」の判定ルール。products.yml で上書きできる。
SITE_PRESETS = {
    "amazon": {
        # カート・予約・今すぐ買うボタンがあれば購入可能とみなす
        "available_selectors": [
            "#add-to-cart-button",
            "#buy-now-button",
            "#add-to-cart-button-ubb",
        ],
        "available_keywords": [],
        "unavailable_keywords": [],
        # カートボックスに表示されている価格（上から順に探す）
        "price_selectors": [
            "#corePrice_feature_div .priceToPay .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
            "#corePrice_feature_div .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-offscreen",
            "#price_inside_buybox",
            "#priceblock_ourprice",
        ],
        # カートボックスの「出荷元・販売元」欄
        "seller_selectors": [
            "#merchantInfoFeature_feature_div",
            "#tabular-buybox",
            "#merchant-info",
            "#sellerProfileTriggerId",
        ],
        # 本物の商品ページにだけある要素。これがなければボット対策の画面とみなす
        "page_marker_selectors": ["#productTitle", "#dp-container"],
    },
    "rakuten": {
        "available_selectors": [],
        "available_keywords": ["かごに追加", "買い物かごに入れる", "予約する"],
        "unavailable_keywords": ["売り切れました", "販売期間外", "在庫切れ"],
        "price_selectors": [
            "meta[itemprop=price]",
            "[itemprop=price]",
            ".price2",
        ],
        "seller_selectors": [],
    },
    "custom": {
        "available_selectors": [],
        "available_keywords": [],
        "unavailable_keywords": [],
        "price_selectors": [],
        "seller_selectors": [],
    },
}

RULE_KEYS = (
    "available_selectors", "available_keywords", "unavailable_keywords",
    "price_selectors", "seller_selectors",
)

# 確認間隔（分）。ワークフローの実行間隔（5 分）が最短。
# products.yml で商品ごとに interval_minutes を指定すると上書きできる。
DEFAULT_INTERVAL_MINUTES = 5
SITE_INTERVAL_MINUTES = {
    "amazon": 30,  # ボット対策が厳しいため、アクセスを控えめにする
}
# GitHub の定期実行は数分ずれることがあるため、その分の余裕を持たせる
INTERVAL_TOLERANCE_SECONDS = 120


# active_hours: "16-18" のように書くと、日本時間のその時間帯だけ確認する
# （16:00〜18:00。終わりの時刻ちょうどの確認も含む。"22-2" のように日付をまたいでもよい）
JST = ZoneInfo("Asia/Tokyo")


def parse_active_hours(value) -> tuple[int, int] | None:
    """'16-18' → (960, 1080)（0 時からの分）。'16:30-18' のように分も書ける。"""
    if value in (None, ""):
        return None
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*[-~〜～]\s*(\d{1,2})(?::(\d{2}))?\s*",
                     str(value))
    if not m:
        raise ValueError(f"active_hours は \"16-18\" のように書いてください: {value!r}")
    sh, sm, eh, em = (int(x) if x else 0 for x in m.groups())
    if not (0 <= sh <= 24 and 0 <= eh <= 24 and sm < 60 and em < 60):
        raise ValueError(f"active_hours の時刻が正しくありません: {value!r}")
    return sh * 60 + sm, eh * 60 + em


def in_active_hours(product: dict, now: datetime | None = None) -> bool:
    window = parse_active_hours(product.get("active_hours"))
    if window is None:
        return True
    now = now or datetime.now(JST)
    minute = now.hour * 60 + now.minute
    start, end = window
    if start <= end:
        return start <= minute <= end
    return minute >= start or minute <= end  # 日付をまたぐ（例: 22-2）


def check_interval(product: dict) -> int:
    if product.get("interval_minutes"):
        return max(int(product["interval_minutes"]), DEFAULT_INTERVAL_MINUTES)
    if product.get("type", "page") == "page":
        return SITE_INTERVAL_MINUTES.get(product.get("site", "custom"),
                                         DEFAULT_INTERVAL_MINUTES)
    return DEFAULT_INTERVAL_MINUTES


# ボット判定（CAPTCHA）ページの目印。これが出たら判定不能として状態を変えない。
CAPTCHA_MARKERS = [
    "validateCaptcha",
    "ロボットではないことを確認",
    "文字を入力してください",
    "Enter the characters you see below",
    "api-services-support@amazon.com",
]


# --------------------------------------------------------------------------
# 設定・状態の読み書き
# --------------------------------------------------------------------------

# type ごとに使えるプロパティ。これ以外を書くと警告を出す（書き間違いに気づけるように）。
COMMON_KEYS = {"name", "type", "enabled", "interval_minutes", "runner", "active_hours"}
ALLOWED_KEYS = {
    "page": COMMON_KEYS | {
        "url", "site", "open_url", "message",
        "available_selectors", "available_keywords", "unavailable_keywords",
        "price_selectors", "seller_selectors", "max_price", "seller_keywords",
        "render",
    },
    "rakuten_search": COMMON_KEYS | {
        "keyword", "must_include", "ng_keyword", "min_price", "max_price",
    },
    "yahoo_search": COMMON_KEYS | {
        "keyword", "must_include", "ng_keyword", "min_price", "max_price",
        "preorder", "condition",
    },
}
REQUIRED_KEYS = {
    "page": {"url"},
    "rakuten_search": {"keyword"},
    "yahoo_search": {"keyword"},
}


def as_list(value, split_spaces: bool = False) -> list[str]:
    """リストでも文字列でも書けるようにそろえる。"""
    if value is None:
        return []
    if isinstance(value, str):
        return value.split() if split_spaces else [value]
    return [str(v) for v in value]


def runner_of(product: dict) -> str:
    return str(product.get("runner") or DEFAULT_RUNNER).strip().lower()


def load_products(include_disabled: bool = False) -> list[dict]:
    with PRODUCTS_FILE.open(encoding="utf-8-sig") as f:
        text = f.read()
    # 全角スペースは YAML の区切りとして扱われず、「# コメント」が値に混ざってしまうため
    # 半角スペースに置き換えてから読み込む（キーワード中の全角スペースも区切りとして扱われる）
    data = yaml.safe_load(text.replace("　", " ")) or {}
    products = data.get("products") or []

    # default_runner: runner を書いていない商品をどこで確認するか（省略時は github）
    default_runner = str(data.get("default_runner") or DEFAULT_RUNNER).strip().lower()
    if default_runner not in RUNNERS:
        print(f"⚠ default_runner は {' / '.join(RUNNERS)} のどれかにしてください"
              f"（{default_runner!r} のため github として扱います）")
        default_runner = DEFAULT_RUNNER
    for p in products:
        if isinstance(p, dict) and not p.get("runner"):
            p["runner"] = default_runner

    names = set()
    for p in products:
        if not p.get("name"):
            raise ValueError(f"name がない商品があります: {p}")
        if p["name"] in names:
            raise ValueError(f"name が重複しています: {p['name']}")
        names.add(p["name"])

        kind = p.get("type", "page")
        if kind not in ALLOWED_KEYS:
            print(f"⚠ {p['name']}: 未対応の type です: {kind!r}"
                  f"（使えるのは {', '.join(ALLOWED_KEYS)}）")
            continue
        unknown = set(p) - ALLOWED_KEYS[kind]
        if unknown:
            print(f"⚠ {p['name']}: type: {kind} では使えない項目があります"
                  f"（無視します）: {', '.join(sorted(unknown))}")
        missing = REQUIRED_KEYS[kind] - set(p)
        if missing:
            print(f"⚠ {p['name']}: 必須の項目がありません: {', '.join(sorted(missing))}")
        try:
            parse_active_hours(p.get("active_hours"))
        except ValueError as e:
            print(f"⚠ {p['name']}: {e}（時間帯の指定は無視します）")
            p.pop("active_hours", None)
        if runner_of(p) not in RUNNERS:
            print(f"⚠ {p['name']}: runner は {' / '.join(RUNNERS)} のどれかにしてください"
                  f"（{p.get('runner')!r} はどこでも確認されません）")

    if include_disabled:
        return products
    return [p for p in products if p.get("enabled", True)]


def load_state() -> dict:
    if STATE_FILE.exists():
        with STATE_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------

def notify(title: str, message: str, click_url: str | None = None,
           tags: list[str] | None = None, dry_run: bool = False) -> None:
    if dry_run:
        print(f"  [通知(dry-run)] {title} / {message}")
        return

    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        raise RuntimeError("環境変数 NTFY_TOPIC が設定されていません")
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")

    # JSON で送ると日本語のタイトルも文字化けしない
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": 4,
        "tags": tags or ["shopping_cart"],
    }
    if click_url:
        payload["click"] = click_url
        payload["actions"] = [
            {"action": "view", "label": "商品ページを開く", "url": click_url}
        ]

    resp = requests.post(server, json=payload, timeout=20)
    resp.raise_for_status()
    print(f"  [通知送信] {title}")


# --------------------------------------------------------------------------
# 商品ページの監視（Amazon / 楽天 / その他）
# --------------------------------------------------------------------------

def build_rule(product: dict) -> dict:
    site = product.get("site", "custom")
    if site not in SITE_PRESETS:
        raise ValueError(f"{product['name']}: 未対応の site です: {site}")
    rule = dict(SITE_PRESETS[site])
    for key in RULE_KEYS:
        if key in product:
            rule[key] = as_list(product[key])
    if not rule["available_selectors"] and not rule["available_keywords"]:
        raise ValueError(
            f"{product['name']}: available_selectors か available_keywords を指定してください"
        )
    if product.get("max_price") is not None and not rule["price_selectors"]:
        raise ValueError(f"{product['name']}: max_price を使うには price_selectors が必要です")
    if product.get("seller_keywords") and not rule["seller_selectors"]:
        raise ValueError(
            f"{product['name']}: seller_keywords を使うには seller_selectors が必要です"
        )
    if product.get("render") not in (None, "http", "browser"):
        raise ValueError(f"{product['name']}: render は browser か http にしてください")
    return rule


# --------------------------------------------------------------------------
# ブラウザ（Playwright）でページを開く（render: browser の商品だけ）
# --------------------------------------------------------------------------
# JavaScript で後から表示されるページ用。画面に見えている文字だけで判定できる。
# ボット対策の回避はしない：ブラウザの名乗り（User-Agent）は標準のまま、
# 403 などで拒否されたらそのサイトは読めないものとして扱う。

_PLAYWRIGHT = None
_BROWSER = None


def fetch_with_browser(url: str) -> tuple[int, str, str, str]:
    """(HTTP ステータス, 最終 URL, 表示後の HTML, 画面に見えている文字) を返す。"""
    global _PLAYWRIGHT, _BROWSER
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "ブラウザ機能（Playwright）が入っていません。"
            "Mac で bash mac/notice.sh setup を実行してください"
        ) from None

    if _BROWSER is None:
        _PLAYWRIGHT = sync_playwright().start()
        _BROWSER = _PLAYWRIGHT.chromium.launch(headless=True)

    context = _BROWSER.new_context(locale="ja-JP")
    try:
        page = context.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeout:
            pass  # 広告などで通信が終わらないページもあるので、その時点の表示で判定する
        page.wait_for_timeout(2000)
        status = resp.status if resp else 0
        visible = " ".join(page.inner_text("body").split())
        return status, page.url, page.content(), visible
    finally:
        context.close()


def close_browser() -> None:
    global _PLAYWRIGHT, _BROWSER
    if _BROWSER is not None:
        _BROWSER.close()
        _BROWSER = None
    if _PLAYWRIGHT is not None:
        _PLAYWRIGHT.stop()
        _PLAYWRIGHT = None


def extract_price(soup: BeautifulSoup, selectors: list[str]) -> int | None:
    """「￥12,800」「12,800円」などから 12800 を取り出す。見つからなければ None。"""
    for sel in selectors:
        el = soup.select_one(sel)
        if not el:
            continue
        raw = el.get("content") or el.get_text(" ", strip=True)
        m = re.search(r"\d[\d,]*", raw)
        if m:
            return int(m.group().replace(",", ""))
    return None


def judge_page(html: str, rule: dict, product: dict,
               visible_text: str | None = None) -> tuple[bool | None, dict]:
    """(購入可能か, 補足情報) を返す。判定不能なら (None, {})。

    補足情報: price（取得できた価格）、reason（購入不可と判定した理由）
    visible_text: ブラウザで開いたときに画面に見えている文字（あればこれで文言を判定する）
    """
    if any(marker in html for marker in CAPTCHA_MARKERS):
        return None, {}

    soup = BeautifulSoup(html, "html.parser")
    markers = rule.get("page_marker_selectors") or []
    if markers and not any(soup.select_one(sel) for sel in markers):
        return None, {}

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    # ブラウザで開いた場合は、隠れている文字（切り替え前の「終了」表示など）を含めない
    text = visible_text if visible_text is not None else soup.get_text(" ", strip=True)

    info: dict = {}
    price = extract_price(soup, rule["price_selectors"])
    if price is not None:
        info["price"] = price

    # 1. 購入不可の文言があれば不可（最優先）
    hit = next((k for k in rule["unavailable_keywords"] if k in text), None)
    if hit:
        info["reason"] = f"「{hit}」の表示あり"
        return False, info

    # 2. カートボタンなどがなければ不可
    has_button = any(soup.select_one(sel) for sel in rule["available_selectors"])
    has_keyword = any(k in text for k in rule["available_keywords"])
    if not (has_button or has_keyword):
        info["reason"] = "購入ボタンなし"
        return False, info

    # 3. 価格の上限（転売価格の出品を除く）
    max_price = product.get("max_price")
    if max_price is not None:
        if price is None:
            info["reason"] = "価格を読み取れない"
            return False, info
        if price > int(max_price):
            info["reason"] = f"価格 {price:,}円 が上限 {int(max_price):,}円 を超えている"
            return False, info

    # 4. 販売元の指定（Amazon.co.jp が販売しているときだけ、など）
    seller_words = as_list(product.get("seller_keywords"))
    if seller_words:
        seller_text = " ".join(
            el.get_text(" ", strip=True)
            for sel in rule["seller_selectors"]
            for el in soup.select(sel)
        )
        if not seller_text:
            info["reason"] = "販売元を読み取れない"
            return False, info
        if not any(w in seller_text for w in seller_words):
            info["reason"] = f"販売元が指定と違う（{seller_text[:40]}）"
            return False, info

    return True, info


def check_page(product: dict, state: dict, dry_run: bool) -> None:
    name = product["name"]
    url = product["url"]
    rule = build_rule(product)
    use_browser = product.get("render") == "browser"

    visible_text = None
    try:
        if use_browser:
            status, final_url, html, visible_text = fetch_with_browser(url)
            if status >= 400:
                print(f"  取得失敗のためスキップ: HTTP {status}"
                      "（サイトに拒否されました。ブラウザ機能でも回避はしません）")
                return
        else:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "ja-JP,ja;q=0.9",
                },
                timeout=30,
            )
            resp.raise_for_status()
            status, final_url, html = resp.status_code, resp.url, resp.text
    except Exception as e:  # noqa: BLE001  通信エラー・ブラウザのエラー
        first_line = (str(e).splitlines() or [""])[0]
        print(f"  取得失敗のためスキップ: {first_line[:200]}")
        return

    if dry_run:
        # 本物の商品ページか、ボット対策の画面かを見分けるための手がかり
        title_tag = BeautifulSoup(html, "html.parser").title
        title = title_tag.get_text(strip=True) if title_tag else "(タイトルなし)"
        print(f"  [dry-run] {'ブラウザ / ' if use_browser else ''}HTTP {status} / "
              f"{len(html):,} 文字 / 最終URL: {final_url} / タイトル: {title[:60]}")

        # 判定に使う文言の前後を表示する（unavailable_keywords を決める手がかり）
        if visible_text is not None:
            page_text = visible_text
        else:
            soup_dbg = BeautifulSoup(html, "html.parser")
            for tag in soup_dbg(["script", "style", "noscript"]):
                tag.decompose()
            page_text = soup_dbg.get_text(" ", strip=True)
        probe_words = (rule["available_keywords"] + rule["unavailable_keywords"]
                       + ["終了", "受付中", "受付期間", "締切", "売り切れ", "SOLD OUT"])
        for word in dict.fromkeys(probe_words):
            pos = page_text.find(word)
            if pos >= 0:
                snippet = page_text[max(0, pos - 40):pos + 40]
                print(f"  [dry-run] 「{word}」付近: …{snippet}…")

    available, info = judge_page(html, rule, product, visible_text)
    if available is None:
        print("  ボット対策の画面が返されたため、今回は判定をスキップします")
        return

    prev = state.get(name, {}).get("available")
    price_text = f"{info['price']:,}円" if "price" in info else "価格不明"
    detail = f" / {info['reason']}" if info.get("reason") else ""
    print(f"  判定: {'購入可能' if available else '購入不可'}"
          f"（{price_text}{detail} / 前回: {prev}）")

    if available and prev is not True:
        message = (product.get("message")
                   or "購入できる状態になりました。タップして商品ページを開けます。")
        if "price" in info:
            message = f"価格: {info['price']:,}円\n" + message
        notify(
            title=f"発売・入荷: {name}",
            message=message,
            # open_url を指定すると、監視する URL とは別の URL（アプリ用など）を開く
            click_url=product.get("open_url") or url,
            tags=["tada", "shopping_cart"],
            dry_run=dry_run,
        )

    # 状態が変わったときだけ state.json が更新される（無駄なコミットを防ぐ）
    state[name] = {"available": available}


# --------------------------------------------------------------------------
# 楽天 商品検索API による新着監視（まだ商品ページがない予定品向け）
# --------------------------------------------------------------------------

def search_rakuten(product: dict) -> list[dict]:
    app_id = os.environ.get("RAKUTEN_APP_ID")
    if not app_id:
        raise RuntimeError("環境変数 RAKUTEN_APP_ID が設定されていません")

    params = {
        "applicationId": app_id,
        "keyword": product["keyword"],
        "availability": 1,          # 購入可能な商品のみ
        "sort": "-updateTimestamp",  # 更新が新しい順
        "hits": 30,
        "formatVersion": 2,
        "format": "json",
    }
    access_key = os.environ.get("RAKUTEN_ACCESS_KEY")
    if access_key:
        params["accessKey"] = access_key
    ng_words = as_list(product.get("ng_keyword"), split_spaces=True)
    if ng_words:
        params["ngKeyword"] = " ".join(ng_words)
    for src, dst in (("min_price", "minPrice"), ("max_price", "maxPrice")):
        if product.get(src) is not None:
            params[dst] = product[src]

    headers = {"User-Agent": "product-release-notifier/1.0"}
    referer = os.environ.get("RAKUTEN_REFERER")
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = referer.rstrip("/")

    # 未設定の Secret は空文字で渡されるため `or` で既定値にする
    endpoint = os.environ.get("RAKUTEN_ENDPOINT") or DEFAULT_RAKUTEN_ENDPOINT
    resp = requests.get(endpoint, params=params, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"楽天API エラー {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    items = data.get("Items") or data.get("items") or []
    # formatVersion=1 の形式（{"Item": {...}}）にも対応しておく
    return [i.get("Item", i) for i in items]


def check_rakuten_search(product: dict, state: dict, dry_run: bool) -> None:
    try:
        raw = search_rakuten(product)
    except Exception as e:  # noqa: BLE001
        print(f"  検索失敗のためスキップ: {e}")
        return
    items = [
        {
            "code": i.get("itemCode"),
            "name": i.get("itemName", ""),
            "price": i.get("itemPrice"),
            "url": i.get("itemUrl"),
            "shop": i.get("shopName", ""),
        }
        for i in raw
    ]
    check_new_items(product, state, items, "楽天", dry_run)


# --------------------------------------------------------------------------
# Yahoo!ショッピング 商品検索API（v3）による新着監視
# --------------------------------------------------------------------------

YAHOO_ENDPOINT = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"


def search_yahoo(product: dict) -> list[dict]:
    app_id = os.environ.get("YAHOO_APP_ID")
    if not app_id:
        raise RuntimeError("環境変数 YAHOO_APP_ID が設定されていません")

    params = {
        "appid": app_id,
        "query": product["keyword"],
        "results": 50,
        "in_stock": "true",  # 在庫ありのみ
    }
    # 既定は新品のみ。condition: any で新品・中古の両方を対象にする
    condition = product.get("condition", "new")
    if condition in ("new", "used"):
        params["condition"] = condition
    if product.get("preorder"):
        params["preorder"] = "true"  # 予約商品のみ
    if product.get("min_price") is not None:
        params["price_from"] = product["min_price"]
    if product.get("max_price") is not None:
        params["price_to"] = product["max_price"]

    resp = requests.get(
        YAHOO_ENDPOINT,
        params=params,
        # appid はクエリーで渡す（User-Agent にも入れると 401 "conflicted" になる）
        headers={"User-Agent": "product-release-notifier/1.0"},
        timeout=30,
    )
    if resp.status_code == 429:
        raise RuntimeError("Yahoo API の利用制限中です（429）。しばらく待つと解除されます")
    if resp.status_code != 200:
        raise RuntimeError(f"Yahoo API エラー {resp.status_code}: {resp.text[:300]}")

    hits = resp.json().get("hits") or []
    return [
        {
            "code": h.get("code"),
            "name": h.get("name", ""),
            "price": h.get("price"),
            "url": h.get("url"),
            "shop": (h.get("seller") or {}).get("name", ""),
        }
        for h in hits
    ]


def check_yahoo_search(product: dict, state: dict, dry_run: bool) -> None:
    try:
        items = search_yahoo(product)
    except Exception as e:  # noqa: BLE001
        print(f"  検索失敗のためスキップ: {e}")
        return
    check_new_items(product, state, items, "Yahoo!ショッピング", dry_run)


# --------------------------------------------------------------------------
# 検索結果の新着判定（楽天・Yahoo 共通）
# --------------------------------------------------------------------------

def check_new_items(product: dict, state: dict, items: list[dict],
                    site_label: str, dry_run: bool) -> None:
    """items は {code, name, price, url, shop} の形にそろえたもの。"""
    name = product["name"]

    # どちらも「リスト」でも「スペース区切りの文字列」でも書ける
    must_words = as_list(product.get("must_include"), split_spaces=True)
    if must_words:
        items = [i for i in items if all(w in i["name"] for w in must_words)]
    ng_words = as_list(product.get("ng_keyword"), split_spaces=True)
    if ng_words:
        items = [i for i in items if not any(w in i["name"] for w in ng_words)]
    items = [i for i in items if i.get("code")]

    entry = state.get(name, {})
    seen = set(entry.get("seen", []))
    first_run = not entry.get("initialized")
    new_items = [i for i in items if i["code"] not in seen]
    print(f"  ヒット {len(items)} 件 / 新着 {len(new_items)} 件")

    if first_run:
        notify(
            title=f"監視開始: {name}",
            message=(
                f"{site_label}で現在 {len(items)} 件ヒットしています。"
                "今後、新しく出品された商品を通知します。"
            ),
            tags=["eyes"],
            dry_run=dry_run,
        )
    else:
        for item in new_items[:5]:
            price = item.get("price")
            price_text = f"{price:,}円" if isinstance(price, int) else ""
            notify(
                title=f"{site_label}に新着: {name}",
                message=f"{item['name'][:80]}\n{price_text} / {item.get('shop', '')}",
                click_url=item.get("url"),
                tags=["new", "shopping_cart"],
                dry_run=dry_run,
            )
        if len(new_items) > 5:
            notify(
                title=f"{site_label}に新着: {name}",
                message=f"ほかにも {len(new_items) - 5} 件の新着があります。",
                dry_run=dry_run,
            )

    seen.update(i["code"] for i in items)
    state[name] = {
        "initialized": True,
        "seen": sorted(seen)[-500:],
    }


# --------------------------------------------------------------------------
# メイン
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="注目商品の発売通知")
    parser.add_argument("--dry-run", action="store_true", help="通知・保存をしない")
    parser.add_argument("--test", action="store_true", help="テスト通知を送る")
    parser.add_argument("--url", help="テスト通知をタップしたときに開く URL")
    parser.add_argument("--only", metavar="NAME",
                        help="この名前の商品だけを試す（--dry-run と一緒に使う）")
    args = parser.parse_args()
    if args.only and not args.dry_run:
        parser.error("--only は --dry-run と一緒に使ってください")

    if args.test:
        if args.url:
            message = "タップして、指定した URL がどのように開くか確認してください。"
        else:
            message = "iPhone に届いていれば設定完了です。"
        notify("テスト通知", message, click_url=args.url or None,
               tags=["white_check_mark"])
        return 0

    runner = (os.environ.get("MONITOR_RUNNER") or DEFAULT_RUNNER).strip().lower()
    if args.only:
        # 試しに 1 件だけ確認する（停止中・担当外の商品も対象にする）
        products = [p for p in load_products(include_disabled=True)
                    if p.get("name") == args.only]
        if not products:
            print(f"「{args.only}」という name の商品が products.yml にありません")
            return 1
    else:
        products = [p for p in load_products() if runner_of(p) == runner]
    print(f"実行場所: {runner} / 対象 {len(products)} 件 / 状態ファイル: {STATE_FILE.name}")
    if not products:
        # 担当の商品がないときは何もしない（もう一方の状態ファイルを消さないよう保存もしない）
        print("担当の商品がないため終了します")
        return 0
    state = load_state()

    # GitHub から Mac へ移した商品は、GitHub が記録していた状態を引き継ぐ
    # （引き継がないと「監視開始」や同じ商品の通知がもう一度届いてしまう）
    legacy_file = BASE_DIR / "state.json"
    if STATE_FILE != legacy_file and legacy_file.exists():
        with legacy_file.open(encoding="utf-8") as f:
            legacy = json.load(f)
        moved = [p["name"] for p in products
                 if p["name"] not in state and p["name"] in legacy]
        for moved_name in moved:
            state[moved_name] = legacy[moved_name]
        if moved:
            print(f"GitHub の記録から {len(moved)} 件の状態を引き継ぎました")

    now = time.time()
    for product in products:
        name = product["name"]
        kind = product.get("type", "page")
        interval = check_interval(product)
        print(f"■ {name}（{kind} / {interval}分おき）")

        # active_hours の時間帯の外は確認しない（dry-run では時間帯に関係なく試す）
        if not args.dry_run and not in_active_hours(product):
            print(f"  スキップ（確認する時間帯 {product['active_hours']} の外）")
            continue

        # ワークフローは 5 分ごとに動くので、間隔の長い商品は前回から時間が経つまで飛ばす
        last = state.get(name, {}).get("last_checked", 0)
        if (not args.dry_run and interval > DEFAULT_INTERVAL_MINUTES
                and now - last < interval * 60 - INTERVAL_TOLERANCE_SECONDS):
            print(f"  スキップ（前回の確認から {int((now - last) / 60)} 分）")
            continue

        try:
            if kind == "page":
                check_page(product, state, args.dry_run)
            elif kind == "rakuten_search":
                check_rakuten_search(product, state, args.dry_run)
            elif kind == "yahoo_search":
                check_yahoo_search(product, state, args.dry_run)
            else:
                print(f"  未対応の type です: {kind}")
        except Exception as e:  # noqa: BLE001  1 商品の失敗で全体を止めない
            print(f"  エラー: {e}")

        # 取得失敗・ボット判定でも「確認した」扱いにして、すぐに再アクセスしない
        if interval > DEFAULT_INTERVAL_MINUTES:
            state.setdefault(name, {})["last_checked"] = int(now)

        # サイトに負荷をかけないよう間隔を空ける（楽天・Yahoo の API は 1 秒 1 回まで）
        time.sleep(2)

    close_browser()

    # 削除された商品の状態は捨てる
    names = {p["name"] for p in products}
    state = {k: v for k, v in state.items() if k in names}

    if not args.dry_run:
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
