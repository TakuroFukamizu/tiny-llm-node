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
| `REPO_URL` | `https://github.com/TakuroFukamizu/tiny-llm-node.git` | 未クローン時の clone 元（HTTPS。Pi に SSH 鍵は不要） |
| `BRANCH` | `feature/wifi-qr-provisioning` | `services/wifi-qr` を含むブランチ。main にマージ済みなら `main` |

## 実行者への規則

1. **ステップは番号順に実行する。** 前のステップの期待出力が確認できるまで次に進まない。
2. **期待する出力と食い違ったら止めて報告する。** 「失敗時」の分岐に該当すればそれに従い、該当しなければ観測した出力をそのまま利用者に見せて指示を待つ。
3. **パスワードは絶対に表示・記録しない。** QR 用の `WIFI:` 文字列に実パスワードを含める場合、そのコマンド行をログや報告文に貼らない。`journalctl` の出力にパスワードが現れたらそれはバグなので、その旨だけ報告する。
4. **再起動の前に必ず利用者に確認する。** 再起動が必要なステップは明示してあり、再開位置も書いてある。
5. `/boot/firmware/config.txt` の編集は `install.sh` に任せる。手で編集しない。
6. 各ステップの結果（成功／失敗／観測した出力の要約）を最後の Step 10 の表にまとめる。
7. **終了しないコマンドを前面で実行しない。** `--once` の待ち受けは `timeout 300` で有界にしてあり、実行者はそのツール呼び出しの timeout を 300 秒より長く（例: 360 秒）設定する。`journalctl -f` は使わず、`date +%T` で時刻を控えてから `--since` で読む。
8. **ログ行の照合はキーワードで行う。** 期待出力に示したログ行は、journald のタイムスタンプ・プレフィックス、`WifiCredential(...)` の引用符や区切りの細部が実装と多少異なりうる。`scan received` / `ssid='…'` / `password=***`（オープンネットワークは `password=None`）/ `dry-run` / `applying` / `connected:` / `connect failed` / `already connected` / `scanner ready` といったキートークンと SSID 値が一致していれば「期待どおり」とみなす。パスワードの実値がどこかに現れた場合だけは、規則 3 のとおり即座に報告する。

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

   **線色は M5Stack 製ケーブルの慣習。Seeed 製 Grove ケーブルは逆（黄 = SCL、白 = SDA）**なので、色ではなく位置で確認してもらう: Grove コネクタの 4 線は必ず SCL・SDA・VCC・GND の順に並んでいるため、**赤の隣の信号線が SDA（pin 3）、赤から最も遠い外側の信号線が SCL（pin 5）**。

3. （参考）ユニットの照準ライト（赤いライン）が点いているか。点いていれば 5V 給電の目安になるが、ファームウェアは最初の I2C 通信までスキャンエンジンを設定せず、エンジンの既定ではアイドル中に照準ライトが消えている可能性がある。**消えていても配線不良とは限らない。** 給電・配線の確定判定は Step 4 の `i2cdetect` で行う。

### 期待する出力

1・2 が「はい」。3 は yes/no を記録するだけ（Step 10 の表）で、進行をブロックしない。

### 失敗時

- 1・2 に「いいえ」がある → 配線を直してもらってから再確認。**Pi の電源を入れたままヘッダに抜き差しするのは避け、必要なら `sudo poweroff` を提案する**（利用者に確認）。
- 照準ライトが点かない → 記録して Step 3 へ進む。Step 4 で `--` しか出なければ、そこで赤/黒の取り違えや pin 番号の数え間違い（pin 1 は SD カード側の角、奇数が内側列）を疑う。

---

## Step 3: インストール

### コマンド

```bash
# 未クローンなら（REPO_URL / BRANCH はパラメータ表の値）:
#   git clone --branch <BRANCH> <REPO_URL> ~/tiny-llm-node
cd ~/tiny-llm-node && git checkout <BRANCH> && git pull --ff-only
cd ~/tiny-llm-node/services/wifi-qr
sudo ./install.sh; echo "exit=$?"
```

### 期待する出力

`install.sh` は次のいずれかで終わる:

