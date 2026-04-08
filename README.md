# Minecraft Mod リスト生成ツール (gen.py)

Minecraftのmodフォルダを再帰的にスキャンし、各ディレクトリに存在するMod(`.jar`/`.zip`)の情報を収集してリスト化するPythonスクリプトです。

## 概要

指定したディレクトリ配下をスキャンし、Modファイルが直接含まれる各ディレクトリに`list.toml`を生成します。また、プロジェクトルートには全ての情報をまとめた`mods_index.yaml`を生成します。

ディレクトリ名から自動的にカテゴリ(「有効」「削除済み」「オプション」など)を分類し、各ModファイルのSHA-256ハッシュやメタデータ(Mod名、バージョン、URLなど)を抽出します。

## 特徴

- **自動カテゴリ分類**: フォルダ名をもとに「active」「deleted」「update」「bug」「ysm」「resourcepacks」「shaderpacks」「optional」などに自動分類。
- **メタデータ抽出**: Forge/NeoForge(`mods.toml`)、Fabric/Quilt(`fabric.mod.json`)、Legacy Forge(`mcmod.info`)などの形式からMod名やバージョン情報を自動的に取得。
- **一括管理**: 各ディレクトリに`list.toml`を配置しつつ、ルートには全体のインデックスとして`mods_index.yaml`を生成。
- **Git連携機能**: 生成・更新されたファイルを自動的にGitにコミットする機能を内蔵。

## 必要要件

- Python 3.11以上推奨(Python 3.11未満の場合は`tomli`パッケージが必要)
- 依存パッケージ:
  - `tomli_w`
  - `pyyaml`
  - `rich`
  - `tomli`(Python 3.11未満の場合)

## インストール

必要なパッケージをインストールします。

```bash
pip install tomli_w pyyaml rich
# Python 3.11未満の場合は以下のコマンドも実行してください
pip install tomli
```

## 使い方

以下のコマンドでスクリプトを実行します。

```bash
python gen.py [オプション]
```

### オプション

- `--path DIR`: スキャン対象のディレクトリのルートパスを指定します。(デフォルト: カレントディレクトリ`.`)
- `--no-git`: Gitへの自動コミットをスキップします。
- `--subdir-limit N`: サブディレクトリの数が`N`を超えるディレクトリはスキャンを後回しにし、ユーザーに確認を求めます。(デフォルト: 10、0で無効化)
- `--verbose`: スキャン中の詳細なログを表示します。
- `-h`, `--help`: ヘルプメッセージを表示します。

### 実行例

対象ディレクトリを指定して詳細ログ付きで実行する場合:

```bash
python gen.py --path /path/to/minecraft/mods --verbose
```

Gitへの自動コミットを行わない場合:

```bash
python gen.py --no-git
```

## 除外ディレクトリ

`.gitignore`に記載されているディレクトリや、以下のディレクトリはデフォルトでスキャン対象から除外されます。

- `.git`
- `__pycache__`
- `.venv`
- `.gemini`

## カテゴリ分類ルール

ディレクトリ名によって、以下のルールでカテゴリが自動判定されます。

- **削除済み**: `deleted`、`delete`、`削除`
- **アップデート待ち**: `update`、`updates`、`アプデ無し`
- **バグあり**: `bug`、`bugs`、`バグ`、`バグ？`
- **YSM**: `ysm`
- **リソースパック**: `resourcepack(s)`、`リソースパック`、`必須リソースパック`
- **シェーダーパック**: `shaderpack(s)`、`shader(s)`、`shader_bak`など
- **有効**: ルート直下、または`使用中`、`有効`
- **オプション**: 上記以外、または`オプション`

## ライセンス

このスクリプトは [zlib License](LICENSE) のもとで公開されています。
