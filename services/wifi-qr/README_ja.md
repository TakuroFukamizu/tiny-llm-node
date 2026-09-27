# wifi-qr: QR コードによるヘッドレス WiFi 設定

**Language:** [English](README.md) | 日本語

`wifi-qr` は、モニタもキーボードもない tiny-llm-node を WiFi に参加させるための小さな systemd サービスです。M5Stack 用 QR コードスキャナーユニット（I2C）を Pi 5 の GPIO ヘッダに接続し、スマートフォンが生成する WiFi 共有 QR コードをかざすと、デーモンがそれを読み取り、NetworkManager の永続プロファイルを作成して接続します。いつでも別の QR をかざせば接続先が切り替わり、プロファイルは再起動後も維持されます。

> ⚠️ **机上設計 — 実機未検証（2026-09-26）。**
> このディレクトリの内容はすべてベンダー資料と設計仕様
> （[docs/superpowers/specs/2026-09-26-wifi-qr-provisioning-design.md](../../docs/superpowers/specs/2026-09-26-wifi-qr-provisioning-design.md)）
> から書き起こしたものです。レジスタマップ、タイミング、消費電流は実機で**未計測**です。
> 実機で [RUNBOOK.md](RUNBOOK.md) を実施し、下の表を埋めてください。

### 実測値

| 項目 | 値 |
|---|---|
| 検証日 | _（未実施）_ |
| スキャナーのファームウェアバージョン（`--probe`） | _（未実施）_ |
| スキャンから `connected` までの秒数 | _（未実施）_ |
| スキャナーの消費電流（5V 系） | _（未計測）_ |
| `i2cdetect -y 1` の結果 | _（未実施）_ |

---

## ハードウェア

