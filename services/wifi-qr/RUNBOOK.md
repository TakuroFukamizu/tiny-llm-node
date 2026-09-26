# RUNBOOK: wifi-qr 実機セットアップ手順

**作成日: 2026-09-26 / 状態: 机上（実機未実施）**

Raspberry Pi 5 上で `services/wifi-qr` をインストールし、QR コードスキャナーによる WiFi 接続を段階的に検証する手順です。SSH で Pi にログインし sudo 権限を持つ **Claude Code が実行する**ことを前提に書かれています。人間が手作業で実施しても構いません。

---

## この runbook を使うとき

- Pi 5 に [docs/software_setup_ja.md](../../docs/software_setup_ja.md) の OS セットアップ（1〜2 節）が済んでいる
- M5Stack 用 QR コードスキャナーユニットを GPIO に配線した（または今から配線する）
- はじめて wifi-qr を導入するとき、あるいは動かなくなって切り分けたいとき

## 開始前に埋めるパラメータ

| パラメータ | 値 | 備考 |
|---|---|---|
| `CC` | `JP` | WiFi 国コード（ISO 3166-1 alpha-2）。既定 JP |
| `REAL_SSID` | _（記入）_ | Step 7 で実際に接続する SSID。パスワードはこの文書に**書かない** |
| `HOSTNAME` | _（記入）_ | `hostname` の出力。Step 7 の `ssh <user>@<HOSTNAME>.local` で使う |
| `USER` | _（記入）_ | Pi のログインユーザー |
| `REPO_DIR` | `~/tiny-llm-node` | リポジトリのクローン先 |

## 実行者への規則

1. **ステップは番号順に実行する。** 前のステップの期待出力が確認できるまで次に進まない。
2. **期待する出力と食い違ったら止めて報告する。** 「失敗時」の分岐に該当すればそれに従い、該当しなければ観測した出力をそのまま利用者に見せて指示を待つ。
3. **パスワードは絶対に表示・記録しない。** QR 用の `WIFI:` 文字列に実パスワードを含める場合、そのコマンド行をログや報告文に貼らない。`journalctl` の出力にパスワードが現れたらそれはバグなので、その旨だけ報告する。
4. **再起動の前に必ず利用者に確認する。** 再起動が必要なステップは明示してあり、再開位置も書いてある。
5. `/boot/firmware/config.txt` の編集は `install.sh` に任せる。手で編集しない。
6. 各ステップの結果（成功／失敗／観測した出力の要約）を最後の Step 10 の表にまとめる。
7. **ログ行の照合はキーワードで行う。** 期待出力に示したログ行は、journald のタイムスタンプ・プレフィックス、`WifiCredential(...)` の引用符や区切りの細部が実装と多少異なりうる。`scan received` / `ssid='…'` / `password=***`（オープンネットワークは `password=None`）/ `dry-run` / `applying` / `connected:` / `connect failed` / `already connected` / `scanner ready` といったキートークンと SSID 値が一致していれば「期待どおり」とみなす。パスワードの実値がどこかに現れた場合だけは、規則 3 のとおり即座に報告する。

---

## Step 0: 前提確認

### コマンド

```bash
uname -r
cat /proc/device-tree/model; echo
nmcli -t -f RUNNING general
nmcli general status
nmcli radio wifi
rfkill list
ls /dev/i2c-*
```

### 期待する出力

| コマンド | 期待 |
|---|---|
| `uname -r` | 末尾が `-v8`（例: `6.12.xx+rpt-rpi-v8`）。`-2712` なら software_setup の 2 節が未完了 |
| `cat /proc/device-tree/model` | `Raspberry Pi 5 Model B ...` |
| `nmcli -t -f RUNNING general` | `running` |
| `nmcli general status` | `STATE` 列が `connected` / `connected (site only)` / `disconnected` のいずれか（エラーにならないこと） |
| `nmcli radio wifi` | `enabled` |
| `rfkill list` | `Wireless LAN` の行で `Soft blocked: no` と `Hard blocked: no` |
| `ls /dev/i2c-*` | `/dev/i2c-1` を含む。何も無ければ「No such file」だが、これは **Step 3 の install.sh で有効化されるので許容** |

### 失敗時

