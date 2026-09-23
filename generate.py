import os
import re
import json
import zipfile
import plistlib
import urllib.request
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

def fetch_by_item_id(item_id):
    """Точный запрос к Apple по уникальному цифровому ID покупки из iMazing"""
    if not item_id:
        return ""
    for country in ["ru", "us", "kz"]:
        try:
            url = f"https://itunes.apple.com/lookup?id={item_id}&country={country}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                if data.get("resultCount", 0) > 0:
                    res = data["results"][0]
                    return res.get("artworkUrl512", res.get("artworkUrl100", ""))
        except Exception:
            pass
    return ""

def extract_icon_from_ipa(z, plist_path, dest_path):
    # 1. Поиск iTunesArtwork в корне архива
    for name in z.namelist():
        if name.lower() in ["itunesartwork", "itunesartwork.png", "itunesartwork@2x"]:
            try:
                raw_data = z.read(name)
                img = Image.open(BytesIO(raw_data))
                img.convert("RGBA").save(dest_path, format="PNG")
                return True
            except Exception:
                with open(dest_path, "wb") as f:
                    f.write(raw_data)
                return True

    # 2. Поиск любых файлов иконок внутри Payload
    app_dir = os.path.dirname(plist_path)
    candidates = [
        n for n in z.namelist() 
        if n.startswith(app_dir) and n.endswith(".png") and any(k in n.lower() for k in ["appicon", "icon", "60x60", "76x76"])
    ]

    # Исключаем системные мелочи
    valid = [c for c in candidates if not any(b in c.lower() for b in ["20x20", "29x29", "notification", "small"])]
    if not valid:
        valid = candidates

    if valid:
        valid.sort(key=lambda x: z.getinfo(x).file_size, reverse=True)
        for cand in valid[:4]:
            try:
                raw_data = z.read(cand)
                try:
                    img = Image.open(BytesIO(raw_data))
                    img.convert("RGBA").save(dest_path, format="PNG")
                    return True
                except Exception:
                    # Сохраняем сырой байтстрим
                    with open(dest_path, "wb") as f:
                        f.write(raw_data)
                    return True
            except Exception:
                continue

    return False

def inspect_ipa(ipa_path, download_url):
    print(f"\n--- Анализ {ipa_path} ---")
    size_str = format_size(os.path.getsize(ipa_path))

    with zipfile.ZipFile(ipa_path, 'r') as z:
        plist_path = None
        for name in z.namelist():
            if re.match(r"^Payload/[^/]+\.app/Info\.plist$", name):
                plist_path = name
                break

        if not plist_path:
            return None

        # Читаем Info.plist
        with z.open(plist_path) as f:
            try:
                plist_data = plistlib.load(f)
            except Exception as e:
                print(f"Ошибка Info.plist: {e}")
                return None

        # Проверяем iTunesMetadata.plist от iMazing
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

        # 1. Сначала пробуем вытащить родную иконку прямо из архива
        has_local = extract_icon_from_ipa(z, plist_path, icon_dest)
        
        icon_url = ""
        if has_local:
            icon_url = f"{BASE_URL}/icons/{icon_filename}?v={version}"
        elif item_id:
            # 2. Если внутри Assets.car — берем оригинальную обложку по точному itemId
            store_icon = fetch_by_item_id(item_id)
            if store_icon:
                icon_url = store_icon

        if not icon_url:
            # 3. Запасной fallback по точному Bundle ID (без текстового поиска)
            for country in ["ru", "us"]:
                try:
                    u = f"https://itunes.apple.com/lookup?bundleId={bundle_id}&country={country}"
                    r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(r, timeout=3) as resp:
                        d = json.loads(resp.read().decode())
                        if d.get("resultCount", 0) > 0:
                            icon_url = d["results"][0].get("artworkUrl512", "")
                            break
                except Exception:
                    pass

        if not icon_url:
            icon_url = f"{BASE_URL}/icons/{icon_filename}"

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
    headers = {"User-Agent": "Python-IPA-Builder"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            releases = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Ошибка: {e}")
        releases = []

    apps_data = []
    for rel in releases:
        for asset in rel.get("assets", []):
            if asset.get("name", "").endswith(".ipa"):
                ipa_name = asset["name"]
                download_url = asset["browser_download_url"]
                local_path = f"/tmp/{ipa_name}"

                d_req = urllib.request.Request(download_url, headers=headers)
                with urllib.request.urlopen(d_req) as src, open(local_path, "wb") as dst:
                    dst.write(src.read())

                info = inspect_ipa(local_path, download_url)
                if info:
                    apps_data.append(info)

                if os.path.exists(local_path):
                    os.remove(local_path)

    with open("apps.json", "w", encoding="utf-8") as f:
        json.dump(apps_data, f, ensure_ascii=False, indent=2)

    print(f"\nГотово! Обработано {len(apps_data)} приложений.")

if __name__ == "__main__":
    main()