- **`exit=0`**: I2C が有効（既に有効だったか、install.sh が今回 `raspi-config nonint do_i2c 0` で即時有効化した。Pi 5 では初回でも通常こちらになり、ログに `enabling I2C ...` が出ていても正常）で、`wifi-qr.service` が enable + start された。→ **Step 4 へ**
- **`exit=3`** かつ `REBOOT REQUIRED:` で始まる行: I2C を `/boot/firmware/config.txt` で有効化したが `/dev/i2c-1` がまだ現れていない。→ 下の分岐へ

途中で `apt-get install -y python3-smbus2 i2c-tools` が走るので、初回は 1〜2 分かかります。

### 失敗時

| 観測 | 対応 |
|---|---|
| `exit=3` | **利用者に再起動の許可を求める。** 許可後 `sudo reboot`。SSH で再接続したら **Step 4 から再開**（Step 0〜3 はやり直さない）。再起動後は `ls /dev/i2c-1` が存在するはず |
| `exit=1` など上記以外 | 出力の最後 20 行を報告して止まる。典型例: `E: Unable to locate package python3-smbus2`（`sudo apt update` を先に実行して再試行）、`rsync: command not found`（`sudo apt install -y rsync`） |
| `git pull` が conflict や `not a git repository` | 止めて報告 |
| `cd ~/tiny-llm-node/services/wifi-qr` が `No such file or directory` | `main` など `services/wifi-qr` を含まないブランチにいる。`git -C ~/tiny-llm-node checkout <BRANCH>` して再実行 |

---

## Step 4: I2C バスの確認

install.sh がサービスを起動済み（`exit=3` 経由なら再起動後に自動起動済み）なので、`i2cdetect` がデーモンのポーリングと交錯してジャーナルに `scanner error` を残さないよう、**先に停止**します（Step 5 でも同じ停止を繰り返すが無害）。

### コマンド

```bash
sudo systemctl stop wifi-qr
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
| 全部 `--`（何も検出されない） | (1) Step 2 の配線（特に SDA/SCL の取り違え、GND 未接続）、(2) スイッチが UART 側になっていないか、(3) 5V 給電（赤が pin 2/4、黒が pin 6 か。照準ライトは消えていても給電不良とは限らない）を利用者に再確認。(4) **SDA/SCL の取り違えが疑わしければ、黄/白（赤の隣の線と外側の線）を入れ替えて再試行**（両線とも 3V3 プルアップなので入れ替えは無害。抜き差しは `sudo poweroff` 後に）。直したら本ステップを再実行 |
| `20:` 行が `UU` | 別のカーネルドライバがそのアドレスを掴んでいる。`ls /sys/bus/i2c/devices/1-0021` と `dmesg \| grep -i i2c` を報告して止まる |
| `21` 以外のアドレスに反応がある（例: `--` の中に `2a` など） | ユニット以外の I2C デバイス、またはアドレスが異なる個体。`21` が無い場合は `/etc/default/wifi-qr` の `WIFI_QR_I2C_ADDR` をその値にし、**さらに Step 5〜7 の手動実行コマンドすべてに `WIFI_QR_I2C_ADDR=<値>` を `sudo` の直後に付ける**（例: `sudo WIFI_QR_I2C_ADDR=0x2a PYTHONPATH=/opt/wifi-qr python3 -m wifi_qr --probe`。`/etc/default/wifi-qr` は systemd だけが読み、手動実行には効かない）。Step 5 の期待出力 `addr=0x21` はその値に読み替える。Step 8 以降は systemd が `/etc/default/wifi-qr` を読むので追加指定不要。**その旨を Step 10 に記録** |
| `Error: Could not open file /dev/i2c-1` | I2C 未有効。Step 3 が `exit=3` だったのに再起動していない可能性。`grep i2c_arm /boot/firmware/config.txt` で `dtparam=i2c_arm=on` を確認し、利用者に再起動を確認 |

---

## Step 5: `--probe` でスキャナーの応答を確認

Step 4 で停止済みですが、念のためもう一度サービスを停止してから実行します（Step 4 でアドレスを変えた場合は `WIFI_QR_I2C_ADDR=<値>` を `sudo` の直後に付ける）。

### コマンド

```bash
sudo systemctl stop wifi-qr
sudo PYTHONPATH=/opt/wifi-qr python3 -m wifi_qr --probe; echo "exit=$?"
```

### 期待する出力

次の 3 行が（この順で）表示され `exit=0`:

```
i2c: bus=1 addr=0x21 ok
firmware version: 0x??      ← 16 進 1 バイト。値は個体依存（Step 10 に記録）。`unreadable (...)` でも可（下表）
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
| `firmware version: unreadable (...)`（`i2c: ... ok` と `trigger mode` 行は出て `exit=0`） | FW バージョンレジスタ（0x00FE）だけがアドレス相違で応答しない。診断専用で動作には影響しないので **記録して先に進む**（デーモンも `firmware=unknown` として通常起動する） |

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