| 観測 | 対応 |
|---|---|
| `uname -r` が `-2712` | 本 runbook の対象外。先に software_setup_ja.md の 2 節を完了する（wifi-qr 自体は 16K カーネルでも動くはずだが、本プロジェクトの標準構成で検証する） |
| `nmcli` がコマンドとして無い、または `Error: NetworkManager is not running` | `sudo apt install -y network-manager && sudo systemctl enable --now NetworkManager`。Raspberry Pi OS Trixie では標準で入っているはずなので、無い場合は利用者に報告 |
| `nmcli radio wifi` が `disabled` | `nmcli radio wifi on` を実行して再確認 |
| `rfkill list` で `Soft blocked: yes` | 国コード未設定が典型。**Step 1 を必ず実施**し、それでも解除されなければ `sudo rfkill unblock wlan` |
| モデルが Pi 5 でない | 本 runbook の対象外（I2C クロックストレッチの問題があり得る）。利用者に報告して指示を待つ |

---

## Step 1: WiFi 国コードの設定

国コードが未設定だと `wlan0` は rfkill でブロックされたままになり、QR を読んでも接続が黙って失敗します。すでに設定済みでも再実行して害はありません。

### コマンド

```bash
sudo raspi-config nonint do_wifi_country JP    # CC を置き換える
iw reg get
rfkill list
```

### 期待する出力

- `raspi-config` は何も表示せず終了コード 0
- `iw reg get` の `global` セクションに `country JP: DFS-JP`（JP の場合）
- `rfkill list` の `Wireless LAN` で `Soft blocked: no`

### 失敗時

| 観測 | 対応 |
|---|---|
| `iw reg get` が `country 00:` のまま | `sudo reboot` で反映されることがある（**再起動は利用者に確認**）。再起動後に本ステップの確認コマンドだけ再実行 |
| `raspi-config: command not found` | `sudo apt install -y raspi-config` して再試行。それも無理なら暫定で `sudo iw reg set JP`（再起動で消える）を使い、利用者に報告 |
| 依然 `Soft blocked: yes` | `sudo rfkill unblock wlan` → 再確認。それでも駄目なら止めて報告 |

---

## Step 2: 配線の確認（人間の作業）

このステップは実行者が**利用者に確認を求める**ステップです。コマンドは実行しません。

### 確認事項（利用者に読み上げて yes/no をもらう）

1. ユニット本体のスライドスイッチが **I2C 側（回路図ラベルでは "Down"）** になっている
2. Grove–ジャンパ変換ケーブルを次のとおり Pi の 40 ピンヘッダに挿している

   | Grove 線色 | 信号 | Pi ヘッダ |
   |---|---|---|
   | 赤 | 5V | pin 2（または 4） |
   | 黒 | GND | pin 6 |
   | 黄 | SDA | pin 3（GPIO2） |
   | 白 | SCL | pin 5（GPIO3） |

3. ユニットの照準ライト（赤いライン）が点灯している（= 5V 給電されている）

### 期待する出力

利用者から 3 点すべて「はい」。

### 失敗時

- 「いいえ」がある → 配線を直してもらってから再確認。**Pi の電源を入れたままヘッダに抜き差しするのは避け、必要なら `sudo poweroff` を提案する**（利用者に確認）。
- 照準ライトが点かない → 赤/黒の取り違え、または pin 番号の数え間違い（pin 1 は SD カード側の角、奇数が内側列）。

---

## Step 3: インストール

### コマンド

```bash
cd ~/tiny-llm-node && git pull --ff-only    # 未クローンなら: git clone <repo url> ~/tiny-llm-node
cd ~/tiny-llm-node/services/wifi-qr
sudo ./install.sh; echo "exit=$?"
```

### 期待する出力

`install.sh` は次のいずれかで終わる:

- **`exit=0`**: I2C はすでに有効で、`wifi-qr.service` が enable + start された。→ **Step 4 へ**
- **`exit=3`** かつ「再起動が必要」（`reboot required` / `再起動が必要`）という趣旨の行: I2C を今回はじめて `/boot/firmware/config.txt` で有効化した。→ 下の分岐へ

途中で `apt-get install -y python3-smbus2 i2c-tools` が走るので、初回は 1〜2 分かかります。

### 失敗時

| 観測 | 対応 |
|---|---|
| `exit=3` | **利用者に再起動の許可を求める。** 許可後 `sudo reboot`。SSH で再接続したら **Step 4 から再開**（Step 0〜3 はやり直さない）。再起動後は `ls /dev/i2c-1` が存在するはず |
| `exit=1` など上記以外 | 出力の最後 20 行を報告して止まる。典型例: `E: Unable to locate package python3-smbus2`（`sudo apt update` を先に実行して再試行）、`rsync: command not found`（`sudo apt install -y rsync`） |
| `git pull` が conflict や `not a git repository` | 止めて報告 |

