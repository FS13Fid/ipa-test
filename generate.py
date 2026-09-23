import os
import re
import json
import struct
import zlib
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

def fix_cgbi_png(data):
    """Декодирует оптимизированный Apple CgBI PNG в стандартный формат."""
    try:
        # Пробуем открыть напрямую через Pillow
        img = Image.open(BytesIO(data))
        img.load()
        out = BytesIO()
        img.convert("RGBA").save(out, format="PNG")
        return out.getvalue()
    except Exception:
        pass

    try:
        # Ручной парсинг Apple CgBI чанка
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            return None

        pos = 8
        chunks = []
        is_cgbi = False

        while pos < len(data):
            length = struct.unpack('>I', data[pos:pos+4])[0]
            chunk_type = data[pos+4:pos+8]
            chunk_data = data[pos+8:pos+8+length]
            crc = data[pos+8+length:pos+12+length]
            pos += 12 + length

            if chunk_type == b'CgBI':
                is_cgbi = True
                continue
            chunks.append((chunk_type, chunk_data))

        if not is_cgbi:
            return data

        # Собираем декомпрессированный IDAT
        idat_acc = b""
        width, height = 0, 0
        for c_type, c_data in chunks:
            if c_type == b'IHDR':
                width, height = struct.unpack('>II', c_data[:8])
            elif c_type == b'IDAT':
                idat_acc += c_data

        decompressed = zlib.decompress(idat_acc, -15)
        # Apple CgBI меняет RGBA на BGRA, переставляем каналы обратно
        stride = width * 4 + 1
        raw_pixels = bytearray()
        for y in range(height):
            line = decompressed[y*stride : (y+1)*stride]
            filter_byte = line[0:1]
            pixels = line[1:]
            fixed_pixels = bytearray()
            for x in range(0, len(pixels), 4):
                b, g, r, a = pixels[x:x+4]
                fixed_pixels.extend([r, g, b, a])
            raw_pixels.extend(filter_byte + fixed_pixels)

        img = Image.frombytes("RGBA", (width, height), bytes(raw_pixels), "raw", "RGBA", stride, 1)
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception as e:
        print(f"Ошибка декодирования CgBI: {e}")
        return None

def fetch_itunes_icon(bundle_id):
    for country in ["ru", "us", "kz", "am"]:
        try:
            url = f"https://itunes.apple.com/lookup?bundleId={bundle_id}&country={country}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode())
                if data.get("resultCount", 0) > 0:
                    item = data["results"][0]
                    return item.get("artworkUrl512", item.get("artworkUrl100", ""))
        except Exception:
            pass
    return ""

def inspect_ipa(ipa_path, download_url):
    print(f"Парсинг {ipa_path}...")
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
            except Exception as e:
                print(f"Ошибка plist: {e}")
                return None

        bundle_id = plist_data.get("CFBundleIdentifier", "unknown.bundle")
        version = plist_data.get("CFBundleShortVersionString", plist_data.get("CFBundleVersion", "1.0.0"))
        name = plist_data.get("CFBundleDisplayName", plist_data.get("CFBundleName", "App"))

        # 1. Сначала пробуем iTunes API
        icon_url = fetch_itunes_icon(bundle_id)

        # 2. Если в iTunes нет (удаленное приложение), декодируем оригинальную иконку из IPA
        if not icon_url:
            icon_filename = f"{bundle_id}.png"
            icon_dest = os.path.join("icons", icon_filename)
            app_dir = os.path.dirname(plist_path)

            icon_candidates = [n for n in z.namelist() if n.startswith(app_dir) and "AppIcon" in n and n.endswith(".png")]
            if not icon_candidates:
                icon_candidates = [n for n in z.namelist() if n.startswith(app_dir) and n.endswith(".png") and "icon" in n.lower()]

            if icon_candidates:
                icon_candidates.sort(key=lambda x: z.getinfo(x).file_size, reverse=True)
                for cand in icon_candidates[:3]:
                    try:
                        raw_data = z.read(cand)
                        cleaned = fix_cgbi_png(raw_data)
                        if cleaned:
                            with open(icon_dest, "wb") as out_f:
                                out_f.write(cleaned)
                            icon_url = f"{BASE_URL}/icons/{icon_filename}?v={version}"
                            break
                    except Exception as e:
                        print(f"Ошибка обработки {cand}: {e}")

        if not icon_url:
            icon_url = "https://img.icons8.com/ios-filled/100/3478F6/macbook-app.png"

        # Создаем manifest.plist
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
            "description": f"Приложение {name} выгружено из личного App Store."
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

    print(f"Готово! Обработано {len(apps_data)} приложений.")

if __name__ == "__main__":
    main()
