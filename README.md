# おんどく ボイス（研究用プロトタイプ）

生徒が家で教材を音読して録音し、学校で「自分の録音 → お手本」の順に聞く、音読練習の研究用Webアプリです（研究1：中学3年生 約140名）。お手本は条件によって、**自分の声で作ったAI音声**（AI自己音声条件）か、**教材のネイティブスピーカー音声**（ネイティブ条件）になります。

- 参加者はブラウザだけで使えます（Windows / Mac / Chromebook / iPad。インストール不要）。
- 音声の生成は**研究者が管理するMac（またはPC）の中だけ**で行い、外部のAIサービスには音声も文章も送りません。
- 生成には、オープンソースの **XTTS-v2**（`pip install` だけで導入できる音声合成モデル）を使います。

```
参加者のブラウザ                       研究者のMac（このアプリ）
 録音 / ファイル選択  ──HTTPS──▶  Webサーバ ─▶ 生成キュー ─▶ XTTS-v2
 生成音声の再生      ◀──────────  保存（data/ フォルダ）◀──┘
                                    管理画面 /admin
```

## 機能

| 生徒（`/`） | 研究者（`/admin`） |
| --- | --- |
| ID と あいことば でログイン | 参加者ID・あいことばの一括発行（クラス・条件つき。条件はクラス内で半々にランダム割り当て可）、CSVでの一括登録 |
| **家（録音の段階）**：教材の文ごとに録音 → 聞き直し → 保存、すべて保存したら「提出」 | 教材の文の登録と、文ごとのネイティブ音声のアップロード |
| 録音の長さ・音量をその場でチェック。マイクが使えない端末はファイルで提出 | クラスごとの段階の切り替え（録音 ／ 聴取 ／ 停止）と、提出状況・AI音声の準備状況の確認 |
| **聴取**：文ごとに ① 自分の録音 → ほかの音声の順で聞く（①を最後まで聞くと次が押せる）。聞ける音声は条件ごとに「自分の録音／AI自己音声（クローン）／標準AI音声／ネイティブ音声」から選べる | AI自己音声条件の生徒が提出すると、その録音を見本にAI音声を自動生成。試聴・再生成・公開前の承認（任意） |
| 「提出したらすぐ聞ける」をオンにすると、クラスの段階を切り替えなくても提出直後に聞ける。速さ 0.5〜2.0倍・くり返し（1・3・5回・ずっと）は研究者がオン・オフ可 | 練習後アンケートのURL設定（`{id}` に参加者IDが入る） |
| 生徒の画面には条件名を表示しない（お手本が1種類なら「お手本」とだけ表示） | 利用ログ・参加者一覧のCSV、生徒の録音のZIP出力。参加者単位のデータ完全削除 |

生成音声はアプリ内でのみ再生でき、ダウンロードボタンは表示しません。

## 1. 準備するもの

- 研究者用の Mac（Apple Silicon 推奨、メモリ 16GB 以上、空き容量 10GB 以上）
- Python 3.10〜3.13（[python.org](https://www.python.org/downloads/macos/) のインストーラで可）
- 参加者が自宅から使う場合は、HTTPS で公開する手段（後述）

## 2. まずAIなしで動かしてみる（5分）

ターミナルで次を実行します。

```bash
git clone <このリポジトリのURL> ondoku-voice
cd ondoku-voice
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
ONDOKU_ENGINE=dummy python -m app
```

- 管理画面: http://127.0.0.1:8000/admin （パスワードは起動時の表示、または `data/admin-password.txt`）
- 参加者画面: http://127.0.0.1:8000/

`dummy` エンジンは声の代わりに「ピポパ」という音を作るだけですが、録音 → 生成 → 再生の流れをすべて確認できます。

## 3. 音声生成AI（XTTS-v2）を入れる

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

**声の品質を上げるには**: 見本の録音が合計10秒以上あると安定します（研究1では、生徒が家で録音した全文が自動で見本になります）。静かな場所で、マイクに近づいてはっきり読むよう案内してください。XTTS-v2 が文の後に「I, I…」のような余分な音を付けることがあるため、アプリは生徒自身の音読の長さを目安に余分な部分を切り、長すぎる場合は最大3回まで作り直します。

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

**生成時間の見積もり**: `try_engine.py` で測った1文あたりの秒数 × AI自己音声条件の生徒数 × 教材の文の数 が、全員分を作り終えるまでの時間です（例：70名 × 10文 = 700件）。家での録音の締切は、この時間を見込んで学校での聴取日より前に設定してください。

### 別のエンジン（実験的）

`ONDOKU_ENGINE=chatterbox` で Resemble AI の Chatterbox Multilingual（MITライセンス、日英対応）に切り替えられます。torch のバージョンを固定するため、別の仮想環境に `pip install chatterbox-tts` してください。XTTS-v2 の日本語の質が合わない場合の比較用です。

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

## 5. 研究者の操作の流れ（研究1）

1. **教材**タブ：教材の文を登録（1行に1文）し、各文の「アップロード」からネイティブ音声（MP3・WAV等）を登録。
2. **参加者**タブ：クラスごとに人数・クラス名・条件（「ランダムに半々」など）を指定して発行 →「CSVを保存」。名簿のIDを使う場合は「CSVで一括登録」。**あいことばは発行時にしか表示されません。** 氏名との対応表はアプリの外で管理します。
3. **設定・出力**タブ：条件ごとに生徒が聞ける音声（研究1の既定：AI群＝自分の録音＋AI自己音声、ネイティブ群＝自分の録音＋ネイティブ音声）、標準AI音声の声、提出後すぐ聞けるか（研究1ではオフ）、速さ・くり返しの可否、AI音声の承認制、練習後アンケートのURLを設定。
4. **進捗・段階**タブ：対象クラスを「録音」にして、生徒に家で録音・提出してもらう。提出状況と「AI音声 準備完了」の数を確認し、未提出の生徒には声をかける。
5. AI自己音声条件の生徒が提出すると、AI音声の生成が自動で始まります。**AI音声**タブで試聴し、不自然なものは「再生成」。
6. 学校での授業の前に、そのクラスを「聴取」に切り替える。授業が終わったら「停止」に。
7. **設定・出力**タブから、利用ログ・参加者一覧（CSV）、生徒の録音（ZIP）を書き出して分析。研究終了時や同意撤回時は「参加者」→「データ削除」。

提出後に録り直しが必要になった生徒は、「参加者」タブの「提出を取り消す」で録音できる状態に戻せます（再提出するとAI音声はつくり直されます）。

## 6. データと運用上の注意

- 保存場所はすべて `data/`（`ONDOKU_DATA_DIR`）の中です: `ondoku.sqlite3`（参加者ID・教材・ログ）、`recordings/`（生徒の録音）、`clips/`（AI音声）、`native/`（ネイティブ音声）。氏名は保存しません。
- `data/` は Git に含まれません（`.gitignore`）。バックアップは暗号化した外付けディスクなど研究者の管理下に限り、iCloud などクラウド同期フォルダには置かないでください。
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
