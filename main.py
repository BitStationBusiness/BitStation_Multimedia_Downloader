# (archivo completo; handler no bloqueante, ciclo de vida manual, límite 45MB, topics OK,
# playlists bloqueadas en bot, Telegram baja a temp y elimina, compatibilidad ampliada yt-dlp,
# auto-check de actualización, ACL WL/BL exclusivas, specs bonitas, guardia de tamaño 45MB, updater ZIP)

import sys
import subprocess
import uuid
import shutil
import threading
from datetime import datetime
from typing import Optional

from packaging.version import parse as parse_version
import telegram
from telegram.ext import Application, MessageHandler, filters

import re
import os
import yt_dlp
import imageio_ffmpeg
import ctypes
import requests
import asyncio
import json

from PyQt6.QtCore import Qt, QSize, QThread, QObject, pyqtSignal, QSettings, QTimer
from PyQt6.QtGui import QIcon, QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QDialog, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QCheckBox, QComboBox, QStyle,
    QProgressBar, QFileDialog, QTabWidget, QInputDialog
)

import stat

# ------------------------------ Utilidades ------------------------------

def _ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

def _safe_rmtree(path, retries=10, delay=0.2):
    import time as _t
    def onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
        except Exception:
            pass
        try:
            func(p)
        except Exception:
            pass

    for _ in range(retries):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, onerror=onerror)
            return True
        except (PermissionError, OSError):
            _t.sleep(delay)
    return not os.path.isdir(path)

def _safe_move(src_path, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    base = os.path.basename(src_path)
    name, ext = os.path.splitext(base)
    candidate = os.path.join(dst_dir, base)
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dst_dir, f"{name} ({n}){ext}")
        n += 1
    return shutil.move(src_path, candidate)

def get_current_version():
    try:
        with open("version.txt", "r") as f:
            version = f.read().strip()
            return version or "0.0"
    except FileNotFoundError:
        with open("version.txt", "w") as f:
            f.write("0.0")
        return "0.0"

APP_VERSION = get_current_version()
GITHUB_USER = "BitStationBusiness"
GITHUB_REPO = "BitStation_Multimedia_Downloader"
URL_REGEX = r'https?://[^\s/$.?#].[^\s]*'
TEMP_DOWNLOADS_DIR = os.path.join(os.getcwd(), "temp_downloads")
TELEGRAM_SIZE_LIMIT = 45 * 1024 * 1024  # 45 MB
AUTOSTART_REG_NAME = "BitStation_TelegramBot_AutoStart"  # NUEVO (no se usa con VBS, se mantiene para compat)
AUTOSTART_VBS_NAME = "BitStation_TelegramBot_AutoStart.vbs"  # NUEVO

# --- Compatibilidad y headers ---
COMMON_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
COMMON_HEADERS = {
    "User-Agent": COMMON_UA,
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Sin forzar 480p
EXTRACTOR_ARGS = {
    "facebook": {"locale": ["es_ES"]},
    "tiktok": {"webpage_url": ["1"]},
}

# Mejor combinación nativa
FORMAT_FALLBACK = "bv*+ba/b"

def base_ytdlp_opts(ffmpeg_path: str):
    return {
        "ffmpeg_location": ffmpeg_path,
        "http_headers": COMMON_HEADERS,
        "extractor_args": EXTRACTOR_ARGS,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "nocheckcertificate": False,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "noprogress": True,
        "geo_bypass": True,
    }

# --- Actualización en segundo plano de yt-dlp ---
def maybe_update_ytdlp_async():
    def _worker():
        try:
            print(f"[{_ts()}] [YTDLP] Comprobando actualización de yt-dlp (en segundo plano)...")
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creation_flags, check=False, encoding="utf-8"
            )
            print(f"[{_ts()}] [YTDLP] Verificación/actualización completada.")
        except Exception as e:
            print(f"[{_ts()}] [YTDLP] No se pudo actualizar automáticamente: {e}")
    threading.Thread(target=_worker, daemon=True).start()

class DownloadPausedException(Exception):
    pass

def get_system_info():
    info = {"cpu": "No detectado", "ram": "No detectada", "gpu": "No detectada", "cuda": "None"}
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        cpu_cmd = ['powershell', '-NoProfile', '-Command',
                   "Get-CimInstance -ClassName Win32_Processor | Select-Object -ExpandProperty Name"]
        cpu_result = subprocess.run(cpu_cmd, capture_output=True, text=True,
                                    creationflags=creation_flags, check=True, encoding='utf-8')
        info["cpu"] = cpu_result.stdout.strip()

        ram_cmd = ['powershell', '-NoProfile', '-Command',
                   "$mem = Get-CimInstance -ClassName Win32_ComputerSystem; [math]::Round($mem.TotalPhysicalMemory / 1GB, 0)"]
        ram_result = subprocess.run(ram_cmd, capture_output=True, text=True,
                                    creationflags=creation_flags, check=True, encoding='utf-8')
        info["ram"] = f"{ram_result.stdout.strip()} GB"

        nvidia_mem_gb = None
        try:
            nvidia_smi_cmd = ['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader,nounits']
            nvidia_result = subprocess.run(nvidia_smi_cmd, capture_output=True, text=True,
                                           creationflags=creation_flags, check=True, encoding='utf-8')
            nvidia_mem_mib = int(nvidia_result.stdout.strip().splitlines()[0])
            nvidia_mem_gb = round(nvidia_mem_mib / 1024)
        except Exception:
            nvidia_mem_gb = None

        gpu_list = []
        ps_command = ("Get-CimInstance -ClassName Win32_VideoController | "
                      "ForEach-Object { $_.Name + '|||' + $_.AdapterRAM }")
        ps_result = subprocess.run(['powershell', '-NoProfile', '-Command', ps_command],
                                   capture_output=True, text=True, creationflags=creation_flags, check=True, encoding='utf-8')
        nvidia_processed = False
        for line in ps_result.stdout.strip().splitlines():
            if '|||' not in line:
                continue
            name, ram_bytes_str = line.split('|||', 1)
            name = name.strip()
            if 'Microsoft' in name or 'Virtual' in name or 'MrIdd' in name:
                continue
            if 'NVIDIA' in name and nvidia_mem_gb is not None and not nvidia_processed:
                gpu_list.append(f"{name} ({nvidia_mem_gb}GB)")
                nvidia_processed = True
            else:
                try:
                    ram_bytes = int(ram_bytes_str)
                    if ram_bytes > 0:
                        ram_mb = ram_bytes / (1024 * 1024)
                        if ram_mb >= 1000:
                            ram_gb = round(ram_mb / 1024)
                            gpu_list.append(f"{name} ({ram_gb}GB)")
                        else:
                            gpu_list.append(f"{name} ({int(ram_mb)}MB)")
                    else:
                        gpu_list.append(name)
                except (ValueError, TypeError):
                    gpu_list.append(name)
        info['gpu'] = "\n".join(gpu_list) if gpu_list else "No detectada"

        try:
            cuda_cmd = ['nvidia-smi']
            cuda_result = subprocess.run(cuda_cmd, capture_output=True, text=True,
                                         creationflags=creation_flags, check=True, encoding='utf-8')
            for line in cuda_result.stdout.splitlines():
                if "CUDA Version" in line:
                    version_part = line.split("CUDA Version:")[1]
                    info["cuda"] = version_part.split("|")[0].strip()
                    break
        except (FileNotFoundError, subprocess.CalledProcessError):
            info["cuda"] = "None"

    except Exception as e:
        print(f"Error al obtener la información del sistema: {e}")
    return info

# ---------------------------- Helpers de formatos ----------------------------

_height_regex = re.compile(r'(?:(\d+)\s*[pP])|(?:\d+\s*[xX]\s*(\d+))')

def _get_height(fmt: dict) -> Optional[int]:
    h = fmt.get('height')
    if isinstance(h, int) and h > 0:
        return h
    res = fmt.get('resolution') or fmt.get('format_note') or ""
    m = _height_regex.search(str(res))
    if m:
        return int(m.group(1) or m.group(2))
    return None

def _filesize_of(fmt: dict) -> Optional[int]:
    return fmt.get('filesize') or fmt.get('filesize_approx')

# ---------------------------- Workers --------------------------------

