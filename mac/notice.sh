#!/bin/bash
# notice-app を Mac で動かすための操作をまとめたスクリプト。
#
# 使い方（ターミナルで notice-app のフォルダに移動してから）:
#   bash mac/notice.sh setup               最初に 1 回だけ（Python の準備と通知先の設定）
#   bash mac/notice.sh test                iPhone にテスト通知を送る
#   bash mac/notice.sh dry-run             Mac 担当（runner: mac）の商品を試しに確認する（通知・保存なし）
#   bash mac/notice.sh dry-run "商品名"    その 1 件だけ試す（enabled: false の商品も試せる）
#   bash mac/notice.sh start               5 分ごとの自動監視を始める（再起動後も自動で再開）
#   bash mac/notice.sh stop                自動監視を止める
#   bash mac/notice.sh status              動いているかと、最近の記録を表示する
#   bash mac/notice.sh log                 記録（ログ）を流し続けて表示する（Control + C で終了）
#
# products.yml は毎回 GitHub から最新を取り込むので、iPhone から編集した内容もそのまま反映される。
# 状態ファイル（state-mac.json）と通知先の設定（mac/config.env）は Mac の中だけに保存され、GitHub には送られない。

set -u

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MAC_DIR="$REPO_DIR/mac"
VENV="$REPO_DIR/.venv"
PY="$VENV/bin/python"
CONFIG="$MAC_DIR/config.env"
LOG_DIR="$MAC_DIR/logs"
LOG="$LOG_DIR/monitor.log"
LOG_MAX_BYTES=5000000

LABEL="com.notice-app.monitor"
AWAKE_LABEL="com.notice-app.awake"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
AWAKE_PLIST="$AGENTS/$AWAKE_LABEL.plist"
GUI="gui/$(id -u)"

say() { printf '%s\n' "$*"; }
die() { printf '✖ %s\n' "$*" >&2; exit 1; }

load_env() {
  [ -f "$CONFIG" ] || die "設定がありません。先に bash mac/notice.sh setup を実行してください"
  [ -x "$PY" ] || die "Python の準備ができていません。先に bash mac/notice.sh setup を実行してください"
  set -a
  # shellcheck disable=SC1090
  . "$CONFIG"
  set +a
  export MONITOR_RUNNER=mac
  export STATE_FILE=state-mac.json
  export PYTHONIOENCODING=utf-8
  export PYTHONUNBUFFERED=1
}

pull_latest() {
  # products.yml の最新を取り込む。失敗しても（ネットが一時的に切れた等）手元の内容で続ける
  if ! git -C "$REPO_DIR" pull --ff-only -q 2>&1; then
    say "⚠ GitHub から最新を取り込めませんでした。手元の products.yml で続けます"
  fi
}

cmd_setup() {
  case "$REPO_DIR" in
    "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
      say "⚠ このフォルダ（$REPO_DIR）は書類・デスクトップ・ダウンロードの中にあります。"
      say "  macOS の保護機能で自動実行が止められることがあるため、"
      say "  ホームフォルダ直下（例: ~/notice-app）に置き直すことをおすすめします。"
      say ""
      ;;
  esac

  if ! command -v python3 >/dev/null 2>&1 \
     || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    die "Python 3.9 以上が見つかりません。xcode-select --install を実行し、インストール完了後にもう一度 setup してください"
  fi
  say "● Python: $(python3 --version 2>&1)"

  say "● 専用の Python 環境を作成しています（1〜2 分かかります）"
  [ -x "$PY" ] || python3 -m venv "$VENV" || die "Python 環境を作成できませんでした"
  "$PY" -m pip install -q --upgrade pip >/dev/null 2>&1
  "$PY" -m pip install -q -r "$REPO_DIR/requirements.txt" || die "必要な部品をインストールできませんでした"
  # macOS 標準の Python は LibreSSL を使っており、新しい urllib3 だと毎回警告が出るため古い版にする
  if "$PY" -c 'import ssl, sys; sys.exit(0 if "LibreSSL" in ssl.OPENSSL_VERSION else 1)'; then
    "$PY" -m pip install -q "urllib3<2" || true
  fi
  say "  完了"

  # render: browser の商品用。JavaScript で後から表示されるページを、画面のないブラウザで開く
  say "● ブラウザ機能（Playwright）を準備しています（初回は数分・約 200MB）"
  if "$PY" -m pip install -q "playwright<2" && "$PY" -m playwright install chromium; then
    say "  完了"
  else
    say "⚠ ブラウザ機能を準備できませんでした（render: browser の商品だけ確認できません。ほかは動きます）"
  fi

  mkdir -p "$LOG_DIR"

  if [ -f "$CONFIG" ]; then
    say "● 通知先の設定はすでにあります（作り直すときは mac/config.env を削除して setup し直してください）"
  else
    say "● 通知先を設定します（入力した内容は Mac の中だけに保存され、GitHub には送られません）"
    topic=""
    while [ -z "$topic" ]; do
      read -r -p "  ntfy のトピック名（iPhone の ntfy アプリで登録した名前）: " topic
    done
    read -r -s -p "  Yahoo の Client ID（Mac で Yahoo 検索をしないなら空のまま Enter）: " yahoo
    say ""
    umask 077
    {
      say "# notice-app の Mac 用設定（GitHub には送られません）"
      printf 'NTFY_TOPIC=%q\n' "$topic"
      printf 'YAHOO_APP_ID=%q\n' "$yahoo"
    } > "$CONFIG"
    chmod 600 "$CONFIG"
    say "  保存しました: $CONFIG"
  fi

  say ""
  say "準備ができました。次は以下を順に試してください。"
  say "  1) bash mac/notice.sh test       … iPhone にテスト通知が届くか"
  say "  2) bash mac/notice.sh dry-run \"【Amazon】山崎12年\"   … Amazon が読めるか"
  say "  3) bash mac/notice.sh start      … 5 分ごとの自動監視を開始"
}

