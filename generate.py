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

def extract_icon_from_ipa(z, plist_path, dest_path):
    """Достает родную иконку прямо из IPA-файла iMazing"""
    # 1. Приоритет №1: iTunesArtwork (родная HD-иконка, которую кладет сам iMazing/Apple в корень IPA)
    for name in z.namelist():
        if name.lower() in ["itunesartwork", "itunesartwork.png", "itunesartwork@2x"]:
            try:
                raw_data = z.read(name)
                img = Image.open(BytesIO(raw_data))
                img.convert("RGBA").save(dest_path, format="PNG")
                print(f"-> Найдена родная iTunesArtwork: {name}")
                return True
            except Exception as e:
                print(f"Ошибка чтения {name}: {e}")

    # 2. Приоритет №2: Настоящие иконки AppIcon из папки .app
    app_dir = os.path.dirname(plist_path)
    
    # Ищем файлы с AppIcon в названии
    icon_files = [n for n in z.namelist() if n.startswith(app_dir) and "appicon" in n.lower() and n.endswith(".png")]
    
    # Фильтруем системные ошметки (строки состояния, уведомления 20x20 и т.д.)
    valid_icons = []
    for f in icon_files:
        low = f.lower()
        if any(bad in low for bad in ["20x20", "29x29", "40x40", "small", "badge"]):
            continue
        valid_icons.append(f)

    if not valid_icons:
        valid_icons = icon_files

    if valid_icons:
        # Сортируем по весу (самая большая — основная иконка для экрана)
        valid_icons.sort(key=lambda x: z.getinfo(x).file_size, reverse=True)
        for cand in valid_icons:
            try:
                raw_data = z.read(cand)
                img = Image.open(BytesIO(raw_data))
                img.convert("RGBA").save(dest_path, format="PNG")
                print(f"-> Извлечена иконка из приложения: {cand}")
                return True
            except Exception:
                # Если сырой Apple PNG (CgBI) не открылся стандартно, сохраняем как есть
                with open(dest_path, "wb") as out_f:
                    out_f.write(raw_data)
                return True

    return False

def inspect_ipa(ipa_path, download_url):
    print(f"\n=== Обработка {ipa_path} ===")
    size_str = format_size(os.path.getsize(ipa_path))

    with zipfile.ZipFile(ipa_path, 'r') as z:
        plist_path = None
        for name in z.namelist():
            if re.match(r"^Payload/[^/]+\.app/Info\.plist$", name):
                plist_path = name
                break

        if not plist_path:
            print("Info.plist не найден!")
            return None

        with z.open(plist_path) as f:
            try:
                plist_data = plistlib.load(f)
            except Exception as e:
                print(f"Ошибка plist: {e}")
                return None

        bundle_id = plist_data.get("CFBundleIdentifier", "unknown.bundle")
        version = plist_data.get("CFBundleShortVersionString", plist_data.get("CFBundleVersion", "1.0.0"))
        name = plist_data.get("CFBundleDisplayName", plist_data.get("CFBundleName", "App"))

        # Вытаскиваем РОДНУЮ иконку из IPA
        icon_filename = f"{bundle_id}.png"
        icon_dest = os.path.join("icons", icon_filename)
        
        has_icon = extract_icon_from_ipa(z, plist_path, icon_dest)
        
        if has_icon:
            icon_url = f"{BASE_URL}/icons/{icon_filename}?v={version}"
        else:
            icon_url = "https://img.icons8.com/ios-filled/100/3478F6/macbook-app.png"

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
        print(f"Ошибка релизов: {e}")
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

    print(f"\nВсе готово! Родные иконки извлечены для {len(apps_data)} приложений.")

if __name__ == "__main__":
    main()
