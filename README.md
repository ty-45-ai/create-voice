# おんどく ボイス（研究用プロトタイプ）

生徒が家で教材を音読して録音し、そのあと **① 自分の声 → ② AI音声（クローン：自分の声で作ったAI音声） → ③ AI音声（標準：全員共通の既成の声）** の順に聞く、音読練習の研究用Webアプリです。生徒は全員、同じ3つの音声を聞きます。

- 参加者はブラウザだけで使えます（Windows / Mac / Chromebook / iPad。インストール不要）。
- 音声の生成は**管理者のMac（またはPC）の中だけ**で行い、外部のAIサービスには音声も文章も送りません。
- 生成には、PCの中だけで動く無料の音声AIアプリ **Voicebox**（クローンは Qwen3-TTS、標準AI音声は Kokoro）を使います。

> **Mac での導入手順**：Python・Git・Voicebox の入れ方から起動・更新までの全手順を [docs/mac-setup.md](docs/mac-setup.md) にまとめています。

```
参加者のブラウザ                       管理者のMac（このアプリ）
 録音 / ファイル選択  ──HTTPS──▶  Webサーバ ─▶ 生成キュー ─▶ Voicebox
 生成音声の再生      ◀──────────  保存（data/ フォルダ）◀──┘
                                    管理画面 /admin
```

## 機能

| 生徒（`/`） | 管理者（`/admin`） |
| --- | --- |
| ID と パスワード でログイン | 参加者ID・パスワードの一括発行（クラスつき）、CSVでの一括登録（パスワード指定可）。パスワードは管理画面でいつでも確認・変更・CSV保存できる |
| **録音**：教材の文ごとに録音 → 聞き直し → 保存、すべて保存したら「提出」 | 教材の登録（改行を含む長い文章も1つの練習に。空行で区切ると別々の練習）。登録するとAI音声（標準）を自動生成 |
| 録音の長さ・音量をその場でチェック。マイクが使えない端末はファイルで提出 | 「進捗」タブで、クラスごとに生徒一人ひとりの状態（未録音／録音中／音声生成中／聴取可能／生成エラー）を色分けで確認 |
| **聴取**（提出後、AI音声の完成次第に聴取可能）：練習ごとに ① 自分の声 → ② AI音声（クローン） → ③ AI音声（標準）の順で聞く（①を最後まで聞くと②③が押せる） | 生徒が提出すると、その録音を見本にAI音声（クローン）を自動生成。試聴・再生成・公開前の承認（任意） |
| 速さ 0.5〜2.0倍（1.0倍がバーの真ん中）・繰り返し（1・3・5・10回・連続）。使えるかどうかは管理者がオン・オフ可 | 練習後アンケートのURL設定（`{id}` に参加者IDが入る） |
| 各音声の状態（再生できます／生成中です／再生済み）を表示 | AI音声（標準）の声を英語・日本語の教材ごとに指定（空欄なら自動）。利用ログ・参加者一覧のCSV、生徒の録音のZIP出力。参加者単位のデータ完全削除 |

生成音声はアプリ内でのみ再生でき、ダウンロードボタンは表示しません。

## 1. 準備するもの