cmd_test() {
  load_env
  "$PY" "$REPO_DIR/monitor.py" --test
}

cmd_dry_run() {
  pull_latest
  load_env
  if [ $# -gt 0 ]; then
    "$PY" "$REPO_DIR/monitor.py" --dry-run --only "$*"
  else
    "$PY" "$REPO_DIR/monitor.py" --dry-run
  fi
}

cmd_run() {
  # launchd（Mac の定時実行の仕組み）から 5 分ごとに呼ばれる
  mkdir -p "$LOG_DIR"
  if [ -f "$LOG" ] && [ "$(wc -c < "$LOG" | tr -d ' ')" -gt "$LOG_MAX_BYTES" ]; then
    mv -f "$LOG" "$LOG.old"
  fi
  {
    say ""
    say "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
    pull_latest
    load_env
    "$PY" "$REPO_DIR/monitor.py"
    say "(終了コード $?)"
  } >> "$LOG" 2>&1
}

write_plists() {
  mkdir -p "$AGENTS" "$LOG_DIR"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$MAC_DIR/notice.sh</string>
    <string>run</string>
  </array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/launchd.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/launchd.log</string>
</dict>
</plist>
EOF

  # 監視中は Mac がスリープしないようにする（画面は消えてよい。電源アダプタ接続時のみ有効）
  cat > "$AWAKE_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AWAKE_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>-s</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF
}

unload_agents() {
  launchctl bootout "$GUI/$LABEL" >/dev/null 2>&1 || true
  launchctl bootout "$GUI/$AWAKE_LABEL" >/dev/null 2>&1 || true
}

cmd_start() {
  load_env
  write_plists
  unload_agents
  launchctl bootstrap "$GUI" "$AWAKE_PLIST" || die "スリープ防止の登録に失敗しました"
  launchctl bootstrap "$GUI" "$PLIST" || die "自動監視の登録に失敗しました"
  say "● 自動監視を開始しました（5 分ごと。Mac を再起動しても自動で再開します）"
  say "  1 回目はすぐに実行されます。1 分ほどしたら bash mac/notice.sh status で結果を確認できます"
}

cmd_stop() {
  unload_agents
  rm -f "$PLIST" "$AWAKE_PLIST"
  say "● 自動監視を止めました（再開は bash mac/notice.sh start）"
}

cmd_status() {
  if launchctl print "$GUI/$LABEL" >/dev/null 2>&1; then
    say "● 自動監視: 動作中（5 分ごと）"
  else
    say "● 自動監視: 停止中（開始するには bash mac/notice.sh start）"
  fi
  if launchctl print "$GUI/$AWAKE_LABEL" >/dev/null 2>&1; then
    say "● スリープ防止: 有効"
  else
    say "● スリープ防止: 無効"
  fi
  if [ -f "$LOG" ]; then
    say ""
    say "── 直近の記録 ──"
    # 最後の 1 回分を表示する
    awk '/^===== /{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}' "$LOG"
  else
    say "（まだ記録がありません）"
  fi
}

cmd_log() {
  [ -f "$LOG" ] || die "まだ記録がありません"
  tail -n 60 -f "$LOG"
}

sub="${1:-}"
[ $# -gt 0 ] && shift
case "$sub" in
  setup)   cmd_setup ;;
  test)    cmd_test ;;
  dry-run) cmd_dry_run "$@" ;;
  run)     cmd_run ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  log)     cmd_log ;;
  *)
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
