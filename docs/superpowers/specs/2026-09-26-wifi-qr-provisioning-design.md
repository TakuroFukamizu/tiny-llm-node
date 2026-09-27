# 設計仕様: QR コードによるヘッドレス WiFi 設定（wifi-qr）

**作成日: 2026-09-26 / 状態: 設計（実機未検証）**

M5Stack 用 QR コードスキャナーユニット（STM32F030、Switch Science 9508）を
Raspberry Pi 5 の GPIO に接続し、スマートフォンが生成する WiFi 共有 QR コードを
かざすだけで、モニタもキーボードもない tiny-llm-node を WiFi に接続できるようにする。

---

## 1. 目的と成功条件

### 目的

tiny-llm-node は可搬性を掲げているが、持ち込んだ先のネットワークに参加させるには
現状 Imager での事前設定か、有線 LAN + SSH が必要である。
本機能により、**電源投入後に QR をかざすだけで WiFi 接続が完了する**状態にする。

### 利用者

- 会場やオフィスなど、出先でノードをネットワークに参加させたい本プロジェクトの利用者
- 実機上でのセットアップ作業は **Claude Code に runbook を実行させる**ことを前提とする

### 成功条件

1. 電源投入から 60 秒以内にスキャナーが待機状態になる
2. スマートフォンの WiFi 共有 QR をかざすと、ユニットのブザーが鳴り、30 秒以内に接続が完了する
3. 接続後、同一ネットワーク上から `ssh <user>@<hostname>.local` で到達できる
4. いつでも別の QR をかざして接続先を切り替えられる（常時スキャン）
5. 再起動後も接続設定が維持される（NetworkManager のプロファイルとして永続化）

### スコープ外

- WPA-EAP（企業向け認証）。QR に `T:WPA-EAP` 等が含まれる場合は明示的に拒否してログに残す
- スキャナー本体のファームウェア更新、ユニット側の設定変更（ブザー音量など）
- UART モードでの接続（I2C のみ対応）
- 接続結果を画面・音・LED で通知する仕組み。**ステータス LED は今後別途実装する**（第 10 節）。本機能ではデーモンに通知用のフック（`Feedback` インターフェース）だけを用意し、既定は何もしない実装とする

---

## 2. ハードウェア

### 対象ユニット