| 項目 | 内容 | 出典 |
|---|---|---|
| 製品 | M5Stack 用 QR コードスキャナーユニット（STM32F030） | [Switch Science 9508](https://www.switch-science.com/products/9508)、[M5Stack M5Unit-QRCode（GitHub）](https://github.com/m5stack/M5Unit-QRCode) |
| インターフェース | I2C、アドレス `0x21` | [I2C プロトコル表](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/770/UnitQrcode.pdf) |
| モード切替 | 本体のスライドスイッチ。**I2C = "Down"**、UART = "Up"（[回路図](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/627/SCH_UNIT_QRCODE_V1.0.pdf)のラベル） | 回路図 |
| ロジックレベル | I2C プルアップは **3V3** へ — Pi の GPIO に直結でき、**レベル変換不要** | 回路図（R15/R16） |
| 電源 | Grove コネクタ経由の 5V | 回路図 |
| 視野角 | 約 ±55° | 製品ページ |

**Grove–ジャンパ（メス）変換ケーブル**（4 線）が 1 本必要です。Pi の 40 ピンヘッダに次のとおり配線します:

| Grove 線色 | 信号 | Pi ヘッダ | GPIO |
|---|---|---|---|
| 赤 | 5V | pin 2（または 4） | — |
| 黒 | GND | pin 6 | — |
| 黄 | SDA | pin 3 | GPIO2 |
| 白 | SCL | pin 5 | GPIO3 |

**線色は M5Stack 製ケーブルの慣習です。Seeed 製 Grove ケーブルは逆（黄 = SCL、白 = SDA）**なので、色ではなく位置で確認してください: Grove コネクタの 4 線は必ず SCL・SDA・VCC・GND の順に並んでいるため、赤の隣の信号線が SDA（pin 3）、赤から最も遠い外側の信号線が SCL（pin 5）です。2 本を入れ替えても電気的には無害で（両線とも 3V3 プルアップ）、正しくなるまで `i2cdetect` に何も出ないだけです。

取り付け: 視野角が ±55° と広いため、視野を横切った QR コードを何でも読んでしまいます。机や画面ではなく、利用者が自然にスマートフォンをかざす位置（ノード前面など）を向くように固定してください。ユニットはデコードに成功するたびにブザーが鳴ります。

---

## QR コードの形式

デーモンは標準の WiFi 共有形式（ZXing / Android / iOS ショートカット）を受け付けます:

```
WIFI:T:WPA;S:MyNetwork;P:secret123;;
WIFI:T:SAE;S:MyWPA3;P:secret123;;
WIFI:T:nopass;S:OpenCafe;;
WIFI:T:WPA;S:Hidden;P:secret123;H:true;;
```

| フィールド | 意味 |
|---|---|
| `T` | 認証方式: `WPA`、`WPA2`、`WPA3`、`SAE`、`WEP`、`nopass`、または空（= オープン）。大文字小文字は区別しない。`WPA`/`WPA2`/`WPA3` はいずれも `wpa-psk` で適用する（`WPA3` は WPA2/WPA3 混在モードの意味）。WPA3-only の AP には `SAE` を使う |
| `S` | SSID（必須） |
| `P` | パスワード。WPA/SAE/WEP では必須。WPA は 8〜63 文字（または 64 桁 hex）。SAE と WEP は長さを検証しない |
| `H` | 隠し SSID なら `true` |

エスケープ: `S` と `P` の中の `;` `:` `,` `\` は `\;` `\:` `\,` `\\` と書きます。
`WPA-EAP`（企業向け認証）および `E:` `A:` `I:` `PH2:` キーは非対応として拒否します。`S` や `P` の中の制御文字（NUL、改行など）も拒否します。

**Android** は「WiFi 設定 → 共有」でこの形式をそのまま出力します。**iOS** ではショートカットや任意の QR 生成ツールを使います。Mac や Linux の端末では:

```bash
# brew install qrencode   /   sudo apt install qrencode
qrencode -t ANSIUTF8 'WIFI:T:WPA;S:MyNet;P:pass1234;;'
```

---

## 動作の仕組み

1. デーモンは I2C 経由でスキャナーの `READY` レジスタを 200 ms ごとにポーリングします（`WIFI_QR_POLL_INTERVAL`）。
2. データが用意できると（`READY` = 1。ポーリングの間に 2 回デコードされた場合は 2 になるが、どちらも同じように読む）、ペイロードを読み出し（長さレジスタ → データレジスタから全長を 1 回で読み出し。ファームウェアはデータ領域内のアドレスオフセットを無視するため、分割読み出しは 1 チャンクより長いデータを壊す）、`READY` をクリアし、`WIFI:` 文字列を解析します。
3. 資格情報を `nmcli` で **`wifi-qr-<ssid>` という名前の永続 NetworkManager プロファイル**として適用します。同名の既存プロファイルは置き換えるので、パスワード変更後に再スキャンすればそのまま使えます。
4. `nmcli connection up` を最大 30 秒待ちます。成功すると SSID とインターフェースのアドレスをログに出し、失敗するとプロファイルを削除します。

スキャンは**常時有効**です。新しい QR をかざせばそのネットワークに切り替わります。デーモンが直前に適用したものと同じ資格情報で、かつその SSID に接続中の場合は `already connected` とログに出して無視します（サービス再起動後の最初のスキャンは常に適用されます）。

---

## CLI

```bash
python3 -m wifi_qr             # デーモンとして常駐（systemd ユニットが行うのと同じ）
python3 -m wifi_qr --probe     # I2C 応答、ファームウェアバージョン、トリガーモードを表示して終了
python3 -m wifi_qr --dry-run   # 解析結果（マスク済み）をログに出すだけで nmcli を呼ばない
python3 -m wifi_qr --once      # 1 回適用したら終了
python3 -m wifi_qr --verbose   # DEBUG ログ
```

フラグは組み合わせられます（テストでは `--dry-run --once` が定番）。インストール済みの実機では `sudo PYTHONPATH=/opt/wifi-qr python3 -m wifi_qr ...` で実行し、I2C バスを空けるために先にサービスを停止してください。`--probe` はトリガーモードの読み出しで応答の有無を判定します。ファームウェアバージョンのレジスタアドレスは未確認のため、その読み出しだけが失敗した場合は `firmware version: unreadable (...)` と表示して終了コード 0 のままです（デーモンも同様に `firmware=unknown` とログに出して通常どおり起動します）。

### 設定（`/etc/default/wifi-qr`）

systemd ユニットは次の環境変数を `/etc/default/wifi-qr` から読みます。コマンドラインでも同じ変数が使えます。

| 変数 | 既定値 | 意味 |
|---|---|---|
| `WIFI_QR_I2C_BUS` | `1` | I2C バス番号（Pi 5 では `/dev/i2c-1`） |
| `WIFI_QR_I2C_ADDR` | `0x21` | スキャナーの I2C アドレス |
| `WIFI_QR_IFACE` | `wlan0` | nmcli に渡す WiFi インターフェース |
| `WIFI_QR_POLL_INTERVAL` | `0.2` | `READY` ポーリング間隔（秒） |
| `WIFI_QR_FAKE_SCANNER` | _（未設定）_ | `WIFI:...` ペイロードを設定すると、I2C の代わりにメモリ上の偽スキャナーを使う（ハードウェア無しのテスト用） |

---

## インストール

Pi 上で（[docs/software_setup_ja.md](../../docs/software_setup_ja.md) に従ってセットアップ済みの Raspberry Pi OS Trixie）:

```bash
cd ~/tiny-llm-node/services/wifi-qr
sudo ./install.sh
```

インストーラは冪等です。`python3-smbus2` と `i2c-tools` を導入し、`/boot/firmware/config.txt` で I2C を有効化し、パッケージを `/opt/wifi-qr/` に配置し、`/etc/default/wifi-qr` が無ければ作成し、`wifi-qr.service` を有効化して起動します。Pi 5 では `raspi-config` が I2C オーバーレイをその場で適用するため、初回の有効化でも通常はサービスが起動して終了コード 0 で終わります。**I2C を有効化しても `/dev/i2c-1` が現れない場合に限り `REBOOT REQUIRED:` と表示して終了コード 3 で終わります** — 再起動してから続けてください。

```bash
journalctl -u wifi-qr -f       # ログを追う
systemctl status wifi-qr
```

期待する出力と失敗時の分岐を含む実機での手順は **[RUNBOOK.md](RUNBOOK.md)**（Claude Code が SSH 越しに実行する前提で書かれています）を参照してください。

### アンインストール

`sudo ./install.sh --uninstall` でサービスの停止・無効化と `/opt/wifi-qr` の削除ができます（`/etc/default/wifi-qr`、NetworkManager プロファイル、apt パッケージは残します）。手動で行う場合:

```bash
sudo systemctl disable --now wifi-qr
sudo rm -f /etc/systemd/system/wifi-qr.service /etc/default/wifi-qr
sudo rm -rf /opt/wifi-qr
sudo systemctl daemon-reload
# 任意: デーモンが作成したプロファイルを削除する
nmcli -t -f UUID,NAME connection show | awk -F: '$2 ~ /^wifi-qr-/ {print $1}' \
  | xargs -r -n1 sudo nmcli connection delete uuid
```

---

## セキュリティと運用上の注意

- **物理アクセス = ネットワークの支配権。** スキャンは常時有効なので、スキャナーの前に QR コードを出せる人は誰でも、接続中であってもノードの接続先を変えられます。本設計はノードに物理的に触れる人を信頼する前提です。
- 資格情報は NetworkManager のキーファイル（`/etc/NetworkManager/system-connections/`、パーミッション 0600）にのみ保存されます。
- パスワードは**ログには絶対に出しません**。`WifiCredential` は `repr()`/`str()` でマスクし、nmcli の失敗出力もそのまま出さず要約します。ただし `nmcli` の引数として渡すため、実機の `ps`/`/proc` から瞬間的に見えます。root で運用する単一利用者機であるため、これは許容しています。
- **WiFi の国コードを必ず設定してください。** Raspberry Pi OS は国コード未設定だと `wlan0` が rfkill でブロックされたままになり、接続が黙って失敗します。runbook では `raspi-config nonint do_wifi_country <CC>` を必須手順にしています。
- スキャナーのカメラは常時動作します。赤い照準ライトはスキャンエンジンが制御しており、アイドル中は消えている可能性があるため、給電の目安にしないでください。5V 系の消費電流は数十〜百数十 mA 程度の見込みですが**未計測**です。[docs/power.md](../../docs/power.md) を参照。

---

## 今後の拡張: ステータス LED

本サービスには現時点で LED などの利用者向けフィードバックは**実装されていません**。デーモンはそのためのフックだけを用意しています:

- `wifi_qr.daemon.Feedback` — `on_event(event: FeedbackEvent)` を持つ Protocol。
- `FeedbackEvent` — `SCANNER_READY`、`SCAN_RECEIVED`、`APPLYING`、`CONNECTED`、`FAILED`、`SCANNER_ERROR`。
- 既定は何もしない `NullFeedback`。

ノード全体のステータス LED を実装する際は、`Feedback` を実装するクラスを追加し、`__main__.py` で `NullFeedback` と差し替えます。想定する表示（仕様第 10 節より）: 待機 → 点灯、読み取り確認 → 短い点灯、接続試行中 → 点滅、成功 → 一定時間点灯後に待機へ、失敗 → 速い点滅、スキャナー異常 → 別パターン。デーモンは LED の駆動方式（GPIO、Pi 本体の ACT LED、I2C ドライバなど）に依存しません。

---

## 開発

ユニットテストはどのマシンでも動きます。ハードウェアも `smbus2` も不要です（遅延インポート）。

```bash
cd services/wifi-qr
python3 -m venv .venv && . .venv/bin/activate
pip install pytest smbus2
python -m pytest -q
```

または [uv](https://docs.astral.sh/uv/) で: `uv run --with pytest --with smbus2 python -m pytest -q`。

ハードウェア無しで CLI を試す:

```bash
WIFI_QR_FAKE_SCANNER='WIFI:T:nopass;S:wifi-qr-test;;' python -m wifi_qr --dry-run --once
```
