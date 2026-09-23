import os
import re
import json
import subprocess
import zipfile
import plistlib
import requests
from io import BytesIO
from PIL import Image

REPO_OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", "FS13Fid")
REPO_NAME = os.environ.get("GITHUB_REPOSITORY", "FS13Fid/ipa-test").split("/")[-1]
BASE_URL = f"https://{REPO_OWNER.lower()}.github.io/{REPO_NAME}"

os.makedirs("manifests", exist_ok=True)
os.makedirs("icons", exist_ok=True)

def format_size(bytes_num):
    for unit in ['Б', 'КБ', 'МБ', 'ГБ']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} ГБ"

def download_with_curl(url, dest_path, token=None):
    cmd = ["curl", "-L", "-s", "--retry", "3", "--retry-delay", "2", "-o", dest_path]
    if token:
        cmd.extend(["-H", f"Authorization: token {token}"])
    cmd.append(url)
    res = subprocess.run(cmd)
    return res.returncode == 0 and os.path.exists(dest_path) and os.path.getsize(dest_path) > 1024

def fetch_apple_cdn_icon(bundle_id, item_id=None):
    """Ищет оригинальную HD-иконку в CDN Apple"""
    queries = []
    if item_id:
        queries.append(f"id={item_id}")
    queries.append(f"bundleId={bundle_id}")

    for q in queries:
        for country in ["ru", "us", "kz", "am"]:
            try:
                url = f"https://itunes.apple.com/lookup?{q}&country={country}"
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("resultCount", 0) > 0:
                        res = data["results"][0]
                        icon_url = res.get("artworkUrl512") or res.get("artworkUrl100")
                        if icon_url:
                            # Заменяем размер на максимальный 512x512 или 1024x1024
                            icon_url = re.sub(r'/\d+x\d+bb\.', '/512x512bb.', icon_url)
                            return icon_url
            except Exception:
                continue
    return ""

def extract_fallback_icon(z, plist_path, dest_path):
    """Если приложения нет в App Store (удалено), вытаскиваем графику из IPA"""
    # 1. Поиск iTunesArtwork
    for name in z.namelist():
        if name.lower() in ["itunesartwork", "itunesartwork.png", "itunesartwork@2x"]:
            try:
                data = z.read(name)
                with open(dest_path, "wb") as f:
                    f.write(data)
                return True
            except Exception:
                pass

    # 2. Поиск файлов иконок внутри .app
    app_dir = os.path.dirname(plist_path)
    candidates = [
        n for n in z.namelist() 
        if n.startswith(app_dir) and n.endswith(".png") and any(k in n.lower() for k in ["appicon", "icon", "60x60", "76x76", "120x120"])
    ]
    valid = [c for c in candidates if not any(b in c.lower() for b in ["20x20", "29x29", "notification", "small"])]
    if not valid:
        valid = candidates

    if valid:
        valid.sort(key=lambda x: z.getinfo(x).file_size, reverse=True)
        for cand in valid[:4]:
            try:
                raw_bytes = z.read(cand)
                # Пробуем сохранить и проверить размер
                with open(dest_path, "wb") as f:
                    f.write(raw_bytes)
                return True
            except Exception:
                continue
    return False