| 項目 | 内容 | 出典 |
|---|---|---|
| 製品 | M5Stack 用 QR コードスキャナーユニット（STM32F030） | [Switch Science 9508](https://www.switch-science.com/products/9508) |
| MCU | STM32F030F4P6、3.3V LDO（HX6306P332MR）で駆動 | [回路図](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/627/SCH_UNIT_QRCODE_V1.0.pdf) |
| 電源 | Grove 経由 5V | 同上 |
| I2C プルアップ | SCL/SDA とも 10kΩ で **3V3** へ | 同上（R15/R16） |
| I2C アドレス | 0x21 | [I2C プロトコル表](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/770/UnitQrcode.pdf) |
| インターフェース切替 | 本体スライドスイッチ。回路図ラベルでは I2C = Down、UART = Up | 回路図 |
| サイズ | 65.8 × 18.4 × 15.8 mm | 製品ページ |

I2C ラインは 3.3V ロジックなので **Raspberry Pi の GPIO に直結できる**（レベル変換不要）。
5V は電源供給にのみ使う。

### 配線（Grove → Pi 40 ピンヘッダ）

| Grove 線色 | 信号 | Pi ヘッダ | GPIO |
|---|---|---|---|
| 赤 | 5V | pin 2（または 4） | — |
| 黒 | GND | pin 6 | — |
| 黄 | SDA | pin 3 | GPIO2 |
| 白 | SCL | pin 5 | GPIO3 |

線色は M5Stack 製ケーブルの慣習。Seeed 製 Grove ケーブルは逆（黄 = SCL、白 = SDA）なので、色ではなく位置で確認する: Grove コネクタの 4 線は SCL・SDA・VCC・GND の順に固定されており、赤の隣の信号線が SDA、赤から最も遠い外側の信号線が SCL。入れ替えても無害（両線とも 3V3 プルアップ）。

必要部品: Grove–ジャンパ（メス）変換ケーブル 1 本。
X1010 は Pi の下面に付くため 40 ピンヘッダは上面に露出しており、アクティブクーラーとも干渉しない。

### 実機で確認が必要な点（机上仕様）

- STM32 の I2C スレーブはクロックストレッチを行うことが多い。Pi 5（RP1）では問題ないとされるが、旧世代 Pi では既知の不具合がある。**Pi 5 のみを対象**とし、runbook の最初の検証項目で確認する
- ファームウェアバージョンのレジスタは I2C プロトコル表では 0x00F0、Arduino ライブラリでは 0x00FE と記載が食い違う。診断表示にのみ使い、動作には依存させない
- データレジスタ（0x1000〜）は **1 トランザクションで全長を読む**（最大 512 バイト）。ベンダーファームウェア（M5Unit-QRCode-Internal-FW の `Slave_Complete_Callback`）はデータ領域内のどのアドレスを指定してもバッファ先頭から送り、レジスタアドレス書き込みのたびに送信インデックスを 0 に戻すため、アドレスを進めながらの分割読み出しは 2 チャンク目以降が先頭の再送になり成立しない（Arduino / UiFlow のベンダードライバも全長を一括で読む）。Linux の I2C_RDWR は 1 メッセージ 8192 バイトまで許すので 512 バイトの一括読みは問題ないはずだが、実機では未検証。runbook 6b で 32 バイト超のテスト QR を読んで確認する
- 照準ライト（赤いライン）はスキャンエンジンが制御し、ファームウェアは最初の I2C 通信までエンジンを設定しない。エンジン既定ではアイドル中に消灯している可能性があるため、点灯を給電の判定に使わない（runbook Step 2 は参考情報とし、確定判定は `i2cdetect` で行う）

---

## 3. ソフトウェア構成

### 配置

```
services/wifi-qr/
  README.md            概要・配線・QR 形式・運用上の注意（英語）
  README_ja.md         同 日本語
  RUNBOOK.md           実機セットアップ手順（Claude Code が実行することを前提）
  pyproject.toml       パッケージ定義（pytest 用。実機では apt のみで動く）
  install.sh           冪等なインストーラ
  wifi-qr.service      systemd ユニット
  wifi-qr.env.example  設定ファイル雛形（/etc/default/wifi-qr）
  wifi_qr/
    __init__.py
    __main__.py        CLI エントリ（python3 -m wifi_qr）
    scanner.py         Unit QRCode I2C ドライバ
    payload.py         WIFI: ペイロードのパーサ
    network.py         NetworkManager（nmcli）ラッパー
    daemon.py          ポーリングループと状態管理、Feedback フック
  tests/
    test_payload.py
    test_daemon.py
    test_network.py
```

言語は Python 3（Trixie 標準の 3.13）。依存は Debian パッケージのみ:
`python3-smbus2`（0.4.3）、`i2c-tools`、`network-manager`（OS 標準）。
venv や pip は使わない（PEP 668 の外部管理環境を避け、apt のみで再現できるようにする）。

### 各ユニットの責務

**scanner.py — `UnitQRCode`**

- `smbus2.SMBus` と `i2c_msg` の `i2c_rdwr` で、2 バイト（リトルエンディアン）のレジスタアドレス書き込み → repeated start → 読み出しを行う
- レジスタ: `TRIGGER=0x0000`, `READY=0x0010`, `LENGTH=0x0020`, `TRIGGER_MODE=0x0030`, `TRIGGER_KEY=0x0040`, `FW_VERSION=0x00FE`（診断のみ）, `DATA=0x1000`
- API: `ready() -> int`（0/1/2）、`read_payload() -> bytes`（長さ読み出し → DATA を 0x1000 から全長 1 回で読み出し → READY に 0 を書いてクリア）、`set_trigger_mode(auto: bool)`、`firmware_version() -> int`
- 長さが 0 または 512 超なら `ScannerError` を送出し、READY をクリアする
- I2C の `OSError` はそのまま上位へ伝播させ、daemon 側でリトライする

**payload.py — `parse_wifi_qr(text: str) -> WifiCredential`**

- 標準形式 `WIFI:T:WPA;S:ssid;P:pass;H:false;;` を解釈する
- エスケープ `\;` `\:` `\,` `\\` を解除する
- `T` は大文字小文字を無視し、`WPA` / `WPA2` / `WPA3` / `SAE` / `WEP` / `nopass` / 空を受け付ける。nmcli の key-mgmt への対応は `WPA` / `WPA2` / `WPA3` → `wpa-psk`（`WPA3` は WPA2/WPA3 混在モードの意味）、`SAE` → `sae`（WPA3-only の AP はこちら）、`WEP` → `none` + WEP キー、`nopass` / 空 → 指定なし。`WPA-EAP` および `E:` `A:` `I:` `PH2:` キーの存在は `UnsupportedAuth` として拒否する
- `H:true` は隠し SSID として扱う
- `S` が空、`WPA` 系・`WEP` で `P` が空、`WPA` で `P` が 8〜63 文字の範囲外かつ 64 桁 hex でもない（WPA-PSK の制約。`SAE` は NetworkManager が長さ制限を免除するため、`WEP` と同様に長さ検証しない）、`WIFI:` 接頭辞なし、`S` または `P` に制御文字（0x00〜0x1F、0x7F。NUL は argv に渡せず、改行はプロファイル名の照合とログを壊す）、はいずれも `PayloadError`
- 戻り値は `WifiCredential(ssid, password, security, hidden)` の frozen dataclass。`__repr__` でパスワードをマスクする

**network.py — `NetworkManagerBackend`**

- `apply(cred) -> ConnectResult`:
  1. 既存の `wifi-qr-<ssid>` プロファイルがあれば削除する
  2. `nmcli connection add type wifi con-name wifi-qr-<ssid> ifname <iface> ssid <ssid>` に、security に応じて `wifi-sec.key-mgmt wpa-psk|sae wifi-sec.psk <pw>`、WEP なら `wifi-sec.key-mgmt none wifi-sec.wep-key0 <pw> wifi-sec.wep-key-type 1`、nopass なら security 指定なし、隠しなら `802-11-wireless.hidden yes` を付ける
  3. `nmcli --wait 30 connection up wifi-qr-<ssid>`
  4. 失敗したらプロファイルを削除して `ConnectResult(ok=False, reason=...)`
- `current_ssid() -> str | None`: `nmcli -t -f ACTIVE,SSID device wifi` から取得
- `ip_addresses(iface) -> list[str]`: 成功ログ用
- パスワードは nmcli の引数として渡す。root 権限の単一利用者機であり、`ps` からの瞬間的な露出は許容する（README に明記）。ログには絶対に出力しない
- テスト用に `subprocess.run` 相当の呼び出しを注入できるようにする

**daemon.py — `Feedback` フック（将来のステータス LED 用）**

- `Feedback` は `on_event(event: FeedbackEvent) -> None` を持つ Protocol。`FeedbackEvent` は
  `SCANNER_READY` / `SCAN_RECEIVED` / `APPLYING` / `CONNECTED` / `FAILED` / `SCANNER_ERROR` の Enum
- 既定実装は `NullFeedback`（何もしない）。デーモンは状態遷移のたびに `on_event` を呼ぶ
- ステータス LED を実装する際は、この Protocol を実装するクラスを追加し、`__main__.py` で差し替える（第 10 節）

**daemon.py — `Daemon`**

状態機械（常時スキャン）:

```
IDLE --(READY==1)--> READ --(parse ok)--> APPLY --(ok)--> IDLE
                        |                    |
                        +--(parse error)-----+--(fail)--> IDLE
```

- 起動時: `set_trigger_mode(auto=True)` を書く。I2C が応答しなければ 5 秒間隔で最大 12 回リトライしてから終了する（systemd の `Restart=on-failure` で再起動）。その後ファームウェアバージョンをログに出すが、これは診断専用（第 2 節）なので読めなければ `firmware=unknown` と記録して起動を続ける。`--probe` も同様にトリガーモードの読み出しで応答を判定し、FW だけ読めなければ `firmware version: unreadable (...)` と表示して終了コード 0 とする
- ポーリング間隔は既定 200 ms
- READY==2 は「前回の読み出し以降に 2 回以上デコードした」状態（ファームウェアのカウンタは 2 で飽和し、DATA を読むか 0 を書くまで保持される）。バッファには最新のデコード結果があるので、1 と同様にそのまま `read_payload()` する（再ポーリングや破棄はしない）
- **重複抑止**: 直前に適用した資格情報と同一で、かつ `current_ssid()` が一致して接続済みなら、適用せず「already connected」をログして無視する。それ以外（別 SSID、同 SSID でパスワード違い、未接続）は常に適用する
- 適用中（`APPLY`）は新たなスキャンを無視し、完了後に READY をクリアして取りこぼしをなくす
- I2C の `OSError` は 1 秒待って継続する。連続 30 回で終了する（systemd 再起動）。「連続」はポーリング 1 回の全体で数える: READY==0 を読めたとき、または `read_payload()` が返ったときだけカウンタを 0 に戻す（READY が読めても LENGTH/DATA の読み出しが失敗し続ければカウンタは進む）
- 依存（scanner / backend / feedback / sleep）はコンストラクタ注入とし、テストでは偽物を差し込む

**__main__.py — CLI**

| オプション | 動作 |
|---|---|
| （なし） | デーモンとして常駐 |
| `--probe` | I2C 応答、ファームウェアバージョン、トリガーモードを表示して終了 |
| `--dry-run` | 解析結果（パスワードはマスク）をログに出すだけで nmcli を呼ばない |
| `--once` | 1 回適用したら終了 |
| `--verbose` | DEBUG ログ |

設定は環境変数（`/etc/default/wifi-qr` を systemd の `EnvironmentFile` で読む）:
`WIFI_QR_I2C_BUS=1`, `WIFI_QR_I2C_ADDR=0x21`, `WIFI_QR_IFACE=wlan0`,
`WIFI_QR_POLL_INTERVAL=0.2`（手動実行時は `sudo VAR=値 ... python3 -m wifi_qr` と `sudo` の直後に付ける。`/etc/default/wifi-qr` は systemd だけが読む）

### systemd ユニット

- `After=NetworkManager.service`、`Wants=NetworkManager.service`
- root で実行（`/dev/i2c-1` と nmcli の両方が必要なため）。`ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, `NoNewPrivileges=yes`
- `Restart=on-failure`, `RestartSec=5`
- ログは journald（`journalctl -u wifi-qr`）

### install.sh（冪等）

1. `apt-get install -y python3-smbus2 i2c-tools`
2. `raspi-config nonint do_i2c 0`（`/boot/firmware/config.txt` に `dtparam=i2c_arm=on` を入れる。既に有効なら何もしない）
3. `wifi_qr/` を `/opt/wifi-qr/wifi_qr/` に `rsync --delete` で配置
4. `/etc/default/wifi-qr` が無ければ雛形をコピー
5. ユニットファイルを `/etc/systemd/system/` に置き、`daemon-reload`、`enable --now`
6. I2C を今回初めて有効化した場合は「再起動が必要」と出力して終了コード 3 を返す

---

## 4. QR コードの仕様

スマートフォン（Android の WiFi 共有、iOS のショートカット等）が生成する
ZXing 由来の標準形式をそのまま使う:

```
WIFI:T:WPA;S:MyNetwork;P:secret123;;
WIFI:T:SAE;S:MyWPA3;P:secret123;;
WIFI:T:nopass;S:OpenCafe;;
WIFI:T:WPA;S:Hidden;P:secret123;H:true;;
```

`S` / `P` 内の `;` `:` `,` `\` はバックスラッシュでエスケープされる。
利用者向けには、README に QR 生成コマンド例（`qrencode -t ANSIUTF8 'WIFI:...'`）を載せる。

---

## 5. セキュリティと運用上の注意

- **常時スキャンの含意**: 接続済みでも、スキャナーの視野に QR を入れられる人は誰でもノードの接続先を変えられる。物理アクセスできる人を信頼する前提であり、README に明記する
- スキャナーの視野角は ±55° と広い。誤読を避けるため、スキャナーはノード前面など意図的にかざす位置に固定する
- 資格情報は NetworkManager のキーファイル（`/etc/NetworkManager/system-connections/`、0600）にのみ保存される
- ログにパスワードを出さない。`WifiCredential.__repr__` でマスクし、nmcli の失敗出力もそのまま出さずに要約する
- **WiFi 国コード**: Raspberry Pi OS は国コード未設定だと wlan0 が rfkill でブロックされ、QR を読んでも黙って失敗する。runbook で `raspi-config nonint do_wifi_country <CC>` を必須手順にする（既定 JP、runbook の引数）
- スキャナーは常時カメラが動作する（照準ライトはエンジン制御で、アイドル中は消えている可能性がある）。消費電力は 5V 系で数十〜百数十 mA 程度の見込み（未計測）。power.md の予算に「未計測・要実測」として追記する

---

## 6. ドキュメントの変更

| ファイル | 変更 |
|---|---|
| `docs/hardware.md` | 「QR code scanner (WiFi provisioning)」節を追加。製品リンク、配線表 |
| `docs/bill_of_materials.md` | additional components に QR ユニットと Grove 変換ケーブルを追加 |
| `docs/assembly.md` | 手順に「connect QR scanner to GPIO」を追加 |
| `docs/power.md` | スキャナーの消費電流を未計測項目として追加 |
| `docs/software_setup.md` / `_ja.md` | 末尾に「8. WiFi provisioning (optional)」を追加し `services/wifi-qr/RUNBOOK.md` へ誘導 |
| `README.md` / `README_ja.md` | Repository Structure に `services/` を追加 |
| `services/wifi-qr/README.md` / `README_ja.md` | 新規 |
| `services/wifi-qr/RUNBOOK.md` | 新規 |

既存ドキュメントの流儀に合わせ、実機未検証の記述には冒頭で「机上」と明記する。
実機で runbook を実施した後、その結果（日付・ファームウェアバージョン・接続までの秒数・消費電流）を
README と本仕様に追記して「実測」に格上げする。

---

## 7. RUNBOOK.md の要件

Claude Code が実機上で実行することを前提に、次の性質をもたせる:

- 各ステップは「実行するコマンド」「期待する出力」「失敗時の分岐」の 3 点セットで書く
- 前提確認から始める: `uname -r`、`nmcli general`、`ls /dev/i2c-*`、`rfkill list`
- 段階的に検証する: (1) `i2cdetect -y 1` で `21` が見える → (2) `--probe` でファームウェア応答 → (3) `--dry-run --once` でテスト用 QR を読める → (4) `--once` で実際に接続 → (5) サービス有効化 → (6) 再起動後の自動起動と再接続
- テスト用 QR は利用者自身の端末（スマートフォン／PC の QR 生成ツール）で表示してもらうか、Pi で `qrencode` の PNG を作って渡す（Claude Code が実行する場合、端末に描画した ANSI QR は利用者の画面に届かない）。ダミーは `WIFI:T:nopass;S:wifi-qr-test-...;;` のオープンネットワークで dry-run し、32 バイトを超える文字列にして DATA 一括読み出しを検証する
- 手動実行の `--once` は `timeout` で有界にし、SSH が WiFi 経由の場合は `systemd-run` で切り離して journal から結果を読む。`journalctl -f` のような終了しないコマンドは使わない
- 破壊的操作（`config.txt` 編集、再起動）は明示し、再起動が必要なステップでは runbook を中断して再開位置を示す
- 最後に「実測値の記録」欄を設け、README への反映を促す

---

## 8. テスト

- **ユニットテスト（Mac で実行）**: `pytest` で `payload.py`（正常系・エスケープ・各認証方式・異常系）、`daemon.py`（偽スキャナーと偽バックエンドで状態遷移、重複抑止、READY==2、I2C エラーのリトライ、Feedback イベントの発火順）、`network.py`（nmcli に渡す引数の組み立てと失敗時のクリーンアップ。subprocess は偽物）、`scanner.py`（偽 I2C バスでレジスタアドレスのエンコードと DATA の一括読み出し）
- **ハードウェア無しでの結合確認**: `--dry-run` は `WIFI_QR_FAKE_SCANNER=<payload>` を指定すると偽スキャナーを使い、I2C なしで CLI を通せるようにする（テストと runbook の前段で使う）
- **実機テスト**: RUNBOOK.md の段階的検証

---

## 9. 実装順序（概要）

1. `payload.py` + テスト
2. `network.py` + テスト
3. `scanner.py`（実機がないためロジックのみ。I2C 呼び出しは注入可能に）
4. `daemon.py`（`Feedback` フック含む）+ テスト
5. `__main__.py`、`install.sh`、`wifi-qr.service`、env 雛形
6. README（EN/ja）、RUNBOOK.md
7. 既存 docs の追記

---

## 10. 将来の拡張: ステータス LED（本仕様では実装しない）

ヘッドレス運用では「QR を読んだが接続に失敗した」ことを利用者が知る手段がない。
ノード全体のステータス LED を実装する際に、本デーモンの `Feedback` フックへ接続する。

| イベント | 想定する表示 |
|---|---|
| `SCANNER_READY` | 待機（スキャン可能） |
| `SCAN_RECEIVED` | 読み取り確認（短い点灯） |
| `APPLYING` | 接続試行中（点滅） |
| `CONNECTED` | 成功（一定時間点灯後、待機へ） |
| `FAILED` | 失敗（速い点滅） |
| `SCANNER_ERROR` | I2C 応答なし |

実装時の要点:

- `daemon.py` の `Feedback` Protocol を実装するクラスを追加し、`__main__.py` で `NullFeedback` と差し替える
- LED の物理実装（GPIO 直結、Pi 本体 ACT LED、I2C LED ドライバ等）はステータス LED の設計で決める。本デーモンは LED の種類に依存しない
- 接続成功時に IP アドレスを伝える手段（LED では表現できない）は別途検討する。当面は mDNS（`<hostname>.local`）で到達する