テスト用 QR をスキャナーにかざしてもらいます。テスト文字列は **32 バイトを超えるもの**を使います（DATA レジスタの 512 バイト一括読み出しを実際に通すため。短い文字列では読み出し経路の不具合を検出できない）:

```
WIFI:T:nopass;S:wifi-qr-test-0123456789abcdef;;
```

#### QR の用意

実行者が Claude Code の場合、端末に描画した ANSI QR は利用者の画面には届かないので、次のどちらかで用意する:

1. **推奨:** 利用者に、上の文字列を自分のスマートフォン／PC の任意の QR 生成ツールで QR にして、その画面をかざしてもらう（Step 7 と同じ方法）。
2. 代替: Pi で PNG を作り、利用者に渡す:

   ```bash
   sudo apt install -y qrencode
   qrencode -o /tmp/wifi-qr-test.png -s 10 'WIFI:T:nopass;S:wifi-qr-test-0123456789abcdef;;'
   ```

   実行者側で `scp <USER>@<HOSTNAME>.local:/tmp/wifi-qr-test.png .` して取り出し、利用者に画像を渡す（実行者のセッションにファイル送信手段があればそれでも良い）。

人間が端末で手作業する場合だけ、`qrencode -t ANSIUTF8 'WIFI:T:nopass;S:wifi-qr-test-0123456789abcdef;;'` で端末に描画したものをそのままかざしてもよい。

#### コマンド

QR の用意ができ、利用者がかざせる状態になってから実行する（300 秒で `timeout` が止める。**実行者はこのコマンドのツール timeout を 300 秒より長く、例えば 360 秒に設定する**。Step 4 でアドレスを変えた場合は `WIFI_QR_I2C_ADDR=<値>` を `sudo` の直後に付ける）:

```bash
sudo PYTHONPATH=/opt/wifi-qr timeout 300 python3 -m wifi_qr --dry-run --once --verbose; echo "exit=$?"
```

利用者に「QR をスキャナーに 10〜20 cm の距離でかざしてください」と依頼する。

#### 期待する出力

1. ユニットのブザーが**1 回鳴る**（利用者に確認）
2. ログに次の行（FW バージョンが読めない個体では `firmware=unknown`）:

```
... scanner ready: firmware=0x?? trigger_mode=auto
... scan received: WifiCredential(ssid='wifi-qr-test-0123456789abcdef', password=None, security=open, hidden=False)
... dry-run: would apply ssid='wifi-qr-test-0123456789abcdef' security=open
```

3. `exit=0`（`--once` なので 1 回で終了）

#### 失敗時

