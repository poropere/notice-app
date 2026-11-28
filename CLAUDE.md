# CLAUDE.md — 発売通知アプリ（notice-app）

利用者（リポジトリのオーナー）は iPhone で通知を受け取り、外出先からも Claude に「監視対象を追加して」と頼むことがある。
返答は **日本語** で、専門用語はかみくだいて説明する。

## 仕組み

- `products.yml` に監視対象を書く → `monitor.py` が確認 → 変化があれば ntfy 経由で iPhone に通知。
- **監視はすべて自宅の Mac で行う（2026-11-28 移行）。** `mac/notice.sh` を launchd で 5 分ごとに実行。
  - `products.yml` の `default_runner: mac` により、`runner` を書いていない商品はすべて Mac が確認する。
    `runner: github` を付けた商品だけ GitHub Actions が確認する（環境変数 `MONITOR_RUNNER` で切り替え）。
  - Mac は毎回 `git pull` で `products.yml` を取り込むだけで、push はしない。**GitHub に push すれば最長 5 分で Mac に反映される。**
  - Mac の状態は `state-mac.json`、通知先などの設定は `mac/config.env`（どちらも Git 管理外）。
  - 監視が止まったら、まず Mac の電源・ログイン・`bash mac/notice.sh status` を疑う（利用者に Mac で実行してもらう）。
- GitHub Actions（`.github/workflows/monitor.yml`）は手動実行（dry-run・テスト通知）用に残している。定期実行はしない。
  cron-job.org からの 5 分ごとの起動は移行時に停止した（動いていても担当 0 件なので何もしない）。
  - `state.json` は GitHub 時代の記録。Mac が初めて確認する商品はここから状態を引き継ぐ。**手で編集しない。**
- Secrets: `NTFY_TOPIC`, `YAHOO_APP_ID`（登録済み。dry-run 用）。楽天 API は未発行（楽天の管理画面がメンテナンス中で発行不可）。
- リポジトリは **Public**（Mac がログインなしで `git pull` できるため。Private にするなら Mac に認証設定が必要）。

## 監視対象の追加（よくある依頼）

`products.yml` の `products:` に追記する。使えるプロパティの一覧は README.md の「使えるプロパティ一覧」を参照。

| type | 用途 |
|---|---|
| `yahoo_search` | まだ商品ページがない／どこで出るか分からない商品を、Yahoo!ショッピングで定価帯の **新着出品** として探す。**最も確実に動く。** |
| `page`（`site: custom`） | 抽選ページや商品ページに特定の文字が出たら通知（`available_keywords`）。 |
| `page`（`site: rakuten`） | 楽天の個別商品ページの在庫復活・予約開始。 |
| `rakuten_search` | 楽天 API が使えるようになるまで使わない。 |

### 方針（利用者と合意済み）

- **転売価格を除く**ため、検索は `min_price`〜`max_price` を「定価 − 少し 〜 定価 + 少し」にする。定価は Web で確認し、根拠をコメントに残す。
- 周辺グッズや中古を `ng_keyword` で除外する（例：ケース スリーブ 空瓶 ミニ 中古 開封済み 1パック）。商品名に必須の語は `must_include`。
- `name` は「【Yahoo】」「【抽選】」などの接頭辞を付け、**重複させない**（重複するとエラーで全体が止まる）。
- 抽選ページの監視では `message:` に「応募期間を確認してください」などの文言を入れる。
- 確認間隔：既定 5 分。お酒の検索は 10 分、抽選ページは 30 分にしている。
- 新しい検索を追加すると、初回に「監視開始」の通知が 1 回届く（仕様）。

### 停止中のサイト（再度追加しない／追加するなら理由を説明して確認を取る）

| サイト | 理由 |
|---|---|
| Amazon | GitHub のサーバーからは毎回ボット対策の画面が返る。**自宅の Mac からは読める**（2026-11-28 確認。間隔は 30 分以上）→ 監視可 |
| ビックカメラ.com | GitHub からも自宅の Mac からも応答しない（遮断） |
| 東急百貨店の抽選ページ | 受付中／終了の表示が JavaScript で切り替わり、文字で判定できない |
| ポケモンセンターオンライン | ボット対策が厳しく、読み取れない可能性が高い（未検証） |
| あみあみ | GitHub からは 403（拒否）。自宅の Mac からも監視できなかった（2026-11-28） |
| 駿河屋 | トップページ・検索ページとも GitHub からは 403（拒否）が返る（2026/11 dry-run で確認） |

駿河屋は GitHub からの確認のみで、自宅の Mac からは未検証（読める可能性がある。試すなら利用者に Mac での dry-run を頼む）。

新しいサイトを `page` で監視する場合は、ボット対策や JavaScript 描画で読み取れないことがあるので、必ず dry-run で確認する。

**`render: browser`**（Playwright・Mac のみ）は、JavaScript で後から表示されるページ（dry-run で HTTP 200 だが文字数が少ない、キーワードが出ない）に使う。
画面に見えている文字だけで判定するので、隠れた「終了」表示などに引っかからない。
**403 などで拒否されるサイトの突破には使わない**（利用者と合意済み。User-Agent の偽装やステルス化もしない）。重いので必要な商品だけに付ける。

## 編集時の注意

- YAML の字下げは `- name:` の前に半角スペース 2 つ、各項目の前に 4 つ。
- 全角スペースは読み込み時に半角へ置き換えられるが、書くときは半角を使う。
- `products.yml` は利用者が iPhone から直接編集することもある。**編集前に必ず最新を取り込む（`git pull --rebase`）。**
- 例として残している `enabled: false` の項目は消さない（利用者が書き方の見本として使っている）。

## 確認のしかた

1. 文法チェック（ローカル）：
   `python -c "import monitor; print(len(monitor.load_products()), 'items')"`
   → 「⚠」の警告が出ないこと。
2. 実際の判定（GitHub CLI が使える場合）：
   `gh workflow run monitor.yml -R poropere/notice-app -f mode=dry-run -f runner=mac` を実行し、
   `gh run view <id> -R poropere/notice-app --log` で「ヒット ○件」「判定: …」を確認する。
   （`runner=mac` を付けないと担当 0 件で何も確認されない。GitHub から試すので、Amazon など GitHub から読めないサイトは失敗するのが正常。）
   dry-run は通知も保存もしない。ページ監視では HTTP ステータス・文字数・タイトルと、キーワード前後の文章がログに出る。
   GitHub から読めないサイトは、利用者に Mac で `bash mac/notice.sh dry-run "商品名"` を実行してもらい、結果を貼ってもらう。
3. 結果（ヒット件数、判定、警告）を利用者に日本語で簡潔に報告する。

## やってはいけないこと

- Secrets の値やアクセスキーをファイルやログに書かない。
- `state.json` を手で書き換えない（通知の重複や取りこぼしの原因になる）。
- 確認間隔を 5 分より短くしない／ボット対策の回避（IP 偽装など）を試みない。
