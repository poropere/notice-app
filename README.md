# 発売通知アプリ

Amazon・楽天・Yahoo!ショッピングで注目している商品が **購入できるようになったら iPhone に通知** する仕組みです。

- 監視は **GitHub Actions**（Public リポジトリなら無料）が **5 分ごと** に実行します。
  ボット対策が厳しい **Amazon の商品だけは 30 分ごと** に確認します。PC の電源を切っていても動きます。
- 通知は iPhone の **ntfy** アプリで受け取ります。Mac や App Store への登録は不要です。
- 作業は **Windows のブラウザだけ** で完結します。

```
GitHub Actions（5分ごと） ──商品ページ / 楽天・Yahoo API を確認──▶ 変化あり？
                                                     │
                                                     ▼
                                          ntfy.sh ──▶ iPhone に通知
```

---

## セットアップ（初回だけ・約 15 分）

### 1. iPhone に ntfy を入れる

1. App Store で **「ntfy」** をインストールして開き、通知を許可します。
2. 右下の **＋** をタップし、トピック名を入力して購読します。
   - トピック名は **他人に推測されない文字列** にしてください（例：`hatsubai-k7x29qpm4w`）。
     ntfy.sh ではトピック名を知っている人なら誰でも購読・送信できるため、パスワードのつもりで決めます。

### 2. GitHub にリポジトリを作る