---

## Step 4: I2C バスの確認

### コマンド

```bash
sudo i2cdetect -y 1
```

### 期待する出力

`0x20` の行の `1` 列に `21` が表示される:

```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:                         -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
20: -- 21 -- -- -- -- -- -- -- -- -- -- -- -- -- --
30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
...
```

### 失敗時

| 観測 | 対応 |
|---|---|
| 全部 `--`（何も検出されない） | (1) Step 2 の配線（特に SDA/SCL の取り違え、GND 未接続）、(2) スイッチが UART 側になっていないか、(3) 5V 給電（照準ライトが点いているか）を利用者に再確認。直したら本ステップを再実行 |
| `20:` 行が `UU` | 別のカーネルドライバがそのアドレスを掴んでいる。`ls /sys/bus/i2c/devices/1-0021` と `dmesg \| grep -i i2c` を報告して止まる |
| `21` 以外のアドレスに反応がある（例: `--` の中に `2a` など） | ユニット以外の I2C デバイス、またはアドレスが異なる個体。`21` が無い場合は `/etc/default/wifi-qr` の `WIFI_QR_I2C_ADDR` をその値にして先に進み、**その旨を Step 10 に記録** |
| `Error: Could not open file /dev/i2c-1` | I2C 未有効。Step 3 が `exit=3` だったのに再起動していない可能性。`grep i2c_arm /boot/firmware/config.txt` で `dtparam=i2c_arm=on` を確認し、利用者に再起動を確認 |

---

## Step 5: `--probe` でスキャナーの応答を確認

install.sh がサービスを起動済みなので、I2C バスを空けるために**先に停止**します。

### コマンド

```bash
sudo systemctl stop wifi-qr
sudo PYTHONPATH=/opt/wifi-qr python3 -m wifi_qr --probe; echo "exit=$?"
```

### 期待する出力

次の 3 行が（この順で）表示され `exit=0`:

```
i2c: bus=1 addr=0x21 ok
firmware version: 0x??      ← 16 進 1 バイト。値は個体依存（Step 10 に記録）
trigger mode: auto
```

`trigger mode` が `manual` と出ても、デーモン起動時に `auto` へ書き換えるので次に進んでよい（ただし記録する）。

### 失敗時

| 観測 | 対応 |
|---|---|
| `OSError: [Errno 121] Remote I/O error` または `i2c: ... error` | I2C のクロックストレッチ／配線長の問題が疑われる。(1) 同じコマンドを 3 回まで再試行、(2) ジャンパ線が 20 cm 以下か、GND がしっかり刺さっているかを利用者に確認、(3) それでも駄目なら `sudo i2cdetect -y 1` を再実行して `21` がまだ見えるかを報告 |
| `ModuleNotFoundError: No module named 'smbus2'` | `sudo apt install -y python3-smbus2` して再試行。install.sh の不備なので報告 |
| `ModuleNotFoundError: No module named 'wifi_qr'` | `ls /opt/wifi-qr/wifi_qr/` で配置を確認。無ければ Step 3 の install.sh をやり直す |
| `firmware version` の値が `0x00` または `0xff` | レジスタアドレス（0x00FE vs 0x00F0）の相違の可能性。動作には影響しないので **記録して先に進む** |

---

## Step 6: dry-run（nmcli を呼ばない読み取りテスト）

### 6a. ハードウェア無しの dry-run（偽スキャナー）

CLI と解析部が通ることを確認します。I2C には触りません。

#### コマンド

```bash
sudo WIFI_QR_FAKE_SCANNER='WIFI:T:nopass;S:wifi-qr-test;;' PYTHONPATH=/opt/wifi-qr python3 -m wifi_qr --dry-run --once; echo "exit=$?"
```

#### 期待する出力

ログに次の趣旨の行が出て `exit=0`:

```
... scan received: WifiCredential(ssid='wifi-qr-test', password=None, security=open, hidden=False)
... dry-run: would apply ssid='wifi-qr-test' security=open
```

`nmcli` は一切呼ばれない（`nmcli connection show | grep wifi-qr-test` が何も返さない）。オープンネットワークには秘密が無いので `password=None` と表示される（パスワード付きなら `password=***`）。

#### 失敗時

