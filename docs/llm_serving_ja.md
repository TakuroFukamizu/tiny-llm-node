# LLM サービング — Pi 5 + RTX 4060 でローカル OpenAI 互換 API

**Language:** [English](llm_serving.md) | 日本語

小型の量子化 LLM 数種を **llama.cpp + Vulkan バックエンド**で動かし、LAN に
OpenAI 互換 API として提供する。ゲーム用 AI バックエンド(NPC 対話 + 高速な
意図分類、Unity クライアント向け)として構築したもの。Unity 側から見た API
リファレンスは [game_ai_backend_ja.md](game_ai_backend_ja.md)。

## 結論

- **llama.cpp + Vulkan**(CUDA/ollama ではない)。Vulkan はこのノードで唯一安定
  実証済みの GPU 経路 — 連続 CUDA 計算は共有電源レールを瞬断させ Pi を強制
  リセットする(ハードウェア問題 #2、[power.md](power.md) 参照)。
- 常時起動の2サービス、ともに OpenAI 互換:
  - **:8080** Qwen2.5-3B-Instruct — 対話・JSON/構造化出力・ツール(~75 tok/s)
  - **:8081** Qwen2.5-1.5B-Instruct — 高速な意図/スロット分類(~140 tok/s)
- **systemd サービス化**: 起動時に自動起動、クラッシュ時に自動再起動(#2 の
  リセット含む)、先に 90W キャップを適用。
- 小さい Q4_K_M モデルは VRAM 常駐 → 実行時 PCIe 転送なし → 安全な電力範囲に収まる。

## 前提条件

- NVIDIA ドライバ 580.95.05(open modules)稼働、Vulkan ICD あり。
  [software_setup.md](software_setup.md) 参照。`nvidia-smi` が動き、
  `vulkaninfo --summary` に RTX 4060 が出ること(Pi の V3D だけでない)。
- ollama は停止・無効化(CUDA を使い負荷でクラッシュする):
  `sudo systemctl disable --now ollama 2>/dev/null || true`。

## 1. llama.cpp を Vulkan でビルド

[`services/llm/build-llama-cpp.sh`](../services/llm/build-llama-cpp.sh):

```bash
cmake -B build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)" --target llama-server llama-cli
```

CUDA ツールキット / nvcc は不要(ドライバが Vulkan ICD を提供)。バイナリは
`/home/pi/llama.cpp/build/bin/llama-server`。

## 2. モデルのダウンロード

[`services/llm/download-models.sh`](../services/llm/download-models.sh) が GGUF を
`/home/pi/comfy/models/llm/` に取得(`wget -c` でレジューム可):

| ファイル | サイズ | 役割 |
|---|---|---|
| `Qwen2.5-3B-Instruct-Q4_K_M.gguf` | 1.8 GB | 主対話(8080) |
| `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` | 941 MB | 高速分類(8081) |
| `Qwen2.5-0.5B-Instruct-Q4_K_M.gguf` | 380 MB | 最小/最速(任意) |
| `Llama-3.2-3B-Instruct-Q4_K_M.gguf` | 1.9 GB | 別の声質(任意) |

7B はあえて取得しない — モデルが大きいほど VRAM 転送が増え #2 リスクが上がる。

## 3. サービスのインストール

[`services/llm/install.sh`](../services/llm/install.sh) が unit 3種をコピーし、
systemd を reload、順序循環が無いか検証して有効化する:

```bash
cd services/llm
./install.sh
```

unit:

| Unit | 役割 |
|---|---|
| `gpu-powercap.service` | oneshot: サーバ起動前に `nvidia-smi -pm 1` + `-pl 90` |
| `llama-primary.service` | llama-server on :8080(Qwen2.5-3B) |
| `llama-intent.service` | llama-server on :8081(Qwen2.5-1.5B) |

### 使用している llama-server フラグ

`-ngl 99`(全層 GPU)· `-c 4096`(コンテキスト)· `-np 2`(並列2スロット)·
`-fa on`(flash attention)· `-t 4`(CPU スレッド)· `--host 0.0.0.0`(LAN)·
`--alias <名前>`(`/v1/models` のモデル名)。

### 順序循環の落とし穴(重要)

`gpu-powercap` はサーバより前に動かす必要があるが、`WantedBy=multi-user.target`
と同時に **`After=multi-user.target` にしてはいけない** — これは循環になり、
systemd がジョブを黙って削除する(再起動後に何も自動起動しない)。同梱の unit は
代わりに `After=systemd-modules-load.service` + `Before=llama-*` を使う。検証:

```bash
systemd-analyze verify gpu-powercap.service llama-primary.service llama-intent.service
```

## 4. 運用

```bash
systemctl status gpu-powercap llama-primary llama-intent   # 状態
journalctl -u llama-primary -f                             # ライブログ(8080)
sudo systemctl restart llama-primary                       # 8080 再起動
curl -s localhost:8080/health ; curl -s localhost:8081/health
nvidia-smi -q -d POWER | grep 'Power Limit'                # 90W キャップ確認
```

### ポートのモデル差し替え

unit の `ExecStart -m` パスを変更(モデルは `/home/pi/comfy/models/llm/`)し、
`sudo systemctl daemon-reload && sudo systemctl restart llama-primary`。

### 別ポートでアドホック起動

```bash
/home/pi/llama.cpp/build/bin/llama-server \
  -m /home/pi/comfy/models/llm/Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8082 -ngl 99 -c 4096 -np 2 -fa on -t 4 --alias llama3.2-3b
```

### 画像生成のために GPU を空ける

ComfyUI の int8 画像経路は 8GB 全てを要する。先に LLM サービスを停止し
(`scripts/qwen-image/comfyui-serve.sh` が実施)、終了後に再開:
`sudo systemctl start llama-primary llama-intent`。

## 安定性と安全

- **Vulkan のみ。** CUDA/ollama は連続負荷で Pi を強制クラッシュさせる(#2)。
- **90W キャップ + 小型 VRAM 常駐モデル**で電力範囲内に収める。10回以上の連続
  生成で耐久テスト済み・クラッシュなし。
- **CPU フォールバック**(GPU が不調なら): `ExecStart` に `-ngl 0` を追加
  (遅い ~8 tok/s だが GPU 不使用 = #2 リスクゼロ)、daemon-reload、再起動。
- **電源投入順:** GPU/ATX が先 → Pi が後。
- **LAN 限定。** bind は LAN 用の `0.0.0.0` — 8080/8081 をポートフォワードしない。
  1クライアントに限定するなら `ExecStart` に `--api-key SECRET` を足し、
  `Authorization: Bearer SECRET` を送る。

## 実測性能

| ポート | モデル | スループット |
|---|---|---|
| 8080 | Qwen2.5-3B-Instruct Q4_K_M | ~75 tok/s |
| 8081 | Qwen2.5-1.5B-Instruct Q4_K_M | ~140 tok/s |

4060・90W キャップ・`-fa on`・VRAM 常駐での実測。
