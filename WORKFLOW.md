# 修正〜GitHub〜AWS 反映の手順

ローカルは Windows（`InvoiceFromTherapist_v2`）、本番は Lightsail の Ubuntu（`~/InvoiceFromTherapist`）、systemd の `invoice.service` で動かしている想定です。パス・ブランチ・IP は環境に合わせて変えてください。

## 全体の流れ

1. コードを編集する（エディタ）
2. GitHub に上げる（バッチ **または** `git` コマンド）
3. サーバーに反映する（バッチ **または** SSH で手動）

---

## A. GitHub へ上げる

### バッチを使う

`scripts\push-to-github.bat` をダブルクリック（またはコマンドプロンプトから実行）。

- コミットメッセージを聞かれるので英語か日本語で入力して Enter
- `git add -A` → `commit` → `origin` の **現在のブランチ** へ `push`

### 手動コマンド（プロジェクトのルートで）

```bat
cd C:\Users\shunk\App\InvoiceFromTherapist_v2
git add -A
git status
git commit -m "説明が入るメッセージ"
git push origin HEAD
```

`main` だけ運用する場合は `git push origin main` に読み替え。

---

## B. AWS（Lightsail）へ反映する

### 事前準備（初回だけ）

1. `scripts\deploy-local.example.bat` をコピーして `scripts\deploy-local.bat` にリネーム
2. `deploy-local.bat` を開き、**自分の環境**に合わせて編集  
   - `LIGHTSAIL_PEM` … `.pem` のフルパス  
   - `LIGHTSAIL_USER` … 多くは `ubuntu`  
   - `LIGHTSAIL_HOST` … パブリック IP または DNS  
   - `GIT_BRANCH` … サーバーで `git pull` するブランチ名  
   - `REMOTE_APP_DIR` … サーバー上のアプリの**フルパス**（例 `/home/ubuntu/InvoiceFromTherapist`）

`deploy-local.bat` は **個人のパスが入るため Git に含めません**（`.gitignore` 済み）。

### バッチを使う

`scripts\deploy-aws.bat` を実行。

- SSH でサーバーに接続し、指定ディレクトリで `git pull` → `venv` 有効化 → `pip install` → `invoice.service` 再起動まで行います
- **OpenSSH クライアント**（Windows 10/11 に標準）が使える必要があります

### 手動（SSH 先で）

```bash
cd ~/InvoiceFromTherapist
git fetch origin
git pull origin <ブランチ名>

source venv/bin/activate
pip install -r requirements.txt

sudo systemctl restart invoice.service
sudo systemctl status invoice.service
```

PC から入る例:

```bat
ssh -i "C:\path\to\LightsailDefaultKey-ap-northeast-1.pem" ubuntu@35.75.205.230
```

---

## C. トップ画面の「バージョン」表示について

表示は **サーバーのその日の日付**（`date.today()`）です。デプロイ直後に日付が想定どおりか見て、反映を確認できます。

---

## トラブル時

- **SSH で Permission denied** … `.pem` のパス、`User@Host`、Lightsail のキー割り当てを確認
- **`git pull` でコンフリクト** … サーバー上で解消するか、一度バックアップしてからやり直し
- **サービスが起動しない** … `sudo journalctl -u invoice.service -n 80 --no-pager`