| 観測 | 対応 |
|---|---|
| Python の traceback | コードのバグ。traceback 全文を報告して止まる |
| `nmcli connection show` に `wifi-qr-wifi-qr-test` が出る | dry-run が効いていないバグ。`sudo nmcli connection delete wifi-qr-wifi-qr-test` で消して報告 |

### 6b. 実機スキャナーでの dry-run

テスト用 QR を端末に表示し、スキャナーにかざしてもらいます。

#### コマンド

```bash
sudo apt install -y qrencode
qrencode -t ANSIUTF8 'WIFI:T:nopass;S:wifi-qr-test;;'
```

QR が端末に表示されたら、**別の端末（または同じ端末で先に QR を表示してから）**で:

```bash
sudo PYTHONPATH=/opt/wifi-qr python3 -m wifi_qr --dry-run --once --verbose; echo "exit=$?"
```

利用者に「表示した QR をスキャナーに 10〜20 cm の距離でかざしてください」と依頼する。SSH クライアントの画面（ノート PC のディスプレイ）をそのままスキャナーに向ければよい。

#### 期待する出力

1. ユニットのブザーが**1 回鳴る**（利用者に確認）
2. ログに次の行:

```
... scanner ready: firmware=0x?? trigger_mode=auto
... scan received: WifiCredential(ssid='wifi-qr-test', password=None, security=open, hidden=False)
... dry-run: would apply ssid='wifi-qr-test' security=open
```

3. `exit=0`（`--once` なので 1 回で終了）

#### 失敗時

| 観測 | 対応 |
|---|---|
| ブザーが鳴らない | 読めていない。QR の表示サイズを大きく（端末フォントを拡大、または `-t ANSI` で全角ブロック表示）、距離を変える、画面の輝度を上げる。それでも駄目なら `qrencode -o /tmp/test.png -s 10 'WIFI:T:nopass;S:wifi-qr-test;;'` で PNG を作り、`scp` でノート PC に取り出して表示してもらう |
| ブザーは鳴るがログに何も出ない、または 60 秒以上待っても終わらない | READY レジスタが読めていない。`--verbose` の出力で `ready=` の値を確認。`ready=2` が続くなら Troubleshooting の「READY が 2 のまま」 |
| `scanner rejected frame: invalid payload length ...` | 長さレジスタの読み出しがおかしい。`--verbose` の出力全文を報告 |
| `ignoring payload (...): PayloadError: ...` | QR の文字列が化けている（別の QR を読んだ、または chunk 読み出しの境界バグ）。生ペイロードは `--verbose` でもログに出ない設計なので、その `ignoring payload` 行（先頭数文字と文字数の要約のみ）と `scan received: N bytes` の DEBUG 行を報告 |
| `OSError: [Errno 121]` が繰り返し出る | Step 5 の失敗時と同じ対応 |

---

## Step 7: 実際に接続する

**ここから nmcli が呼ばれ、ノードの WiFi 設定が変わります。** `REAL_SSID` の QR を用意します。

- スマートフォンの WiFi 共有画面の QR をそのまま使う（推奨。パスワードを端末に打ち込まなくて済む）
- または利用者自身の PC で `qrencode -t ANSIUTF8 'WIFI:T:WPA;S:<REAL_SSID>;P:<password>;;'` を実行してもらう。**実行者（Claude Code）はパスワードを含むコマンドを組み立てない・実行しない。**

### コマンド

```bash
date +%T    # 開始時刻の記録用
sudo PYTHONPATH=/opt/wifi-qr python3 -m wifi_qr --once; echo "exit=$?"
```

利用者に本番 QR をかざしてもらう。

### 期待する出力

```
... scan received: WifiCredential(ssid='<REAL_SSID>', password=***, security=wpa-psk, hidden=False)
... applying: ssid='<REAL_SSID>'
... connected: ssid='<REAL_SSID>' addresses=192.168.x.y/24
```

`exit=0`。`scan received` から `connected` までの時間差（ログのタイムスタンプ）を **Step 10 に記録**（目標 30 秒以内）。

続けて検証:

```bash
nmcli -t -f NAME,DEVICE connection show --active    # wifi-qr-<REAL_SSID>:wlan0 が含まれる
nmcli -t -f ACTIVE,SSID device wifi | grep '^yes'   # yes:<REAL_SSID>
ping -c 3 -I wlan0 1.1.1.1                          # 0% packet loss（外部到達できない閉域網なら GW へ）
ls -l /etc/NetworkManager/system-connections/       # wifi-qr-<REAL_SSID>.nmconnection が -rw------- root
```

