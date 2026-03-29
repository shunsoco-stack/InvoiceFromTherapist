# Lightsail コンテナサービスへのデプロイ

## 前提

- AWS CLI が入り、`aws configure` で認証済み
- Lightsail に **コンテナサービス** が既にある（またはこれから作成）
- コンテナの公開ポートと、Lightsail が期待する **コンテナ内ポート**（例: `5000`）を一致させる

## 環境変数（Lightsail コンソールで設定）

| 名前 | 説明 |
|------|------|
| `SALON_SECRET_KEY` | **必須**: ランダムな長い文字列（セッション等） |
| `SALON_DB_PATH` | 任意。未設定時は `/app/data/salon.sqlite3`（`data` をボリュームにマウント推奨） |
| `SALON_LOGO_FILENAME` / `SALON_LOGO_ALT` | 任意（ロゴ） |
| `PORT` | 通常は未設定でよい（既定 5000）。プラットフォームが上書きする場合に使用 |

## データの永続化

SQLite を消さないため、コンテナサービスで **`/app/data` を永続ストレージにマウント**するか、`SALON_DB_PATH` でマウント先パスを指定してください。

## 手順（CLI の例）

リージョン・サービス名は自分の環境に合わせて置き換えてください。

```bash
# 1. イメージをビルド
docker build -t invoice-salon:latest .

# 2. Lightsail レジストリへプッシュ（表示される docker push コマンドをそのまま実行）
aws lightsail push-container-image \
  --region ap-northeast-1 \
  --service-name your-container-service-name \
  --label invoice-salon \
  --image invoice-salon:latest
```

`push-container-image` の出力に **`docker push ...`** が表示されるので、それを実行します。

## デプロイの更新

1. Lightsail コンソール → **コンテナ** → 対象サービス → **デプロイを作成**
2. イメージとして、プッシュした **`invoice-salon`**（または指定ラベル）の **最新タグ** を選択
3. 環境変数・ストレージマウントを確認してデプロイ

または Lightsail API / CLI で新デプロイを作成します（コンソールが最も分かりやすいです）。

## ローカル確認

```bash
docker compose up --build
```

ブラウザで http://localhost:5000 を開きます。DB はホストの `./data` に保存されます。