| 観測 | 対応 |
|---|---|
| `exit=124`（`scan received` が無い） | 300 秒以内にスキャンされなかった。QR をかざす準備ができてから再実行 |
| ブザーが鳴らない | 読めていない。QR の表示サイズを大きく、距離を変える、画面の輝度を上げる。PNG 経路なら `-s 10` を `-s 16` にして作り直す |
| ブザーは鳴るが `scan received` が出ないまま `exit=124` | READY レジスタが読めていない。`--verbose` の出力で `ready=` の値を確認して報告（`ready=2` は「ポーリングの間に 2 回デコードした」だけで正常。デーモンは 1 と同様に読む） |
| `scanner rejected frame: invalid payload length ...` | 長さレジスタの読み出しがおかしい。`--verbose` の出力全文を報告 |
| `ignoring payload (...): PayloadError: ...` | QR の文字列が化けている（別の QR を読んだ、または 512 バイト一括読み出しが実機で通っていない）。生ペイロードは `--verbose` でもログに出ない設計なので、その `ignoring payload` 行（先頭数文字と文字数の要約のみ）と `scan received: N bytes` の DEBUG 行を報告 |
| `scanner error (k/30)` が 30 回続いて `scanner gave up` | READY は読めるが LENGTH/DATA の読み出しが失敗し続けている（一括読み出しが実機で通っていない可能性）。`--verbose` の出力全文を報告 |
| `OSError: [Errno 121]` が繰り返し出る | Step 5 の失敗時と同じ対応 |

---

## Step 7: 実際に接続する

**ここから nmcli が呼ばれ、ノードの WiFi 設定が変わります。** `REAL_SSID` の QR を用意します。

- スマートフォンの WiFi 共有画面の QR をそのまま使う（推奨。パスワードを端末に打ち込まなくて済む）
- または利用者自身の PC で `qrencode -t ANSIUTF8 'WIFI:T:WPA;S:<REAL_SSID>;P:<password>;;'` を実行してもらう。**実行者（Claude Code）はパスワードを含むコマンドを組み立てない・実行しない。**

### 前提: SSH の経路を確認する

`nmcli connection up` の時点で wlan0 の既存接続が新しいプロファイルに切り替わる。**実行者の SSH が WiFi（wlan0）経由なら、そこでセッションが切れることがあり、前面実行の出力と `exit=` が失われる。** 先に経路を確認し、A / B のどちらかで実行する:

```bash
ip route get "${SSH_CLIENT%% *}"    # dev eth0 → A（有線）、dev wlan0 → B（WiFi 経由）
```

### コマンド

A・B とも 300 秒で止まる。A では実行者のツール timeout を 300 秒より長く（例: 360 秒）設定する。Step 4 でアドレスを変えた場合は `WIFI_QR_I2C_ADDR=<値>` を `sudo` の直後（B では `--setenv=WIFI_QR_I2C_ADDR=<値>` を追加）に付ける。

```bash
# A. 有線 LAN で SSH している場合: 前面で実行
date +%T    # 開始時刻の記録用
sudo PYTHONPATH=/opt/wifi-qr timeout 300 python3 -m wifi_qr --once; echo "exit=$?"
```

```bash
# B. WiFi 経由で SSH している場合: 一時 systemd ユニットに切り離して実行し、journal から結果を読む
date +%T    # T0 を記録
sudo systemd-run --unit wifi-qr-once --collect -p RuntimeMaxSec=300 \
  --setenv=PYTHONPATH=/opt/wifi-qr /usr/bin/python3 -m wifi_qr --once
# 利用者に QR をかざしてもらい、SSH が切れたら connected: に出た新しい IP か <HOSTNAME>.local で再接続してから:
sudo journalctl -u wifi-qr-once --since '<T0>' --no-pager -o short-iso
systemctl is-active wifi-qr-once    # active のままなら未スキャン。中止するなら sudo systemctl stop wifi-qr-once
```

利用者に本番 QR をかざしてもらう。

### 期待する出力

```
... scan received: WifiCredential(ssid='<REAL_SSID>', password=***, security=wpa-psk, hidden=False)
... applying: ssid='<REAL_SSID>'
... connected: ssid='<REAL_SSID>' addresses=192.168.x.y/24
```

A: `exit=0`。B: journal に `wifi-qr-once.service: Deactivated successfully.`（= 終了コード 0。`Main process exited, code=exited, status=N` なら N が終了コード）。`scan received` から `connected` までの時間差（ログのタイムスタンプ。B は `-o short-iso` の時刻）を **Step 10 に記録**（目標 30 秒以内）。

続けて検証:

```bash
nmcli -t -f NAME,DEVICE connection show --active    # wifi-qr-<REAL_SSID>:wlan0 が含まれる
LC_ALL=C nmcli -t -f ACTIVE,SSID device wifi | grep '^yes'   # yes:<REAL_SSID>
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
| A で `exit=124`、または B で journal に `Failed with result 'timeout'` | 300 秒以内にスキャンされなかった。QR をかざす準備ができてから再実行（B は `--collect` 済みなので同じユニット名で再実行できる） |
| SSH が WiFi 経由で切れた | 正常動作。`connected:` に出た新しい IP または `<HOSTNAME>.local` で再接続し、B なら `journalctl -u wifi-qr-once` で期待出力を照合する。A で実行していて出力が失われた場合は `nmcli -t -f ACTIVE,SSID device wifi` で接続先を確認し、Step 10 の秒数は「未取得（SSH 切断）」と記録 |

---

## Step 8: サービスとして動かす

Step 5〜7 の手動プロセスが残っていると、サービスと READY レジスタを取り合って読み落としが起きる（`/dev/i2c-1` は排他ではないので、サービスが `failed` になるのではなく黙って競合する）。先に残骸が無いことを確認する。

### コマンド

```bash
pgrep -af '[p]ython3 -m wifi_qr'           # 何も出ないのが期待。出たら sudo kill <PID>（pkill -f は使わない: サービス本体も同じ文字列に一致する）
sudo systemctl stop wifi-qr-once 2>/dev/null || true   # Step 7 B の一時ユニットが残っていれば止める
sudo systemctl start wifi-qr
sleep 3
systemctl status wifi-qr --no-pager
sudo journalctl -u wifi-qr -n 50 --no-pager
```

### 期待する出力

- `Active: active (running)`
- journal に `scanner ready: firmware=0x?? trigger_mode=auto`（または `firmware=unknown`）
- journal にパスワード文字列が**含まれない**
- Step 4 より前に動いていた頃の `scanner error (k/30)` 行が `-n 50` に混じることがある（`i2cdetect` との交錯）。`scanner ready` より前の行は無視してよい

続けて**切り替えテスト**: 別のネットワーク（スマートフォンのテザリングで可）の QR をかざしてもらう。`journalctl -f` は終了しないので使わず、時刻を控えてから `--since` で読む:

```bash
date +%T    # T0 を記録してから利用者に QR をかざしてもらう
# かざしてもらった後 30 秒ほど待ってから（SSH が切れた場合は再接続後に）:
sudo journalctl -u wifi-qr --since '<T0>' --no-pager
```

期待: `scan received` → `applying` → `connected: ssid='<別SSID>'`。SSH セッションが切れた場合は新しい IP（またはテザリング先から `<HOSTNAME>.local`）で再接続。（代替: `sudo timeout 120 journalctl -u wifi-qr -f -n 0 --no-pager`。この場合 `exit=124` は「120 秒の窓が過ぎた」の意味で正常）

続けて**重複抑止テスト**: 今つながっている SSID と同じ QR をもう一度かざす:

期待: `already connected: ssid='<SSID>'; ignoring` が出て、`applying` は出ない。`nmcli connection show --active` に変化なし。

最後に元のネットワーク（`REAL_SSID`）の QR をかざして戻す。

### 失敗時

| 観測 | 対応 |
|---|---|
| `Active: failed` / `activating (auto-restart)` | `journalctl -u wifi-qr -n 50` の最後の traceback または `scanner gave up` 行を報告（起動リトライ 13 回が尽きた = I2C 無応答なら Step 4 に戻る） |
| `scanner error (k/30)` が 30 回続いて `scanner gave up` で終了・再起動を繰り返す | I2C が応答していない（READY は読めるのに LENGTH/DATA で失敗し続ける場合もここに来る）。Step 4 に戻る |
| ブザーは鳴るが `scan received` が出ない、または読み落としが多い | Step 5〜7 の手動プロセスが残って READY を取り合っている可能性。`pgrep -af wifi_qr` の PID を `systemctl show -p MainPID --value wifi-qr` と比べ、MainPID 以外があれば `sudo kill <PID>`（`pkill -f` は使わない） |
| 同じ QR で `applying` が出てしまう | 重複抑止のバグ、または `current_ssid()` が取れていない。`LC_ALL=C nmcli -t -f ACTIVE,SSID device wifi` の出力を報告 |
| 切り替え後にログが `connect failed` | Step 7 の失敗時と同じ |

---

## Step 9: 再起動テスト

**再起動は利用者に確認してから実行する。**

### コマンド

```bash
sudo reboot
```

再接続後（`ssh -o ConnectTimeout=10 <USER>@<HOSTNAME>.local uptime` を最大 4 回まで試す。実行者が前面で待機できない場合は、ツールのバックグラウンド実行／監視機能で間隔を空ける。前面 `sleep` のループは書かない）:

```bash
uptime
systemctl is-active wifi-qr
LC_ALL=C nmcli -t -f ACTIVE,SSID device wifi | grep '^yes'
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
| Step 2 時点（最初の I2C 通信前）で照準ライトが点いていたか | はい / いいえ |
| スキャナーのファームウェアバージョン（Step 5。`unreadable` ならそう記録） | |
| トリガーモード初期値（Step 5） | |
| Step 6b の 32 バイト超ペイロードを一括読み出しで正しく読めたか | |
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
| `i2cdetect -y 1` に何も出ない | 配線ミス（線色の慣習が違うケーブルで SDA/SCL が逆、を含む）、スイッチが UART 側、5V 未給電、I2C 未有効 | Step 2 の配線を**位置**で再確認し、黄/白を入れ替えて再試行。照準ライトは消えていても給電不良とは限らない。`grep i2c_arm /boot/firmware/config.txt`。再起動 |
| `i2cdetect` で `21` が `UU` | 他のカーネルドライバがアドレスを占有 | `dmesg \| grep -i i2c`、`/sys/bus/i2c/devices/1-0021` を確認。該当 overlay を config.txt から外す |
| `--probe` で `Remote I/O error` が断続的に出る | クロックストレッチ、ケーブル長、GND 不良 | ジャンパ線を短く（20 cm 以下）、GND を確認。Pi 5 以外は非対応 |
| `--verbose` で `ready=2` が出る | ポーリングの間に 2 回以上デコードした（READY は 2 で飽和し、DATA を読むまで保持される） | 正常。デーモンは 1 と同様にペイロードを読む。対処不要 |
| ブザーは鳴るのに `scan received` が出ない・読み落とす | Step 5〜7 の手動プロセスが残り、サービスと READY を取り合っている | `pgrep -af wifi_qr` と `systemctl show -p MainPID --value wifi-qr` を比べ、MainPID 以外の PID を `sudo kill <PID>`（`pkill -f` はサービス本体も殺すので使わない） |
| `connect failed` に `Secrets were required, but not provided` | パスワード違い、または AP の認証方式が QR の `T:` と不一致 | 正しい QR を作り直す。WPA3-only AP は `T:SAE` にする |
| 隠し SSID につながらない | QR に `H:true` が無い | `WIFI:T:WPA;S:<ssid>;P:<pw>;H:true;;` で作り直す |
| WPA3-only の AP で `T:WPA` の QR が失敗する | key-mgmt が `wpa-psk` になっている | `T:SAE` の QR にする（`T:WPA3` は WPA/WPA2 と同じ `wpa-psk` 扱いなので効果なし）。混在（WPA2/WPA3 transition）AP なら `T:WPA` でよい |
| 記号入りパスワードで `PayloadError` または認証失敗 | `;` `:` `,` `\` のエスケープ漏れ | QR 内で `\;` `\:` `\,` `\\` にエスケープする。Android の共有 QR は自動でエスケープ済み |
| `already connected` が出ず毎回 `applying` する | `nmcli -t -f ACTIVE,SSID device wifi` の SSID が QR と一致しない（全角／大小文字） | 出力を報告。SSID 文字列の比較は完全一致 |
| journal にパスワードらしき文字列が出る | **バグ** | すぐ報告。`sudo journalctl --rotate && sudo journalctl --vacuum-time=1s` でログを消す（利用者に確認） |