さらに**別のマシンから**（利用者に実行してもらう）:

```bash
ssh <USER>@<HOSTNAME>.local
```

### 失敗時

| 観測 | 対応 |
|---|---|
| `connect failed: ssid='...' reason=...` かつ理由に `Secrets were required, but not provided` | パスワード違い、または SSID は同名で認証方式が違う（WPA3-only 等）。QR を作り直してもらい再スキャン。プロファイルはデーモンが削除済みのはず（`nmcli connection show \| grep wifi-qr-` で確認） |
| `connect failed: ...` かつ `No suitable device found` / `device not ready` | rfkill か国コード。Step 0/1 を再確認 |
| `connect failed: ... timeout` | AP が遠い、または隠し SSID なのに `H:true` が無い。Troubleshooting 参照 |
| `connected` は出たが `ping` が失敗 | DHCP は通ったが上流が無い（閉域網）か AP の隔離設定。`ip -4 addr show wlan0` と `ip route` を報告。wifi-qr としては成功扱い |
| `<HOSTNAME>.local` で名前解決できない | mDNS の問題であり wifi-qr の問題ではない。`avahi-daemon` の状態（`systemctl status avahi-daemon`）と、`connected` に出た IP で直接 ssh できるかを報告 |
| 有線 LAN で SSH している場合に SSH が切れた | WiFi 側に経路が移ったため。`connected` の IP へ再接続 |

---

## Step 8: サービスとして動かす

### コマンド

```bash
sudo systemctl start wifi-qr
sleep 3
systemctl status wifi-qr --no-pager
sudo journalctl -u wifi-qr -n 50 --no-pager
```

### 期待する出力

- `Active: active (running)`
- journal に `scanner ready: firmware=0x?? trigger_mode=auto`
- journal にパスワード文字列が**含まれない**

続けて**切り替えテスト**: 別のネットワーク（スマートフォンのテザリングで可）の QR をかざしてもらい:

```bash
sudo journalctl -u wifi-qr -f
```

期待: `scan received` → `applying` → `connected: ssid='<別SSID>'`。SSH セッションが切れた場合は新しい IP（またはテザリング先から `<HOSTNAME>.local`）で再接続。

続けて**重複抑止テスト**: 今つながっている SSID と同じ QR をもう一度かざす:

期待: `already connected: ssid='<SSID>'; ignoring` が出て、`applying` は出ない。`nmcli connection show --active` に変化なし。

最後に元のネットワーク（`REAL_SSID`）の QR をかざして戻す。

### 失敗時

| 観測 | 対応 |
|---|---|
| `Active: failed` / `activating (auto-restart)` | `journalctl -u wifi-qr -n 50` の最後の traceback を報告。典型: I2C バスが Step 5〜7 の手動実行で使用中（手動プロセスを `Ctrl-C` で止める） |
| `scanner error (k/30)` が 30 回続いて `scanner gave up` で終了・再起動を繰り返す | I2C が応答していない。Step 4 に戻る |
| 同じ QR で `applying` が出てしまう | 重複抑止のバグ、または `current_ssid()` が取れていない。`nmcli -t -f ACTIVE,SSID device wifi` の出力を報告 |
| 切り替え後にログが `connect failed` | Step 7 の失敗時と同じ |

---

## Step 9: 再起動テスト

**再起動は利用者に確認してから実行する。**

### コマンド

```bash
sudo reboot
```

再接続後（`ssh <USER>@<HOSTNAME>.local`。60 秒待っても入れなければ 30 秒おきに 3 回まで再試行）:

```bash
uptime
systemctl is-active wifi-qr
nmcli -t -f ACTIVE,SSID device wifi | grep '^yes'
sudo journalctl -u wifi-qr -b --no-pager | head -20
```

### 期待する出力

- `systemctl is-active wifi-qr` → `active`
- `yes:<REAL_SSID>`（NetworkManager が自動再接続した）
- journal に起動から 60 秒以内の `scanner ready: ...`
- 再起動から SSH 復帰までの時間を Step 10 に記録

### 失敗時

