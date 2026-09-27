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
os.makedirs("custom-icons", exist_ok=True)

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

def save_clean_png(raw_bytes, dest_path):
    try:
        img = Image.open(BytesIO(raw_bytes))
        img.load()
        img.convert("RGBA").save(dest_path, "PNG")
        return True
    except Exception:
        try:
            with open(dest_path, "wb") as f:
                f.write(raw_bytes)
            return True
        except Exception:
            return False

def extract_native_icon(z, plist_data, plist_path, dest_path):
    icon_names = []
    icons_dict = plist_data.get("CFBundleIcons", {}).get("CFBundlePrimaryIcon", {})
    if "CFBundleIconFiles" in icons_dict:
        icon_names.extend(icons_dict["CFBundleIconFiles"])
    if "CFBundleIconName" in icons_dict:
        icon_names.append(icons_dict["CFBundleIconName"])
    if "CFBundleIconFile" in plist_data:
        icon_names.append(plist_data["CFBundleIconFile"])
    if "CFBundleIconFiles" in plist_data:
        icon_names.extend(plist_data["CFBundleIconFiles"])

    app_dir = os.path.dirname(plist_path)
    for iname in reversed(icon_names):
        clean_name = iname.replace(".png", "")
        matches = [
            n for n in z.namelist() 
            if n.startswith(app_dir) and clean_name.lower() in n.lower() and n.endswith(".png")
        ]
        if matches:
            matches.sort(key=lambda x: z.getinfo(x).file_size, reverse=True)
            for m in matches:
                try:
                    if save_clean_png(z.read(m), dest_path):
                        return True
                except Exception:
                    continue

    for name in z.namelist():
        if name.lower() in ["itunesartwork", "itunesartwork.png", "itunesartwork@2x"]:
            try:
                if save_clean_png(z.read(name), dest_path):
                    return True
            except Exception:
                pass

    candidates = [
        n for n in z.namelist() 
        if n.startswith(app_dir) and n.endswith(".png") and any(k in n.lower() for k in ["appicon", "icon", "60x60", "76x76", "1024"])
    ]
    valid = [c for c in candidates if not any(b in c.lower() for b in ["20x20", "29x29", "notification", "small", "badge"])]
    if not valid:
        valid = candidates

    if valid:
        valid.sort(key=lambda x: z.getinfo(x).file_size, reverse=True)
        for cand in valid[:4]:
            try:
                if save_clean_png(z.read(cand), dest_path):
                    return True
            except Exception:
                continue

    return False

def inspect_ipa(ipa_path, download_url):
    print(f"\n--- Обработка {os.path.basename(ipa_path)} ---")
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

        # Считываем Apple ID из iTunesMetadata.plist
        account_email = "Неизвестный аккаунт"
        for meta_name in ["iTunesMetadata.plist", "Payload/iTunesMetadata.plist"]:
            if meta_name in z.namelist():
                try:
                    with z.open(meta_name) as mf:
                        m_data = plistlib.load(mf)
                        found_acc = m_data.get("apple-id") or m_data.get("userName")
                        if found_acc:
                            account_email = str(found_acc).strip()
                            print(f"-> Обнаружен Apple ID: {account_email}")
                            break
                except Exception:
                    pass

        bundle_id = plist_data.get("CFBundleIdentifier", "unknown.bundle")
        version = plist_data.get("CFBundleShortVersionString", plist_data.get("CFBundleVersion", "1.0.0"))
        name = plist_data.get("CFBundleDisplayName", plist_data.get("CFBundleName", "App"))

        icon_filename = f"{bundle_id}.png"
        icon_dest = os.path.join("icons", icon_filename)
        custom_icon_path = os.path.join("custom-icons", icon_filename)

        if os.path.exists(custom_icon_path):
            with open(custom_icon_path, "rb") as src, open(icon_dest, "wb") as dst:
                dst.write(src.read())
            timestamp = int(os.path.getmtime(custom_icon_path))
            icon_url = f"{BASE_URL}/icons/{icon_filename}?t={timestamp}"
        else:
            has_icon = extract_native_icon(z, plist_data, plist_path, icon_dest)
            if has_icon:
                icon_url = f"{BASE_URL}/icons/{icon_filename}?v={version}"
            else:
                icon_url = f"{BASE_URL}/icons/{icon_filename}"

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
            "account": account_email,
            "size": size_str,
            "icon": icon_url,
            "manifestUrl": f"{BASE_URL}/manifests/{manifest_filename}",
            "description": f"Приложение {name} выгружено из учетной записи {account_email}."
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

                print(f"\nЗагрузка {ipa_name}...")
                ok = download_with_curl(download_url, local_path, token)
                if not ok:
                    continue

                try:
                    info = inspect_ipa(local_path, download_url)
                    if info:
                        apps_data.append(info)
                except Exception as ex:
                    print(f"Ошибка анализа {ipa_name}: {ex}")

                if os.path.exists(local_path):
                    os.remove(local_path)

    if apps_data:
        with open("apps.json", "w", encoding="utf-8") as f:
            json.dump(apps_data, f, ensure_ascii=False, indent=2)
        print(f"\nУспех! Каталог обновлен. Всего: {len(apps_data)}")

if __name__ == "__main__":
    main()
