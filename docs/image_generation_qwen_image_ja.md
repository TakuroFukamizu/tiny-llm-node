# 画像生成 — Qwen-Image-2.1 (4bit) on Pi 5 + RTX 4060

**言語:** [English](image_generation_qwen_image.md) | 日本語

このノードで unsloth の **Qwen-Image-2.1 4bit GGUF** を使い、text-to-image
(T2I)・image-to-image (I2I)・指示ベースの **Edit** を動かすための再現手順。

2026-09-25 に動作確認済み (Pi 5 8GB + RTX 4060 8GB、PCIe Gen 2 x1 eGPU)。

---

## 結論(最重要)

**unsloth の Qwen-Image-2.1 4bit GGUF は、ComfyUI ではなく
[`stable-diffusion.cpp`](https://github.com/leejet/stable-diffusion.cpp) +
Vulkan バックエンドで動く。** T2I / I2I / Edit の3機能すべてが、このノードで
CUDA 負荷を落とす電源瞬断(ハードウェア問題 #2、後述)を起こさずに GPU 拡散を
完走する。

ComfyUI が使えない理由:

1. **unsloth の 4bit GGUF には `general.architecture` メタデータが無く**、
   sd.cpp 向けにビルドされているため、ComfyUI(および ComfyUI-GGUF)はモデル
   アーキテクチャを判定できずロードを拒否する。ComfyUI-GGUF は
   Qwen-Image-2.1 の DiT に非対応。
2. **ComfyUI ネイティブ経路は bf16 か int8 が必要**:
   - bf16 は 8GB VRAM に載らない。
   - int8 (`int8_convrot`) 経路は `comfy_kitchen` バックエンド依存で、これは
     **CUDA 13.0 (cu130) ビルドの PyTorch** を要求する。最終的に cu130 の
     aarch64 wheel (`torch 2.11.0+cu130`) は見つかり kitchen backend も
     `available: True, disabled: False` にはなったが、ComfyUI 上の int8 生成の
     最終テストは未完了であり、不要。sd.cpp 経路が全要件を満たしている。
3. **sd.cpp は Qwen-Image-2.1 と Qwen3-VL をネイティブ認識**し、4bit GGUF を
   そのまま動かす。これが実証済みのサポート経路であり、本書が扱う経路。

> 放棄した ComfyUI int8 実験を再開する場合のみ
> [付録A](#付録a--comfyui-int8-経路非推奨) を参照。通常利用では ComfyUI は無視。

---

## 1. 前提条件

NVIDIA ドライバ + カーネルのセットアップが前提。ここでは繰り返さない —
まず [software_setup_ja.md](software_setup_ja.md) に従い、以下を確認する:

- NVIDIA ドライバ **580.95.05**(open kernel modules、ARM パッチ版)、4K ページ
  カーネル `kernel8.img`、構成は**凍結**。`nvidia-smi` で RTX 4060 が見えること。
- **Vulkan が NVIDIA ドライバ経由で動く** — CUDA Toolkit (`nvcc`) は未導入で、
  不要。確認:
  ```bash
  vulkaninfo --summary | grep -i "deviceName"
  # => GPU0: NVIDIA GeForce RTX 4060 (GPU1 は Pi の V3D。無視)
  ```
- ビルドツール: `cmake`, `glslc`, `libvulkan-dev`, `ccache`, C++ コンパイラ
  (本ノードは導入済み)。

### ハードウェア問題 #2 — 電源瞬断(実行前に必読)

Pi と GPU は **USB-PD → DC-DC の1系統レール**(X1010 経由)を共有している。
大きな VRAM 転送や持続的 CUDA 計算時に GPU 電流スパイクが共有レールの電圧を
サグさせ、**Pi ごと強制リセット**する(永続 journald で確認: boot ログが
`load_tensors` の途中でプツリと切れ、panic 痕跡なし、次 boot で ext4 orphan
cleanup)。GPU 側の電力キャップだけでは防げず、`nvidia-smi -pl 90` でも持続
CUDA/ollama 負荷で再発した。

**Vulkan が安定経路である理由:** 経験上、`stable-diffusion.cpp` の **Vulkan**
バックエンドは拡散を**クラッシュせず完走**した(CUDA 経路は完走しなかった)。
電気的な確定要因は不明だが、Vulkan + 逐次ロード + 90W キャップが、このノードで
画像ジョブを完走できる唯一実証された組み合わせ。**生成前に必ずキャップを適用**
(ラッパースクリプトが自動で実施):

```bash
sudo nvidia-smi -pm 1       # persistence mode
sudo nvidia-smi -pl 90      # 90W キャップ(RTX 4060 の有効範囲は 90〜115W。60W は拒否)
```

キャップをしても大解像度・長時間では残留リスクがある。
**小さく始めること(512px / 10 steps)。**

---

## 2. ストレージ構成 — SSD 上の ext4 イメージ

**問題:** SD カードのルートは小さく(約29GB、逼迫)、拡散スタック(モデルだけで
約15GB、加えて sd.cpp ビルドと出力)が載らない。1.9TB SSD は **exFAT**
(`/media/pi/SUNEAST`)で、Linux のビルドツリー(権限/シンボリックリンク)や
巨大モデルの mmap には不向き。

**解決:** exFAT SSD 上に**イメージファイル内の ext4 ファイルシステム**を1つ
作り、`/home/pi/comfy` にループマウントする。sd.cpp ビルド・Python venv・
モデル・出力のすべてをこの ext4 イメージ内に置く。

### イメージ作成(初回のみ)

```bash
truncate -s 64G /media/pi/SUNEAST/comfyui-ext4.img
mkfs.ext4 -m 0 /media/pi/SUNEAST/comfyui-ext4.img   # -m 0: root 予約ブロック無し
mkdir -p /home/pi/comfy
sudo mount -o loop /media/pi/SUNEAST/comfyui-ext4.img /home/pi/comfy
sudo chown pi:pi /home/pi/comfy
```

> 注: exFAT は `fallocate`/sparse ファイル非対応のため、本ノードでは拡張時に
> `truncate` ではなく `dd` 追記でイメージを大きくした。初回作成はどちらでも可で、
> `truncate` が最も簡単。

### 後から拡張(容量不足時)

```bash
sudo umount /home/pi/comfy
truncate -s +32G /media/pi/SUNEAST/comfyui-ext4.img
e2fsck -f /media/pi/SUNEAST/comfyui-ext4.img
resize2fs /media/pi/SUNEAST/comfyui-ext4.img
sudo mount -o loop /media/pi/SUNEAST/comfyui-ext4.img /home/pi/comfy
```

(アンマウントせずオンライン拡張も可能: バッキングファイルに追記 →
`sudo losetup -c /dev/loopN` で容量更新 → `sudo resize2fs /dev/loopN`。)

### 再起動後に毎回マウント — `comfy-mount.sh`

イメージは `/etc/fstab` に**入れていない**。起動ごと(かつ SSD 接続後)に
マウントが必要。ラッパーが自動呼び出しするが、手動では(スクリプトは
[`scripts/qwen-image/comfy-mount.sh`](../scripts/qwen-image/comfy-mount.sh) にコミット済み):

```bash
# /home/pi/comfy-mount.sh
#!/bin/bash
IMG=/media/pi/SUNEAST/comfyui-ext4.img; MNT=/home/pi/comfy
mountpoint -q "$MNT" && { echo "already mounted"; exit 0; }
[ -f "$IMG" ] || { echo "image not found (SSD mounted at /media/pi/SUNEAST?)"; exit 1; }
sudo mount -o loop "$IMG" "$MNT" && sudo chown pi:pi "$MNT" && echo "mounted $MNT"
```

```bash
mountpoint -q /home/pi/comfy || /home/pi/comfy-mount.sh
```

> 大容量のダウンロード・ビルドは**すべて** `/home/pi/comfy` 配下に。
> 数GB のモデルを SD カードルートに置かないこと。

---

## 3. stable-diffusion.cpp を Vulkan でビルド

```bash
mountpoint -q /home/pi/comfy || /home/pi/comfy-mount.sh
cd /home/pi/comfy
git clone --recursive https://github.com/leejet/stable-diffusion.cpp
cd stable-diffusion.cpp

# Vulkan バックエンド(CUDA ではない — 問題 #2 参照)。nvcc 不要。
cmake -B build -DSD_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(nproc)
```

成果物:

- `build/bin/sd-cli`    — 以下で使う CLI
- `build/bin/sd-server` — HTTP サーバ(本書では未使用)

**Vulkan が RTX 4060 を選んでいることを確認。** 実行の冒頭に:

```
ggml_vulkan: Found 2 Vulkan devices:
ggml_vulkan: 0 = NVIDIA GeForce RTX 4060 (NVIDIA) | ... matrix cores: NV_coopmat2
ggml_vulkan: 1 = V3D 7.1.10.2 (V3DV Mesa) | ...        <- Pi の iGPU。device 0 を使用
```

> **なぜここで CUDA でなく Vulkan か:** (1) CUDA Toolkit は未導入で、凍結された
> ドライバ構成を崩してまで追加しない。(2) CUDA 経路は持続計算で問題 #2 の瞬断を
> 誘発するが、Vulkan 経路は拡散を繰り返し完走した。Vulkan は NVIDIA ドライバの
> Vulkan ICD 経由で RTX 4060 に届くため toolkit 不要。

---

## 4. モデルファイル

4ファイルは ext4 イメージ内の `ComfyUI/models/` 配下に置く(このディレクトリ
構成は sd.cpp 採用前の名残。sd.cpp は絶対パスで読むだけ)。
[`scripts/qwen-image/dl-models.sh`](../scripts/qwen-image/dl-models.sh) で取得
(`wget -c` でレジューム可):

| 用途 | ファイル | サイズ | 入手元 (HF, `unsloth/…`) |
|---|---|---|---|
| 拡散 (DiT) 4bit | `models/unet/qwen-image-2.1-Q4_K_M.gguf` | 4.0GB | `Qwen-Image-2.1-GGUF/resolve/main/qwen-image-2.1-Q4_K_M.gguf` |
| テキストエンコーダ (LLM) 4bit | `models/text_encoders/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf` | 4.8GB | `Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf` |
| 視覚投影 (Edit のみ) | `models/text_encoders/Qwen3-VL-8B-Instruct-mmproj-F16.gguf` | 1.1GB | `Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-F16.gguf` |
| VAE | `models/vae/qwen_image_2.1_vae_bf16.safetensors` | 645MB | `Qwen-Image-2.1-FP8/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors` |

```bash
cd /home/pi/comfy/ComfyUI
B=https://huggingface.co/unsloth
dl(){ echo "### $(date +%T) DL $2"; wget -c -q --show-progress -O "$2" "$1" 2>&1 | tail -1; }
dl "$B/Qwen-Image-2.1-GGUF/resolve/main/qwen-image-2.1-Q4_K_M.gguf"              models/unet/qwen-image-2.1-Q4_K_M.gguf
dl "$B/Qwen-Image-2.1-FP8/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors" models/vae/qwen_image_2.1_vae_bf16.safetensors
dl "$B/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf" models/text_encoders/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf
dl "$B/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-F16.gguf"                   models/text_encoders/Qwen3-VL-8B-Instruct-mmproj-F16.gguf
```

> **VAE は unsloth 版**(`qwen_image_2.1_vae_bf16.safetensors`)を使う。sd.cpp は
> これを正しくロードする。放棄した ComfyUI 実験由来の Comfy-Org 版 VAE
> (`qwen_image_2.1_vae_comfyorg.safetensors`、同じく645MB)は **ComfyUI 専用**。
> sd.cpp には渡さないこと(トラブルシュート参照)。

---

## 5. 実行 — sd-cli コマンド

### 環境ファイル — [`scripts/qwen-image/qwen-image.env`](../scripts/qwen-image/qwen-image.env)

```bash
# /home/pi/comfy/qwen-image.env
export SD_BIN="/home/pi/comfy/stable-diffusion.cpp/build/bin/sd-cli"
export QIM_DIFF="/home/pi/comfy/ComfyUI/models/unet/qwen-image-2.1-Q4_K_M.gguf"
export QIM_LLM="/home/pi/comfy/ComfyUI/models/text_encoders/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"
export QIM_MMPROJ="/home/pi/comfy/ComfyUI/models/text_encoders/Qwen3-VL-8B-Instruct-mmproj-F16.gguf"
export QIM_VAE="/home/pi/comfy/ComfyUI/models/vae/qwen_image_2.1_vae_bf16.safetensors"
export QIM_OUT="/home/pi/comfy/outputs"
```

### フラグ早見表

| フラグ | 意味 |
|---|---|
| `--diffusion-model` | Qwen-Image-2.1 DiT GGUF(4bit 拡散モデル) |
| `--llm` | Qwen3-VL テキストエンコーダ GGUF(プロンプト→条件付け) |
| `--llm_vision` | mmproj GGUF(視覚投影)。**Edit で必須**。参照画像を「見る」ために必要 |
| `--vae` | VAE safetensors(ピクセル⇔潜在空間の encode/decode) |
| `-p` | プロンプト(T2I/I2I: 説明、Edit: 編集指示) |
| `-W` / `-H` | 出力の幅 / 高さ(px) |
| `--steps` | サンプラーのステップ数。10=高速、20=高品質 |
| `--cfg-scale 2.5` | classifier-free guidance。**Qwen-Image の定番は 2.5** |
| `--sampling-method euler` | サンプラー。**Qwen-Image の定番は euler** |
| `-i` / `--strength` | **I2I:** init 画像 / デノイズ強度(0=init 維持、1=プロンプト優先、~0.6 が標準) |
| `-r` | **Edit:** 編集対象の参照画像(複数は `-r` を複数回) |
| `--flow-shift` | flow-matching のタイムステップシフト(別名 `shift`)。調整時以外は既定。上げると構造寄り |
| `--vae-tiling` | VAE デコードをタイル分割しピーク VRAM を抑制。Edit で VAE デコード警告/OOM 回避に使用 |
| `-o` | 出力 PNG パス |

### T2I — テキストから画像

```bash
source /home/pi/comfy/qwen-image.env
sudo nvidia-smi -pm 1; sudo nvidia-smi -pl 90
"$SD_BIN" --diffusion-model "$QIM_DIFF" --llm "$QIM_LLM" --vae "$QIM_VAE" \
  -p "a cute red panda on a bamboo branch" \
  -W 512 -H 512 --steps 10 --cfg-scale 2.5 --sampling-method euler \
  -o "$QIM_OUT/t2i.png"
```

### I2I — 画像から画像

init 画像と strength を追加:

```bash
"$SD_BIN" --diffusion-model "$QIM_DIFF" --llm "$QIM_LLM" --vae "$QIM_VAE" \
  -i init.png --strength 0.6 \
  -p "watercolor painting style" \
  --steps 10 --cfg-scale 2.5 --sampling-method euler \
  -o "$QIM_OUT/i2i.png"
```

### Edit — 指示ベースの編集

視覚投影 (`--llm_vision`) と参照画像 (`-r`) を追加し、プロンプトは編集指示に。
`--vae-tiling` で VRAM を緩和:

```bash
"$SD_BIN" --diffusion-model "$QIM_DIFF" --llm "$QIM_LLM" --llm_vision "$QIM_MMPROJ" --vae "$QIM_VAE" \
  -r photo.png \
  -p "Change the background to a snowy forest" \
  --steps 10 --cfg-scale 2.5 --sampling-method euler --vae-tiling \
  -o "$QIM_OUT/edit.png"
# 複数参照: -r を増やす。例 -r a.png -r b.png -p "combine ..."
```

### 便利ラッパー

`/home/pi/comfy/` の3スクリプト(リポジトリでは
[`scripts/qwen-image/`](../scripts/qwen-image/) にコミット済み)が env 読み込み・
マウント確認・90W キャップ・解像度/ステップの既定値を面倒見る。出力は
`/home/pi/comfy/outputs/`。

```bash
# T2I:  prompt [W] [H] [steps] [out.png]
/home/pi/comfy/t2i.sh "a cute red panda on a bamboo branch" 768 768 20

# I2I:  init.png prompt [strength] [steps] [out.png]
/home/pi/comfy/i2i.sh input.png "watercolor painting style" 0.6 20

# Edit: ref.png "instruction" [steps] [out.png]  (2枚目参照は QIM_REF2)
/home/pi/comfy/edit.sh photo.png "Change the background to a snowy forest" 20
QIM_REF2=other.png /home/pi/comfy/edit.sh a.png "combine the two scenes" 20
```

ラッパー既定は **768×768 / 20 steps**。最速・最小リスクなら **512×512 / 10
steps** に落とす。

---

## 6. 実測性能と安定性

2026-09-25 実測、512×512 / 10 steps、cfg 2.5、euler、Vulkan、90W キャップ。
時間は**モデルロードを含むエンドツーエンド**(コールド)。GPU は逐次ロード
(拡散→テキストエンコーダ→VAE)のため、各段が PCIe Gen 2 x1 を一度ずつ流れる。

| モード | 全体 | テキストエンコード | サンプリング | VAE | 備考 |
|---|---|---|---|---|---|
| T2I | **約159秒** | 約36秒 | 約106秒 | 約17秒 (decode) | 512²/10 steps |
| I2I | **約61秒** | — | — | — | init + strength 0.6、実効ステップ減で高速 |
| Edit | T2I より長い | 約71秒 | — | タイル化 | mmproj 条件付けが支配的、最も VRAM を使う |

3モードとも Vulkan 経路で**クラッシュせず完走**。

安定性の注意(問題 #2):

- **必ず**先に `sudo nvidia-smi -pm 1 && sudo nvidia-smi -pl 90`(ラッパーは自動)。
- **小さく始める: 512px / 10 steps。** 成功してから 768px / 20 steps へ。
  1024px は Qwen-Image のネイティブ2K品質を引き出すが、VRAM・時間・瞬断リスクが
  増える。
- Edit が最も VRAM を使う(mmproj + 参照エンコード)。512px に抑え `--vae-tiling`
  を使う。
- 品質対リスク: `--steps 20+` は品質向上と引き換えに時間/リスクが比例増。

---

## 7. トラブルシューティング

- **ComfyUI が「アーキテクチャ検出不可」/ GGUF をロードしない。**
  想定どおり。unsloth 4bit GGUF は `general.architecture` メタ無しで sd.cpp 向け
  ビルド。ComfyUI で読もうとせず sd.cpp を使う。

- **sd.cpp で VAE アーキテクチャ不整合。** **unsloth** 版 VAE
  (`qwen_image_2.1_vae_bf16.safetensors`)を渡す。`*_comfyorg.safetensors` は
  ComfyUI 専用で、sd.cpp に渡す(逆も)と VAE ロードで arch/shape 不整合になる。

- **Qwen3-VL の「12288次元」/テンソル形状エラー(ComfyUI のみ)。** ComfyUI で
  Qwen3-VL テキストエンコーダを GGUF ロードした場合に発生。sd.cpp 経路には無関係
  (ComfyUI では int8 ネイティブ safetensors を標準 `CLIPLoader` でロードして
  回避した — 付録A)。

- **Edit モードで VRAM 不足 / OOM。** mmproj + 参照エンコードで圧迫。`--vae-tiling`
  を使い、512px に抑え、ステップを減らし、不要なら複数 `-r` を避ける。

- **生成中に Pi が再起動(瞬断、問題 #2)。** 90W キャップ適用を確認
  (`nvidia-smi -q -d POWER | grep 'Power Limit'`)。解像度とステップを下げる。
  再発するなら本質的対策は電気側 — GPU 給電を Pi のレールから分離する
  ([power.md](power.md) 参照)。Vulkan は緩和であって保証ではない。

- **ラッパーが "image not found"。** ext4 イメージが未マウント、または SSD が
  `/media/pi/SUNEAST` に無い。`/home/pi/comfy-mount.sh` を実行。

- **Vulkan に 4060 でなく Pi の V3D しか出ない。** NVIDIA ドライバ / Vulkan ICD
  が未ロード。`nvidia-smi` とドライバを [software_setup_ja.md](software_setup_ja.md)
  で再確認。

---

## 付録A — ComfyUI int8 経路(非推奨)

記録目的のみ。**不要**かつ完全には検証していない。目的は Qwen-Image-2.1 int8 を
ComfyUI 本体で動かすことだった。放棄時点の状態:

- `torch 2.11.0+cu130`(aarch64/cp313)は**発見・導入できた**(piwheels の干渉を
  止めるため `PIP_CONFIG_FILE=/dev/null` 必須)。これで `comfy_kitchen` CUDA
  backend が有効化(`available: True, disabled: False`)し `int8_convrot` GEMM
  ops が使えた。
- int8 ネイティブ重みを取得:
  `diffusion_models/qwen_image_2.1_int8_convrot.safetensors`(6.8GB)、
  `text_encoders/qwen3vl_8b_int8_convrot.safetensors`(8.8GB)、Comfy-Org 版 VAE。
- テキストエンコーダは **ネイティブ safetensors を標準 `CLIPLoader`**(type
  `qwen_image`)でロードする必要があった(`CLIPLoaderGGUF` ではなく。12288次元
  不整合を回避)。
- 最終 int8 生成テストは未実行。たとえ動いても **CUDA 経路**で動くため、sd.cpp の
  Vulkan 経路が回避している問題 #2 の瞬断リスクを負う。**sd.cpp を推奨。**