| 観測 | 対応 |
|---|---|
| SSH が戻らない | 有線 LAN か Pi の HDMI で確認してもらう。`nmcli connection show wifi-qr-<REAL_SSID> \| grep autoconnect` が `yes` か |
| `wifi-qr` が `inactive` / `failed` | `systemctl is-enabled wifi-qr` が `enabled` か。journal の traceback を報告。`After=NetworkManager.service` にもかかわらず I2C が起動直後に応答しない場合は startup retry（5 秒 × 12 回）で吸収されるはず |
| `scanner ready` が 60 秒を超えて出る | 起動リトライ回数を記録。成功条件 1（60 秒以内）未達として報告 |

---

## Step 10: 実測値の記録

次の表を埋め、報告に含めてください。

| 項目 | 値 |
|---|---|
| 実施日 | |
| 実施者（Claude Code のセッション / 人） | |
| OS / カーネル（`uname -r`） | |
| スキャナーのファームウェアバージョン（Step 5） | |
| トリガーモード初期値（Step 5） | |
| `i2cdetect -y 1` の結果（`21` の有無、他アドレス） | |
| Step 7 の `scan received` → `connected` の秒数 | |
| Step 9 の再起動 → SSH 復帰の秒数 | |
| Step 9 の起動 → `scanner ready` の秒数 | |
| スキャナーの消費電流（USB 電流計等で測れた場合、5V 系） | 未計測 / ___ mA |
| 遭遇した失敗分岐（あれば） | |

記録後、次のファイルを更新すること（利用者に PR として提案する）:

1. `services/wifi-qr/README.md` と `README_ja.md` の「Measured on hardware / 実測値」表を埋め、冒頭の「机上設計 — 実機未検証」の注意書きを「実機検証済み（日付）」に書き換える
2. `docs/superpowers/specs/2026-09-26-wifi-qr-provisioning-design.md` の状態を「設計（実機未検証）」から「実装済み・実機検証済み（日付）」に更新し、第 2 節「実機で確認が必要な点」に結果を追記する
3. 消費電流が測れた場合は `docs/power.md` にも反映する

---

## Troubleshooting

| 症状 | 原因 | 対処 |
|---|---|---|
| `rfkill list` で `Soft blocked: yes`、接続が黙って失敗 | WiFi 国コード未設定 | Step 1（`raspi-config nonint do_wifi_country <CC>`）。改善しなければ `sudo rfkill unblock wlan`、再起動 |
| `i2cdetect -y 1` に何も出ない | 配線ミス、スイッチが UART 側、5V 未給電、I2C 未有効 | Step 2 の配線を再確認。照準ライトが点いているか。`grep i2c_arm /boot/firmware/config.txt`。再起動 |
| `i2cdetect` で `21` が `UU` | 他のカーネルドライバがアドレスを占有 | `dmesg \| grep -i i2c`、`/sys/bus/i2c/devices/1-0021` を確認。該当 overlay を config.txt から外す |
| `--probe` で `Remote I/O error` が断続的に出る | クロックストレッチ、ケーブル長、GND 不良 | ジャンパ線を短く（20 cm 以下）、GND を確認。Pi 5 以外は非対応 |
| `--verbose` で `ready=2` が続き読み取れない（READY が 2 のまま） | ユニットが「再読み出し要求」状態で固まっている | デーモンは 3 回連続で 2 なら READY をクリアして捨てる。それでも続くならユニットの電源を入れ直す（5V を抜き差し。利用者に依頼） |
| `connect failed` に `Secrets were required, but not provided` | パスワード違い、または AP の認証方式が QR の `T:` と不一致 | 正しい QR を作り直す。WPA3-only AP は `T:SAE` にする |
| 隠し SSID につながらない | QR に `H:true` が無い | `WIFI:T:WPA;S:<ssid>;P:<pw>;H:true;;` で作り直す |
| WPA3-only の AP で `T:WPA` の QR が失敗する | key-mgmt が `wpa-psk` になっている | `T:SAE` または `T:WPA3` の QR にする。混在（WPA2/WPA3 transition）AP なら `T:WPA` でよい |
| 記号入りパスワードで `PayloadError` または認証失敗 | `;` `:` `,` `\` のエスケープ漏れ | QR 内で `\;` `\:` `\,` `\\` にエスケープする。Android の共有 QR は自動でエスケープ済み |
| `already connected` が出ず毎回 `applying` する | `nmcli -t -f ACTIVE,SSID device wifi` の SSID が QR と一致しない（全角／大小文字） | 出力を報告。SSID 文字列の比較は完全一致 |
| journal にパスワードらしき文字列が出る | **バグ** | すぐ報告。`sudo journalctl --rotate && sudo journalctl --vacuum-time=1s` でログを消す（利用者に確認） |