class UpdateCheckerWorker(QObject):
    update_check_finished = pyqtSignal(dict)
    def run(self):
        api_url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/releases/latest"
        update_info = {'update_available': False, 'latest_version': '', 'error': None, 'assets': []}
        try:
            response = requests.get(api_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            latest_version_str = data['tag_name'].lstrip('v')
            if parse_version(latest_version_str) > parse_version(APP_VERSION):
                update_info['update_available'] = True
                update_info['latest_version'] = latest_version_str
                update_info['assets'] = data.get('assets', [])
        except Exception as e:
            update_info['error'] = str(e)
        self.update_check_finished.emit(update_info)

class FormatFetcherWorker(QObject):
    formats_fetched = pyqtSignal(int, list)
    error = pyqtSignal(int, str)
    finished = pyqtSignal()
    def __init__(self, row, url, ydl_opts):
        super().__init__()
        self.row, self.url, self.ydl_opts = row, url, ydl_opts
    def run(self):
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info_dict = ydl.extract_info(self.url, download=False)
            self.formats_fetched.emit(self.row, info_dict.get('formats', []))
        except Exception as e:
            self.error.emit(self.row, str(e))
        finally:
            self.finished.emit()

class DownloadWorker(QObject):
    finished = pyqtSignal(dict, str, str)
    progress = pyqtSignal(int, int)
    error = pyqtSignal(dict, str)
    paused = pyqtSignal(dict)
    def __init__(self, job, options):
        super().__init__()
        self.job, self.ydl_opts = job, options
        self.is_running = True

    def run(self):
        final_result = ""
        temp_dir = self.job.get('temp_dir')
        before = set(os.listdir(temp_dir)) if temp_dir and os.path.isdir(temp_dir) else set()
        try:
            self.ydl_opts['progress_hooks'] = [self.progress_hook]
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info_dict = ydl.extract_info(self.job['url'], download=True)

            # ¿Playlist?
            is_playlist = isinstance(info_dict, dict) and (info_dict.get('_type') == 'playlist' or info_dict.get('entries'))
            if is_playlist:
                final_result = temp_dir
            else:
                # Detectar archivos creados realmente (soporta postprocesadores: extracción de audio, merge, etc.)
                after = set(os.listdir(temp_dir)) if temp_dir and os.path.isdir(temp_dir) else set()
                created = [os.path.join(temp_dir, f) for f in sorted(after - before)]
                files = [
                    p for p in created
                    if os.path.isfile(p) and not p.endswith(('.part', '.ytdl', '.temp', '.tmp'))
                ]

                if files:
                    final_file = files[-1]  # el más reciente creado
                else:
                    # Fallback a prepare_filename, y si no existe, tomamos cualquier media del dir
                    with yt_dlp.YoutubeDL(self.ydl_opts) as ydl2:
                        cand_path = ydl2.prepare_filename(info_dict)
                    final_file = cand_path if os.path.exists(cand_path) else ""
                    if not final_file:
                        media_exts = {'.mp3','.m4a','.aac','.opus','.wav','.flac','.mp4','.mkv','.webm','.mov','.ts','.m4v'}
                        cands = [os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if os.path.splitext(f)[1].lower() in media_exts]
                        if cands:
                            final_file = max(cands, key=lambda p: os.path.getmtime(p))

                # Si el usuario pidió "solo video" pero el sitio solo ofrece stream combinado, quitamos el audio
                if self.job.get('strip_audio') and final_file and os.path.exists(final_file):
                    try:
                        ffmpeg_path = self.ydl_opts.get('ffmpeg_location') or imageio_ffmpeg.get_ffmpeg_exe()
                        base, ext = os.path.splitext(final_file)
                        tmp_out = base + ".__noaudio__" + ext
                        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                        subprocess.run(
                            [ffmpeg_path, '-y', '-i', final_file, '-c', 'copy', '-an', tmp_out],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            check=False, creationflags=creation_flags
                        )
                        if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
                            try:
                                os.replace(tmp_out, final_file)
                            except Exception:
                                shutil.move(tmp_out, final_file)
                        else:
                            if os.path.exists(tmp_out):
                                try: os.remove(tmp_out)
                                except Exception: pass
                    except Exception as e:
                        print(f"[{_ts()}] [DL] No se pudo eliminar audio: {e}")

                final_result = final_file

            if self.is_running:
                self.finished.emit(self.job, "Completado", final_result)

        except DownloadPausedException:
            self.paused.emit(self.job)
        except Exception as e:
            if self.is_running:
                self.error.emit(self.job, str(e))


    def progress_hook(self, d):
        if not self.is_running:
            raise DownloadPausedException("Download paused by user.")
        if d['status'] == 'downloading':
            percent_str = d.get('_percent_str', '0.0%').strip()
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            percent_str = ansi_escape.sub('', percent_str)
            try:
                percent = int(float(percent_str.strip('%')))
                self.progress.emit(self.job['row'], percent)
            except (ValueError, TypeError):
                pass

    def stop(self):
        self.is_running = False

class ApiValidatorWorker(QObject):
    validation_finished = pyqtSignal(bool)
    def __init__(self, token):
        super().__init__()
        self.token = token
    def run(self):
        if not self.token:
            self.validation_finished.emit(False)
            return
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot = telegram.Bot(token=self.token)
            is_valid = loop.run_until_complete(self.check_token(bot))
            self.validation_finished.emit(is_valid)
        except Exception:
            self.validation_finished.emit(False)
    async def check_token(self, bot):
        try:
            await bot.get_me()
            return True
        except (telegram.error.InvalidToken, telegram.error.NetworkError):
            return False

# --------------------- Telegram Bot Worker (ciclo de vida robusto) ---------------------

class TelegramBotWorker(QObject):
    finished = pyqtSignal()
    def __init__(self, token, whitelist, blacklist, whitelist_enabled, blacklist_enabled, ffmpeg_path, dest_video_path):
        super().__init__()
        self.token = token
        self.whitelist = [str(x) for x in whitelist]
        self.blacklist = [str(x) for x in blacklist]
        self.whitelist_enabled = whitelist_enabled
        self.blacklist_enabled = blacklist_enabled
        self.ffmpeg_path = ffmpeg_path
        self.dest_video_path = dest_video_path  # compat
        self.application = None
        self.is_running = True
        self.loop = None
        self.stop_event = None

        # estado de guardia de tamaño
        self._dl_files = {}  # filename -> {'downloaded': int, 'total': Optional[int]}

    # ----- utilidades específicas del bot -----
    def _topic_kwargs(self, update):
        try:
            tid = getattr(update.message, "message_thread_id", None)
        except Exception:
            tid = None
        return {"message_thread_id": tid} if tid else {}

    def _is_playlist(self, url: str) -> bool:
        lower = url.lower()
        if "playlist?" in lower or "list=" in lower:
            return True
        ydl_opts = base_ytdlp_opts(self.ffmpeg_path) | {"extract_flat": "in_playlist"}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if isinstance(info, dict) and (info.get("_type") == "playlist" or info.get("entries")):
                return True
        except Exception:
            pass
        return False

    def _head_content_length(self, url: str) -> Optional[int]:
        try:
            r = requests.head(url, headers=COMMON_HEADERS, allow_redirects=True, timeout=8)
            cl = r.headers.get('Content-Length') or r.headers.get('content-length')
            if cl and cl.isdigit():
                return int(cl)
        except Exception:
            pass
        try:
            r = requests.get(url, headers={**COMMON_HEADERS, "Range": "bytes=0-0"}, stream=True, timeout=8)
            cr = r.headers.get('Content-Range') or r.headers.get('content-range')
            if cr and '/' in cr:
                total = cr.split('/')[-1]
                if total.isdigit():
                    return int(total)
        except Exception:
            pass
        return None

    def _estimate_download_size(self, url):
        ydl_opts = base_ytdlp_opts(self.ffmpeg_path) | {
            'format': FORMAT_FALLBACK,
            'skip_download': True,
            'noplaylist': True
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if 'requested_formats' in info and info['requested_formats']:
                total = 0
                for f in info['requested_formats']:
                    size = f.get('filesize') or f.get('filesize_approx')
                    if not size:
                        f_url = f.get('url')
                        if f_url:
                            size = self._head_content_length(f_url)
                    if size:
                        total += int(size)
                    else:
                        return None
                return total or None

            size = info.get('filesize') or info.get('filesize_approx')
            if not size:
                i_url = info.get('url')
                if i_url:
                    size = self._head_content_length(i_url)
            return int(size) if size else None

        except Exception:
            return None

    def _progress_hook_guard(self, d):
        if not self.is_running:
            raise yt_dlp.utils.DownloadError("Detenido por el usuario/bot desactivado")

        if d.get('status') != 'downloading':
            return

        fn = d.get('filename') or ""
        downloaded = int(d.get('downloaded_bytes') or 0)
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or None

        self._dl_files[fn] = {'downloaded': downloaded, 'total': int(total) if total else None}

        sum_downloaded = sum(v['downloaded'] for v in self._dl_files.values())
        sum_totals_known = sum(v['total'] for v in self._dl_files.values() if v['total'] is not None)

        if sum_totals_known and sum_totals_known > TELEGRAM_SIZE_LIMIT:
            raise yt_dlp.utils.DownloadError("El archivo final supera 45MB (estimado)")
        if sum_downloaded > TELEGRAM_SIZE_LIMIT:
            raise yt_dlp.utils.DownloadError("El archivo final supera 45MB durante la descarga")

    def _download_video_blocking(self, url, temp_dir):
        os.makedirs(temp_dir, exist_ok=True)
        before = set(os.listdir(temp_dir))

        self._dl_files = {}

        ydl_opts = base_ytdlp_opts(self.ffmpeg_path) | {
            'format': FORMAT_FALLBACK,
            'outtmpl': os.path.join(temp_dir, '%(title)s - %(id)s.%(ext)s'),
            'progress_hooks': [self._progress_hook_guard],
            'noplaylist': True
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                _ = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            if ("Requested format is not available" in msg) or ("not available" in msg and "format" in msg.lower()):
                retry_opts = base_ytdlp_opts(self.ffmpeg_path) | {
                    'format': 'b',
                    'outtmpl': os.path.join(temp_dir, '%(title)s - %(id)s.%(ext)s'),
                    'progress_hooks': [self._progress_hook_guard],
                    'noplaylist': True
                }
                with yt_dlp.YoutubeDL(retry_opts) as ydl:
                    _ = ydl.extract_info(url, download=True)
            else:
                raise

        after = set(os.listdir(temp_dir))
        created = [os.path.join(temp_dir, f) for f in sorted(after - before)]
        final_files = [p for p in created if os.path.isfile(p) and not p.endswith(('.part', '.ytdl', '.temp'))]
        return final_files

    # ------------------------------------------

    def run(self):
        """Hilo dedicado con su propio event loop y parada graceful (sin run_polling)."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def lifecycle():
            try:
                self.stop_event = asyncio.Event()
                self.application = Application.builder().token(self.token).build()
                self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

                # init + start + polling manual
                await self.application.initialize()
                await self.application.start()
                await self.application.updater.start_polling()

                # esperar señal de stop
                await self.stop_event.wait()

                # detener ordenadamente
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            except Exception as e:
                print(f"[{_ts()}] [ERROR Telegram lifecycle] {e}")

        try:
            self.loop.run_until_complete(lifecycle())
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop=self.loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            except Exception:
                pass
            try:
                if not self.loop.is_closed():
                    self.loop.close()
            except Exception:
                pass
            print(f"[{_ts()}] [BOT] Loop cerrado. run() termina.")
            self.finished.emit()

    async def handle_message(self, update, context):
        if not self.is_running:
            return

        chat = update.effective_chat
        msg  = update.effective_message
        user = update.effective_user

        chat_id   = chat.id
        thread_id = getattr(msg, "message_thread_id", None)
        text      = msg.text or ""

        print(f"[{_ts()}] [BOT] Incoming text chat_id={chat_id} type={chat.type} thread_id={thread_id} user_id={user.id}")
        print(f"[{_ts()}] [BOT] ACL estado: WL={self.whitelist_enabled} BL={self.blacklist_enabled}")
        print(f"[{_ts()}] [BOT] ACL membership: in_WL={str(user.id) in self.whitelist} in_BL={str(user.id) in self.blacklist}")

        if self.whitelist_enabled:
            if str(user.id) not in self.whitelist:
                await context.bot.send_message(chat_id, "❌ No tienes permiso para usar este bot.", **self._topic_kwargs(update))
                return
        elif self.blacklist_enabled:
            if str(user.id) in self.blacklist:
                await context.bot.send_message(chat_id, "❌ Tienes el acceso restringido.", **self._topic_kwargs(update))
                return

        urls = re.findall(URL_REGEX, text)
        if not urls:
            print(f"[{_ts()}] [BOT] No se detectaron URLs en el mensaje.")
            return
        url = urls[0]

        is_playlist = await asyncio.to_thread(self._is_playlist, url)
        if is_playlist:
            print(f"[{_ts()}] [BOT] Playlist detectada. Rechazando (versión gratuita).")
            await context.bot.send_message(
                chat_id,
                "⚠️ *Playlists no disponibles en el bot de Telegram (versión gratuita).* "
                "Descarga playlists desde la *app de Windows*. Envíame un enlace de un solo video.",
                parse_mode="Markdown",
                **self._topic_kwargs(update),
            )
            return

        # === Estimación previa ===
        est_bytes = await asyncio.to_thread(self._estimate_download_size, url)
        if est_bytes is not None:
            if est_bytes > TELEGRAM_SIZE_LIMIT:
                est_mb = est_bytes / (1024*1024)
                print(f"[{_ts()}] [BOT] Rechazado (~{est_mb:.1f}MB > 45MB) chat_id={chat_id} thread_id={thread_id}")
                await context.bot.send_message(
                    chat_id,
                    f"⛔ El video pesa ~{est_mb:.1f} MB, supera el límite de 45 MB. No se descargará.",
                    **self._topic_kwargs(update),
                )
                return
        else:
            await context.bot.send_message(
                chat_id,
                "ℹ️ No pude estimar el tamaño previamente; intentaré descargar y cancelaré si supera 45 MB.",
                **self._topic_kwargs(update),
            )

        print(f"[{_ts()}] [BOT] Aceptado. Iniciando descarga chat_id={chat_id} thread_id={thread_id}")

        tg_temp_dir = os.path.join(TEMP_DOWNLOADS_DIR, "_tg", uuid.uuid4().hex)
        await context.bot.send_message(chat_id, "✅ Link recibido. Descargando…", **self._topic_kwargs(update))

        filepaths = []
        try:
            filepaths = await asyncio.to_thread(self._download_video_blocking, url, tg_temp_dir)

            if not self.is_running:
                print(f"[{_ts()}] [BOT] Bot desactivado durante descarga. Abortando envío.")
                return

            if not filepaths:
                await context.bot.send_message(
                    chat_id, "😕 No se generó ningún archivo final (posible error o excede 45MB).",
                    **self._topic_kwargs(update),
                )
                return

            path = filepaths[0]
            if os.path.getsize(path) > TELEGRAM_SIZE_LIMIT:
                await context.bot.send_message(
                    chat_id, "⛔ El archivo final supera 45MB. No puedo enviarlo por Telegram.",
                    **self._topic_kwargs(update),
                )
                return

            print(f"[{_ts()}] [BOT] Enviando video chat_id={chat_id} thread_id={thread_id}")
            with open(path, 'rb') as video_file:
                await context.bot.send_video(chat_id, video=video_file, supports_streaming=True, **self._topic_kwargs(update))

        except yt_dlp.utils.DownloadError as e:
            msg_err = str(e)
            print(f"[{_ts()}] [BOT] DownloadError: {msg_err}")
            if "45MB" in msg_err or "45mb" in msg_err or "supera 45MB" in msg_err.lower():
                await context.bot.send_message(
                    chat_id,
                    "⛔ El archivo final supera 45 MB. No puedo enviarlo por Telegram.",
                    **self._topic_kwargs(update),
                )
            elif ("facebook" in msg_err.lower() or "facebook" in url.lower()) and "Cannot parse data" in msg_err:
                await context.bot.send_message(
                    chat_id,
                    "😕 Facebook devolvió una página que requiere inicio de sesión o no es pública. "
                    "Prueba con un enlace público (p. ej. fb.watch/… o /videos/…) "
                    "o descárgalo desde la app de Windows.",
                    **self._topic_kwargs(update),
                )
            else:
                await context.bot.send_message(
                    chat_id,
                    "😕 Error del extractor. Probé máxima compatibilidad.\n"
                    "Si persiste, actualiza yt-dlp y vuelve a intentar.",
                    **self._topic_kwargs(update),
                )
        except Exception as e:
            print(f"[{_ts()}] [BOT] Error general en handle_message: {e}")
            await context.bot.send_message(chat_id, f"😕 Error al procesar: {str(e)[:1000]}", **self._topic_kwargs(update))
        finally:
            try:
                _safe_rmtree(tg_temp_dir)
                print(f"[{_ts()}] [BOT] Limpieza temporal Telegram -> {tg_temp_dir}")
            except Exception as e:
                print(f"[{_ts()}] [BOT] Error limpiando temporales Telegram: {e}")

    def stop(self):
        """Parada robusta: detiene polling y ciclo, y libera el hilo siempre."""
        print(f"[BOT WORKER] Stop requested.")
        self.is_running = False
        try:
            if self.loop and not self.loop.is_closed():
                # Señales explícitas para salir rápido
                if self.stop_event is not None:
                    self.loop.call_soon_threadsafe(self.stop_event.set)
                if self.application:
                    asyncio.run_coroutine_threadsafe(self.application.updater.stop(), self.loop)
                    asyncio.run_coroutine_threadsafe(self.application.stop(), self.loop)
                    asyncio.run_coroutine_threadsafe(self.application.shutdown(), self.loop)
        except Exception as e:
            print(f"[BOT WORKER] stop error: {e}")

# ------------------------------ GUI -----------------------------------

class SettingsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.setWindowTitle("Configuración")
        self.setWindowIcon(QIcon("BitStation.ico"))
        self.setMinimumSize(600, 600)
        self.setModal(True)
        self.validator_thread = None

        main_layout = QVBoxLayout(self)
        top_bar_layout = QHBoxLayout()
        back_button = QPushButton("←")
        font_arrow = QFont(); font_arrow.setPointSize(16); font_arrow.setBold(True)
        back_button.setFont(font_arrow)
        back_button.setFixedSize(QSize(40, 40))
        back_button.clicked.connect(self.close)
        title_label = QLabel("Configuración")
        font_title = QFont(); font_title.setPointSize(14)
        title_label.setFont(font_title)
        top_bar_layout.addWidget(back_button)
        top_bar_layout.addWidget(title_label)
        top_bar_layout.addStretch()
        main_layout.addLayout(top_bar_layout)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self.setup_general_tab()
        self.setup_telegram_tab()

        self.tabs.currentChanged.connect(self.on_tab_changed)

    def on_tab_changed(self, index):
        if self.tabs.tabText(index) == "Telegram" and self.enable_telegram_cb.isChecked():
            print("[SETTINGS] Pestaña de Telegram seleccionada, iniciando validación automática.")
            self.validate_api_token()

    # ==== General (con bloque bonito de specs) ====
    def setup_general_tab(self):
        general_widget = QWidget()
        layout = QVBoxLayout(general_widget)
        layout.setContentsMargins(15, 20, 15, 15)
        layout.setSpacing(10)

        video_path_label = QLabel("Ruta actual de Videos:")
        self.video_path_display = QLineEdit(self.main_window.video_path)
        self.video_path_display.setReadOnly(True)
        video_folder_button = QPushButton("Elegir carpeta de destino")
        video_folder_button.clicked.connect(self.select_video_path)

        audio_path_label = QLabel("Ruta Actual de Audios:")
        self.audio_path_display = QLineEdit(self.main_window.audio_path)
        self.audio_path_display.setReadOnly(True)
        audio_folder_button = QPushButton("Elegir carpeta de destino")
        audio_folder_button.clicked.connect(self.select_audio_path)

        layout.addWidget(video_path_label)
        layout.addWidget(self.video_path_display)
        layout.addWidget(video_folder_button)
        layout.addSpacing(20)
        layout.addWidget(audio_path_label)
        layout.addWidget(self.audio_path_display)
        layout.addWidget(audio_folder_button)

        separator = QFrame(); separator.setFrameShape(QFrame.Shape.HLine); separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addSpacing(12); layout.addWidget(separator); layout.addSpacing(10)

        caracteristicas_label = QLabel("Características:")
        font_caracteristicas = QFont(); font_caracteristicas.setBold(True)
        caracteristicas_label.setFont(font_caracteristicas)
        layout.addWidget(caracteristicas_label)

        info = self.main_window.system_info or {}
        cpu  = info.get('cpu', 'No detectado')
        ram  = info.get('ram', 'No detectada')
        gpu_text = info.get('gpu', 'No detectada') or 'No detectada'
        gpus = "<br>".join([x for x in gpu_text.splitlines() if x.strip()])
        cuda = info.get('cuda', 'None')

        if "NVIDIA" in gpu_text.upper():
            if cuda and cuda != "None":
                cuda_block = "Se detectó una GPU NVIDIA...<br>Versión de CUDA instalada: " + cuda
            else:
                cuda_block = "Se detectó una GPU NVIDIA...<br>No se encontró la versión de CUDA."
        else:
            cuda_block = "No se detectó GPU NVIDIA."

        specs_html = (
            "<div>"
            "<b>[PROCESADOR]</b><br>"
            f"{cpu}<br><br>"
            "<b>[MEMORIA RAM]</b><br>"
            f"Capacidad Total: {ram}<br><br>"
            "<b>[TARJETA GRÁFICA (GPU)]</b><br>"
            f"{gpus}<br><br>"
            "<b>[VERSIÓN DE CUDA]</b><br>"
            f"{cuda_block}"
            "</div>"
        )

        self.specs_block = QLabel(specs_html)
        self.specs_block.setTextFormat(Qt.TextFormat.RichText)
        self.specs_block.setWordWrap(True)
        self.specs_block.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.specs_block.setStyleSheet("""
            QLabel {
                background: #f6f7fb;
                border: 1px solid #e1e3ea;
                border-radius: 8px;
                padding: 10px 12px;
                font-family: Consolas, 'Courier New', monospace;
                line-height: 1.3em;
            }
        """)
        layout.addWidget(self.specs_block)

        layout.addSpacing(10)
        self.version_label = QLabel(f"Version: v{APP_VERSION}")
        self.update_button = QPushButton("Comprobando actualizaciones...")
        self.update_button.setEnabled(False)

        layout.addWidget(self.version_label)
        self.update_button.setEnabled(False)
        layout.addStretch()
        layout.addWidget(self.update_button)

        self.tabs.addTab(general_widget, "General")

    # ==== Telegram ====
    def setup_telegram_tab(self):
        telegram_widget = QWidget()
        layout = QVBoxLayout(telegram_widget)
        layout.setContentsMargins(15, 20, 15, 15)
        layout.setSpacing(10)

        self.enable_telegram_cb = QCheckBox("Habilitar Telegram Bot")
        self.enable_telegram_cb.stateChanged.connect(self.toggle_telegram_widgets)
        layout.addWidget(self.enable_telegram_cb)

        self.telegram_api_group = QWidget()
        api_layout = QHBoxLayout(self.telegram_api_group)
        api_layout.setContentsMargins(0, 0, 0, 0)
        api_label = QLabel("Bot API Token:")
        self.api_token_input = QLineEdit()
        self.api_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_status_label = QLabel("⚪")
        self.api_token_input.textChanged.connect(self.save_telegram_settings)
        self.api_token_input.editingFinished.connect(self.validate_api_token)

        self.validate_api_button = QPushButton("Validar")
        self.validate_api_button.setVisible(False)

        api_layout.addWidget(api_label)
        api_layout.addWidget(self.api_token_input)
        api_layout.addWidget(self.validate_api_button)
        api_layout.addWidget(self.api_status_label)
        layout.addWidget(self.telegram_api_group)

        separator = QFrame(); separator.setFrameShape(QFrame.Shape.HLine); separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        # --- Autostart Windows (bot en segundo plano) --- (NUEVO)
        self.autostart_cb = QCheckBox("Iniciar bot al iniciar Windows (segundo plano)")
        self.autostart_cb.stateChanged.connect(self.on_autostart_toggled)
        layout.addWidget(self.autostart_cb)

        lists_layout = QHBoxLayout()
        # Whitelist
        whitelist_group = QVBoxLayout()
        wl_header_layout = QHBoxLayout()
        self.enable_whitelist_cb = QCheckBox("Habilitar Whitelist")
        self.enable_whitelist_cb.stateChanged.connect(self.on_whitelist_toggled)

        wl_header_layout.addWidget(self.enable_whitelist_cb)
        wl_header_layout.addStretch()
        add_wl_button = QPushButton("+"); remove_wl_button = QPushButton("-")
        add_wl_button.setFixedSize(25, 25); remove_wl_button.setFixedSize(25, 25)
        add_wl_button.clicked.connect(lambda: self.add_id_to_list(self.whitelist_table))
        remove_wl_button.clicked.connect(lambda: self.remove_id_from_list(self.whitelist_table))
        wl_header_layout.addWidget(add_wl_button); wl_header_layout.addWidget(remove_wl_button)
        whitelist_group.addLayout(wl_header_layout)
        self.whitelist_table = QTableWidget(0, 1)
        self.whitelist_table.setHorizontalHeaderLabels(["User ID"])
        self.whitelist_table.horizontalHeader().setStretchLastSection(True)
        whitelist_group.addWidget(self.whitelist_table)
        lists_layout.addLayout(whitelist_group)

        # Blacklist
        blacklist_group = QVBoxLayout()
        bl_header_layout = QHBoxLayout()
        self.enable_blacklist_cb = QCheckBox("Habilitar Blacklist")
        self.enable_blacklist_cb.stateChanged.connect(self.on_blacklist_toggled)

        bl_header_layout.addWidget(self.enable_blacklist_cb)
        bl_header_layout.addStretch()
        add_bl_button = QPushButton("+"); remove_bl_button = QPushButton("-")
        add_bl_button.setFixedSize(25, 25); remove_bl_button.setFixedSize(25, 25)
        add_bl_button.clicked.connect(lambda: self.add_id_to_list(self.blacklist_table))
        remove_bl_button.clicked.connect(lambda: self.remove_id_from_list(self.blacklist_table))
        bl_header_layout.addWidget(add_bl_button); bl_header_layout.addWidget(remove_bl_button)
        blacklist_group.addLayout(bl_header_layout)
        self.blacklist_table = QTableWidget(0, 1)
        self.blacklist_table.setHorizontalHeaderLabels(["User ID"])
        self.blacklist_table.horizontalHeader().setStretchLastSection(True)
        blacklist_group.addWidget(self.blacklist_table)
        lists_layout.addLayout(blacklist_group)

        layout.addLayout(lists_layout)
        self.tabs.addTab(telegram_widget, "Telegram")
        self.load_telegram_settings()

    # --- Exclusividad WL/BL ---
    def on_whitelist_toggled(self, state):
        wl_on = (state == Qt.CheckState.Checked.value)
        if wl_on and self.enable_blacklist_cb.isChecked():
            self.enable_blacklist_cb.blockSignals(True)
            self.enable_blacklist_cb.setChecked(False)
            self.enable_blacklist_cb.blockSignals(False)
        self.save_telegram_settings()

    def on_blacklist_toggled(self, state):
        bl_on = (state == Qt.CheckState.Checked.value)
        if bl_on and self.enable_whitelist_cb.isChecked():
            self.enable_whitelist_cb.blockSignals(True)
            self.enable_whitelist_cb.setChecked(False)
            self.enable_whitelist_cb.blockSignals(False)
        self.save_telegram_settings()

    # --- NUEVO: toggle de autostart Windows ---
    def on_autostart_toggled(self, state):
        enabled = (state == Qt.CheckState.Checked.value)
        try:
            self.main_window.settings.setValue("telegram/autostart", enabled)
            self.main_window.settings.sync()
            self.main_window.set_windows_autostart(enabled)
            print(f"[AUTOSTART] Autostart Windows -> {enabled}")
        except Exception as e:
            print(f"[AUTOSTART] Error al aplicar autostart: {e}")

    def add_id_to_list(self, table):
        user_id, ok = QInputDialog.getText(self, "Añadir ID", "Introduce el User ID de Telegram:")
        if ok and user_id.strip():
            row_count = table.rowCount()
            table.insertRow(row_count)
            table.setItem(row_count, 0, QTableWidgetItem(user_id.strip()))
            self.save_telegram_settings()

    def remove_id_from_list(self, table):
        selected_rows = table.selectionModel().selectedRows()
        if not selected_rows:
            return
        for index in sorted(selected_rows, reverse=True):
            table.removeRow(index.row())
        self.save_telegram_settings()

    def toggle_telegram_widgets(self, state):
        is_checked = (state == Qt.CheckState.Checked.value)
        print(f"[{_ts()}] [UI] Checkbox Telegram -> {is_checked}")
        self.telegram_api_group.setVisible(is_checked)
        self.save_telegram_settings()
        if is_checked:
            self.validate_api_token()
        self.main_window.toggle_telegram_bot()

    def validate_api_token(self):
        if not self.telegram_api_group.isVisible():
            return
        print(f"[{_ts()}] [VALIDATOR] validate_api_token() llamado.")
        token = self.api_token_input.text()
        if getattr(self, "validator_thread", None) is not None and self.validator_thread.isRunning():
            return
        self.api_status_label.setText("⚪")
        self.validator_thread = QThread()
        self.validator_worker = ApiValidatorWorker(token)
        self.validator_worker.moveToThread(self.validator_thread)
        self.validator_thread.started.connect(self.validator_worker.run)
        self.validator_worker.validation_finished.connect(self.on_validation_finished)
        self.validator_worker.validation_finished.connect(self.validator_thread.quit)
        self.validator_worker.validation_finished.connect(self.validator_worker.deleteLater)
        self.validator_thread.finished.connect(self.on_validator_thread_finished)
        self.validator_thread.start()

    def on_validator_thread_finished(self):
        self.validator_thread = None

    def on_validation_finished(self, is_valid: bool):
        print(f"[{_ts()}] [VALIDATOR] Validación terminada. Resultado: {is_valid}")
        self.api_status_label.setText("🟢" if is_valid else "🔴")

        # Guarda lo editado (token y flags) en QSettings
        self.save_telegram_settings()

        token_now = self.api_token_input.text().strip()

        # Si no es válido, no reiniciamos nada
        if not is_valid:
            return

        # Si el bot está habilitado y el token CAMBIÓ => reinicio en caliente
        if self.enable_telegram_cb.isChecked():
            if token_now and token_now != getattr(self, "_last_valid_token", ""):
                print("[SETTINGS] Token válido y CAMBIADO. Reiniciando bot…")
                self._last_valid_token = token_now
                # Reinicio limpio con el nuevo token sin cerrar la app
                self.main_window.restart_telegram_bot(new_token=token_now)
            else:
                # Si no cambió pero el bot no estaba corriendo, intenta levantarlo
                self.main_window.toggle_telegram_bot()

    def load_telegram_settings(self):
        settings = self.main_window.settings

        self.enable_telegram_cb.blockSignals(True)
        self.enable_whitelist_cb.blockSignals(True)
        self.enable_blacklist_cb.blockSignals(True)

        self.enable_telegram_cb.setChecked(settings.value("telegram/enabled", False, type=bool))
        self.api_token_input.setText(settings.value("telegram/token", "", type=str))
        wl_enabled = settings.value("telegram/whitelist_enabled", False, type=bool)
        bl_enabled = settings.value("telegram/blacklist_enabled", False, type=bool)
        if wl_enabled and bl_enabled:
            bl_enabled = False  # exclusividad
        self.enable_whitelist_cb.setChecked(wl_enabled)
        self.enable_blacklist_cb.setChecked(bl_enabled)

        self.enable_telegram_cb.blockSignals(False)
        self.enable_whitelist_cb.blockSignals(False)
        self.enable_blacklist_cb.blockSignals(False)

        for user_id in json.loads(settings.value("telegram/whitelist", "[]", type=str)):
            self.add_id_to_list_silent(self.whitelist_table, user_id)
        for user_id in json.loads(settings.value("telegram/blacklist", "[]", type=str)):
            self.add_id_to_list_silent(self.blacklist_table, user_id)

        self.telegram_api_group.setVisible(self.enable_telegram_cb.isChecked())

        # ⚠️ Inicializa el último token válido para comparación futura
        self._last_valid_token = self.api_token_input.text().strip()

        # Estado real del autostart desde la carpeta Startup (NUEVO)
        auto_enabled = False
        try:
            auto_enabled = self.main_window.is_windows_autostart_enabled()
        except Exception:
            auto_enabled = False
        self.autostart_cb.blockSignals(True)
        self.autostart_cb.setChecked(auto_enabled)
        self.autostart_cb.blockSignals(False)

        self.save_telegram_settings()

    def add_id_to_list_silent(self, table, user_id):
        row_count = table.rowCount()
        table.insertRow(row_count)
        table.setItem(row_count, 0, QTableWidgetItem(str(user_id)))

    def save_telegram_settings(self):
        settings = self.main_window.settings

        wl_enabled = self.enable_whitelist_cb.isChecked()
        bl_enabled = self.enable_blacklist_cb.isChecked()
        if wl_enabled and bl_enabled:
            self.enable_blacklist_cb.blockSignals(True)
            self.enable_blacklist_cb.setChecked(False)
            self.enable_blacklist_cb.blockSignals(False)
            bl_enabled = False

        settings.setValue("telegram/enabled", self.enable_telegram_cb.isChecked())
        settings.setValue("telegram/token", self.api_token_input.text())
        settings.setValue("telegram/whitelist_enabled", wl_enabled)
        settings.setValue("telegram/blacklist_enabled", bl_enabled)

        whitelist = [self.whitelist_table.item(row, 0).text()
                     for row in range(self.whitelist_table.rowCount())
                     if self.whitelist_table.item(row, 0)]
        blacklist = [self.blacklist_table.item(row, 0).text()
                     for row in range(self.blacklist_table.rowCount())
                     if self.blacklist_table.item(row, 0)]
        settings.setValue("telegram/whitelist", json.dumps(whitelist))
        settings.setValue("telegram/blacklist", json.dumps(blacklist))

        # Actualiza ACL en caliente
        self.main_window.apply_telegram_acl_settings()

    def closeEvent(self, event):
        print("[SETTINGS] Cerrando ventana de configuración...")
        if getattr(self, "validator_thread", None) and self.validator_thread.isRunning():
            print("[SETTINGS] Deteniendo hilo de validación activo al cerrar.")
            self.validator_thread.quit()
            self.validator_thread.wait()
        self.save_telegram_settings()
        super().closeEvent(event)

    def select_video_path(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta para Videos", self.main_window.video_path)
        if folder:
            self.main_window.set_video_path(folder)
            self.video_path_display.setText(folder)

    def select_audio_path(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta para Audios", self.main_window.audio_path)
        if folder:
            self.main_window.set_audio_path(folder)
            self.audio_path_display.setText(folder)

# ------------------------------ MainWindow ----------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("BitStation", "MultimediaDownloader")
        self.load_settings()
        self.ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"[INFO] FFMPEG encontrado en: {self.ffmpeg_path}")

        # Chequeo no bloqueante de yt-dlp (mejora compatibilidad)
        maybe_update_ytdlp_async()

        self.download_modes = ['Ambos', 'Audio', 'Video']; self.current_download_mode_index = 0
        self.is_downloading = False; self.active_thread = None; self.active_worker = None; self.download_queue = []
        self.active_format_fetchers = {}; self.active_downloads = {}
        self.download_info = {}
        self.system_info = get_system_info(); self.update_info = {}

        self.telegram_thread = None
        self.telegram_worker = None
        self.telegram_pending_start = False

        self.setWindowTitle("BitStation Multimedia Downloader"); self.setWindowIcon(QIcon("BitStation.ico")); self.setGeometry(100, 100, 900, 600)
        self.setup_ui(); self.setup_connections(); self.check_for_updates()
        self.toggle_telegram_bot()

    def setup_ui(self):
        central_widget = QWidget(); self.setCentralWidget(central_widget); main_layout = QVBoxLayout(central_widget)

        top_controls_layout = QHBoxLayout()
        self.settings_button = QPushButton("⚙️")
        font_settings = QFont(); font_settings.setPointSize(16)
        self.settings_button.setFont(font_settings)
        self.settings_button.setFixedSize(QSize(40, 40))

        self.download_mode_button = QPushButton(self.download_modes[self.current_download_mode_index])
        top_controls_layout.addWidget(self.settings_button); top_controls_layout.addStretch(); top_controls_layout.addWidget(self.download_mode_button)

        self.prompt_label = QLabel("¿Qué desea descargar?")
        font_prompt = QFont(); font_prompt.setPointSize(18)
        self.prompt_label.setFont(font_prompt); self.prompt_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.prompt_label.setContentsMargins(0, 10, 0, 10)

        search_layout = QHBoxLayout()
        self.search_bar = QLineEdit(); self.search_bar.setPlaceholderText("Pega uno o más links aquí (Ctrl+V)..."); self.search_bar.setMinimumHeight(35)
        self.master_download_button = QPushButton(); self.master_download_button.setFixedSize(QSize(40, 35)); self.update_master_download_icon()
        search_layout.addWidget(self.search_bar); search_layout.addWidget(self.master_download_button)

        self.table = QTableWidget(); self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(['#', 'Link', 'Estado', 'Formato', 'Resolución', 'Eliminar'])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        main_layout.addLayout(top_controls_layout); main_layout.addWidget(self.prompt_label)
        main_layout.addLayout(search_layout); main_layout.addWidget(self.table)

    def setup_connections(self):
        self.search_bar.textChanged.connect(self.handle_paste)
        self.download_mode_button.clicked.connect(self.toggle_download_mode)
        self.master_download_button.clicked.connect(self.toggle_master_download)

        self.settings_button.clicked.connect(self.open_settings_window)

    def check_for_updates(self):
        self.update_thread = QThread(self)
        self.update_worker = UpdateCheckerWorker()
        self.update_worker.moveToThread(self.update_thread)
        self.update_thread.started.connect(self.update_worker.run)
        self.update_worker.update_check_finished.connect(self.on_update_check_finished)
        self.update_worker.update_check_finished.connect(self.update_thread.quit)
        self.update_thread.finished.connect(self.update_worker.deleteLater)
        self.update_thread.finished.connect(self.update_thread.deleteLater)
        self.update_thread.start()

    def on_update_check_finished(self, update_info):
        self.update_info = update_info
        if update_info.get('error'):
            print(f"[UPDATE] {update_info['error']}")

    def open_settings_window(self):
        print("\n[MAIN] Abriendo ventana de configuración...")
        dialog = SettingsWindow(self)
        if self.update_info:
            if self.update_info.get('update_available'):
                version = self.update_info.get('latest_version')
                dialog.update_button.setText(f"Actualización disponible: v{version}")
                dialog.update_button.setEnabled(True)
                dialog.update_button.clicked.connect(self.start_update_process)
            else:
                dialog.update_button.setText("Estás en la última versión")
                dialog.update_button.setEnabled(False)
        dialog.exec()
        print("[MAIN] Ventana de configuración cerrada.")

    def start_update_process(self):
        print("Iniciando proceso de actualización...")
        update_dir = "update_temp"
        try:
            if not os.path.exists(update_dir):
                os.makedirs(update_dir)
            assets = self.update_info.get('assets', [])
            if not assets:
                print("[ERROR] No se encontraron archivos en el release de GitHub.")
                return

            # Elegimos ZIP (preferido)
            zip_asset = None
            for asset in assets:
                name = asset.get('name', '').lower()
                if name.endswith('.zip'):
                    zip_asset = asset
                    break
            if not zip_asset:
                print("[ERROR] El release no contiene un .zip. Sube un ZIP con todos los archivos.")
                return

            asset_url = zip_asset['browser_download_url']
            asset_name = zip_asset['name']
            file_path = os.path.join(update_dir, asset_name)

            print(f"Descargando {asset_name}...")
            with requests.get(asset_url, stream=True) as r:
                r.raise_for_status()
                with open(file_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            print(f" -> Descargado: {asset_name}")

            print("\nTodos los archivos descargados. Lanzando actualizador...")
            updater_script_path = "updater.py"
            if not os.path.exists(updater_script_path):
                print(f"[ERROR] No se encontró '{updater_script_path}' en la raíz del proyecto.")
                return

            pid = os.getpid()
            new_version = self.update_info.get('latest_version', '')
            subprocess.Popen([sys.executable, updater_script_path, str(pid), new_version],
                             creationflags=(subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0))
            self.close()

        except Exception as e:
            print(f"[ERROR] Falló el proceso de actualización: {e}")

    def load_settings(self):
        default_video_path = os.path.join(os.getcwd(), "Video")
        default_audio_path = os.path.join(os.getcwd(), "Audio")
        self.video_path = self.settings.value("videoPath", defaultValue=default_video_path)
        self.audio_path = self.settings.value("audioPath", defaultValue=default_audio_path)
        os.makedirs(self.video_path, exist_ok=True)
        os.makedirs(self.audio_path, exist_ok=True)

    def set_video_path(self, path):
        # Setter usado por SettingsWindow
        self.video_path = path
        os.makedirs(self.video_path, exist_ok=True)
        self.settings.setValue("videoPath", path)

    def set_audio_path(self, path):
        # Setter usado por SettingsWindow
        self.audio_path = path
        os.makedirs(self.audio_path, exist_ok=True)
        self.settings.setValue("audioPath", path)

    def toggle_download_mode(self):
        self.current_download_mode_index = (self.current_download_mode_index + 1) % len(self.download_modes)
        mode_text = self.download_modes[self.current_download_mode_index]
        self.download_mode_button.setText(mode_text)

    def toggle_master_download(self):
        if self.is_downloading:
            self.is_downloading = False; self.update_master_download_icon()
            if self.active_worker: self.active_worker.stop()
        else:
            self.build_download_queue()
            if self.download_queue:
                self.is_downloading = True; self.update_master_download_icon(); self.start_next_download()

    def update_master_download_icon(self):
        style = self.style()
        icon = style.standardIcon(QStyle.StandardPixmap.SP_MediaPause) if self.is_downloading else style.standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        self.master_download_button.setIcon(icon)

    def build_download_queue(self):
        self.download_queue.clear()
        for row in range(self.table.rowCount()):
            progress_bar = self.table.cellWidget(row, 2)
            if progress_bar and (progress_bar.format() in ["En cola", "Detenido", "Error"]):
                self.download_queue.append({'row': row})

    def start_next_download(self):
        if not self.is_downloading or not self.download_queue:
            self.is_downloading = False; self.update_master_download_icon(); return
        job_base = self.download_queue.pop(0)
        self.start_download_for_job(job_base['row'])

    def start_download_for_job(self, row):
        progress_bar = self.table.cellWidget(row, 2)
        if progress_bar and progress_bar.format() == "Detenido":
            print(f"Reanudando trabajo para la fila {row+1}. Limpiando progreso previo.")
            job_uuid = self.download_info[row].get('uuid')
            if job_uuid:
                temp_job_dir = os.path.join(TEMP_DOWNLOADS_DIR, job_uuid)
                if os.path.isdir(temp_job_dir):
                    try: shutil.rmtree(temp_job_dir)
                    except OSError as e: print(f"No se pudo limpiar el directorio temporal anterior: {e}")

        format_widget = self.table.cellWidget(row, 3)
        checkboxes = format_widget.findChildren(QCheckBox)
        want_audio = checkboxes[0].isChecked()
        want_video = checkboxes[1].isChecked()

        if want_audio and want_video:
            job_type = 'video'     # "ambos" se gestiona como salida de video final
        elif want_audio:
            job_type = 'audio'
        else:
            job_type = 'video'

        combo_res = self.table.cellWidget(row, 4)
        format_id = combo_res.currentData()
        fmt_id = (format_id or "").strip() if format_id else ""

        # === Selección de formato con fallbacks seguros (evita "Requested format is not available") ===
        if want_audio and not want_video:
            # Audio solo -> permitimos caer a "best" y luego extraemos audio con FFmpeg
            format_selection = "bestaudio/best"
        elif want_video and not want_audio:
            # Video solo -> intentamos id elegido o bestvideo; si no existe, caemos a "best" (combinado)
            if '+' in fmt_id:
                fmt_id = fmt_id.split('+', 1)[0].strip()
            if not fmt_id or fmt_id == "bestvideo+bestaudio/best":
                format_selection = "bestvideo/best"
            else:
                format_selection = f"{fmt_id}/best"
        else:
            # Ambos
            if fmt_id and '+' in fmt_id:
                format_selection = fmt_id
            elif fmt_id:
                format_selection = f"{fmt_id}+bestaudio/best"
            else:
                format_selection = "bestvideo+bestaudio/best"
        # === FIN selección de formato ===

        if not format_selection:
            self.on_download_error({'row': row}, "Formato inválido")
            return

        self.download_info[row]['format_selection'] = format_selection
        self.download_info[row]['job_type'] = job_type

        job_uuid = self.download_info[row]['uuid']
        temp_job_dir = os.path.join(TEMP_DOWNLOADS_DIR, job_uuid)
        os.makedirs(temp_job_dir, exist_ok=True)

        url = self.table.item(row, 1).text()

        if isinstance(progress_bar, QProgressBar):
            progress_bar.setFormat(f"Descargando {job_type}... %p%")
            progress_bar.setValue(0)

        ydl_opts = base_ytdlp_opts(self.ffmpeg_path) | {
            'format': format_selection,
            'outtmpl': os.path.join(temp_job_dir, '%(title)s.%(ext)s'),
            'nocolor': True,
            'hls_prefer_native': True,
            'continuedl': True,
            'nooverwrites': False,
        }

        # Audio solo -> extraer audio (soluciona TikTok/otros cuando no hay pista separada)
        if job_type == 'audio':
            ydl_opts |= {
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '0',
                }],
                'keepvideo': False,
            }

        # Pasamos flags al worker para poder quitar audio si el video vino combinado
        job = {
            'row': row, 'url': url, 'uuid': job_uuid, 'temp_dir': temp_job_dir,
            'job_type': job_type, 'strip_audio': (want_video and not want_audio)
        }

        self.active_thread = QThread(self)
        self.active_worker = DownloadWorker(job, ydl_opts)
        self.active_worker.moveToThread(self.active_thread)

        self.active_thread.started.connect(self.active_worker.run)
        self.active_worker.progress.connect(self.update_download_progress)
        self.active_worker.finished.connect(self.on_download_finished)
        self.active_worker.error.connect(self.on_download_error)
        self.active_worker.paused.connect(self.on_download_paused)
        self.active_worker.finished.connect(self.active_thread.quit)
        self.active_worker.error.connect(self.active_thread.quit)
        self.active_worker.paused.connect(self.active_thread.quit)
        self.active_thread.finished.connect(self.active_thread.deleteLater)
        self.active_worker.finished.connect(self.active_worker.deleteLater)

        self.active_downloads[row] = (self.active_thread, self.active_worker)
        self.active_thread.start()


    def update_download_progress(self, row, percent):
        widget = self.table.cellWidget(row, 2)
        if isinstance(widget, QProgressBar):
            widget.setValue(percent)

    def on_download_finished(self, job, message, final_result):
        row = job['row']
        if row in self.active_downloads:
            del self.active_downloads[row]

        job_type = self.download_info[row].get('job_type', 'video')
        destination_folder = self.audio_path if job_type == 'audio' else self.video_path

        try:
            if final_result and os.path.exists(final_result) and os.path.isdir(final_result):
                temp_job_dir = final_result
                print(f"[{_ts()}] [MOVE] Playlist detectada. Moviendo archivos desde: {temp_job_dir}")
                moved = 0
                for name in os.listdir(temp_job_dir):
                    src = os.path.join(temp_job_dir, name)
                    if os.path.isdir(src): continue
                    if name.endswith(('.part', '.ytdl', '.temp')): continue
                    try:
                        dst = _safe_move(src, destination_folder)
                        print(f"[{_ts()}] [MOVE] -> {dst}"); moved += 1
                    except Exception as e:
                        print(f"[{_ts()}] [MOVE] Error moviendo '{name}': {e}")
                try:
                    shutil.rmtree(temp_job_dir, ignore_errors=True)
                    print(f"[{_ts()}] [MOVE] Directorio temporal limpiado: {temp_job_dir}")
                except Exception as e:
                    print(f"[{_ts()}] [MOVE] Error limpiando dir temporal: {e}")

                if moved == 0:
                    message = "Sin archivos"
            else:
                final_filepath = final_result
                if final_filepath and os.path.exists(final_filepath):
                    try:
                        final_destination_path = _safe_move(final_filepath, destination_folder)
                        print(f"Archivo movido a: {final_destination_path}")
                        temp_job_dir = os.path.dirname(final_filepath)
                        shutil.rmtree(temp_job_dir, ignore_errors=True)
                        print(f"Directorio temporal limpiado: {temp_job_dir}")
                    except Exception as e:
                        print(f"Error al mover/limpiar el archivo final: {e}")
                        message = "Error de guardado"
        except Exception as e:
            print(f"[{_ts()}] [MOVE] Error general al finalizar: {e}")
            message = "Error de guardado"

        widget = self.table.cellWidget(row, 2)
        if isinstance(widget, QProgressBar):
            widget.setValue(100); widget.setFormat(message)
        if row in self.download_info:
            self.download_info[row]['completed'] = (message == "Completado")

        self.active_thread = None; self.active_worker = None
        self.start_next_download()

    def on_download_paused(self, job):
        row = job['row']
        print(f"La descarga en la fila {row+1} fue pausada por el usuario.")
        if row in self.active_downloads:
            thread, worker = self.active_downloads.pop(row)
            thread.quit(); thread.wait()
        widget = self.table.cellWidget(row, 2)
        if isinstance(widget, QProgressBar):
            widget.setFormat("Detenido")
        self.active_thread = None; self.active_worker = None

    def on_download_error(self, job, error_message):
        row = job['row']
        if row in self.active_downloads:
            del self.active_downloads[row]
        widget = self.table.cellWidget(row, 2)
        if isinstance(widget, QProgressBar):
            widget.setFormat("Error")
        print(f"Error en la fila {row+1}: {error_message}")
        self.download_queue = [q_job for q_job in self.download_queue if q_job['row'] != row]
        self.active_thread = None; self.active_worker = None
        self.start_next_download()

    def apply_telegram_acl_settings(self):
        if not self.telegram_worker:
            return
        wl = json.loads(self.settings.value("telegram/whitelist", "[]", type=str))
        bl = json.loads(self.settings.value("telegram/blacklist", "[]", type=str))
        wl_enabled = self.settings.value("telegram/whitelist_enabled", False, type=bool)
        bl_enabled = self.settings.value("telegram/blacklist_enabled", False, type=bool)
        self.telegram_worker.whitelist = [str(x) for x in wl]
        self.telegram_worker.blacklist = [str(x) for x in bl]
        self.telegram_worker.whitelist_enabled = wl_enabled
        self.telegram_worker.blacklist_enabled = bl_enabled
        print(f"[{_ts()}] [BOT] ACL actualizada: wl_enabled={wl_enabled} ({len(wl)} ids) bl_enabled={bl_enabled} ({len(bl)} ids)")

    # --- NUEVO helper: ruta del .vbs en carpeta Startup ---
    def _startup_vbs_path(self) -> str:
        appdata = os.environ.get("APPDATA", "")
        return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup", AUTOSTART_VBS_NAME)

    # --- NUEVO: consulta/ajuste de autostart en Windows mediante VBS ---
    def is_windows_autostart_enabled(self) -> bool:
        """Devuelve True si existe nuestro .vbs en la carpeta Startup del usuario."""
        if sys.platform != "win32":
            return False
        try:
            vbs_path = self._startup_vbs_path()
            return os.path.isfile(vbs_path)
        except Exception as e:
            print(f"[AUTOSTART] Error comprobando Startup: {e}")
            return False

    def set_windows_autostart(self, enable: bool) -> None:
        """
        Crea/borra un .vbs en la carpeta Startup que:
          - espera a que haya red (ping a 1.1.1.1),
          - arranca la app con --autostart-bot,
          - ejecuta oculto (sin ventanas ni popups).
        """
        if sys.platform != "win32":
            print("[AUTOSTART] Ignorado: no es Windows.")
            return

        vbs_path = self._startup_vbs_path()

        if not enable:
            try:
                if os.path.exists(vbs_path):
                    os.remove(vbs_path)
                    print(f"[AUTOSTART] Eliminado autostart: {vbs_path}")
            except Exception as e:
                print(f"[AUTOSTART] No se pudo eliminar el .vbs: {e}")
            return

        try:
            # Rutas
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable  # fallback

            script_path = os.path.realpath(sys.argv[0])
            workdir = os.path.dirname(script_path)

            def vbs_quote(s: str) -> str:
                # Genera un literal de VBScript con comillas dobles escapadas
                return '"' + s.replace('"', '""') + '"'

            vbs_content = (
                "Option Explicit\r\n"
                "On Error Resume Next\r\n\r\n"
                "Dim sh : Set sh = CreateObject(\"WScript.Shell\")\r\n"
                "Dim dq : dq = Chr(34)\r\n"
                "Dim i, rc\r\n\r\n"
                "' Espera de red (60 intentos x 5s = ~5 minutos)\r\n"
                "For i = 1 To 60\r\n"
                "  rc = sh.Run(\"cmd /c ping -n 1 -w 1000 1.1.1.1 >nul\", 0, True)\r\n"
                "  If rc = 0 Then Exit For\r\n"
                "  WScript.Sleep 5000\r\n"
                "Next\r\n\r\n"
                "Dim workdir, pythonw, script\r\n"
                f"workdir = {vbs_quote(workdir)}\r\n"
                f"pythonw = {vbs_quote(pythonw)}\r\n"
                f"script  = {vbs_quote(script_path)}\r\n\r\n"
                "sh.CurrentDirectory = workdir\r\n"
                "Dim cmd : cmd = dq & pythonw & dq & \" \" & dq & script & dq & \" --autostart-bot\"\r\n"
                "sh.Run cmd, 0, False\r\n"
            )

            os.makedirs(os.path.dirname(vbs_path), exist_ok=True)
            # Contenido ASCII; si tu ruta tiene acentos, usa 'utf-16' en su lugar.
            with open(vbs_path, "w", encoding="utf-8") as f:
                f.write(vbs_content)

            print(f"[AUTOSTART] Autostart preparado: {vbs_path}")

        except Exception as e:
            print(f"[AUTOSTART] Error al configurar autostart: {e}")

    def on_telegram_thread_finished(self):
        print(f"[{_ts()}] [BOT] Hilo del bot terminado y limpiado.")
        self.telegram_thread = None; self.telegram_worker = None
        if self.telegram_pending_start:
            self.telegram_pending_start = False
            print(f"[{_ts()}] [BOT] Reiniciando bot tras cierre limpio previo...")
            self._start_telegram_bot()

    def _start_telegram_bot(self):
        whitelist = json.loads(self.settings.value("telegram/whitelist", "[]", type=str))
        blacklist = json.loads(self.settings.value("telegram/blacklist", "[]", type=str))
        wl_enabled = self.settings.value("telegram/whitelist_enabled", False, type=bool)
        bl_enabled = self.settings.value("telegram/blacklist_enabled", False, type=bool)

        self.telegram_thread = QThread()
        self.telegram_worker = TelegramBotWorker(
            self.settings.value("telegram/token", "", type=str),
            whitelist, blacklist, wl_enabled, bl_enabled,
            self.ffmpeg_path, self.video_path
        )
        self.telegram_worker.moveToThread(self.telegram_thread)

        # Conexiones clave para cierre limpio y reinicio
        self.telegram_worker.finished.connect(self.telegram_thread.quit)
        self.telegram_worker.finished.connect(self.telegram_worker.deleteLater)
        self.telegram_thread.finished.connect(self.on_telegram_thread_finished)
        self.telegram_thread.finished.connect(self.telegram_thread.deleteLater)

        self.telegram_thread.started.connect(self.telegram_worker.run)
        self.telegram_thread.start()
        print(f"[{_ts()}] [BOT] Hilo del bot iniciado.")

    # === NUEVO: reinicio explícito al cambiar token ===
    def restart_telegram_bot(self, new_token: Optional[str] = None):
        """Detiene el bot actual (si corre) y lo levanta con el token nuevo."""
        if new_token is not None:
            self.settings.setValue("telegram/token", new_token)
            self.settings.setValue("telegram/enabled", True)
            self.settings.sync()

        # Si hay un hilo corriendo, pedir stop y programar reinicio cuando termine
        if self.telegram_thread and self.telegram_thread.isRunning():
            print(f"[{_ts()}] [BOT] restart_telegram_bot(): solicitando stop para reiniciar.")
            self.telegram_pending_start = True
            try:
                if self.telegram_worker:
                    self.telegram_worker.stop()
            except Exception as e:
                print(f"[{_ts()}] [BOT] Error al solicitar stop en reinicio: {e}")
            return

        # Si no está corriendo, arranca directamente
        print(f"[{_ts()}] [BOT] restart_telegram_bot(): no había bot corriendo, iniciando ahora...")
        self._start_telegram_bot()

    def toggle_telegram_bot(self):
        is_enabled = self.settings.value("telegram/enabled", False, type=bool)
        token = self.settings.value("telegram/token", "", type=str)
        print(f"[{_ts()}] [MAIN] toggle_telegram_bot() enabled={is_enabled} token={'SET' if bool(token) else 'EMPTY'}")

        # Desactivar
        if not is_enabled or not token:
            self.telegram_pending_start = False
            if self.telegram_thread is not None:
                print(f"[{_ts()}] [BOT] Deteniendo bot de Telegram...")
                try:
                    if self.telegram_worker:
                        self.telegram_worker.stop()
                except Exception as e:
                    print(f"[{_ts()}] [BOT] Error al solicitar stop: {e}")
                # Espera razonable; si no terminó, NO destruimos el QThread manualmente
                if not self.telegram_thread.wait(15000):
                    print(f"[{_ts()}] [BOT] Aún cerrando en background. Espera a que termine.")
                    return
                self.telegram_thread = None
                self.telegram_worker = None
                print(f"[{_ts()}] [BOT] Bot detenido.")
            return

        # Activar
        if self.telegram_thread is not None:
            if self.telegram_thread.isRunning():
                # ¿se está cerrando? programamos reinicio tras cerrar
                if self.telegram_worker and not self.telegram_worker.is_running:
                    print(f"[{_ts()}] [BOT] El bot está cerrándose. Programando reinicio automático…")
                    self.telegram_pending_start = True
                else:
                    print(f"[{_ts()}] [BOT] Bot ya en ejecución.")
                return
            else:
                # hilo presente pero no corriendo -> limpiar referencias y arrancar nuevo
                self.telegram_thread = None
                self.telegram_worker = None

        print(f"[{_ts()}] [BOT] Iniciando bot de Telegram...")
        self._start_telegram_bot()

    def closeEvent(self, event):
        print("[MAIN] Solicitud de cierre de la aplicación.")
        self.is_downloading = False
        if self.active_worker: self.active_worker.stop()
        if self.active_thread: self.active_thread.quit(); self.active_thread.wait()

        if os.path.isdir(TEMP_DOWNLOADS_DIR):
            print("[MAIN] Limpiando carpetas de descarga temporales...")
            try:
                shutil.rmtree(TEMP_DOWNLOADS_DIR)
                print(" -> Limpieza completada.")
            except OSError as e:
                print(f" -> Error en la limpieza final: {e}")

        if self.telegram_thread and self.telegram_thread.isRunning():
            print("[MAIN] Deteniendo bot de Telegram antes de cerrar...")
            try:
                if self.telegram_worker:
                    self.telegram_worker.stop()
            except Exception as e:
                print(f"[MAIN] Error al solicitar stop del bot: {e}")
            self.telegram_thread.wait(15000)

        print("[MAIN] Guardando configuración final...")
        self.settings.sync()
        event.accept()

    def handle_paste(self, text):
        urls = re.findall(URL_REGEX, text)
        if urls:
            mode = self.download_modes[self.current_download_mode_index]
            for url in urls:
                # Evitar agregar enlaces duplicados en la lista
                if any(info.get('url') == url for info in self.download_info.values()):
                    continue
                if mode == 'Audio':
                    self.add_link_to_table(url, download_type='audio')
                elif mode == 'Video':
                    self.add_link_to_table(url, download_type='video')
                elif mode == 'Ambos':
                    self.add_link_to_table(url, download_type='ambos')
            self.search_bar.blockSignals(True); self.search_bar.clear(); self.search_bar.blockSignals(False)

    def add_link_to_table(self, link_text, download_type='video'):
        row_position = self.table.rowCount()
        job_uuid = uuid.uuid4().hex
        self.download_info[row_position] = {
            'uuid': job_uuid, 'url': link_text,
            'format_selection': None, 'job_type': None, 'completed': False,
        }

        self.table.insertRow(row_position)
        item_num = QTableWidgetItem(str(row_position + 1))
        item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row_position, 0, item_num)
        self.table.setItem(row_position, 1, QTableWidgetItem(link_text))
        progress_bar = QProgressBar(); progress_bar.setFormat("En cola")
        self.table.setCellWidget(row_position, 2, progress_bar)
        self.table.setCellWidget(row_position, 3, self.create_format_widget(download_type))
        combo = QComboBox(); combo.addItem("Mejor Calidad", "bestvideo+bestaudio/best"); combo.setEnabled(False)
        self.table.setCellWidget(row_position, 4, combo)
        self.table.setCellWidget(row_position, 5, self.create_delete_widget())

        if download_type in ('video', 'ambos'):
            self.fetch_formats_for_row(row_position, link_text)
        else:
            combo.clear(); combo.addItem("N/A"); combo.setEnabled(False)

    def fetch_formats_for_row(self, row, url):
        ydl_opts = base_ytdlp_opts(self.ffmpeg_path) | {'nocolor': True}
        thread = QThread(self); worker = FormatFetcherWorker(row, url, ydl_opts)

        worker.moveToThread(thread); self.active_format_fetchers[row] = (thread, worker)
        thread.started.connect(worker.run)
        worker.formats_fetched.connect(self.on_formats_fetched)
        worker.error.connect(self.on_formats_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda r=row: self.cleanup_format_fetcher(r))
        thread.start()

    def cleanup_format_fetcher(self, row):
        if row in self.active_format_fetchers:
            del self.active_format_fetchers[row]

    def on_formats_fetched(self, row, formats):
        combo = self.table.cellWidget(row, 4)
        if not combo:
            return
        combo.clear(); combo.setEnabled(True)
        combo.addItem("Mejor Calidad", "bestvideo+bestaudio/best")

        def sort_key(f):
            h = _get_height(f) or 0
            return (h, f.get('acodec') not in (None, 'none'), f.get('tbr') or 0, f.get('fps') or 0)

        added_heights = set()
        for f in sorted(formats, key=sort_key, reverse=True):
            vcodec = f.get('vcodec')
            height = _get_height(f)
            if not height:
                continue
            if not vcodec or vcodec == 'none':
                continue
            if height in added_heights:
                continue
            added_heights.add(height)

            ext = f.get('ext', 'N/A')
            size = _filesize_of(f)
            size_str = f" ~{size/(1024*1024):.1f}MB" if size else ""
            display_text = f"{height}p ({ext}){size_str}"
            format_id = f.get('format_id')
            if format_id:
                combo.addItem(display_text, format_id)

        if combo.count() == 1:
            combo.addItem("360p", "18")  # fallback clásico

    def on_formats_error(self, row, error_message):
        combo = self.table.cellWidget(row, 4)
        if combo:
            combo.clear(); combo.addItem("Error")
        print(f"Error al obtener formatos para la fila {row+1}: {error_message}")

    def create_format_widget(self, download_type):
        widget = QWidget(); layout = QHBoxLayout(widget); layout.setContentsMargins(5, 0, 5, 0)
        cb_audio = QCheckBox("Audio"); cb_video = QCheckBox("Video")
        if download_type == 'audio':
            cb_audio.setChecked(True); cb_video.setChecked(False)
        elif download_type == 'video':
            cb_audio.setChecked(False); cb_video.setChecked(True)
        elif download_type == 'ambos':
            cb_audio.setChecked(True); cb_video.setChecked(True)
        layout.addWidget(cb_audio); layout.addWidget(cb_video)
        return widget

    def create_delete_widget(self):
        widget = QWidget(); layout = QHBoxLayout(widget); layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        delete_button = QPushButton("❌"); delete_button.setFixedSize(QSize(28, 28))
        delete_button.clicked.connect(self.delete_row); layout.addWidget(delete_button)
        return widget

    def delete_row(self):
        button = self.sender()
        if not button:
            return
        parent_widget = button.parent()
        if not parent_widget:
            return
        row = self.table.indexAt(parent_widget.pos()).row()
        if row < 0:
            return

        # Si la fila a eliminar está en descarga activa, detenerla
        if row in self.active_downloads:
            thread, worker = self.active_downloads.pop(row)
            try:
                worker.stop()
            except Exception:
                pass
            thread.quit()
            thread.wait(5000)

        # Cancelar fetch de formatos si está en progreso para esta fila
        if row in self.active_format_fetchers:
            f_thread, f_worker = self.active_format_fetchers.pop(row)
            f_thread.quit()
            f_thread.wait()

        # Remover de cola de descarga pendiente si aplica
        self.download_queue = [job for job in self.download_queue if job.get('row') != row]

        # Si había entradas de cola con índices mayores, ajustar sus índices -1
        for job in self.download_queue:
            if job['row'] > row:
                job['row'] -= 1

        # Si la descarga activa eliminada, continuar con la siguiente si corresponde
        if self.is_downloading and not self.active_thread:
            # Nota: active_thread es None significa que la descarga activa fue detenida arriba
            if self.download_queue:
                self.start_next_download()
            else:
                self.is_downloading = False
                self.update_master_download_icon()

        # Eliminar información de la fila y limpiar temporales si no completado
        if row in self.download_info:
            info = self.download_info[row]
            job_uuid = info.get('uuid')
            was_completed = info.get('completed', False)
            if not was_completed and job_uuid:
                temp_job_dir = os.path.join(TEMP_DOWNLOADS_DIR, job_uuid)
                if os.path.isdir(temp_job_dir):
                    print(f"Limpiando directorio de trabajo temporal: {temp_job_dir}")
                    ok = _safe_rmtree(temp_job_dir)
                    print(" -> Directorio temporal eliminado con éxito." if ok else " -> No se pudo eliminar.")
            # Eliminar entrada de info
            del self.download_info[row]

            # Ajustar índices de download_info para filas posteriores
            new_download_info = {}
            for r, info_val in sorted(self.download_info.items()):
                if r < row:
                    new_download_info[r] = info_val
                else:
                    new_download_info[r - 1] = info_val
            self.download_info = new_download_info

            # Ajustar referencias en active_downloads
            new_active_downloads = {}
            for r, tup in list(self.active_downloads.items()):
                if r < row:
                    new_active_downloads[r] = tup
                elif r > row:
                    new_active_downloads[r - 1] = tup
            self.active_downloads = new_active_downloads

            # Ajustar referencias en active_format_fetchers
            new_active_format_fetchers = {}
            for r, tup in list(self.active_format_fetchers.items()):
                if r < row:
                    new_active_format_fetchers[r] = tup
                elif r > row:
                    new_active_format_fetchers[r - 1] = tup
                    # Actualizar el atributo 'row' del worker de formatos
                    try:
                        tup[1].row = r - 1
                    except Exception:
                        pass
            self.active_format_fetchers = new_active_format_fetchers

        # Remover la fila de la tabla y actualizar numeración
        self.table.removeRow(row)
        for i in range(row, self.table.rowCount()):
            num_item = self.table.item(i, 0)
            if num_item:
                num_item.setText(str(i+1))


# ----------------------------- Main -----------------------------------

if __name__ == "__main__":
    # En Windows: fija AppUserModelID para icono consistente en la barra de tareas
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "BitStation.MultimediaDownloader"
            )
        except Exception:
            pass

    # Ruta de icono (usa ico/BitStation.ico si existe; si no, BitStation.ico como fallback)
    icon_path = "ico/BitStation.ico"
    if not os.path.exists(icon_path):
        icon_path = "BitStation.ico"

    # Arranque de la app Qt
    app = QApplication(sys.argv)
    app.setApplicationName("BitStation Multimedia Downloader")
    app.setWindowIcon(QIcon(icon_path))

    # Crea la ventana principal
    window = MainWindow()

    # Soporte de arranque oculto para el bot (NUEVO)
    AUTOSTART_BOT_ONLY = ("--autostart-bot" in sys.argv)

    if AUTOSTART_BOT_ONLY:
        # No mostramos la ventana; solo dejamos correr el loop Qt para el hilo del bot
        should_run = (
            window.settings.value("telegram/enabled", False, type=bool)
            and bool(window.settings.value("telegram/token", "", type=str))
        )
        if not should_run:
            print("[AUTOSTART] Bot no habilitado o token vacío. Saliendo.")
            QTimer.singleShot(0, app.quit)
        # Si sí debe correr, simplemente no llamamos a window.show()
    else:
        window.show()

    # Ejecuta el loop de eventos
    sys.exit(app.exec())
