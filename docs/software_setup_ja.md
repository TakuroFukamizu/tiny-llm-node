# ソフトウェアセットアップ

**Language:** [English](software_setup.md) | 日本語

Raspberry Pi 5 に NVIDIA ドライバと LLM 実行環境をゼロから構築する完全手順です。

本手順は 2026-06-12 に実機(Pi 5 8GB + RTX 4060)で実施した構築作業に基づき、実際に遭遇した失敗とその回避方法を含みます。

---

## 検証済み構成(凍結)

ユーザースペースドライバとカーネルモジュールはバージョンを**厳密に一致**させる必要があります。任意の CUDA Toolkit を入れる場合は、LLM ランタイムの節で示すこの凍結構成向けのバージョンを使い、ドライバを置き換えないでください。

| コンポーネント | バージョン | 備考 |
|---|---|---|
| OS | Raspberry Pi OS 13 (Trixie) 64-bit | Lite 版で可(ヘッドレス運用) |
| カーネル | `kernel8.img`(**4KB ページ**) | デフォルトの 16K カーネル(`kernel_2712.img`)では**動作しない** |
| NVIDIA ユーザースペースドライバ | **580.95.05**(aarch64) | 2026年初頭時点で ARM userspace を含む最後のリリース |
| カーネルモジュール | [mariobalanica/open-gpu-kernel-modules](https://github.com/mariobalanica/open-gpu-kernel-modules) の `non-coherent-arm-fixes` ブランチ | [NVIDIA PR #972](https://github.com/NVIDIA/open-gpu-kernel-modules/pull/972) の元 |
| 推論ランタイム | ollama | CUDA ランタイムを同梱 — CUDA Toolkit 不要 |

> 開始前に、PR #972 がマージ済みか、より新しいドライバに ARM userspace が含まれるかを確認してください。状況が変わっていなければ(2026年6月時点では未マージ)上記バージョンを使用します。動作報告のない組み合わせは決して使わないこと。

---

## 0. 前提

- ハードウェアは [assembly.md](assembly.md) / [hardware.md](hardware.md) に従って組み立て済み
- **電源投入順序: GPU 側を先に ON → Pi 5 を後から ON。** PCIe リンクトレーニング時に GPU へ給電済みである必要があります
- Pi への SSH アクセス(NVIDIA GPU からのディスプレイ出力は動作しません。ヘッドレス運用前提。Pi 本体の HDMI は通常どおり使えます)

---

## 1. OS インストールとパッケージ

Raspberry Pi Imager で Raspberry Pi OS(64-bit, Trixie)を書き込みます。Imager のカスタマイズで SSH を有効化してください。

```bash
sudo apt update
sudo apt install -y build-essential git linux-headers-rpi-v8 pciutils wget curl ca-certificates
```

`linux-headers-rpi-v8` は NVIDIA ドライバが要求する 4K ページカーネル用のヘッダを提供します。

Raspberry Pi OS ではカーネルヘッダが**2つのパッケージに分割**されています(アーキ固有の `linux-headers-*-rpi-v8` + 共通の `linux-headers-*-common-rpi`)。`linux-headers-rpi-v8` を入れれば両方入ります。この分割が後で効いてきます — ビルド手順の警告を参照。

---

## 2. ブート設定

`/boot/firmware/config.txt` の `[all]` セクション配下に追記します:

```ini
# GPU 用の外部 PCIe コネクタ
dtparam=pciex1
dtparam=pciex1_gen=2

# 4K ページカーネル — NVIDIA ドライバに必須
kernel=kernel8.img
```

```bash
sudo reboot
```

注意:

- NVIDIA ドライバのメモリマネージャは 4K ページのみサポートします。Pi 5 デフォルトの 16K カーネルでは `Cannot initialize GSP firmware RM` で失敗します
- `pciex1_gen=3` は転送帯域が約2倍になりモデルロードが速くなりますが、Pi 5 の Gen 3 は非公式です。本構成は安定性重視で Gen 2 運用。まず Gen 2 で全体を通し、動作後に Gen 3 を試してください
- `lspci` でアイドル時のリンク速度が 2.5GT/s と表示されるのは省電力ダウントレーニングで、異常ではありません

再起動後に確認:

```bash
getconf PAGE_SIZE        # => 4096(16384 なら kernel= 行の位置を確認)
uname -r                 # => 末尾が -v8(-2712 ではない)
lspci -nn | grep -i nvidia
# => 0001:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation AD107 [GeForce RTX 4060] [10de:2882]
```

`lspci` に何も出ない場合はソフトウェアに進まず、ハードウェアを疑います: ケーブルの嵌合・向き、GPU 補助電源、電源投入順序、`dtparam=pciex1` の記述。

---

## 3. NVIDIA ユーザースペースドライバ

```bash
mkdir -p ~/nvidia && cd ~/nvidia
wget https://us.download.nvidia.com/XFree86/aarch64/580.95.05/NVIDIA-Linux-aarch64-580.95.05.run
sh ./NVIDIA-Linux-aarch64-580.95.05.run --check   # 整合性確認
sudo sh ./NVIDIA-Linux-aarch64-580.95.05.run --no-kernel-modules --silent
```

`--no-kernel-modules` が本手順全体の核心です: ユーザースペース(libcuda, nvidia-smi, nvidia-modprobe …)のみをインストールし、パッチ済みカーネルモジュールは次のステップで別途ビルドします。

想定される警告(無害): "will not install any kernel modules"、"Unable to determine the path to install the libglvnd EGL vendor library config files"。

---

## 4. パッチ済みカーネルモジュール

```bash
cd ~/nvidia
git clone --branch non-coherent-arm-fixes --depth 1 https://github.com/mariobalanica/open-gpu-kernel-modules.git
cd open-gpu-kernel-modules
head -2 version.mk    # NVIDIA_VERSION = 580.95.05 を確認(userspace と一致必須)

make modules -j$(nproc)
sudo make modules_install
sudo depmod -a
```

Pi 5 でのビルド時間は約 30〜60 分。`Warning: modules_install: missing 'System.map' file. Skipping depmod.` は問題ありません(だからこそ `depmod -a` を明示的に実行します)。

> ⚠️ **`SYSSRC=/lib/modules/$(uname -r)/build` を make に渡してはいけません。** Raspberry Pi OS のヘッダ分割により、`SYSSRC` にアーキ固有ディレクトリを指定すると、ビルドの機能検出ステップ(conftest)から共通ヘッダが見えなくなります。すると全ヘッダ判定が失敗し、`fatal error: stdarg.h: No such file or directory` が大量に出てビルドが死にます。`SYSSRC` を一切渡さなければ、Makefile が `/lib/modules/<ver>/source`(共通)と `build`(アーキ)の symlink を自動で正しく解決します。
>
> 実行中以外のカーネル向けにビルドしたい場合は、**`KERNEL_UNAME=<対象バージョン>` のみ**を渡します(例: `KERNEL_UNAME=6.12.75+rpt-rpi-v8`)。これを使えば 16K カーネルで動作中のままモジュールを先行ビルドでき、手順全体を再起動1回に短縮できます。その場合は `sudo depmod -a <対象バージョン>` も実行してください。引数なしの `depmod -a` は実行中カーネル向けにしか依存関係を再生成しません。
>
> ビルドが途中で失敗して再試行する場合は、まず古い機能検出の状態を掃除します:
> ```bash
> cd kernel-open && rm -rf conftest && find . -name '*.o' -delete && find . -name '.*.cmd' -delete && cd ..
> ```

モジュールを起動時にロードさせ、`apt upgrade` でドライバが静かに壊れないようカーネルを固定します:

```bash
printf 'nvidia\nnvidia-uvm\n' | sudo tee /etc/modules-load.d/nvidia.conf
sudo apt-mark hold linux-image-rpi-v8 linux-headers-rpi-v8
sudo reboot
```

(後で hold 解除してカーネルを更新したら、モジュールを再ビルドしてください: セクション4を再実行)

---

## 5. ドライバの確認

```bash
lsmod | grep nvidia                       # nvidia と nvidia_uvm がロード済みであること
sudo dmesg | grep -iE 'nvrm|nvidia' | tail -20
nvidia-smi
```

成功時の表示:

```
| NVIDIA GeForce RTX 4060   Off | 00000001:01:00.0 Off | ... 8188MiB ... |
```

dmesg の見方:

- `NVRM: loading NVIDIA UNIX Open Kernel Module ... 580.95.05` → 正常
- ヘッドレス CUDA 専用ノードでは `nvidia_modeset` がロードされない場合があります。ollama で重要なのは `nvidia` と `nvidia_uvm` です
- `NVRM: Chipset not recognized (vendor ID 0x14e4, device ID 0x2712)` と "has not been qualified on this platform" → **無害**。Broadcom ホストブリッジに対する注意書き
- `Cannot initialize GSP firmware RM` → 異常: 16K カーネルのまま、もしくは未パッチのモジュールがロードされている(トラブルシューティング参照)

---

## 6. LLM ランタイム(ollama)

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

インストールログの最後に `NVIDIA GPU installed.` が出ること。(CPU-only と出たらドライバが ollama から見えていません。)

```bash
# VRAM 8GB の GPU に収まるモデル例
ollama pull qwen3:8b        # 約 5.2 GB
ollama run qwen3:8b --verbose "自己紹介して"
```

GPU オフロードの確認:

```bash
ollama ps      # PROCESSOR 列が "100% GPU" であること
nvidia-smi     # ollama プロセスが VRAM 約 5.5 GB を確保していること
```

運用上の注意:

- **コールドスタート後の初回応答は遅い**(8B モデルで数分): モデルが PCIe x1 リンクを一度通過し、CUDA グラフがコンパイルされるため。2回目以降のロードは数秒で、推論はフル速度で走ります。性能は必ず2回目以降で測定してください
- モデルは VRAM 内に収めること。CPU オフロードされたレイヤーは毎トークン x1 リンクを通過し、スループットが激減します。VRAM 8GB では 8B クラスの Q4 モデルが最適点
- ollama に CUDA Toolkit は**不要**。自分で llama.cpp の CUDA ビルドをしたい場合のみ導入します。この凍結ドライバ構成では CUDA Toolkit 13.0.2(`linux_sbsa` runfile)を使い、コンポーネント選択で **"Driver" のチェックを外す** こと

実測リファレンス(RTX 4060, qwen3:8b Q4, ウォーム時): **生成 43 tok/s、プロンプト評価 約 615 tok/s**。

---

## 7. メンテナンスのルール

1. ドライバスタックは 580.95.05 で凍結。ユーザースペース・モジュール・CUDA を個別に更新しないこと。バージョンが厳密一致しないと壊れます
2. カーネルパッケージは hold 済み。意図的にカーネルを更新したら、モジュールを再ビルド・再インストールすること(セクション4)。hold 中はカーネルのセキュリティ更新も止まるため、未更新カーネルで動かし続けるのではなく、定期的に意図的なカーネル更新＋モジュール再ビルドを計画すること
3. 電源シーケンス: 起動時は GPU 電源を先に、シャットダウン時は GPU を最後に
4. 本構成はコミュニティパッチ依存の非公式構成です。本番用途ではなく実験・開発ノードと割り切ってください

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| ビルド中に `fatal error: stdarg.h: No such file or directory` が大量発生 | `SYSSRC` を渡したため、Pi OS のヘッダ分割で conftest のヘッダ検出が壊れた | `SYSSRC` なしで再ビルド(クロスビルド時は `KERNEL_UNAME=` のみ)。先に `kernel-open/conftest` を掃除 |
| dmesg に `Cannot initialize GSP firmware RM` | 16K カーネルのまま、または未パッチモジュールがロード | `getconf PAGE_SIZE` が 4096 であること。`modinfo nvidia \| grep filename` でどのモジュールが解決されるか確認 |
| `nvidia-smi` が `No devices were found` | ドライバはロードされたが GPU 初期化に失敗。PCIe、GSP、デバイスノード、userspace/module 不一致などがあり得る | `lspci`、`dmesg`、`cat /proc/driver/nvidia/version`、`modinfo nvidia \| grep filename`、`/dev/nvidia*` の順に確認 |
| モジュールビルドがヘッダ関連で失敗 | ヘッダが対象カーネルと不一致 | `uname -r` と `/lib/modules/` を照合。`linux-headers-rpi-v8` を再インストール |
| 負荷時に GPU が消える / PCIe エラー多発 | Gen 3 リンク不安定 | `dtparam=pciex1_gen=2` に設定 |
| `lspci` に何も出ない | ハードウェア: 配線、GPU 電源、投入順序 | セクション0 と [troubleshooting.md](troubleshooting.md) を参照 |
| ollama が CPU-only でインストールされる | ドライバが動作していない | 先にセクション5を直し、インストールスクリプトを再実行 |