def inspect_ipa(ipa_path, download_url):
    print(f"\nОбработка: {os.path.basename(ipa_path)}")
    size_str = format_size(os.path.getsize(ipa_path))

    with zipfile.ZipFile(ipa_path, 'r') as z:
        plist_path = None
        for name in z.namelist():
            if re.match(r"^Payload/[^/]+\.app/Info\.plist$", name):
                plist_path = name
                break

        if not plist_path:
            return None

        with z.open(plist_path) as f:
            try:
                plist_data = plistlib.load(f)
            except Exception:
                return None

        # Ищем точный цифровой ID покупки Apple из iMazing
        item_id = None
        for meta_name in ["iTunesMetadata.plist", "Payload/iTunesMetadata.plist"]:
            if meta_name in z.namelist():
                try:
                    with z.open(meta_name) as mf:
                        m_data = plistlib.load(mf)
                        item_id = m_data.get("itemId") or m_data.get("playlistId")
                        if item_id:
                            print(f"-> Найден официальный Store ID: {item_id}")
                            break
                except Exception:
                    pass

        bundle_id = plist_data.get("CFBundleIdentifier", "unknown.bundle")
        version = plist_data.get("CFBundleShortVersionString", plist_data.get("CFBundleVersion", "1.0.0"))
        name = plist_data.get("CFBundleDisplayName", plist_data.get("CFBundleName", "App"))

        icon_filename = f"{bundle_id}.png"
        icon_dest = os.path.join("icons", icon_filename)

        # 1. Проверяем CDN Apple
        cdn_url = fetch_apple_cdn_icon(bundle_id, item_id)
        icon_saved = False

        if cdn_url:
            try:
                img_data = requests.get(cdn_url, timeout=6).content
                if len(img_data) > 1000:
                    with open(icon_dest, "wb") as f:
                        f.write(img_data)
                    icon_saved = True
                    print(f"-> Иконка успешно скачана из CDN Apple!")
            except Exception as e:
                print(f"Не удалось скачать с CDN: {e}")

        # 2. Если CDN не отдал — достаем из IPA
        if not icon_saved:
            icon_saved = extract_fallback_icon(z, plist_path, icon_dest)

        icon_url = f"{BASE_URL}/icons/{icon_filename}?v={version}" if icon_saved else ""

        # Manifest
        manifest_filename = f"{bundle_id}.plist"
        manifest_path = os.path.join("manifests", manifest_filename)
        manifest_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>items</key>
    <array>
        <dict>
            <key>assets</key>
            <array>
                <dict>
                    <key>kind</key>
                    <string>software-package</string>
                    <key>url</key>
                    <string>{download_url}</string>
                </dict>
            </array>
            <key>metadata</key>
            <dict>
                <key>bundle-identifier</key>
                <string>{bundle_id}</string>
                <key>bundle-version</key>
                <string>{version}</string>
                <key>kind</key>
                <string>software</string>
                <key>title</key>
                <string>{name}</string>
            </dict>
        </dict>
    </array>
</dict>
</plist>"""
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(manifest_content)

        return {
            "name": name,
            "version": version,
            "bundleId": bundle_id,
            "size": size_str,
            "icon": icon_url,
            "manifestUrl": f"{BASE_URL}/manifests/{manifest_filename}",
            "description": f"Приложение {name} выгружено из личной медиатеки."
        }

def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY", f"{REPO_OWNER}/{REPO_NAME}")
    headers = {"User-Agent": "Mozilla/5.0"}
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        resp = requests.get(f"https://api.github.com/repos/{repo}/releases", headers=headers, timeout=15)
        releases = resp.json() if resp.status_code == 200 else []
    except Exception as e:
        print(f"Ошибка получения релизов: {e}")
        releases = []

    apps_data = []
    for rel in releases:
        for asset in rel.get("assets", []):
            if asset.get("name", "").endswith(".ipa"):
                ipa_name = asset["name"]
                download_url = asset["browser_download_url"]
                local_path = f"/tmp/{ipa_name}"

                ok = download_with_curl(download_url, local_path, token)
                if not ok:
                    continue

                try:
                    info = inspect_ipa(local_path, download_url)
                    if info:
                        apps_data.append(info)
                except Exception as ex:
                    print(f"Ошибка при анализе {ipa_name}: {ex}")

                if os.path.exists(local_path):
                    os.remove(local_path)

    if apps_data:
        with open("apps.json", "w", encoding="utf-8") as f:
            json.dump(apps_data, f, ensure_ascii=False, indent=2)
        print(f"\nУСПЕХ! Автоматически обновлено {len(apps_data)} приложений.")

if __name__ == "__main__":
    main()
