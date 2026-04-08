# tiny-llm-node

**Language:** [English](README.md) | 日本語

Raspberry Pi 5 と USB-PD で動作する超小型 GPU LLM ノード。

tiny-llm-node は、Raspberry Pi 5 をホストとして使用し、小型で電力効率の高い GPU 上で最新のオープンウェイトモデルを動かすために設計された、持ち運び可能なローカル LLM 推論ノードです。

目標は、次のような LLM 実行環境を構築することです。

- 小型
- 手頃な価格
- 可搬性
- 低消費電力
- モジュール構成

---

## Concept

USB-PD だけで給電される小型 GPU ノード上で、ローカル LLM を動かします。

このプロジェクトでは、次のような最小構成アーキテクチャを検討します。

- PCIe ホストとしての Raspberry Pi 5
- PCIe 経由で接続する外部 GPU
- 唯一の電源としての USB-PD
- 効率的な DC-DC 電源設計
- 完全ローカル推論

---

## Architecture Overview

```
USB-PD 3.2 power source
↓
PD trigger (28V)
↓
DC-DC converter (28V → 12V)
↓
X1010 PCIe expansion board
├── Raspberry Pi 5
└── NVIDIA RTX GPU
```

---

## Goals

- 持ち運び可能な LLM ノード
- 最小限の消費電力
- シンプルなハードウェア構成
- 再現可能なビルド
- モジュール型アーキテクチャ

---

## Planned Model Targets

- Gemma 7B
- Mistral 7B
- Llama3 8B
- Qwen 7B

---

## Documentation

詳細は `docs` ディレクトリを参照してください。

- アーキテクチャ
- ハードウェア設計
- 電源設計
- 部品表
- 組み立てガイド
- ソフトウェア設定
- ベンチマーク

---

## Status

現在はプロトタイプ設計段階です。

---

## Repository Structure

```
docs/
architecture.md
hardware.md
power.md
bill_of_materials.md
assembly.md
software_setup.md
```

---

## License

MIT