1. [github.com](https://github.com) でアカウントを作成（無料）し、右上の **＋ → New repository** を選びます。
2. 名前は任意（例：`notice-app`）。**Public** を選んで作成します。
   - Public だと GitHub Actions を無料・無制限で使えます（Private は月 2,000 分までで、5 分ごとの実行だと足りません）。
   - 監視している商品の一覧（`products.yml`）は誰でも見られる状態になります。
     通知先のトピック名や API の ID は Secrets に入れるので公開されません。
3. 作成後の画面で **「uploading an existing file」** をクリックし、このフォルダの次のファイルをドラッグ＆ドロップしてコミットします。
   - `monitor.py`
   - `products.yml`
   - `requirements.txt`
   - `README.md`
4. `.github/workflows/monitor.yml` は、ドラッグ＆ドロップでは隠しフォルダが入らないことがあるので、次の方法で作ります。
   - リポジトリのトップで **Add file → Create new file** を選ぶ
   - ファイル名欄に `.github/workflows/monitor.yml` と入力（`/` を打つとフォルダになります）
   - このフォルダの `monitor.yml` の中身を貼り付けて **Commit changes**

### 3. トピック名を登録する（Secrets）

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で登録します。

| Name | 値 | 必須 |
|---|---|---|
| `NTFY_TOPIC` | 手順 1 で決めたトピック名 | ✅ |
| `RAKUTEN_APP_ID` | 楽天のアプリID（下記「楽天APIを使う場合」） | rakuten_search を使う場合 |
| `RAKUTEN_ACCESS_KEY` | 楽天のアクセスキー（発行される場合） | 同上 |
| `RAKUTEN_REFERER` | 楽天アプリ登録時に入力した URL | 403 エラーが出る場合 |
| `YAHOO_APP_ID` | Yahoo! の Client ID（下記「Yahoo!ショッピングAPIを使う場合」） | yahoo_search を使う場合 |

### 4. テスト通知を送る

1. リポジトリの **Actions** タブを開きます（初回は有効化ボタンが出たら押す）。
2. 左の **「商品の発売チェック」 → Run workflow** で、mode に **`test-notification`** を選んで実行します。
3. iPhone に「テスト通知」が届けば完了です。

---

## 監視する商品を登録する

GitHub 上で `products.yml` を開き、右上の鉛筆アイコンで編集して保存（Commit）します。
iPhone の Safari から GitHub を開いて編集することもできます。

### すでに商品ページがある商品（予約開始待ち・在庫切れ）

```yaml
  - name: ○○ 限定版
    type: page
    site: amazon            # amazon / rakuten / custom
    url: https://www.amazon.co.jp/dp/B0XXXXXXXX
```

「カートに入れる」「予約注文する」などのボタンが表示されたら通知します。
Amazon の URL は `https://www.amazon.co.jp/dp/商品コード` の短い形にすると確実です。

### 定価のときだけ通知する（転売対策）

Amazon では 1 つの商品ページに複数の人が出品しているため、転売価格の出品でも「カートに入れる」が表示されます。
`max_price` と `seller_keywords` を付けると、条件に合うときだけ通知します。

```yaml
  - name: ウイスキー ○○ 12年
    type: page
    site: amazon
    url: https://www.amazon.co.jp/dp/B0XXXXXXXX
    max_price: 12000               # カートボックスの価格が 12,000 円以下のときだけ
    seller_keywords: Amazon.co.jp  # 販売元が Amazon.co.jp のときだけ（任意）
```

- 判定に使うのは、商品ページの **カートボックス（「カートに入れる」ボタンの近く）に表示されている出品** です。
  定価の出品があっても、カートボックスに表示されていなければ通知されません。
- 価格や販売元を読み取れなかったときは、誤通知を防ぐため「購入不可」として扱います。
- 送料は価格に含めていません。定価に送料を足した額で `max_price` を決めると安心です。
- `dry-run` のログに「判定: 購入不可（15,800円 / 価格 15,800円 が上限 12,000円 を超えている）」のように表示されるので、
  価格が正しく読み取れているか確認してください。

### まだ商品ページがない発売予定品（楽天で探す）

```yaml
  - name: ○○ 新モデル
    type: rakuten_search
    keyword: ○○ 新モデル 型番
    must_include: ["型番"]    # 商品名に必ず含まれる語（任意）
    ng_keyword: 中古 ケース    # 除外したい語（任意）
    min_price: 10000          # 価格の下限（任意。アクセサリー類の除外に便利）
```

初回は「監視開始」の通知が届き、その後は **新しく出品された商品** があれば通知します。

### Yahoo!ショッピングで発売予定品・新着を探す

```yaml
  - name: ○○ 新モデル
    type: yahoo_search
    keyword: ○○ 新モデル 型番
    must_include: ["型番"]    # 任意
    ng_keyword: ケース フィルム # 任意
    min_price: 10000          # 任意
    preorder: true            # 任意。予約商品だけを対象にする
```

書き方は楽天とほぼ同じです。在庫のある新品だけを検索します（中古も含めたい場合は `condition: any`）。
Yahoo の API には「新着順」の並び替えがないため、検索結果の上位 50 件の中から新着を探します。
**キーワードは型番などで具体的に絞り込む** と、取りこぼしが少なくなります。

### 動作確認

**Actions → Run workflow → `dry-run`** で実行すると、通知せずに各商品の判定結果だけをログで確認できます。
ログの「判定: 購入可能 / 購入不可」が実際のページと合っているかを確認してください。

---

## 楽天APIを使う場合

1. [楽天ウェブサービス](https://webservice.rakuten.co.jp/) に楽天会員でログインし、**アプリID発行** を行います。
2. 発行画面に表示される **アプリID**（と、表示されていれば **アクセスキー**）を Secrets に登録します。
3. 楽天ウェブサービスは 2026 年に接続先と認証方式が変更されています。
   ログに `楽天API エラー` が出る場合は、管理画面やドキュメントに記載されている **商品検索API の URL** を確認し、
   Secrets に `RAKUTEN_ENDPOINT` として登録してください。

### 使えるプロパティ一覧

**全タイプ共通**

| プロパティ | 必須 | 内容 |
|---|---|---|
| `name` | ✅ | 商品の名前（通知のタイトルになります。重複不可） |
| `type` | | `page` / `rakuten_search` / `yahoo_search`（省略時は `page`） |
| `enabled` | | `false` にすると監視を止める |
| `interval_minutes` | | 確認間隔（分）。最短 5。省略時は 5（Amazon のページだけ 30） |
| `runner` | | `mac` にすると自宅の Mac が確認する（GitHub では確認しない）。省略時は GitHub |
| `render` | page | `browser` にすると、画面のないブラウザでページを開き、**画面に見えている文字** で判定する（JavaScript で後から表示されるページ用。Mac のみ・少し重い）。403 などで拒否されるサイトは読めません |

**タイプ別**（✅ 必須　○ 任意　△ custom ではどちらか一方が必須　空欄 = 使えない）

| プロパティ | page (amazon) | page (rakuten) | page (custom) | rakuten_search | yahoo_search | 内容 |
|---|:-:|:-:|:-:|:-:|:-:|---|
| `url` | ✅ | ✅ | ✅ | | | 監視する商品ページ |
| `site` | ✅ | ✅ | ✅ | | | `amazon` / `rakuten` / `custom`（省略時は `custom`） |
| `open_url` | ○ | ○ | ○ | | | 通知をタップしたときに開く URL |
| `message` | ○ | ○ | ○ | | | 通知の本文（省略時は「購入できる状態になりました…」） |
| `available_selectors` | ○ | ○ | △ | | | この要素（CSS セレクター）があれば購入可能 |
| `available_keywords` | ○ | ○ | △ | | | この文字があれば購入可能 |
| `unavailable_keywords` | ○ | ○ | ○ | | | この文字があれば購入不可（最優先） |
| `max_price` | ○ | ○ | ※ | ○ | ○ | page では「表示価格がこれを超えたら購入不可」（転売対策） |
| `seller_keywords` | ○ | | ※ | | | 販売元にこの文字があるときだけ購入可能（例：`Amazon.co.jp`） |
| `price_selectors` | ○ | ○ | ○ | | | 価格を読み取る要素（amazon / rakuten は設定済み） |
| `seller_selectors` | ○ | ○ | ○ | | | 販売元を読み取る要素（amazon は設定済み） |
| `keyword` | | | | ✅ | ✅ | 検索キーワード（スペース区切りで AND） |
| `must_include` | | | | ○ | ○ | 商品名に必ず含まれるべき語 |
| `ng_keyword` | | | | ○ | ○ | 商品名に含まれていたら除外する語 |
| `min_price` / `max_price` | | | | ○ | ○ | 価格の範囲（円） |
| `preorder` | | | | | ○ | `true` で予約商品のみ |
| `condition` | | | | | ○ | `new`（既定） / `used` / `any` |

- ※ custom で `max_price` / `seller_keywords` を使うには、`price_selectors` / `seller_selectors` も指定します。
- `page (amazon)` / `page (rakuten)` の判定ルールは最初から設定済みです。`available_*` / `unavailable_keywords` を書くと、その項目だけ上書きされます。
- 複数の語を書く項目は、`["A", "B"]` のリスト形式でも、`A B` のスペース区切りでも書けます
  （`available_keywords` などページ用の項目は、スペースを含む 1 つの語として扱われます）。
- 使えない項目を書いた場合は、実行ログに「⚠ … 使えない項目があります」と表示されます。

---

## 通知をタップしたときの動き

通知をタップする（または「商品ページを開く」ボタンを押す）と、商品ページが開きます。
iPhone に Amazon・楽天市場・Yahoo!ショッピングのアプリが入っていれば、アプリで開くことがあります（ユニバーサルリンク）。

**実際にどう開くかは、次の手順で確認できます。**

1. **Actions → 商品の発売チェック → Run workflow** を選ぶ
2. mode に `test-notification` を選ぶ
3. test_url に確認したい商品ページの URL（例：`https://www.amazon.co.jp/dp/商品コード`）を入れて実行
4. 届いた通知をタップし、アプリ・Safari・ntfy 内のブラウザのどれで開くかを見る

アプリで開かない場合は、ntfy 内のブラウザ右下などにある **Safari で開く** から開くと、アプリに切り替わることがあります。
また `type: page` の商品では、`open_url:` に別の URL を書くと、その URL を開くようにできます。

---

## Yahoo!ショッピングAPIを使う場合

1. [Yahoo!デベロッパーネットワーク](https://developer.yahoo.co.jp/) に Yahoo! JAPAN ID でログインします。
2. 右上の **アプリケーションの管理 → 新しいアプリケーションを開発** を選び、
   アプリケーションの種類は **「サーバーサイド（Yahoo! ID連携を利用しない）」** を選んで登録します。
3. 表示された **Client ID** を、Secrets に `YAHOO_APP_ID` として登録します。

利用回数の上限は 1 秒に 1 回です。このスクリプトは商品ごとに 2 秒空けて実行するので、上限を超えることはありません。

---

## 自宅の Mac で監視する（現在はすべて Mac で監視）

`products.yml` の `default_runner: mac` により、すべての商品を自宅の Mac が 5 分ごとに確認しています
（Amazon は GitHub のサーバーからは読めませんが、自宅の回線からは読めます）。
商品ごとに `runner: github` を付けると、その商品だけを GitHub Actions が確認します（同じ通知が 2 回届くことはありません）。
GitHub Actions の定期実行は止めており、手動の dry-run・テスト通知用にだけ残しています。

- 監視対象の一覧は GitHub の `products.yml` のままです。Mac は毎回最新を取り込むので、iPhone からの追加もそのまま反映されます。
- Mac 側の状態（`state-mac.json`）・通知先の設定（`mac/config.env`）・記録（`mac/logs/`）は Mac の中だけに保存され、GitHub には送られません。
- Claude の使用量はかかりません（普通のプログラムとして動きます）。

### 準備（最初に 1 回・約 15 分）

Mac の **ターミナル**（アプリケーション → ユーティリティ）で、1 行ずつ実行します。

```bash
xcode-select --install          # git と Python を入れる（入っていれば「already installed」と出る）
cd ~
git clone https://github.com/poropere/notice-app.git
cd ~/notice-app
bash mac/notice.sh setup        # Python の準備と、ntfy のトピック名の入力
bash mac/notice.sh test         # iPhone にテスト通知が届くか確認
```

> フォルダは **ホームフォルダ直下（`~/notice-app`）** に置いてください。
> 書類・デスクトップの中に置くと、macOS の保護機能で自動実行が止められることがあります。

### 読めるか試す → 自動監視を始める

```bash
bash mac/notice.sh dry-run "【Amazon】山崎12年"   # 1 件だけ試す（通知・保存はしない）
bash mac/notice.sh start                         # 5 分ごとの自動監視を開始
bash mac/notice.sh status                        # 動いているかと、直近の結果を表示
bash mac/notice.sh stop                          # 止める
```

`dry-run` で「判定: 購入可能／購入不可」と出れば読めています。「ボット対策の画面」「取得失敗」と出る場合は Mac からも読めません。
読めた商品は `products.yml` で `enabled: false` の行を消すと、次回から Mac が監視します。

### Mac の設定で気をつけること

- **電源アダプタをつないだまま** にします。`start` すると、監視中はスリープしないように自動で設定されます（画面は消えて構いません）。
  ノート型はふたを閉じるとスリープするので、開けたままにしてください。
- 停電・アップデートで再起動したときに監視が再開するよう、**システム設定 → ユーザとグループ → 自動ログイン** をオンにしておくと安心です
  （FileVault を使っている場合は自動ログインできないので、再起動後に一度ログインしてください）。
- **VPN は使わないでください。** 自宅回線のほうがブロックされにくく、VPN は接続が切れて取りこぼしの原因になります。

---

## 注意点

- **Amazon はボット対策が強い** ため、GitHub のサーバーからのアクセスが確認画面（CAPTCHA）で止められることがあります。
  その場合はログに「ボット判定ページが返されたため…スキップ」と出て、誤通知はしません。
  何度も続く場合、Amazon の商品は楽天の `rakuten_search` で代わりに探すのが確実です。
- サイトのデザインが変わると判定が外れることがあります。`dry-run` で時々確認し、
  外れていたら `available_keywords` / `unavailable_keywords` / `available_selectors` を商品ごとに指定して調整します。
- 確認間隔は 5 分（Amazon の商品ページは 30 分）です。GitHub の定期実行は混雑時に数分〜十数分遅れることがあります。
  商品ごとに変えたい場合は `products.yml` で `interval_minutes: 15` のように指定します（5 分より短くはできません）。
- Public リポジトリでは、**60 日間リポジトリに動きがないと定期実行が自動で止まります**。
  Actions タブに停止の案内が出たら **Enable workflow** で再開してください。
  （商品の状態が変わるたびに自動でコミットされるので、通常は止まりにくいです。）
- リポジトリを Private に変える場合は、無料枠を超えないよう `monitor.yml` の cron を `*/30 * * * *` に戻してください。
- 人気商品の争奪戦のように **数分単位の速さ** が必要な用途には向きません。
- 各サイトの利用規約の範囲で、個人利用にとどめてください。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `products.yml` | 監視する商品の一覧（ここだけ編集すれば OK） |
| `monitor.py` | 監視と通知を行うスクリプト |
| `.github/workflows/monitor.yml` | 5 分ごとの自動実行設定 |
| `state.json` | 前回の状態（自動で作成・更新されます） |
| `mac/notice.sh` | 自宅の Mac で動かすための操作（準備・開始・停止・確認） |
