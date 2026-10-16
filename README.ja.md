# gpu-duet

[中文](README.md) · [English](README.en.md) · 日本語

**ローカル LLM と音声クローン TTS で 8 GB の GPU を 1 枚共有。生成しながら話し始め、長い回答でもほとんど音切れしません。**

*ローカル LLM とゼロショット音声クローン TTS が、8 GB の GPU 1 枚を交代で使います。ローカル HTTP サービスとして使うことも、xiaozhi ファームウェアを載せた ESP32 ボード向けのオフライン音声サーバーとして使うこともできます。*

[効果](#効果) · [仕組み](#仕組み) · [必要なもの](#必要なもの) · [クイックスタート](#クイックスタート) · [ボードをつなぐ](#ボードをつなぐ使い方-b) · [既知の制限](#既知の制限) · [よくある問題](#よくある問題) · [上流への修正](#上流への修正) · [ドキュメント](#ドキュメント) · [ハマりどころ 32 件](docs/pitfalls.md)

[![CI](https://github.com/korexil/gpu-duet/actions/workflows/ci.yml/badge.svg)](https://github.com/korexil/gpu-duet/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/korexil/gpu-duet)](https://github.com/korexil/gpu-duet/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Bilibili](https://img.shields.io/badge/デモ動画-Bilibili-00A1D6)](https://www.bilibili.com/video/BV15Cpr6eEMx)

| 最初の一声 | 音切れ合計 | VRAM |
|:-:|:-:|:-:|
| 4.4&nbsp;→&nbsp;**2.3&nbsp;s** | 4.59&nbsp;→&nbsp;**0.09&nbsp;s** | **7.3&nbsp;GB**&nbsp;/&nbsp;8&nbsp;GB |

<a href="https://www.bilibili.com/video/BV15Cpr6eEMx"><img src="docs/img/before-after.ja.webp" alt="実機の録画、同じノート PC・同じ質問。改善前は最初の一声 5.95 s、その後ほぼ毎文で途切れる。改善後は 1.92 s、回答全体で途切れは 1 回（0.1 s）だけ" width="760"></a>

<sub>実機の録画（2 倍速）、同じノート PC・同じ質問。赤いバーは音が途切れたところです。画像をクリックすると完全版の動画（Bilibili、3 分 28 秒、中国語）。質問はその場でリアルタイムに話したもので、動画では質問の声だけ後から合成音声に差し替えています。ボードの回答はその場の音声そのままです。</sub>

**ボードなしで試す：** Windows + NVIDIA 8 GB GPU + 3～10 秒の録音があれば、[クイックスタート](#クイックスタート)の手順 1～5 で動きます。最後の手順で音切れの合計が表示されます。

## できること

- **8 GB の GPU 1 枚に 2 つのモデルを搭載**：Gemma 4 E4B（LLM）+ Qwen3-TTS 1.7B（3～10 秒の録音から声をクローン）。カード全体の VRAM 使用量は約 7.3 GB（2 つのモデルで約 6.8 GB、残りはデスクトップなど）。
- **LLM と TTS が GPU を交代で使用**：1 分を超える長い回答も生成しながら再生し、音切れの合計は 4.59 s から 0.09 s（ネイティブエンジンへの切り替えと交代制の合計） に減少。
- **文ごとの整音**：1 文合成するたびに音量・間・ピークをそろえるので、小さなスピーカーでも音量が上下したり音割れしたりしません。
- **完全オフライン、Windows PC 1 台で動作**：普通のローカル HTTP サービスとしても、xiaozhi の ESP32 ボード用サーバーとしてもそのまま使えます。

## 効果

| | 導入前 | 導入後 |
|---|---|---|
| 長い回答の第一声（質問を送ってからサーバーが最初の音声フレームを送るまで。ボードが鳴るのはさらに後で、その差は未測定） | 4.4 s | **2.3 s** |
| 長い回答の音切れ合計（再生中に止まった時間） | 4.59 s、48～60 回途切れる | **0.09 s** |
| VRAM 合計（8 GB のカード） | 約 8.3 GB、収まらない（パッチなしのネイティブエンジン、推定） | **約 7.3 GB** |

<sub>RTX 4060 Laptop 8 GB。第一声・音切れ：同じ長文回答の質問をそれぞれ 3 回ずつ行った平均（4 段階を同じ時間帯に測定）。質問はテキストで送信（第一声に VAD・ASR は含まない）。音切れはデバイスの 0.3 s プリバッファを基にサーバー側で推定。「導入前」は PyTorch 版 TTS + CPU 上の LLM。VRAM：「導入後」は実測 7,448 MiB、「導入前」はパッチで削減した 488 + 594 MiB を足し戻した推算値。GB はタスクマネージャーの表記に合わせています（1 GB = 1024 MiB）。スケジューリングだけを変えた対照実験（各群 6 回、0.5 s バッファの再生を模擬）では、交代なしでは 6/6 回で音切れが発生し、交代ありでは 0/6 回でした。全データ：[docs/benchmarks.md](docs/benchmarks.md)。</sub>

## xiaozhi（小智）との関係

xiaozhi はオープンソースの ESP32 音声アシスタントで、ボード上の [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) ファームウェアと、PC 上の [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) サーバーの 2 つで構成されています。

**このリポジトリの中核部分（TTS エンジン、TTS サービス、GPU の交代利用、文ごとの整音）は xiaozhi に依存しません**。xiaozhi が必要になるのはボードをつなぐときだけです。使い方は 3 通りあります。

| 使い方 | 向いている用途 | 動かすもの | xiaozhi は必要？ |
|---|---|---|---|
| **A. ボードなし** | 自作プログラムや他の音声フレームワーク向けのローカル LLM + TTS | `tts-service` + `llama-server` | 不要 |
| **B. スタンドアロンサーバー** | xiaozhi ボードはあるが、Docker・データベース・管理画面は入れたくない | A + `standalone/server.py` | ボード上のファームウェアのみ |
| **C. xiaozhi-esp32-server に組み込む** | 管理画面、複数デバイス、話者認識、ツール呼び出しが必要 | A + 公式 Docker スタック + [`server/`](server/) の provider | ファームウェア + サーバー |

ボードをつなぐ 2 つの方法の詳しい比較と、各部品の依存関係：[docs/architecture.md](docs/architecture.md)。

## 仕組み

```
マイク → VAD · ASR（CPU） → LLM（GPU） ⇄ 交代 ⇄ TTS（GPU） → スピーカー
```


- **VRAM の節約**：TTS をパッチを当てた C++ ネイティブエンジンに置き換え、VRAM を約 5.0 → 4.0 GB に削減して LLM の場所を空けます。
- **GPU の交代利用**：両方を同時に動かすと TTS が遅くなり、合成が再生に追いつきません。そこで LLM は 1 文書き終えるごとに一時停止し、TTS がその文を合成し終えてから続きを書きます。
- **文ごとの整音**：1 文合成するたびに、余分な無音のカット、ゲインの統一、リミッター処理、間の補完を行います。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/img/timeline-dark.svg">
  <img alt="模式図：同時に動かすと TTS が遅くなり、第一声 3.20 s・音切れ合計 2.27 s。交代で動かすと第一声 1.83 s・音切れ 0（benchmark の中央値）" src="docs/img/timeline-light.svg" width="480">
</picture>

詳しい説明とパラメータ：[docs/how-it-works.md](docs/how-it-works.md)。

## 必要なもの

| | |
|---|---|
| GPU | NVIDIA、VRAM 8 GB（RTX 4060 Laptop で動作確認。ビルド済みエンジンは RTX 20 / 30 / 40 / 50 シリーズに対応） |
| メモリ | 16 GB |
| OS | Windows 10 / 11 x64（Windows 11 で動作確認。Linux では CI が CPU 版の C ブリッジをビルドして ABI を確認しているだけで、CUDA エンジンとパイプライン全体は動かしたことがありません） |
| ディスク | 約 13 GB（モデルのダウンロードは約 8.8 GB） |
| 声 | 3～10 秒のきれいな録音と、その書き起こし（一字一句そのまま）（[準備のしかた](docs/voice-reference.md)） |
| ボード（任意） | xiaozhi-esp32 ファームウェアを書き込んだ ESP32-S3（M5Stack Atom EchoS3R、Stack-chan で動作確認） |

## クイックスタート

リポジトリのルートで PowerShell から実行します。Python 3.10+ と git が必要です（「このシステムではスクリプトの実行が無効になっている」と表示される場合は、先に `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` を実行してください）。以下ではリポジトリの隣にある `..\gpu-duet-engine`、`..\models`、`..\llama.cpp` を例にしています。自分のディレクトリに置き換えてください。

**1. エンジンのダウンロードと依存関係のインストール。** [Releases](https://github.com/korexil/gpu-duet/releases/latest) から zip をダウンロードし、`..\gpu-duet-engine` に展開します（SHA256 は [native-tts/README.md](native-tts/README.md) を参照）。CUDA ランタイムは pip で入れるので、CUDA Toolkit のインストールは不要です。

```powershell
pip install -r tts-service\requirements.txt nvidia-cuda-runtime-cu12 nvidia-cublas-cu12
```

**2. モデルのダウンロード。** TTS は F16 GGUF に変換して `..\models\tts` に、LLM は `..\models\llm` に置かれます。変換後は `..\models\src` を削除してかまいません。

```powershell
.\native-tts\setup-models.ps1 -Out ..\models -InstallDeps
```

**3. 声の抽出。** 録音を `my_voice.wav` に、一字一句そのままの書き起こしを `my_voice.txt`（UTF-8）に保存します。

```powershell
python tts-service\extract_voice.py --library ..\gpu-duet-engine\qwen3_tts_bridge.dll `
    --models ..\models\tts --wav my_voice.wav --text my_voice.txt --out voice\icl-prompt.json
```

**4. 先に TTS を起動し、別のウィンドウで LLM を起動。** 順番を逆にすると、TTS が共有メモリに追い出されて遅くなることがあります。`llama-server` は [llama.cpp Releases](https://github.com/ggml-org/llama.cpp/releases) の Windows CUDA 版を使います。

```powershell
python tts-service\server.py --library ..\gpu-duet-engine\qwen3_tts_bridge.dll `
    --models ..\models\tts --icl-prompt voice\icl-prompt.json --language zh
```

```powershell
.\scripts\start-llm.ps1 -LlamaServer ..\llama.cpp\llama-server.exe `
    -Model ..\models\llm\gemma-4-E4B-it-q4_0-exact.gguf
```

**5. ボードなしで試す。** 出力の `underrun_total_s` が音切れの合計で、0 なら途切れていません。

```powershell
python tools\turn_taking_sim.py --mode turns --question "说说你最喜欢的季节，讲长一点。"
```

ここまでで使い方 A の状態です。自作プログラムから `http://127.0.0.1:18766/stream` を呼べば音声を合成できます。API は [tts-service/README.md](tts-service/README.md) を参照してください。

### ボードをつなぐ（使い方 B）

```powershell
pip install -r standalone\requirements.txt
python standalone\setup_models.py      # ASR・VAD・ノイズ除去モデル、約 240 MB
copy standalone\config.example.yaml standalone\config.yaml
python standalone\server.py
```

ボードの OTA アドレスを、起動ログに表示される `http://<このPCのIP>:8003/xiaozhi/ota/` に変更し、ファイアウォールで TCP 8000 と 8003 を許可します。OTA アドレスの変更方法と、ボードがない場合のテスト方法は [standalone/README.md](standalone/README.md) にあります。

使い方 C は [server/README.md](server/README.md)、エンジンを自分でビルドする場合は [native-tts/README.md](native-tts/README.md) を参照してください。

## 既知の制限

- 実機で試したのは RTX 4060 Laptop + Windows 11 のみです。Linux では CI が CPU 版の C ブリッジをビルドして ABI を確認しているだけで、CUDA エンジンとパイプライン全体は動かしたことがありません。macOS は未検証です。
- `llamacpp_paced` はツール呼び出し（MCP、デバイスの音量調整など）に対応していません。スタンドアロンサーバーには話者認識、記憶、ストリーミング ASR がありません。
- 文と文の間の音量は、各文の冒頭 0.6 s だけをもとにゲインを決めています。ラウドネス規格に準拠したものではありません。
- スタンドアロンサーバーのデフォルト設定は、信頼できる LAN 内での利用のみを想定しています（平文通信、token はデフォルトで空）。インターネットに直接公開しないでください。[セキュリティ](standalone/README.md#安全)を参照。

全一覧：[docs/architecture.md](docs/architecture.md#限制)。

## よくある問題

以下の数値はすべて RTX 4060 Laptop（8 GB）+ Windows 11 で測定したものです。

### ローカル音声アシスタントの声が途切れ途切れになる、長い回答が途中で止まる

LLM と TTS が同時に 1 枚の GPU を使うと TTS が遅くなり、合成が再生に追いつきません。このリポジトリでは 2 つが GPU を交代で使います。同じサーバーパイプラインで、同時に動かした場合と比べて音切れの合計は 2.84 s から 0.09 s に減りました（それぞれ 3 回の平均、[4 段階の測定](docs/benchmarks.md)）。スケジューリングだけを変えた対照実験では、同時実行で 6/6 回、交代制で 0/6 回の音切れでした（再生を模擬、[ハマりどころ #9](docs/pitfalls.md)）。LLM の速度制限や Windows の GPU スケジューリング優先度の変更は試しましたが、効果はありませんでした。

### 質問してから話し始めるまで数秒かかる

ここでいう「話し始め」は、サーバーがボードへ最初の音声フレームを送った時点です。ボード側のバッファリングとデコードはその後で、測定していません。

- LLM の置き場所とスケジューリング（TTS はいずれもネイティブエンジン）：LLM が CPU 上で 5.3 s、GPU 上で 3.9 s、交代制にすると 2.3 s です（それぞれ 3 回の平均、[4 段階の測定](docs/benchmarks.md)）。
- GPU はアイドル時にクロックが下がります。8 s アイドルの後、TTS の最初のブロックは 0.64 s、温まった状態なら 0.17 s です（ハマりどころ #11）。
- スタンドアロンサーバーは区切り末尾の無音を削るために 1.2 s 分の音声を保留します。最初に届いた音声が 1.2 s に満たないと、次のブロックが届くまで最初のフレームを送れません。交代制で 0.19～0.20 s、同時実行で 0.46～0.48 s 余計に待ちます（[PCM トレース](bench/results/traces-2026-10-16/)）。

### VRAM はぎりぎり足りているのに、エラーもなくだんだん遅くなる

実際に起きたケース：TTS のインスタンスが 2 つ GPU に載っていたとき、Windows のドライバーはエラーを出さず、llama-server の VRAM の大部分を共有メモリに移し、デコードが 66 から 45 tok/s に落ちました。タスクマネージャーで各プロセスの「専用 GPU メモリ」を確認し、起きたら影響を受けたプロセスを再起動してください。普段は TTS を先に、LLM を後に起動します（ハマりどころ #12、[仕組み](docs/how-it-works.md)）。

### 8 GB の GPU に LLM と TTS が収まらない

パッチなしだと推定で約 8.3 GB 必要で、8 GB を超えます。TTS エンジンへの 2 つのパッチで約 1 GB 節約できます。Q/K/V 重みの重複排除で 488 MiB、テキスト埋め込み表を CPU に置いて 594 MiB です。パッチ適用後は 2 つのモデルで約 6.8 GB、カード全体の使用量はデスクトップ込みで 7.3 GB です。[docs/how-it-works.md](docs/how-it-works.md) を参照。

### 文ごとに音量がばらつく、間の長さがばらつく、小さいスピーカーで音が割れる

合成した文を同じ手順で整えます。冒頭 0.6 s から決めたゲインを文全体に掛け、冒頭の無音を削り、文末の間を一定の長さ（既定 0.9 s）に揃え、なめらかなリミッターでピークを -6 dBFS に抑えます（ハマりどころ #14～#16）。

### 話し始める前に毎回「プツッ」と鳴る

テストしたボードはアイドル時にアンプを切り、再生を始めるときに入れ直します。ほかのボードでも同じとは限りません。1.5 s 以上無音が続いた後の最初の文は、頭に 0.3 s の無音を入れます（ハマりどころ #17）。

### 日本語などを含むフォルダーからモデルを開けない

上流の qwen3-tts.cpp はモデルと参照音声を ANSI の `fopen` で開くため、非 ASCII のパスでは別のファイルを探してしまいます。このリポジトリのエンジンパッチは先にワイド文字に変換してから開きます（`src/common/utf8_path.h`）。日本語のフォルダー名で動作を確認済みで、中国語のフォルダー名は個別にテストしていません（ハマりどころ #32）。

### 依存関係のインストールで `WinError 206`、ビルドで `Could not open output file ...cu.obj.d` が出る

どちらも Windows の 260 文字のパス長制限に当たっています。依存関係は venv を短いパスに置くか、Windows で長いパスのサポートを有効にしてください（ハマりどころ #29）。ビルドはビルド用ディレクトリを短いパスにしてください（ハマりどころ #24）。

## 上流への修正

このプロジェクトの開発中に上流で見つけた問題を修正し、PR を送りました（2026-10-14 時点）。

| 上流 | PR | 内容 | 状態 |
|---|---|---|---|
| [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) | [#3411](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3411) | FunASR の JSON がそのまま会話履歴に入る | マージ済み |
| | [#3413](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3413) | TTS の文分割が引用符の内側で文を切ってしまう | マージ済み |
| | [#3412](https://github.com/xinnan-tech/xiaozhi-esp32-server/pull/3412) | SenseVoice の日本語認識結果に単語ごとの空白が入る | マージ済み |
| [qwen3-tts.cpp](https://github.com/Danmoreng/qwen3-tts.cpp) | [#5](https://github.com/Danmoreng/qwen3-tts.cpp/pull/5) | モデル、ボイスプロンプト、参照音声のパスを UTF-8 で開く（中国語・日本語のディレクトリ名） | 返信待ち |
| | [#6](https://github.com/Danmoreng/qwen3-tts.cpp/pull/6) | C 境界での例外捕捉、構造体 ABI テーブルのエクスポート、C ブリッジライブラリ | 返信待ち |

ほかに 3 つのブランチを用意してあり、#5・#6 に反応があり次第提出します：Q/K/V の重複排除（GPU −488 MiB）、テキスト埋め込みテーブルの CPU 配置（GPU・メモリそれぞれ −594 MiB）、ICL プロンプト JSON の `\uXXXX` デコード。詳細：[docs/upstream.md](docs/upstream.md)。

## ドキュメント

以下のドキュメントは中国語です。

| ドキュメント | 内容 |
|---|---|
| [docs/how-it-works.md](docs/how-it-works.md) | 仕組み：VRAM 節約パッチ、GPU 交代利用のタイミングとパラメータ、文ごとの整音 |
| [docs/benchmarks.md](docs/benchmarks.md) | 実測データと測定方法 |
| [docs/architecture.md](docs/architecture.md) | 3 つの使い方、部品の依存関係、ポートとリソース、動作環境、制限 |
| [tts-service/README.md](tts-service/README.md) | TTS サービスの起動オプションと HTTP API |
| [native-tts/README.md](native-tts/README.md) | エンジンパッチのスイッチ、ビルド済みパッケージ、自前ビルド、モデル |
| [standalone/README.md](standalone/README.md) | スタンドアロンサーバー：ボードの接続、設定、リアルタイムダッシュボード |
| [server/README.md](server/README.md) | xiaozhi-esp32-server への組み込み |
| [docs/voice-reference.md](docs/voice-reference.md) | 参照音声の準備のしかた |
| [docs/pitfalls.md](docs/pitfalls.md) | ハマりどころ 32 項目 |
| [docs/standalone-tuning.md](docs/standalone-tuning.md) | スタンドアロンサーバーの調整記録 |
| [docs/upstream.md](docs/upstream.md) | 上流に送った PR、実験ブランチ |
| [docs/development.md](docs/development.md) | ディレクトリ構成、テスト（GPU・モデル不要）、CI |
| [bench/README.md](bench/README.md) | benchmark の再現 |

## 謝辞

[llama.cpp](https://github.com/ggml-org/llama.cpp)、[qwen3-tts.cpp](https://github.com/Danmoreng/qwen3-tts.cpp)、[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)、[Gemma](https://ai.google.dev/gemma)、[xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)、[xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)、[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)。

## ライセンス

[Apache-2.0](LICENSE)。帰属表示は [NOTICE](NOTICE) にあります。配布・改変する際は NOTICE を残し、変更したファイルにはその旨を明記してください。上流はそれぞれ独自のライセンスに従います。xiaozhi-esp32-server・qwen3-tts.cpp・ggml は MIT、Qwen3-TTS・Gemma 4 は Apache-2.0 です。このリポジトリはモデルと CUDA ランタイムを配布していません。他人の声を参照音声に使う場合は、事前に本人の同意を得てください。