- 管理用の Mac（Apple Silicon 推奨、メモリ 16GB 以上、空き容量 10GB 以上）
- Python 3.10〜3.13（推奨 3.13。[python.org](https://www.python.org/downloads/) のインストーラで可）
- Git（Mac は `xcode-select --install`、Windows は https://git-scm.com/download/win ）
- [Voicebox](https://voicebox.sh)（手順3）
- 参加者が自宅から使う場合は、HTTPS で公開する手段（後述）

## 2. まずAIなしで動かしてみる（5分）

ターミナルで次を実行します。

```bash
git clone https://github.com/ty-45-ai/create-voice.git
cd create-voice
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
ONDOKU_ENGINE=dummy python -m app
```

- 管理画面: http://127.0.0.1:8000/admin （パスワードは起動時の表示、または `data/admin-password.txt`）
- 参加者画面: http://127.0.0.1:8000/

`dummy` エンジンは声の代わりに「ピポパ」という音を作るだけですが、録音 → 生成 → 再生の流れをすべて確認できます。

## 3. 音声生成AI：Voicebox を使う（おすすめ）

[Voicebox](https://github.com/jamiepine/voicebox)（MITライセンス）は、PCの中だけで動く音声AIのデスクトップアプリです。Windows / Mac のインストーラーで入れられ、NVIDIA 以外の GPU（Windows は DirectML、Mac は Apple シリコン）や CPU でも動きます。アプリは Voicebox の API（`http://127.0.0.1:17493`）を呼び出して、生徒の声のクローンと標準AI音声を作ります。

1. https://github.com/jamiepine/voicebox/releases/latest の「Assets」から Windows は `.msi`、Mac は `.dmg` を入れて起動する（Mac は https://voicebox.sh/download/mac-arm （Apple シリコン）/ https://voicebox.sh/download/mac-intel からも取得可）
2. Voicebox の画面で一度ボイスを作って生成し、Qwen3-TTS 0.6B（クローン用）と Kokoro（標準AI音声用）のモデルをダウンロードしておく（アプリからの初回生成時にも自動でダウンロードされますが、時間がかかります）
3. `.env` を `ONDOKU_ENGINE=voicebox` にして、アプリを起動する（Voicebox は起動したままにする）
4. AI音声（標準）の声は、空欄なら教材の言語を話せる声が自動で選ばれます（英語：`kokoro:af_heart`、日本語：Qwen CustomVoice の日本語話者など）。変えたい場合は管理画面「設定・出力」で英語・日本語ごとに指定します（候補は入力欄に表示されます）

- 生徒が提出すると、アプリが Voicebox に「ondoku-生徒ID-…」という名前のボイスを作り、教材の録音と読んだ文を見本として登録します。生成した音声は取り込んだあと Voicebox の履歴から消します。
- アプリで生徒のデータを削除すると、Voicebox 側のその生徒のボイスも削除されます。
- Voicebox のクラウド機能は既定で無効です。外部との通信はモデルのダウンロードのみです。
- 試験用サーバーでの自動テストで API とのやり取りを確認していますが、実際の Voicebox との接続は各自のPCで確認してください。

## 3'. 音声生成AI（XTTS-v2）を入れる（別の選択肢）

```bash
source .venv/bin/activate
pip install -r requirements-xtts.txt
```

1. XTTS-v2 のモデルは **Coqui Public Model License（非商用）** です。[ライセンス](https://coqui.ai/cpml)を確認し、研究利用で問題なければ `.env` の `COQUI_TOS_AGREED=1` にします。
2. `.env` の `ONDOKU_ENGINE=xtts` を確認します。
3. 動作確認（初回はモデル約1.8GBをダウンロードします）:

```bash
# 6秒以上の自分の声の WAV を用意して
python scripts/try_engine.py my_voice.wav "きょうは いい てんき です。"
python scripts/try_engine.py my_voice.wav "I like reading books." --lang en
# → try_engine_out.wav ができれば成功。表示される秒数が1文あたりの生成時間の目安です。
```

4. アプリを起動: `python -m app`
5. それまで `dummy` で作った音声が残っている場合は、管理画面「生成音声」→「全部つくり直す」で XTTS-v2 の声に作り直します。

**声の品質を上げるには**: 見本の録音が合計10秒以上あると安定します（生徒が録音した全文が自動で見本になります）。静かな場所で、マイクに近づいてはっきり読むよう案内してください。XTTS-v2 が文の後に「I, I…」のような余分な音を付けることがあるため、アプリは生徒自身の音読の長さを目安に余分な部分を切り、長すぎる場合は最大3回まで作り直します。

### Windows の場合

`source .venv/bin/activate` の代わりに、仮想環境の Python を直接呼びます（PowerShell、プロジェクトのフォルダで実行）。

```powershell
.venv\Scripts\python -m pip install -r requirements-xtts.txt
notepad .env      # COQUI_TOS_AGREED=1 と ONDOKU_ENGINE=xtts にして保存
.venv\Scripts\python scripts\try_engine.py data\recordings\P001\1.wav "I like reading books."
.venv\Scripts\python -m app
```

- 声のサンプルには、アプリで生徒が録音した音声（`data\recordings\<ID>\<文の番号>.wav`）をそのまま使えます。
- モデルは初回に `%LOCALAPPDATA%\tts` にダウンロードされます。
- プロジェクトは OneDrive の外に置いてください（録音がクラウドに同期されるのを防ぐため）。
- インストール中に「パスが長すぎる」というエラーが出た場合は、管理者 PowerShell で `New-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force` を実行して再起動してから、もう一度インストールしてください。

GPT-SoVITS のような専用WebUIや個別の学習は不要で、生徒が家で録音した教材の音読（合計数秒〜30秒）をそのまま声の見本にして生成します。Mac では CPU で動かします（XTTS-v2 は Apple の MPS では不安定なため）。

**生成時間の見積もり**: `try_engine.py` で測った1文あたりの秒数 × 生徒数 × 教材の文の数 が、全員分を作り終えるまでの時間です（例：70名 × 10文 = 700件）。録音の締切は、この時間を見込んで、生徒に聞かせたい日より前に設定してください。

### 別のエンジン（実験的）

`ONDOKU_ENGINE=chatterbox` で Resemble AI の Chatterbox Multilingual（MITライセンス、日英対応）に切り替えられます。torch のバージョンを固定するため、別の仮想環境に `pip install chatterbox-tts` してください。XTTS-v2 の日本語の質が合わない場合の比較用です。

## 起動と更新（ダブルクリック）

| ファイル | 内容 |
| --- | --- |
| `start-windows.bat` / `start-mac.command` | アプリを起動（初回は `.env` と Python の部品も自動で準備）し、管理画面をブラウザで開く |
| `update-windows.bat` / `update-mac.command` | GitHub から最新版を取り込む（アプリを止めてから実行） |

更新用ファイルは、このフォルダを Git で取得（`git clone`）した場合に使えます。ZIP で取得した場合は、Git で取得し直してください（`.env` と `data` は新しいフォルダにコピー）。

## 4. 参加者に使ってもらう

ブラウザのマイク機能は **HTTPS（または同じMac上の localhost）でしか使えません**。参加者の端末から使う方法は次のどちらかです。どちらを使うかは、所属機関の情報セキュリティ規程と倫理審査の記載に合わせてください。

**A. 自宅など学外から使う（トンネル）**

Mac から外向きに接続して HTTPS のURLを発行するサービスを使います。ルーターの設定は不要です。

```bash
# Cloudflare Tunnel の例（brew install cloudflared）
cloudflared tunnel --url http://127.0.0.1:8000
# Tailscale Funnel の例
tailscale funnel 8000
```

- 表示された `https://...` のURLを参加者に配ります。`.env` は `ONDOKU_SECURE_COOKIE=1` にします。
- 通信はトンネル事業者のサーバを経由します（AIサービスではありませんが、倫理審査の説明に含めてください）。お試し用の一時URL（trycloudflare.com）は起動のたびに変わるため、本番では名前付きトンネルを設定してください。

**B. 学校の中だけで使う（同じWi-Fi）**

1. [mkcert](https://github.com/FiloSottile/mkcert) で証明書を作り、`.env` の `ONDOKU_SSL_CERTFILE` / `ONDOKU_SSL_KEYFILE` に指定
2. `ONDOKU_HOST=0.0.0.0`、`ONDOKU_SECURE_COOKIE=1`
3. 参加者端末に mkcert のルート証明書を入れるか、マイクの代わりに「ファイルを選ぶ」で音声ファイルを送ってもらいます

## 5. 管理者の操作の流れ

1. **教材**タブ：教材の文章を登録（空行で区切ると別々の練習）。AI音声（標準）が自動で作られます。
2. **参加者**タブ：クラスごとに人数・クラス名を指定して発行 →「CSVを保存」。名簿のIDを使う場合は「CSVで一括登録」。パスワードは「参加者一覧」でいつでも確認・変更でき、「ID・パスワード一覧をCSVで保存」で配布用の一覧を出せます。 氏名との対応表はアプリの外で管理します。
3. **設定・出力**タブ：AI音声（標準）の声（英語・日本語ごと。空欄なら自動）、速さ・くり返しの可否、AI音声の承認制、練習後アンケートのURLを設定。
4. 生徒に録音・提出してもらう。**進捗**タブで生徒ごとの状態を確認し、未録音・録音中の生徒には声をかける。
5. 生徒が提出すると、AI音声（クローン）の生成が自動で始まり、完成次第、その生徒は聴取可能になります。**AI音声**タブで試聴し、不自然なものは「再生成」（公開前に確かめたいときは「設定・出力」で承認制に）。研究期間が終わった生徒は「参加者」タブの「停止」でログインできなくなります。
6. **設定・出力**タブから、利用ログ・参加者一覧（CSV）、生徒の録音（ZIP）を書き出して分析。研究終了時や同意撤回時は「参加者」→「データ削除」。お試し用の生徒などをまとめて消すときは、参加者一覧でチェックを入れて（「クラス単位で選択」も可）「選択した参加者を削除」。

提出後に録り直しが必要になった生徒は、「参加者」タブの「提出を取り消す」で録音できる状態に戻せます（再提出するとAI音声はつくり直されます）。

## 6. データと運用上の注意

- 保存場所はすべて `data/`（`ONDOKU_DATA_DIR`）の中です: `ondoku.sqlite3`（参加者ID・教材・ログ）、`recordings/`（生徒の録音）、`clips/`（AI音声（クローン））、`standard/`（AI音声（標準））。氏名は保存しません。
- `data/` は Git に含まれません（`.gitignore`）。バックアップは暗号化した外付けディスクなど管理者の管理下に限り、iCloud などクラウド同期フォルダには置かないでください。
- Mac の FileVault（ディスク暗号化）をオンにしてください。
- 実験期間中は Mac がスリープしないようにします: `caffeinate -dims python -m app`
- 管理者パスワードは `.env` の `ONDOKU_ADMIN_PASSWORD` で固定できます。自動生成した場合は、控えたあと `data/admin-password.txt` を削除してください。
- 同じIPからログイン失敗が続くと5分間ロックされます。

## 7. 未確認の点（プロトタイプとして）

- XTTS-v2 の組み込みは公開APIに沿って書いていますが、**この開発環境ではモデルをダウンロードできず、実際の生成は未確認です**。最初に `scripts/try_engine.py` で動作と品質を確認してください。
- 子どもの声でのクローン品質、日本語の声で英語を話させたときの自然さは、モデルの学習データ（主に大人の声）に依存します。パイロットで確認が必要です。
- 動作確認は Chromium（PC・スマホ幅）で行いました。Safari / iPad / Chromebook では実験前に確認してください。

## 開発者向け

```bash
pip install -r requirements-dev.txt
python -m pytest
```

構成: `app/main.py`（API）、`app/store.py`（SQLite とデータ規則）、`app/worker.py`（生成キュー）、`app/engines/`（音声エンジン）、`app/static/`（画面。ビルド不要の HTML/JS）。録音はブラウザ側で 24kHz モノラル WAV に変換して送るため、サーバに ffmpeg は不要です。
